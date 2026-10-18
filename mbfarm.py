#!/usr/bin/env python3
"""Solo item farm for story quests: every window hunts the PINK mobs (texture swapped) around its
own spot, on its own (quest items go to whoever kills the mob, so nobody shares kills).
Explorers bolt (F1 + click), the Swordsman (window 5) clicks to auto-attack. Loot: auto-pickup.
Broadcast must be off.

    mbfarm.py MINUTES X0 Y0 X1 Y1 [WINDOW...]     area in tiles; default windows 1-5
"""
import os
import random
import sys
import threading
import time

import numpy as np
from PIL import Image

import mbcoords
import mbquest as q
import mbtarget as t

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "multibox")
FEET = (683, 384)


def log(msg):
    q.say(f"farm: {msg}")


LOOT = os.environ.get("MB_LOOT", "meat,fruit,shell,tail,cashmere,saliva,pearl,stone,tool,necklace").split(",")


def pick_up(d):
    """Drops lie on the ground (no auto-pickup on BPO: 0/5 Bubble Clam Meat after 9 min of kills).
    Walk onto every wanted item label on screen and press the pick-up key."""
    import mbreconnect
    for _ in range(4):
        t.key(d, "keydown", "a", "sleep", "0.1", "keyup", "a")
        time.sleep(0.4)
        try:
            items = [(x, y) for name, x, y in mbreconnect.names_on_screen(t.screenshot(d))
                     if any(k in name.lower() for k in LOOT)]
        except Exception:
            items = []
        if not items:
            return
        x, y = min(items, key=lambda p: (p[0] - FEET[0]) ** 2 + (p[1] - FEET[1]) ** 2)
        t.click_at(d, t.ORIGIN[0] + x, t.ORIGIN[1] + y - 14)   # the item sits just above its label
        time.sleep(2.0)
        t.key(d, "keydown", "a", "sleep", "0.1", "keyup", "a")
        time.sleep(0.5)


QUEST = os.environ.get("MB_QUEST", "Food for the Navy")
WATCH = [int(v) for v in os.environ.get("MB_WATCH", "").split(",") if v.strip()]   # objective line (0, 1, 2...) to finish; -1: time only


def objective_counts(i, d):
    """[(have, need), ...] from the quest log's Objectives box (the selected quest)."""
    import re
    q.alt(d, "q")
    time.sleep(1.5)
    full = t.screenshot(d)
    box = t.find_text(full.crop((480, 200, 730, 560)), [QUEST.split()[0]])   # select the quest
    if box:
        x, y, bw, bh = box
        q.click(d, 480 + x + bw // 2, 200 + y + bh // 2)
        time.sleep(0.8)
        full = t.screenshot(d)
    im = full.crop((900, 455, 995, 520))                          # the "n/m" column only
    q.alt(d, "q")
    a = np.asarray(im.convert("L")).astype(int)
    big = Image.fromarray(np.where(a < 110, 0, 255).astype("uint8")).resize((im.width * 4, im.height * 4))
    txt = t.ocr(big, 6, 2)
    return [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*/\s*(\d+)", txt)]


def hunt(w, area, until):
    i, d = w - 1, f":6{w}"
    x0, y0, x1, y1 = area
    kills, last_move = 0, 0.0
    with q.Pause(STATE, d):
        q.close_all(STATE, i, d)
        last_check = time.time()
        while time.time() < until:
            try:
                if WATCH and time.time() - last_check > 90:
                    last_check = time.time()
                    c = objective_counts(i, d)
                    if c and all(len(c) > k and c[k][0] >= c[k][1] for k in WATCH):
                        log(f"window {w}: objectives {WATCH} done {c}")
                        break
                if (t.own_hp(d) or 0) == 0:
                    log(f"window {w} died: revive, walk back")
                    t.close_popups(STATE, i, d)
                    t.revive(STATE, i, d)
                    t.rest_one(STATE, i, d)
                    t._trace(d, (x0 + x1) // 2, (y0 + y1) // 2)
                    time.sleep(20)
                    continue
                p = mbcoords.read(d)
                mobs = t.pink_mobs(t.screenshot(d))
                if p and not (x0 - 15 <= p[0] <= x1 + 15 and y0 - 15 <= p[1] <= y1 + 15):
                    mobs = []                   # strayed out of the area: go back first
                if not mobs:
                    if time.time() - last_move > 8:
                        t._trace(d, random.randint(x0, x1), random.randint(y0, y1))
                        last_move = time.time()
                    time.sleep(1.5)
                    continue
                mx, my = min(mobs, key=lambda m: (m[0] - FEET[0]) ** 2 + (m[1] - FEET[1]) ** 2)
                X, Y = t.ORIGIN[0] + int(mx), t.ORIGIN[1] + int(my)
                if w == 5:
                    t.key(d, "mousemove", str(X), str(Y), "sleep", "0.1", "mousedown", "1", "sleep", "0.1", "mouseup", "1")
                    time.sleep(4)
                else:
                    t.key(d, "keydown", "F1", "sleep", "0.15", "keyup", "F1")
                    time.sleep(0.4)                 # at 10 fps the skill arms a few frames later
                    t.key(d, "mousemove", str(X), str(Y), "sleep", "0.15", "mousedown", "1", "sleep", "0.15", "mouseup", "1")
                    time.sleep(1.0)
                    t.key(d, "mousedown", "3", "sleep", "0.1", "mouseup", "3")   # drop a still-armed skill
                    time.sleep(3.8)
                kills += 1
                pick_up(d)
            except Exception as exc:
                log(f"window {w}: {exc!r}")
                time.sleep(3)
    log(f"window {w}: {kills} attacks, done at {mbcoords.read(d)}")


def main():
    minutes = float(sys.argv[1])
    area = tuple(int(v) for v in sys.argv[2:6])
    windows = [int(v) for v in sys.argv[6:]] or [1, 2, 3, 4, 5]
    until = time.time() + minutes * 60
    ths = [threading.Thread(target=hunt, args=(w, area, until)) for w in windows]
    for th in ths:
        th.start()
        time.sleep(2)
    for th in ths:
        th.join()


if __name__ == "__main__":
    main()
