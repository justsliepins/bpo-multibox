#!/usr/bin/env python3
"""Safe paths on Ascaron (garner) from the server's own map data.

  * land / sea and the area of every tile: garner.atr (4096x4096 x 3 bytes: flags, -, area id)
  * obstacles: garner.blk (1 bit per half tile)
  * forbidden: mines, mazes, bandit camp (AreaSet ids below) and a ring around every map-change
    point (garnerswhmap.txt: walking near one has put the party into Silver Mine 1)
  * dangerous: spawn rectangles (garnerChaSpn.txt) of mobs DANGER_GAP+ levels above the party
    cost a lot (a detour is taken when one exists)
A* runs on 4-tile cells; the route comes back as waypoints ~STEP tiles apart, short enough that
the game's own auto-trace between two of them can't wander into a mine.

usage: mbpath.py X0 Y0 X1 Y1 [PARTY_LEVEL]     prints the waypoints
"""
import heapq
import os
import sys

import numpy as np

RES = os.path.expanduser("~/Work/TopServerfiles/TalesOfPirateDX9-main/server/GameServer/resource")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CELL = 4                       # tiles per planning cell
STEP = 30                      # tiles between waypoints
AVOID_AREAS = {39, 41, 50, 68}  # Abandon Mine 1, Silver Mine 1, Santuary Maze, Bandit Camp
SWITCH_RING = 20               # tiles around a map-change point
AVOID_PAD = 2                  # cells (8 tiles) kept clear of a mine's edge: walking onto area 41
                               # at its very border (Argent -> Abandon Mine Haven) entered Silver Mine 1
DANGER_GAP = -5                # aggressive mobs from 5 levels below the party up are detoured (Killer Shrooms L19, Bees L20)
DANGER_COST = 60


