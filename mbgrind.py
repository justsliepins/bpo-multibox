#!/usr/bin/env python3
"""Grind supervisor: level the party band by band up to a target level, unattended.

Runs next to KILL (`multibox kill --f1 0`) and owns it:
  * every LEVEL_EVERY s: party levels (mblevels). When the lowest level reaches the band's end,
    KILL stops, everyone walks to the next band's spot, KILL starts there.
  * every BAG_EVERY s: free bag slots in every window. Fewer than BAG_MIN anywhere: KILL stops,
    the party walks to Argent, each window sells its junk (mbquest keep-list), walks back.
  * KILL not running (stopped after two wipes, crashed): restarted after a pause.
  * a captcha (mbcaptchawatch) pauses everything: nothing here clicks while one is open.
Spots come from the server's spawn file (garner/garnerChaSpn.txt): dense mobs at the band's level.

usage: mbgrind.py STATE TARGET_LEVEL DISPLAY...
"""
import os
import re
import subprocess
import sys
import time

import mbjunk
import mblevels
import mbquest as q
import mbtarget as t

HERE = os.path.dirname(os.path.abspath(__file__))
LEVEL_EVERY, BAG_EVERY, BAG_MIN = 300, 7200, 1   # bag checks stop KILL: every 2 h, sell only when a bag is full (a trip costs ~25 min)

# (until level, name, anchor, safespot, patrol, avoid, via). Mobs a few levels above the party:
# four Lightning Bolts at once (~890) one-shot them and the tank holds aggro. All drop NSS.
# via: waypoints walked first (around dangerous fields); "ANDES" = the haven teleport chain to
# Andes Forest Haven and record spawn there (deaths respawn next to the Forest).
BANDS = [
    # passive mobs only (CharacterInfo AI 5); full EXP up to 9 levels above a character
    # (exp_and_level.lua). Four Lv9+ Lightning Bolts one-shot all of these.
    # the SW corner of the Boxeroo spawn, next to Andes Forest Haven: a revived character walks
    # 20-40 tiles back (from the field's middle, lone walks of 100+ tiles died to Killer Shrooms)
    # camp in the spawn's corner ~30 tiles from the Andes respawn: a revived character's walk back
    # is short (lone walks of 100 tiles through Killer Shrooms killed them again and again)
    (29, "Rookie Boxeroo L27 (Forest 4A)", (1050, 2946), (1016, 2976),
     "1050,2946", "Siren|Wolf|Bandit|Porky|Bee", ["ANDES"]),
    (33, "Grassland Elk L31 / White Owlie L32 (Argent 9A)", (1360, 2685), (1420, 2702),
     "1360,2685 1340,2710 1385,2665 1335,2665 1390,2715", "Siren|Wolf|Boxeroo|Shroom", []),
    (41, "Slowpoke Snail L35 (Forest 3B, south)", (1226, 2790), (1300, 2780),
     "1226,2790 1215,2800 1240,2775", "Siren|Wolf|Boxeroo|Beak|Porky", []),
]
TOWN = (2196, 2714)                 # beside Blacksmith Goldie (2193,2706), the quiet shop
TANK = os.environ.get("MB_TANK", "5")          # window of the Swordsman (Taunt on F1, CON)


def log(msg):
    line = time.strftime("%H:%M:%S ") + "grind: " + msg
    print(line, flush=True)
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(line + "\n")


def mb(*args, env=None, timeout=900):
    return subprocess.run([os.path.join(HERE, "multibox"), *args], cwd=HERE, env={**os.environ, **(env or {})},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout).returncode


def kill_running():
    try:
        pid = int(open(os.path.join(STATE, "hunt.pid")).read())
        return b"mbhunt.py" in open(f"/proc/{pid}/cmdline", "rb").read()
    except (OSError, ValueError):
        return False


def captcha_open():
    return any(f.startswith("pause.") and _pid_is(f[6:], b"mbcaptchawatch") for f in os.listdir(STATE))


def _pid_is(pid, name):
    try:
        return name in open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        return False


