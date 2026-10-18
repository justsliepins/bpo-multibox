#!/usr/bin/env python3
"""Camera yaw from the minimap's view cone (two white lines from the camera, opening the way it
looks; the minimap itself is north-up). Every window's camera must look the same way: one screen
mapping then serves all of them. align(): right-drag (rotates the camera) until it matches.

usage: mbcam.py [DISPLAY...]          prints each window's yaw
       mbcam.py align YAW DISPLAY...  turns each window to YAW
"""
import math
import sys
import time

import cv2
import numpy as np

import mbtarget as t

MAP = (1228, 50, 1356, 155)               # minimap picture (below its title, above the coords)
GROUND = 2089885924                       # plain cursor over empty ground


def yaw(im):
    """Degrees, 0 = looking north (up on the minimap), clockwise; None if no cone found."""
    a = np.asarray(im.crop(MAP).convert("RGB")).astype(int)
    white = ((a.min(axis=2) > 185)).astype(np.uint8) * 255
    lines = cv2.HoughLinesP(white, 1, np.pi / 180, 10, minLineLength=14, maxLineGap=8)
    if lines is None or len(lines) < 2:
        return None
    segs = sorted((tuple(int(v) for v in np.ravel(l)[:4]) for l in lines), key=lambda s: -math.hypot(s[2] - s[0], s[3] - s[1]))
    s1 = segs[0]
    a1 = math.atan2(s1[3] - s1[1], s1[2] - s1[0])
    s2 = next((s for s in segs[1:] if abs(math.sin(math.atan2(s[3] - s[1], s[2] - s[0]) - a1)) > 0.3), None)
    if s2 is None:
        return None
    # apex: intersection of the two lines
    x1, y1, x2, y2 = map(float, s1)
    x3, y3, x4, y4 = map(float, s2)
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-6:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    dirs = []
    for x1, y1, x2, y2 in (s1, s2):         # each line's far end, away from the apex
        fx, fy = (x1, y1) if math.hypot(x1 - px, y1 - py) > math.hypot(x2 - px, y2 - py) else (x2, y2)
        n = math.hypot(fx - px, fy - py) or 1
        dirs.append(((fx - px) / n, (fy - py) / n))
    bx, by = dirs[0][0] + dirs[1][0], dirs[0][1] + dirs[1][1]
    return (math.degrees(math.atan2(bx, -by)) + 360) % 360


def read(d):
    return yaw(t.screenshot(d))


def drag(d, px):
    """Right-drag px pixels (negative: left) with small RELATIVE moves: absolute jumps under Wine
    turned the camera by random amounts. ~0.33 degree per pixel, right = yaw up."""
    O = t.ORIGIN
    for sx, sy in ((1100, 640), (260, 520), (1100, 160), (683, 600)):
        t.move(d, O[0] + sx, O[1] + sy)
        time.sleep(0.15)
        if t.cursor_id(d) == GROUND:
            break
    else:
        return False
    step = 4 if px > 0 else -4
    seq = ["mousedown", "3", "sleep", "0.1"]
    for _ in range(max(1, abs(px) // 4)):
        seq += ["mousemove_relative", "--", str(step), "0", "sleep", "0.02"]
    seq += ["sleep", "0.1", "mouseup", "3"]
    t.key(d, *seq)
    time.sleep(1.2)                         # the camera glides
    return True


DEG_PER_PX = 0.33


def align(d, target, tol=3):
    y = read(d)
    for _ in range(8):
        if y is None:
            return None
        diff = (target - y + 180) % 360 - 180
        if abs(diff) <= tol:
            return y
        if not drag(d, int(round(diff / DEG_PER_PX))):
            return y
        y = read(d)
    return y


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "align":
        tgt = float(sys.argv[2])
        for d in sys.argv[3:]:
            print(d, align(d, tgt))
    else:
        for d in sys.argv[1:] or [":61"]:
            print(d, read(d))
