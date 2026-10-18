#!/usr/bin/env python3
"""Argent City -> Icespire Haven by teleporters only (every step in a town or haven):
Jovial (Icicle Castle) -> Helen (Icicle Haven) -> Lily (Atlantis Haven) -> Sofia (Skeleton Haven)
-> Mina (Icespire Haven). Each hop costs 500 gold from Lv41. Helen stands behind a fence: talk to
her from the north side (1323,540). Steps already passed are skipped, so it also works from Icicle
Castle or any haven on the way. Tested 2026-10-18 with all four Voyagers.

usage: mbtoicespire.py WINDOW...      (1..5)
"""
import sys
import time

import mbcoords
import mbquest as q
import mbtarget as t
import mbvoyager as v

D = [":61", ":62", ":63", ":64", ":65"]
CHAIN = [("Jovial", (2187, 2776), "Teleport: Icicle Castle", lambda p: 1250 < p[0] < 1450 and 400 < p[1] < 650),
         ("Helen", (1323, 544), "Teleport: Icicle Haven", lambda p: 700 < p[0] < 950 and 250 < p[1] < 450),
         ("Lily", (818, 354), "Teleport: Atlantis Haven", lambda p: 950 < p[0] < 1150 and 550 < p[1] < 750),
         ("Sofia", (1046, 639), "Teleport: Skeleton Haven", lambda p: 2050 < p[0] < 2200 and 480 < p[1] < 620),
         ("Mina", (2116, 551), "Teleport: Icespire Haven", lambda p: 2650 < p[0] < 2710 and 620 < p[1] < 670)]


def at(d, test):
    p = mbcoords.read(d)
    return bool(p) and test(p)


def go(w):
    i, d = w - 1, D[w - 1]
    for k, (npc, xy, opt, arrived) in enumerate(CHAIN):
        if any(at(d, c[3]) for c in CHAIN[k:]):
            continue
        side = (1323, 540) if npc == "Helen" else (xy[0] + 3, xy[1] + 3)
        t.trace(d, *side)
        for _ in range(60):
            time.sleep(1)
            p = mbcoords.read(d)
            if p and abs(p[0] - xy[0]) <= 6 and abs(p[1] - xy[1]) <= 6:
                break
        ok = False
        for _ in range(3):
            title = q.talk(v.STATE, d, expect=npc, npc_xy=xy)
            if title:
                q.choose_exact(d, title, opt)
                for _ in range(15):
                    time.sleep(1)
                    if at(d, arrived):
                        ok = True
                        break
            if ok:
                break
            q.close_all(v.STATE, i, d)
            t.trace(d, *side)
            time.sleep(3)
        print(f"window {w}: {npc} -> {opt}: {'ok' if ok else 'FAILED'} at {mbcoords.read(d)}", flush=True)
        if not ok:
            return False
    return True


if __name__ == "__main__":
    for w in map(int, sys.argv[1:]):
        print(f"window {w}: {'AT ICESPIRE' if go(w) else 'STOPPED'}", flush=True)