def start_kill(band):
    """The fast hunter (mbhunt.py: one persistent loop) for the Explorers; the tank runs its own."""
    ensure_tank()
    if kill_running():                          # (a supervisor restart keeps the running hunter)
        return
    _, _, anchor, safe, patrol, _, _ = band
    stand_up()
    subprocess.Popen(["python3", os.path.join(HERE, "mbhunt.py"), STATE, str(safe[0]), str(safe[1]), patrol,
                      *DISPLAYS], cwd=HERE, env={**os.environ, "MB_TANK": TANK, "HUNT_SKIP": "", "HUNT_FIRST": "",   # mob_bars: only mobs anyway
                                                "HUNT_DIAG": "1"},
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(STATE, "hunt.err"), "a"),
                     stdin=subprocess.DEVNULL, start_new_session=True)
    time.sleep(3)


def active():
    """Windows that play: everyone except windows out for recovery (the tank is excluded from
    broadcast only, it still sells, levels and gets checked)."""
    return [i for i in range(len(DISPLAYS)) if i not in t.excluded(STATE) or i == int(TANK) - 1]


class tank_hold:
    """The tank loop does nothing while the supervisor uses the tank window (bag, stats, selling)."""
    def __enter__(self):
        t.write(os.path.join(STATE, "tank-hold"), "1\n")
        time.sleep(1.0)

    def __exit__(self, *a):
        try:
            os.remove(os.path.join(STATE, "tank-hold"))
        except OSError:
            pass


def ensure_tank():
    try:
        pid = int(open(os.path.join(STATE, "tank.pid")).read())
        if b"mbtank.py" in open(f"/proc/{pid}/cmdline", "rb").read():
            return
    except (OSError, ValueError):
        pass
    subprocess.Popen(["python3", os.path.join(HERE, "mbtank.py"), STATE, TANK, *DISPLAYS], cwd=HERE,
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(STATE, "tank.err"), "a"),
                     stdin=subprocess.DEVNULL, start_new_session=True)
    log(f"tank client started in window {TANK}")
    time.sleep(2)


def stop_kill():
    mb("kill-stop")
    try:
        pid = int(open(os.path.join(STATE, "hunt.pid")).read())
        if b"mbhunt.py" in open(f"/proc/{pid}/cmdline", "rb").read():
            # the hunter stops itself between fights: killed mid-fight, a mob kept hitting the
            # idle party and killed the tank
            t.write(os.path.join(STATE, "hunt-stop"), "1\n")
            end = time.time() + 90
            while time.time() < end and os.path.exists(f"/proc/{pid}"):
                time.sleep(0.5)
            if os.path.exists(f"/proc/{pid}"):
                os.kill(pid, 15)
    except (OSError, ValueError):
        pass
    try:
        os.remove(os.path.join(STATE, "hunt-stop"))
    except OSError:
        pass
    time.sleep(2)
    stand_up()
    for d in DISPLAYS:                          # a Lightning Bolt left armed blocks NPC clicks
        if t.cursor_id(d) in t.SKILL_FRAMES:
            t.clear_selection(STATE, d)
    revive_dead()


RELOG = {"last": 0.0, "pending": set()}


def relog_check(band):
    """Every 2 minutes: a client that dropped out (disconnect, crash) is restarted and logged in
    again; once it has walked back to the group the leader invites it into the party."""
    import mbrelog
    if time.time() - RELOG["last"] < 120:
        return
    RELOG["last"] = time.time()
    try:
        for w in mbrelog.check():
            RELOG["pending"].add(w)
        lead = q.mbcoords.read(DISPLAYS[mbrelog.LEADER - 1])
        for w in list(RELOG["pending"]):
            p = q.mbcoords.read(DISPLAYS[w - 1])
            if lead and p and max(abs(p[0] - lead[0]), abs(p[1] - lead[1])) <= 10:
                stop_kill()
                if mbrelog.invite(w):
                    RELOG["pending"].discard(w)
                if band:
                    start_kill(band)
            elif p and band and not kill_running():
                t.trace(DISPLAYS[w - 1], *band[2])
    except Exception as exc:
        log(f"relog problem: {exc!r}")


