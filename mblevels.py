#!/usr/bin/env python3
"""Party levels from window 5's screen: its own frame plus the four party frames.

usage: mblevels.py [DISPLAY]   prints "min max l1 l2 l3 l4 l5" (unreadable frames are skipped)
Each number is read three times (OCR at scales 3, 4, 5) and the majority wins: a level read wrong
once must not send the party to a harder spot.
"""
import collections
import sys

from PIL import Image, ImageOps

import mbtarget as t

BOXES = [(62, 8, 82, 24)] + [(56, 143 + 65 * k, 78, 158 + 65 * k) for k in range(4)]


def levels(display=":65"):
    im = t.screenshot(display)
    out = []
    for box in BOXES:
        crop = im.crop((box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1))
        votes = collections.Counter()
        # black-and-white, padded, psm 7: the grey anti-aliased digits read 26 as 28/128/nothing
        # (2026-10-13: the supervisor was blind for a whole level); this read all five frames right
        for scale, invert in ((4, False), (6, True), (8, False)):
            g = crop.convert("L").resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
            g = g.point(lambda v: 255 if v > 140 else 0)
            if invert:
                g = ImageOps.invert(g)
            g = ImageOps.expand(g, border=16, fill=255 if invert else 0)
            s = t.ocr(g, 7, 1, "0123456789").strip()
            if s.isdigit() and 1 <= int(s) <= 120:
                votes[int(s)] += 1
        v, n = votes.most_common(1)[0] if votes else (None, 0)
        out.append(v if n >= 2 else None)       # one slot per frame: positions must not shift
    return out


if __name__ == "__main__":
    lv = levels(sys.argv[1] if len(sys.argv) > 1 else ":65")
    known = [v for v in lv if v is not None]
    if not known:
        sys.exit(1)
    print(min(known), max(known), *("?" if v is None else v for v in lv))