def _base():
    """(passable bool grid, area grid) on CELL-tile cells, cached."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"garner_cells{CELL}_pad{AVOID_PAD}.npz")
    if os.path.exists(path):
        z = np.load(path)
        return z["ok"], z["area"]
    atr = np.memmap(os.path.join(RES, "garner/garner.atr"), dtype=np.uint8, mode="r", offset=8).reshape(4096, 4096, 3)
    n = 4096 // CELL
    land = (np.asarray(atr[:, :, 0]) != 0).reshape(n, CELL, n, CELL).mean(axis=(1, 3))
    area = np.asarray(atr[CELL // 2::CELL, CELL // 2::CELL, 2])
    blk = np.unpackbits(np.fromfile(os.path.join(RES, "garner/garner.blk"), dtype=np.uint8, offset=8))
    blk = blk.reshape(8192, 8192).reshape(n, CELL * 2, n, CELL * 2).mean(axis=(1, 3))
    bad = np.isin(area, list(AVOID_AREAS))
    grown = bad.copy()
    for dy in range(-AVOID_PAD, AVOID_PAD + 1):
        for dx in range(-AVOID_PAD, AVOID_PAD + 1):
            grown |= np.roll(np.roll(bad, dy, 0), dx, 1)
    ok = (land >= 0.75) & (blk < 0.5) & ~grown
    for line in open(os.path.join(RES, "garner/garnerswhmap.txt"), encoding="latin-1"):
        f = line.split("\t")
        if len(f) > 4 and f[0].strip().isdigit() and not line.startswith("//"):
            x, y = (int(v) // 100 for v in f[3].split(","))
            r = SWITCH_RING // CELL + 1
            cx, cy = x // CELL, y // CELL
            ok[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1] = False
    np.savez_compressed(path, ok=ok, area=area)
    return ok, area


def _levels():
    lv = {}
    for line in open(os.path.join(RES, "CharacterInfo.txt"), encoding="latin-1"):
        f = line.split("\t")
        if len(f) > 61 and f[0].isdigit():
            try:
                lv[int(f[0])] = (int(f[60]), int(f[53]))      # level, AI (10 = aggressive)
            except ValueError:
                pass
    return lv


# Hazards seen in BPO that the server files don't have (x0, y0, x1, y1), never walked through:
NO_GO = [
    (1750, 2915, 1910, 3000),   # pine field with Lv62 "???" Small Treants (killed LuckyThird)
    (1755, 2575, 1800, 2610),   # Silver Mine 1 entrance in BPO (walked into at (1774,2590))
]


def _ids(names):
    out = set()
    for line in open(os.path.join(RES, "CharacterInfo.txt"), encoding="latin-1"):
        f = line.split("\t")
        if len(f) > 2 and f[0].isdigit() and f[1].strip() in names:
            out.add(int(f[0]))
    return out


ALWAYS_AVOID = _ids({"Whacky Lamb", "Killer Shroom", "Barbaric Bee", "Air Porky", "Stramonium"})


def danger_grid(party_level):
    n = 4096 // CELL
    cost = np.ones((n, n), np.float32)
    for x0, y0, x1, y1 in NO_GO:
        cost[max(0, y0 // CELL - 5):y1 // CELL + 6, max(0, x0 // CELL - 5):x1 // CELL + 6] += 500   # 20-tile margin
    lv = _levels()
    for line in open(os.path.join(RES, "garner/garnerChaSpn.txt"), encoding="latin-1"):
        f = line.rstrip("\n").split("\t")
        if len(f) < 6 or not f[0].isdigit():
            continue
        mobs = [int(m) for m in f[4].split(",") if m.strip().isdigit()]
        # only aggressive mobs (AI 10) attack a passing party; passive ones (AI 5) don't. Some
        # are far stronger in BPO than their server level (Whacky Lamb "L14" hits 835): those
        # are always avoided
        if not any((lv.get(m, (0, 0))[0] >= party_level + DANGER_GAP or m in ALWAYS_AVOID)
                   and lv.get(m, (0, 0))[1] == 10 for m in mobs):
            continue
        (x0, y0), (x1, y1) = [tuple(int(v) // 100 for v in p.split(",")) for p in f[1:3]]
        if (x1 - x0) * (y1 - y0) > 400 * 400:      # ocean-wide spawns: not on a land route anyway
            continue
        pad = 6                                    # 24 tiles: aggressive mobs chase
        cost[max(0, y0 // CELL - pad):y1 // CELL + pad, max(0, x0 // CELL - pad):x1 // CELL + pad] += DANGER_COST
    return cost


def clear(a, b):
    """The straight walk a -> b (tiles) stays on passable, mine-free cells."""
    ok, _ = _base()
    n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) // 2) + 1
    for k in range(n + 1):
        x = a[0] + (b[0] - a[0]) * k / n
        y = a[1] + (b[1] - a[1]) * k / n
        if not ok[int(y) // CELL, int(x) // CELL]:
            return False
    return True


def _nearest_ok(ok, c):
    cx, cy = c
    if ok[cy, cx]:
        return c
    for r in range(1, 30):
        ys, xs = np.nonzero(ok[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1])
        if len(xs):
            k = np.argmin((xs - r) ** 2 + (ys - r) ** 2)
            return (max(0, cx - r) + xs[k], max(0, cy - r) + ys[k])
    return c


def plan(start, goal, party_level=13):
    """Waypoints (tiles) from start to goal, or None when no safe walk exists."""
    ok, _ = _base()
    cost = danger_grid(party_level)
    s = _nearest_ok(ok, (start[0] // CELL, start[1] // CELL))
    g = _nearest_ok(ok, (goal[0] // CELL, goal[1] // CELL))
    pad = 120
    x0, x1 = max(0, min(s[0], g[0]) - pad), min(ok.shape[1], max(s[0], g[0]) + pad)
    y0, y1 = max(0, min(s[1], g[1]) - pad), min(ok.shape[0], max(s[1], g[1]) + pad)
    dist = {s: 0.0}
    prev = {}
    heap = [(0.0, s)]
    moves = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
    while heap:
        _, c = heapq.heappop(heap)
        if c == g:
            break
        dc = dist[c]
        for dx, dy, w in moves:
            nx, ny = c[0] + dx, c[1] + dy
            if not (x0 <= nx < x1 and y0 <= ny < y1) or not ok[ny, nx]:
                continue
            if dx and dy and not (ok[c[1], nx] and ok[ny, c[0]]):
                continue                        # no corner cutting past a wall
            nd = dc + w * cost[ny, nx]
            if nd < dist.get((nx, ny), 1e18):
                dist[(nx, ny)] = nd
                prev[(nx, ny)] = c
                h = ((nx - g[0]) ** 2 + (ny - g[1]) ** 2) ** 0.5
                heapq.heappush(heap, (nd + h, (nx, ny)))
    if g not in dist:
        return None
    cells = [g]
    while cells[-1] != s:
        cells.append(prev[cells[-1]])
    cells.reverse()
    pts = [(cx * CELL + CELL // 2, cy * CELL + CELL // 2) for cx, cy in cells]
    out, acc = [], 0.0
    for a, b in zip(pts, pts[1:]):
        acc += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        if acc >= STEP:
            out.append(b)
            acc = 0.0
    out.append(tuple(goal))
    return out


if __name__ == "__main__":
    a = list(map(int, sys.argv[1:5]))
    lvl = int(sys.argv[5]) if len(sys.argv) > 5 else 13
    wp = plan((a[0], a[1]), (a[2], a[3]), lvl)
    print(wp if wp is None else " ".join(f"{x},{y}" for x, y in wp))
