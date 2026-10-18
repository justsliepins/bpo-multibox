#!/usr/bin/env python3
"""Read a box's map coordinates from the label under the minimap ("2033, 2732").

The label is a fixed bitmap font (white, 5 px wide digits), so each character is matched pixel-exact
against learned glyphs (mbglyphs.json). tesseract misread it now and then (it turned "2006" into
"86" or "20086"), so it is only a fallback for glyphs not seen yet: it reads the label at several
scales and thresholds, takes the majority, and then the new glyphs are learned, so tesseract is no
longer needed for them.
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections import Counter

from PIL import Image, ImageOps
from Xlib import X, display

# Label position inside the game window (BPO, 1366x768), relative to the window's top-left.
LABEL = (1240, 154, 1352, 172)   # x0, y0, x1, y1 (a bit wider than the text)
LIT = 200                        # the digits are pure white; the panel behind them is dark
DIGIT_H = 9                      # digits are 9 px tall; the comma's tail reaches 2 rows lower
GLYPHS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mbglyphs.json")
COORD = re.compile(r"^(\d{1,4}),(\d{1,4})$")

_glyphs = None
_lock = threading.Lock()   # ALIGN reads all boxes in parallel threads
_last_tesseract = [0.0]
TESSERACT_EVERY = 5.0      # s; an unreadable label must not keep three tesseracts busy (lag)


def _load():
    global _glyphs
    if _glyphs is None:
        try:
            with open(GLYPHS) as f:
                _glyphs = json.load(f)
        except (OSError, ValueError):
            _glyphs = {}
    return _glyphs


def _save():   # caller holds _lock
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(GLYPHS), prefix=".mbglyphs.")
    with os.fdopen(fd, "w") as f:
        json.dump(dict(_glyphs), f, indent=0, sort_keys=True)
    os.replace(tmp, GLYPHS)


_CONN = {}


def grab(disp, rect):
    """One X connection per (display, thread), kept open: opening one per grab cost ~60 ms."""
    import threading
    key = (disp, threading.get_ident())
    d = _CONN.get(key)
    if d is None:
        d = _CONN[key] = display.Display(disp)
    x0, y0, x1, y1 = rect
    try:
        raw = d.screen().root.get_image(x0, y0, x1 - x0, y1 - y0, X.ZPixmap, 0xffffffff)
    except Exception:
        _CONN.pop(key, None)
        d = _CONN[key] = display.Display(disp)
        raw = d.screen().root.get_image(x0, y0, x1 - x0, y1 - y0, X.ZPixmap, 0xffffffff)
    return Image.frombytes("RGB", (x1 - x0, y1 - y0), raw.data, "raw", "BGRX")


def _trim(rows):
    """Cut a glyph bitmap (list of row strings) down to its lit bounding box."""
    rows = [r for r in rows]
    while rows and "#" not in rows[0]:
        rows.pop(0)
    while rows and "#" not in rows[-1]:
        rows.pop()
    if not rows:
        return ""
    cols = [c for c in range(len(rows[0])) if any(r[c] == "#" for r in rows)]
    return "|".join(r[cols[0]:cols[-1] + 1] for r in rows)


def segment(im):
    """Split the label into glyph bitmaps, left to right, each trimmed to its own outline.

    Only the band where the digits are (the densest DIGIT_H rows, plus the comma's tail) is used:
    in bright areas (Icicle snow) the minimap's edge puts white pixels just above the label, and
    those used to merge into the glyphs ("1242" was read as "242")."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    px = rgb.load()

    def white(x, y):   # the digits are pure white; snow and the minimap edge are bluish
        r, g, b = px[x, y]
        return min(r, g, b) > LIT and max(r, g, b) - min(r, g, b) < 30

    lit = [[white(x, y) for x in range(w)] for y in range(h)]
    counts = [sum(r) for r in lit]
    if not any(counts):
        return []
    best = max(range(max(1, h - DIGIT_H + 1)), key=lambda t: sum(counts[t:t + DIGIT_H]))
    top, bottom = best, min(h - 1, best + DIGIT_H + 1)
    cols = [any(lit[y][x] for y in range(top, bottom + 1)) for x in range(w)]
    out, x = [], 0
    while x < w:
        if not cols[x]:
            x += 1
            continue
        start = x
        while x < w and cols[x]:
            x += 1
        rows = ["".join("#" if lit[y][c] else "." for c in range(start, x)) for y in range(top, bottom + 1)]
        out.append(_trim(rows))
    return out


def _tesseract(im):
    votes = Counter()
    for scale in (3, 4, 5, 6):
        for thr in (120, 150, 180):
            g = ImageOps.grayscale(im).resize((im.width * scale, im.height * scale), Image.LANCZOS)
            g = g.point(lambda v, t=thr: 0 if v > t else 255)
            b = io.BytesIO()
            g.save(b, "PNG")
            out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "7",
                                  "-c", "tessedit_char_whitelist=0123456789,"],
                                 input=b.getvalue(), capture_output=True).stdout.decode()
            txt = re.sub(r"\s", "", out)
            if COORD.match(txt):
                votes[txt] += 1
    if not votes:
        return None, 0
    text, n = votes.most_common(1)[0]
    return text, n


def read(disp, origin=(8, 37)):
    ox, oy = origin
    x0, y0, x1, y1 = LABEL
    im = grab(disp, (ox + x0, oy + y0, ox + x1, oy + y1))
    with _lock:
        glyphs = _load()
    parts = segment(im)
    text = "".join(glyphs.get(p, "?") for p in parts)
    if "?" in text or not COORD.match(text):
        import time
        with _lock:
            if time.time() - _last_tesseract[0] < TESSERACT_EVERY:
                return None
            _last_tesseract[0] = time.time()
        voted, n = _tesseract(im)
        if not voted:
            return None
        # learn only from a clear majority (8 of 12 readings), one glyph per character
        if n >= 8 and len(voted) == len(parts):
            with _lock:
                changed = False
                for p, ch in zip(parts, voted):
                    if glyphs.get(p) != ch:
                        glyphs[p] = ch
                        changed = True
                if changed:
                    _save()
        text = voted
    m = COORD.match(text)
    return (int(m.group(1)), int(m.group(2))) if m else None


if __name__ == "__main__":
    for disp in sys.argv[1:] or [":61", ":62", ":63", ":64"]:
        print(disp, read(disp))
