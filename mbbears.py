#!/usr/bin/env python3
"""Great Polar Bear farm (Upper Icicle east, darkblue map). The user's procedure (2026-10-17):

  the party waits at SAFE (17 tiles north of the bear zone, outside their 10-tile vision)
  -> the tank walks into the bears, Tiger Roar (F2) pulls the swarm, it sits
  -> when the bears are on the tank, the 4 Voyagers walk in and Conch Ray (F2) them
     (Lightning Bolt F1 between Conch cooldowns)
  -> the Voyagers walk back to SAFE and sit; the tank stays, roars/taunts (F1) what comes
  -> repeat when the Voyagers have their SP back.

Route and spot are from the server files (darkblueChaSpn.txt, CharacterInfo AI/vision, darkblue.atr
terrain): Icespire Haven exits north, y~615 above the Steel-shell Snail zone, y~598 between the
Infant Icy Dragons (16+ tiles) and the Water Fairies (18+ tiles). Great Polar Bear texture 0504 is
pink. Deaths respawn at Icespire Haven (spawn recorded) and walk the route again.

    mbbears.py [HOURS]
"""
import os
import sys
import threading
import time

import mbcoords
import mbquest as q
import mbtarget as t

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "multibox")
os.environ.setdefault("MB_STATE", STATE)
D = [f":6{i}" for i in range(1, 6)]
AMIS, TANK = [0, 1, 2, 3], 4
ROUTE = [(2671, 640), (2665, 630), (2666, 620), (2669, 614), (2687, 609), (2699, 606),
         (2711, 606), (2723, 606), (2735, 606), (2747, 602), (2759, 602), (2771, 602), (2796, 599), (2821, 598),
         (2871, 596), (2921, 598), (2946, 603), (2971, 610), (3021, 616), (3071, 618), (3100, 630)]
# The haven exit goes WEST then north (x 2665-2671): a trace from the haven straight to y 606
# made the game's pathfinder cut through the Steel-shell Snails; LuckySecond died there 6 times.
# Short hops so the game's own pathfinding has no room to detour. Snails 10+ tiles all the way.
SAFE = ROUTE[-1]
PULL = (3100, 658)          # inside the bear zone (3057-3145 x 647-686), its north part
FEET = (683, 384)
SIT_KEY = "Insert"


def log(msg):
    q.say(f"bears: {msg}")


def pos(i):
    return mbcoords.read(D[i])


def hp(i, im=None):
    try:
        return t.party_hp(im or t.screenshot(D[i]))[0] or 0
    except Exception:
        return 0


def sp(i, im=None):
    try:
        return t.party_sp(im or t.screenshot(D[i]))[0] or 0
    except Exception:
        return 0


def near(p, xy, r):
    return p and abs(p[0] - xy[0]) <= r and abs(p[1] - xy[1]) <= r


sitting = {i: False for i in range(5)}


def sit(i, on):
    if sitting[i] != on:
        t.key(D[i], "keydown", SIT_KEY, "sleep", "0.15", "keyup", SIT_KEY)
        sitting[i] = on
        time.sleep(0.6)


def tr(d, x, y):
    """Alt+R trace that never leaves the dialog open (an open dialog swallowed clicks: a
    Voyager stood in the fight not casting, 2026-10-17)."""
    ok = t._trace(d, x, y)
    time.sleep(0.3)
    try:
        if t.trace_open(t.screenshot(d)):
            t._close_trace(d)
    except Exception:
        pass
    return ok


def walk(i, xy, near_r=4, limit=150):
    """Auto-trace to xy; stands up first; re-traces when stopped; True when arrived."""
    d = D[i]
    sit(i, False)
    tr(d, *xy)
    last, still, t0 = None, 0, time.time()
    while time.time() - t0 < limit:
        time.sleep(1.5)
        if dead(i):
            return False
        p = pos(i)
        if near(p, xy, near_r):
            return True
        still = still + 1 if p == last else 0
        last = p
        if still >= 3:
            if still >= 6:                      # maybe seated out of step: toggle and retry
                t.key(d, "keydown", SIT_KEY, "sleep", "0.15", "keyup", SIT_KEY)
                time.sleep(0.6)
                sitting[i] = False
                still = 0
            tr(d, *xy)
    return False


routing = set()                                 # windows walking the route (one walker each)


def follow_route(i, to_safe=True):
    if i in routing:
        return False
    routing.add(i)
    try:
        return _follow_route(i, to_safe)
    finally:
        routing.discard(i)


