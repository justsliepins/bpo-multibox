#!/usr/bin/env python3
"""Icespire Haven -> Argent City: Artemis (Teleport: Icicle Castle), then Icicle Teleporter Helen
(Teleport: Argent City). Helen is behind a fence: talk to her from the north side (1323,540).

usage: mbtoargent.py WINDOW [WINDOW...]   (1..5)
"""
import sys
import time

import mbcoords
import mbquest as q
import mbtarget as t
import mbvoyager as v

ARTEMIS = (2696, 638)
HELEN = (1323, 544)
HELEN_SIDE = (1323, 540)


def to_argent(w):
    i, d = w - 1, t.DISPLAYS[w - 1] if hasattr(t, "DISPLAYS") else [":61", ":62", ":63", ":64", ":65"][w - 1]
    p = mbcoords.read(d)
    if p and p[0] > 2000 and p[1] > 2500:
        return True                                  # already in Argent
    if not (p and 1250 < p[0] < 1450 and 450 < p[1] < 650):
        if not q.teleport(v.STATE, i, d, ARTEMIS, "Artemis", ["Teleport: Icicle Castle"]):
            print(f"window {w}: Artemis failed", flush=True)
            return False
        time.sleep(3)
    t.trace(d, *HELEN_SIDE)
    for _ in range(30):
        time.sleep(1)
        if mbcoords.read(d) == HELEN_SIDE:
            break
    for _ in range(3):
        title = q.talk(v.STATE, d, expect="Helen", npc_xy=HELEN)
        if title:
            q.choose_exact(d, title, "Teleport: Argent City")
            for _ in range(15):
                time.sleep(1)
                p = mbcoords.read(d)
                if p and p[0] > 2000 and p[1] > 2500:
                    print(f"window {w}: in Argent at {p}", flush=True)
                    return True
        q.close_all(v.STATE, i, d)
        t.trace(d, *HELEN_SIDE)
        time.sleep(3)
    print(f"window {w}: Helen failed", flush=True)
    return False


if __name__ == "__main__":
    for w in map(int, sys.argv[1:]):
        to_argent(w)
