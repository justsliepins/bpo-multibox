/*
 * mbcast — mirror keyboard and mouse between X displays.
 *
 *   mbcast DISPLAY DISPLAY [DISPLAY...]      e.g.  mbcast :61 :62 :63 :64
 *
 * Every box runs in its own rootful Xwayland, so each game client is always the
 * focused window of its own X server. That matters for Tales of Pirates: clicks
 * and hotkeys are read through DirectInput (MPGameApp::_ReadKeyboardInput), and
 * that only works while the client's window is foreground. The cursor position
 * comes from WM_MOUSEMOVE, so motion is mirrored at the same absolute coordinates.
 *
 * Input on every display is captured with the RECORD extension. The box you are
 * in leads: events from the leading display are replayed on all the others with
 * XTEST, so the clients see ordinary hardware events. RECORD also reports our own
 * injections back, so an event on a follower only counts as yours (and makes that
 * box the leader) if nothing was injected into it for ECHO_MS. Echoes arrive within
 * a few milliseconds; when you move to another box the old one goes quiet, so the
 * new box takes over almost at once.
 *
 * Pause, or SIGUSR1, toggles broadcasting. The toggle key itself is never
 * forwarded. Turning broadcast off (or switching leader) releases any keys and
 * buttons still held on the followers; turning it on syncs their pointers.
 */
#include <X11/Xlib.h>
#include <X11/Xlibint.h>
#include <X11/keysym.h>
#include <X11/extensions/XTest.h>
#include <X11/extensions/XInput2.h>
#include <X11/extensions/record.h>
#include <X11/Xproto.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <math.h>
#include <sys/socket.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAXBOXES 16
#define ECHO_MS 100   /* events this soon after an injection into a box are treated as its echo */

struct box {
    const char *name;
    Display *ctrl, *data;    /* control connection (XTEST, queries) + RECORD data connection */
    XRecordContext rc;
    KeyCode toggle_key;
    long last_inject_ms;
    Display *xi;              /* XInput2 raw motion (only selected on the leader that can lead) */
    int xi_opcode;
    signed char rel_dev[256]; /* per source device: 1 relative pointer, 0 not, -1 unknown */
};

/* Mouse motion. Wine gives DirectInput the *raw* relative deltas from XInput2 (camera rotation
 * with the right button uses them), and takes the cursor position from core motion. So followers
 * get the leader's raw deltas as relative XTEST motion (raw values are pre-acceleration, so they
 * match exactly), and their cursor is then put at the leader's exact position with XWarpPointer,
 * which moves the cursor without producing raw events. Absolute XTEST motion would show up as
 * huge bogus raw deltas. */
static int lead_x = -1, lead_y = -1;
/* The game polls the mouse once per frame (DirectInput GetDeviceState): a press and release inside
 * one frame is never seen. Followers always hold a button at least this long. */
#define MIN_HOLD_MS 70
static long button_down_ms[32];

/* Click offsets (mbtrack.py). When a follower's character stands a tile or two from window 1's,
 * the camera (which follows each character) shows the world shifted, so its cursor is moved by
 * (leader - follower) tiles in pixels: a click on a monster in window 1 hits the same monster
 * there. Only over the game world: the interface (bottom bar, chat, minimap, party list) sits at
 * the same place in every box, so those clicks are never shifted. */
static int nboxes;   /* defined below */
static const char *offsets_path;
static const char *target_state;
static int aim_all;
static int skip_start_hook;
static struct timespec offsets_mtime;
/* Windows taken out of broadcast (STATE/excluded, one box index per line), e.g. a revived
 * character auto-tracing back: the leader's clicks would interrupt its walk. */
static char excluded[MAXBOXES];
static struct timespec excluded_mtime;
static int excluded_present;
static int off_x[MAXBOXES], off_y[MAXBOXES];   /* indexed by box; box 0 (leader) stays 0 */
static int game_x = 8, game_y = 37, game_w = 1366, game_h = 768;   /* game window inside a box */

static int over_world(int rx, int ry)
{
    int x = rx - game_x, y = ry - game_y;
    if (x < 0 || y < 0 || x >= game_w || y >= game_h)
        return 0;
    if (y >= game_h - 188)                      /* chat, hotbars, bottom-right buttons */
        return 0;
    if (x < 205 && y < 320)                     /* own and party frames */
        return 0;
    if (x >= game_w - 156 && y < 370)           /* minimap, clock, shop and menu icons */
        return 0;
    return 1;
}

