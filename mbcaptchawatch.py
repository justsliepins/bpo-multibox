#!/usr/bin/env python3
"""Captcha watcher: BPO's occasional "Image Captcha" is for the player to answer, never the bot.

Every few seconds every client is checked for the captcha title bar (template mbcaptcha.png,
from the user's PyBot). While one is open: input mirroring is paused (your typing in that window
must not reach the others), an alarm sounds and a notification repeats every 60 s, and one line
"CAPTCHA window N" is printed per sighting (for a log watcher). KILL pauses itself separately.

usage: mbcaptchawatch.py STATE DISPLAY...
"""
import os
import subprocess
import sys
import time

import mbtarget as t

EVERY = float(os.environ.get("CAPTCHA_EVERY", "3"))
SOUND = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"


def main(argv):
    state, displays = argv[1], argv[2:]
    pause = os.path.join(state, f"pause.{os.getpid()}")
    open_in, alerted = set(), 0.0
    try:
        while True:
            now_open = set()
            for i, d in enumerate(displays):
                try:
                    if t.captcha_box(t.screenshot(d)):
                        now_open.add(i + 1)
                except Exception:          # a client restarting or a failed grab: try next round
                    pass
            if now_open:
                if not os.path.exists(pause):
                    t.write(pause, "1\n")
                for w in sorted(now_open - open_in):
                    print(f"CAPTCHA window {w}: answer it; mirroring paused until it's gone", flush=True)
                if time.time() - alerted >= 60 or now_open - open_in:
                    wins = ", ".join(map(str, sorted(now_open)))
                    subprocess.run(["notify-send", "-u", "critical", "-a", "multibox", "Black Pearl Multibox",
                                    f"Captcha in window {wins}: please answer it"], check=False)
                    subprocess.Popen(["pw-play", SOUND], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    alerted = time.time()
            elif open_in:
                print(f"CAPTCHA gone (window {', '.join(map(str, sorted(open_in)))}); mirroring back on", flush=True)
                if os.path.exists(pause):
                    os.remove(pause)
            open_in = now_open
            time.sleep(EVERY)
    finally:
        if os.path.exists(pause):
            os.remove(pause)


if __name__ == "__main__":
    main(sys.argv)
