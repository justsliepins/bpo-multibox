#!/usr/bin/env python3
"""Great Polar Bears, the user's loop (shown 2026-10-18 11:04, mbwatchplay):

  * the tank sits at TANK_AT and is never touched (out of the broadcast)
  * the Voyagers sit on SAFE while waiting; they stand up shortly before the killed bears respawn
  * when pink bears are on the tank and every Voyager has full HP: Alt+R to IN (broadcast: all
    four trace together), one Conch Ray (F2) on the bear pile on the tank, Alt+R back to SAFE, sit
  * no ground clicks

usage: mbbearloop.py [HOURS]
"""
import os
import sys
import time

os.environ["MB_SNOW"] = "1"
import mbbears as b
import mbtarget as t

STATE = b.STATE
D = b.D
LEAD, TANK = 0, 4
AMIS = tuple(int(w) - 1 for w in os.environ.get("MB_AMIS", "1,2,3,4").split(","))   # the Voyagers in the loop
SAFE = (3119, 685)
IN = (3128, 674)
TANK_AT = (3132, 669)
FEET = b.FEET
CAM = b.CAM
ON_TANK = 110               # px: a pink bear this close to the tank's screen spot is "on the tank"
RESPAWN = 98                # s (Great Polar Bear spawn time)
STAND_EARLY = 8             # s before the respawn
sitting = False


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "bearloop: " + msg + "\n")


def broadcast_on():
    with open(os.path.join(STATE, "excluded"), "w") as f:
        f.write("".join(f"{i}\n" for i in [TANK] + [j for j in range(4) if j not in AMIS]))
    if open(os.path.join(STATE, "broadcast")).read().strip() != "on":
        import subprocess
        subprocess.run(["./multibox", "toggle"], cwd=os.path.dirname(os.path.abspath(__file__)))
        time.sleep(4)


def sit_all(on):
    """Sit / stand all Voyagers (Insert on broadcast)."""
    global sitting
    if sitting == on:
        return
    # the user: everything on broadcast - Insert in window 1, the broadcast sits the other
    # Voyagers (the tank is out of the broadcast and keeps sitting)
    with ACT:
        t.key(D[LEAD], "keydown", "Insert", "sleep", "0.15", "keyup", "Insert")
        time.sleep(0.5)
        sitting = on
    log("sit" if on else "stand up")


def tank_screen(me):
    """Where the tank shows on window 1's screen - measured, the user's camera is not the default
    one (the CAM formula put it 400 px off): from SAFE at the top edge, from IN where the user
    clicked the Conch (710,254)."""
    if b.near(me, IN, 2):
        return (710, 254)
    return (720, 45)