/* Where follower i's cursor goes for the leader's position (rx, ry). */
static void follower_point(int i, int rx, int ry, int *x, int *y)
{
    *x = rx;
    *y = ry;
    if (!over_world(rx, ry))
        return;
    *x = rx + off_x[i];
    *y = ry + off_y[i];
    /* stay inside the game's world area, so a shifted cursor never lands on the interface */
    if (*x < game_x) *x = game_x;
    if (*x > game_x + game_w - 1) *x = game_x + game_w - 1;
    if (*y < game_y) *y = game_y;
    if (*y > game_y + game_h - 189) *y = game_y + game_h - 189;
}

static void warp_followers(void);

/* Pauses held by processes: STATE/pause.<pid> files. A pause ends when its file is removed or its
 * process is gone, so a killed ALIGN/SIT/KILL can never leave broadcast paused. */
static int pidfile_pauses(void)
{
    if (!target_state)
        return 0;
    DIR *dir = opendir(target_state);
    if (!dir)
        return 0;
    int n = 0;
    struct dirent *e;
    while ((e = readdir(dir))) {
        int pid;
        if (sscanf(e->d_name, "pause.%d", &pid) != 1)
            continue;
        if (kill(pid, 0) == 0 || errno == EPERM) {
            n++;
        } else {
            char path[600];
            snprintf(path, sizeof path, "%s/%s", target_state, e->d_name);
            unlink(path);                   /* stale: its process died */
        }
    }
    closedir(dir);
    return n;
}

static void load_excluded(void)
{
    char path[512];
    struct stat st;
    if (!target_state)
        return;
    snprintf(path, sizeof path, "%s/excluded", target_state);
    if (stat(path, &st) < 0) {
        if (excluded_present) {
            memset(excluded, 0, sizeof excluded);
            excluded_present = 0;
            fprintf(stderr, "mbcast: no windows excluded\n");
        }
        return;
    }
    if (excluded_present && st.st_mtim.tv_sec == excluded_mtime.tv_sec &&
        st.st_mtim.tv_nsec == excluded_mtime.tv_nsec)
        return;
    excluded_mtime = st.st_mtim;
    excluded_present = 1;
    memset(excluded, 0, sizeof excluded);
    FILE *f = fopen(path, "r");
    int i;
    while (f && fscanf(f, "%d", &i) == 1)
        if (i >= 0 && i < MAXBOXES)
            excluded[i] = 1;
    if (f)
        fclose(f);
    fprintf(stderr, "mbcast: excluded windows updated\n");
}


static void load_offsets(void)
{
    struct stat st;
    if (!offsets_path || stat(offsets_path, &st) < 0)
        return;
    if (st.st_mtim.tv_sec == offsets_mtime.tv_sec && st.st_mtim.tv_nsec == offsets_mtime.tv_nsec)
        return;
    offsets_mtime = st.st_mtim;
    FILE *f = fopen(offsets_path, "r");
    if (!f)
        return;
    int i = 1, x, y;
    while (i < nboxes && fscanf(f, "%d %d", &x, &y) == 2) {
        off_x[i] = x;
        off_y[i] = y;
        i++;
    }
    for (; i < nboxes; i++)
        off_x[i] = off_y[i] = 0;
    fclose(f);
    warp_followers();   /* the hover moves with the new offset */
}

static double acc_x, acc_y;

static struct box boxes[MAXBOXES];
static int nboxes;
static int leader;
static int fixed_leader;   /* -1: the first display always leads, input in the others is never copied */
static int enabled = 1;
static long paused_since_ms;
#define PAUSE_MAX_MS 1800000  /* safespot trip: revive, walk, rest, walk back */   /* a pause nobody released (crashed ALIGN/FIX) ends by itself */
static int paused;   /* SIGRTMIN pauses, SIGRTMIN+1 resumes (nested: ALIGN inside FIX). Doesn't touch
                        the ON/OFF state the user chose, so a click on BROADCAST meanwhile is kept. */
static unsigned char held_keys[256];
static unsigned char held_buttons[32];
static int sigpipe[2];

static long now_ms(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec * 1000L + tv.tv_usec / 1000;
}

