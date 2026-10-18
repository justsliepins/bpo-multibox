#!/usr/bin/env python3
"""Snowmen the user's way: one at a time. The tank taunts (F1) the snowman first, then window 1
casts on it and the broadcast copies the cast to the other Voyagers (tank excluded from broadcast).
The tank re-taunts every 6 s; it sits when nothing is being fought. Dead characters are revived,
rested and walked back; 3 deaths in 20 minutes -> exit 3 (the caller switches to snails)."""
import os
import subprocess
import sys
import threading
import time

os.environ["MB_SNOW"] = "1"
import mbbears as b
import mbtarget as t

STATE = b.STATE
LEAD, TANK, AMIS = 0, 4, (0, 1, 2, 3)
FEET = b.FEET
SPOT = (3125, 600)
CAM = b.CAM
OUT = [(2665, 630), (2669, 614), (2687, 609), (2711, 606), (2735, 606), (2759, 602), (2771, 602), (2796, 599),
       (2821, 598), (2871, 596), (2921, 598), (2946, 603), (2971, 610), (3021, 616), (3071, 618), (3100, 630),
       (3109, 615), (3125, 600)]

away = set()                 # windows out of the fight (dead / travelling): excluded from broadcast
deaths = []
START = time.time()


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "snow: " + msg + "\n")


def press(i, k):
    t.key(b.D[i], "keydown", k, "sleep", "0.15", "keyup", k)


def write_excluded():
    with open(os.path.join(STATE, "excluded"), "w") as f:
        f.write("".join(f"{i}\n" for i in sorted(away | {TANK})))


def broadcast(want):
    if open(os.path.join(STATE, "broadcast")).read().strip() != want:
        subprocess.run(["./multibox", "toggle"], cwd=os.path.dirname(os.path.abspath(__file__)))
        time.sleep(4)


def here(i):
    p = b.pos(i)
    return bool(p) and max(abs(p[0] - SPOT[0]), abs(p[1] - SPOT[1])) <= 4


def walk_out(i):
    """Continuous walking (next waypoint as soon as one is within 6 tiles; a refused tile is skipped)."""
    if b.hp(i) < 0.9:
        os.environ["RECOVER_REST"] = "0.95"
        t.rest_one(STATE, i, b.D[i])
        b.sitting[i] = False
    p = b.pos(i)
    k = min(range(len(OUT)), key=lambda j: max(abs(OUT[j][0] - p[0]), abs(OUT[j][1] - p[1])))
    b.sit(i, False)
    b.tr(b.D[i], *OUT[k])
    last, still, fails, t0 = p, 0, 0, time.time()
    while k < len(OUT) and time.time() - t0 < 600:
        time.sleep(1)
        if b.dead(i):
            return False
        p = b.pos(i)
        if b.near(p, OUT[k], 6 if k < len(OUT) - 1 else 3):
            k += 1
            if k < len(OUT):
                b.tr(b.D[i], *OUT[k])
                still = fails = 0
            continue
        still = still + 1 if p == last else 0
        last = p
        if still >= 3:
            fails += 1
            if fails >= 2 and k < len(OUT) - 1:
                k += 1
                fails = 0
            b.tr(b.D[i], *OUT[k])
            still = 0
    return here(i)


recovering = set()
ready_at_haven = set()


def recover(i):
    """Revive at Icespire Haven, rest, wait (max 3 min) for a companion, walk back together."""
    try:
        if time.time() - START > 30:          # characters already dead at the start don't count
            deaths.append(time.time())
        log(f"window {i + 1} died: revive, rest, walk back")
        if i == LEAD:
            broadcast("off")
        t.close_popups(STATE, i, b.D[i])
        t.revive(STATE, i, b.D[i])
        b.sitting[i] = False
        os.environ["RECOVER_REST"] = "0.95"
        t.rest_one(STATE, i, b.D[i])
        b.sitting[i] = False
        ready_at_haven.add(i)
        t0 = time.time()
        # leave together with anyone else still reviving/resting (max 3 min)
        while time.time() - t0 < 180 and any(j not in ready_at_haven for j in recovering if j != i):
            time.sleep(3)
        time.sleep(2)
        ok = walk_out(i)
        log(f"window {i + 1} {'back at the spot' if ok else 'did not make it back'}")
    except Exception as exc:
        log(f"window {i + 1}: recover failed: {exc!r}")
    finally:
        ready_at_haven.discard(i)
        recovering.discard(i)


def tank_point(x, y):
    """The leader's screen point (x, y) in the tank's screen (they may stand a tile or two apart)."""
    a, c = b.pos(LEAD), b.pos(TANK)
    if not a or not c:
        return x, y
    dx, dy = c[0] - a[0], c[1] - a[1]
    return (x - (dx * CAM[0][0] + dy * CAM[1][0]), y - (dx * CAM[0][1] + dy * CAM[1][1]))


