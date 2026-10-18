#!/usr/bin/env python3
"""After a server restart: get every window back into the game. A window at character select
showing "target map cannot be reached" (map server still starting) gets Confirm + Enter once a
minute; a window anywhere else gets the full mbrelog login. Ends when all five are in the game."""
import time

import mbcoords
import mbrelog
import mbtarget as t

D = mbrelog.DISPLAYS


def in_game(d):
    """Coordinates alone lie: a disconnected client keeps showing its frozen HUD."""
    return mbcoords.read(d) is not None and not mbrelog.ocr_has(d, (470, 360, 900, 395), "disconnected")


def at_char_select(d):
    return mbrelog.ocr_has(d, (560, 715, 890, 745), "enter")


def main():
    tried = {}                                  # window -> time of its last full relog
    while True:
        out = [w for w in mbrelog.CHARS if not in_game(D[w - 1])]
        if not out:
            mbrelog.log("all windows are in the game")
            print("ALL IN", flush=True)
            return
        for w in out:
            d = D[w - 1]
            if at_char_select(d):
                mbrelog.click(d, 682, 420)            # Confirm on "target map cannot be reached"
                time.sleep(1)
                mbrelog.click(d, 707, 730)            # Enter (the character stays selected)
                mbrelog.log(f"window {w}: at character select: Enter again")
            elif time.time() - tried.get(w, 0) > 300:   # server refused / still down: again in 5 min
                tried[w] = time.time()
                print(f"window {w}: relog {mbrelog.relog(w)}", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