/* Publish the state: the on-screen button and the waybar indicator read it from this file. */
static void show_state(void)
{
    fprintf(stderr, "mbcast: broadcast %s\n", enabled ? "ON" : "OFF");

    /* State for the waybar indicator (multibox waybar), then tell waybar to refresh it. */
    const char *rt = getenv("XDG_RUNTIME_DIR");
    if (rt) {
        char path[512];
        snprintf(path, sizeof path, "%s/multibox/broadcast", rt);
        FILE *sf = fopen(path, "w");
        if (sf) {
            fputs(enabled ? "on\n" : "off\n", sf);
            fclose(sf);
        }
        if (system("pkill -RTMIN+11 -x waybar >/dev/null 2>&1")) {}
    }

    /* MB_HOOK on|off: the launcher re-arranges the boxes (stacked when ON, spread out when OFF).
       Double fork so the hook never blocks us and leaves no zombie. */
    const char *hook = getenv("MB_HOOK");
    if (skip_start_hook) {
        skip_start_hook = 0;
        return;
    }
    if (hook && *hook) {
        pid_t pid = fork();
        if (pid == 0) {
            if (fork() == 0) {
                execl(hook, hook, "_state", enabled ? "on" : "off", (char *)NULL);
                _exit(127);
            }
            _exit(0);
        }
        if (pid > 0)
            waitpid(pid, NULL, 0);
    }
}

static void flush_followers(void)
{
    long t = now_ms();
    for (int i = 0; i < nboxes; i++)
        if (i != leader) {
            XFlush(boxes[i].ctrl);
            boxes[i].last_inject_ms = t;
        }
}

static void release_held(void)
{
    for (int i = 0; i < nboxes; i++) {
        if (i == leader)
            continue;
        for (int k = 0; k < 256; k++)
            if (held_keys[k])
                XTestFakeKeyEvent(boxes[i].ctrl, k, False, CurrentTime);
        for (int b = 0; b < 32; b++)
            if (held_buttons[b])
                XTestFakeButtonEvent(boxes[i].ctrl, b, False, CurrentTime);
    }
    memset(held_keys, 0, sizeof held_keys);
    memset(held_buttons, 0, sizeof held_buttons);
    flush_followers();
}

static void sync_pointer(void)
{
    Window root, child;
    int rx, ry, wx, wy;
    unsigned int mask;
    Display *d = boxes[leader].ctrl;
    if (!XQueryPointer(d, DefaultRootWindow(d), &root, &child, &rx, &ry, &wx, &wy, &mask))
        return;
    for (int i = 0; i < nboxes; i++)
        if (i != leader)
            {
                int fx, fy;
                follower_point(i, rx, ry, &fx, &fy);
                XWarpPointer(boxes[i].ctrl, None, DefaultRootWindow(boxes[i].ctrl), 0, 0, 0, 0, fx, fy);
            }
    lead_x = rx;
    lead_y = ry;
    flush_followers();
}

static void set_enabled(int on)
{
    if (on == enabled)
        return;
    enabled = on;
    if (enabled)
        sync_pointer();
    else
        release_held();
    show_state();
}

static void set_leader(int i)
{
    if (i == leader)
        return;
    release_held();
    leader = i;
    fprintf(stderr, "mbcast: leader is now %s\n", boxes[i].name);
}

/* Aiming (mbaim.py): at a click over the game world, look up the thing under the leader's cursor
 * in each follower's screen and put that follower's cursor exactly on it, then give the game one
 * frame to register what's under the cursor before the click goes out. */
static const char *aim_path;
#define AIM_TIMEOUT_MS 120
#define HOVER_FRAME_US 40000   /* one frame at 30 fps, plus a little */

static void aim_followers(int rx, int ry)
{
    int px[MAXBOXES], py[MAXBOXES];
    char buf[512];
    int n = snprintf(buf, sizeof buf, "%d %d", rx, ry);
    for (int i = 0; i < nboxes; i++) {
        follower_point(i, rx, ry, &px[i], &py[i]);
        if (i != leader)
            n += snprintf(buf + n, sizeof buf - n, " %d %d", px[i], py[i]);
    }
    buf[n++] = '\n';

    if (aim_path) {
        int fd = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un sa = {.sun_family = AF_UNIX};
        snprintf(sa.sun_path, sizeof sa.sun_path, "%s", aim_path);
        struct timeval tv = {0, AIM_TIMEOUT_MS * 1000};
        if (fd >= 0) {
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
            setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
        }
        if (fd >= 0 && connect(fd, (struct sockaddr *)&sa, sizeof sa) == 0 && write(fd, buf, n) == n) {
            char rep[512];
            ssize_t got = read(fd, rep, sizeof rep - 1);
            if (got > 0) {
                rep[got] = 0;
                char *p = rep;
                for (int i = 0; i < nboxes; i++) {
                    if (i == leader || excluded[i])
                        continue;
                    int x, y, used;
                    if (sscanf(p, "%d %d%n", &x, &y, &used) != 2)
                        break;
                    px[i] = x;
                    py[i] = y;
                    p += used;
                }
            }
        }
        if (fd >= 0)
            close(fd);
    }
    for (int i = 0; i < nboxes; i++)
        if (i != leader)
            XWarpPointer(boxes[i].ctrl, None, DefaultRootWindow(boxes[i].ctrl), 0, 0, 0, 0, px[i], py[i]);
    flush_followers();
    usleep(HOVER_FRAME_US);
}

