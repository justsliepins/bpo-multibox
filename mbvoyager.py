#!/usr/bin/env python3
"""Explorer -> Voyager (Lv40) for one window, step by step. Broadcast must be off.

    mbvoyager.py WINDOW [STEP...]      steps: accept zephyr glacier outlaw finish (default: all)

BPO facts (2026-10-17): Little Daniel's dialog hides "Journey of the Voyager" below the fold (his
greeting is long): scroll the dialog. On BPO, Harbor Operator Whitney stands on Outlaw Isle
(3620,743), so no ship is needed. Island teleports are free up to Lv40.
"""
import io
import os
import subprocess
import sys
import time

import mbcoords
import mbquest as q
import mbtarget as t

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "multibox")
QUEST = "Journey of the Voyager"
SILVIUS = ((2248, 2853), "Island Teleporter - Silvius")
ANDREA = ((3266, 3264), "Island Teleporter - Andrea")
ARSENE = ((2272, 1114), "Island Teleporter - Arsene")
SHAYALA = ((3611, 728), "Island Teleporter - Shayala")


def log(msg):
    q.say(f"voyager: {msg}")


def talk_to(i, d, name, xy):
    q.close_all(STATE, i, d)
    t.trace(d, xy[0] + 2, xy[1] + 2)
    q.walk_to(STATE, d, xy[0], xy[1], near=4, limit=90)
    return q.talk(STATE, d, expect=name, npc_xy=xy)


def pick(d, title, text):
    """Click the dialog line containing text; scrolls the dialog down when it is below the fold."""
    for scroll in (0, 10, 10):
        if scroll:
            t.move(d, t.ORIGIN[0] + title[0], t.ORIGIN[1] + title[1] + 220)
            time.sleep(0.2)
            t.key(d, *(["click", "5"] * scroll))
            time.sleep(0.6)
        if q.choose(d, title, [text]):
            return True
    return False


def button(d, words=("Complete", "Accept")):
    """Click the quest panel's Complete/Accept button (found by OCR, else its usual spot)."""
    box = (240, 500, 520, 560)
    crop = t.screenshot(d).crop(box).convert("L")
    crop = crop.resize((crop.width * 3, crop.height * 3))
    b = io.BytesIO()
    crop.save(b, format="PNG")
    out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=b.getvalue(),
                         capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and any(f[11].strip().lower().startswith(w.lower()[:5]) for w in words):
            x = box[0] + (int(f[6]) + int(f[8]) / 2) / 3
            y = box[1] + (int(f[7]) + int(f[9]) / 2) / 3
            q.click(d, int(x), int(y))
            return f[11]
    q.click(d, 346, 528)
    return "default spot"


def quest_step(i, d, name, xy):
    title = talk_to(i, d, name, xy)
    if not title:
        log(f"window {i + 1}: couldn't talk to {name} at {mbcoords.read(d)}")
        return False
    if not pick(d, title, QUEST):
        txt = q.dialog_text(d, title)[0].replace("\n", " | ")
        log(f"window {i + 1}: {name} has no '{QUEST}' line: {txt[:160]}")
        q.close_all(STATE, i, d)
        return False
    time.sleep(1.2)
    log(f"window {i + 1}: {name}: clicked {button(d)}")
    time.sleep(1.5)
    q.close_all(STATE, i, d)
    return True


def hop(i, d, tp, option):
    for _ in range(2):
        if q.teleport(STATE, i, d, tp[0], tp[1], [option]):
            return True
    log(f"window {i + 1}: {tp[1]}: {option} failed")
    return False


SINBAD = ((2255, 2801), "Builder - Sinbad")
SHIRLEY = ((2243, 2826), "Harbor Operator - Shirley")
# open water only (checked against garner.atr), between the shark spawns 11A (x<=3038) and
# 11B (x>=3238) south of y 3344; Whitney's island spans x 3072-3159, y 3474-3551
TO_WHITNEY = [(2300, 2860), (2700, 2860), (3070, 3300), (3060, 3560), (3100, 3557)]
FROM_WHITNEY = [(3060, 3560), (3070, 3300), (2700, 2860), (2300, 2860), (2262, 2833)]


