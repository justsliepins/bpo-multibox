#!/usr/bin/env python3
"""The simple hunter. Everyone stands on ONE tile (CAMP), so a spot on window 1's screen is the
same spot on every screen (cameras aligned). Loop:

  target : window 1, one screenshot: the nearest mob (by its "Lv" label + health bar). If anyone
           just lost HP, the mob nearest the party instead (the attacker).
  volley : the 4 Explorers stand up, F1 + click the target, sit down; the tank (window 5) taunts
           it (F1 + click) and keeps auto-attacking it (another click).
  repeat : when the bolt's cooldown is over: stand up, bolt the same target if it lives, else the
           next one. Loot: the clients' auto-pickup.
  death  : that window revives in town, walks straight back to CAMP and joins again; the others
           never stop.

usage: mbsimple.py CAMP_X CAMP_Y
"""
import faulthandler
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import mbcoords
import mbhunt
import mbtarget as t

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "multibox")
os.environ["MB_STATE"] = STATE
D = [":61", ":62", ":63", ":64", ":65"]
AMIS, TANK = [0, 1, 2, 3], 4
SIT = os.environ.get("SIMPLE_SIT", "0") == "1"   # sit between bolts (off: the toggles desynced)
SAFE = (1012, 2970)            # Andes Forest Haven's safe zone (mobs can't hit there)
COOLDOWN = 4.8                 # Lightning Bolt Lv9 (8400 - 400*lv ms) + margin
REACH = int(os.environ.get("SIMPLE_REACH", "420"))                    # px from the party: further mobs are not pulled
O = t.ORIGIN


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "simple: " + msg + "\n")