/* target-points is written only after Python has found the same green mob bar in every box.
 * Unlike ordinary broadcast movement, these are box-specific sprite click locations, so do not
 * apply offsets or image matching to this one manually requested click. */
static int target_points_followers(void)
{
    char path[512];
    int px[MAXBOXES], py[MAXBOXES], have[MAXBOXES] = {0};
    snprintf(path, sizeof path, "%s/target-points", target_state);
    FILE *f = fopen(path, "r");
    if (!f)
        return 0;
    int i, x, y;
    while (fscanf(f, "%d %d %d", &i, &x, &y) == 3) {
        if (i < 0 || i >= nboxes || x < 0 || y < 0)
            continue;
        px[i] = x;
        py[i] = y;
        have[i] = 1;
    }
    fclose(f);
    for (i = 0; i < nboxes; i++)
        if (i != leader && !have[i])
            return 0;
    for (i = 0; i < nboxes; i++)
        if (i != leader)
            XWarpPointer(boxes[i].ctrl, None, DefaultRootWindow(boxes[i].ctrl),
                         0, 0, 0, 0, px[i], py[i]);
    flush_followers();
    usleep(HOVER_FRAME_US);
    return 1;
}

static void on_record(XPointer priv, XRecordInterceptData *d)
{
    int src = (int)(intptr_t)priv;
    if (d->category != XRecordFromServer) {
        XRecordFreeData(d);
        return;
    }
    const xEvent *ev = (const xEvent *)d->data;
    if (src != leader) {
        if (fixed_leader) {
            XRecordFreeData(d);
            return;
        }
        if (now_ms() - boxes[src].last_inject_ms < ECHO_MS) {   /* echo of our own injection */
            XRecordFreeData(d);
            return;
        }
        set_leader(src);
    }
    int type = ev->u.u.type & 0x7f;
    int detail = ev->u.u.detail;

    if ((type == KeyPress || type == KeyRelease) && detail == boxes[src].toggle_key) {
        if (type == KeyPress)
            set_enabled(!enabled);
        XRecordFreeData(d);
        return;
    }
    if (!enabled || paused) {
        XRecordFreeData(d);
        return;
    }

    /* a left or right click on the game world: aim first (ignore wheel buttons 4/5) */
    int target_aim = 0;
    if (type == ButtonPress && detail == 1 && target_state) {
        char path[512];
        snprintf(path, sizeof path, "%s/target-aim", target_state);
        struct stat st;
        /* only a fresh marker (KILL writes it right before its click); a stale one left by an
           interrupted KILL must not turn the user's next ordinary click into a target click */
        if (stat(path, &st) == 0) {
            struct timespec now;
            clock_gettime(CLOCK_REALTIME, &now);
            long age_ms = (now.tv_sec - st.st_mtim.tv_sec) * 1000 + (now.tv_nsec - st.st_mtim.tv_nsec) / 1000000;
            target_aim = unlink(path) == 0 && age_ms < 1500;
        }
    }
    int target_routed = 0;
    if (target_aim && type == ButtonPress && detail == 1)
        target_routed = target_points_followers();
    if (aim_path && (aim_all || target_aim) && type == ButtonPress &&
        (detail == 1 || detail == 3) &&
        !target_aim &&
        over_world(ev->u.keyButtonPointer.rootX, ev->u.keyButtonPointer.rootY))
        aim_followers(ev->u.keyButtonPointer.rootX, ev->u.keyButtonPointer.rootY);

    if (type == ButtonRelease && detail < 32 && button_down_ms[detail]) {
        long held = now_ms() - button_down_ms[detail];
        if (held < MIN_HOLD_MS)
            usleep((MIN_HOLD_MS - held) * 1000);
    }
    if (type == ButtonPress && detail < 32)
        button_down_ms[detail] = now_ms();

    for (int i = 0; i < nboxes; i++) {
        if (i == src || excluded[i])
            continue;
        Display *dpy = boxes[i].ctrl;
        switch (type) {
        case KeyPress:
        case KeyRelease:
            XTestFakeKeyEvent(dpy, detail, type == KeyPress, CurrentTime);
            break;
        case ButtonPress:
        case ButtonRelease:
            /* routing failed: followers still get the normal (offset) click rather than none */
            (void)target_routed;
            XTestFakeButtonEvent(dpy, detail, type == ButtonPress, CurrentTime);
            break;
        case MotionNotify: {
            int fx, fy;
            follower_point(i, ev->u.keyButtonPointer.rootX, ev->u.keyButtonPointer.rootY, &fx, &fy);
            XWarpPointer(dpy, None, DefaultRootWindow(dpy), 0, 0, 0, 0, fx, fy);
            break;
        }
        }
    }
    if (type == MotionNotify) {
        lead_x = ev->u.keyButtonPointer.rootX;
        lead_y = ev->u.keyButtonPointer.rootY;
    }
    if (type == KeyPress || type == KeyRelease)
        held_keys[detail] = type == KeyPress;
    else if ((type == ButtonPress || type == ButtonRelease) && detail < 32)
        held_buttons[detail] = type == ButtonPress;
    flush_followers();
    XRecordFreeData(d);
}