def revive_dead():
    """Anyone lying dead (a recovery the stopped hunter didn't finish): revive in town now - the
    supervisor's own work (bags, points) takes minutes, nobody may lie dead meanwhile."""
    import numpy as np
    gone = set(t.read(os.path.join(STATE, "gone")).split())
    for i, d in enumerate(DISPLAYS):
        if str(i) in gone or np.asarray(t.screenshot(d).convert("L")).max() < 10:
            continue
        if t.own_hp(d) == 0.0:
            try:
                t.close_popups(STATE, i, d)
                t.revive(STATE, i, d)
                log(f"window {i + 1} was dead: revived in town")
            except Exception as exc:
                log(f"window {i + 1}: revive failed: {exc}")


def stand_up():
    """KILL stopped in the middle of a rest leaves everyone seated: seated characters can't walk,
    attack or cast (the party stood idle next to a mob). The sit state file says which it is."""
    if t.read(os.path.join(STATE, "sit")).strip() == "sitting":
        mb("sit")                               # toggles everyone back up
        time.sleep(1.5)
        log("party was left sitting by a stopped rest: stood up")


def goto(x, y, tries=3):
    """Everyone (not excluded) to x,y; on a refusal try halfway points from window 1's position."""
    for _ in range(tries):
        r = subprocess.run(["python3", "mbtarget.py", "goto", STATE, str(x), str(y), *DISPLAYS], cwd=HERE,
                           env={**os.environ, "MB_STATE": STATE}, capture_output=True, timeout=1500)
        if r.returncode == 0:
            return True
        # retried on the safe path only: a straight-line midpoint walked travellers into a
        # Smuggler pack six times in a row
        time.sleep(5)
    return False


# BPO's real teleporter menus (read in game; they differ from the server files):
# Jovial (Argent): Shaitan, Icicle, Abandon Mine Haven. Meiya: Rockery Haven, Argent.
# Felicia (Rockery): Abandon Mine, Andes Forest Haven. Wendy (Andes): Argent, Rockery, Valhalla.
ANDES_CHAIN = [((2187, 2776), "Argent Teleporter - Jovial", "Teleport: Abandon Mine Haven"),
               ((1912, 2802), "Haven Teleporter - Meiya", "Teleport: Rockery Haven"),
               ((1519, 3076), "Haven Teleporter - Felicia", "Teleport: Andes Forest Haven")]
WENDY = ((1018, 2975), "Haven Teleporter - Wendy")


def _near(p, q_, r):
    return p and abs(p[0] - q_[0]) <= r and abs(p[1] - q_[1]) <= r


ROCKERY = ((1519, 3076), "Haven Teleporter - Felicia")
MEIYA = ((1912, 2802), "Haven Teleporter - Meiya")
HAVENS = {"ANDES": (3, WENDY), "ROCKERY": (2, ROCKERY), "ABANDON": (1, MEIYA)}    # hops on ANDES_CHAIN, spawn NPC


