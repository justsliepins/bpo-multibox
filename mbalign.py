#!/usr/bin/env python3
"""ALIGN: walk every box's character onto the same map tile.

    mbalign.py DISPLAY [DISPLAY...]      first display = the leader (window 1)

Each box shows its character's map coordinates under the minimap (mbcoords.py reads them).
Clicking where the leader stands doesn't work: clicking another player's model only selects
it (WorldScene.cpp), so the click never turns into a walk. Instead everyone, the leader too,
walks to a fresh spot 3 tiles from the leader. Each box gets its own click, computed from its own
coordinates, so they all end on the same tile. Every round uses a new direction (a blocked spot,
or a monster standing there in one box, doesn't fail every round), skips spots that would need a
click off screen, and stops with a reason if a window doesn't walk (seated) or everyone walks the
wrong way (rotated camera).

The camera follows the character, so in every box the character's feet are at the window
centre, and map tiles map to screen pixels as TILE_X / TILE_Y below (the default camera, turned).
"""
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import mbcoords

ORIGIN = (8, 37)              # game window inside the box
CENTER = (683, 384)           # character's feet, relative to the game window (1366x768)
# Screen pixels per map tile in the game's default camera (Home), which multibox restores before
# ALIGN and when broadcast turns on. That view is turned ~33 degrees: one tile in map x is 18 px
# right and 12 px up, one tile in map y is 13 px right and 17 px down. Measured on BPO with eight
# test walks and a least-squares fit (2026-10-06). CLICK_FEET: where to click to stay on your tile.
TILE_X = (18.06, -11.77)
TILE_Y = (13.30, 17.37)
CLICK_FEET = (-1, 17)


def tiles_to_px(dx, dy):
    """Screen offset in pixels for a map offset in tiles (default camera)."""
    return (dx * TILE_X[0] + dy * TILE_Y[0], dx * TILE_X[1] + dy * TILE_Y[1])
MAX_DIST = 14                 # farther than this is off screen: walk closer by hand
ROUNDS = 5
TOL = int(__import__("os").environ.get("MB_ALIGN_TOL", "0"))


def read_all(displays):
    with ThreadPoolExecutor(len(displays)) as ex:
        return list(ex.map(mbcoords.read, displays))


def settle(displays, timeout=6.0):
    """Coordinates once nobody has moved for ~0.8 s."""
    last, stable, end = None, 0, time.time() + timeout
    while time.time() < end:
        now = read_all(displays)
        stable = stable + 1 if now == last and None not in now else 0
        last = now
        if stable >= 2:
            break
        time.sleep(0.2)
    return last


def click(display, x, y):
    # Don't start a fight: if a monster is under the spot (attack cursor), shift the click by about
    # a tile; if every nearby spot has one, skip this click (the next round tries another direction).
    try:
        import mbtarget
        for dx, dy in ((0, 0), (20, 0), (-20, 0), (0, 20), (0, -20), (20, 20), (-20, -20)):
            subprocess.run(["xdotool", "mousemove", str(x + dx), str(y + dy)],
                           env={"DISPLAY": display, "PATH": "/usr/bin:/bin"}, check=False)
            time.sleep(0.06)
            if mbtarget.cursor_id(display) not in mbtarget.ATTACK_FRAMES:
                x, y = x + dx, y + dy
                break
        else:
            return
    except Exception:
        pass
    # Move, then move again right before pressing: if the real mouse wanders into box 1 meanwhile,
    # the game would otherwise pick up the user's position for the click.
    subprocess.run(["xdotool", "mousemove", str(x), str(y), "sleep", "0.08",
                    "mousemove", str(x), str(y), "mousedown", "1", "sleep", "0.1", "mouseup", "1"],
                   env={"DISPLAY": display, "PATH": "/usr/bin:/bin"}, check=False)


