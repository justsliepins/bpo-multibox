#!/usr/bin/env python3
"""Play window 1 the way the user does, with broadcast ON: the broadcaster copies every click and
key to the other four windows, so all four Explorers bolt the same mob and the tank's F1 (Taunt)
lands on it too. Learned from the user's own play (2026-10-12, mbwatchplay):

  * a bolt every ~5.8 s: cursor onto the mob (attack cursor), F1, click ~0.4 s later
  * targets anywhere on screen (median 190 px, up to ~400 px from the character)
  * between bolts a walk click toward the next mob; roams the field instead of standing still
  * sits (Insert) when the lowest SP is ~25%, stands at ~85% or when anyone is hit
  * a window that doesn't follow (a skill left armed, or left behind): right-click IN THAT WINDOW
    (cancels the armed skill), then it walks back to window 1

usage: mbleader.py [FIELD_X0 FIELD_Y0 FIELD_X1 FIELD_Y1]   (roam box; default Andes Boxeroo field)
"""
import os
import random
import sys
import threading
import time

import numpy as np

import mbcoords
import mbhunt
import mbtarget as t

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "multibox")
TODO = os.path.join(STATE, "points.todo")   # windows with level-up points to spend (written by mbsuper)
HOLD = os.path.join(STATE, "leader.hold")   # the supervisor is using a window (stats, skills): hands off


def held():
    """Index of the window the supervisor is working in (None: none)."""
    try:
        return int(open(HOLD).read())
    except (OSError, ValueError):
        return None
os.environ["MB_STATE"] = STATE
D = [":61", ":62", ":63", ":64", ":65"]
LEAD = D[0]
O = t.ORIGIN
CX, CY = t.PLAYER_BAR_X, t.PLAYER_BAR_Y
REACH = 400
# the tank taunts the mob just bolted: Slowpoke Snails take two volleys and hit window 1 in
# between (it died a minute after arriving, 2026-10-15); Elks died in one volley and the taunt
# only pulled the tank into fresh fights
TAUNT_BOLTED = True
# each Ami casts on its own with a verified click (mirrored clicks missed snails, and a follower
# without its skill ready melee-attacked one and died)
PINK_ONLY = os.environ.get("MB_PINK", "1") == "1"   # only the texture-swapped (pink) mobs are hunted; MB_PINK=0: label/bar search
PER_WINDOW_BOLT = False             # the user: window 1 clicks, broadcast copies it to the others at the same moment
HUNT_THREE = True                   # three Amis hunt while one walks back (~5 min of idle per death otherwise; user: never idle)
COOLDOWN = 4.6                      # Lightning Bolt LV10: 8400 - 10*400 = 4400 ms (SkillCooldown_Lj) + margin
SIT_BELOW, STAND_AT = 0.35, 0.85   # two bolts in reserve: at Argent 9A a party caught at 5-9% SP could not finish its mob (14 deaths in 1 h 50)
HP_UP = 0.7                         # resting ends when every Ami has at least this much HP (0.5: Amis stood at half HP and the snails killed them, 2-3 deaths an hour at Lv40, 2026-10-17)
LEAD_SIT = 0.5                     # window 1's own SP reserve
HP_SIT = 0.45                        # rest when any Ami is under 70% (a leader back at 58% fought and died again)
FIELD = (1010, 2895, 1100, 2975)
SAFE = tuple(map(int, os.environ["MB_SAFE"].split(","))) if os.environ.get("MB_SAFE") else (1012, 2970)
# Screen pixels per map tile at the client's Home camera (what "multibox camera" / broadcast ON
# resets every window to): rows are screen x and screen y, columns map dx (east) and dy (south).
# The hand-turned angle mbhunt.A0 was fitted for is lost on every client restart; Home is not.
CAM = ((18.1, 13.3), (-11.8, 17.4))
FEET = (683, 395)


def screen_of(here, goal):
    """Screen point of map tile `goal` with window 1 standing on `here`."""
    dx, dy = goal[0] - here[0], goal[1] - here[1]
    return FEET[0] + CAM[0][0] * dx + CAM[0][1] * dy, FEET[1] + CAM[1][0] * dx + CAM[1][1] * dy


CAMP = (1070, 2935)
TRAVEL = 25                         # further than this from the camp: walk there before hunting
EXIT_DIRS = [(6, -4), (-3, -7), (-7, -1), (7, 3)]    # out of the safe zone: north-east first, then around
KEEP = 22                           # tiles kept from a mob that takes no damage (it hits from the safe zone)
KNOWN_IMMUNE = [] if os.environ.get("MB_SAFE") else [(1040, 2950)]       # the Killer Shroom standing just inside Andes Forest Haven's safe zone
REST_AT = tuple(map(int, os.environ["MB_REST"].split(","))) if os.environ.get("MB_REST") else None   # rest spot inside a safe zone
SAFE_R = int(os.environ.get("MB_SAFE_R", "22"))   # tiles: Andes Forest Haven's safe zone (mobs there take no damage)


def target_hp(im):
    """Fill of the selected mob's HP bar in the Mob Info panel (top, x 256-388, y 30); None if no
    mob is selected."""
    px = im.convert("RGB").load()
    red = [x for x in range(250, 395) if px[x, 31][0] > 100 and px[x, 31][1] < 70 and px[x, 31][2] < 40]
    if not red:
        return None
    return min(1.0, (max(red) - 256) / (388 - 256))


def weak_label(im, b):
    """True if the name label above health bar b is green: a mob below the party's level - in the
    Andes field that is the aggressive Lv19 Killer Shroom (Boxeroo labels are yellow)."""
    x, y = int(b[0]), int(b[1])
    a = np.asarray(im.crop((x - 70, y - 58, x + 80, y - 30)).convert("RGB")).astype(int)
    r, g, bl = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    green = int(((g > 170) & (r < 150) & (bl < 110)).sum())
    yellow = int(((r > 200) & (g > 170) & (bl < 110)).sum())
    return green > 150 and green > 2 * yellow


def bag_free(i, d):
    """Free unlocked bag cells of window d (opens the inventory for a moment), None if unreadable."""
    import mbquest as q
    try:
        q.close_all(STATE, i, d)
        q.alt(d, "e")
        time.sleep(1.2)
        im = t.screenshot(d)
        p = t.find_image(im, "mbinvtitle.png")
        q.alt(d, "e")
        time.sleep(0.4)
        if not p:
            return None
        return [q.slot_state(im, p[0] - 9 + 35 * c, p[1] + 78 + 35 * r) for r in range(6) for c in range(8)].count("empty")
    except Exception as exc:
        log(f"bag check problem: {exc!r}")
        return None


def tank_sell(i, d):
    """The tank sells at Accessory - Rikka in Andes Forest Haven; kept: Blueprints (Encrypted
    too), Sheepskin Scrolls, chests, skill books and gear of its own level band or above."""
    import mblevels
    import mbquest as q
    try:
        lv = mblevels.levels(d)[0] or 30
        os.environ["MB_LEVEL"] = str(lv)
        q.KEEP_WORDS = ["Blueprint", "Sheepskin", "Chest", "Skill", "Book", "Fairy"]
        return q.sell_everything(STATE, i, d, shop="Accessory - Rikka", shop_xy=(997, 2968))
    except Exception as exc:
        log(f"tank: selling failed: {exc!r}")
        return -1


def tank_town(i, d, back):
    """Outside Andes (no shop near the field): the tank alone goes to Argent via Abandon Mine
    Haven, sells and repairs at Blacksmith Goldie, and comes back; the Explorers keep bolting.
    Every death costs 5% durability: the tank's sword was at 79/200 after a morning at 9A."""
    import mbquest as q
    import mbrepair
    try:
        far_walk(d, (1904, 2810))
        if not q.teleport(STATE, i, d, (1912, 2802), "Haven Teleporter - Meiya", ["Teleport: Argent City"]):
            log("tank: Meiya's teleport to Argent failed")
            return
        import mblevels
        os.environ["MB_LEVEL"] = str(mblevels.levels(d)[0] or 30)
        q.KEEP_WORDS = ["Blueprint", "Sheepskin", "Chest", "Skill", "Book", "Fairy"]
        n = q.sell_everything(STATE, i, d)
        log(f"tank: sold {n} item(s) at Goldie, repaired {mbrepair.repair(i, d)}")
        t._trace(d, 2194, 2758)                 # around the fence to Jovial
        time.sleep(8)
        for _ in range(2):
            if q.teleport(STATE, i, d, (2187, 2776), "Argent Teleporter - Jovial", ["Teleport: Abandon Mine Haven"]):
                break
        far_walk(d, back)
        log(f"tank: back from Argent at {mbcoords.read(d)}")
    except Exception as exc:
        log(f"tank: town trip failed: {exc!r}")


