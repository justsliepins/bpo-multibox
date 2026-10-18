#!/usr/bin/env python3
"""Steel-shell Snails (Lv44, aggressive) at the east edge of the Icespire Haven safe zone (x<=2703).
Every character runs its own loop (broadcast off - a leader-driven group kept leaving single
characters behind outside the zone):
  Voyager: HP < 40% or SP < 15% -> walk into the safe zone (mobs can't hurt anyone there) and rest;
           else stand on FIGHT and Bolt (F1) / Conch (F2) the nearest mob.
  Tank:    taunt (F1) the nearest mob every 5 s, howl (F5) when nothing is near; < 45% HP -> rest.
  Dead:    revive (at this haven), rest, back to work.
Targets come from the mobs' "Lv" name labels; the body is ~65 px right of and ~22 px below it."""
import os
import subprocess
import sys
import threading
import time

os.environ["MB_SNOW"] = "1"
import mbbears as b
import mbtarget as t

STATE = b.STATE
TANK, AMIS = 4, (0, 1, 2, 3)
FEET = b.FEET
REST = (2699, 645)          # inside the safe zone (flag 3 up to x=2703)
FIGHT = (2707, 645)         # 2712 pulled too many: 55% -> dead in 11 s (2026-10-17 23:33); zone x<=2703
BODY = (65, 22)
RADIUS = 260


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "snail: " + msg + "\n")


def press(i, k):
    t.key(b.D[i], "keydown", k, "sleep", "0.15", "keyup", k)


def tile_x(i, q):
    """Map x of a screen point (default camera): inverse of the CAM tile->pixel matrix."""
    p = b.pos(i)
    if not p:
        return None
    (a, c), (bb, d) = b.CAM                   # sx = a*dx + bb*dy ; sy = c*dx + d*dy
    sx, sy = q[0] - FEET[0], q[1] - FEET[1]
    det = a * d - bb * c
    return p[0] + (d * sx - bb * sy) / det


def targets(i):
    im = t.screenshot(b.D[i])
    out = [(lx + BODY[0], ly + BODY[1]) for lx, ly in t.mob_labels(im)]
    out = [q for q in out if (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= RADIUS ** 2]
    out = [q for q in out if (tile_x(i, q) or 0) > 2705]   # never at mobs by/inside the safe zone
    return sorted(out, key=lambda q: (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2)


def idle_sp(i):
    """Nothing to fight and SP not full: sit on the spot (the user: restore SP in spare time);
    up again as soon as a mob is near or HP drops."""
    log(f"window {i + 1}: idle, SP {b.sp(i):.0%}: sitting for SP")
    b.sit(i, True)
    t0 = time.time()
    while time.time() - t0 < 120:
        time.sleep(3)
        if b.sp(i) >= 0.95 or b.hp(i) < 0.6 or b.dead(i) or targets(i):
            break
    b.sit(i, False)


def go(i, xy, r=2, limit=20):
    b.sit(i, False)
    b.tr(b.D[i], *xy)
    t0 = time.time()
    while time.time() - t0 < limit:
        time.sleep(1)
        if b.dead(i) or b.near(b.pos(i), xy, r):
            return
    b.tr(b.D[i], *xy)


def inside(i):
    p = b.pos(i)
    return bool(p) and p[0] <= 2702 and b.near(p, REST, 20)


def rest(i, why):
    log(f"window {i + 1}: {why}: into the safe zone to rest")
    for attempt in range(6):                  # never sit down outside the zone (died that way)
        go(i, REST)
        if inside(i) or b.dead(i):
            break
        t.key(b.D[i], "keydown", "Insert", "sleep", "0.15", "keyup", "Insert")   # maybe seated
        b.sitting[i] = False
        time.sleep(0.8)
    if not inside(i):
        log(f"window {i + 1}: could not get into the safe zone ({b.pos(i)})")
        return
    os.environ["RECOVER_REST"] = "0.95"
    t.rest_one(STATE, i, b.D[i])
    b.sitting[i] = False


def revive(i):
    log(f"window {i + 1} died: revive and rest")
    t.close_popups(STATE, i, b.D[i])
    t.revive(STATE, i, b.D[i])
    b.sitting[i] = False
    time.sleep(3)
    rest(i, "revived")


def safe_to_work(i):
    p = b.pos(i)
    return bool(p) and b.near(p, REST, 40)


def voyager(i, until):
    last_conch = last_fight = 0.0
    while time.time() < until:
        try:
            if b.dead(i):
                revive(i)
                continue
            if not safe_to_work(i):
                log(f"window {i + 1} at {b.pos(i)}: not at the haven - waiting")
                time.sleep(30)
                continue
            if b.hp(i) < 0.65:                # 40% and 55% died on the way in (2026-10-17)
                rest(i, "HP low")
                continue
            if b.sp(i) < 0.15:
                rest(i, "SP low")
                continue
            if b.dead(TANK) or b.hp(TANK) < 0.45 or not b.near(b.pos(TANK), FIGHT, 4):
                if not b.near(b.pos(i), REST, 3):  # no tank out there: wait inside
                    go(i, REST)
                time.sleep(3)
                continue
            if not b.near(b.pos(i), FIGHT, 2):
                go(i, FIGHT)
                continue
            tg = targets(i)
            if not tg:
                if time.time() - last_fight > 8 and b.sp(i) < 0.8:
                    idle_sp(i)
                time.sleep(2)
                continue
            last_fight = time.time()
            x, y = tg[0]
            k = "F2" if time.time() - last_conch > 4.5 else "F1"
            if k == "F2":
                last_conch = time.time()
            b.cast(i, k, x, y)
            time.sleep(1.3)
        except Exception as exc:
            log(f"window {i + 1}: {exc!r}")
            time.sleep(3)


def tank(until):
    last_taunt = last_howl = last_fight = 0.0
    while time.time() < until:
        try:
            i = TANK
            if b.dead(i):
                revive(i)
                continue
            if not safe_to_work(i):
                log(f"tank at {b.pos(i)}: not at the haven - waiting")
                time.sleep(30)
                continue
            if b.hp(i) < 0.45:
                rest(i, "HP low")
                continue
            if not b.near(b.pos(i), FIGHT, 2):
                go(i, FIGHT)
                continue
            tg = targets(i)
            if tg:
                last_fight = time.time()
            elif time.time() - last_fight > 8 and b.sp(i) < 0.8:
                idle_sp(i)
                continue
            if tg and time.time() - last_taunt > 10 and b.sp(i) >= 0.15:   # 152 SP: taunt sparingly
                b.cast(i, "F1", *tg[0])
                last_taunt = time.time()
            elif not tg and time.time() - last_howl > 20 and b.sp(i) >= 0.15:
                press(i, "F5")
                last_howl = time.time()
            time.sleep(1.5)
        except Exception as exc:
            log(f"tank: {exc!r}")
            time.sleep(3)


def main(hours):
    until = time.time() + hours * 3600
    if open(os.path.join(STATE, "broadcast")).read().strip() == "on":
        subprocess.run(["./multibox", "toggle"], cwd=os.path.dirname(os.path.abspath(__file__)))
    log("start: snails, every character on its own loop (broadcast off)")
    ths = [threading.Thread(target=tank, args=(until,), daemon=True)]
    ths += [threading.Thread(target=voyager, args=(i, until), daemon=True) for i in AMIS]
    for th in ths:
        th.start()
        time.sleep(1)
    while time.time() < until:
        time.sleep(600)
        log(f"status: HP {[round(float(b.hp(i)), 2) for i in range(5)]} "
            f"pos {[b.pos(i) for i in range(5)]}")
    log("finished")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 10)