def to_haven(which, record=True):
    """Every window: haven chain up to the haven `which` (from wherever on the chain it is)."""
    hops, (npc_xy, npc_name) = HAVENS[which]
    # not on the haven chain yet (out in a field): the safe walk to its first haven, all together
    here = q.mbcoords.read(DISPLAYS[0])
    on_chain = here and (_near(here, npc_xy, 80) or any(_near(here, xy, 120 if k == 0 else 60)
                                                       for k, (xy, _, _) in enumerate(ANDES_CHAIN[:hops])))
    if not on_chain and _near(here, npc_xy, 250):   # out in this haven's own fields: walk back to it
        t.set_excluded(STATE, int(TANK) - 1, False)
        goto(npc_xy[0] - 4, npc_xy[1] + 4)
        t.set_excluded(STATE, int(TANK) - 1, True)
        on_chain = True
    if not on_chain:
        log("walking to Abandon Mine Haven (start of the haven teleports)")
        t.set_excluded(STATE, int(TANK) - 1, False)
        goto(1904, 2810)   # west of Meiya: tents to the east wedge characters
        t.set_excluded(STATE, int(TANK) - 1, True)
    with tank_hold():
        for i, d in enumerate(DISPLAYS):
            if i not in active():
                continue
            here = q.mbcoords.read(d)
            if which == "ROCKERY" and _near(here, WENDY[0], 80):     # back from Andes: one hop
                q.teleport(STATE, i, d, WENDY[0], WENDY[1], ["Teleport: Rockery Haven"])
            for k, (xy, npc, opt) in enumerate(ANDES_CHAIN[:hops]):
                here = q.mbcoords.read(d)
                if _near(here, npc_xy, 80):
                    break
                if _near(here, xy, 120 if k == 0 else 60) and not (k == 0 and _near(here, ANDES_CHAIN[1][0], 60)):
                    if k == 0 and not _near(here, xy, 20):
                        # from Goldie, Jovial is behind a fence: the talk-walk failed twice for
                        # every window (3 min each, 2026-10-15); round the fence first
                        t._trace(d, 2194, 2758)
                        for _ in range(20):
                            time.sleep(1)
                            if _near(q.mbcoords.read(d), (2194, 2758), 3):
                                break
                    if not q.teleport(STATE, i, d, xy, npc, [opt]):
                        log(f"window {i + 1}: {npc}: teleport failed")
                        break
            here = q.mbcoords.read(d)
            if not _near(here, npc_xy, 80):
                log(f"window {i + 1}: not at {which} haven ({here}); it will walk")
                continue
            done_file = os.path.join(STATE, f"spawn-{which}")
            if record and str(i) not in t.read(done_file).split():
                ok = q.teleport(STATE, i, d, npc_xy, npc_name, ["Record Spawn"], wait=2)
                log(f"window {i + 1}: at {which} haven, spawn {'recorded' if ok else 'NOT recorded'}")
                if ok:
                    t.write(done_file, t.read(done_file) + f" {i}")
                    for other in HAVENS:        # its spawn is HERE now, not at any other haven
                        if other != which:
                            f = os.path.join(STATE, f"spawn-{other}")
                            t.write(f, " ".join(x for x in t.read(f).split() if x != str(i)) + "\n")


def to_andes(record=True):
    """Every window: haven chain to Andes Forest Haven (from wherever on it it is), record spawn."""
    with tank_hold():
        for i, d in enumerate(DISPLAYS):
            if i not in active():
                continue
            for k, (xy, npc, opt) in enumerate(ANDES_CHAIN):
                here = q.mbcoords.read(d)
                if _near(here, WENDY[0], 80):
                    break                       # arrived
                # this hop applies if we stand in its town/haven (Argent is big: 120 tiles)
                if _near(here, xy, 120 if k == 0 else 60) and not (k == 0 and _near(here, ANDES_CHAIN[1][0], 60)):
                    if not q.teleport(STATE, i, d, xy, npc, [opt]):
                        log(f"window {i + 1}: {npc}: teleport failed")
                        break
            here = q.mbcoords.read(d)
            if not _near(here, WENDY[0], 80):
                log(f"window {i + 1}: not in Andes Forest Haven ({here}); it will walk")
                continue
            if record:
                q.teleport(STATE, i, d, WENDY[0], WENDY[1], ["Record Spawn"], wait=2)
                log(f"window {i + 1}: in Andes Forest Haven, spawn recorded")


def to_argent():
    """From the Forest: Wendy teleports straight to Argent (one hop)."""
    with tank_hold():
        for i, d in enumerate(DISPLAYS):
            if i in active() and not _near(q.mbcoords.read(d), TOWN, 150):
                if not q.teleport(STATE, i, d, WENDY[0], WENDY[1], ["Teleport: Argent City"]):
                    log(f"window {i + 1}: Wendy's teleport to Argent failed; it will walk")


def travel(band):
    for step in band[6]:
        if step in HAVENS:
            to_haven(step)
        else:
            goto(*step)


def set_spot(band):
    _, name, anchor, safe, _, _, _ = band
    t.write(os.path.join(STATE, "anchor"), f"{anchor[0]} {anchor[1]} 20\n")
    t.write(os.path.join(STATE, "safespot"), f"{safe[0]} {safe[1]}\n")


