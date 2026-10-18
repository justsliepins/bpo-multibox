#!/usr/bin/env python3
"""Aim service: where in each follower's screen is the thing window 1 is clicking on?

    mbaim.py SOCKET DISPLAY [DISPLAY...]      first display = the leader (window 1)

mbcast asks this at the moment of a click over the game world, with the leader's cursor and the
predicted follower cursors (leader point + tile offset from mbtrack.py):

    request:  "lx ly fx1 fy1 fx2 fy2 ...\\n"
    reply:    "fx1 fy1 fx2 fy2 ...\\n"         (corrected follower points)

The offset alone is only good to about a tile (positions are whole tiles), and each client draws a
moving monster where its own copy of the server's updates puts it, so a follower's click can just
miss. Here a patch around the leader's cursor (the monster, its name and HP bar) is looked up in
each follower's screen near the predicted point (OpenCV template matching), preferring matches
close to the prediction so a neighbouring monster of the same kind isn't picked. Without a
confident match the prediction is kept.
"""
import os
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from Xlib import X, display

PATCH_W, PATCH_H = 72, 84     # around the cursor; a bit more above it (name, HP bar)
PATCH_UP = 12
SEARCH = 56                   # look this far around the predicted point (~2.5 tiles)
MIN_SCORE = 0.55              # normalized correlation needed to trust a match
DIST_PENALTY = 0.004          # per pixel from the prediction: prefer the nearest look-alike

_local = threading.local()


def _display(name):
    cache = getattr(_local, "displays", None)
    if cache is None:
        cache = _local.displays = {}
    if name not in cache:
        cache[name] = display.Display(name)
    return cache[name]


def grab(name, x, y, w, h):
    d = _display(name)
    root = d.screen().root
    g = root.get_geometry()
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(g.width, x + w), min(g.height, y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None, x0, y0
    raw = root.get_image(x0, y0, x1 - x0, y1 - y0, X.ZPixmap, 0xffffffff)
    a = np.frombuffer(raw.data, dtype=np.uint8).reshape(y1 - y0, x1 - x0, 4)
    return cv2.cvtColor(a, cv2.COLOR_BGRA2GRAY), x0, y0


def pink_mask(name, x, y, w, h):
    d = _display(name)
    root = d.screen().root
    g = root.get_geometry()
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(g.width, x + w), min(g.height, y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None, x0, y0
    raw = root.get_image(x0, y0, x1 - x0, y1 - y0, X.ZPixmap, 0xffffffff)
    a = np.frombuffer(raw.data, dtype=np.uint8).reshape(y1 - y0, x1 - x0, 4).astype(int)
    b, gg, r = a[..., 0], a[..., 1], a[..., 2]
    return ((r > 150) & (b > 100) & (gg < 90) & (r - gg > 100) & (b - gg > 60)).astype(np.uint8), x0, y0


PINK_R = 10          # the leader clicked a pink (texture-swapped) mob if pink is this close
PINK_SEARCH = 90     # look this far around the prediction in the follower


def aim_pink(leader, lx, ly, follower, fx, fy):
    """The leader clicked a magenta mob: the follower clicks the centre of the pink blob nearest
    its predicted point (exact, unlike the template match on a small snail)."""
    m, _, _ = pink_mask(leader, lx - PINK_R, ly - PINK_R, 2 * PINK_R, 2 * PINK_R)
    if m is None or m.sum() < 4:
        return None
    area, ax, ay = pink_mask(follower, fx - PINK_SEARCH, fy - PINK_SEARCH, 2 * PINK_SEARCH, 2 * PINK_SEARCH)
    if area is None or area.sum() < 15:
        return None
    area = cv2.dilate(area, np.ones((5, 5), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(area)
    best = None
    for k in range(1, n):
        if stats[k][4] < 20:
            continue
        cx, cy = ax + cents[k][0], ay + cents[k][1]
        dist = (cx - fx) ** 2 + (cy - fy) ** 2
        if best is None or dist < best[0]:
            best = (dist, int(cx), int(cy))
    return (best[1], best[2]) if best else None


def aim(leader, lx, ly, follower, fx, fy):
    p = aim_pink(leader, lx, ly, follower, fx, fy)
    # only clicks on a pink mob are aimed: with AIM=1 every click asks, and a template match
    # moved ground clicks and walked followers away from the party (2026-10-15)
    return p if p else (fx, fy)
    tpl, tx, ty = grab(leader, lx - PATCH_W // 2, ly - PATCH_H // 2 - PATCH_UP, PATCH_W, PATCH_H)
    if tpl is None:
        return fx, fy
    # where the cursor sits inside the template
    cx, cy = lx - tx, ly - ty
    th, tw = tpl.shape
    sx, sy = fx - cx - SEARCH, fy - cy - SEARCH
    area, ax, ay = grab(follower, sx, sy, tw + 2 * SEARCH, th + 2 * SEARCH)
    if area is None or area.shape[0] < th or area.shape[1] < tw:
        return fx, fy
    if float(tpl.std()) < 6:          # featureless (plain snow, water): nothing to lock onto
        return fx, fy
    res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
    # prefer matches near the prediction
    h, w = res.shape
    px, py = fx - cx - ax, fy - cy - ay    # template origin of the prediction, in res coordinates
    yy, xx = np.mgrid[0:h, 0:w]
    score = res - DIST_PENALTY * np.hypot(xx - px, yy - py)
    iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
    if res[iy, ix] < MIN_SCORE:
        return fx, fy
    return int(ax + ix + cx), int(ay + iy + cy)


def serve(path, displays):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    pool = ThreadPoolExecutor(max(1, len(displays) - 1))
    while True:
        conn, _ = srv.accept()
        with conn:
            try:
                line = conn.makefile().readline()
                nums = [int(v) for v in line.split()]
                lx, ly, rest = nums[0], nums[1], nums[2:]
                pts = [(rest[i], rest[i + 1]) for i in range(0, len(rest), 2)]
                jobs = [pool.submit(aim, displays[0], lx, ly, displays[i + 1], fx, fy)
                        for i, (fx, fy) in enumerate(pts)]
                out = []
                for (fx, fy), job in zip(pts, jobs):
                    try:
                        out.extend(job.result(timeout=0.08))
                    except Exception:
                        out.extend((fx, fy))
                conn.sendall((" ".join(map(str, out)) + "\n").encode())
            except (ValueError, IndexError, OSError):
                pass


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2:])
