#!/usr/bin/env python3
"""Repair everything a character wears at Blacksmith - Goldie (Argent, 2193,2706). Stand next to
him first. His "Repair" option opens the inventory in repair mode: a click on an item asks
"Repair: <item> Fees: 1G Confirm?"; the mode stays on for the next item.

Each death costs 5% durability; at 0/200 a weapon no longer casts skills (2026-10-12: all five
weapons were at 0 and the party hunted an hour without one Lightning Bolt).

usage: mbrepair.py DISPLAY...
"""
import os
import sys
import time

import mbquest as q
import mbtarget as t

STATE = os.environ.get("MB_STATE", os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "multibox"))
GOLDIE = (2193, 2706)
SLOTS = [(x, 243) for x in (562, 603, 643, 683, 724, 765, 806)] + \
        [(x, 517) for x in (552, 591, 631, 733, 773, 817)]      # worn items around the bag
ASK = (575, 340, 795, 400)        # "Repair: <item> / Fees: 1G / Confirm?"
CONFIRM = (700, 420)


def asking(d):
    return "repair" in t.ocr(t.screenshot(d).crop(ASK), 6, 2).lower()


def leave_repair_mode(d):
    """Repair mode stays on after the inventory closes: no click walks or talks until a
    right-click drops it."""
    t.move(d, t.ORIGIN[0] + 950, t.ORIGIN[1] + 620)
    time.sleep(0.3)
    t.key(d, "mousedown", "3", "sleep", "0.08", "mouseup", "3")
    time.sleep(0.5)


def repair(i, d):
    done = []
    with q.Pause(STATE, d):
        if not asking(d):
            q.close_all(STATE, i, d)
            title = q.talk(STATE, d, expect="Blacksmith - Goldie", npc_xy=GOLDIE)
            if not title:
                q.say(f"window {i + 1}: couldn't talk to Blacksmith - Goldie")
                return None
            q.click(d, title[0] - 82, title[1] + 225)           # "Repair" (third option)
            time.sleep(1.8)
        # slots relative to where the inventory window actually is: the tank's opens at
        # (342,110), not at the Amis' (569,210), and the fixed points missed every item
        inv = t.find_image(t.screenshot(d), "mbinvtitle.png") or (569, 210)
        slots = [(inv[0] + dx, inv[1] + 33) for dx in (-7, 34, 74, 114, 155, 196, 237)] + \
                [(inv[0] + dx, inv[1] + 307) for dx in (-17, 22, 62, 164, 204, 248)]
        for n, (x, y) in enumerate(slots):
            if not asking(d):
                q.click(d, x, y)
                time.sleep(0.5)
                # off the item: its tooltip covers the question (and was read instead of it)
                t.move(d, t.ORIGIN[0] + 300, t.ORIGIN[1] + 450)
                time.sleep(0.9)
            if asking(d):
                name = t.ocr(t.screenshot(d).crop(ASK), 6, 2).strip().splitlines()[0][:40]
                q.click(d, *CONFIRM)
                for _ in range(8):              # the question fades slowly: seen as "still asking",
                    time.sleep(0.5)             # the next slot was skipped (a weapon stayed at 0)
                    if not asking(d):
                        break
                done.append(name)
        t.move(d, t.ORIGIN[0] + 300, t.ORIGIN[1] + 450)
        time.sleep(0.3)
        # weapon check: the tooltip must say 200/200 now
        t.move(d, t.ORIGIN[0] + inv[0] + 74, t.ORIGIN[1] + inv[1] + 33)
        time.sleep(1.1)
        im = t.screenshot(d)
        im.crop((480, 150, 1100, 720)).save(os.path.join(STATE, f"repair-w{i + 1}.png"))
        tip = " ".join(t.ocr(im.crop((640, 230, 1100, 330)), 6, 2).split())
        t.move(d, t.ORIGIN[0] + 300, t.ORIGIN[1] + 450)
        q.close_all(STATE, i, d)
        leave_repair_mode(d)
        t._trace(d, 2197 + 2 * i, 2715)        # off the blacksmith: characters on top of him take the clicks
        time.sleep(2.5)
    q.say(f"window {i + 1}: repaired {len(done)} item(s); weapon: {tip[:70]}")
    return done


if __name__ == "__main__":
    for d in sys.argv[1:]:
        i = int(d[-1]) - 1
        print(d, repair(i, d), flush=True)