def free_slots(i, d):
    """Free unlocked bag cells (empty cells are flat beige: low pixel spread)."""
    with q.Pause(STATE, d):
        q.close_all(STATE, i, d)
        q.alt(d, "e")
        time.sleep(1.2)
        im = t.screenshot(d)
        p = t.find_image(im, "mbinvtitle.png")
        q.alt(d, "e")
        time.sleep(0.4)
    if not p:
        return None
    states = [q.slot_state(im, p[0] - 9 + 35 * c, p[1] + 78 + 35 * r) for r in range(6) for c in range(8)]
    return states.count("empty")


STAT_PLUS = {"con": (102, 383), "spr": (217, 383)}    # "+" buttons in the Character window (Alt+A)
STATS_PTS = (55, 565, 92, 581)                          # "Stats Pts: N" value


def stat_points(d):
    s = t.ocr(t.screenshot(d).crop(STATS_PTS), 7, 4, "0123456789").strip()
    return int(s) if s.isdigit() else None


SKILL_PLUS = (567, 242)              # "+" on the first skill row (Lightning Bolt) in Alt+S
SKILL_PTS = (538, 535, 580, 553)


def allocate_skills(i, d):
    """Explorers: every free skill point into Lightning Bolt (the user's OK, 2026-10-10)."""
    if i == int(TANK) - 1:
        return
    spent = 0
    with q.Pause(STATE, d):
        q.close_all(STATE, i, d)
        q.alt(d, "s")
        time.sleep(1.2)
        for _ in range(30):
            s = t.ocr(t.screenshot(d).crop(SKILL_PTS), 7, 4, "0123456789").strip()
            if not s.isdigit() or int(s) == 0:
                break
            q.click(d, *SKILL_PLUS)
            time.sleep(0.8)
            s2 = t.ocr(t.screenshot(d).crop(SKILL_PTS), 7, 4, "0123456789").strip()
            if s2 == s:                         # maxed (or the click didn't take): stop
                break
            spent += 1
        q.alt(d, "s")
        time.sleep(0.4)
    if spent:
        log(f"window {i + 1}: {spent} skill point(s) into Lightning Bolt")


def char_level(lv, i):
    """mblevels reads window 5's screen: its own frame first, then the four party frames in
    party order (LuckyForth, LuckyThird, LuckySecond, LuckyOne = windows 3, 4, 2, 1)."""
    order = [4, 2, 3, 1, 0]
    try:
        return lv[order.index(i)]
    except (ValueError, IndexError):
        return min(lv)


def allocate_stats(i, d):
    """Spend every free stat point: the tank all into CON, the Explorers all into SPR."""
    which = "con" if i == int(TANK) - 1 else "spr"
    with q.Pause(STATE, d):
        q.close_all(STATE, i, d)
        q.alt(d, "a")
        time.sleep(1.2)
        n = stat_points(d)
        spent = 0
        while n and spent < 60:
            q.click(d, *STAT_PLUS[which])
            time.sleep(0.5)
            left = stat_points(d)
            if left is None or left >= n:       # the click didn't take: stop, try next time
                break
            spent += n - left
            n = left
        q.alt(d, "a")
        time.sleep(0.4)
    if spent:
        log(f"window {i + 1}: {spent} stat point(s) into {which.upper()}")


def _req(tip, pat):
    m = re.search(pat, tip.replace("\n", " "), re.I)
    return int(m.group(1)) if m else None