/* Is this source device a real relative pointer? Xwayland's absolute pointer also sends raw
 * events (with absolute values) that Wine ignores; our own XTEST injections must not count. */
static int is_relative_source(struct box *b, int src)
{
    if (src < 0 || src > 255)
        return 0;
    if (b->rel_dev[src] >= 0)
        return b->rel_dev[src];
    int n, rel = 0;
    XIDeviceInfo *info = XIQueryDevice(b->xi, src, &n);
    if (info && n == 1 && !strstr(info->name, "XTEST")) {
        int rx = 0, ry = 0;
        for (int j = 0; j < info->num_classes; j++) {
            if (info->classes[j]->type != XIValuatorClass)
                continue;
            XIValuatorClassInfo *v = (XIValuatorClassInfo *)info->classes[j];
            if (v->mode == XIModeRelative && v->number == 0) rx = 1;
            if (v->mode == XIModeRelative && v->number == 1) ry = 1;
        }
        rel = rx && ry;
    }
    if (info)
        XIFreeDeviceInfo(info);
    b->rel_dev[src] = rel;
    return rel;
}

static void process_raw(int index)
{
    struct box *b = &boxes[index];
    while (XPending(b->xi)) {
        XEvent e;
        XNextEvent(b->xi, &e);
        if (e.xcookie.type != GenericEvent || e.xcookie.extension != b->xi_opcode ||
            !XGetEventData(b->xi, &e.xcookie))
            continue;
        XIRawEvent *r = e.xcookie.data;
        if (r->evtype == XI_RawMotion && index == leader && enabled && !paused && is_relative_source(b, r->sourceid)) {
            double *rv = r->raw_values, dx = 0, dy = 0;
            for (int i = 0; i < r->valuators.mask_len * 8; i++) {
                if (!XIMaskIsSet(r->valuators.mask, i))
                    continue;
                if (i == 0) dx = *rv;
                if (i == 1) dy = *rv;
                rv++;
            }
            acc_x += dx;
            acc_y += dy;
            int ix = (int)trunc(acc_x), iy = (int)trunc(acc_y);
            acc_x -= ix;
            acc_y -= iy;
            if (ix || iy) {
                for (int i = 0; i < nboxes; i++) {
                    if (i == leader || excluded[i])
                        continue;
                    XTestFakeRelativeMotionEvent(boxes[i].ctrl, ix, iy, CurrentTime);
                    if (lead_x >= 0) {   /* relative motion is accelerated: put the cursor back exactly */
                        int fx, fy;
                        follower_point(i, lead_x, lead_y, &fx, &fy);
                        XWarpPointer(boxes[i].ctrl, None, DefaultRootWindow(boxes[i].ctrl), 0, 0, 0, 0,
                                     fx, fy);
                    }
                }
                flush_followers();
            }
        }
        XFreeEventData(b->xi, &e.xcookie);
    }
}