def _follow_route(i, to_safe=True):
    """From wherever on the route (or the haven) to SAFE, waypoint by waypoint."""
    if dead(i):
        return False
    if hp(i) < 0.9:                             # never walk out hurt (a restart mid-rest: 1 HP)
        os.environ["RECOVER_REST"] = "0.95"
        t.rest_one(STATE, i, D[i])
        sitting[i] = False
    p = pos(i)
    if not p:
        return False
    k = min(range(len(ROUTE)), key=lambda j: max(abs(ROUTE[j][0] - p[0]), abs(ROUTE[j][1] - p[1])))
    if max(abs(ROUTE[k][0] - p[0]), abs(ROUTE[k][1] - p[1])) > 40 and not near(p, (2680, 645), 40):
        log(f"window {i + 1} at {p}: not on the route, not at the haven - not walking blind")
        return False
    for wp in ROUTE[k:]:
        if not walk(i, wp, near_r=5):
            log(f"window {i + 1}: stuck before {wp} at {pos(i)}")
            return False
    return True


def dead(i):
    return (t.own_hp(D[i]) or 0) == 0


def recover(i):
    """Dead: revive (Icespire Haven), rest, walk the route back to SAFE."""
    d = D[i]
    log(f"window {i + 1} died: revive at Icespire Haven, rest, route back")
    try:
        t.close_popups(STATE, i, d)
        t.revive(STATE, i, d)
        sitting[i] = False
        os.environ["RECOVER_REST"] = "0.95"
        t.rest_one(STATE, i, d)
        sitting[i] = False
        if not os.environ.get("MB_SNOW"):
            follow_route(i)
    except Exception as exc:
        log(f"window {i + 1}: recover failed: {exc!r}")


def pinks(i, im=None, radius=None, centre=FEET):
    m = t.pink_mobs(im or t.screenshot(D[i]))
    if radius:
        m = [b for b in m if (b[0] - centre[0]) ** 2 + (b[1] - centre[1]) ** 2 <= radius ** 2]
    return m


def cast(i, key, x, y):
    d = D[i]
    t.key(d, "keydown", key, "sleep", "0.15", "keyup", key)
    time.sleep(0.4)
    X, Y = t.ORIGIN[0] + int(x), t.ORIGIN[1] + int(y)
    t.key(d, "mousemove", str(X), str(Y), "sleep", "0.15", "mousedown", "1", "sleep", "0.15", "mouseup", "1")
    time.sleep(0.3)


def drop_skill(i):
    t.key(D[i], "mousedown", "3", "sleep", "0.12", "mouseup", "3")


CAM = ((18.1, 13.3), (-11.8, 17.4))   # screen px per tile (x step, y step), default camera (mbleader)


def tank_on_screen(me, tp):
    dx, dy = tp[0] - me[0], tp[1] - me[1]
    return (FEET[0] + dx * CAM[0][0] + dy * CAM[1][0], FEET[1] + dx * CAM[0][1] + dy * CAM[1][1])


# --- the Voyagers' attack run --------------------------------------------------------------------
def ami_run(i, stop):
    """Walk next to the tank, Conch/Bolt the pink bears until none is near or SP/HP is low."""
    busy.add(i)
    try:
        _ami_run(i, stop)
    finally:
        busy.discard(i)


def _ami_run(i, stop):
    tp = pos(TANK)
    if not tp:
        return
    spot = (tp[0], tp[1] - 11)                # north of the tank, at range (bears turned on Voyagers at 6)
    walk(i, spot, near_r=3, limit=60)
    last_conch = 0.0
    t0 = time.time()
    while not stop.is_set() and time.time() - t0 < 120:
        if dead(i):
            return
        im = t.screenshot(D[i])
        if sp(i, im) < 0.15 or hp(i, im) < 0.65:   # bears switch to Voyagers: leave early
            break
        tp, me = pos(TANK), pos(i)
        if not tp or not me:
            break
        tx, ty = tank_on_screen(me, tp)
        targets = [b for b in pinks(i, im) if (b[0] - tx) ** 2 + (b[1] - ty) ** 2 <= 140 ** 2]
        if not targets:                       # only bears held by the tank, never loose ones
            break
        x, y = min(targets, key=lambda b: (b[0] - tx) ** 2 + (b[1] - ty) ** 2)
        if time.time() - last_conch > 4.5:
            cast(i, "F2", x, y)               # Conch Ray: the whole line
            last_conch = time.time()
        else:
            cast(i, "F1", x, y)               # Lightning Bolt
        time.sleep(1.0)
        drop_skill(i)
        time.sleep(0.8)
    drop_skill(i)
    walk(i, SAFE, near_r=4, limit=90)
    sit(i, True)