def upgrade_gear(i, d, level):
    """Open chests that this level unlocks and wear gear made for this level band.
    Gear: double-click (dragging can drop it on the ground). Only items needing level-4..level:
    an old low-level item in the bag must never replace better gear."""
    O = t.ORIGIN
    done = []
    with q.Pause(STATE, d):
        q.close_all(STATE, i, d)
        q.alt(d, "e")
        time.sleep(1.2)
        p = t.find_image(t.screenshot(d), "mbinvtitle.png")
        if not p:
            return
        if p[0] != 342:                         # standard place: the cell grid below assumes it
            q.drag(d, p[0], p[1], 342, 110, steps=20)
            time.sleep(0.6)
            p = (342, 110)
        for _ in range(8):                      # a chest adds items: scan again after opening one
            # (3 rounds ended right after a chest was opened: its gear stayed in the bag)
            im = t.screenshot(d)
            acted = False
            for r in range(6):
                for c in range(8):
                    x, y = p[0] - 9 + 35 * c, p[1] + 78 + 35 * r
                    if q.slot_state(im, x, y) != "item":
                        continue
                    tip = q.item_tooltip(d, x, y)
                    if "chest" in tip.lower():
                        need = _req(tip, r"Level\s*(\d+)")
                        if need and need <= level:
                            t.move(d, O[0] + x, O[1] + y)
                            time.sleep(0.3)
                            t.key(d, "mousedown", "3", "sleep", "0.1", "mouseup", "3")
                            time.sleep(0.9)
                            q.click(d, x + 18, y + 11)          # "Use"
                            time.sleep(2.0)
                            done.append(f"opened a chest (Lv{need})")
                            acted = True
                            break
                        continue
                    need = _req(tip, r"Level Requirement:?\s*(\d+)")
                    if need and level - 4 <= need <= level:
                        t.move(d, O[0] + x, O[1] + y)
                        time.sleep(0.3)
                        t.key(d, "mousedown", "1", "sleep", "0.06", "mouseup", "1", "sleep", "0.06",
                              "mousedown", "1", "sleep", "0.06", "mouseup", "1")
                        time.sleep(1.2)
                        if q.slot_state(t.screenshot(d), x, y) != "item" or \
                                q.item_tooltip(d, x, y) != tip:
                            done.append(f"equipped a Lv{need} item")
                            acted = True
                            break
                if acted:
                    break
            if not acted:
                break
        t.move(d, O[0] + 1100, O[1] + 650)
        q.alt(d, "e")
        time.sleep(0.4)
    if done:
        log(f"window {i + 1}: {', '.join(done)}")


def band_mobs(band):
    return re.findall(r"([A-Z][A-Za-z' ]+?) L\d+", band[1])


def purge_junk(i, d, band):
    """Bag full: delete (in place, no town trip) every item on this band's junk list."""
    names = mbjunk.junk_names(band_mobs(band))
    gone = 0
    with q.Pause(STATE, d):
        q.close_all(STATE, i, d)
        q.alt(d, "e")
        time.sleep(1.2)
        p = t.find_image(t.screenshot(d), "mbinvtitle.png")
        if not p:
            return 0
        if p[0] != 342:
            q.drag(d, p[0], p[1], 342, 110, steps=20)
            time.sleep(0.6)
            p = (342, 110)
        for r in range(6):
            for c in range(8):
                x, y = p[0] - 9 + 35 * c, p[1] + 78 + 35 * r
                if q.slot_state(t.screenshot(d), x, y) != "item":
                    continue
                tip = q.item_tooltip(d, x, y)
                if mbjunk.is_junk(tip, names) and q.delete_item(d, x, y):
                    gone += 1
        t.move(d, t.ORIGIN[0] + 1100, t.ORIGIN[1] + 650)
        q.alt(d, "e")
        time.sleep(0.4)
    log(f"window {i + 1}: bag full: deleted {gone} junk item(s) in place")
    return gone


def tank_sell_trip(band):
    """Only the tank is full (it fetches all the loot): it walks to Argent alone, sells, walks
    back, while the Explorers keep killing. Its loop is held meanwhile."""
    i = int(TANK) - 1
    d = DISPLAYS[i]
    log("tank's bag is full: it sells alone, the Explorers keep hunting")
    with tank_hold():
        forest = "ANDES" in band[6]
        if forest:
            q.teleport(STATE, i, d, WENDY[0], WENDY[1], ["Teleport: Argent City"])
        tank_goto(*TOWN)
        try:
            os.environ["MB_LEVEL"] = str(LEVELS.get(i, 0))
            log(f"window {i + 1}: sold {q.sell_everything(STATE, i, d)}")
        except Exception as exc:
            log(f"window {i + 1}: selling failed: {exc}")
        f = free_slots(i, d)
        if f is not None and f < BAG_MIN:
            STUCK.add(i + 1)
            log(f"window {i + 1}: bag still full after selling ({f} free): it won't trigger sell trips any more")
        if forest:
            for xy, npc, opt in ANDES_CHAIN:
                if not q.teleport(STATE, i, d, xy, npc, [opt]):
                    break
        tank_goto(*band[2])