class Hunter:
    def __init__(self, camp):
        self.camp = camp
        self.pool = ThreadPoolExecutor(8)
        self.sitting = {i: False for i in AMIS}
        self.away = set()                       # windows reviving / walking back
        self.walking = set()
        self.prev_hp = None
        self.kills = 0
        self.last_taunt = 0.0
        self.hurt = False
        self.tank_sitting = False
        self.last_cam = time.monotonic()

    # --- sit / stand (Insert toggles; the server takes one press per ~0.5 s from the group) ----
    def set_sit(self, want):
        if not SIT:
            return
        for i in AMIS:
            if i in self.away or self.sitting[i] == want:
                continue
            t.key(D[i], "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
            self.sitting[i] = want
            time.sleep(0.45)

    # --- target ------------------------------------------------------------------------------
    def target(self):
        im = t.screenshot(D[0])
        hp = t.party_hp(im)
        hurt = self.prev_hp is not None and any(
            a is not None and b is not None and 0 < b < a - 0.004 for a, b in zip(self.prev_hp, hp))
        self.prev_hp = hp
        cx, cy = t.PLAYER_BAR_X, t.PLAYER_BAR_Y
        # Ctrl+B hides other players and the party stands on one tile: every health bar away
        # from our own spot is a mob (the "Lv" label template missed Killer Shrooms)
        bars = [b for b in t.hp_bars(im) if 22 ** 2 < (b[0] - cx) ** 2 + (b[1] - cy) ** 2 <= REACH ** 2]
        bars.sort(key=lambda b: (b[0] - cx) ** 2 + (b[1] - cy) ** 2)
        if bars and t.cursor_id(D[0]) in t.SKILL_FRAMES:
            mbhunt.fast_drop(D[0])
        self.hurt = hurt
        if hurt:                                # the attacker stands ON the party (its bar is
            vx, vy = O[0] + cx, O[1] + cy - 28  # under ours): sweep the attack cursor around us
            for dx, dy in mbhunt.RING:
                pt = mbhunt.probe_point(D[0], vx + dx, vy + dy)
                if pt:
                    log("someone is hit: the attacker on the party first")
                    return (pt[0] - O[0], pt[1] - O[1] + 28, "stack"), pt
        for b in bars[:4]:
            pt = mbhunt.probe_mob(D[0], O[0] + b[0], O[1] + b[1])
            if pt:
                return b, pt
        return None, None

    def alive(self, bar):
        if len(bar) > 2:                        # a mob on the party: alive while the cursor says so
            if t.cursor_id(D[0]) in t.SKILL_FRAMES:
                mbhunt.fast_drop(D[0])
            for dx, dy in ((0, 0), (0, -10), (10, 0), (-10, 0), (0, 10)):
                if mbhunt.probe_point(D[0], O[0] + bar[0] + dx, O[1] + bar[1] - 28 + dy):
                    return bar
            return None
        im = t.screenshot(D[0])
        near = [b for b in t.hp_bars(im) if (b[0] - bar[0]) ** 2 + (b[1] - bar[1]) ** 2 <= 150 ** 2
                and (b[0] - t.PLAYER_BAR_X) ** 2 + (b[1] - t.PLAYER_BAR_Y) ** 2 > 22 ** 2]
        near.sort(key=lambda b: (b[0] - bar[0]) ** 2 + (b[1] - bar[1]) ** 2)
        if t.cursor_id(D[0]) in t.SKILL_FRAMES:
            mbhunt.fast_drop(D[0])
        for b in near[:2]:                      # a mob, not a party member walking back
            pt = mbhunt.probe_mob(D[0], O[0] + b[0], O[1] + b[1])
            if pt:
                self.aim = pt
                return b
        return None

    # --- the volley ----------------------------------------------------------------------------
    def volley(self, pt):
        def ami(i):
            if i in self.away:
                return False
            d = D[i]
            if t.cursor_id(d) in t.SKILL_FRAMES:
                mbhunt.fast_drop(d)
            hit = mbhunt.probe_point(d, pt[0], pt[1])
            if not hit:                         # the same mob animates a little differently in
                bx, by = self.bar_abs           # each client: search its body above its bar
                hit = mbhunt.probe_mob(d, bx, by)
            if not hit:
                return False                    # no click without the attack cursor: it would
            mbhunt.bolt(d, *hit)                # be a walk order (that split the party)
            return True

        def tank():
            if TANK in self.away or (self.tank_sitting and not self.hurt):
                return                          # resting: only an attacker gets it up
            d = D[TANK]
            if self.tank_sitting:
                t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert", "sleep", "0.4")
                self.tank_sitting = False
            taunt = getattr(self, "hurt", False) and time.monotonic() - self.last_taunt > 5.1
            if taunt:                           # only for a mob hitting us: Taunt costs 10 SP
                self.last_taunt = time.monotonic()
                t.key(d, "keydown", "F1", "sleep", "0.04", "keyup", "F1", "sleep", "0.05",
                      "mousemove", str(int(pt[0])), str(int(pt[1])), "sleep", "0.04",
                      "mousedown", "1", "sleep", "0.06", "mouseup", "1", "sleep", "0.3")
            t.key(d, "mousemove", str(int(pt[0])), str(int(pt[1])), "sleep", "0.04",
                  "mousedown", "1", "sleep", "0.06", "mouseup", "1")   # auto-attack it

        self.pool.submit(tank)
        return sum(self.pool.map(ami, AMIS))

    def volley_at(self, bar, pt):
        self.bar_abs = (O[0] + bar[0], O[1] + bar[1])
        return self.volley(pt)

    def sp_rest(self):
        """SP: a bolt costs ~43. Anyone below 35% SP (or HP): everyone sits until 90% (the rest
        checks each character really regenerates), stopped at once when someone is hit."""
        self.check_dead()                       # a dead member is revived, not "rested"
        im = t.screenshot(D[0])
        hp, sp = t.party_hp(im), t.party_sp(im)
        if any(v == 0.0 for v in hp):
            return
        lows = [v for v in hp + sp if v is not None and v > 0]
        if not lows or min(lows) >= 0.4:
            return
        log(f"resting in the safe zone (lowest HP/SP {min(lows):.0%})")
        # rest INSIDE the haven's safe zone: resting at the camp with empty SP wiped the party
        members0 = [i for i in AMIS + [TANK] if i not in self.away]
        for i in members0:
            if i in AMIS and self.sitting[i]:
                t.key(D[i], "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                self.sitting[i] = False
        rest_in_place = max(abs(SAFE[0] - self.camp[0]), abs(SAFE[1] - self.camp[1])) > 40
        for i in ([] if rest_in_place else members0):   # a passive camp: rest right there
            t._trace(D[i], *SAFE)
        for _ in range(0 if rest_in_place else 20):
            time.sleep(1.5)
            ps = [mbcoords.read(D[i]) for i in members0]
            if all(p and abs(p[0] - SAFE[0]) <= 3 and abs(p[1] - SAFE[1]) <= 3 for p in ps):
                break
        start_hp = t.party_hp(t.screenshot(D[0]))
        members = [(i, D[i]) for i in AMIS + [TANK] if i not in self.away and (t.own_hp(D[i]) or 0) > 0]
        os.environ["REST_FULL"] = "0.95"

        def hit():
            now = t.party_hp(t.screenshot(D[0]))
            return "attacked" if any(a is not None and b is not None and 0 < b < a - 0.01
                                     for a, b in zip(start_hp, now)) else None
        try:
            t.rest_until_full(STATE, members, "simple rest", limit=150, with_sp=True, watch=hit)
        except Exception as exc:
            log(f"rest problem: {exc!r}")
        for i in AMIS:
            self.sitting[i] = False
        self.tank_sitting = False
        if not rest_in_place:
            for i in members0:                  # back to the camp together
                t._trace(D[i], *self.camp)
            time.sleep(6)

    def cameras(self):
        """Every view must match window 1's (they drift): terrain-matched, fixed beyond 4 deg."""
        import mbrot
        self.last_cam = time.monotonic()
        try:
            ref = mbrot.gray(D[0])
            for i in (1, 2, 3, 4):
                if i in self.away:
                    continue
                r = mbrot.rotation(D[i], ref)
                if r is not None and abs(r) > 4:
                    mbrot.align(D[i], log=lambda m: None)
                    log(f"window {i + 1}: camera was {r:+.0f} deg off: turned back")
                elif r is None:
                    log(f"window {i + 1}: camera unreadable (far off?)")
        except Exception as exc:
            log(f"camera check problem: {exc!r}")

    def tank_rest(self):
        """The tank sits while low and nobody is being hit (its taunt/attack stands it up)."""
        if TANK in self.away or self.hurt:
            self.tank_sitting = False
            return
        hp = t.own_hp(D[TANK]) or 0
        if hp < 0.5 and not self.tank_sitting:
            t.key(D[TANK], "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
            self.tank_sitting = True
        elif hp >= 0.9 and self.tank_sitting:
            t.key(D[TANK], "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
            self.tank_sitting = False

    # --- deaths --------------------------------------------------------------------------------
    def check_dead(self):
        for i, d in enumerate(D):
            if i in self.away or (t.own_hp(d) or 0) > 0:
                continue
            self.away.add(i)
            log(f"window {i + 1} died: revive, straight back to {self.camp}")
            threading.Thread(target=self.recover, args=(i, d), daemon=True).start()

    def recover(self, i, d):
        try:
            t.close_popups(STATE, i, d)
            t.revive(STATE, i, d)
            os.environ["RECOVER_REST"] = "0.95"   # full HP and SP: at 60%/0 SP it died again at once
            t.rest_one(STATE, i, d)
            if i in AMIS:
                self.sitting[i] = False
            for _ in range(120):                # straight back, hop by hop (safe path)
                p = mbcoords.read(d)
                if p and max(abs(p[0] - self.camp[0]), abs(p[1] - self.camp[1])) <= 1:
                    break
                t.trace(d, *self.camp)
                time.sleep(4)
            log(f"window {i + 1} is back")
        except Exception as exc:
            log(f"window {i + 1}: recovery problem: {exc!r}")
        finally:
            self.away.discard(i)

    def clear_popups(self):
        """The 'Please input proper coordinates!' popup (a mistyped walk command) blocks every
        click in its window - it kept two Amis out of the fight for an hour. Confirm + close."""
        for i, d in enumerate(D):
            im = t.screenshot(d)
            p = t.find_image(im, "mbbadcoords.png")
            if p:
                t.move(d, O[0] + p[0], O[1] + p[1] + 40)
                time.sleep(0.2)
                t.key(d, "mousedown", "1", "sleep", "0.08", "mouseup", "1")
                time.sleep(0.5)
                log(f"window {i + 1}: cleared a 'proper coordinates' popup")
            if p or t.trace_open(im):
                t._close_trace(d)

    def regroup(self):
        """Anyone >2 tiles off the camp tile walks back - in the background, never blocking the
        fight (walking 5 windows one after another froze the loop for minutes)."""
        for i, d in enumerate(D):
            if i in self.away or i in self.walking:
                continue
            p = mbcoords.read(d)
            if p and max(abs(p[0] - self.camp[0]), abs(p[1] - self.camp[1])) > 2:
                self.walking.add(i)
                self.pool.submit(self._walk_back, i, d)

    def _walk_back(self, i, d):
        try:
            if i in AMIS and self.sitting[i]:
                t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                self.sitting[i] = False
            p0 = mbcoords.read(d)
            t.trace(d, *self.camp)
            if t.trace_open(t.screenshot(d)):
                t._close_trace(d)               # never leave a half-typed dialog open
            time.sleep(2.5)
            if mbcoords.read(d) == p0:          # didn't move: seated (state lost) - stand, again
                t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
                time.sleep(0.6)
                t.trace(d, *self.camp)
                if i in AMIS:
                    self.sitting[i] = False
        except Exception as exc:
            log(f"window {i + 1}: walk back problem: {exc!r}")
        finally:
            self.walking.discard(i)

    # --- NEAREST mode: every window fights the mob nearest to itself, on its own screen ------
    def strike_nearest(self, i):
        d = D[i]
        if i in self.away:
            return False
        if t.cursor_id(d) in t.SKILL_FRAMES:
            mbhunt.fast_drop(d)
        im = t.screenshot(d)
        cx, cy = t.PLAYER_BAR_X, t.PLAYER_BAR_Y
        bars = sorted((b for b in t.hp_bars(im) if 22 ** 2 < (b[0] - cx) ** 2 + (b[1] - cy) ** 2 <= REACH ** 2),
                      key=lambda b: (b[0] - cx) ** 2 + (b[1] - cy) ** 2)
        hit = None
        if self.hurt:                           # a mob on us first: sweep right around us
            for dx, dy in mbhunt.RING[:16]:
                hit = mbhunt.probe_point(d, O[0] + cx + dx, O[1] + cy - 28 + dy)
                if hit:
                    break
        for b in ([] if hit else bars[:3]):
            hit = mbhunt.probe_mob(d, O[0] + b[0], O[1] + b[1])
            if hit:
                break
        if not hit:
            return False
        x, y = hit
        if i == TANK:
            keys = []
            if self.hurt and time.monotonic() - self.last_taunt > 5.1:
                keys = ["keydown", "F1", "sleep", "0.04", "keyup", "F1", "sleep", "0.05"]
                self.last_taunt = time.monotonic()
            t.key(d, *keys, "mousemove", str(int(x)), str(int(y)), "sleep", "0.04",
                  "mousedown", "1", "sleep", "0.06", "mouseup", "1")
        else:
            mbhunt.bolt(d, x, y)
        return True

    def run_nearest(self):
        t.write(os.path.join(STATE, "simple.pid"), f"{os.getpid()}\n")
        self.pause = os.path.join(STATE, f"pause.{os.getpid()}")
        t.write(self.pause, "1\n")
        log(f"start (nearest mode) at {self.camp}")
        n, last, volleys = 0, 0.0, 0
        while True:
            n += 1
            if not os.path.exists(self.pause):
                t.write(self.pause, "1\n")
            self.check_dead()
            if n % 3 == 1:
                self.clear_popups()
                self.regroup()
            self.sp_rest()
            im = t.screenshot(D[0])
            hp = t.party_hp(im)
            self.hurt = self.prev_hp is not None and any(
                a is not None and b is not None and 0 < b < a - 0.004 for a, b in zip(self.prev_hp, hp))
            self.prev_hp = hp
            wait = last + COOLDOWN - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            hits = list(self.pool.map(self.strike_nearest, AMIS + [TANK]))
            last = time.monotonic()
            volleys += 1
            if volleys % 10 == 0:
                log(f"{volleys} volleys; last one: {sum(hits[:4])}/4 Amis + tank {hits[4]}")

    # --- main loop -----------------------------------------------------------------------------
    def run(self):
        t.write(os.path.join(STATE, "simple.pid"), f"{os.getpid()}\n")
        # the broadcaster must NOT copy our input on window 1 into the others (it did: cameras
        # spun, characters walked off, half-typed dialogs appeared in other windows)
        self.pause = os.path.join(STATE, f"pause.{os.getpid()}")
        t.write(self.pause, "1\n")
        log(f"start at {self.camp}")
        last_bolt, n = 0.0, 0
        cur = None
        while True:
            n += 1
            if not os.path.exists(self.pause):  # (something removed it: keep the broadcaster off us)
                t.write(self.pause, "1\n")
            if n % 5 == 1:
                self.check_dead()
            if n % 3 == 1:
                self.clear_popups()
                self.regroup()
            if cur is None:
                self.sp_rest()
            if cur is None and time.monotonic() - self.last_cam > 180:
                self.cameras()
            if cur is None:
                bar, pt = self.target()
                if not pt:
                    time.sleep(0.4)
                    continue
                cur, started = (bar, pt), time.monotonic()
            wait = last_bolt + COOLDOWN - 1.9 - time.monotonic()   # standing 4 takes ~1.8 s
            if wait > 0:
                time.sleep(wait)
            self.set_sit(False)
            wait = last_bolt + COOLDOWN - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            b = self.alive(cur[0])
            if b is None:
                self.kills += 1
                log(f"kill {self.kills} ({time.monotonic() - started:.0f}s)")
                cur = None
                continue
            if len(b) == 2:
                cur = (b, self.aim)             # the confirmed spot on its body
            pt = cur[1]
            n_hit = self.volley_at(cur[0], pt)
            last_bolt = time.monotonic()
            if os.environ.get("SIMPLE_DEBUG", "1") == "1":
                log(f"volley at {pt[0] - O[0]:.0f},{pt[1] - O[1]:.0f}: {n_hit}/4 Amis aimed, target {cur[0]}")
            self.set_sit(True)
            self.tank_rest()
            if time.monotonic() - started > 40:
                log("target took too long: next")
                cur = None


if __name__ == "__main__":
    faulthandler.register(signal.SIGUSR1, all_threads=True)   # kill -USR1: stacks -> simple.out
    h = Hunter((int(sys.argv[1]), int(sys.argv[2])))
    h.run_nearest() if os.environ.get("SIMPLE_MODE", "nearest") == "nearest" else h.run()