def far_walk(d, target):
    """A long way back (a respawn at Abandon Mine Haven is ~550 tiles from Argent 9A): the planned
    safe-path walk. The game's own auto-walk gives up over that distance - three revived
    characters stood at the haven while two Amis hunted alone."""
    import subprocess
    p = mbcoords.read(d)
    if not p or not target or max(abs(p[0] - target[0]), abs(p[1] - target[1])) <= 60:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run([sys.executable, os.path.join(here, "mbtarget.py"), "goto", STATE,
                        str(int(target[0])), str(int(target[1])), d], cwd=here, timeout=900,
                       env={**os.environ, "MB_STATE": STATE, "MB_STATUS_FILE": f"walk-{d[-1]}"},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        log(f"window {d[-1]}: the long walk back took too long")


GROUND_CURSOR = 2089885924


def walks(d):
    """True if window d's character walks when open ground is clicked, False if it stays (seated),
    None if no clear ground was found. Only points with the plain ground cursor are clicked: a
    click on a mob or a player selects it and moves nobody."""
    fails = 0
    if t.cursor_id(d) in t.SKILL_FRAMES:       # an armed skill is no ground cursor: drop it first
        mbhunt.fast_drop(d)
    for x, y in ((900, 560), (420, 560), (950, 300), (380, 250), (1000, 450), (560, 620)):
        t.move(d, O[0] + x, O[1] + y)
        time.sleep(0.25)
        if t.cursor_id(d) != GROUND_CURSOR:
            continue
        a = mbcoords.read(d)
        t.click_at(d, O[0] + x, O[1] + y)
        time.sleep(1.5)
        b = mbcoords.read(d)
        if a and b:
            if a != b:
                return True
            fails += 1                          # water and cliffs show the ground cursor too:
            if fails >= 2:                      # one click that moves nobody proves nothing
                return False
    return None


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "leader: " + msg + "\n")


def key1(k):
    t.key(LEAD, "keydown", k, "sleep", "0.06", "keyup", k)