def build_ship(i, d, name):
    """Sinbad: Build Ship > Build Guppy, name it, Confirm (9,990G). Stand beside Sinbad first."""
    title = q.talk(STATE, d, expect=SINBAD[1], npc_xy=SINBAD[0])
    if not title or not q.choose_exact(d, title, "Build Ship", need=0.7):
        return False
    time.sleep(1.2)
    if not q.choose_exact(d, title, "Build Guppy", need=0.7):
        return False
    time.sleep(1.5)
    q.click(d, 400, 424)                         # the Name field
    time.sleep(0.5)
    t.key(d, "type", "--delay", "120", name)
    time.sleep(0.6)
    q.click(d, 420, 556)                         # Confirm
    time.sleep(2)
    log(f"window {i + 1}: built ship {name}")
    return True


def set_sail(i, d):
    """Shirley: Set sail, pick the first ship in the list; True once on the water."""
    title = q.talk(STATE, d, expect=SHIRLEY[1], npc_xy=SHIRLEY[0])
    if not title or not q.choose_exact(d, title, "Set sail", need=0.7):
        return False
    time.sleep(2)
    q.click(d, 653, 171)                         # the first ship in "Ship Dialogue"
    time.sleep(4)
    p = mbcoords.read(d)
    return bool(p and abs(p[0] - 2260) <= 6 and abs(p[1] - 2829) <= 6)


def sail(d, route):
    for wx, wy in route:
        t.trace(d, wx, wy)
        last, still, t0 = None, 0, time.time()
        while time.time() - t0 < 360:
            time.sleep(3)
            p = mbcoords.read(d)
            if p and max(abs(p[0] - wx), abs(p[1] - wy)) <= 4:
                break
            still = still + 1 if p == last else 0
            last = p
            if still >= 4:
                t.trace(d, wx, wy)
                still = 0


def run(w, steps):
    i, d = w - 1, f":6{w}"
    with q.Pause(STATE, d):
        q.close_all(STATE, i, d)
        q.chat_off(d)
        if "accept" in steps:
            quest_step(i, d, "Little Daniel", (2193, 2730))
        if "zephyr" in steps:
            if hop(i, d, SILVIUS, "Teleport: Zephyr Isle"):
                quest_step(i, d, "Trader - Burgess", (3254, 3278))
        if "glacier" in steps:
            if hop(i, d, ANDREA, "Teleport: Glacier Isle"):
                quest_step(i, d, "Trader - Sacenis", (2279, 1112))
        if "outlaw" in steps:
            if hop(i, d, ARSENE, "Teleport: Outlaw Isle"):
                quest_step(i, d, "Trader - Dilady", (3595, 739))
        if "ship" in steps:                      # Argent, build a Guppy, set sail, sail to Whitney
            import mbreconnect
            if mbcoords.read(d) and mbcoords.read(d)[1] < 2000:
                hop(i, d, SHAYALA, "Teleport: Argent City")
            mbreconnect.walk([w], (2257, 2805))
            build_ship(i, d, f"Voyage{w}")
            t.trace(d, 2243, 2806)
            q.walk_to(STATE, d, 2243, 2806, near=3, limit=60)
            t.trace(d, 2244, 2823)
            q.walk_to(STATE, d, 2244, 2823, near=3, limit=60)
            if set_sail(i, d):
                sail(d, TO_WHITNEY)
                log(f"window {w}: at Whitney's island {mbcoords.read(d)}: click Whitney")
            else:
                log(f"window {w}: could not set sail at {mbcoords.read(d)}")
        if "back" in steps:                      # Whitney done: sail home, then Daniel
            sail(d, FROM_WHITNEY)
            import mbreconnect
            mbreconnect.walk([w], (2195, 2733))
            quest_step(i, d, "Little Daniel", (2193, 2730))
        if "finish" in steps:
            if hop(i, d, SHAYALA, "Teleport: Argent City"):
                quest_step(i, d, "Little Daniel", (2193, 2730))
    log(f"window {w}: done ({' '.join(steps)}) at {mbcoords.read(d)}")


if __name__ == "__main__":
    run(int(sys.argv[1]), sys.argv[2:] or ["accept", "zephyr", "glacier", "outlaw", "finish"])