def sell_trip(band):
    with tank_hold():
        _sell_trip(band)


def tank_goto(x, y):
    """The tank is out of broadcast (excluded): walk it on its own (a one-window goto)."""
    subprocess.run(["python3", "mbtarget.py", "goto", STATE, str(x), str(y), DISPLAYS[int(TANK) - 1]],
                   cwd=HERE, env={**os.environ, "MB_STATE": STATE, "MB_STATUS_FILE": "tank-status"},
                   capture_output=True, timeout=1500)


LEVELS = {}        # window index -> current level (for "next-phase gear" when selling)
STUCK = {int(w) for w in os.environ.get("MB_STUCK", "").split(",") if w}   # windows whose bag stayed full after selling (kept items only)


def _after_sell(sold):
    """Re-count free slots right after selling; remember the bags that stay full (only those
    whose selling worked: a failed visit to the shop says nothing about the bag)."""
    frees = {i + 1: free_slots(i, d) for i, d in enumerate(DISPLAYS) if i in active()}
    for w, f in frees.items():
        if f is not None and f < BAG_MIN and sold.get(w, -1) >= 0:
            if w not in STUCK:
                log(f"window {w}: bag still full after selling ({f} free): only kept items left; "
                    f"it won't trigger sell trips any more")
            STUCK.add(w)
        else:
            STUCK.discard(w)


# the nearest shop on a safe road per area: Accessory - Pucan in Abandon Mine Haven sells/buys
# like any shop and is ~400 tiles from the Argent 8A/9A fields (Argent itself ~720)
NEAR_SHOP = {"Argent 8A": (("Accessory - Pucan", (1889, 2804)), (1895, 2810)),
             # Andes Forest Haven's own shop (~(998, 2974), estimated from the screen): a walk
             # from the Boxeroo field, no teleports
             "Forest 4A": (("Accessory - Rikka", (998, 2974)), (1004, 2972))}


def _sell_trip(band):
    sold = {}
    shop, park = next(((v[0], v[1]) for k, v in NEAR_SHOP.items() if k in band[1]),
                      (("Blacksmith - Goldie", (2193, 2706)), TOWN))
    log(f"bags nearly full: selling at {shop[0]}")
    stop_kill()
    forest = "ANDES" in band[6] and not any(k in band[1] for k in NEAR_SHOP)
    if forest:
        to_argent()                             # (Rockery bands walk: it's near Argent)
    # the tank walks with the others (one goto for all five, in parallel), not before them
    t.set_excluded(STATE, int(TANK) - 1, False)
    ok = goto(*park)
    t.set_excluded(STATE, int(TANK) - 1, True)
    if not ok:
        log(f"couldn't reach {shop[0]} for selling; back to grinding")
        return
    for i, d in enumerate(DISPLAYS):
        if i not in active():                   # (the tank is out of broadcast but sells too)
            continue
        try:
            os.environ["MB_LEVEL"] = str(LEVELS.get(i, 0))     # gear above this level is kept
            n = q.sell_everything(STATE, i, d, shop=shop[0], shop_xy=shop[1])
            sold[i + 1] = n
            log(f"window {i + 1}: sold {n}")
        except Exception as exc:                # one window failing must not stop the others
            log(f"window {i + 1}: selling failed: {exc}")
    _after_sell(sold)
    if forest:
        to_andes(record=False)
    # (back to the camp: the hunter walks everyone there, defended)


