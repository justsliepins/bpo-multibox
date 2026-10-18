#!/usr/bin/env python3
"""Track how far each follower stands from window 1 and publish click offsets for mbcast.

    mbtrack.py STATE_DIR DISPLAY [DISPLAY...]      first display = the leader (window 1)

The camera follows each box's own character, so when a follower stands a little away from window
1's character, the same screen point is a different spot in its world. mbcast shifts that
follower's cursor so a click on a monster in window 1 lands on the same monster everywhere.

The shift is measured, not computed: a patch of scenery from window 1 (away from the characters in
the middle) is looked up in each follower's screen. That is exact to a pixel or two, where the
coordinates under the minimap are whole tiles (up to ~20 px off), and it doesn't depend on the
camera's zoom or angle as long as all four cameras are the same (Home resets them). If the match
is poor (an open game window, characters far apart), the tile offset is used instead.
This writes, a few times a second:

  STATE_DIR/offsets   one line per follower: "dx dy" in pixels (0 0 if unknown or far away)
  STATE_DIR/aligned   "yes", or the largest distance in tiles, or "?" if a box can't be read
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

import mbalign
import mbcoords

ORIGIN = (8, 37)
# scenery patch, game coordinates: up and left of the characters (feet ~683,384, names above)
PATCH = (330, 130, 690, 300)
SEARCH = 70             # px around the tile-based guess
SCALE = 2               # match at half resolution
MIN_SCORE = 0.6

MAX_TILES = 15          # farther than this the spot is off screen anyway: don't shift
PERIOD = 0.75          # ~1 update a second: the group moves together, offsets change slowly


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def gray(disp, x0, y0, x1, y1):
    im = mbcoords.grab(disp, (x0, y0, x1, y1))
    g = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY)
    return cv2.resize(g, (g.shape[1] // SCALE, g.shape[0] // SCALE), interpolation=cv2.INTER_AREA)


def measure(leader_patch, disp, guess):
    """Pixel shift of the scenery in this follower's screen, near the tile-based guess."""
    x0, y0, x1, y1 = PATCH
    gx, gy = guess
    try:
        area = gray(disp, ORIGIN[0] + x0 + gx - SEARCH, ORIGIN[1] + y0 + gy - SEARCH,
                    ORIGIN[0] + x1 + gx + SEARCH, ORIGIN[1] + y1 + gy + SEARCH)
    except Exception:
        return None
    if area.shape[0] < leader_patch.shape[0] or area.shape[1] < leader_patch.shape[1]:
        return None
    res = cv2.matchTemplate(area, leader_patch, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < MIN_SCORE:
        return None
    return (gx - SEARCH + loc[0] * SCALE, gy - SEARCH + loc[1] * SCALE)


def main(state, displays):
    pool = ThreadPoolExecutor(len(displays))
    last = None
    while os.path.exists(os.path.join(state, "session")):
        pos = list(pool.map(mbcoords.read, displays))
        lead = pos[0]
        x0, y0, x1, y1 = PATCH
        try:
            patch = gray(displays[0], ORIGIN[0] + x0, ORIGIN[1] + y0, ORIGIN[0] + x1, ORIGIN[1] + y1)
            if float(patch.std()) < 6:
                patch = None            # featureless (water, plain snow): nothing to match
        except Exception:
            patch = None
        guesses = []
        for p in pos[1:]:
            if lead is None or p is None:
                guesses.append((0, 0))
            else:
                gx, gy = mbalign.tiles_to_px(lead[0] - p[0], lead[1] - p[1])
                guesses.append((round(gx), round(gy)))
        measured = (list(pool.map(lambda a: measure(patch, *a), zip(displays[1:], guesses)))
                    if patch is not None else [None] * len(guesses))
        lines, spread, unknown = [], 0, lead is None
        try:
            with open(os.path.join(state, "excluded")) as f:
                excl = {int(x) for x in f.read().split()}
        except (OSError, ValueError):
            excl = set()
        for n, (p, guess, shift) in enumerate(zip(pos[1:], guesses, measured), start=1):
            if n in excl:                       # travelling back after a death: not part of the spread
                lines.append(f"{guess[0]} {guess[1]}")
                continue
            if lead is None or p is None:
                unknown = True
            else:
                tx, ty = lead[0] - p[0], lead[1] - p[1]
                spread = max(spread, abs(tx), abs(ty))
                if max(abs(tx), abs(ty)) > MAX_TILES:
                    lines.append("0 0")
                    continue
            if shift is None:
                shift = guess
            lines.append(f"{shift[0]} {shift[1]}")
        out = "\n".join(lines) + "\n"
        status = "?" if unknown else ("yes" if spread == 0 else str(spread))
        if (out, status) != last:
            write(os.path.join(state, "offsets"), out)
            write(os.path.join(state, "aligned"), status + "\n")
            last = (out, status)
        time.sleep(PERIOD)


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2:])
    except KeyboardInterrupt:
        pass