class HotSpots:
    """Where bolts were cast: 6x6-tile cells, the last 2 hours, kept across restarts. When nothing
    is in sight the party goes to the busiest cell nearby; a cell found empty is skipped 3 min."""
    CELL, KEEP = 6, 7200

    def __init__(self, path):
        import json
        self.path, self.skip = path, {}
        try:
            self.log = [tuple(e) for e in json.load(open(path)) if time.time() - e[2] < self.KEEP]
        except (OSError, ValueError):
            self.log = []

    def add(self, p):
        self.log.append((p[0], p[1], time.time()))

    def save(self):
        import json
        self.log = [e for e in self.log if time.time() - e[2] < self.KEEP]
        try:
            json.dump(self.log, open(self.path, "w"))
        except OSError:
            pass

    def next_spot(self, here, field):
        fx0, fy0, fx1, fy1 = field
        now = time.time()
        counts = {}
        for x, y, ts in self.log:
            if now - ts < self.KEEP and fx0 - 20 <= x <= fx1 + 20 and fy0 - 20 <= y <= fy1 + 20:
                c = (x // self.CELL, y // self.CELL)
                counts[c] = counts.get(c, 0) + 1
        best = [c for c in sorted(counts, key=lambda c: -counts[c]) if self.skip.get(c, 0) < now and counts[c] >= 3]
        if not best:
            return None
        c = best[0]
        return (c[0] * self.CELL + self.CELL // 2, c[1] * self.CELL + self.CELL // 2)

    def empty(self, spot):
        self.skip[(spot[0] // self.CELL, spot[1] // self.CELL)] = time.time() + 180


class Leader:
    def __init__(self, field):
        self.field = field
        self.shrooms = field == FIELD           # green labels first only where they are Killer Shrooms
        # where it waits when no mob is in sight: the field's middle; in the Andes Boxeroo field a
        # spot clear of the haven's undamageable Killer Shroom (KNOWN_IMMUNE + KEEP)
        self.camp = CAMP if field == FIELD else ((field[0] + field[2]) // 2, (field[1] + field[3]) // 2)
        self.patrol, self.leg, self.stuck, self.walk_from = [], 0, 0, None
        if field != FIELD:                      # a loop of six points, 25 tiles inside the field's edge
            x0, y0, x1, y1 = field[0] + 25, field[1] + 25, field[2] - 25, field[3] - 25
            xm = (x0 + x1) // 2
            self.patrol = [(xm, y0), (x1, y0), (x1, y1), (xm, y1), (x0, y1), (x0, y0)]
            self.camp = self.patrol[0]
        self.sitting = False
        self.last_aim = (0, 0, 0.0)
        self.order = (0, 0, 0.0)
        self.taunts = 0
        self.skip, self.miss_at, self.full_at = [], None, None
        self.wait_log = 0.0
        self.lead_down = False
        self.exit_dir, self.exit_from = 0, None
        self.misses = 0
        self.fixing = getattr(self, "fixing", set())
        self.tank_frame = None
        self.low_sp = False
        self.hot = HotSpots(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"hotspots-{field[0]}-{field[1]}.json"))
        self.stuck_at = {}
        self.sat_at = 0.0
        self.own_read_at, self.own_bars = 0.0, ([], [])
        self.tracing = set()
        self.frame_win = {}                 # party frame -> window index (1..3) of the Amis
        self.immune = []
        self.prev_hp = None
        self.last_hurt = 0.0
        self.tank_hurt_at = 0.0          # the tank itself losing HP: it sits, the Amis bolt the mob on it
        self.casts = 0
        self.fixing = set()

    def hurt_watch(self):
        """~8x a second: anyone in the party losing HP -> last_hurt (the main loop answers at once)."""
        prev = None
        while True:
            try:
                hp = t.party_hp(t.screenshot(LEAD))
                if prev and any(a is not None and b is not None and 0 < b < a - 0.01 for k, (a, b) in enumerate(zip(prev, hp)) if k != self.tank_frame):
                    self.last_hurt = time.monotonic()
                prev = hp
            except Exception:
                pass
            time.sleep(0.12)

    def in_safe_zone(self, here):
        return here and max(abs(here[0] - SAFE[0]), abs(here[1] - SAFE[1])) <= SAFE_R

    # --- what window 1 sees ----------------------------------------------------------------
    def party(self):
        im = t.screenshot(LEAD)
        hp, sp = t.party_hp(im), t.party_sp(im)
        # the tank being hit is its job (it sits for it): only an Ami taking damage counts - with
        # the tank's frame the resting party stood up and sat down every few seconds
        if self.prev_hp and any(a is not None and b is not None and 0 < b < a - 0.01
                                for k, (a, b) in enumerate(zip(self.prev_hp, hp)) if k != self.tank_frame):
            self.last_hurt = time.monotonic()
        self.prev_hp = hp
        return im, hp, sp

    def target(self, im):
        """The nearest mob on screen with the attack cursor on it (a mob on the party first)."""
        if t.cursor_id(LEAD) in t.SKILL_FRAMES:
            mbhunt.fast_drop(LEAD)
        self.aim_map = None
        # (not in a Battle Zone like Argent 9A: players show the attack cursor there, and the
        # "attacker" found next to the party was a teammate - no bolt was ever cast)
        if self.shrooms and time.monotonic() - self.last_hurt < 5:      # the mob hitting us comes first, always
            for dx, dy in mbhunt.RING[:16]:
                hit = mbhunt.probe_point(LEAD, O[0] + CX + dx, O[1] + CY - 28 + dy)
                if hit:
                    return hit
        # Killer Shrooms first, at any distance in reach: one hits for ~795 and an Ami has ~740 HP,
        # so the one that walks up kills before anyone can react; a volley kills it on the way
        # pink mobs first (their texture swapped for solid magenta): the body itself, found in
        # one pass, no label/bar search and no probing around
        pink = self.pink_target(im)
        if pink or PINK_ONLY:
            return pink                         # the label/bar search cost ~1 s per miss
        found = t.hp_bars(im) if self.shrooms else t.mob_bars(im)     # mob_bars: only bars under a "Lv" name
        # the mob bolted last stays the target until it is gone (its bar is the one within 130 px
        # of the last bolt): switching to whichever was nearest spread the volleys over three
        # mobs and sent the party walking between them, most casts never finished
        lx, ly, lt = self.last_aim
        fresh = time.monotonic() - lt < 12

        def same(b):
            return fresh and (b[0] - lx) ** 2 + (b[1] - 40 - ly) ** 2 <= 130 ** 2
        here = mbcoords.read(LEAD)
        tank_pt = None
        if here and time.monotonic() - self.tank_hurt_at < 6:
            tp = mbcoords.read(D[4])
            if tp and max(abs(tp[0] - here[0]), abs(tp[1] - here[1])) <= 25:
                tank_pt = screen_of(here, tp)

        def on_tank(b):                         # a bar just above the tank: the mob hitting it
            return tank_pt is not None and (b[0] - tank_pt[0]) ** 2 + (b[1] - tank_pt[1] + 40) ** 2 <= 110 ** 2
        # low on SP: only what is on the party (a bar within ~3 tiles) - starting fresh fights at
        # 10-20% SP chained into deaths (2026-10-15)
        reach = 150 if self.low_sp else REACH
        bars = sorted((b for b in found if 22 ** 2 < (b[0] - CX) ** 2 + (b[1] - CY) ** 2 <= reach ** 2 or on_tank(b)),
                      key=lambda b: (not on_tank(b), not (self.shrooms and weak_label(im, b)), not same(b),
                                     (b[0] - CX) ** 2 + (b[1] - CY) ** 2))
        now = time.monotonic()
        self.immune = [m for m in self.immune if m[2] > now]
        for b in bars[:8]:                      # (the nearest four can all be party members' own bars)
            if here:                            # mobs inside the haven's safe zone can't be hurt
                w = self.map_of(here, b)
                if self.in_safe_zone(w) or any(abs(w[0] - x) <= 4 and abs(w[1] - y) <= 4 for x, y, _ in self.immune):
                    continue
                self.skip = [m for m in self.skip if m[2] > now]
                if any(abs(w[0] - x) <= 5 and abs(w[1] - y) <= 5 for x, y, _ in self.skip):
                    continue
                # a bar whose mob couldn't be pointed at (behind a tree, a player's pet): not
                # probed again for 20 s (re-probing it every pass took half the leader's time)
                self.noprobe = [m for m in getattr(self, "noprobe", []) if m[2] > now]
                if any(abs(w[0] - x) <= 3 and abs(w[1] - y) <= 3 for x, y, _ in self.noprobe):
                    continue
                self.aim_map = w
            hit = mbhunt.probe_mob(LEAD, O[0] + b[0], O[1] + b[1])
            if hit:
                return hit
            if here:
                self.noprobe.append((w[0], w[1], now + 20))
        self.aim_map = None
        return None

    def pink_near(self, im, here):
        """The nearest pink mob inside the field (any distance on screen), or None."""
        fx0, fy0, fx1, fy1 = self.field
        best = None
        for b in t.pink_mobs(im):
            w = self.map_of(here, (b[0], b[1] + 25))
            if not (fx0 - 3 <= w[0] <= fx1 + 3 and fy0 - 3 <= w[1] <= fy1 + 3):
                continue
            dist = (b[0] - CX) ** 2 + (b[1] - CY) ** 2
            if best is None or dist < best[0]:
                best = (dist, b)
        return best[1] if best else None

    def pink_target(self, im):
        """The nearest magenta mob that shows the attack cursor (a mob on the tank first)."""
        found = t.pink_points(im)
        if not found:
            return None
        pts_of = {c: pts for c, pts in found}
        blobs = list(pts_of)
        here = mbcoords.read(LEAD)
        now = time.monotonic()
        tank_pt = None
        if here and now - self.tank_hurt_at < 6:
            tp = mbcoords.read(D[4])
            if tp and max(abs(tp[0] - here[0]), abs(tp[1] - here[1])) <= 25:
                tank_pt = screen_of(here, tp)
        # the whole screen: the game walks each Ami into range by itself (the 400 px reach left
        # visible pink snails alone and the party walked about instead)
        reach = 150 if self.low_sp else 180    # within bolt range (~8 tiles): window 1 walked ahead into 3 snails alone and died; (whole screen: window 1 ran off after far snails, the others fell behind and never cast)
        lx, ly, lt = self.last_aim
        fresh = now - lt < 12

        def key(b):
            on_tank = tank_pt is not None and (b[0] - tank_pt[0]) ** 2 + (b[1] - tank_pt[1] + 20) ** 2 <= 90 ** 2
            same = fresh and (b[0] - lx) ** 2 + (b[1] - ly) ** 2 <= 90 ** 2
            return (not on_tank, not same, (b[0] - CX) ** 2 + (b[1] - CY) ** 2)
        self.skip = [m for m in self.skip if m[2] > now]
        self.noprobe = [m for m in getattr(self, "noprobe", []) if m[2] > now]
        for b in sorted((b for b in blobs if (b[0] - CX) ** 2 + (b[1] - CY + 20) ** 2 <= reach ** 2), key=key)[:6]:
            w = self.map_of(here, (b[0], b[1] + 25)) if here else None
            if w and any(abs(w[0] - x) <= 4 and abs(w[1] - y) <= 4 for x, y, _ in self.skip + self.noprobe):
                continue
            # only snails inside the field: chasing one north took the party into the Grassland
            # Wolves (aggressive, L33) and all four Amis died at once (2026-10-15 23:13)
            fx0, fy0, fx1, fy1 = self.field
            if w and not (fx0 - 3 <= w[0] <= fx1 + 3 and fy0 - 3 <= w[1] <= fy1 + 3):
                continue
            self.aim_map = w
            for px, py in pts_of[b][:3]:        # (a dead one stays pink a while: give up fast)
                hit = mbhunt.probe_point_wait(LEAD, O[0] + px, O[1] + py)
                if hit:
                    mbhunt.STATS["pink"] += 1
                    return hit
            if w:
                self.noprobe.append((w[0], w[1], now + 4))   # (20 s left snails beside the party alone)
        self.aim_map = None
        return None

    @staticmethod
    def map_of(here, b):
        """Map tile under a mob's health bar at screen point b (window 1 at map point here)."""
        bx, by = b[0] - FEET[0], b[1] - CY
        (a, bb), (c, dd) = CAM
        det = a * dd - bb * c
        return here[0] + (dd * bx - bb * by) / det, here[1] + (-c * bx + a * by) / det

    def note_bolt(self):
        """A normal mob dies to one volley: a spot bolted 3 times in 40 s holds a mob that takes no
        damage (inside the haven's safe zone, whose edge isn't exactly known): skip it 15 min."""
        w, now = self.aim_map, time.monotonic()
        # only near the haven: mobs walk up to the party one after another, so far from the safe
        # zone the same spot is bolted again and again with a fresh full-HP mob each time (the
        # camp itself got flagged, and the keep-away rule then drove the party off it)
        if not w or max(abs(w[0] - SAFE[0]), abs(w[1] - SAFE[1])) > 45:
            return
        self.bolted = [(x, y, t0) for x, y, t0 in self.bolted if now - t0 < 40] + [(w[0], w[1], now)]
        if sum(abs(w[0] - x) <= 4 and abs(w[1] - y) <= 4 for x, y, _ in self.bolted) >= 3:
            # let the bolt land first: mobs walk up to the party one after another, so the same
            # spot is bolted again and again; read 0.8 s after a bolt, a fresh mob was still full
            time.sleep(1.8)
            hp = target_hp(t.screenshot(LEAD))
            if hp is None or hp < 0.97:          # it is losing HP (just tough): keep killing it
                self.bolted = [(x, y, t0) for x, y, t0 in self.bolted if not (abs(w[0] - x) <= 4 and abs(w[1] - y) <= 4)]
                return
            self.immune.append((w[0], w[1], now + 900))
            self.bolted = [(x, y, t0) for x, y, t0 in self.bolted if not (abs(w[0] - x) <= 4 and abs(w[1] - y) <= 4)]
            log(f"mob at {w[0]:.0f},{w[1]:.0f} takes no damage (safe zone): ignored for 15 min")

    def bolt(self, pt):
        x, y = pt
        if PER_WINDOW_BOLT:
            self.bolt_each(x, y)
        else:
            # plain and simple (the user): F1, click, right-click - broadcast copies all of it
            t.key(LEAD, "keydown", "F1", "sleep", "0.05", "keyup", "F1", "sleep", "0.3")
            t.key(LEAD, "mousemove", str(int(x)), str(int(y)), "sleep", "0.05",
                  "mousedown", "1", "sleep", "0.08", "mouseup", "1")
            # a right-click after every bolt drops any skill still armed (the user; Escape and
            # clicks on the interface don't). Not mirrored: a mirrored right-click held while
            # mbtrack moved a follower's cursor turned that Ami's camera. Broadcast paused, each
            # window clicked straight, the cursor still while the button is down.
            time.sleep(1.0)                     # (at 0.35 s it cancelled followers still walking into range)
            self.drop_skills()
        self.casts += 1
        self.last_aim = (x - O[0], y - O[1], time.monotonic())
        here = mbcoords.read(LEAD)
        if here:                                # for the tank: the mob's map tile (bar ~40 px below the body)
            self.order = (*self.map_of(here, (x - O[0], y - O[1] + 40)), time.monotonic())
        if True:                                # every bolt (2-minute checks count them)
            log(f"bolt {self.casts} at {int(x - O[0])},{int(y - O[1])}")
        if here:
            self.hot.add(here)

    def bolt_each(self, x, y):
        """Every Ami casts on its own, broadcast paused: F1, then a click on a point where its own
        cursor shows the attack cursor. A mirrored click missed small Slowpoke Snails in some
        windows, and a follower whose F1 wasn't ready walked up and hit the snail with its
        dagger and died (2026-10-15). A window that finds no attack cursor drops the skill."""
        offs = [(0, 0)]
        try:
            for line in open(os.path.join(STATE, "offsets")).read().split("\n")[:3]:
                a, b = line.split()
                offs.append((int(a), int(b)))
        except (OSError, ValueError):
            pass
        while len(offs) < 4:
            offs.append((0, 0))
        pause = os.path.join(STATE, f"pause.{os.getpid()}.bolt")
        t.write(pause, "1\n")
        time.sleep(0.15)

        def one(k):
            d = D[k]
            if k and (k in self.fixing or (t.own_hp(d) or 0) == 0):
                return
            px, py = x + offs[k][0], y + offs[k][1]
            # find the spot first (an armed skill shows its own cursor, never the attack one),
            # then F1 and the click; nothing found: no click at all (a plain click is a melee hit)
            hit = (px, py) if k == 0 else None
            if not hit:
                if t.cursor_id(d) in t.SKILL_FRAMES:
                    mbhunt.fast_drop(d)
                # the same pink mob in this window: the blob nearest the expected point
                near = sorted((b for b in t.pink_mobs(t.screenshot(d))
                               if (O[0] + b[0] - px) ** 2 + (O[1] + b[1] - py) ** 2 <= 90 ** 2),
                              key=lambda b: (O[0] + b[0] - px) ** 2 + (O[1] + b[1] - py) ** 2)
                for b in near[:1]:
                    for dx, dy in ((0, 0), (0, -6), (0, 6)):
                        hit = mbhunt.probe_point_wait(d, O[0] + b[0] + dx, O[1] + b[1] + dy)
                        if hit:
                            break
            if not hit:
                for dx, dy in [(0, 0)] + mbhunt.RING[:12]:
                    hit = mbhunt.probe_point_wait(d, px + dx, py + dy)
                    if hit:
                        break
            if hit:
                t.key(d, "keydown", "F1", "sleep", "0.05", "keyup", "F1", "sleep", "0.3",
                      "mousemove", str(int(hit[0])), str(int(hit[1])), "sleep", "0.05",
                      "mousedown", "1", "sleep", "0.08", "mouseup", "1")
        try:
            th = [threading.Thread(target=one, args=(k,), daemon=True) for k in range(4)]
            for h in th:
                h.start()
            for h in th:
                h.join(3)
        finally:
            if os.path.exists(pause):
                os.remove(pause)

    def walk(self, im):
        """A ground click toward the nearest mob further out (only ever a click: typing an Alt+R
        walk in window 1 is copied half-garbled to the others and split the party)."""
        bars = sorted((b for b in (t.hp_bars(im) if self.shrooms else t.mob_bars(im)) if (b[0] - CX) ** 2 + (b[1] - CY) ** 2 > (REACH * 0.7) ** 2),
                      key=lambda b: (b[0] - CX) ** 2 + (b[1] - CY) ** 2) if not PINK_ONLY else []
        here = mbcoords.read(LEAD)
        fx0, fy0, fx1, fy1 = self.field
        if here and not (fx0 <= here[0] <= fx1 and fy0 <= here[1] <= fy1):
            # back toward the field: one screen step in the field's direction (map -> screen)
            import mbquest
            mx, my = (fx0 + fx1) // 2, (fy0 + fy1) // 2
            dx, dy = max(-8, min(8, mx - here[0])), max(-8, min(8, my - here[1]))
            x, y = screen_of(here, (here[0] + dx, here[1] + dy))
        elif bars:
            b = bars[0]
            x, y = CX + (b[0] - CX) * 0.5, CY + (b[1] - CY) * 0.5
        elif here and self.patrol and (max(abs(self.camp[0] - here[0]), abs(self.camp[1] - here[1])) <= 5
                                       or self.stuck >= 3):
            # sparse field (Argent 8A: ~45 mobs on 130x120 tiles): nothing in sight at this point
            # (or it can't be reached): on to the next point of the loop
            self.leg = (self.leg + 1) % len(self.patrol)
            self.camp, self.stuck = self.patrol[self.leg], 0
            return
        elif here and PINK_ONLY and self.pink_near(im, here):
            # a pink snail beyond bolt range: the whole party (broadcast) steps toward it together,
            # instead of window 1 walking up alone with its bolt armed
            b = self.pink_near(im, here)
            x, y = CX + (b[0] - CX) * 0.5, CY + (b[1] - CY) * 0.5
        elif here and PINK_ONLY and self.hot.next_spot(here, self.field):
            # nothing in sight: to the spot where most bolts were cast lately (the user: the
            # Amis remember where the snails are); one that came up empty is skipped a while
            spot = self.hot.next_spot(here, self.field)
            if max(abs(spot[0] - here[0]), abs(spot[1] - here[1])) <= 3:
                self.hot.empty(spot)
                return
            dx, dy = max(-8, min(8, spot[0] - here[0])), max(-8, min(8, spot[1] - here[1]))
            x, y = screen_of(here, (here[0] + dx, here[1] + dy))
        elif here and max(abs(self.camp[0] - here[0]), abs(self.camp[1] - here[1])) > 5:
            self.stuck = self.stuck + 1 if here == self.walk_from else 0
            self.walk_from = here
            # nothing in sight (it stood idle 4 min at the field's empty edge): toward the camp,
            # the middle of the spawn; there it waits, mobs respawn
            dx, dy = max(-8, min(8, self.camp[0] - here[0])), max(-8, min(8, self.camp[1] - here[1]))
            x, y = screen_of(here, (here[0] + dx, here[1] + dy))
        else:
            return                              # at the camp, nothing in sight: wait, mobs respawn
        x, y = max(260, min(1150, x)), max(120, min(620, y))
        t.click_at(LEAD, O[0] + x, O[1] + y)

    def stand_all(self):
        """After the stat windows (their clicks stand some characters up, others stay seated: the
        party then "rested" 4 minutes half standing): every Ami standing, each checked in its own
        window with broadcast paused, so the next sit is one Insert for all again."""
        pause = os.path.join(STATE, f"pause.{os.getpid()}")
        t.write(pause, "1\n")
        try:
            time.sleep(0.3)
            for k, d in enumerate(D[:4]):
                if (t.own_hp(d) or 0) == 0 or k in self.fixing:
                    continue
                if walks(d) is False:
                    t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                    time.sleep(0.6)
                    log(f"window {k + 1} was seated: stood up")
        finally:
            if os.path.exists(pause):
                os.remove(pause)
        self.sitting = False

    # --- followers ---------------------------------------------------------------------------
    def check_followers(self):
        """A window that doesn't follow: right-click in it (cancels an armed skill - then the
        cursor moves it again), then walk it back next to window 1. Dead windows revive."""
        lead = mbcoords.read(LEAD)
        lead_skill = t.cursor_id(LEAD) in t.SKILL_FRAMES
        for i, d in enumerate(D[1:], 1):
            if i in self.fixing or held() == i:
                continue
            if i == 4:                          # the tank: only revived here; its own loop does the rest
                if (t.own_hp(d) or 0) == 0:
                    self.fixing.add(i)
                    threading.Thread(target=self.revive, args=(i, d), daemon=True).start()
                continue
            if (t.own_hp(d) or 0) == 0:
                self.fixing.add(i)
                threading.Thread(target=self.revive, args=(i, d), daemon=True).start()
                continue
            p = mbcoords.read(d)
            stuck_skill = t.cursor_id(d) in t.SKILL_FRAMES and not lead_skill
            limit = 14 if i == 4 else 9         # the tank runs to what it taunts: let it
            far = bool(p and lead and max(abs(p[0] - lead[0]), abs(p[1] - lead[1])) > limit)
            if p and lead and max(abs(p[0] - lead[0]), abs(p[1] - lead[1])) > 60:
                self.fixing.add(i)
                log(f"window {i + 1} is far away ({p}): the long walk back")
                threading.Thread(target=self.bring_back, args=(i, d), daemon=True).start()
                continue
            # the same spot three times in a row: stuck on something (window 2 stood at one tile
            # for 2 min, each blocking walk-back cost ~10 s of the leader's loop): the planned
            # walk in its own thread
            if far and p:
                n = self.stuck_at.get(i, (None, 0))
                self.stuck_at[i] = (p, n[1] + 1 if n[0] == p else 1)
                # the same tile twice while far: most likely still seated (out of step with the
                # party's Insert - LuckyForth sat on alone, the user): stand it up
                if self.stuck_at[i][1] >= 2 and not self.sitting and time.monotonic() - self.sat_at > 15:
                    self.stuck_at[i] = (None, 0)
                    self.fixing.add(i)
                    threading.Thread(target=self.stand_one, args=(i, d), daemon=True).start()
                    continue
            if stuck_skill or far:
                x, y = O[0] + t.CLEAR_SPOTS[0][0], O[1] + t.CLEAR_SPOTS[0][1]
                t.key(d, "mousemove", str(x), str(y), "sleep", "0.15",
                      "mousedown", "3", "sleep", "0.06", "mouseup", "3")
                if far and i not in self.tracing:
                    # in its own thread: typing the Alt+R walk takes ~7 s and held up the whole
                    # leader loop (116 s of 300 one time, 2026-10-15)
                    self.tracing.add(i)

                    def go(i=i, d=d, target=tuple(lead)):
                        try:
                            t._trace(d, *target)
                        finally:
                            self.tracing.discard(i)
                    threading.Thread(target=go, daemon=True).start()
                log(f"window {i + 1} not following ({'skill armed' if stuck_skill else f'{p} vs {lead}'}): right-click" +
                    (", walk back" if far else ""))

    # --- the tank: out of broadcast, sits near window 1; Taunt (F2) when anyone is hit; loot ---
    def tank_loop(self):
        import mbtank
        d, TI = D[4], 4
        t.set_excluded(STATE, TI, True)
        sitting, prev, last_taunt, idle = False, None, 0.0, time.monotonic()
        last_pick = 0.0
        stuck, last_insert = 0, 0.0
        last_order = 0.0
        bag_check = time.monotonic() - 360          # first bag check two minutes in
        # learn the real sit state first: a tiny walk that doesn't move means seated -> stand
        for _ in range(2):
            p0 = mbcoords.read(d)
            t.click_at(d, O[0] + CX + 30, O[1] + CY + 20)
            time.sleep(1.2)
            if mbcoords.read(d) != p0:
                break
            t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
            time.sleep(0.8)
        log("tank: standing, ready")

        def stand():
            nonlocal sitting
            if sitting:
                t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                time.sleep(0.5)
                sitting = False

        while True:
            try:
                time.sleep(0.25)
                if held() == 4:
                    continue
                if TI in self.fixing or (t.own_hp(d) or 0) == 0:
                    sitting, prev = False, None
                    continue
                im = t.screenshot(d)
                hp = t.party_hp(im)
                # only the Amis count: a mob on an Ami gets taunted even while the tank sits for
                # its own hits (sitting through Ami hits tripled the deaths at 9A, 2026-10-14)
                hurt = prev is not None and any(a is not None and b is not None and 0 < b < a - 0.01
                                                for a, b in zip(prev[1:], hp[1:]))
                if prev and prev[0] is not None and hp[0] is not None and 0 < hp[0] < prev[0] - 0.01:
                    # the user's rule (2026-10-14): a tank that is being hit sits down and the
                    # Amis kill the mob on it (taunting more mobs only got it killed at 9A)
                    if not self.tank_hurt_at or time.monotonic() - self.tank_hurt_at > 6:
                        log(f"tank: hit ({hp[0]:.0%}): sits, the Amis bolt the mob on it")
                    self.tank_hurt_at = time.monotonic()
                prev = hp
                if time.monotonic() - self.tank_hurt_at < 6 and not (hurt and time.monotonic() - last_taunt > 5.1):
                    if not sitting:
                        if t.cursor_id(d) in t.SKILL_FRAMES:
                            mbhunt.fast_drop(d)
                        t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                        sitting = True
                    idle = time.monotonic()
                    continue
                me = mbcoords.read(d)
                if self.lead_down:
                    # window 1 is in town: stay with an Ami that is on the field (it followed
                    # window 2, which was itself walking back from town, all the way east)
                    fx0, fy0, fx1, fy1 = self.field
                    on_field = [p for p in (mbcoords.read(x) for x in D[1:4])
                                if p and fx0 - 10 <= p[0] <= fx1 + 10 and fy0 - 10 <= p[1] <= fy1 + 10]
                    lead = on_field[0] if on_field else me
                else:
                    lead = mbcoords.read(LEAD)
                # the user's design: the tank taunts and attacks the mob that was just bolted, so
                # what survives the volley (LuckyOne's weak dagger leaves ~10%) hits the tank, not
                # the Ami whose bolt landed first
                ox, oy, ot = self.order
                # only in Andes: at Argent 9A (level 31+) the volley kills its mob, and the "nearest
                # bar to the bolted tile" was then a fresh Elk the tank fought alone (it died 12
                # times in 3 h, 2026-10-14); there it taunts only when the party is hurt
                if (self.shrooms or TAUNT_BOLTED) and me and time.monotonic() - ot < 2.5 and ot > last_order and time.monotonic() - last_taunt > 5.1:
                    last_order = ot
                    bx = FEET[0] + CAM[0][0] * (ox - me[0]) + CAM[0][1] * (oy - me[1])
                    by = CY + CAM[1][0] * (ox - me[0]) + CAM[1][1] * (oy - me[1])
                    near = sorted((b for b in t.hp_bars(im) if (b[0] - bx) ** 2 + (b[1] - by) ** 2 <= 110 ** 2
                                   and (b[0] - CX) ** 2 + (b[1] - CY) ** 2 > 22 ** 2),
                                  key=lambda b: (b[0] - bx) ** 2 + (b[1] - by) ** 2)
                    hit = None
                    for b in near[:2]:
                        hit = mbhunt.probe_mob(d, O[0] + b[0], O[1] + b[1])
                        if hit:
                            break
                    if hit:
                        stand()
                        x, y = hit
                        t.key(d, "keydown", "F2", "sleep", "0.05", "keyup", "F2", "sleep", "0.3",
                              "mousemove", str(int(x)), str(int(y)), "sleep", "0.05",
                              "mousedown", "1", "sleep", "0.08", "mouseup", "1", "sleep", "0.4",
                              "mousedown", "1", "sleep", "0.08", "mouseup", "1")
                        last_taunt = time.monotonic()
                        self.taunts += 1
                        idle = time.monotonic()
                        continue
                if hurt and time.monotonic() - last_taunt > 5.1:
                    stand()
                    if t.cursor_id(d) in t.SKILL_FRAMES:
                        mbhunt.fast_drop(d)
                    hit = None
                    for dx, dy in (mbhunt.RING[:16] if self.shrooms else ()):   # the attacker is on the party
                        hit = mbhunt.probe_point(d, O[0] + CX + dx, O[1] + CY - 28 + dy)
                        if hit:
                            break
                    if not hit:
                        # the mobs nearest window 1 first: its bolt lands first, so the mobs go for
                        # it (it died 3 times in 15 min at 9A while the tank taunted its own Elk)
                        rx, ry = CX, CY
                        lp, mp = mbcoords.read(LEAD), mbcoords.read(d)
                        if lp and mp and max(abs(lp[0] - mp[0]), abs(lp[1] - mp[1])) <= 12:
                            rx, ry = screen_of(mp, lp)
                            ry -= 40                    # the bar sits ~40 px above the feet
                        bars = sorted((b for b in (t.hp_bars(im) if self.shrooms else t.mob_bars(im)) if 22 ** 2 < (b[0] - CX) ** 2 + (b[1] - CY) ** 2 <= 260 ** 2),
                                      key=lambda b: (b[0] - rx) ** 2 + (b[1] - ry) ** 2)
                        for b in bars[:3]:
                            hit = mbhunt.probe_mob(d, O[0] + b[0], O[1] + b[1])
                            if hit:
                                break
                    if hit:
                        x, y = hit
                        t.key(d, "keydown", "F2", "sleep", "0.05", "keyup", "F2", "sleep", "0.3",
                              "mousemove", str(int(x)), str(int(y)), "sleep", "0.05",
                              "mousedown", "1", "sleep", "0.08", "mouseup", "1", "sleep", "0.4",
                              "mousedown", "1", "sleep", "0.08", "mouseup", "1")
                        last_taunt = time.monotonic()
                        log(f"tank: taunt (F2) at {x - O[0]},{y - O[1]}")
                    idle = time.monotonic()
                    continue
                if time.monotonic() - last_pick > 8:
                    last_pick = time.monotonic()  # pick-up key even mid-fight: the label clicks
                    t.key(d, "keydown", "a", "sleep", "0.06", "keyup", "a")    # below rarely ran at 9A
                    # valuable drops lying anywhere on the tank's screen (an NSS stayed on the
                    # ground, the user): read the item labels, walk onto it with a click
                    if time.monotonic() - self.tank_hurt_at > 4:
                        try:
                            import mbreconnect
                            want = [(x, y) for w, x, y in mbreconnect.names_on_screen(im)
                                    if any(k in w.lower() for k in ("sheep", "skin", "blueprint", "bluep", "fairy", "chest"))]
                        except Exception:
                            want = []
                        if want:
                            x, y = want[0]
                            stand()
                            log(f"tank: picking up a drop at {x:.0f},{y:.0f}")
                            t.click_at(d, O[0] + x, O[1] + y - 14)   # the item sits just above its label
                            time.sleep(2.5)
                            t.key(d, "keydown", "a", "sleep", "0.06", "keyup", "a")
                if time.monotonic() - last_taunt < 6:
                    continue                    # fighting what it taunted
                if lead and me and max(abs(me[0] - lead[0]), abs(me[1] - lead[1])) > 60:
                    stand()
                    log(f"tank is far away ({me}): the long walk back")
                    far_walk(d, lead)
                    continue
                if lead and me and max(abs(me[0] - lead[0]), abs(me[1] - lead[1])) > 4:
                    stand()
                    t._trace(d, lead[0] + 2, lead[1] + 2)
                    idle = time.monotonic()
                    time.sleep(1.5)
                    # didn't move twice in a row: maybe seated -> one Insert, then not again for 30 s
                    # (an Insert on every stuck walk sat it down and up every 9 s)
                    stuck = stuck + 1 if mbcoords.read(d) == me else 0
                    if stuck >= 2 and time.monotonic() - last_insert > 30:
                        t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                        sitting, stuck, last_insert = False, 0, time.monotonic()
                        log("tank: not moving: one Insert (stand up)")
                        time.sleep(0.6)
                    continue
                # the user's rule (2026-10-13): a full bag (loot like Encrypted Blueprints was left
                # on the ground) -> the tank alone walks to the haven's shop, sells and comes
                # back; the Explorers keep bolting. Checked every 8 minutes, in a calm moment
                town_req = os.path.join(STATE, "tank.town")
                if not self.shrooms and time.monotonic() - self.last_hurt > 8 and (
                        os.path.exists(town_req) or time.monotonic() - bag_check > 480):
                    free = None if os.path.exists(town_req) else bag_free(TI, d)
                    bag_check = time.monotonic()
                    if os.path.exists(town_req) or (free is not None and free < 2):
                        if os.path.exists(town_req):
                            os.remove(town_req)
                        log(f"tank: to Argent alone (sell, repair); bag free: {free}")
                        stand()
                        tank_town(TI, d, mbcoords.read(LEAD))
                        continue
                if self.shrooms and time.monotonic() - bag_check > 480 and time.monotonic() - self.last_hurt > 8:
                    bag_check = time.monotonic()
                    free = bag_free(TI, d)
                    if free is not None and free < 2:
                        stand()
                        n = tank_sell(TI, d)
                        left = bag_free(TI, d)
                        log(f"tank: bag was full: sold {n} item(s) at Rikka, {left} slot(s) free now")
                        if left is not None and left < 2:      # only kept items left: don't walk again soon
                            bag_check = time.monotonic() + 3600
                        continue
                loot = [p for p in mbtank.parcels(im) if (p[0] - CX) ** 2 + (p[1] - CY) ** 2 <= 220 ** 2]
                if loot and time.monotonic() - self.last_hurt > 6:
                    stand()
                    x, y = loot[0]
                    t.click_at(d, O[0] + x, O[1] + y)
                    time.sleep(2.0)
                    t.key(d, "keydown", "a", "sleep", "0.1", "keyup", "a")
                    idle = time.monotonic()
                    continue
                # sits only when the party sits (or it is low itself): standing ready otherwise
                want = self.sitting or (t.own_hp(d) or 1) < 0.5
                if want and not sitting and time.monotonic() - idle > 3:
                    t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                    sitting = True
                elif not want and sitting:
                    stand()
            except Exception as exc:
                log(f"tank problem: {exc!r}")
                time.sleep(1)

    def revive(self, i, d):
        try:
            log(f"window {i + 1} died: revive, rest, back")
            try:
                t.screenshot(d).save(os.path.join(STATE, time.strftime(f"recap-w{i + 1}-%H%M%S.png")))
            except Exception:
                pass
            t.close_popups(STATE, i, d)
            t.revive(STATE, i, d)
            os.environ["RECOVER_REST"] = "0.9"
            t.rest_one(STATE, i, d)
            far_walk(d, mbcoords.read(LEAD if not self.lead_down else D[1]))
            for _ in range(60):
                lead = mbcoords.read(LEAD)
                p = mbcoords.read(d)
                if p and lead and max(abs(p[0] - lead[0]), abs(p[1] - lead[1])) <= 4:
                    break
                if lead:
                    t._trace(d, *lead)
                time.sleep(4)
            log(f"window {i + 1} is back")
        except Exception as exc:
            log(f"window {i + 1}: revive problem: {exc!r}")
        finally:
            self.fixing.discard(i)

    def bring_back(self, i, d):
        try:
            if t.cursor_id(d) in t.SKILL_FRAMES:
                mbhunt.fast_drop(d)
            # to window 1 when it stands on the field, else to the camp (a walk to window 1 while it
            # was itself on its way back sent two Amis east to the haven, 2026-10-15)
            fx0, fy0, fx1, fy1 = self.field
            # revived in town by itself (the revive step had failed): it stands there with ~2% HP
            # and 0 SP - rest at the safe respawn first, or it dies again on arrival (LuckySecond
            # twice in 5 min, 2026-10-17)
            hp = t.own_hp(d) or 0
            if 0 < hp < 0.9:
                os.environ["RECOVER_REST"] = "0.9"
                t.rest_one(STATE, i, d)
            p = mbcoords.read(LEAD)
            ok = p and fx0 - 10 <= p[0] <= fx1 + 10 and fy0 - 10 <= p[1] <= fy1 + 10
            far_walk(d, p if ok else self.camp)
            log(f"window {i + 1} is back")
        finally:
            self.fixing.discard(i)

    def drop_skills(self):
        pause = os.path.join(STATE, f"pause.{os.getpid()}.drop")
        t.write(pause, "1\n")
        try:
            time.sleep(0.12)                    # mbcast checks pause files 10x a second
            x, y = O[0] + t.CLEAR_SPOTS[0][0], O[1] + t.CLEAR_SPOTS[0][1]
            for k, d in enumerate(D[:4]):
                if k in self.fixing or t.cursor_id(d) not in t.SKILL_FRAMES:
                    continue                    # only a window whose skill is still armed
                t.key(d, "mousemove", str(x), str(y), "sleep", "0.12",
                      "mousedown", "3", "sleep", "0.05", "mouseup", "3")
            time.sleep(0.05)
        finally:
            if os.path.exists(pause):
                os.remove(pause)

    def reset_cameras(self):
        """Home in every window before the party sits (a turned camera sends that Ami's copied
        clicks in another direction). Always True, so it can sit in a condition."""
        for d in D[:4]:
            t.key(d, "key", "Home")
        return True

    def stand_one(self, i, d):
        pause = os.path.join(STATE, f"pause.{os.getpid()}.s{i}")
        t.write(pause, "1\n")
        try:
            if walks(d) is False:
                t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                time.sleep(0.6)
                log(f"window {i + 1} was still seated: stood up")
        except Exception as exc:
            log(f"window {i + 1}: stand check failed: {exc!r}")
        finally:
            if os.path.exists(pause):
                os.remove(pause)
            self.fixing.discard(i)

    def walk_to_camp(self, i, d):
        try:
            if t.cursor_id(d) in t.SKILL_FRAMES:
                mbhunt.fast_drop(d)
            # stuck because seated (out of step with the others' Insert): stand it up first
            pause = os.path.join(STATE, f"pause.{os.getpid()}.w{i}")
            t.write(pause, "1\n")
            try:
                if walks(d) is False:
                    t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                    time.sleep(0.6)
                    log(f"window {i + 1} was seated: stood up")
            finally:
                if os.path.exists(pause):
                    os.remove(pause)
            import subprocess
            subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mbtarget.py"),
                            "goto", STATE, str(self.camp[0]), str(self.camp[1]), d], timeout=600,
                           cwd=os.path.dirname(os.path.abspath(__file__)),
                           env={**os.environ, "MB_STATE": STATE, "MB_STATUS_FILE": f"walk-{d[-1]}"},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            log(f"window {i + 1}: walk to the camp failed: {exc!r}")
        finally:
            self.fixing.discard(i)

    def lead_dead(self):
        """Window 1 died: revive it with broadcast paused (its clicks must not reach the others),
        rest it, then walk back with broadcast ON - the others are already there."""
        pause = os.path.join(STATE, f"pause.{os.getpid()}")
        t.write(pause, "1\n")
        self.lead_down = True
        threading.Thread(target=self.defend_while_down, args=(self.sitting,), daemon=True).start()
        try:
            log("window 1 died: revive, rest, back")
            try:                                # the Death Recap names the killer and the hit
                t.screenshot(LEAD).save(os.path.join(STATE, time.strftime("recap-%H%M%S.png")))
            except Exception:
                pass
            t.close_popups(STATE, 0, LEAD)
            t.revive(STATE, 0, LEAD)
            os.environ["RECOVER_REST"] = "0.9"
            t.rest_one(STATE, 0, LEAD)
            # back to a living Ami that is on the field; nobody there (most of the party died
            # too, 2026-10-15: the leader walked to an Ami still on its way back and the party
            # stood outside the field): the camp
            fx0, fy0, fx1, fy1 = self.field
            on_field = [p for p in (mbcoords.read(x) for x in D[1:4] if (t.own_hp(x) or 0) > 0)
                        if p and fx0 - 10 <= p[0] <= fx1 + 10 and fy0 - 10 <= p[1] <= fy1 + 10]
            target = on_field[0] if on_field else self.camp
            far_walk(LEAD, target)
            for _ in range(60):
                p = mbcoords.read(LEAD)
                if p and max(abs(p[0] - target[0]), abs(p[1] - target[1])) <= 3:
                    break
                t._trace(LEAD, *target)
                time.sleep(4)
        finally:
            if os.path.exists(pause):
                os.remove(pause)
            self.lead_down = False
        self.sitting = False

    def lead_dead_walk(self):
        """Window 1 alive at the haven spawn: the walk back part of lead_dead (broadcast paused)."""
        pause = os.path.join(STATE, f"pause.{os.getpid()}")
        t.write(pause, "1\n")
        self.lead_down = True
        try:
            far_walk(LEAD, self.camp)
        finally:
            if os.path.exists(pause):
                os.remove(pause)
            self.lead_down = False

    def defend_while_down(self, were_sitting):
        """While window 1 revives nobody drives the other Amis: the tank held the mobs alone and
        died with them (3 deaths in 90 s). Each living Ami bolts the nearest mob from its own
        window until the leader is back (broadcast is paused, nothing is mirrored)."""
        if were_sitting:                        # they sat down together: up again, each in its window
            for i in (1, 2, 3):
                if (t.own_hp(D[i]) or 0) > 0:
                    t.key(D[i], "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
            time.sleep(0.6)
        prev = {}
        while self.lead_down:
            t0 = time.monotonic()
            for i in (1, 2, 3):
                d = D[i]
                if not self.lead_down or i in self.fixing:
                    continue
                if (t.own_hp(d) or 0) == 0:     # the main loop (which revives followers) is busy
                    self.fixing.add(i)
                    threading.Thread(target=self.revive, args=(i, d), daemon=True).start()
                    continue
                try:
                    if t.cursor_id(d) in t.SKILL_FRAMES:
                        mbhunt.fast_drop(d)
                    im = t.screenshot(d)
                    found = t.hp_bars(im) if self.shrooms else t.mob_bars(im)
                    # no new fights without the leader (three Amis leave a Boxeroo alive and it
                    # kills one of them: two wipes this way): only what is hitting this window's
                    # character, or a Killer Shroom in reach
                    hp_now = t.own_hp(d) or 0
                    hit_now = i in prev and hp_now < prev[i] - 0.01
                    prev[i] = hp_now
                    shroom = self.shrooms and any(weak_label(im, b) for b in found
                                                  if (b[0] - CX) ** 2 + (b[1] - CY) ** 2 <= REACH ** 2)
                    if not hit_now and not shroom:
                        continue
                    bars = sorted((b for b in found if 22 ** 2 < (b[0] - CX) ** 2 + (b[1] - CY) ** 2 <= REACH ** 2),
                                  key=lambda b: (not (self.shrooms and weak_label(im, b)), (b[0] - CX) ** 2 + (b[1] - CY) ** 2))
                    for b in bars[:3]:
                        hit = mbhunt.probe_mob(d, O[0] + b[0], O[1] + b[1])
                        if hit:
                            t.key(d, "keydown", "F1", "sleep", "0.05", "keyup", "F1", "sleep", "0.35",
                                  "mousemove", str(int(hit[0])), str(int(hit[1])), "sleep", "0.05",
                                  "mousedown", "1", "sleep", "0.08", "mouseup", "1")
                            break
                except Exception as exc:
                    log(f"window {i + 1}: defending problem: {exc!r}")
            time.sleep(max(0.5, COOLDOWN - (time.monotonic() - t0)))

    # --- main loop ---------------------------------------------------------------------------
    def run(self):
        t.write(os.path.join(STATE, "leader.pid"), f"{os.getpid()}\n")
        log("start: window 1 leads, broadcast copies it to the Amis; the tank sits nearby")
        # learn every Ami's real sit state, each in its own window (a click next to the character
        # landed on a mob, looked like "seated", and the wrong Insert sat the whole party down)
        self.stand_all()
        # which of window 1's party frames is the tank (frame order follows the invite order)
        for _ in range(6):                      # the name OCR misses now and then: retry
            im0 = t.screenshot(LEAD)
            for k in range(4):
                name = t.ocr(im0.crop((76, 143 + 65 * k, 196, 160 + 65 * k)), 7, 3).lower()
                if any(w in name for w in ("crab", "kebab", "kingk", "ebab", "rabk")):
                    self.tank_frame = k + 1     # index 0 is window 1 itself
                # the Amis' frames too (window index by name): one recovering in town is left
                # out of the rest decision (the party sat a minute for an Ami resting in town)
                # (OCR reads e.g. "LucleF orf:h", "Lucl':ySectznd", "Lucl':yThix:d")
                for w, keys in ((1, ("sec", "cond", "ctz", "znd")), (2, ("thi", "hir", "hix")), (3, ("for", "orf", "rth", "f:h"))):
                    if any(key in name for key in keys) and "crab" not in name:
                        self.frame_win[k + 1] = w
            if self.tank_frame:
                break
            time.sleep(1)
        log(f"party standing, hunting (tank is party frame {self.tank_frame}, Ami frames {self.frame_win})")
        threading.Thread(target=self.tank_loop, daemon=True).start()
        threading.Thread(target=self.hurt_watch, daemon=True).start()
        last_bolt, last_check, last_walk, last_report = 0.0, 0.0, 0.0, time.monotonic()
        self.no_mob_since = None
        self.immune, self.bolted, self.aim_map = [], [], None
        self.lead_down = False
        self.rest_best, self.rest_since = 1.0, time.monotonic()
        self.lost_walk = 0.0
        while True:
            if held() == 0:
                time.sleep(0.3)
                continue
            now = time.monotonic()
            if (t.own_hp(LEAD) or 0) == 0:
                self.lead_dead()
                continue
            im, hp, sp = self.party()
            if now - last_check > 5:            # (every 3 s took a sixth of the leader's time)
                last_check = now
                self.check_followers()
            sp0 = sp
            # the tank's SP doesn't count: ~100 SP that taunts empty, and the whole party sat down
            # and waited for it to refill
            away = {k for k, w in self.frame_win.items() if w in self.fixing}
            sp = [v for k, v in enumerate(sp) if k != self.tank_frame and k not in away]
            # nor its HP: back from a death it sits by itself; the Amis sat 3+ minutes among
            # passive mobs waiting for its HP to reach 85%
            hp = [v for k, v in enumerate(hp) if k != self.tank_frame and k not in away]
            lows = [v for v in sp + hp if v is not None and v > 0.03]   # (an empty party slot read 0.01 and the party rested forever)   # SP or HP (dead: 0, skipped)
            low = min(lows) if lows else 1.0
            sps = [v for v in sp if v is not None and v > 0.03]
            hps = [v for v in hp if v is not None and v > 0.03]
            # each Ami's own bars, read in its own window (once a second): window 1's party list
            # showed only three of the four Amis, and the missing one - never counted - kept
            # bolting at 0 SP and died (LuckySecond and LuckyThird, 4 deaths in 45 min)
            if now - self.own_read_at > 1.0:
                self.own_read_at = now
                own_sp, own_hp = [], []
                for k in range(4):
                    if k in self.fixing:
                        continue
                    try:
                        ims = t.screenshot(D[k])
                        a, b = t.party_sp(ims)[0], t.party_hp(ims)[0]
                    except Exception:
                        continue
                    if a is not None and b:            # alive (HP > 0)
                        own_sp.append(a)
                        own_hp.append(b)
                self.own_bars = (own_sp, own_hp)
            if self.own_bars[0]:
                sps, hps = self.own_bars
                lows = [v for v in sps + hps if v is not None]
                low = min(lows) if lows else 1.0
            # window 1 casts every volley, the others miss a copy now and then: its SP runs out
            # first, and the mobs go for it (its bolt lands first) - it died at 0 SP with the
            # others at 70-100% (2026-10-15): it gets a bigger reserve
            lead_sp = sp0[0] if sp0 and sp0[0] is not None else None
            lead_low = lead_sp is not None and 0 < lead_sp < LEAD_SIT
            need_rest = (sps and min(sps) < SIT_BELOW) or (hps and min(hps) < HP_SIT) or lead_low
            if self.sitting:
                # resting but nothing refills (one Ami not really seated: LuckyOne stood at 10% HP
                # for half an hour while the leader waited, 2026-10-14): stand everyone up, and
                # the next pass sits them all down together again
                if low > self.rest_best + 0.02:
                    self.rest_best, self.rest_since = low, now
                elif now - self.rest_since > 25:
                    log(f"resting but nothing refills (lowest {low:.0%}): standing all up, sitting again")
                    self.stand_all()
                    self.rest_best, self.rest_since = low, time.monotonic()
                    continue
                # a mob on the tank gets killed (the user's rule): up when there is SP for it
                # window 1 itself low (its bolt lands first, the snails go for it): it stays seated
                # until it has recovered, whatever else happens (the user: it attacked at low HP)
                lead_hp = hp[0] if hp and hp[0] is not None else 1.0
                lead_ok = (lead_sp is None or lead_sp >= 0.6) and lead_hp >= 0.7
                tank_fight = lead_ok and now - self.tank_hurt_at < 2 and sps and min(sps) > 0.6 and not (hps and min(hps) < 0.6)
                # up once SP is back; HP only has to be out of danger (an Ami regains HP very
                # slowly: LuckyForth sat 2 minutes from 37% HP, the user)
                ready = (not sps or min(sps) >= STAND_AT) and (not hps or min(hps) >= HP_UP)
                # (an Ami being hit stood the party up at 36% SP and window 1 attacked nearly empty:
                # only with SP left for it)
                hurt_up = lead_ok and now - self.last_hurt < 1.5 and sps and min(sps) > 0.6 and not (hps and min(hps) < 0.6)
                if (ready and lead_ok) or hurt_up or tank_fight:
                    key1("Insert")
                    self.sitting = False
                    log(f"stand up (lowest SP {low:.0%})")
                    time.sleep(0.6)
                else:
                    # level-up points are spent here, seated and calm, never mid-fight
                    if os.path.exists(TODO) and now - self.last_hurt > 4 and low < STAND_AT - 0.25:
                        import subprocess
                        log("resting: spending level-up points")
                        try:
                            subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mbsuper.py"), "spend"],
                                           timeout=150, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except subprocess.TimeoutExpired:
                            log("spending points took too long: stopped")
                        if os.path.exists(TODO):
                            os.remove(TODO)
                        if os.path.exists(HOLD):
                            os.remove(HOLD)
                        self.stand_all()
                        continue
                    time.sleep(0.5)
                    continue
            elif need_rest and now - self.last_hurt > 3 and self.reset_cameras() and not (now - self.tank_hurt_at < 3 and sps and min(sps) > 0.45 and not (hps and min(hps) < HP_SIT)):
                if REST_AT:                     # rest inside the safe zone (Icespire snails killed a party resting in the field)
                    here = mbcoords.read(LEAD)
                    if not here or max(abs(here[0] - REST_AT[0]), abs(here[1] - REST_AT[1])) > 3:
                        log(f"rest: into the safe zone at {REST_AT}")
                        t._trace(LEAD, *REST_AT)     # broadcast: everyone traces with it
                        t0 = time.monotonic()
                        while time.monotonic() - t0 < 25:
                            time.sleep(1)
                            here = mbcoords.read(LEAD)
                            if here and max(abs(here[0] - REST_AT[0]), abs(here[1] - REST_AT[1])) <= 3:
                                break
                        time.sleep(2)           # the others arrive a moment later
                key1("Insert")
                self.sitting = True
                self.sat_at = now
                self.rest_best, self.rest_since = low, now
                log(f"sit (lowest SP {low:.0%})")
                time.sleep(0.6)
                continue
            here = mbcoords.read(LEAD)
            # window 1 hundreds of tiles from its field (2026-10-13: it turned up in Argent City,
            # cause unknown, and the bot walked the others across the map after it): stop
            # everything where it stands and leave it to the supervisor / the user
            fx0, fy0, fx1, fy1 = self.field
            if here and (here[0] < fx0 - 250 or here[0] > fx1 + 250 or here[1] < fy0 - 250 or here[1] > fy1 + 250) \
                    and abs(here[0] - 1903) + abs(here[1] - 2807) < 30:
                # at the Abandon Mine Haven spawn (a restart while it walked back from town): walk it
                # back to the camp instead of stopping (it stood there 5 minutes, the user)
                log("window 1 is at the haven spawn: walking it back to the camp")
                self.lead_dead_walk()
                continue
            if here and (here[0] < fx0 - 250 or here[0] > fx1 + 250 or here[1] < fy0 - 250 or here[1] > fy1 + 250):
                log(f"window 1 is at {here}, far from its field: stopping (nobody is walked after it)")
                t.write(os.path.join(STATE, "leader.lost"), f"{here}\n")
                os._exit(3)
            # an undamageable mob (in the safe zone) still attacks: keep KEEP tiles from it
            near = [(x, y) for x, y, _ in self.immune + [(*p, 0) for p in KNOWN_IMMUNE]
                    if here and max(abs(here[0] - x), abs(here[1] - y)) < KEEP]
            if near and now - last_walk > 2.5:
                import mbquest
                x0, y0 = near[0]
                # away from the mob AND from the haven (the mob stands at the safe zone's edge:
                # "away from it" alone sent a party coming back from the haven back into it)
                def unit(vx, vy):
                    m = max(1.0, (vx * vx + vy * vy) ** 0.5)
                    return vx / m, vy / m
                ax, ay = unit(here[0] - x0, here[1] - y0)
                hx, hy = unit(here[0] - SAFE[0], here[1] - SAFE[1])
                dx, dy = unit(ax + 1.5 * hx, ay + 1.5 * hy)
                goal = (here[0] + dx * 10, here[1] + dy * 10)
                x, y = screen_of(here, goal)
                t.click_at(LEAD, O[0] + x, O[1] + y)
                last_walk = now
                log(f"too close to the undamageable mob at {x0:.0f},{y0:.0f}: moving away")
                time.sleep(1.2)
                continue
            if self.in_safe_zone(here) and now - self.last_hurt > 3:
                import mbquest                  # out of the safe zone first, toward the field
                # a step that didn't move the leader ran into something (the haven's hut stands
                # north-east of Wendy): try the next direction
                if self.exit_from == here:
                    self.exit_dir = (self.exit_dir + 1) % len(EXIT_DIRS)
                self.exit_from = here
                ex, ey = EXIT_DIRS[self.exit_dir]
                x, y = screen_of(here, (here[0] + ex, here[1] + ey))
                t.click_at(LEAD, O[0] + x, O[1] + y)
                time.sleep(1.6)
                continue
            # far from the camp (back from the haven) and not under attack: travel there first.
            # Hunting where the first mobs showed up kept the party at the riverside Killer
            # Shrooms north of the haven (3 deaths in a minute; none in 30 min at the camp)
            if (self.shrooms and here and max(abs(self.camp[0] - here[0]), abs(self.camp[1] - here[1])) > TRAVEL
                    and now - self.last_hurt > 3 and now - last_walk > 1.5):
                dx = max(-8, min(8, self.camp[0] - here[0]))
                dy = max(-8, min(8, self.camp[1] - here[1]))
                x, y = screen_of(here, (here[0] + dx, here[1] + dy))
                t.click_at(LEAD, O[0] + max(260, min(1150, x)), O[1] + max(120, min(620, y)))
                last_walk = now
                time.sleep(1.2)
                continue
            # outside the field by more than TRAVEL (the river between: the step-clicks of walk()
            # went nowhere for 10 minutes, 2026-10-15): the planned safe-path walk for the whole
            # party, broadcast paused (an Alt+R typed in window 1 reaches the others garbled)
            fx0, fy0, fx1, fy1 = self.field
            if (not self.shrooms and here and now - self.last_hurt > 5 and now - self.lost_walk > 60
                    and (here[0] < fx0 - TRAVEL or here[0] > fx1 + TRAVEL or here[1] < fy0 - TRAVEL or here[1] > fy1 + TRAVEL)):
                self.lost_walk = now
                log(f"party at {here}, outside its field: walking everyone to the camp {self.camp}")
                pause = os.path.join(STATE, f"pause.{os.getpid()}")
                t.write(pause, "1\n")
                try:
                    import subprocess
                    subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mbtarget.py"),
                                    "goto", STATE, str(self.camp[0]), str(self.camp[1]), *D], timeout=900,
                                   cwd=os.path.dirname(os.path.abspath(__file__)), env={**os.environ, "MB_STATE": STATE},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as exc:
                    log(f"walk to the camp failed: {exc!r}")
                finally:
                    if os.path.exists(pause):
                        os.remove(pause)
                continue
            # Argent 9A (Elk L31 / White Owlie L32 against a level-25 party): a mob needs two full
            # volleys. With an Ami away (walking back from a death) it needs four, and it killed
            # the tank in 26 s and the next Ami after it: no new fights until all four are here
            # (in Andes too: the leader, back first from a wipe, fought alone at the camp and died
            # again; Boxeroos are passive, nothing happens while nobody starts a fight)
            if now - self.last_hurt > 6 and not (self.shrooms and any(
                    weak_label(im, b) for b in t.hp_bars(im) if (b[0] - CX) ** 2 + (b[1] - CY) ** 2 <= REACH ** 2)):
                away = [k + 1 for k, d in enumerate(D[:4])
                        if (t.own_hp(d) or 0) == 0 or not (mbcoords.read(d) and here and
                            max(abs(mbcoords.read(d)[0] - here[0]), abs(mbcoords.read(d)[1] - here[1])) <= 12)]
                # one Ami on the long walk back from town (~5 min from Abandon Mine Haven): the
                # other three keep bolting the passive snails (the user: never idle, 2026-10-14)
                # keep bolting with whoever is here (the user: no standing around); only wait
                # when window 1 would be alone with the tank
                if HUNT_THREE and len(away) == 1 and not self.shrooms and (t.own_hp(D[away[0] - 1]) or 0) > 0:
                    away = []
                if away:
                    if now - self.wait_log > 60:
                        self.wait_log = now
                        log(f"waiting for window(s) {away}: no new fights without all four Amis")
                    time.sleep(1.0)
                    continue
            ready_at = last_bolt + COOLDOWN       # (COOLDOWN - 0.3 under attack hit the game's cooldown)
            if now >= ready_at - 2.5:           # look for the next target during the cooldown (the user), fire the moment it is ready
                self.low_sp = bool(sps and min(sps) < SIT_BELOW) or lead_low
                pt = self.target(im)
                if pt:
                    self.no_mob_since = None
                    time.sleep(max(0.0, ready_at - time.monotonic()))
                    own0 = sp0[0] if sp0 and sp0[0] is not None else None
                    self.bolt(pt)
                    self.note_bolt()
                    last_bolt = time.monotonic()
                    # a bolt that costs window 1 no SP was not cast: two in a row = seated
                    time.sleep(1.5)
                    im1 = t.screenshot(LEAD)
                    own1 = t.party_sp(im1)[0]
                    # a volley that leaves the target at full HP did nothing: a mob across the
                    # river (Argent 9A) can't walk to its attacker and resets to full - 130 bolts
                    # went that way for 1% EXP. Twice on one mob: skip it for two minutes
                    w, thp = self.aim_map, target_hp(im1)
                    if w and thp is not None and thp >= 0.97:
                        if self.full_at and abs(w[0] - self.full_at[0]) <= 5 and abs(w[1] - self.full_at[1]) <= 5:
                            self.skip.append((w[0], w[1], time.monotonic() + 600))   # (2 min: the same mob across the river was bolted again every 2-3 min)
                            self.last_aim = (0, 0, 0.0)
                            self.full_at = None
                            log(f"mob at {w[0]:.0f},{w[1]:.0f} takes no damage (out of reach): skipped 10 min")
                        else:
                            self.full_at = w
                    elif thp is not None:
                        self.full_at = None
                    if own0 is not None and own1 is not None and own1 > own0 - 0.02:
                        self.misses += 1
                        # two bolts at one mob that cost nothing: it can't be reached (across the
                        # river in Argent 9A): leave it for a minute
                        w = self.aim_map
                        if w and self.miss_at and abs(w[0] - self.miss_at[0]) <= 5 and abs(w[1] - self.miss_at[1]) <= 5:
                            self.skip.append((w[0], w[1], time.monotonic() + 60))
                            self.last_aim = (0, 0, 0.0)
                            self.miss_at, self.misses = None, 0
                            log(f"mob at {w[0]:.0f},{w[1]:.0f} can't be reached: skipped for a minute")
                        else:
                            self.miss_at = w
                        # a far target is walked to first: the cast (and its SP cost) comes after
                        # this reading; only a long run of misses means "seated"
                        if self.misses >= 6:
                            log("six bolts cost no SP (seated?): checking every window")
                            self.stand_all()
                            self.misses = 0
                    else:
                        self.misses, self.miss_at = 0, None
                    continue
                if self.no_mob_since is None:
                    self.no_mob_since = now
            # move only when nothing has been in reach for 3 s: stay put while mobs are around
            if (self.no_mob_since is not None and now - self.no_mob_since > 3 and now - last_walk > 2.5
                    and now - self.last_hurt > 2):
                self.walk(im)
                last_walk = time.monotonic()
            if now - last_report > 300:
                last_report = now
                log(f"{self.casts} bolts so far, {self.taunts} taunts on bolted mobs")
                if getattr(self, "TIMES", None) is not None:
                    log("time per step (s, last 5 min): " + ", ".join(f"{k} {v:.0f}" for k, v in self.TIMES.most_common()))
                    self.TIMES.clear()
                    log(f"probes: {dict(mbhunt.STATS)}; best offsets {sorted(mbhunt.HITS.items(), key=lambda kv: -kv[1])[:4]}")
                    mbhunt.STATS.clear()
                    mbhunt.save_hits()
                    self.hot.save()
            time.sleep(0.2)


if __name__ == "__main__":
    f = tuple(map(int, sys.argv[1:5])) if len(sys.argv) >= 5 else FIELD
    # time spent per step, reported with the 5-minute pace line (where the time between bolts goes)
    import collections
    TIMES = collections.Counter()

    def timed(name, fn):
        def w(*a, **k):
            t0 = time.monotonic()
            try:
                return fn(*a, **k)
            finally:
                TIMES[name] += time.monotonic() - t0
        return w
    for name in ("party", "check_followers", "target", "bolt", "note_bolt", "walk", "stand_all"):
        setattr(Leader, name, timed(name, getattr(Leader, name)))
    Leader.TIMES = TIMES
    mbhunt.load_hits()
    Leader(f).run()