def screen_point(pos, target):
    """Where to click in a box to walk from pos to target; None if that's off the usable screen."""
    px, py = tiles_to_px(target[0] - pos[0], target[1] - pos[1])
    x = CENTER[0] + CLICK_FEET[0] + round(px)
    y = CENTER[1] + CLICK_FEET[1] + round(py)
    if not (20 <= x <= 1346 and 60 <= y <= 580):    # off screen, or on the chat/skill bar
        return None
    return ORIGIN[0] + x, ORIGIN[1] + y


def dist(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


# A fresh spot next to the leader, in a different direction each round: if the spot is blocked
# (tree, wall) or a monster or player stands on it in one box, the next round tries elsewhere.
DIRECTIONS = [(0, 3), (3, 0), (-3, 0), (0, -3), (3, 3), (-3, 3), (3, -3), (-3, -3)]


def trace_align(displays, pos):
    """Alt+R auto-trace: every follower walks to window 1's exact tile (the game pathfinds around
    obstacles and never attacks on the way). All followers at once. Returns the new positions."""
    import mbtarget
    lead = pos[0]
    todo = [d for d, p in zip(displays[1:], pos[1:]) if p != lead]
    if not todo:
        return pos
    with ThreadPoolExecutor(len(todo)) as ex:
        list(ex.map(lambda d: mbtarget.trace(d, lead[0], lead[1]), todo))
    t0 = time.time()
    while time.time() - t0 < 20:
        time.sleep(1.0)
        now = read_all(displays)
        if None not in now and max(dist(p, lead) for p in now) <= max(TOL, 0):
            break
    return settle(displays)


def main(displays):
    import mbfix   # System menu check: a click there could hit Logout or Exit Game
    import mbtarget
    # the dead can't walk: align the living (KILL's death handling revives the rest)
    dead = [d for d in displays if mbtarget.own_hp(d) == 0.0]
    if displays[0] in dead:
        return "window 1 is dead; revive it first"
    displays = [d for d in displays if d not in dead]
    pos = settle(displays)
    if None in pos:
        bad = [d for d, p in zip(displays, pos) if p is None]
        return f"can't read the coordinates in {', '.join(bad)}"
    blocked = [d for d in displays if mbfix.menu_open(d)]
    if blocked:
        return "System menu open in " + ", ".join(blocked) + "; close it first (or press FIX)"
    # All movement via Alt+R auto-trace (map clicks can land on a monster and start a fight):
    # every follower traces to window 1's tile, repeated for the ones that didn't make it.
    for rnd in range(ROUNDS):
        print(f"round {rnd}: {pos}", file=sys.stderr)
        spread = max(dist(p, pos[0]) for p in pos)
        if spread == 0:
            return f"aligned at {pos[0][0]}, {pos[0][1]}"
        if TOL and spread <= TOL:
            return f"aligned at {pos[0][0]}, {pos[0][1]} (within {TOL} tile)"
        far = [int(displays[i].lstrip(":")) - 60 for i, p in enumerate(pos) if dist(p, pos[0]) > 60]
        if far:
            return "window " + ", ".join(map(str, far)) + " is too far away; walk it closer first"
        before = pos
        pos = trace_align(displays, pos)
        if None in pos:
            return "lost the coordinates while tracing"
        stuck = [int(displays[i].lstrip(":")) - 60 for i, (a, b) in enumerate(zip(before, pos)) if a == b and a != pos[0]]
        if stuck and rnd == 1:          # didn't move twice: probably sitting. Insert stands up
            for w in stuck:
                subprocess.run(["xdotool", "keydown", "Insert", "sleep", "0.1", "keyup", "Insert"],
                               env={**os.environ, "DISPLAY": displays[w - 1]}, check=False)
            time.sleep(1.5)
            continue
        if stuck and rnd >= 2:
            return ("window " + ", ".join(map(str, stuck)) + " doesn't move with Alt+R "
                    "(sitting? dialog open? blocked?)")
    spread = max(dist(p, pos[0]) for p in pos)
    return f"almost: off by {spread} tile{'s' * (spread != 1)}; press ALIGN again"


if __name__ == "__main__":
    msg = main(sys.argv[1:] or [":61", ":62", ":63", ":64"])
    print(msg)
