#!/usr/bin/env python3
"""Fast hunter for the Explorer party (4 Lightning Bolt Explorers + a taunting tank).

  PRIORITY: a mob hitting any party member. A guard thread reads the party HP frames ~10x a
          second; the moment anyone loses HP, the tank taunts the mob next to them (no cursor
          check: speed first) and that mob becomes everyone's target, ahead of anything else.
  target: else the nearest mob with the attack cursor on window 1 (strong kinds skipped).
  cast  : every window aims on ITS OWN screen: the mob's spot is mapped through map coordinates,
          the attack cursor is looked for right there, then F1 + click, all in parallel. The
          tank taunts the same mob first, so it turns on the tank and not on an Explorer.
  kill  : dead when its health bar is gone (two looks), not when window 1's panel is empty;
          until then bolted again the moment the cooldown ends. The next target is lined up
          meanwhile. loot: A everywhere.
  move  : nothing for NO_MOB_S -> the party traces to the next patrol point (regroup first).
  safety: deaths -> group_recover; low HP/SP -> rest (stopped the moment someone is hit).

usage: mbhunt.py STATE SAFE_X SAFE_Y "x,y x,y ..." DISPLAY...   (tank = MB_TANK window)
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import mbcoords
import mbquest
import mbtarget as t

COOLDOWN = float(os.environ.get("BOLT_CD", "4.7"))
NO_MOB_S = float(os.environ.get("NO_MOB_S", "6"))
REST_BELOW = float(os.environ.get("REST_BELOW", "0.5"))
REST_UNTIL = float(os.environ.get("REST_UNTIL", "0.85"))
TANK = int(os.environ.get("MB_TANK", "5")) - 1
TAUNT_CD = 5.1
TARGET_MAX_S = 25
FIGHT_HP = 0.5                      # no new fight while anyone is below this
SAFE_ZONE = (1010, 2966)            # Andes Forest Haven: mobs in it take no damage
FORMATION = {1: (5, 0), 2: (0, 5), 3: (5, 5), 4: (2, 2)}   # tiles from the camp centre: Amis on
                                    # the corners, the tank in the middle where it can taunt anything
                                    # mob on one of us is not hidden under the others (clickable)
BOLT_PX = 220                      # px from the caster: further targets are not bolted
STICK = 3                           # tiles from window 1 before a character is walked back
REACH = 230                         # px from our character: targets further away are not taken
                                    # (bolting them walks the caster off, away from the group)
F1_SLOT = (302, 740, 326, 752)      # lower part of the F1 slot, below its label (empty: std ~11, icon: ~70)
FIRST = [w for w in os.environ.get("HUNT_FIRST", "").split("|") if w]   # aggressive kinds: kill these first
SKIP = [w for w in os.environ.get("HUNT_SKIP", "Siren|Wolf|Boar|Shroom|Bee|Smuggler").split("|") if w]


def log(state, msg):
    with open(os.path.join(state, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "hunt: " + msg + "\n")


PROBES = ((0, 0), (0, -12), (0, 10), (-12, -6), (12, -6), (0, -24))


def fast_drop(d):
    """Drop an armed skill: right-click on ground below the party (no 0.6 s of waiting)."""
    x, y = t.ORIGIN[0] + t.CLEAR_SPOTS[0][0], t.ORIGIN[1] + t.CLEAR_SPOTS[0][1]
    # the move must settle before the button goes down: a late-processed move while the right
    # button is held is a camera drag (the cameras drifted 20-170 degrees apart that way)
    t.key(d, "mousemove", str(x), str(y), "sleep", "0.15", "mousedown", "3", "sleep", "0.05",
          "mouseup", "3", "sleep", "0.05")


# measured 2026-10-10 on Boxeroos/Shrooms (dense sweep, attack cursor hits relative to the bar
# centre): the body is 30-46 px above the bar and a little right of its centre - best spots first
# + small low mobs first (Slowpoke Snail: body 15-30 px above the bar, measured 2026-10-14; the
# Boxeroo offsets missed it and target() spent half the time probing): the learned order (HITS)
# puts whatever hits the current field's mobs first
BODY = [(-3, -25), (0, -21), (-6, -28), (3, -28), (-3, -18),
        (6, -46), (10, -38), (2, -46), (10, -30), (18, -38), (2, -38), (18, -46), (10, -22),
        (-6, -46), (10, -54), (18, -30), (2, -30), (26, -30), (18, -22), (10, -14), (-14, -38)]


HITS = {}                                       # probe offset -> times it showed the attack cursor
# kept across leader restarts (each restart re-learned the Elk offsets for ~10 minutes)
HITS_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "multibox", "probe-hits.json")


def load_hits():
    import json
    try:
        HITS.update({tuple(json.loads(k)): v for k, v in json.load(open(HITS_FILE)).items()})
    except (OSError, ValueError):
        pass


def save_hits():
    import json
    try:
        json.dump({json.dumps(list(k)): v for k, v in HITS.items()}, open(HITS_FILE, "w"))
    except OSError:
        pass


CAM_YAW = 40                           # every camera is turned to this (minimap cone, mbcam.py)
GROUND_CURSOR = 2089885924             # the plain cursor over empty ground
# screen offset per map tile with the cameras aligned (least squares over all windows' party bars,
# 2026-10-10, 3.6 px mean error); refitted live by fit_mapping()
A0 = ((4.7, 22.3), (-20.2, 4.35))


import collections
STATS = collections.Counter()               # probes and hits, reported by the leader every 5 min
HOVER_CURSOR = 558161692                    # the cursor over any entity, before it becomes the attack cursor


def probe_mob(d, bx, by):
    """Screen point on the mob whose health bar is at (bx, by) (absolute coordinates) where the
    attack cursor shows, or None. Offsets that worked before are tried first (a learned table);
    the cursor is polled every ~8 ms after a move and the probe moves on after ~110 ms."""
    STATS["probes"] += 1
    order = sorted(BODY, key=lambda o: -HITS.get(o, 0))
    for dx, dy in order[:6]:                    # the learned best six (ten: a miss cost 1.1 s)
        t.move_fast(d, bx + dx, by + dy)
        end = time.monotonic() + 0.11
        hovered = False
        while time.monotonic() < end:
            time.sleep(0.008)
            c = t.cursor_id(d)
            # over a mob the game first shows the plain hover cursor and the attack cursor only
            # ~250-300 ms later (measured 2026-10-14): on the hover cursor, wait for it
            if c == HOVER_CURSOR and not hovered:
                hovered = True
                end = time.monotonic() + 0.4
            if c in t.ATTACK_FRAMES:
                time.sleep(0.01)                # confirm once (the SRL "confirm" pattern)
                if t.cursor_id(d) in t.ATTACK_FRAMES:
                    HITS[(dx, dy)] = HITS.get((dx, dy), 0) + 1
                    STATS["hits"] += 1
                    STATS["hovered" if hovered else "direct"] += 1
                    return bx + dx, by + dy
                break
    return None


RING = [(22, 0), (16, 11), (0, 15), (-16, 11), (-22, 0), (-16, -11), (0, -15), (16, -11), (39, 10), (16, 25), (-16, 25), (-39, 11), (-39, -10), (-16, -25), (16, -25), (39, -11), (62, 0), (44, 26), (0, 37), (-44, 26), (-62, 0), (-44, -26), (0, -37), (44, -26)]   # around a victim's body, nearest first


def probe_point(d, x, y):
    """The attack cursor right at (x, y): that point, else None (fast polling, ~80 ms max)."""
    t.move_fast(d, x, y)
    end = time.monotonic() + 0.08
    while time.monotonic() < end:
        time.sleep(0.008)
        if t.cursor_id(d) in t.ATTACK_FRAMES:
            return x, y
    return None


def probe_point_wait(d, x, y):
    """probe_point that waits for the attack cursor after the hover cursor (~300 ms later)."""
    t.move_fast(d, x, y)
    end = time.monotonic() + 0.1
    hovered = False
    while time.monotonic() < end:
        time.sleep(0.008)
        c = t.cursor_id(d)
        if c == HOVER_CURSOR and not hovered:
            hovered = True
            end = time.monotonic() + 0.45
        if c in t.ATTACK_FRAMES:
            return x, y
    return None


def bolt(d, x, y):
    t.key(d, "keydown", "F1", "sleep", "0.04", "keyup", "F1", "sleep", "0.05",
          "mousemove", str(int(x)), str(int(y)), "sleep", "0.04",
          "mousedown", "1", "sleep", "0.06", "mouseup", "1")


def attack_here(d):
    """The attack cursor at the mouse. Over any entity the game first shows a generic hover
    cursor and the real one ~0.15 s later: wait for it instead of moving on (followers never
    found their mob that way)."""
    c = t.cursor_id(d)
    for _ in range(6):
        if c in t.ATTACK_FRAMES:
            return True
        if c != mbquest.ENTITY_HOVER:
            return False
        time.sleep(0.04)
        c = t.cursor_id(d)
    return c in t.ATTACK_FRAMES


class Hunter:
    def __init__(self, state, safe, patrol, displays):
        self.state, self.safe, self.patrol, self.displays = state, safe, patrol, displays
        self.k = 0
        self.kills = 0
        self.pool = ThreadPoolExecutor(len(displays) + 2)
        self.pause = os.path.join(state, f"pause.{os.getpid()}")
        self.alert = None                   # (bar, point) of a mob hitting us, set by the guard
        self.lock = threading.Lock()        # window 1's cursor: guard and main loop take turns
        self.last_taunt = 0.0
        self.prev_hp = None
        self.fmap = None
        self.last_hurt = 0.0
        self.tank_lock = threading.Lock()
        self.last_diag = {}
        self.recovering = set()
        self.focus = None                   # (map position, when) of the mob hitting a member
        self.positions_cache = {}
        self.ignored = []                   # (map position, until): unkillable mobs
        self.A = {}
        self.last_calib = 0.0
        self.resting = False
        self.walk_until = 0.0
        self.fmap_tank = None
        self.wl = [threading.Lock() for _ in displays]     # one hand on each window's mouse
        self.bolt_at = [0.0] * len(displays)               # each window's last Lightning Bolt

    def explorers(self):
        ex = t.excluded(self.state)
        return [(i, d) for i, d in enumerate(self.displays) if i != TANK and i not in ex]

    def each(self, fn):
        list(self.pool.map(lambda a: fn(*a), self.explorers()))

    def positions(self):
        return dict(zip(range(len(self.displays)), self.pool.map(mbcoords.read, self.displays)))

    # --- each window's own camera ------------------------------------------------------------
    # Cameras are rotated differently per window (Home does not reset rotation in this client), so
    # a map position is projected with each window's own matrix: screen = FEET + A_i @ (dx, dy),
    # A_i fitted live from where the party members' health bars are on that window's screen.
    def proj(self, i, here, there):
        """Screen point (relative to the window) of map position `there` on window i, whose own
        character stands at `here`."""
        (a, b), (c, d) = self.A.get(i, A0)
        dx, dy = there[0] - here[0], there[1] - here[1]
        return mbquest.FEET[0] + a * dx + b * dy, mbquest.FEET[1] + c * dx + d * dy

    def unproj(self, i, here, sx, sy):
        """Map position under window i's screen point (sx, sy) (relative to the window)."""
        import numpy as np
        A = np.array(self.A.get(i, A0), float)
        dx, dy = np.linalg.solve(A, [sx - mbquest.FEET[0], sy - mbquest.FEET[1]])
        return here[0] + dx, here[1] + dy

    def on_screen(self, i, pt, pos):
        """Window 1's screen point (absolute) -> the same world spot on window i's screen."""
        if i == 0 or not pos.get(i) or not pos.get(0):
            return pt
        w = self.unproj(0, pos[0], pt[0] - t.ORIGIN[0], pt[1] - t.ORIGIN[1])
        x, y = self.proj(i, pos[i], w)
        return t.ORIGIN[0] + x, t.ORIGIN[1] + y

    def yaw(self, i, d, pos):
        """Window i's camera yaw (degrees) from where its party members' bars are, or None."""
        import math
        me = pos.get(i)
        mates = [p for k, p in pos.items() if k != i and p and k not in self.recovering
                 and 2 <= max(abs(p[0] - me[0]), abs(p[1] - me[1])) <= 12] if me else []
        if len(mates) < 2:                      # (a mate on our own tile hides under our bar)
            return None
        im = t.screenshot(d)
        mobs = t.mob_bars(im)
        bars = [b for b in t.hp_bars(im) if b not in mobs
                and (b[0] - t.PLAYER_BAR_X) ** 2 + (b[1] - t.PLAYER_BAR_Y) ** 2 > 8 ** 2]
        if not bars:
            return None
        oy = t.PLAYER_BAR_Y - mbquest.FEET[1]
        best = None
        for th in range(0, 360, 2):
            ct, st_ = math.cos(math.radians(th)), math.sin(math.radians(th))
            for k in (20, 22, 24):
                for c in (0.85, 0.95):
                    e = 0.0
                    for p in mates:
                        dx, dy = p[0] - me[0], p[1] - me[1]
                        x = mbquest.FEET[0] + k * ct * dx - k * st_ * dy
                        y = mbquest.FEET[1] + k * c * st_ * dx + k * c * ct * dy + oy
                        e += min(min(math.hypot(x - q[0], y - q[1]) for q in bars), 25)
                    if best is None or e < best[0]:
                        best = (e, th)
        e, th = best
        # at least two mates must land on a bar (a mate behind a tree may have none)
        return th if e <= 25 * (len(mates) - 2) + 16 else None

    def align_cameras(self):
        """Every camera turned like window 1's: terrain features of each window's view matched
        against window 1's give the rotation (mbrot); small relative right-drags fix it. Then
        the shared screen mapping is refitted from where the party members' bars are."""
        import mbrot
        try:
            with self.wl[0]:
                ref = mbrot.gray(self.displays[0])
            fixed = []
            for i, d in enumerate(self.displays):
                if i == 0 or i in self.recovering or (i != TANK and i in t.excluded(self.state)):
                    continue
                with self.wl[i]:
                    r = mbrot.rotation(d, ref)
                    if r is not None and abs(r) > 3:
                        mbrot.align(d, log=lambda m: None)
                        fixed.append(f"{i + 1}({r:+.0f})")
            if fixed:
                log(self.state, f"camera turned like window 1's in windows {fixed}")
            self.fit_mapping()
        except Exception as exc:
            log(self.state, f"camera alignment problem: {exc!r}")
        self.last_align = time.monotonic()

    def fit_mapping(self):
        """Least squares: map tiles -> screen pixels, from every window's party bars."""
        import numpy as np
        A = np.array(self.A.get(0, A0), float)
        pos = self.positions()
        X, Y = [], []
        for i, d in enumerate(self.displays):
            me = pos.get(i)
            if not me or i in self.recovering:
                continue
            with self.wl[i]:
                im = t.screenshot(d)
            mobs = t.mob_bars(im)
            bars = [b for b in t.hp_bars(im) if b not in mobs
                    and (b[0] - t.PLAYER_BAR_X) ** 2 + (b[1] - t.PLAYER_BAR_Y) ** 2 > 8 ** 2]
            for k, p in pos.items():
                if k == i or not p or k in self.recovering or not 2 <= max(abs(p[0] - me[0]), abs(p[1] - me[1])) <= 12:
                    continue
                dx, dy = p[0] - me[0], p[1] - me[1]
                x, y = A @ [dx, dy] + [t.PLAYER_BAR_X, t.PLAYER_BAR_Y]
                if not bars:
                    continue
                n = min(bars, key=lambda b: (b[0] - x) ** 2 + (b[1] - y) ** 2)
                if (n[0] - x) ** 2 + (n[1] - y) ** 2 < 35 ** 2:
                    X.append([dx, dy])
                    Y.append([n[0] - t.PLAYER_BAR_X, n[1] - t.PLAYER_BAR_Y])
        if len(X) < 8:
            return
        X, Y = np.array(X, float), np.array(Y, float)
        sol = np.linalg.lstsq(X, Y, rcond=None)[0].T
        err = np.linalg.norm(X @ sol.T - Y, axis=1).mean()
        if err < 8:
            for i in range(len(self.displays)):
                self.A[i] = tuple(map(tuple, sol))

    def calibrate_all(self):
        """All cameras look the same way (aligned on broadcast), so ONE screen mapping serves every
        window: fitted jointly from where the party members' health bars are on every screen."""
        import math
        pos = self.positions()
        obs = []                                    # (window's own pos, [mates' pos], party bars)
        for i, d in enumerate(self.displays):
            me = pos.get(i)
            if not me or i in self.recovering or (i != TANK and i in t.excluded(self.state)):
                continue
            mates = [p for k, p in pos.items() if k != i and p and k not in self.recovering
                     and 2 <= max(abs(p[0] - me[0]), abs(p[1] - me[1])) <= 12]
            if not mates:
                continue
            with self.wl[i]:
                im = t.screenshot(d)
            mobs = t.mob_bars(im)
            bars = [bb for bb in t.hp_bars(im) if bb not in mobs
                    and (bb[0] - t.PLAYER_BAR_X) ** 2 + (bb[1] - t.PLAYER_BAR_Y) ** 2 > 8 ** 2]
            if bars:
                obs.append((me, mates, bars))
        self.last_calib = time.monotonic()
        n = sum(len(m) for _, m, _ in obs)
        if n < 4:
            return
        oy = t.PLAYER_BAR_Y - mbquest.FEET[1]

        def cost(A):
            e = 0.0
            for me, mates, bars in obs:
                for p in mates:
                    dx, dy = p[0] - me[0], p[1] - me[1]
                    x = mbquest.FEET[0] + A[0][0] * dx + A[0][1] * dy
                    y = mbquest.FEET[1] + A[1][0] * dx + A[1][1] * dy + oy
                    e += min(min(math.hypot(x - q[0], y - q[1]) for q in bars), 40)
            return e

        best = None
        for th in range(0, 360, 2):
            ct, st_ = math.cos(math.radians(th)), math.sin(math.radians(th))
            for k in (19, 21, 23, 25, 27):
                for c in (0.75, 0.85, 0.95, 1.05):
                    A = ((k * ct, -k * st_), (k * c * st_, k * c * ct))
                    e = cost(A)
                    if best is None or e < best[0]:
                        best = (e, A)
        e, A = best
        if e / n > 12:
            log(self.state, f"camera fit poor ({e / n:.0f} px/member): kept the old mapping")
            return
        for i in range(len(self.displays)):
            self.A[i] = A
        msg = f"camera fitted: {e / n:.0f} px/member, A={[[round(v, 1) for v in r] for r in A]}"
        if msg.split(",")[0] != getattr(self, "_calib_logged", None):
            self._calib_logged = msg.split(",")[0]
            log(self.state, msg)

    # --- the tank --------------------------------------------------------------------------
    def aim(self, i, d, pt, pos):
        """(x, y, ok): where window i must click for the mob that is at window 1's point pt. The
        mob's own health bar on window i's screen nearest the mapped spot, then the attack cursor
        looked for around its body (fast: ~9 probes)."""
        x, y = self.on_screen(i, pt, pos)
        if t.cursor_id(d) in t.SKILL_FRAMES:   # an armed skill's cursor hides the attack cursor
            fast_drop(d)
        im = t.screenshot(d)
        if t.trace_open(im):     # a leftover auto-trace dialog blocks hovering
            t._close_trace(d)
        bx, by = x - t.ORIGIN[0], y - t.ORIGIN[1] + 28
        near = [b for b in t.mob_bars(im)
                if (b[0] - t.PLAYER_BAR_X) ** 2 + (b[1] - t.PLAYER_BAR_Y) ** 2 > 12 ** 2
                and (b[0] - bx) ** 2 + (b[1] - by) ** 2 <= 90 ** 2]
        if near:
            b = min(near, key=lambda b: (b[0] - bx) ** 2 + (b[1] - by) ** 2)
            x, y = t.ORIGIN[0] + b[0], t.ORIGIN[1] + b[1] - 28
        if (x - t.ORIGIN[0] - t.PLAYER_BAR_X) ** 2 + (y + 28 - t.ORIGIN[1] - t.PLAYER_BAR_Y) ** 2 > BOLT_PX ** 2:
            return x, y, False                  # out of bolt range: the caster would walk off to it
        hit = probe_mob(d, x, y + 28)
        if hit:
            return hit[0], hit[1], True
        return x, y, False

    # --- the tank --------------------------------------------------------------------------
    def tank_hit(self, pt, pos):
        """Tank: aim on its own screen, then Taunt + click the mob (+ a plain click: it fights).
        Only a click with the attack cursor counts: Taunt on a party member does nothing."""
        if TANK < 0 or not pos.get(TANK) or not self.tank_lock.acquire(blocking=False):
            return
        try:
          with self.wl[TANK]:
            d = self.displays[TANK]
            x, y, ok = self.aim(TANK, d, pt, pos)
            if not ok:
                log(self.state, "tank: no attack cursor on the target (no taunt)")
                return
            if time.monotonic() - self.last_taunt < TAUNT_CD:
                return                          # no plain attack click: the tank would chase
            self.last_taunt = time.monotonic()
            t.key(d, "keydown", "F1", "sleep", "0.04", "keyup", "F1", "sleep", "0.05",
                  "mousemove", str(int(x)), str(int(y)), "sleep", "0.04",
                  "mousedown", "1", "sleep", "0.06", "mouseup", "1")
            t.write(os.path.join(self.state, "tank-busy"), f"{time.time():.1f}\n")
            log(self.state, "tank: taunted the target")
        finally:
            self.tank_lock.release()

    # --- self-defence: every window watches ITSELF ------------------------------------------
    def strike(self, i, d, im, near, ours=()):
        """Hit the mob attacking the character whose health bar is at `near` on window i's own
        screen: tank -> Taunt + attack, Explorer -> Lightning Bolt. A melee attacker stands ON
        its victim, under our name labels, so it is found by sweeping the attack cursor in a ring
        around the victim (our own characters show the player cursor, a mob the attack cursor),
        after any labelled mob bar right there. Mobs further away are never hit (that pulled more
        of them). Returns the screen point it hit, or None."""
        vx, vy = t.ORIGIN[0] + near[0], t.ORIGIN[1] + near[1] - 28      # the victim's body
        pts = [(t.ORIGIN[0] + b[0], t.ORIGIN[1] + b[1], True) for b in t.mob_bars(im)
               if (b[0] - near[0]) ** 2 + (b[1] - near[1]) ** 2 <= 75 ** 2]
        pts.sort(key=lambda p: (p[0] - vx) ** 2 + (p[1] - vy) ** 2)
        pts += [(vx + dx, vy + dy, False) for dx, dy in RING]
        # the victim already found its attacker: aim exactly there first (the mob hides under
        # our own models on every other screen, so searching around the victim kept failing)
        f = self.focus
        if f and time.monotonic() - f[1] < 4 and self.positions_cache.get(i):
            fx, fy = self.proj(i, self.positions_cache[i], f[0])
            pts = [(t.ORIGIN[0] + fx + dx, t.ORIGIN[1] + fy + dy, False)
                   for dx, dy in ((0, 0), (0, -8), (8, 0), (-8, 0), (0, 8), (0, -16))] + pts
        with self.wl[i]:
            if t.cursor_id(d) in t.SKILL_FRAMES:
                fast_drop(d)
            if t.trace_open(im):
                t._close_trace(d)
            for px, py, is_bar in pts[:9 + len(RING)]:
                hit = probe_mob(d, px, py) if is_bar else probe_point(d, px, py)
                if hit:
                    x, y = hit
                    if i == TANK:                # taunt (when ready) and attack it
                        keys = []
                        if time.monotonic() - self.last_taunt >= TAUNT_CD:
                            keys = ["keydown", "F1", "sleep", "0.04", "keyup", "F1", "sleep", "0.05"]
                            self.last_taunt = time.monotonic()
                        t.key(d, *keys, "mousemove", str(int(x)), str(int(y)), "sleep", "0.04",
                              "mousedown", "1", "sleep", "0.06", "mouseup", "1",
                              *(["sleep", "0.25", "mousedown", "1", "sleep", "0.06", "mouseup", "1"] if keys else []))
                        t.write(os.path.join(self.state, "tank-busy"), f"{time.time():.1f}\n")
                    else:
                        wait = self.bolt_at[i] + COOLDOWN - time.monotonic()
                        if wait > 0:
                            time.sleep(wait)
                            t.move_fast(d, x, y)
                            time.sleep(0.07)
                        bolt(d, x, y)
                        self.bolt_at[i] = time.monotonic()
                    return x, y
        return None

    def defend_loop(self, i, d):
        """~10x a second on window i's OWN screen: did anyone in the party (its own frame + the
        four party frames) lose HP? Then window i hits the mob next to that character at once:
        the tank taunts it, an Explorer bolts it. Every window does this on its own - nobody waits
        for window 1 or the main loop, and no point is mapped between windows."""
        prev = None
        while True:
            try:
                if i in self.recovering or (i != TANK and i in t.excluded(self.state)):
                    prev = None
                    time.sleep(0.5)
                    continue
                im = t.screenshot(d)
                hp = t.party_hp(im)
                was, prev = prev, hp
                if was is None or len(was) != len(hp):
                    continue
                hurt = [k for k, (a, b) in enumerate(zip(was, hp))
                        if a is not None and b is not None and 0 < b < a - 0.004]
                if not hurt:
                    time.sleep(0.05)
                    continue
                self.last_hurt = time.monotonic()
                pos = self.positions()
                fmap = self.fmaps.get(i) or {}
                v = fmap.get(hurt[0])           # frame -> window of the character being hit
                me = pos.get(i)
                if v is not None and v != i and pos.get(v) and me:
                    near = self.proj(i, me, pos[v])
                    near = (near[0], near[1] + (t.PLAYER_BAR_Y - mbquest.FEET[1]))
                else:
                    near = (t.PLAYER_BAR_X, t.PLAYER_BAR_Y)
                ours = [mbquest.predicted(me, p) for k, p in pos.items() if p and me]
                ours = [(x, y + (t.PLAYER_BAR_Y - mbquest.FEET[1])) for x, y in ours]
                self.positions_cache = pos
                hit = self.strike(i, d, im, near, ours)
                if hit and v == i and me:                # its own attacker: share where it is
                    w = self.unproj(i, me, hit[0] - t.ORIGIN[0], hit[1] - t.ORIGIN[1])
                    self.focus = (w, time.monotonic())
                who = f"window {v + 1}" if v is not None else f"frame {hurt[0]}"
                log(self.state, f"{who} hit -> w{i + 1} " +
                    (("taunts" if i == TANK else "bolts") + f" the mob at {hit[0] - t.ORIGIN[0]:.0f},{hit[1] - t.ORIGIN[1]:.0f}"
                     if hit else f"found no mob near {near[0]:.0f},{near[1]:.0f}"))
                prev = None                     # re-read after acting
            except Exception as exc:
                log(self.state, f"defend w{i + 1} problem: {exc!r}")
                time.sleep(0.5)

    def anchor(self, pos):
        """(window, position) the party gathers on: the member nearest the group's median - one
        straggler (a revived character back at the haven) never pulls the others to it."""
        ms = [i for i in range(len(self.displays)) if pos.get(i) and i not in self.recovering
              and (i == TANK or i not in t.excluded(self.state))]
        if not ms:
            return None
        mx = sorted(pos[i][0] for i in ms)[len(ms) // 2]
        my = sorted(pos[i][1] for i in ms)[len(ms) // 2]
        a = min(ms, key=lambda i: max(abs(pos[i][0] - mx), abs(pos[i][1] - my)))
        return a, pos[a]

    def tank_fighting(self):
        try:
            return time.time() - float(t.read(os.path.join(self.state, "tank-busy")).split()[0]) < 8
        except (ValueError, IndexError):
            return False

    def assembled(self):
        pos = self.positions()
        anchor = self.anchor(pos)
        if anchor is None:
            return True
        a, lead = anchor
        for i in range(len(self.displays)):
            if i == a or i in self.recovering or not pos.get(i):
                continue
            if i != TANK and i in t.excluded(self.state):
                continue
            if i == TANK and self.tank_fighting():
                continue                        # it is holding a mob: no need to wait for it
            ox, oy = FORMATION.get(i, (0, 0))
            ax, ay = FORMATION.get(a, (0, 0))
            if max(abs(pos[i][0] - lead[0] - ox + ax), abs(pos[i][1] - lead[1] - oy + ay)) > 8:
                if time.monotonic() - getattr(self, "_asm_log", 0) > 10:
                    self._asm_log = time.monotonic()
                    log(self.state, f"waiting for window {i + 1} to join before the next fight")
                return False
        return True

    # --- stick together ------------------------------------------------------------------------
    def cohesion_loop(self):
        """CAMP: every member (the tank too) keeps its own spot at the camp (patrol[0] + its
        formation offset). Anyone more than STICK tiles off it walks straight back, every second.
        No group walks at all: walking split the party and killed lone characters."""
        sent = {}
        cx, cy = self.patrol[0]
        while True:
            try:
                time.sleep(1.0)
                if self.resting:
                    continue                    # (seated characters can't walk)
                pos = self.positions()
                for i, d in enumerate(self.displays):
                    if i in self.recovering or not pos.get(i):
                        continue
                    if i != TANK and i in t.excluded(self.state):
                        continue
                    ox, oy = FORMATION.get(i, (0, 0))
                    gx, gy = cx + ox, cy + oy
                    far = max(abs(pos[i][0] - gx), abs(pos[i][1] - gy))
                    if far > 150:
                        continue                # another region: brought back by teleport, never a
                                                # walk across the map
                    if far > 1 and time.monotonic() - sent.get(i, 0) > 2.5:
                        sent[i] = time.monotonic()
                        with self.wl[i]:
                            t.trace(d, gx, gy)
                        if far > 12:
                            log(self.state, f"window {i + 1} was {far} tiles from its camp spot: back")
            except Exception as exc:
                log(self.state, f"cohesion problem: {exc!r}")

    # --- the guard thread ------------------------------------------------------------------
    def guard_loop(self):
        while True:
            try:
                self.guard_once()
            except Exception as exc:
                log(self.state, f"guard problem: {exc}")
                time.sleep(0.5)
            time.sleep(0.03)

    def guard_once(self):
        d0 = self.displays[0]
        im = t.screenshot(d0)
        hp = t.party_hp(im)
        prev, self.prev_hp = self.prev_hp, hp
        if prev is None:
            return
        hurt = [k for k, (a, b) in enumerate(zip(prev, hp))
                if a is not None and b is not None and 0 < b < a - 0.004]
        if not hurt:
            return
        self.last_hurt = time.monotonic()
        if self.fmap is None:
            self.fmap = t._party_map(self.displays, 0)
        pos = self.positions()
        me = pos.get(0)
        if not me:
            return
        own = [mbquest.predicted(me, p) for p in pos.values() if p]
        bars = [b for b in t.mob_bars(im)
                if all((b[0] - x) ** 2 + (b[1] - y) ** 2 > 10 ** 2 for x, y in own)]
        best = None
        for k in hurt:
            v = self.fmap.get(k)
            vx, vy = mbquest.predicted(me, pos[v]) if v is not None and pos.get(v) else \
                (t.PLAYER_BAR_X, t.PLAYER_BAR_Y)
            for b in bars:
                d2 = (b[0] - vx) ** 2 + (b[1] - vy) ** 2
                if d2 <= 160 ** 2 and (best is None or d2 < best[0]):
                    best = (d2, b)
        if not best:
            return
        b = best[1]
        pt = (t.ORIGIN[0] + b[0], t.ORIGIN[1] + b[1] - 28)      # the body, just above the bar
        self.pool.submit(self.tank_hit, pt, pos)                # at once, aimed on its own screen
        if self.alert is None:
            self.alert = (b, pt)
            log(self.state, f"attacked (frames {hurt}): tank taunts, everyone on the mob at "
                            f"{round(b[0])},{round(b[1])}")

    # --- targets ---------------------------------------------------------------------------
    def find(self, avoid=None):
        d0 = self.displays[0]
        with self.lock, self.wl[0]:            # (a right-click while another thread moves the
            im = t.screenshot(d0)              # mouse is a right-drag: it rotates the camera)
            if t.trace_open(im):  # a leftover auto-trace dialog blocks hovering
                t._close_trace(d0)
                im = t.screenshot(d0)
            bars = [b for b in t.nearest_bar(t.mob_bars(im), t.PLAYER_BAR_X, t.PLAYER_BAR_Y)
                    if 45 ** 2 < (b[0] - t.PLAYER_BAR_X) ** 2 + (b[1] - t.PLAYER_BAR_Y) ** 2 <= REACH ** 2
                    and (avoid is None or (b[0] - avoid[0]) ** 2 + (b[1] - avoid[1]) ** 2 > 60 ** 2)]
            if False and (SKIP or FIRST) and bars:   # (name OCR: ~1 s per search - too slow)
                try:
                    labels = t.screen_labels(im)
                    if SKIP:
                        bars = [b for b in bars if not t.label_near(labels, b, SKIP)]
                    if FIRST:                   # stable sort: nearest first within each group
                        bars.sort(key=lambda b: 0 if t.label_near(labels, b, FIRST) else 1)
                except Exception:
                    pass
            if len(bars) > 1:                   # higher-level mobs (red/purple label: Boxeroo,
                low = t.low_level_bars(im)      # Elk) first - Owlies (white) give half the EXP
                bars.sort(key=lambda b: 1 if b in low else 0)
            if bars and t.cursor_id(d0) in t.SKILL_FRAMES:
                fast_drop(d0)                   # an armed bolt hides the attack cursor
            for b in bars[:4]:
                if self.alert:
                    break
                if self.is_ignored(b):
                    continue
                pt = probe_mob(d0, t.ORIGIN[0] + b[0], t.ORIGIN[1] + b[1])
                if pt:
                    return b, pt
        return None, None

    def world_of(self, bar):
        """Map position (rounded) of a mob bar on window 1's screen."""
        here = mbcoords.read(self.displays[0])
        if not here:
            return None
        x, y = self.unproj(0, here, bar[0], bar[1] - (t.PLAYER_BAR_Y - mbquest.FEET[1]))
        return round(x), round(y)

    def is_ignored(self, bar):
        now = time.monotonic()
        self.ignored = [(w, until) for w, until in self.ignored if until > now]
        if not self.ignored:
            return False
        w = self.world_of(bar)
        return bool(w) and any(max(abs(w[0] - a[0]), abs(w[1] - a[1])) <= 3 for a, _ in self.ignored)

    def bar_alive(self, bar):
        """The mob's health bar near where it was (it may walk): its new spot, or None."""
        im = t.screenshot(self.displays[0])
        near = [b for b in t.mob_bars(im)
                if (b[0] - bar[0]) ** 2 + (b[1] - bar[1]) ** 2 <= 70 ** 2
                and (b[0] - t.PLAYER_BAR_X) ** 2 + (b[1] - t.PLAYER_BAR_Y) ** 2 > 30 ** 2]
        return min(near, key=lambda b: (b[0] - bar[0]) ** 2 + (b[1] - bar[1]) ** 2) if near else None

    # --- casting ---------------------------------------------------------------------------
    def cast(self, bar, pt):
        """Tank taunts it; every Explorer aims on its own screen and bolts it - in parallel."""
        pos = self.positions()
        # PULL: the tank taunts first and the bolts follow. Taunt hate = the mob's max HP, more
        # than a volley's damage, so the Boxeroo that survives the volley goes for the tank, not
        aimed = {}

        def one(i, d):
            with self.wl[i]:
                x, y, aimed[i] = self.aim(i, d, pt, pos)
                if not aimed[i]:
                    return                      # a blind click walks off / leaves the bolt armed
                bolt(d, x, y)
                self.bolt_at[i] = time.monotonic()
        with self.lock:
            self.each(one)
        miss = [i + 1 for i, ok in aimed.items() if not ok]
        if miss:
            log(self.state, f"no attack cursor on windows {miss} (no bolt from them this time)")
        return sum(1 for ok in aimed.values() if ok)

    def camera_home(self):
        """Home in every window: default camera angle and zoom. Cross-window aiming maps window 1's
        screen to the others and is only right when every camera has the same angle (a rotated
        camera put window 3's mobs 300-400 px from where they were looked for)."""
        for d in self.displays:
            t.key(d, "keydown", "Home", "sleep", "0.1", "keyup", "Home")
        self.last_home = time.monotonic()

    def check_f1(self):
        """Every Explorer has Lightning Bolt on F1 (window 1 lost it once and stood idle):
        an empty F1 slot gets the skill dragged back from the skill window."""
        import numpy as np
        for i, d in self.explorers():
            slot = np.asarray(t.screenshot(d).crop(F1_SLOT).convert("L")).astype(int)
            if slot.std() > 25:
                continue
            log(self.state, f"window {i + 1}: F1 is empty: putting Lightning Bolt back")
            with mbquest.Pause(self.state, d):
                mbquest.close_all(self.state, i, d)
                mbquest.alt(d, "s")
                time.sleep(1.2)
                mbquest.drag(d, 430, 238, 312, 737, steps=30)
                time.sleep(0.8)
                mbquest.alt(d, "s")
                time.sleep(0.4)

    def loot(self):
        self.each(lambda i, d: t.key(d, "keydown", "a", "sleep", "0.1", "keyup", "a"))

    # --- safety ----------------------------------------------------------------------------
    def check_party(self):
        im = t.screenshot(self.displays[0])
        hp, sp = t.party_hp(im), t.party_sp(im)
        # a death: only the dead one revives, rests and walks back; the others keep hunting
        # (the whole party running to the haven and back for one death wasted minutes)
        gone = t.excluded(self.state) - {TANK}
        full = any(v == 0.0 for v in hp) or time.monotonic() - getattr(self, "_dead_scan", 0) > 10
        if full:
            self._dead_scan = time.monotonic()
        for i, d in (enumerate(self.displays) if full else ()):
            if i == TANK or i in self.recovering or i in gone:
                continue                        # (the tank loop revives the tank itself)
            # dead = HP 0 or the "Revive in Town?" dialog (once the frame still showed 76 HP)
            if (t.own_hp(d) or 0) > 0 and not t.find_image(t.screenshot(d).crop((200, 150, 700, 450)), "mbrevive.png"):
                continue
            ox, oy = FORMATION.get(i, (0, 0))
            here = (self.patrol[0][0] + ox, self.patrol[0][1] + oy)   # its own camp spot
            log(self.state, f"window {i + 1} died: it revives and comes back alone, the rest hunt on")
            self.recovering.add(i)

            def back(i=i, d=d, here=here):
                try:
                    t.recover(self.state, i, d, *here)
                except Exception as exc:
                    log(self.state, f"window {i + 1}: recovery problem: {exc}")
                finally:
                    self.recovering.discard(i)
                    self.prev_hp = None
            threading.Thread(target=back, daemon=True).start()
        lows = [v for v in hp + sp if v is not None]
        if lows and min(lows) < REST_BELOW:
            log(self.state, f"resting (lowest HP/SP {min(lows):.0%})")
            os.environ["REST_FULL"] = str(REST_UNTIL)
            self.resting = True
            started = time.monotonic()
            try:
                members = self.explorers() + ([(TANK, self.displays[TANK])] if TANK >= 0 else [])
                why = t.rest_until_full(self.state, members, "hunt rest", limit=240, with_sp=True,
                                        watch=lambda: "attacked while resting"
                                        if self.last_hurt > started + 1.5 else None)
                if why:
                    log(self.state, f"rest stopped: {why}")
            except Exception as exc:
                log(self.state, f"rest problem: {exc}")
            finally:
                self.resting = False

    def at_camp(self):
        pos = self.positions()
        cx, cy = self.patrol[0]
        for i in range(len(self.displays)):
            if i in self.recovering or (i != TANK and i in t.excluded(self.state)):
                continue
            p = pos.get(i)
            ox, oy = FORMATION.get(i, (0, 0))
            if p and max(abs(p[0] - cx), abs(p[1] - cy)) > 30:
                continue                        # travelling back from a revive: don't wait for it
            if not p or max(abs(p[0] - cx - ox), abs(p[1] - cy - oy)) > 4:
                if time.monotonic() - getattr(self, "_camp_log", 0) > 15:
                    self._camp_log = time.monotonic()
                    log(self.state, f"waiting for window {i + 1} at its camp spot ({p})")
                return False
        return True

    def top_up(self):
        im = t.screenshot(self.displays[0])
        hp = [v for v in t.party_hp(im) if v is not None and v > 0]
        if hp and min(hp) < FIGHT_HP:
            log(self.state, f"resting before the next fight (lowest HP {min(hp):.0%})")
            members = self.explorers() + ([(TANK, self.displays[TANK])] if TANK >= 0 else [])
            self.resting = True
            started = time.monotonic()
            os.environ["REST_FULL"] = "0.9"
            try:
                t.rest_until_full(self.state, members, "top-up rest", limit=180, with_sp=False,
                                  watch=lambda: "attacked" if self.last_hurt > started + 1.5 else None)
            except Exception as exc:
                log(self.state, f"rest problem: {exc}")
            finally:
                self.resting = False

    def regroup(self):
        pos = self.positions()
        here = pos.get(0)
        if not here:
            return
        ps = [pos[i] for i, _ in self.explorers() if pos.get(i)]
        if not ps:
            return
        mx = sorted(p[0] for p in ps)[len(ps) // 2]     # the group = the median position
        my = sorted(p[1] for p in ps)[len(ps) // 2]
        members = [i for i, _ in self.explorers()] + ([TANK] if TANK >= 0 else [])
        far = [i for i in members
               if pos.get(i) and max(abs(pos[i][0] - mx), abs(pos[i][1] - my)) > 10]
        if not far:
            return
        hx, hy = (mx, my) if len(far) < len(ps) / 2 else self.patrol[0]
        log(self.state, f"regrouping at {hx},{hy} ({len(far)} away)")
        try:
            ds = [self.displays[i] for i in far]
            list(self.pool.map(lambda d: t.trace(d, hx, hy), ds))
            t._arrive(self.state, ds, hx, hy, "regroup", limit=900, near=6)
        except Exception as exc:
            log(self.state, f"regroup problem: {exc}")

    def move_on(self):
        """CAMP: never walk the group anywhere - mobs wander in and respawn. Just look again."""
        time.sleep(0.5)
        return self.find()

    def _move_on(self):
        self.k = (self.k + 1) % len(self.patrol)
        x, y = self.patrol[self.k]
        log(self.state, f"no mob in sight: on to {x},{y}")
        ds = [d for _, d in self.explorers()] + ([self.displays[TANK]] if TANK >= 0 else [])
        self.walk_until = time.monotonic() + 25
        list(self.pool.map(lambda d: t.trace(d, x, y), ds))      # the tank walks with them
        end = time.monotonic() + 25
        while time.monotonic() < end and not self.alert:
            time.sleep(0.8)
            b, pt = self.find()
            if pt:
                return b, pt
            here = mbcoords.read(self.displays[0])
            if here and abs(here[0] - x) <= 4 and abs(here[1] - y) <= 4:
                break
        return None, None

    # --- main loop -------------------------------------------------------------------------
    def wait_ready(self, last_cast):
        while time.monotonic() - last_cast < COOLDOWN:
            if self.alert:
                return
            time.sleep(0.05)

    def run(self):
        t.write(self.pause, "1\n")
        t.write(os.path.join(self.state, "hunt.pid"), f"{os.getpid()}\n")
        log(self.state, f"start: {len(self.explorers())} Explorers, patrol {self.patrol}")
        for i, _ in enumerate(self.displays):    # recoveries of an earlier hunter died with it
            if i != TANK:
                t.set_excluded(self.state, i, False)
        for i, d in enumerate(self.displays):    # a crashed client (black screen): leave it alone
            import numpy as np
            if np.asarray(t.screenshot(d).convert("L")).max() < 10:
                gone = set(t.read(os.path.join(self.state, "gone")).split()) | {str(i)}
                t.write(os.path.join(self.state, "gone"), " ".join(sorted(gone)) + "\n")
                log(self.state, f"window {i + 1}: client gone (black screen) - left out")
        self.camera_home()
        self.check_f1()
        self.fmaps = {}                         # per window: its frames -> window index
        for i in range(len(self.displays)):
            try:
                self.fmaps[i] = t._party_map(self.displays, i)
            except Exception as exc:
                log(self.state, f"party frames of window {i + 1} unknown: {exc}")
        if TANK >= 0:                           # SIMPLE: only the tank reacts to hits
            threading.Thread(target=self.defend_loop, args=(TANK, self.displays[TANK]), daemon=True).start()
        threading.Thread(target=self.cohesion_loop, daemon=True).start()
        idle_since, last_report, last_cast = time.monotonic(), time.monotonic(), 0.0
        cur, nxt, started, gone = None, None, 0.0, 0
        try:
            while True:
              try:
                cur, nxt, started, gone, last_cast, idle_since, last_report = self.step(
                    cur, nxt, started, gone, last_cast, idle_since, last_report)
              except StopIteration:
                return
              except Exception as exc:          # never die mid-hunt: nobody would taunt or bolt
                log(self.state, f"loop problem (going on): {exc!r}")
                cur, nxt = None, None
                time.sleep(0.3)
        finally:
            if os.path.exists(self.pause):
                os.remove(self.pause)

    def step(self, cur, nxt, started, gone, last_cast, idle_since, last_report):
        """One pass of the hunt loop (the old while-body; 'return st()' = return the state)."""
        st = lambda: (cur, nxt, started, gone, last_cast, idle_since, last_report)
        if True:
            if True:
                if self.alert:                  # PRIORITY: the mob hitting someone, now
                    (ab, apt), self.alert = self.alert, None
                    cur = (ab, apt)
                    nxt, started, gone = None, time.monotonic(), 0
                    self.wait_ready(last_cast)
                    if self.cast(*cur):           # nobody bolted: no cooldown to wait for
                        last_cast = time.monotonic()
                    return st()
                stop = os.path.join(self.state, "hunt-stop")
                if cur is None and os.path.exists(stop) and time.monotonic() - self.last_hurt > 4 \
                        and not self.recovering:        # (a recovery thread would die with us)
                    os.remove(stop)             # asked to stop: only between fights, never mid-fight
                    log(self.state, "stopping (asked to, nobody under attack)")
                    raise StopIteration
                if cur is None and time.monotonic() - getattr(self, "last_align", 0) > 60:
                    self.last_align = time.monotonic()
                    self.fit_mapping()          # (camera turning disabled: matching misread)
                if cur is None:
                    self.check_party()          # deaths first: a dead member never "joins"
                    self.top_up()               # nobody starts a fight below FIGHT_HP

                if cur is None:
                    bar, pt = nxt if nxt else self.find()
                    nxt = None
                    if not pt:
                        if self.alert:
                            return st()
                        if time.monotonic() - idle_since > NO_MOB_S:
                            bar, pt = self.move_on()
                            idle_since = time.monotonic()
                        if not pt:
                            return st()
                    self.wait_ready(last_cast)
                    if self.alert:
                        return st()
                    if self.cast(bar, pt):           # nobody bolted: no cooldown to wait for
                        last_cast = time.monotonic()
                    cur, started, gone = (bar, pt), time.monotonic(), 0
                    idle_since = time.monotonic()
                    return st()
                time.sleep(max(0.0, 0.9 - (time.monotonic() - last_cast)))   # let the volley land
                b = self.bar_alive(cur[0])
                if b is None:                   # dead already? (one volley usually does it)
                    time.sleep(0.2)
                    b = self.bar_alive(cur[0])
                    if b is None:
                        self.kills += 1
                        self.loot()
                        log(self.state, f"kill {self.kills} ({time.monotonic() - started:.0f}s)")
                        cur = None
                        return st()
                if nxt is None:                 # line up the next one during the cooldown
                    nb = self.find(avoid=cur[0])
                    nxt = nb if nb[1] else None
                self.wait_ready(last_cast)
                if self.alert:
                    return st()
                b = self.bar_alive(cur[0])
                if b is None:
                    gone += 1
                    if gone >= 2:               # its bar is gone twice: dead
                        self.kills += 1
                        self.loot()
                        log(self.state, f"kill {self.kills} ({time.monotonic() - started:.0f}s)")
                        cur = None
                        return st()
                    time.sleep(0.25)
                    return st()
                gone = 0
                if time.monotonic() - started > TARGET_MAX_S:
                    w = self.world_of(b)
                    if w and max(abs(w[0] - SAFE_ZONE[0]), abs(w[1] - SAFE_ZONE[1])) <= 20:
                        self.ignored.append((w, time.monotonic() + 300))
                        log(self.state, f"target in the haven's safe zone: ignored for 5 min at {w}")
                    else:
                        log(self.state, "target took too long: next")
                    cur = None
                    return st()
                cur = (b, (cur[1][0] + b[0] - cur[0][0], cur[1][1] + b[1] - cur[0][1]))   # it moved
                if self.cast(*cur):           # nobody bolted: no cooldown to wait for
                    last_cast = time.monotonic()
                if time.monotonic() - last_report > 300:
                    log(self.state, f"{self.kills} kills so far")
                    last_report = time.monotonic()
        return st()


def main(argv):
    state, sx, sy, patrol, displays = argv[1], int(argv[2]), int(argv[3]), argv[4], argv[5:]
    os.environ.setdefault("MB_STATE", state)
    os.environ["MB_F1_AT_CLICK"] = "1"
    t.OCR_TIMEOUT = 6
    pts = [tuple(map(int, p.split(","))) for p in patrol.split()]
    Hunter(state, (sx, sy), pts, displays).run()


if __name__ == "__main__":
    main(sys.argv)
