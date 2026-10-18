#!/usr/bin/env python3
"""Tank client: one window (the Swordsman, Taunt on F1) out of broadcast, on its own loop while
the others run KILL.

Every tick, in this order:
  1. dead: revive, rest, walk back to the party.
  2. an Explorer (any other window) loses HP: stand up, F1 (Taunt) + click the mob next to it.
  3. loot (yellow parcels on the ground) within LOOT_PX: stand up, click the nearest parcel (walks
     there and picks it up), press A. Walking past mobs pulls them to the Explorers: intended.
  4. away from window 1: walk back to it (auto-trace to its coordinates).
  5. next to the party and idle for a while: sit (regen).
The window is kept in STATE/excluded so mbcast doesn't mirror window 1 into it.

usage: mbtank.py STATE TANK_WINDOW DISPLAY...      (TANK_WINDOW is 1-based)
"""
import os
import sys
import time

import cv2
import numpy as np

import mbcoords
import mbtarget as t

LOOT_PX = int(os.environ.get("LOOT_PX", "380"))
HOME_TILES = 3                 # stays right with the leader (it must taunt what hits them)
SIT_AFTER = 4.0                 # idle seconds before sitting down
TAUNT_EVERY = 5.1                # Taunt cooldown 5 s (SkillCooldown_CHF), 10 SP
# parcels: saturated yellow/gold (BPO drop bags); HUD and the minimap are masked out
LOOT_HSV = ((18, 120, 150), (34, 255, 255))
LOOT_AREA = (25, 900)
HUD_MASK = [(0, 0, 210, 400), (1210, 0, 1366, 200), (1320, 180, 1366, 768), (0, 570, 300, 768),
            (285, 670, 730, 768)]


