#!/usr/bin/env python3
"""Supervisor for mbleader: levels to 40 without the user.

Every few minutes: read the party's levels (window 5's frames, read-only). When a character
levels up, spend its stat points (Amis SPR, tank CON) and skill points (Lightning Bolt) while the
leader holds. When the party out-levels its spot (the server's EXP cut starts 4 levels above the
mob: exp_and_level.lua), move once to the next passive-only spot and restart the leader there.

Spots (passive mobs only, CharacterInfo AI 5, no aggressive spawn overlapping):
  Rookie Boxeroo L27, Andes Forest 4A      until the lowest level is 31
  Grassland Elk L31 / White Owlie L32, Argent 9A   from 31 (respawn: Abandon Mine Haven)

usage: mbsuper.py
"""
import os
import subprocess
import time

import mbcoords
import mblevels
import mbtarget as t
import mbgrind as g
import mbquest as q
import mbreconnect

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "multibox")
D = [":61", ":62", ":63", ":64", ":65"]
g.STATE, g.DISPLAYS = STATE, D
os.environ["MB_STATE"] = STATE
HOLD = os.path.join(STATE, "leader.hold")
SPOT_FILE = os.path.join(STATE, "super.spot")
O = t.ORIGIN
EVERY = 60
ARGENT_8A = (1440, 2640, 1567, 2757)       # Meadow Deer L25 (+ Rookie Boxeroo L27, Owlie L21): passive only
ARGENT_9A = (1195, 2755, 1260, 2805)       # Slowpoke Snail L35 field (Belda Meadow): the Elk field got crowded again (user, 2026-10-15); north edge 2735 -> 2755: Grassland Wolves (L33, aggressive) killed Amis there (2026-10-17)
ARGENT_9A_CAMP = (1228, 2770)
MOVE_AT = 31                  # Boxeroo L27: full EXP up to level 30, cut from 32


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "super: " + msg + "\n")


def leader_pid():
    try:
        pid = int(open(os.path.join(STATE, "leader.pid")).read())
        return pid if b"mbleader" in open(f"/proc/{pid}/cmdline", "rb").read() else None
    except (OSError, ValueError):
        return None


def start_leader(box=None):
    args = ["python3", os.path.join(HERE, "mbleader.py"), *map(str, box or ())]
    out = open(os.path.join(STATE, "leader.out"), "a")
    if leader_pid():                            # never two: both would click in window 1
        return
    proc = subprocess.Popen(args, cwd=HERE, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):                         # until it has written its pid file: a check right
        time.sleep(0.25)                        # after the launch saw "not running" and started a second
        if leader_pid() == proc.pid:
            break


def stop_leader():
    pid = leader_pid()
    if pid:
        # the whole process group: a death walk-back (mbtarget goto) the leader had started kept
        # walking LuckyThird back to the field during the level-40 gear stop (2026-10-17)
        try:
            os.killpg(pid, 15)
        except OSError:
            os.kill(pid, 15)
        time.sleep(1)


