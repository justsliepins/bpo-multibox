#!/usr/bin/env python3
"""Live party readout, one line a second: per window offset from window 1 (tiles) and own HP,
plus any new hunter/tank log lines. For watching the bot work.

usage: mbwatch.py SECONDS
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import mbcoords
import mbtarget as t

D = [":61", ":62", ":63", ":64", ":65"]
LOG = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "multibox", "kill.log")


def snap(d):
    im = t.screenshot(d)
    return mbcoords.read(d), t.party_hp(im)[0]


def main():
    end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 30)
    pool = ThreadPoolExecutor(5)
    with open(LOG) as f:
        f.seek(0, 2)
        while time.time() < end:
            t0 = time.time()
            r = list(pool.map(snap, D))
            lead = r[0][0]
            cells = []
            for k, (p, hp) in enumerate(r):
                if p and lead:
                    off = f"{p[0] - lead[0]:+d},{p[1] - lead[1]:+d}"
                else:
                    off = "?"
                cells.append(f"w{k + 1} {off:>9} {('%3d%%' % round(100 * hp)) if hp is not None else '  ?'}")
            print(time.strftime("%H:%M:%S"), f"lead {lead}", " | ".join(cells), flush=True)
            for line in f.readlines():
                if "rest: resting" in line or "arrived" in line:
                    continue
                print("     >", line.rstrip(), flush=True)
            time.sleep(max(0, 1 - (time.time() - t0)))


if __name__ == "__main__":
    main()