def log(state, msg):
    with open(os.path.join(state, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "tank: " + msg + "\n")


def parcels(im, hud=True):
    """Ground loot: every drop shows its name as white text on a dark half-transparent box
    (CSceneItem::RenderUI, 0x90000000); mob and player names have no box. Returns the label
    centres (clicking the name picks the item up), nearest to the tank first."""
    a = np.asarray(im.convert("RGB")).astype(int)
    v = a.max(2)
    white = ((a.min(2) > 195).astype(np.uint8)) * 255
    if hud:
        for x0, y0, x1, y1 in HUD_MASK:
            white[y0:y1, x0:x1] = 0
    merged = cv2.dilate(white, np.ones((3, 9), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    H, W = v.shape
    out = []
    for i in range(1, n):
        x, y, w, h, area = map(int, st[i])
        if not (6 <= h <= 18 and 18 <= w <= 220):
            continue
        x0, x1 = max(0, x - 1), min(W, x + w + 1)
        inside = v[max(0, y - 1):min(H, y + h + 1), x0:x1]
        inside = inside[inside < 180]                     # the box, not the letters
        above = v[max(0, y - 7):max(0, y - 3), x0:x1]
        below = v[min(H, y + h + 3):min(H, y + h + 7), x0:x1]
        if inside.size < 20 or above.size + below.size < 20:
            continue
        outside = np.concatenate([above.ravel(), below.ravel()])
        if inside.mean() < 75 and np.median(outside) > 70 and inside.mean() < 0.7 * np.median(outside):
            cx, cy = x + w // 2, y + h // 2
            dist2 = (cx - t.PLAYER_BAR_X) ** 2 + (cy - t.PLAYER_BAR_Y - 30) ** 2
            # within 70 px: our own stacked name tags look the same (and A picks up there anyway)
            if not hud or 70 ** 2 < dist2 <= LOOT_PX ** 2:
                out.append((cx, cy))
    return sorted(out, key=lambda p: (p[0] - t.PLAYER_BAR_X) ** 2 + (p[1] - t.PLAYER_BAR_Y) ** 2)


class Tank:
    def __init__(self, state, idx, displays):
        self.state, self.i, self.displays = state, idx, displays
        self.d = displays[idx]
        self.sitting = False
        self.idle_since = time.monotonic()
        self.hp = {}
        self.last_taunt = 0.0

    def key(self, *k):
        t.key(self.d, *k)

    def stand(self):
        if self.sitting:
            self.key("keydown", "Insert", "sleep", "0.1", "keyup", "Insert")
            self.sitting = False
            time.sleep(0.6)

    def sit(self):
        if not self.sitting:
            self.key("keydown", "Insert", "sleep", "0.1", "keyup", "Insert")
            self.sitting = True
            time.sleep(0.6)

    def leader_xy(self):
        for i, d in enumerate(self.displays):
            if i != self.i and i not in t.excluded(self.state):
                return mbcoords.read(d)
        return None

    def threats(self, im):
        """Mobs on the tank's screen that are on an Explorer. Explorer positions come from map
        coordinates (scenery offsets are wrong for the tank, which runs out of broadcast); bars
        right on an Explorer are the Explorer's own. The Explorer losing HP (party frames on the
        tank's own screen, mapped frame -> window) is guarded first and from further away."""
        import mbquest
        if not hasattr(self, "fmap"):
            self.fmap = t._party_map(self.displays, self.i)      # frame -> window (tank's screen)
        me = mbcoords.read(self.d)
        if not me:
            return [], False
        ex = t.excluded(self.state)
        pts = {}
        for i, d in enumerate(self.displays):
            if i != self.i and (i not in ex or i == self.i):
                p = mbcoords.read(d)
                if p and max(abs(p[0] - me[0]), abs(p[1] - me[1])) <= 25:
                    pts[i] = mbquest.predicted(me, p)
        hp = t.party_hp(im)
        prev, self.hp = self.hp, dict(enumerate(hp))
        hurt_frames = [k for k, v in enumerate(hp) if k and v is not None
                       and (v < 0.6 or (prev.get(k) is not None and v < prev[k] - 0.01))]
        hurt = {self.fmap[k] for k in hurt_frames if k in self.fmap}
        if any(k not in self.fmap for k in hurt_frames):
            hurt = set(pts)                     # a frame we couldn't map: guard everyone
        own = list(pts.values()) + [(t.PLAYER_BAR_X, t.PLAYER_BAR_Y)]
        bars = [b for b in t.green_bar_candidates(im)
                if all((b[0] - x) ** 2 + (b[1] - y) ** 2 > 10 ** 2 for x, y in own)]   # 22 px hid a Panda right beside LuckyThird
        out = []
        for i, (x, y) in pts.items():
            if i not in hurt:
                continue                        # never start a fight: only pull what is hurting us
            reach = 170
            for b in bars:
                d2 = (b[0] - x) ** 2 + (b[1] - y) ** 2
                if d2 <= reach ** 2:
                    out.append((0 if i in hurt else 1, d2, b))
        return [b for _, _, b in sorted(out)], bool(hurt)

    def taunt(self):
        if time.monotonic() - self.last_taunt < TAUNT_EVERY:
            return False
        im = t.screenshot(self.d)
        mobs, hurt = self.threats(im)
        if not mobs:
            if hurt and time.monotonic() - getattr(self, "warned", 0) > 10:
                self.warned = time.monotonic()
                log(self.state, "an Explorer is losing HP but no attacker found near it")
                im.save(os.path.join(self.state, "tank-miss.png"))
            return False
        self.stand()
        tried = 0
        for b in mobs[:4]:
            tried += 1
            pt = t.hover_point(self.d, b[0], b[1], None, im=im, wide=True, patient=True)
            if pt:
                self.key("keydown", "F1", "sleep", "0.08", "keyup", "F1")
                time.sleep(0.12)
                t.click_at(self.d, *pt)                 # Taunt
                time.sleep(0.4)
                t.click_at(self.d, *pt)                 # then hit it (normal attack)
                self.last_taunt = time.monotonic()
                self.publish(b)
                log(self.state, f"taunted the mob at {round(b[0])},{round(b[1])}"
                                f"{' (an Explorer was losing HP)' if hurt else ''}")
                return True
        if hurt:
            log(self.state, f"an Explorer is losing HP: {tried} mob bar(s) near it, none clickable")
        return False

    def publish(self, b):
        """The taunted mob, in window 1's screen coordinates, for KILL to focus all bolts on."""
        offs = t.read_offsets(self.state, len(self.displays))
        x = b[0] - (offs[self.i][0] - offs[0][0])
        y = b[1] - (offs[self.i][1] - offs[0][1])
        t.write(os.path.join(self.state, "tank-target"), f"{time.time():.1f} {x:.0f} {y:.0f}\n")

    def party_busy(self, im=None):
        """A fight near the party: no loot runs now (the tank must stay to taunt)."""
        im = im or t.screenshot(self.d)
        mobs, hurt = self.threats(im)
        hp = [v for v in t.party_hp(im)[1:] if v is not None]
        return hurt or bool(mobs) or bool(hp and min(hp) < 0.8)

    def loot(self):
        im = t.screenshot(self.d)
        found = parcels(im)
        if not found or self.party_busy(im):
            return False
        self.stand()
        x, y = found[0]
        log(self.state, f"loot at {x},{y}: fetching it")
        t.click_at(self.d, t.ORIGIN[0] + x, t.ORIGIN[1] + y)     # clicking the name picks it up
        start = mbcoords.read(self.d)
        deadline = time.monotonic() + 8
        last = start
        while time.monotonic() < deadline:          # walk there (stop when we stop moving)
            time.sleep(0.6)
            if self.party_busy():                   # a fight started: back to taunting
                return True
            here = mbcoords.read(self.d)
            if here == last and here != start:
                break
            last = here
        for _ in range(2):
            self.key("keydown", "a", "sleep", "0.1", "keyup", "a")
            time.sleep(0.5)
        return True

    def go_home(self):
        lead = self.leader_xy()
        here = mbcoords.read(self.d)
        if not lead or not here:
            return False
        if max(abs(lead[0] - here[0]), abs(lead[1] - here[1])) <= HOME_TILES:
            self.walk_from = None
            return False
        self.stand()
        # trace again only once it stopped (each trace of a long way is one safe-path hop)
        if here == getattr(self, "walk_from", None) or getattr(self, "walk_from", None) is None:
            t.trace(self.d, lead[0], lead[1])
            time.sleep(1.5)
        self.walk_from = mbcoords.read(self.d)
        return True

    def dead(self):
        if t.own_hp(self.d) != 0.0:
            return False
        log(self.state, "the tank died: reviving, resting, coming back")
        self.sitting = False
        lead = self.leader_xy() or (0, 0)
        try:
            t.recover(self.state, self.i, self.d, *lead)
        except Exception as exc:
            log(self.state, f"recovery problem: {exc}")
        t.set_excluded(self.state, self.i, True)     # recover() puts it back into broadcast
        return True

    def tick(self):
        if os.path.exists(os.path.join(self.state, "tank-hold")):
            return                              # the supervisor is using this window
        if self.i not in t.excluded(self.state):
            t.set_excluded(self.state, self.i, True)
        here = mbcoords.read(self.d)
        in_town = here and 2140 <= here[0] <= 2300 and 2690 <= here[1] <= 2860   # Argent: no loot runs
        # taunting is done by the hunter in real time (it watches the party every 0.15 s)
        busy = os.path.exists(os.path.join(self.state, "tank-busy")) and \
            time.time() - os.path.getmtime(os.path.join(self.state, "tank-busy")) < 8
        if busy:
            return                              # it is fighting what it just taunted
        try:                                    # while the party hunts, no loot walks: the tank
            hpid = int(open(os.path.join(self.state, "hunt.pid")).read())     # must stay to taunt
            hunting = os.path.exists(f"/proc/{hpid}")
        except (OSError, ValueError):
            hunting = False
        if hunting:                             # the hunter keeps the tank at its camp spot
            self.dead()
            return
        if self.dead() or (not in_town and self.loot()) or self.go_home():
            self.idle_since = time.monotonic()
            return
        if time.monotonic() - self.idle_since > SIT_AFTER:
            self.sit()


def main(argv):
    state, idx, displays = argv[1], int(argv[2]) - 1, argv[3:]
    os.environ.setdefault("MB_STATE", state)
    t.write(os.path.join(state, "tank.pid"), f"{os.getpid()}\n")
    tank = Tank(state, idx, displays)
    log(state, f"window {idx + 1} is the tank: out of broadcast, looting and taunting on its own")
    try:
        while True:
            try:
                tank.tick()
            except Exception as exc:            # never die on one bad tick
                log(state, f"tick failed: {exc}")
                time.sleep(2)
            time.sleep(0.4)
    finally:
        t.set_excluded(state, idx, False)


if __name__ == "__main__":
    main(sys.argv)