def bears_on_tank():
    """Pink bears on the tank, seen in the tank's own window (its feet at FEET; from SAFE the
    pile is at window 1's top edge, where pink_mobs masks the interface)."""
    pk = t.pink_mobs(t.screenshot(D[TANK]))
    return [q for q in pk if (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= 120 ** 2]


def conch_target():
    """From IN: the pink nearest the tank's spot on window 1's screen."""
    me = b.pos(LEAD)
    if not me:
        return None
    tx, ty = tank_screen(me)
    pk = t.pink_mobs(t.screenshot(D[LEAD]))
    near = [q for q in pk if (q[0] - tx) ** 2 + (q[1] - ty) ** 2 <= 150 ** 2]
    return min(near, key=lambda q: (q[0] - tx) ** 2 + (q[1] - ty) ** 2) if near else None


LOOT = ("bear paw", "tooth", "skin", "blueprint", "ducky", "rifle")
LOOT_AT = (3131, 671)          # next to the tank: the drops land around it


def loot_names(d):
    import mbreconnect
    try:
        found = mbreconnect.names_on_screen(t.screenshot(d))
    except Exception:
        return []
    out = []
    for k, (w, x, y) in enumerate(found):
        two = (w + " " + found[k + 1][0]).lower() if k + 1 < len(found) else w.lower()
        if any(n in w.lower() or n in two for n in LOOT):
            out.append((x, y))
    return out


def pick_up(i, until):
    """Click each wanted item label near window i's character (the item sits just above it),
    until the deadline (LuckyForth stayed 50 s at the tank and the respawn killed it)."""
    d = D[i]
    for _ in range(3):
        if time.time() > until:
            return
        items = [q for q in loot_names(d) if (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= 130 ** 2]
        if not items:
            return
        x, y = min(items, key=lambda q: (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2)
        t.click_at(d, t.ORIGIN[0] + int(x), t.ORIGIN[1] + int(y) - 14)
        time.sleep(1.5)


def loot_run():
    """Bears dead and valuable drops at the tank: the four step next to it, pick up, go back
    (the drops belong to whoever did the damage; the tank can't take them)."""
    if bears_on_tank() or not loot_names(D[TANK]):
        return
    import threading
    log("loot: picking up at the tank")
    pause = os.path.join(STATE, f"pause.{os.getpid()}")
    with open(pause, "w") as f:
        f.write("1")
    time.sleep(0.3)
    try:
        ths = [threading.Thread(target=b.tr, args=(D[i], *LOOT_AT)) for i in AMIS]
        [x.start() for x in ths]
        [x.join() for x in ths]
        t0 = time.time()
        while time.time() - t0 < 8 and not all(b.near(b.pos(i), LOOT_AT, 2) for i in AMIS):
            time.sleep(0.6)
        until = time.time() + 10
        ths = [threading.Thread(target=pick_up, args=(i, until)) for i in AMIS]
        [x.start() for x in ths]
        [x.join() for x in ths]
    finally:
        os.remove(pause)
        time.sleep(0.2)


ACT = None                     # threading.Lock, set in main(): one action at a time


def attackers(i):
    """Mobs near window i's character (pink bodies and name labels, never a Yeti), nearest first."""
    im = t.screenshot(D[i])
    pts = list(t.pink_mobs(im))
    for lx, ly in t.mob_labels(im):
        name = t.ocr(im.crop((lx, ly - 4, lx + 190, ly + 14)), 7, 2).lower()
        if "yeti" in name:
            continue
        pts.append((lx + 65, ly + 22))
    pts = [q for q in pts if (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2 <= 220 ** 2]
    return sorted(pts, key=lambda q: (q[0] - FEET[0]) ** 2 + (q[1] - FEET[1]) ** 2)


def defend(i, stop_at):
    """Window i is losing HP: Conch (Bolt while it cools down) the nearest mob from its own window
    until the HP stops dropping (the user: an Ami being attacked conches immediately)."""
    d = D[i]
    pause = os.path.join(STATE, f"pause.{os.getpid()}.d{i}")
    with open(pause, "w") as f:
        f.write("1")
    time.sleep(0.2)
    last_conch, quiet = 0.0, 0
    try:
        while time.time() < stop_at and quiet < 3:
            h0 = b.hp(i)
            tg = attackers(i)
            if tg:
                x, y = tg[0]
                k = "F2" if time.time() - last_conch > 4.5 else "F1"
                if k == "F2":
                    last_conch = time.time()
                b.cast(i, k, x, y)
            time.sleep(1.0)
            quiet = quiet + 1 if b.hp(i) >= h0 - 0.005 else 0
        if sitting:
            t.key(d, "keydown", "Insert", "sleep", "0.15", "keyup", "Insert")   # back down like the rest
    finally:
        os.remove(pause)


def guard():
    """Every 0.5 s: any Voyager losing HP -> defend it at once."""
    import threading
    prev = {i: b.hp(i) for i in AMIS}
    while True:
        time.sleep(0.5)
        for i in AMIS:
            try:
                h = b.hp(i)
            except Exception:
                continue
            if 0 < h < prev.get(i, 1) - 0.01 and not b.dead(i):
                log(f"window {i + 1} attacked ({h:.0%}): conch the attacker")
                with ACT:
                    defend(i, time.time() + 20)
                h = b.hp(i)
            prev[i] = h


def all_full():
    return all(not b.dead(i) and b.hp(i) >= 0.95 for i in AMIS)


def sp_ok():
    return all(b.sp(i) >= 0.15 for i in AMIS)


def trace_lead(xy, limit=20):
    """Alt+R in all four Voyager windows at the same moment, each in its own window, with the
    broadcast paused: on broadcast the dialog clicks were shifted for followers standing a tile
    off and landed on the ground - they walked into the bears (2026-10-18 11:14). Waits until all
    four are there."""
    import threading
    pause = os.path.join(STATE, f"pause.{os.getpid()}")
    with open(pause, "w") as f:
        f.write("1")
    time.sleep(0.3)
    try:
        ths = [threading.Thread(target=b.tr, args=(D[i], *xy)) for i in AMIS]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
    finally:
        os.remove(pause)
        time.sleep(0.2)
    t0 = time.time()
    while time.time() - t0 < limit:
        time.sleep(0.8)
        if all(b.near(b.pos(i), xy, 1) for i in AMIS):
            return True
    late = [i + 1 for i in AMIS if not b.near(b.pos(i), xy, 1)]
    log(f"not everyone reached {xy}: windows {late} at {[b.pos(i - 1) for i in late]}")
    return False


def main(hours):
    global sitting
    until = time.time() + hours * 3600
    global ACT
    import threading
    ACT = threading.RLock()        # re-entrant: sit_all() runs inside a trip too
    broadcast_on()
    log(f"start: safe {SAFE}, in {IN}, tank {TANK_AT} (tank out of the broadcast)")
    threading.Thread(target=guard, daemon=True).start()
    if not all(b.near(b.pos(i), SAFE, 1) for i in AMIS):
        trace_lead(SAFE)
    last_conch = 0.0
    last_loot = 0.0
    trips = 0
    sitting = False
    sit_all(True)
    while time.time() < until:
        try:
            dead = [i + 1 for i in AMIS + (TANK,) if b.dead(i)]
            if dead:
                log(f"DEAD: windows {dead} - waiting (needs the user)")
                time.sleep(30)
                continue
            since = time.time() - last_conch
            bears = bears_on_tank()
            # stand up shortly before the respawn (or now, if bears are already on the tank)
            if sitting and (bears or (last_conch and since > RESPAWN - STAND_EARLY)):
                if all_full() and sp_ok():
                    sit_all(False)
            if not bears:
                if not sitting and last_conch and since < RESPAWN - STAND_EARLY - 5:
                    sit_all(True)
                time.sleep(1.5)
                continue
            if not (all_full() and sp_ok()):
                sit_all(True)                 # rest until everyone is full
                time.sleep(3)
                continue
            if sitting:
                sit_all(False)
                time.sleep(0.8)
            # in, one Conch on the pile on the tank, back
            with ACT:
                if not trace_lead(IN):
                    trace_lead(SAFE)
                    continue
                tgt = conch_target()
                if tgt:
                    x, y = tgt
                    b.cast(LEAD, "F2", x, y)
                    last_conch = time.time()
                    trips += 1
                    time.sleep(1.2)
                else:
                    log("no pink near the tank from IN")
                trace_lead(SAFE)
                hp = [round(float(b.hp(i)), 2) for i in AMIS]
                log(f"trip {trips}: conch done, back at the safe spot, HP {hp}")
                if not bears_on_tank():
                    sit_all(True)
        except Exception as exc:
            log(f"loop: {exc!r}")
            time.sleep(3)
    sit_all(True)
    log("finished")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 10)