static void warp_followers(void)
{
    if (!enabled || paused || lead_x < 0)
        return;
    for (int i = 0; i < nboxes; i++) {
        if (i == leader || excluded[i])
            continue;
        int fx, fy;
        follower_point(i, lead_x, lead_y, &fx, &fy);
        XWarpPointer(boxes[i].ctrl, None, DefaultRootWindow(boxes[i].ctrl), 0, 0, 0, 0, fx, fy);
    }
    flush_followers();
}

static void on_signal(int sig)
{
    char c = sig == SIGUSR1 ? 't' : sig == SIGRTMIN ? 'p' : sig == SIGRTMIN + 1 ? 'r' : 'q';
    (void)!write(sigpipe[1], &c, 1);
}

/* A failed request (e.g. a device that vanished) must not kill the broadcaster: the default
 * Xlib error handler exits the process. */
static int on_x_error(Display *d, XErrorEvent *e)
{
    char msg[128];
    XGetErrorText(d, e->error_code, msg, sizeof msg);
    fprintf(stderr, "mbcast: X error on %s: %s (request %d)\n", DisplayString(d), msg, e->request_code);
    return 0;
}

static int on_io_error(Display *d)
{
    fprintf(stderr, "mbcast: lost connection to %s, exiting\n", DisplayString(d));
    exit(1);
}

static Display *open_or_die(const char *name)
{
    Display *d = XOpenDisplay(name);
    if (!d) {
        fprintf(stderr, "mbcast: cannot open display %s\n", name);
        exit(1);
    }
    return d;
}

static void setup_box(struct box *b, int index)
{
    int ev, er, maj, min;
    b->ctrl = open_or_die(b->name);
    b->data = open_or_die(b->name);
    if (!XTestQueryExtension(b->ctrl, &ev, &er, &maj, &min)) {
        fprintf(stderr, "mbcast: %s has no XTEST\n", b->name);
        exit(1);
    }
    if (!XRecordQueryVersion(b->ctrl, &maj, &min)) {
        fprintf(stderr, "mbcast: %s has no RECORD\n", b->name);
        exit(1);
    }
    b->toggle_key = XKeysymToKeycode(b->ctrl, XK_Pause);

    memset(b->rel_dev, -1, sizeof b->rel_dev);
    b->xi = open_or_die(b->name);
    int xi_ev, xi_err, xmaj = 2, xmin = 2;
    if (!XQueryExtension(b->xi, "XInputExtension", &b->xi_opcode, &xi_ev, &xi_err) ||
        XIQueryVersion(b->xi, &xmaj, &xmin) != Success) {
        fprintf(stderr, "mbcast: %s has no XInput2\n", b->name);
        exit(1);
    }
    if (index == 0 || !fixed_leader) {
        unsigned char m[XIMaskLen(XI_LASTEVENT)] = {0};
        XISetMask(m, XI_RawMotion);
        XIEventMask em = {XIAllMasterDevices, sizeof m, m};
        XISelectEvents(b->xi, DefaultRootWindow(b->xi), &em, 1);
        XFlush(b->xi);
    }

    XRecordRange *range = XRecordAllocRange();
    range->device_events.first = KeyPress;
    range->device_events.last = MotionNotify;
    XRecordClientSpec spec = XRecordAllClients;
    b->rc = XRecordCreateContext(b->ctrl, 0, &spec, 1, &range, 1);
    XFree(range);
    if (!b->rc) {
        fprintf(stderr, "mbcast: XRecordCreateContext failed on %s\n", b->name);
        exit(1);
    }
    XSync(b->ctrl, False);
    if (!XRecordEnableContextAsync(b->data, b->rc, on_record, (XPointer)(intptr_t)index)) {
        fprintf(stderr, "mbcast: XRecordEnableContextAsync failed on %s\n", b->name);
        exit(1);
    }
}