def broadcast(on):
    if (t.read(os.path.join(STATE, "broadcast")).strip() == "on") != on:
        subprocess.run([os.path.join(HERE, "multibox"), "toggle"], cwd=HERE,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        time.sleep(2)


def ensure_standing():
    for d in D:
        p0 = mbcoords.read(d)
        t.click_at(d, O[0] + 723, O[1] + 414)
        time.sleep(1.2)
        if mbcoords.read(d) == p0:
            t.key(d, "keydown", "Insert", "sleep", "0.05", "keyup", "Insert")
            time.sleep(0.8)


TODO = os.path.join(STATE, "points.todo")   # windows with points to spend, for the leader's next rest


def queue_points(windows):
    """Opening the stat window mid-fight got characters killed (3 deaths, each within a minute of
    a level-up): only note who has points. The leader runs `mbsuper.py spend` while the party
    sits resting and nobody is being hit."""
    old = {int(x) for x in t.read(TODO).split() if x.isdigit()}
    t.write(TODO, " ".join(map(str, sorted(old | set(windows)))) + "\n")


def spend_points():
    """Run by the leader (its main loop waits for this) during a rest."""
    windows = [int(x) for x in t.read(TODO).split() if x.isdigit()]
    for i in windows:
        if i == 4:
            t.write(HOLD, "4\n")              # the tank's own loop keeps out of its window
        try:
            g.allocate_stats(i, D[i])
            g.allocate_skills(i, D[i])
        except Exception as exc:
            log(f"window {i + 1}: spending points failed: {exc!r}")
        finally:
            if os.path.exists(HOLD):
                os.remove(HOLD)
    if os.path.exists(TODO):
        os.remove(TODO)


GEAR_FILE = os.path.join(HERE, "gear_done.txt")     # highest milestone handled (kept across reboots)
HAVEN = (1010, 2962)                                # inside Andes Forest Haven's safe zone


def gear_done():
    s_ = t.read(GEAR_FILE).strip()
    return int(s_) if s_.isdigit() else 0


def gear_stop(milestone, spot):
    """Everyone has reached a 5-level milestone (25, 30, ...): open the chests it unlocks and put on
    the new gear (the user's rule). It takes minutes per window with the bag open, so not in the
    field: the Andes party walks into its haven first; broadcast is off the whole time."""
    log(f"everyone is level {milestone}+: gear stop (chests, new equipment)")
    stop_leader()
    broadcast(False)
    ensure_standing()
    if spot == "andes":
        for i, d in enumerate(D):
            if (t.own_hp(d) or 0) == 0:
                try:
                    t.close_popups(STATE, i, d)
                    t.revive(STATE, i, d)       # revives in the haven
                except Exception as exc:
                    log(f"window {i + 1}: revive failed: {exc!r}")
            else:
                t._trace(d, *HAVEN)
        for _ in range(60):
            time.sleep(2)
            pos = [mbcoords.read(d) for d in D]
            if all(p_ and max(abs(p_[0] - HAVEN[0]), abs(p_[1] - HAVEN[1])) <= 15 for p_ in pos):
                break
    town = spot != "andes"
    if town:
        # the Argent fields have no shop and no safe zone: walk to Abandon Mine Haven, Meiya's
        # teleport to Argent, and do the stop at Blacksmith Goldie (sell + repair there too:
        # every death costs 5% durability and nothing else repairs the gear)
        t.write(SPOT_FILE, "moving\n")
        g.to_haven("ABANDON")
        for i, d in enumerate(D):
            if not q.teleport(STATE, i, d, g.MEIYA[0], g.MEIYA[1], ["Teleport: Argent City"]):
                log(f"window {i + 1}: Meiya's teleport to Argent failed")
    for i, d in enumerate(D):
        try:
            if town:
                if not g._near(mbcoords.read(d), g.TOWN, 150):
                    log(f"window {i + 1}: not in Argent: gear upgrade only")
                    g.upgrade_gear(i, d, milestone)
                    continue
                os.environ["MB_KEEP"] = "Blueprint|Sheepskin|Chest|Skill|Book|Fairy"
                os.environ["MB_LEVEL"] = str(milestone)
                q.KEEP_WORDS = os.environ["MB_KEEP"].split("|")
                g.upgrade_gear(i, d, milestone)
                n = q.sell_everything(STATE, i, d)
                log(f"window {i + 1}: sold {n} item(s) at Goldie")
                g.upgrade_gear(i, d, milestone)
                import mbrepair
                log(f"window {i + 1}: repaired {mbrepair.repair(i, d)}")
                continue
            g.upgrade_gear(i, d, milestone)
            if spot == "andes":
                # chests need 5 free bag slots: sell at the haven's shop (never gear of the
                # character's own level band: equip first, sell, then equip what the chests gave)
                os.environ["MB_KEEP"] = "Blueprint|Sheepskin|Chest|Skill|Book|Fairy"
                os.environ["MB_LEVEL"] = str(milestone)
                q.KEEP_WORDS = os.environ["MB_KEEP"].split("|")
                n = q.sell_everything(STATE, i, d, shop="Accessory - Rikka", shop_xy=(997, 2968))
                log(f"window {i + 1}: sold {n} item(s) at Rikka")
                g.upgrade_gear(i, d, milestone)
        except Exception as exc:
            log(f"window {i + 1}: gear upgrade failed: {exc!r}")
    t.write(GEAR_FILE, f"{milestone}\n")
    if town:
        g.to_haven("ABANDON", record=False)     # Jovial's teleport back
        t.set_excluded(STATE, 4, False)
        ok = g.goto(*ARGENT_9A_CAMP)
        log(f"back at the field: {ok} {[mbcoords.read(d) for d in D]}")
        t.write(SPOT_FILE, spot + "\n")
    broadcast(True)
    log(f"gear stop for level {milestone} done")
    start_leader(BOX.get(spot))


BOX = {"argent8a": ARGENT_8A, "argent9a": ARGENT_9A}


def move_to_argent_8a():
    """Andes -> Argent 8A. The Andes field's Killer Shrooms hit for ~795 and an Ami has ~740 HP:
    10 deaths in 30 min (2026-10-13), 5% durability each. Argent 8/8A has passive mobs only."""
    log("moving to Argent 8A (passive mobs only): Killer Shrooms one-shot the Amis in the Andes field")
    t.write(SPOT_FILE, "moving\n")
    stop_leader()
    broadcast(False)
    ensure_standing()
    for i, d in enumerate(D):
        if (t.own_hp(d) or 0) == 0:
            t.close_popups(STATE, i, d)
            t.revive(STATE, i, d)
    g.to_argent()
    g.to_haven("ABANDON")                     # records spawn at Abandon Mine Haven
    t.set_excluded(STATE, 4, False)
    ok = g.goto(1503, 2698)
    log(f"at Argent 8A: {ok} {[mbcoords.read(d) for d in D]}")
    broadcast(True)
    t.write(SPOT_FILE, "argent8a\n")
    start_leader(ARGENT_8A)


def walk_to_argent_9a():
    """8A -> 9A next door (Elk L31 / White Owlie L32), a planned walk, no teleports."""
    log("level 31: walking over to Argent 9A (Elk L31 / White Owlie L32)")
    t.write(SPOT_FILE, "moving\n")
    stop_leader()
    broadcast(False)
    ensure_standing()
    t.set_excluded(STATE, 4, False)
    ok = g.goto(*ARGENT_9A_CAMP)
    log(f"at Argent 9A: {ok} {[mbcoords.read(d) for d in D]}")
    broadcast(True)
    t.write(SPOT_FILE, "argent9a\n")
    start_leader(ARGENT_9A)


def move_to_argent_9a():
    log("party out-levelled the Boxeroos: moving to Argent 9A (Elk L31 / White Owlie L32, passive)")
    t.write(SPOT_FILE, "moving\n")
    stop_leader()
    broadcast(False)
    ensure_standing()
    g.to_argent()
    g.to_haven("ABANDON")                     # records spawn at Abandon Mine Haven: deaths respawn close
    t.set_excluded(STATE, 4, False)
    ok = g.goto(*ARGENT_9A_CAMP)
    log(f"at Argent 9A: {ok} {[mbcoords.read(d) for d in D]}")
    broadcast(True)
    t.write(SPOT_FILE, "argent9a\n")
    start_leader(ARGENT_9A)


def walk_back_from_haven(spot):
    """The whole party of Amis revived at the Abandon Mine Haven spawn: walk them to the camp."""
    log(f"the Amis revived at Abandon Mine Haven: walking them back to {spot}")
    t.write(SPOT_FILE, "moving\n")
    stop_leader()
    broadcast(False)
    for i, d in enumerate(D):
        if (t.own_hp(d) or 0) == 0:
            t.close_popups(STATE, i, d)
            t.revive(STATE, i, d)               # in town: the Abandon Mine Haven spawn
    time.sleep(5)
    ensure_standing()
    tank = mbcoords.read(D[4])                  # still at the camp: its trace is refused
    t.set_excluded(STATE, 4, bool(tank and abs(tank[0] - ARGENT_9A_CAMP[0]) < 60 and abs(tank[1] - ARGENT_9A_CAMP[1]) < 60))
    ok = g.goto(*ARGENT_9A_CAMP)
    t.set_excluded(STATE, 4, False)
    log(f"back at the field: {ok} {[mbcoords.read(d) for d in D]}")
    broadcast(True)
    t.write(SPOT_FILE, spot + "\n")
    start_leader(BOX.get(spot))


def close_daily_buff():
    """The "Daily Buff" popup (every day at 18:00 server time) sits over the middle of every
    window and blocks clicks: press its Close button."""
    # ("Daily Buff" is often read as "Da;ly Buff": match the second word too)
    hit = [d for d in D if any(w in t.ocr(t.screenshot(d).crop((560, 140, 800, 172)), 7, 2).lower()
                               for w in ("daily", "buff", "da;ly"))]
    if not hit:
        return
    t.write(HOLD, "0\n")
    pause = os.path.join(STATE, f"pause.{os.getpid()}")
    t.write(pause, "1\n")
    try:
        time.sleep(0.5)
        for d in hit:
            t.click_at(d, O[0] + 672, O[1] + 566)
            time.sleep(0.4)
    finally:
        for f in (pause, HOLD):
            if os.path.exists(f):
                os.remove(f)
    log(f"closed the Daily Buff popup in {len(hit)} window(s)")


def main():
    t.write(os.path.join(STATE, "super.pid"), f"{os.getpid()}\n")
    spot = t.read(SPOT_FILE).strip() or "andes"
    if spot == "moving":                      # a move was cut short: finish it
        spot = "andes"
    last = None
    first = None
    log(f"start: spot {spot}")
    while True:
        try:
            if g.captcha_open():
                time.sleep(10)
                continue
            close_daily_buff()
            dropped = mbreconnect.gone()
            if dropped and t.read(SPOT_FILE).strip() != "moving":
                log(f"window(s) {dropped} dropped by the server: relog, walk back, re-invite")
                stop_leader()
                broadcast(False)
                back = mbreconnect.recover(dropped, ARGENT_9A_CAMP if spot == "argent9a" else mbcoords.read(D[3]))
                subprocess.run([os.path.join(HERE, "multibox"), "camera"], cwd=HERE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                broadcast(True)
                log(f"back after the drop: {back}")
                start_leader(BOX.get(spot))
            lv = mblevels.levels(D[4])
            # frame order on window 5: itself, then party members (the Amis) in party order
            # a misread (26 seen as 28, 2026-10-13) must never trigger a gear stop or a move: nobody
            # gains more than one level, or loses one, between two checks a minute apart
            if None not in lv and last and any(v > o + 1 or v < o for v, o in zip(lv, last)):
                log(f"level reading {lv} doesn't fit the last one {last}: ignored")
                lv = [None]
            if None not in lv and last is None and lv != first:
                first = lv                  # the first reading counts only once it is seen twice
                lv = [None]
            if None not in lv:
                if last and lv != last:
                    log(f"levels {last} -> {lv}")
                    up = [k for k in range(5) if lv[k] > last[k]]
                    # frame 0 is window 5 (the tank); frames 1-4 are the Amis in party order, not
                    # window order: an Ami levelling up checks all four (no points = no clicks)
                    windows = ([4] if 0 in up else []) + ([0, 1, 2, 3] if any(k > 0 for k in up) else [])
                    queue_points(windows)
                last = lv
                milestone = min(lv) // 5 * 5
                if milestone >= 25 and milestone > gear_done():
                    gear_stop(milestone, spot)
                if spot == "argent8a" and min(lv) >= MOVE_AT:
                    walk_to_argent_9a()
                    spot = "argent9a"
                if spot == "andes" and min(lv) >= MOVE_AT:
                    move_to_argent_9a()
                    spot = "argent9a"
                if min(lv) >= 40:
                    log("everyone is level 40")
                    t.write(os.path.join(STATE, "level40"), "1\n")    # the class quests come next (watched by Claude)
            lost = os.path.join(STATE, "leader.lost")
            if os.path.exists(lost):
                # the leader stopped itself far from its field. All four Amis at the Abandon Mine
                # Haven spawn (a wipe, 2026-10-14) -> walk them back; anything else: no restart
                pos = [mbcoords.read(d) for d in D[:4]]
                if spot == "argent9a" and all((t.own_hp(d) or 0) == 0 or (p and abs(p[0] - 1912) < 150 and abs(p[1] - 2802) < 150)
                                            for p, d in zip(pos, D)):
                    walk_back_from_haven(spot)
                    os.remove(lost)
                continue
            if not leader_pid() and t.read(SPOT_FILE).strip() != "moving":
                log("leader not running: restarting it")
                start_leader(BOX.get(spot))
        except Exception as exc:
            log(f"problem: {exc!r}")
        time.sleep(EVERY)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["spend"]:
        spend_points()
    else:
        main()
