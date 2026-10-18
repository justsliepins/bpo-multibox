#!/usr/bin/env python3
"""FIX: bring every box back into the same state after broadcast starts misbehaving.

    mbfix.py DISPLAY [DISPLAY...]

Broadcast copies the same input everywhere, so it only works while all four game UIs are in the
same state. They drift apart (an inventory or skill window open in some boxes, chat active, a
stuck key), and then the same click lands on a window in one box and on the ground in another.
For every box this releases stuck keys and mouse buttons and closes all open game windows: in this
client each Esc closes one window, and when none is open Esc opens the System menu
(UIGlobalVar.cpp _evtESCKey). So press Esc until the System menu shows up, then close it.
The caller (multibox fix) pauses broadcast, restarts the helpers and runs ALIGN afterwards.
"""
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageChops, ImageStat

import mbcoords

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = (8, 37)
MENU_RECT = (610, 300, 760, 520)        # where the System menu's buttons are (1366x768 window)
MENU_SHOWN = 20                         # mean pixel difference below this = the menu is open
MAX_ESC = 10
MODIFIERS = ["Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R"]

_template = Image.open(os.path.join(HERE, "mbsysmenu.png")).convert("L")


def xdo(disp, *args):
    subprocess.run(["xdotool", *args], env={"DISPLAY": disp, "PATH": "/usr/bin:/bin"},
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def menu_open(disp):
    x0, y0, x1, y1 = MENU_RECT
    im = mbcoords.grab(disp, (ORIGIN[0] + x0, ORIGIN[1] + y0, ORIGIN[0] + x1, ORIGIN[1] + y1))
    return sum(ImageStat.Stat(ImageChops.difference(im.convert("L"), _template)).mean) < MENU_SHOWN


def fix_box(disp):
    for key in MODIFIERS:                      # stuck modifiers make every click a Ctrl/Alt-click
        xdo(disp, "keyup", key)
    for button in ("1", "2", "3"):
        xdo(disp, "mouseup", button)
    for presses in range(MAX_ESC + 1):
        if menu_open(disp):
            for _ in range(3):                     # close the System menu: nothing is open now
                xdo(disp, "key", "Escape")
                time.sleep(0.6)
                if not menu_open(disp):
                    return f"{disp}: closed {presses} window{'s' * (presses != 1)}"
            return f"{disp}: System menu didn't close"
        if presses == MAX_ESC:
            break
        xdo(disp, "key", "Escape")
        time.sleep(0.5)
    return f"{disp}: still something open after {MAX_ESC} Esc presses"


if __name__ == "__main__":
    displays = sys.argv[1:] or [":61", ":62", ":63", ":64"]
    with ThreadPoolExecutor(len(displays)) as ex:
        results = list(ex.map(fix_box, displays))
    for line in results:
        print(line, file=sys.stderr)
    time.sleep(0.5)
    still = [d for d in displays if menu_open(d)]
    if still:
        print("System menu still open in " + ", ".join(still), file=sys.stderr)
    ok = not still and all("closed" in r for r in results)
    sys.exit(0 if ok else 1)
