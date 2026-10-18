#!/usr/bin/env python3
"""SIT / STAND for all boxes: press Insert (the game's sit/stand key) in each window.

    mbsit.py sit|stand DISPLAY [DISPLAY...]

The game server accepts only about one sit per half second across your characters. A rejected
sit still makes that client think its character is seated, and then it can't walk (clicks only
turn it). With a broadcast press, or presses 0.3 s apart, every second character stayed standing.
One second apart, all of them sit, also on the same tile.
"""
import subprocess
import sys
import time

GAP = 1.0


def press_insert(displays):
    for i, d in enumerate(displays):
        if i:
            time.sleep(GAP)
        subprocess.run(["xdotool", "keydown", "Insert", "sleep", "0.12", "keyup", "Insert"],
                       env={"DISPLAY": d, "PATH": "/usr/bin:/bin"}, check=False)


if __name__ == "__main__":
    mode, displays = sys.argv[1], sys.argv[2:]
    press_insert(displays)
    print("sitting" if mode == "sit" else "standing")