int main(int argc, char **argv)
{
    int first = 1;
    if (argc > 1 && strcmp(argv[1], "-1") == 0) {
        fixed_leader = 1;
        first = 2;
    }
    if (argc - first < 2 || argc - first > MAXBOXES) {
        fprintf(stderr, "usage: %s [-1] DISPLAY DISPLAY [DISPLAY...]   (max %d)\n"
                        "  -1  only the first display leads\n", argv[0], MAXBOXES);
        return 2;
    }
    if (getenv("MB_START_OFF"))
        enabled = 0;
    offsets_path = getenv("MB_OFFSETS");
    target_state = getenv("MB_TARGET_STATE");
    aim_path = getenv("MB_AIM");
    aim_all = getenv("MB_AIM_ALL") && strcmp(getenv("MB_AIM_ALL"), "1") == 0;
    skip_start_hook = getenv("MB_SKIP_START_HOOK") && strcmp(getenv("MB_SKIP_START_HOOK"), "1") == 0;
    if (getenv("MB_GAME_RECT"))
        sscanf(getenv("MB_GAME_RECT"), "%d,%d,%d,%d", &game_x, &game_y, &game_w, &game_h);
    XSetIOErrorHandler(on_io_error);
    XSetErrorHandler(on_x_error);
    for (int i = first; i < argc; i++) {
        boxes[nboxes].name = argv[i];
        setup_box(&boxes[nboxes], nboxes);
        nboxes++;
    }

    if (pipe(sigpipe) < 0)
        return 1;
    struct sigaction sa = {0};
    sa.sa_handler = on_signal;
    sigaction(SIGUSR1, &sa, NULL);
    sigaction(SIGRTMIN, &sa, NULL);
    sigaction(SIGRTMIN + 1, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);

    fprintf(stderr, "mbcast: %d boxes, %s leads; Pause or SIGUSR1 toggles\n", nboxes,
            fixed_leader ? boxes[0].name : "the box you use");
    sync_pointer();
    show_state();

    for (;;) {
        fd_set fds;
        FD_ZERO(&fds);
        int maxfd = sigpipe[0];
        FD_SET(sigpipe[0], &fds);
        for (int i = 0; i < nboxes; i++) {
            int fd = ConnectionNumber(boxes[i].data);
            FD_SET(fd, &fds);
            if (fd > maxfd)
                maxfd = fd;
            fd = ConnectionNumber(boxes[i].xi);
            FD_SET(fd, &fds);
            if (fd > maxfd)
                maxfd = fd;
        }
        load_offsets();
        load_excluded();
        {
            static int file_paused;
            int fp = pidfile_pauses() > 0;
            if (fp != file_paused) {
                file_paused = fp;
                if (fp) {
                    if (paused++ == 0)
                        release_held();
                    paused_since_ms = now_ms();
                    fprintf(stderr, "mbcast: paused by a process (%d)\n", paused);
                } else {
                    if (paused > 0 && --paused == 0 && enabled)
                        sync_pointer();
                    fprintf(stderr, "mbcast: process pause ended (%d)\n", paused);
                }
            }
        }
        struct timeval tv = {0, 100000};   /* also re-checks the offsets file 10x a second */
        if (paused && now_ms() - paused_since_ms > PAUSE_MAX_MS) {
            paused = 0;
            fprintf(stderr, "mbcast: pause timed out, resumed\n");
            if (enabled)
                sync_pointer();
        }
        if (select(maxfd + 1, &fds, NULL, NULL, &tv) < 0) {
            if (errno == EINTR)
                continue;
            perror("select");
            break;
        }
        if (FD_ISSET(sigpipe[0], &fds)) {
            char c;
            if (read(sigpipe[0], &c, 1) == 1) {
                if (c == 't')
                    set_enabled(!enabled);
                else if (c == 'p') {
                    if (paused++ == 0)
                        release_held();
                    paused_since_ms = now_ms();   /* the watchdog counts from the latest pause */
                    fprintf(stderr, "mbcast: paused (%d)\n", paused);
                } else if (c == 'r') {
                    if (paused > 0 && --paused == 0 && enabled)
                        sync_pointer();
                    fprintf(stderr, "mbcast: resumed (%d)\n", paused);
                } else
                    break;
            }
        }
        for (int i = 0; i < nboxes; i++)
            if (FD_ISSET(ConnectionNumber(boxes[i].data), &fds))
                XRecordProcessReplies(boxes[i].data);
        for (int i = 0; i < nboxes; i++)
            if (FD_ISSET(ConnectionNumber(boxes[i].xi), &fds) || XPending(boxes[i].xi))
                process_raw(i);
    }

    release_held();
    for (int i = 0; i < nboxes; i++) {
        XRecordDisableContext(boxes[i].ctrl, boxes[i].rc);
        XRecordFreeContext(boxes[i].ctrl, boxes[i].rc);
        XFlush(boxes[i].ctrl);
    }
    return 0;
}