def main(hours):
    until = time.time() + hours * 3600
    last_conch = last_taunt = last_fight = last_home = 0.0
    tank_sitting = False
    locked = None
    write_excluded()
    broadcast("on")
    log("start: tank taunts (F1) first, then broadcast volleys from window 1")
    while time.time() < until:
        try:
            # deaths
            for i in AMIS + (TANK,):
                if i not in recovering and b.dead(i):
                    recovering.add(i)
                    away.add(i)
                    write_excluded()
                    threading.Thread(target=recover, args=(i,), daemon=True).start()
            recent = [d for d in deaths if time.time() - d < 1200]
            if len(recent) >= 3:
                log("3 deaths in 20 minutes: giving up on snowmen")
                broadcast("off")
                return 3
            # windows back at the spot rejoin the broadcast
            for i in list(away):
                if i not in recovering and here(i):
                    away.discard(i)
                    write_excluded()
                    log(f"window {i + 1} rejoins")
            if LEAD in away or TANK in away:
                time.sleep(3)
                continue
            far = [i for i in AMIS + (TANK,) if i not in recovering and not b.near(b.pos(i), SPOT, 30)]
            if far:                           # alive but far (revived by hand): walk out, in the background
                if LEAD in far:
                    broadcast("off")
                log(f"walking {[i + 1 for i in far]} out")
                def go(i):
                    try:
                        walk_out(i)
                    finally:
                        recovering.discard(i)
                for i in far:
                    recovering.add(i)
                    away.add(i)
                write_excluded()
                for i in far:
                    threading.Thread(target=go, args=(i,), daemon=True).start()
                    time.sleep(0.5)
                continue
            broadcast("on")
            # stay on the tank's tile
            if not here(LEAD) and time.time() - last_home > 20 and time.time() - last_fight > 8:
                b.tr(b.D[LEAD], *SPOT)
                last_home = time.time()
                time.sleep(3)
                continue
            if not here(TANK) and time.time() - last_home > 20 and time.time() - last_fight > 8:
                b.tr(b.D[TANK], *SPOT)
                last_home = time.time()
            im = t.screenshot(b.D[LEAD])
            pk = t.pink_mobs(im)
            close = [q for q in pk if (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= 150 ** 2]
            fighters = [i for i in AMIS if i not in away]
            hps = {i: b.hp(i) for i in fighters}
            if min(hps.values()) < 0.45 and not close:
                log(f"HP low {[round(v, 2) for v in hps.values()]}: everyone sits")
                press(LEAD, "Insert")
                t0 = time.time()
                while time.time() - t0 < 150 and min(b.hp(i) for i in fighters) < 0.85:
                    if any(b.dead(i) for i in fighters):
                        break
                    time.sleep(3)
                press(LEAD, "Insert")
                continue
            if (t.party_sp(im)[0] or 0) < 0.2 and not close:
                log("SP low: everyone sits")
                press(LEAD, "Insert")
                t0 = time.time()
                while time.time() - t0 < 150 and (t.party_sp(t.screenshot(b.D[LEAD]))[0] or 0) < 0.85:
                    time.sleep(3)
                press(LEAD, "Insert")
                continue
            tg = [q for q in pk if (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= 340 ** 2]
            if not tg:
                if not tank_sitting and time.time() - last_fight > 8:
                    press(TANK, "Insert")
                    tank_sitting = True
                time.sleep(2)
                continue
            if locked and time.time() - last_fight < 6:
                near_lock = [q for q in tg if (q[0] - locked[0]) ** 2 + (q[1] - locked[1]) ** 2 <= 90 ** 2]
            else:
                near_lock = []
            if near_lock:                     # the same snowman until it dies (no second one woken up)
                x, y = min(near_lock, key=lambda q: (q[0] - locked[0]) ** 2 + (q[1] - locked[1]) ** 2)
            else:
                x, y = min(tg, key=lambda q: (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2)
                last_taunt = 0.0              # a new target: the tank taunts it first
            locked = (x, y)
            last_fight = time.time()
            # the tank first: taunt the same snowman (on its own screen, nearest pink to the point)
            if time.time() - last_taunt > 6:
                px, py = tank_point(x, y)
                tp = [q for q in t.pink_mobs(t.screenshot(b.D[TANK]))
                      if (q[0] - px) ** 2 + (q[1] - py) ** 2 <= 160 ** 2]
                if tp:
                    qx, qy = min(tp, key=lambda q: (q[0] - px) ** 2 + (q[1] - py) ** 2)
                    if tank_sitting:
                        press(TANK, "Insert")
                        tank_sitting = False
                        time.sleep(0.6)
                    b.cast(TANK, "F1", qx, qy)
                    last_taunt = time.time()
                    time.sleep(1.0)
            k = "F2" if time.time() - last_conch > 4.5 else "F1"
            if k == "F2":
                last_conch = time.time()
            b.cast(LEAD, k, x, y)             # broadcast: every Voyager in the fight casts with it
            time.sleep(1.4)
        except Exception as exc:
            log(f"loop: {exc!r}")
            time.sleep(3)
    log("finished")
    return 0


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 9))