# --- self-defence: wandering Lv37-39 sheep/boars/scorpions (aggressive) attack anyone ------------
busy = set()                                    # windows in an attack run (they fight anyway)


def defender(i, stop):
    """Hit since the last look: bolt the mob nearest the character (any mob: label + bar)."""
    last = hp(i)
    while not stop.is_set():
        time.sleep(2)
        if i in busy or dead(i):
            last = 1.0
            continue
        h = hp(i)
        try:
            im = t.screenshot(D[i])
            bars = t.mob_bars(im)
        except Exception:
            bars = []
        close = [b for b in bars if (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2 < 170 ** 2]
        # hit, or any mob right next to it (a hurt Voyager arriving at SAFE with a boar behind it:
        # everyone there helps)
        if h < last - 0.02 or (close and i != TANK):
            try:
                bars = close or bars
                if bars:
                    bx, by = min(bars, key=lambda b: (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2)
                    with lock(i):
                        sit(i, False)
                        cast(i, "F1" if i != TANK else "F1", bx, by + 25)   # the body is under its bar
                        time.sleep(0.9)
                        drop_skill(i)
                    log(f"window {i + 1} hit ({h:.0%}): hits back at {bx:.0f},{by:.0f}")
            except Exception as exc:
                log(f"window {i + 1}: defence failed: {exc!r}")
        last = h


_locks = {i: threading.Lock() for i in range(5)}


def lock(i):
    return _locks[i]


# --- the cycle -----------------------------------------------------------------------------------
def tank_pull():
    d = D[TANK]
    walk(TANK, PULL, near_r=4, limit=90)
    for _ in range(3):
        tg = pinks(TANK, radius=400)
        if tg:
            x, y = min(tg, key=lambda b: (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2)
            cast(TANK, "F2", x, y)            # Tiger Roar
            time.sleep(1.2)
            drop_skill(TANK)
            break
        time.sleep(3)
    sit(TANK, True)


def tank_keep():
    """While the Voyagers fight: roar again / taunt what is near; stand up when hit hard."""
    im = t.screenshot(D[TANK])
    near_b = pinks(TANK, im, radius=330)
    if not near_b:
        return 0
    x, y = min(near_b, key=lambda b: (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2)
    sit(TANK, False)
    cast(TANK, "F2", x, y)
    time.sleep(1.0)
    cast(TANK, "F1", x, y)
    time.sleep(0.8)
    drop_skill(TANK)
    return len(near_b)


def ready_amis():
    alive = [i for i in AMIS if not dead(i)]
    return [i for i in alive if near(pos(i), SAFE, 8) and sp(i) >= 0.75 and hp(i) >= 0.9]


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 6
    until = time.time() + hours * 3600
    for i in range(5):
        q.close_all(STATE, i, D[i])
    stop_all = threading.Event()
    for i in range(5):
        threading.Thread(target=defender, args=(i, stop_all), daemon=True).start()
    with q.Pause(STATE, D[0]):
        log("gathering at SAFE " + str(SAFE))
        ths = [threading.Thread(target=follow_route, args=(i,)) for i in range(5)]
        for th in ths:
            th.start()
            time.sleep(1.5)
        for th in ths:
            th.join()
        for i in range(5):
            if near(pos(i), SAFE, 8):
                sit(i, True)
        log(f"at SAFE: {[pos(i) for i in range(5)]}")
        cycles = 0
        while time.time() < until:
            for i in range(5):                # deaths first
                if dead(i):
                    recover(i)
                    if i in AMIS and near(pos(i), SAFE, 8):
                        sit(i, True)
            if hp(TANK) < 0.45:               # the tank rests at SAFE
                log(f"tank low HP ({hp(TANK):.0%}): resting at SAFE")
                walk(TANK, SAFE, near_r=4, limit=90)
                sit(TANK, True)
                while hp(TANK) < 0.9 and not dead(TANK):
                    time.sleep(3)
                continue
            ready = ready_amis()
            if len(ready) < 4:                # all four: 13 bears on 3 Voyagers killed one (17:46)
                time.sleep(4)
                continue
            # pull
            tank_pull()
            t0 = time.time()
            held, t0 = 0, time.time()          # the tank must hold the swarm first (user)
            while time.time() - t0 < 30:
                n_on = len(pinks(TANK, radius=130))
                held = held + 1 if n_on >= 3 else 0
                if held >= 3:                     # 3 looks in a row with 3+ bears on the tank
                    break
                if held == 0 and time.time() - t0 > 8:
                    tank_keep()
                time.sleep(1.5)
            n = len(pinks(TANK, radius=130))
            if n == 0:
                log("no bears near the tank (respawning): waiting")
                time.sleep(15)
                continue
            log(f"cycle {cycles + 1}: {n} bears on the tank (HP {hp(TANK):.0%}), {len(ready)} Voyagers go in")
            stop = threading.Event()
            ths = [threading.Thread(target=ami_run, args=(i, stop)) for i in ready]
            for th in ths:
                th.start()
                time.sleep(0.5)
            while any(th.is_alive() for th in ths):
                if dead(TANK):
                    log("the tank died: the Voyagers pull out")
                    stop.set()
                    break
                if hp(TANK) < 0.3:
                    log(f"tank at {hp(TANK):.0%}: Voyagers finish, tank holds")
                tank_keep()
                time.sleep(2.5)
            for th in ths:
                th.join()
            sit(TANK, True)
            cycles += 1
            log(f"cycle {cycles} done: tank HP {hp(TANK):.0%}, Voyagers SP "
                f"{[round(sp(i), 2) for i in AMIS]}")
    log("finished")


if __name__ == "__main__" and not os.environ.get("MB_SNOW"):
    main()


# --- night mode: the passive Horrific Snowmen (Lv50, 0.5% Sealed) next to SAFE ------------------
SNOW_SPOT = (3125, 600)      # inside the passive snowman zone (3091-3161 x 550-626); bears (y>=647) roamed up to 3100,632


def snow_ami(i, stop):
    """Bolt/Conch the nearest pink snowman; sit at 30% SP until 85%; never chase far."""
    last_conch = 0.0
    while not stop.is_set():
        try:
            if dead(i):
                time.sleep(3)
                continue
            p = pos(i)
            if p and not near(p, SNOW_SPOT, 40):      # far (revived at the haven): the route only,
                if not follow_route(i):              # never a straight line through the snails
                    time.sleep(10)
                continue
            if p and not near(p, SNOW_SPOT, 12):
                walk(i, SNOW_SPOT, near_r=3, limit=60)
            im = t.screenshot(D[i])
            if sp(i, im) < 0.3 or hp(i, im) < 0.5:
                with lock(i):
                    sit(i, True)
                while not stop.is_set() and not dead(i) and (sp(i) < 0.85 or hp(i) < 0.8):
                    time.sleep(3)
                continue
            tg = [b for b in pinks(i, im) if (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2 <= 300 ** 2]
            if not tg:
                with lock(i):
                    sit(i, True)                   # regen while waiting for a respawn
                time.sleep(4)
                continue
            x, y = min(tg, key=lambda b: (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2)
            with lock(i):
                sit(i, False)
                if time.time() - last_conch > 4.5:
                    cast(i, "F2", x, y)
                    last_conch = time.time()
                else:
                    cast(i, "F1", x, y)
                time.sleep(1.0)
                drop_skill(i)
            time.sleep(0.6)
        except Exception as exc:
            log(f"snow window {i + 1}: {exc!r}")
            time.sleep(3)


def snow_tank(stop):
    """Stay at the spot; taunt (F1) any non-pink mob close to the group, roar (F2) if several."""
    while not stop.is_set():
        try:
            if dead(TANK):
                time.sleep(3)
                continue
            p = pos(TANK)
            if p and not near(p, SNOW_SPOT, 40):
                if not follow_route(TANK):
                    time.sleep(10)
                continue
            if p and not near(p, SNOW_SPOT, 6):
                walk(TANK, SNOW_SPOT, near_r=3, limit=60)
            im = t.screenshot(D[TANK])
            pk = t.pink_mobs(im)
            bars = [b for b in t.mob_bars(im)
                    if (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2 <= 230 ** 2
                    and not any(abs(b[0] - x) < 60 and -10 < y - b[1] < 90 for x, y in pk)]
            if bars:
                bx, by = min(bars, key=lambda b: (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2)
                with lock(TANK):
                    sit(TANK, False)
                    cast(TANK, "F2" if len(bars) >= 2 else "F1", bx, by + 25)
                    time.sleep(0.8)
                    drop_skill(TANK)
                    t.key(D[TANK], "mousemove", str(t.ORIGIN[0] + int(bx)), str(t.ORIGIN[1] + int(by) + 25),
                          "sleep", "0.15", "mousedown", "1", "sleep", "0.15", "mouseup", "1")   # and hit it
                time.sleep(2)
                continue
            if hp(TANK, im) < 0.6:
                with lock(TANK):
                    sit(TANK, True)
            time.sleep(2)
        except Exception as exc:
            log(f"snow tank: {exc!r}")
            time.sleep(3)


def tank_taunt_pink():
    """Taunt (F1) the pink snowman nearest the tank, the one the volley hits: it turns on the tank."""
    try:
        tg = [b for b in pinks(TANK) if (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2 <= 300 ** 2]
        if not tg:
            return
        x, y = min(tg, key=lambda b: (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2)
        with lock(TANK):
            sit(TANK, False)
            cast(TANK, "F1", x, y)
            time.sleep(0.8)
            drop_skill(TANK)
    except Exception as exc:
        log(f"snow tank taunt: {exc!r}")


def snow_main(hours):
    until = time.time() + hours * 3600
    stop = threading.Event()
    for i in range(5):
        q.close_all(STATE, i, D[i])
    log("snow mode: gathering at " + str(SNOW_SPOT))
    ths = [threading.Thread(target=follow_route, args=(i,)) for i in range(5)]
    for th in ths:
        th.start()
        time.sleep(1.5)
    for th in ths:
        th.join()
    workers = [threading.Thread(target=snow_volley, args=(stop,), daemon=True)]
    workers.append(threading.Thread(target=snow_tank, args=(stop,), daemon=True))
    for w in workers:
        w.start()
    last = time.time()
    while time.time() < until:
        time.sleep(10)
        for i in range(5):
            if dead(i):
                recover(i)               # revive at Icespire Haven, rest, route back
        if time.time() - last > 600:
            last = time.time()
            log(f"snow mode: HP {[round(hp(i), 2) for i in range(5)]} SP {[round(sp(i), 2) for i in range(5)]}")
    stop.set()
    log("snow mode finished")


def snow_volley(stop):
    """All four Voyagers on one tile, cameras on Home: window 1 picks the target, all four cast at
    the same screen point at the same moment (the user: attack together, led by the main)."""
    last_conch, last_home, last_taunt = 0.0, 0.0, 0.0
    while not stop.is_set():
        try:
            alive = [i for i in AMIS if not dead(i)]
            here = [i for i in alive if near(pos(i), SNOW_SPOT, 3)]
            far = [i for i in alive if i not in here]
            for i in far:                           # stragglers: route if far, else onto the tile
                if not near(pos(i), SNOW_SPOT, 40):
                    threading.Thread(target=follow_route, args=(i,), daemon=True).start()
                else:
                    sit(i, False)
                    tr(D[i], *SNOW_SPOT)
            if not here or 0 not in here:
                time.sleep(4)
                continue
            if time.time() - last_home > 60:        # identical cameras for identical clicks
                for i in here:
                    t.key(D[i], "keydown", "Home", "sleep", "0.15", "keyup", "Home")
                last_home = time.time()
                time.sleep(1.2)
            sps = [sp(i) for i in here]
            hps = [hp(i) for i in here]
            close = [b for b in pinks(0) if (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2 <= 200 ** 2]
            # never sit down with a snowman next to us (it keeps hitting a sitter): finish it first
            if (min(sps) < 0.3 or min(hps) < 0.5) and not (close and min(sps) >= 0.08):
                for i in here:
                    sit(i, True)
                while not stop.is_set() and min(sp(i) for i in here) < 0.85 and not any(dead(i) for i in here):
                    time.sleep(3)
                continue
            tg = [b for b in pinks(0) if (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2 <= 300 ** 2]
            if not tg:
                for i in here:
                    sit(i, True)
                time.sleep(4)
                continue
            x, y = min(tg, key=lambda b: (b[0] - FEET[0]) ** 2 + (b[1] - FEET[1]) ** 2)
            if time.time() - last_taunt > 6 and not dead(TANK) and near(pos(TANK), SNOW_SPOT, 6):
                last_taunt = time.time()
                threading.Thread(target=tank_taunt_pink, daemon=True).start()
            key = "F2" if time.time() - last_conch > 4.5 else "F1"
            if key == "F2":
                last_conch = time.time()
            def one(i):
                with lock(i):
                    sit(i, False)
                    cast(i, key, x, y)
                    time.sleep(1.0)
                    drop_skill(i)
            ths = [threading.Thread(target=one, args=(i,)) for i in here]
            for th in ths:
                th.start()
            for th in ths:
                th.join()
            time.sleep(0.5)
        except Exception as exc:
            log(f"snow volley: {exc!r}")
            time.sleep(3)


if __name__ == "__main__" and os.environ.get("MB_SNOW"):
    snow_main(float(sys.argv[1]) if len(sys.argv) > 1 else 8)
    sys.exit(0)
