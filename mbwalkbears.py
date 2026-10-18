#!/usr/bin/env python3
"""Voyagers from Icespire Haven to the Great Polar Bear safe spot (3119,685) as one group, broadcast
OFF (each window traces itself), then optionally start the bear loop (mbbearloop.py).

  * group walking: all trace to the same waypoint (in parallel), the next one when all are there
  * FIGHT BACK: anyone losing HP or a mob within ~6 tiles -> all stop and Conch/Bolt the nearest
    mob from their own window until it's quiet for 6 s. Targets come from mob name labels only,
    never a Yeti (the Lv44 Cumbersome Yetis on the route are passive - hitting them wakes them), and
    a mob standing right on a character counts (the boar that killed a Voyager twice at 2823,607)
  * rest (sit, one per second) when someone is under 75% HP or 40% SP and nothing is near
  * before the last stretch (wanderer zone 3037-3165 x 637-706): regroup at STAGE, rest to full
  * the last stretch avoids the rocks at 3113-3118 x 706-710 where a group stalled and died twice
Route from the server's spawn/terrain data (darkbluemonster spawns, darkblue.atr/.blk), walked
successfully 2026-10-18.

usage: mbwalkbears.py [WINDOWS] [--loop]     WINDOWS e.g. 1,2,3,4 (default)
"""
import os
import subprocess
import sys
import threading
import time

os.environ["MB_SNOW"] = "1"
import mbbears as b
import mbtarget as t

ROUTE = [(2666, 628), (2672, 616), (2679, 610), (2703, 610), (2727, 609), (2751, 610), (2775, 610), (2799, 610),
         (2823, 607), (2847, 607), (2871, 605), (2895, 606), (2915, 615), (2918, 639), (2935, 647), (2959, 647),
         (2983, 644), (3006, 652), (3018, 664), (3029, 676), (3034, 700)]
STAGE = (3034, 700)
LAST = [(3050, 701), (3075, 700), (3095, 698), (3108, 694), (3112, 689), (3119, 685)]
AM = (0, 1, 2, 3)
FEET = b.FEET
D = b.D


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def tile_x(i, q):
    p = b.pos(i)
    if not p:
        return None
    (a, c), (bb, d) = b.CAM
    sx, sy = q[0] - FEET[0], q[1] - FEET[1]
    return p[0] + (d * sx - bb * sy) / (a * d - bb * c)


