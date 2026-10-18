#!/usr/bin/env python3
"""Turn a window's camera until its view matches the shared screen mapping (A0 in mbhunt): the
party members' health bars must be where A0 puts them. (The minimap cone does not follow the 3D
view's rotation, so it can't be used.) Small relative right-drags, ~0.33 degree per pixel.

usage: mbalignview.py DISPLAY...
"""
import math
import sys

import mbcam
import mbcoords
import mbtarget as t

A0 = ((4.7, 22.3), (-20.2, 4.35))
ALL = [":61", ":62", ":63", ":64", ":65"]


def err(d, pos=None):
    pos = pos or {o: mbcoords.read(o) for o in ALL}
    me = pos.get(d)
    im = t.screenshot(d)
    mobs = t.mob_bars(im)
    bars = [b for b in t.hp_bars(im) if b not in mobs
            and (b[0] - t.PLAYER_BAR_X) ** 2 + (b[1] - t.PLAYER_BAR_Y) ** 2 > 8 ** 2]
    es = []
    for o, p in pos.items():
        if o == d or not p or not me or not 2 <= max(abs(p[0] - me[0]), abs(p[1] - me[1])) <= 12:
            continue
        dx, dy = p[0] - me[0], p[1] - me[1]
        x = t.PLAYER_BAR_X + A0[0][0] * dx + A0[0][1] * dy
        y = t.PLAYER_BAR_Y + A0[1][0] * dx + A0[1][1] * dy
        es.append(min([math.hypot(x - b[0], y - b[1]) for b in bars] + [80]))
    if len(es) < 2:
        return None
    es.sort()
    return sum(es[:max(2, len(es) - 1)]) / max(2, len(es) - 1)   # drop the worst (a hidden bar)


def align(d, log=print):
    e = err(d)
    if e is None:
        return None
    step = 60                                   # px of drag: ~20 degrees
    tried = 0
    while e > 8 and step >= 6 and tried < 20:
        tried += 1
        mbcam.drag(d, step)
        e2 = err(d)
        if e2 is not None and e2 < e:
            e = e2
            continue
        mbcam.drag(d, -2 * step)
        e3 = err(d)
        if e3 is not None and e3 < e:
            e = e3
            step = -step
            continue
        mbcam.drag(d, step)                     # back to the start; finer
        step = int(step / 2)
    log(f"{d}: view error {e:.0f} px")
    return e


if __name__ == "__main__":
    for d in sys.argv[1:]:
        align(d)