def main(argv):
    global STATE, DISPLAYS
    STATE, target, DISPLAYS = argv[1], int(argv[2]), argv[3:]
    os.environ["MB_STATE"] = STATE
    os.environ["MB_F1_AT_CLICK"] = "1"          # Explorers: revived travellers rest SP too
    t.write(os.path.join(STATE, "grind.pid"), f"{os.getpid()}\n")
    band, last_bag, dead_since, last_lv = None, time.time() - BAG_EVERY + float(os.environ.get("BAG_FIRST", BAG_EVERY)), None, None
    while True:
        if captcha_open():
            time.sleep(10)
            continue
        raw = mblevels.levels(DISPLAYS[-1])
        # unreadable frames keep their last known level (a misread must not move the party)
        lv = [v if v is not None else (last_lv[k] if last_lv and k < len(last_lv) else None)
              for k, v in enumerate(raw)]
        if None in lv:
            known = [v for v in lv if v is not None]
            # some frames unreadable (covered for a moment): never sit idle over it - use the
            # readable ones for the band choice, keep the last full list for per-window logic
            lv = last_lv if last_lv else ([min(known)] * len(lv) if known else None)
        if lv and lv != last_lv:
            if last_lv:                         # someone levelled up: spend the new stat points
                stop_kill()
                # in the haven's safe zone: the party stood idle in the field for minutes while
                # points and chests were done, and the mobs killed three Amis
                if band:
                    t.set_excluded(STATE, int(TANK) - 1, False)
                    goto(*band[3])
                    t.set_excluded(STATE, int(TANK) - 1, True)
                with tank_hold():
                    for i, d in enumerate(DISPLAYS):
                        if i in active():
                            allocate_stats(i, d)
                            allocate_skills(i, d)
                    for i, d in enumerate(DISPLAYS):
                        if i in active():
                            try:
                                upgrade_gear(i, d, char_level(lv, i))
                            except Exception as exc:
                                log(f"window {i + 1}: gear upgrade failed: {exc}")
                if band:
                    start_kill(band)
            last_lv = lv
        if lv:
            t.write(os.path.join(STATE, "party-level"), f"{min(lv)}\n")   # mbpath's danger costs
            LEVELS.update({i: char_level(lv, i) for i in range(len(DISPLAYS))})
            low = min(lv)
            if low >= target:
                log(f"everyone is level {low}+ (target {target}): done")
                stop_kill()
                return
            want = next(b for b in BANDS if low < b[0])
            if want is not band:
                log(f"levels {lv}: hunting {want[1]} until level {want[0]}")
                here = q.mbcoords.read(DISPLAYS[0])
                if band is None and kill_running() and _near(here, want[2], 150):
                    log("supervisor restart: the hunter keeps going")
                else:
                    stop_kill()
                    set_spot(want)
                    if _near(here, want[2], 150):
                        log("already at the hunting ground: no haven trip")
                    else:
                        travel(want)
                    # no undefended group walk to the camp (it killed travellers): the hunter
                    # walks everyone there itself, its defenders answering anything on the way
                band = want
                start_kill(band)
        if band and time.time() - last_bag > BAG_EVERY:
            last_bag = time.time()
            stop_kill()
            with tank_hold():
                frees = {i + 1: free_slots(i, d) for i, d in enumerate(DISPLAYS) if i in active()}
            log(f"free bag slots: {frees}")
            # a bag still nearly full right after a sell trip holds only kept items: selling
            # again won't help, so it no longer triggers trips (its loot goes to the others)
            # full bag: delete the band's junk in place first (no town trip); sell only if that
            # didn't free enough
            for w, f in list(frees.items()):
                if f is not None and f < BAG_MIN:
                    with tank_hold():
                        purge_junk(w - 1, DISPLAYS[w - 1], band)
                    frees[w] = free_slots(w - 1, DISPLAYS[w - 1])
            full = [w for w, f in frees.items() if f is not None and f < BAG_MIN and w not in STUCK]
            if not full and any(f is not None and f < BAG_MIN for f in frees.values()):
                pass                            # only "stuck" bags are full: hunt on
            if full:                            # never leave the Explorers without the tank
                sell_trip(band)
                start_kill(band)
            else:
                start_kill(band)
        relog_check(band)
        if band and not kill_running():
            dead_since = dead_since or time.time()
            if time.time() - dead_since > 20:
                log("KILL isn't running: restarting it")
                start_kill(band)
                dead_since = None
        else:
            dead_since = None
        # short sleeps: a dead hunter is restarted within ~3 s (the party stands unprotected
        # in an aggressive field without it)
        for _ in range(int(LEVEL_EVERY // 2)):
            time.sleep(2)
            if band and not kill_running():
                time.sleep(1)
                if not kill_running():
                    log("hunter gone: restarting it now")
                    start_kill(band)


if __name__ == "__main__":
    main(sys.argv)