def targets(i):
    im = t.screenshot(D[i])
    pts = []
    for lx, ly in t.mob_labels(im):
        name = t.ocr(im.crop((lx, ly - 4, lx + 190, ly + 14)), 7, 2).lower()
        if "yeti" in name or "eti" in name[-6:]:
            continue
        pts.append((lx + 65, ly + 22))
    pts = [q for q in pts if (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= 220 ** 2]
    pts = [q for q in pts if (tile_x(i, q) or 9999) > 2708]      # never a mob by the haven's safe zone
    return sorted(pts, key=lambda q: (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2)


def fight():
    log("fight: mob near / someone hit")
    for i in AM:
        if b.sitting.get(i):
            b.sit(i, False)
    last_conch = {i: 0.0 for i in AM}
    quiet_since, t0 = None, time.time()
    while time.time() - t0 < 120:
        alive = [i for i in AM if not b.dead(i)]
        if not alive:
            return
        h0 = {i: b.hp(i) for i in alive}
        found = []

        def one(i):
            tg = targets(i)
            if not tg:
                return
            found.append(i)
            x, y = tg[0]
            k = "F2" if time.time() - last_conch[i] > 4.5 else "F1"
            if k == "F2":
                last_conch[i] = time.time()
            b.cast(i, k, x, y)
        ths = [threading.Thread(target=one, args=(i,)) for i in alive]
        [x.start() for x in ths]
        [x.join() for x in ths]
        time.sleep(1.0)
        hit = any(b.hp(i) < h0[i] - 0.01 for i in alive if not b.dead(i))
        if found or hit:
            quiet_since = None
        else:
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since > 6:
                log("fight: quiet again")
                return


def danger():
    alive = [i for i in AM if not b.dead(i)]
    h0 = [b.hp(i) for i in alive]
    time.sleep(1.0)
    if any(b.hp(i) < h - 0.01 for i, h in zip(alive, h0)):
        return True
    return any((q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= 120 ** 2 for i in alive for q in targets(i))


def rest_to(full):
    alive = [i for i in AM if not b.dead(i)]
    log(f"rest: HP {[round(float(b.hp(i)), 2) for i in alive]}")
    for i in alive:
        b.sit(i, True)
        time.sleep(1.0)
    t0 = time.time()
    while time.time() - t0 < 240:
        if danger():
            for i in alive:
                b.sit(i, False)
            fight()
            alive = [i for i in AM if not b.dead(i)]
            for i in alive:
                b.sit(i, True)
                time.sleep(1.0)
            continue
        if all(min(b.hp(i), b.sp(i)) >= full for i in alive):
            break
    for i in alive:
        b.sit(i, False)
        time.sleep(1.0)


def trace_all(ids, wp):
    ths = [threading.Thread(target=b.tr, args=(D[i], *wp)) for i in ids]
    [x.start() for x in ths]
    [x.join() for x in ths]


def group_to(wp, last=False):
    r = 1 if last else 4
    alive = [i for i in AM if not b.dead(i)]
    trace_all(alive, wp)
    t0, stuck = time.time(), {i: 0 for i in alive}
    last_pos = {i: b.pos(i) for i in alive}
    while time.time() - t0 < 60:
        if danger():
            fight()
            trace_all([i for i in AM if not b.dead(i)], wp)
            continue
        alive = [i for i in AM if not b.dead(i)]
        if not alive:
            return False
        if all(b.near(b.pos(i), wp, r) for i in alive):
            return True
        for i in alive:
            p = b.pos(i)
            if not b.near(p, wp, r):
                stuck[i] = stuck[i] + 1 if p == last_pos[i] else 0
                last_pos[i] = p
                if stuck[i] >= 2:
                    b.sit(i, False)
                    b.tr(D[i], *wp)
                    stuck[i] = 0
    log(f"{wp}: not everyone arrived {[b.pos(i) for i in AM]}")
    return all(b.near(b.pos(i), wp, r + 4) for i in alive)


def main():
    p = b.pos(AM[0])
    k = min(range(len(ROUTE)), key=lambda j: max(abs(ROUTE[j][0] - p[0]), abs(ROUTE[j][1] - p[1])))
    for wp in ROUTE[k:]:
        if any(b.dead(i) for i in AM):
            log(f"DEAD: {[i + 1 for i in AM if b.dead(i)]} - stopping")
            return False
        group_to(wp)
        hps = [b.hp(i) for i in AM]
        sps = [b.sp(i) for i in AM]
        log(f"at {wp}: HP {[round(float(h), 2) for h in hps]}")
        if min(hps) < 0.75 or min(sps) < 0.4:
            rest_to(0.9)
    log("stage: regroup and full HP before the last stretch")
    group_to(STAGE)
    rest_to(0.97)
    for wp in LAST:
        if any(b.dead(i) for i in AM):
            log(f"DEAD: {[i + 1 for i in AM if b.dead(i)]} - stopping")
            return False
        group_to(wp, last=(wp == LAST[-1]))
    ok = all(b.pos(i) == LAST[-1] for i in AM)
    log(("ARRIVED ALL " if ok else "ARRIVED (not exact) ") + str([b.pos(i) for i in AM]))
    return ok


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        AM = tuple(int(w) - 1 for w in args[0].split(","))
    if main() and "--loop" in sys.argv:
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.Popen(["python3", os.path.join(here, "mbbearloop.py"), "10"], start_new_session=True,
                         stdout=open(os.path.join(b.STATE, "bearloop.out"), "a"), stderr=subprocess.STDOUT)
        log("bear loop started")
