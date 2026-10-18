#!/usr/bin/env python3
"""Quest helpers: NPC list navigation, talking to NPCs, dialog choices, selling, quest log.

    mbquest.py STATE MODE ARGS... DISPLAY...

Everything here acts on one window at a time with direct input (xdotool into that display). Direct
input into a follower is never mirrored; for window 1 the broadcaster is paused while we act
(pid-file pause, released automatically if this process dies).

UI facts (BPO, 1366x768, measured 2026-10-08):
- Alt+Q: Quest Log. The objective text names the NPC or monster and its [x,y] coordinates.
- Minimap side icon 2 (1222,70): "NPCs & Monsters Coordinates" list. With Auto Trace ticked,
  clicking an entry walks the character to it.
- NPC under the cursor: cursor CRC 2940367208 (talk). Clicking opens "Dialogue" with blue options.
- Shop (Trade): items are sold by dragging them from the inventory onto the shop panel with
  RELATIVE motion (DirectInput sees no absolute jumps), then "Trade" in the quantity dialog.
"""
import os
import re
import sys
import time

import numpy as np
from PIL import Image

import mbcoords
import mbtarget as t

import threading

t.OCR_TIMEOUT = 20                     # 5 windows OCR at once on a laptop CPU
t.OCR_SLOTS = threading.Semaphore(3)

TALK_FRAMES = {2940367208, 1900147406, 2961211603, 590472619, 1968590699, 3347853258}   # animated, 6 frames
ENTITY_HOVER = 558161692    # shown first over any NPC/mob/player; the real cursor follows ~0.15 s later


def is_talk(d):
    """The cursor is (or turns into) the NPC talk cursor at its current position."""
    c = t.cursor_id(d)
    if c in TALK_FRAMES:
        return True
    if c != ENTITY_HOVER:
        return False
    for _ in range(6):                          # something is here: wait for its own cursor
        time.sleep(0.05)
        c = t.cursor_id(d)
        if c in TALK_FRAMES:
            return True
        if c != ENTITY_HOVER:
            return False
    return False
NPC_LIST_ICON = (1222, 70)
STALL_ICON = (1222, 147)          # hide player stalls (fewer labels and models in town)
HERE = os.path.dirname(os.path.abspath(__file__))


class Pause:
    """Pause the broadcaster while acting in window 1 (its input would be mirrored)."""
    def __init__(self, state, display_name):
        self.path = os.path.join(state, f"pause.{os.getpid()}") if display_name.endswith(":61") or \
            display_name == ":61" else None

    def __enter__(self):
        if self.path:
            t.write(self.path, "1\n")
            time.sleep(0.25)

    def __exit__(self, *a):
        if self.path and os.path.exists(self.path):
            os.remove(self.path)


def say(msg):
    print(msg, file=sys.stderr, flush=True)
    st = os.environ.get("MB_STATE")
    if st:                                      # the activity panel (📜) shows kill.log
        try:
            with open(os.path.join(st, "kill.log"), "a") as f:
                f.write(time.strftime("%H:%M:%S ") + "quest: " + msg + "\n")
        except OSError:
            pass


def click(d, x, y):
    t.click_at(d, t.ORIGIN[0] + x, t.ORIGIN[1] + y)


def drag(d, x0, y0, x1, y1, steps=10):
    """Drag in the game: press, then RELATIVE motion (absolute jumps aren't seen as a drag)."""
    O = t.ORIGIN
    t.key(d, "mousemove", str(O[0] + x0), str(O[1] + y0), "sleep", "0.2", "mousedown", "1", "sleep", "0.25")
    dx, dy = (x1 - x0) / steps, (y1 - y0) / steps
    for _ in range(steps):
        t.key(d, "mousemove_relative", "--", str(round(dx)), str(round(dy)), "sleep", "0.04")
    t.key(d, "sleep", "0.3", "mouseup", "1")


def alt(d, k):
    chat_off(d)
    t.key(d, "keydown", "alt", "sleep", "0.05", "key", k, "sleep", "0.05", "keyup", "alt")


def chat_off(d):
    """If the chat input is active (keys would be typed into chat), clear it and close it."""
    for _ in range(2):
        if t.find_image(t.screenshot(d), "mbchatidle.png"):
            return True
        t.key(d, *(["key", "BackSpace"] * 30), "key", "Return")   # an empty line just closes it
        time.sleep(0.5)
    return bool(t.find_image(t.screenshot(d), "mbchatidle.png"))


def close_all(state, i, d):
    """Close every dialog with a title-bar (x). Never Escape: with nothing open it opens the
    system menu (Logout / Exit Game). A skill left armed by the hunt (Lightning Bolt cursor)
    turns every NPC click into a cast attempt: it is dropped too."""
    r = t.close_popups(state, i, d)
    if t.cursor_id(d) in t.SKILL_FRAMES:
        t.clear_selection(state, d)
    return r


CHAT_AREA = (0, 578, 290, 740)


def stall_signs(im):
    """Number of player-stall signboards (beige boxes with text) in the world view."""
    import cv2
    x0, y0, x1, y1 = LABEL_AREA
    a = np.asarray(im.crop(LABEL_AREA).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    beige = ((r > 225) & (g > 215) & (b > 185) & (r - b > 10) & (r - b < 50)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(beige, connectivity=8)
    return sum(1 for k in range(1, n) if 50 <= st[k][2] <= 320 and 14 <= st[k][3] <= 34 and st[k][4] > 500)


def hide_stalls(d):
    """Player stalls (shop signs + their sitting characters) crowd towns and cover NPCs. The
    minimap icon toggles them; keep the state with fewer signboards (none, outside towns)."""
    before = stall_signs(t.screenshot(d))
    if before < 2:
        return True                             # hidden already (or no stalls around)
    click(d, *STALL_ICON)
    time.sleep(1.2)
    after = stall_signs(t.screenshot(d))
    if after > before:                          # we just turned them ON: toggle back
        click(d, *STALL_ICON)
        time.sleep(1.2)
        after = stall_signs(t.screenshot(d))
    return after < 2


# --- NPC list ------------------------------------------------------------------------------------
def open_npc_list(d):
    im = t.screenshot(d)
    p = t.find_image(im, "mbnpclist.png")
    if p:
        return p
    click(d, *NPC_LIST_ICON)
    time.sleep(1.0)
    return t.find_image(t.screenshot(d), "mbnpclist.png")


def npc_list_goto(d, name, pages=12):
    """Click `name` in the NPC list (Auto Trace walks there). Returns True if clicked."""
    title = open_npc_list(d)
    if not title:
        return False
    tx, ty = title
    list_box = (tx - 175, ty + 90, tx + 120, ty + 400)          # the NPC Name column, all rows
    t.move(d, t.ORIGIN[0] + tx, t.ORIGIN[1] + ty + 200)
    t.key(d, *(["click", "4"] * 40))                            # to the top
    time.sleep(0.4)
    words = [w for w in re.split(r"[\s\-]+", name) if len(w) >= 3]
    key = max(words, key=len) if words else name
    for _ in range(pages):
        im = t.screenshot(d)
        box = t.find_text(im.crop(list_box), [key])
        if box:
            x, y, w, h = box
            click(d, list_box[0] + x + w // 2, list_box[1] + y + h // 2)
            return True
        t.move(d, t.ORIGIN[0] + tx, t.ORIGIN[1] + ty + 200)
        t.key(d, *(["click", "5"] * 5))
        time.sleep(0.4)
    return False


NPC_DB = os.path.join(HERE, "npcs.json")


def _column_rows(img, row_h, scale=3, whitelist=None):
    """OCR one column strip; {row index: text}, rows by the words' y position."""
    import io
    import subprocess
    b = io.BytesIO()
    img.convert("L").resize((img.width * scale, img.height * scale)).save(b, format="PNG")
    cmd = ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"]
    if whitelist:
        cmd[5:5] = ["-c", f"tessedit_char_whitelist={whitelist}"]
    out = subprocess.run(cmd, input=b.getvalue(), capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    rows = {}
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and f[11].strip():
            y = (int(f[7]) + int(f[9]) / 2) / scale
            rows.setdefault(int(y // row_h), []).append((int(f[6]), f[11]))
    return {k: " ".join(w for _, w in sorted(v)) for k, v in rows.items()}


NPC_ROW0, NPC_ROW_H, NPC_ROWS = 89, 20, 15     # first row centre below the list title; pitch


def scan_npc_list(d, pages=40):
    """OCR every row of the NPC list -> [{name, area, x, y}] (saved to npcs.json). Row by row on
    the list's 20 px grid: name+area as one line, the coordinates separately as digits."""
    import json
    title = open_npc_list(d)
    if not title:
        return []
    tx, ty = title
    t.move(d, t.ORIGIN[0] + tx, t.ORIGIN[1] + ty + 200)
    t.key(d, *(["click", "4"] * 60))
    time.sleep(0.4)
    rows, last = {}, None
    for _ in range(pages):
        im = t.screenshot(d)
        page = []
        for r in range(NPC_ROWS):
            y = ty + NPC_ROW0 + r * NPC_ROW_H
            text = t.ocr(im.crop((tx - 120, y - 9, tx + 190, y + 9)), 7, 3)
            xy = t.ocr(im.crop((tx + 200, y - 9, tx + 300, y + 9)), 7, 3, "0123456789,<>")
            m = re.search(r"(\d{3,4})\s*,\s*(\d{3,4})", xy)
            a = re.search(r"[<({\[](.+?)[>)}\]]?\s*$", text)
            if not m or not a:
                continue
            nm = text[:a.start()].strip()
            nm = re.sub(r"^(?:[^A-Za-z]*[A-Za-z0-9]{0,2}\s+)(?=[A-Z0-9])", "", nm)
            nm = nm.replace("\u2014", "-").replace("\u2013", "-").strip(" -")
            area = a.group(1).strip(" <>{}()[]")
            rows[(nm, int(m.group(1)), int(m.group(2)))] = area
            page.append(nm)
        if page == last:
            break
        last = page
        t.move(d, t.ORIGIN[0] + tx, t.ORIGIN[1] + ty + 200)
        t.key(d, *(["click", "5"] * 5))
        time.sleep(0.4)
    out = [{"name": n, "area": a, "x": x, "y": y} for (n, x, y), a in rows.items()]
    with open(NPC_DB, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    return out


def npc_at(xy, radius=6):
    """Name of the listed NPC at (or next to) xy, or None."""
    import json
    try:
        with open(NPC_DB) as f:
            db = json.load(f)
    except (OSError, ValueError):
        return None
    best = min(db, key=lambda r: max(abs(r["x"] - xy[0]), abs(r["y"] - xy[1])), default=None)
    if best and max(abs(best["x"] - xy[0]), abs(best["y"] - xy[1])) <= radius:
        return best["name"]
    return None


def walk_to(state, d, x, y, near=3, limit=180):
    """Wait until the character is within `near` tiles of (x, y) (after an auto-trace)."""
    t0, last, still = time.monotonic(), None, 0
    while time.monotonic() - t0 < limit:
        p = mbcoords.read(d)
        if p and max(abs(p[0] - x), abs(p[1] - y)) <= near:
            return True
        still = still + 1 if p == last else 0
        last = p
        if still >= 6:
            return False
        time.sleep(1)
    return False


# --- talking ---------------------------------------------------------------------------------------
def find_talk(d, cx=683, cy=384, radius=230, step=9, skip=()):
    """Screen point near (cx, cy) where the talk cursor shows (an NPC), spiralling outward.
    skip: points of NPCs already tried (the wrong one): anything within 45 px is passed over."""
    pts = [(cx, cy)]
    for r in range(step, radius + 1, step):
        for k in range(max(8, r // 4)):
            a = 2 * np.pi * k / max(8, r // 4)
            pts.append((int(cx + r * np.cos(a)), int(cy - 20 + r * np.sin(a) * 0.8)))
    for x, y in pts:
        if any((x - a) ** 2 + (y - b) ** 2 < 45 ** 2 for a, b in skip):
            continue
        t.move(d, t.ORIGIN[0] + x, t.ORIGIN[1] + y)
        time.sleep(0.06)
        if is_talk(d):
            return x, y
    return None


LABEL_AREA = (200, 90, 1210, 640)
# Default camera (Home), measured 2026-10-08 on Tailor Nila and Blacksmith Goldie from 3 tiles away:
# the NPC's body centre on screen = FEET + dx * TILE_X + dy * TILE_Y (dx, dy = NPC tile - ours).
FEET, TILE_X, TILE_Y = (683, 395), (25.0, -5.7), (7.0, 19.7)


def predicted(here, npc):
    dx, dy = npc[0] - here[0], npc[1] - here[1]
    return (FEET[0] + dx * TILE_X[0] + dy * TILE_Y[0], FEET[1] + dx * TILE_X[1] + dy * TILE_Y[1])


def probe_around(d, p, radius=70, step=6, skip=()):
    """Spiral outward from screen point p for the talk cursor."""
    pts = [(0, 0)]
    for r in range(step, radius + 1, step):
        for dx in range(-r, r + 1, step):
            pts += [(dx, -r), (dx, r)]
        for dy in range(-r + step, r, step):
            pts += [(-r, dy), (r, dy)]
    for dx, dy in pts:
        x, y = int(p[0] + dx), int(p[1] + dy)
        if not (210 <= x <= 1200 and 100 <= y <= 660):
            continue
        if any((x - a) ** 2 + (y - b) ** 2 < 45 ** 2 for a, b in skip):
            continue
        t.move(d, t.ORIGIN[0] + x, t.ORIGIN[1] + y)
        time.sleep(0.06)
        if is_talk(d):
            return x, y
    return None


def find_labels(im, name):
    """Screen boxes (x, y, w, h) of world label words that fuzzy-match a word of `name` (NPC
    labels are white text with a dark outline: OCR'd on a white-pixel mask, 2x)."""
    import io
    import subprocess
    x0, y0, x1, y1 = LABEL_AREA
    a = np.asarray(im.crop(LABEL_AREA).convert("RGB")).astype(int)
    m = (a.min(2) > 215) & (a.max(2) - a.min(2) < 30)
    from PIL import Image
    mask = Image.fromarray(np.where(m, 0, 255).astype("uint8"))
    mask = mask.resize((mask.width * 2, mask.height * 2))
    b = io.BytesIO()
    mask.save(b, format="PNG")
    with (t.OCR_SLOTS or threading.Semaphore(1)):
        out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=b.getvalue(),
                             capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    words = [w for w in re.split(r"[\s\-\u2013\u2014]+", name) if len(w) >= 4]
    hits = []
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and len(f[11].strip()) >= 3:
            if any(fuzzy_in(w, f[11], 0.75) or fuzzy_in(f[11], w, 0.75) for w in words):
                X, Y, W, H = (int(v) // 2 for v in f[6:10])
                hits.append((x0 + X, y0 + Y, W, H))
    return hits


def probe_under(d, box, skip=()):
    """Dense probe below a label box for the talk cursor."""
    x, y, w, h = box
    cx = x + w // 2
    for dy in range(h + 6, h + 48, 6):
        for dx in (0, -6, 6, -12, 12, -18, 18, -26, 26, -34, 34):
            px, py = cx + dx, y + dy
            if any((px - a) ** 2 + (py - b) ** 2 < 45 ** 2 for a, b in skip):
                continue
            t.move(d, t.ORIGIN[0] + px, t.ORIGIN[1] + py)
            time.sleep(0.07)
            if is_talk(d):
                return px, py
    return None


def talk(state, d, cx=683, cy=384, expect=None, tries=5, npc_xy=None):
    """Click the NPC nearest the given screen point; returns the Dialogue title position. With
    expect (an NPC name), a dialog from another NPC is closed and the next NPC is tried."""
    tried = []
    for _ in range(tries):
        pt = None
        here = mbcoords.read(d)
        guess = predicted(here, npc_xy) if (npc_xy and here) else None
        if expect:                              # under the NPC's own name label (exact, fast)
            boxes = find_labels(t.screenshot(d), expect)
            if guess:                           # the label nearest the predicted spot first
                boxes.sort(key=lambda b: (b[0] - guess[0]) ** 2 + (b[1] - guess[1]) ** 2)
            for box in boxes[:4]:
                pt = probe_under(d, box, skip=tried)
                if pt:
                    break
        if not pt and guess:                    # where the NPC must be (default camera); the
            dist = max(abs(npc_xy[0] - here[0]), abs(npc_xy[1] - here[1]))   # error grows with distance
            pt = probe_around(d, guess, radius=min(120, 40 + 10 * dist), step=8, skip=tried)
        if not pt and not (npc_xy or expect):   # nothing known: nearest NPC around us
            pt = find_talk(d, cx, cy, skip=tried)
        if not pt:
            return None
        click(d, *pt)
        title = None
        for _ in range(10):
            time.sleep(0.4)
            title = t.find_image(t.screenshot(d), "mbdialogue.png")
            if title:
                break
        if not title:
            tried.append(pt)
            continue
        if not expect:
            return title
        _, lines = dialog_text(d, title)
        header = " ".join(l for l, _ in lines[:3])
        parts = [p for p in re.split(r"\s*[-\u2013\u2014]\s*", expect) if len(p) >= 5]
        if not parts or any(fuzzy_in(p, header) for p in parts) or fuzzy_in(expect, header):
            return title                    # title OR name matching is enough (OCR slips)
        say(f"talked to the wrong NPC ({header.strip()[:40]!r}), looking for {expect!r}")
        t.close_popups(state, int(d.lstrip(":")) - 61, d)
        tried.append(pt)
    return None


def dialog_text(d, title):
    """(full text, [(line text, (x, y))]) of the open Dialogue: every OCR'd line with its screen
    position. Options (Trade, quest names with ! / ? icons, Next, Accept...) are among them."""
    import io
    import subprocess
    tx, ty = title
    box = (tx - 140, ty + 40, tx + 140, ty + 290)
    im = t.screenshot(d).crop(box)
    b = io.BytesIO()
    im.convert("L").resize((im.width * 2, im.height * 2)).save(b, format="PNG")
    out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6", "tsv"], input=b.getvalue(),
                         capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    rows = {}
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        key = (int(f[2]), int(f[3]), int(f[4]))
        r = rows.setdefault(key, {"words": [], "x": int(f[6]) // 2, "y": int(f[7]) // 2, "h": int(f[9]) // 2})
        r["words"].append(f[11])
    lines = [(" ".join(r["words"]), (box[0] + r["x"] + 30, box[1] + r["y"] + max(5, r["h"] // 2)))
             for r in sorted(rows.values(), key=lambda r: r["y"])]
    return "\n".join(l for l, _ in lines), lines


def fuzzy_in(needle, text, cutoff=0.8):
    """needle appears in text, allowing OCR slips (Welcome/Helcome, Blacksnith's Greeti.../
    Blacksmith' s Greetings): letters only, best same-length window of text."""
    import difflib
    words = re.sub(r"[^\w]", " ", text.lower()).split()
    if len(needle) <= 4:                     # short words ("OK", "Next"): whole words only
        return needle.lower() in words
    n = re.sub(r"[^a-z0-9]", "", needle.lower())
    h = re.sub(r"[^a-z0-9]", "", text.lower())
    if not n or not h:
        return False
    if n in h:
        return True
    L = len(n)
    best = max(difflib.SequenceMatcher(None, n, h[j:j + L]).ratio() for j in range(max(1, len(h) - L + 1)))
    return best >= cutoff


def choose(d, title, keywords, skip_first=1):
    """Click the first dialog line containing one of keywords (case-insensitive), ignoring the
    header line (the NPC's name). Returns the clicked line or None."""
    _, lines = dialog_text(d, title)
    lines = lines[skip_first:]
    for kw in keywords:
        for text, (x, y) in lines:
            if fuzzy_in(kw, text):
                click(d, x, y)
                time.sleep(0.9)
                return text
    return None


def choose_exact(d, title, option, skip_first=1, need=0.8):
    """Click the dialog line that best matches the WHOLE option text ("Teleport: Haven" must not
    pick "Teleport: Abandon Mine Haven", which fuzzy_in allowed). Returns the line or None."""
    import difflib
    _, lines = dialog_text(d, title)
    best, score = None, 0.0
    for text, xy in lines[skip_first:]:
        r = difflib.SequenceMatcher(None, option.lower(), " ".join(text.split()).lower()).ratio()
        if r > score:
            best, score = (text, xy), r
    if best and score >= need:
        click(d, *best[1])
        time.sleep(1.0)
        return best[0]
    say(f"no dialog line like {option!r} (best {best[0] if best else None!r}, {score:.2f})")
    return None


def teleport(state, i, d, npc_xy, npc, options, wait=12, tries=3):
    """teleport_once up to `tries` times, each from another side of the NPC (crowds, look-alike
    NPCs next to it: Geliya beside Meiya, our own characters around Wendy)."""
    sides = next((v for k, v in NPC_SIDES.items() if k in npc), [(4, 4), (-4, 4), (4, -4), (-4, -4)])
    for k, off in enumerate(sides[:tries]):
        r = teleport_once(state, i, d, npc_xy, npc, options, wait, off)
        if r:
            return r
        say(f"window {i + 1}: {npc}: attempt {k + 1} failed")
    return False


# open ground beside each NPC, best first (east of Meiya are tents: the tank got wedged there)
NPC_SIDES = {"Meiya": [(-4, 4), (-5, 0), (-4, -4), (0, 5)],
             "Wendy": [(-4, 4), (-4, -4), (4, 4), (4, -4)]}   # (4, 4) puts Nurse Laura in the way


def teleport_once(state, i, d, npc_xy, npc, options, wait=12, off=(4, 4)):
    """Walk beside a teleporter NPC, talk, click the exact options in order; True once the
    character's coordinates jump (a teleport, or the dialog step finished for non-teleports)."""
    with Pause(state, d):
        close_all(state, i, d)
        chat_off(d)
        t.trace(d, npc_xy[0] + off[0], npc_xy[1] + off[1])   # not on the NPC: we'd cover it
        last, still = None, 0
        for _ in range(60):
            time.sleep(1.0)
            here = mbcoords.read(d)
            if here and abs(here[0] - npc_xy[0]) <= 6 and abs(here[1] - npc_xy[1]) <= 6:
                break
            still = still + 1 if here == last else 0
            last = here
            if still >= 6:                      # not walking (blocked): try talking from here
                break
        before = mbcoords.read(d)
        title = talk(state, d, expect=npc, npc_xy=npc_xy)
        if not title and before:                         # probe around where the NPC must be
            pt = find_talk(d, *map(int, predicted(before, npc_xy)), radius=60, step=5)
            if pt:
                click(d, *pt)
                time.sleep(1.2)
                title = find_in(t.screenshot(d), "mbdialogue.png", (300, 60, 1100, 300))
        if not title:
            say(f"window {i + 1}: couldn't talk to {npc}")
            return False
        for opt in options:
            if not choose_exact(d, title, opt):
                close_all(state, i, d)
                return False
            time.sleep(0.6)
        for _ in range(wait):
            time.sleep(1.0)
            here = mbcoords.read(d)
            if here and before and max(abs(here[0] - before[0]), abs(here[1] - before[1])) > 30:
                say(f"window {i + 1}: {' > '.join(options)}: now at {here}")
                return True
        close_all(state, i, d)
        return "Record" in options[-1]          # recording a spawn point doesn't move us


# --- selling --------------------------------------------------------------------------------------
INV_SLOT0 = (-9, 76)      # first slot, relative to the "Inventory" title (template mbinvtitle.png)
INV_PITCH = 35
SHOP_DROP = (200, 300)    # a spot on the Item Trade panel (opens at the left)


def slot_state(im, x, y):
    """'item', 'empty' or 'locked' for an inventory slot centred at (x, y)."""
    import cv2
    crop = im.crop((x - 13, y - 13, x + 13, y + 13)).convert("RGB")
    lock = cv2.imread(os.path.join(HERE, "mblock.png"))
    a = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2BGR)
    if cv2.matchTemplate(a, lock, cv2.TM_CCOEFF_NORMED).max() > 0.8:
        return "locked"
    return "item" if np.asarray(crop).astype(int).std() > 28 else "empty"


def find_in(im, tpl, box, threshold=0.85):
    """Template centre inside box (x0, y0, x1, y1) only, or None."""
    p = t.find_image(im.crop(box), tpl, threshold)
    return (box[0] + p[0], box[1] + p[1]) if p else None


DIALOG_AREA = (450, 180, 1000, 620)   # where the sell quantity / confirm dialogs open (not the shop)


# Items never sold: crafting loot the user farms, chest gear (class requirement) and travel stuff.
# The user's rule: sell everything except Blurry Blueprints, NSS (New Sheepskin Scroll) and
# next-phase equipment (chests, and gear needing a higher level than the character has now).
KEEP_WORDS = [w for w in os.environ.get("MB_KEEP", "Blueprint|Sheepskin|Chest").split("|") if w]


def next_phase_gear(tip):
    """Gear the character can't wear yet (Level Requirement above its level, MB_LEVEL)."""
    m = re.search(r"Level Requirement:?\s*(\d+)", tip.replace("\n", " "), re.I)
    try:
        # also gear of the character's own 5-level band: fresh chest gear waiting in the bag was
        # sold (LuckyOne's level-25 Swift Kris, 2026-10-12)
        return bool(m) and int(m.group(1)) > int(os.environ.get("MB_LEVEL", "0")) - 5
    except ValueError:
        return False


def item_tooltip(d, x, y):
    """Text of the tooltip of the bag item at screen (x, y): hover, OCR the bright text only."""
    O = t.ORIGIN
    t.move(d, O[0] + 1100, O[1] + 650)
    time.sleep(0.25)
    t.move(d, O[0] + x, O[1] + y)
    time.sleep(0.7)
    im = t.screenshot(d)
    box = (x + 2, max(0, y - 20), min(t.GAME_W, x + 330), min(t.GAME_H, y + 230))
    a = np.asarray(im.crop(box).convert("RGB")).astype(int)
    m = a.max(2) > 150
    mask = Image.fromarray(np.where(m, 0, 255).astype("uint8"))
    try:
        return t.ocr(mask, 6, 2)
    finally:
        t.move(d, O[0] + 1100, O[1] + 650)


DELETE_JUNK = os.environ.get("MB_DELETE_JUNK", "1") == "1"   # user's OK (2026-10-10): junk the shop
MENU_DELETE = (17, 52)                                         # won't buy / pays 0$ for is deleted


def delete_item(d, x, y):
    """Right-click the bag item at (x, y) -> Delete -> Confirm. True if the slot emptied."""
    O = t.ORIGIN
    t.move(d, O[0] + x, O[1] + y)
    time.sleep(0.3)
    t.key(d, "mousedown", "3", "sleep", "0.1", "mouseup", "3")
    time.sleep(0.8)
    click(d, x + MENU_DELETE[0], y + MENU_DELETE[1])
    time.sleep(0.8)
    t.move(d, O[0] + 1100, O[1] + 650)
    time.sleep(0.3)
    im = t.screenshot(d)
    c = find_in(im, "mbconfirmbtn.png", (560, 320, 800, 450))
    if not c:
        return False
    click(d, *c)
    time.sleep(0.8)
    return slot_state(t.screenshot(d), x, y) == "empty"


def sell_all(state, d, skip=()):
    """With the shop open: drag every inventory item onto it and confirm. Items the shop doesn't
    take, or would take for 0$ (chests, starter books: likely useful), are kept. Returns count sold."""
    sold, tries, skip, again = 0, 0, set(skip), {}
    while tries < 60:
        im = t.screenshot(d)
        inv = t.find_image(im, "mbinvtitle.png")
        if not inv:
            say("sell: inventory not open")
            return sold
        target = None
        for r in range(6):
            for c in range(8):
                x = inv[0] + INV_SLOT0[0] + c * INV_PITCH
                y = inv[1] + INV_SLOT0[1] + r * INV_PITCH
                if (r, c) not in skip and slot_state(im, x, y) == "item":
                    target = (r, c, x, y)
                    break
            if target:
                break
        if not target:
            return sold
        r, c, x, y = target
        tries += 1
        if KEEP_WORDS:
            tip = item_tooltip(d, x, y)
            hit = [w for w in KEEP_WORDS if w.lower() in tip.lower()] or \
                (["next-phase gear"] if next_phase_gear(tip) else [])
            if hit:
                skip.add((r, c))
                say(f"sell: slot {r},{c} kept ({hit[0]}): {tip.splitlines()[0] if tip else ''!r}")
                continue
            im = t.screenshot(d)
        before = np.asarray(im.crop((x - 13, y - 13, x + 13, y + 13)).convert("L")).astype(int)
        drag(d, x, y, *SHOP_DROP)
        time.sleep(0.8)
        im2 = t.screenshot(d)
        trade = find_in(im2, "mbtradebtn.png", DIALOG_AREA)
        confirm = find_in(im2, "mbconfirmbtn.png", DIALOG_AREA)
        if trade:                                              # quantity dialog (has a price)
            click(d, *trade)
        elif confirm:                                          # "Do you wish to Sell ... Price"
            q = find_in(im2, "mbsellq.png", DIALOG_AREA)
            text = t.ocr(im2.crop((q[0] - 70, q[1] - 8, q[0] + 120, q[1] + 32)), 6, 3) if q else ""
            m = re.search(r"Price:\s*(\d+)", text)
            # "$" is often read as "%" or "3" ("0$" -> "03"): a price starting with 0 is zero
            if not q or not m or m.group(1).startswith("0"):
                cancel = find_in(im2, "mbcancelbtn.png", DIALOG_AREA)
                if cancel:
                    click(d, *cancel)
                time.sleep(0.6)
                if DELETE_JUNK and delete_item(d, x, y):
                    say(f"sell: slot {r},{c} worth 0$: deleted")
                    continue
                skip.add((r, c))
                say(f"sell: slot {r},{c} is worth 0$ (kept): {text.strip()!r}")
                continue
            click(d, *confirm)
        else:
            if DELETE_JUNK and delete_item(d, x, y):
                say(f"sell: slot {r},{c} can't be sold: deleted")
                continue
            skip.add((r, c))
            say(f"sell: slot {r},{c} can't be sold, kept")
            continue
        time.sleep(0.9)
        after = np.asarray(t.screenshot(d).crop((x - 13, y - 13, x + 13, y + 13)).convert("L")).astype(int)
        if np.abs(after - before).mean() < 3:
            skip.add((r, c))
            say(f"sell: slot {r},{c} didn't change after confirming, kept")
            continue
        sold += 1
        say(f"sell: sold slot {r},{c}")
        # the same slot "sold" again and again is an item the shop doesn't really take (an apple
        # ate 31 of the 40 tries; the old gear behind it was never reached): leave it
        again[(r, c)] = again.get((r, c), 0) + 1
        if again[(r, c)] >= 3:
            skip.add((r, c))
            say(f"sell: slot {r},{c} keeps coming back: left alone")
    return sold


def sell_everything(state, i, d, shop="Blacksmith - Goldie", shop_xy=(2193, 2706)):   # Jimberry is walled in by player stalls
    """Walk to the shop, open Trade, sell all, close the windows."""
    with Pause(state, d):
        close_all(state, i, d)
        chat_off(d)
        title = None
        for attempt in range(3):
            if True:
                # straight to the shop's tile (the NPC list sent some windows elsewhere)
                t.trace(d, shop_xy[0] - 1, shop_xy[1] + 2)
                for k in range(120):
                    time.sleep(1.0)
                    here = mbcoords.read(d)
                    if here and abs(here[0] - shop_xy[0]) <= 3 and abs(here[1] - shop_xy[1]) <= 4:
                        break
                    if k % 8 == 7:                # stood still (end of a safe-path hop): next hop
                        t.trace(d, shop_xy[0] - 1, shop_xy[1] + 2)
            close_all(state, i, d)
            time.sleep(0.5)
            title = talk(state, d)
            if title:
                break
            say(f"window {i + 1}: couldn't click {shop} (attempt {attempt + 1})")
        if not title:
            return -1
        if not choose(d, title, ["Trade"]):
            say(f"window {i + 1}: no Trade option")
            close_all(state, i, d)
            return -1
        time.sleep(1)
        if not t.find_image(t.screenshot(d), "mbinvtitle.png"):
            alt(d, "e")
            time.sleep(0.8)
        # the bag must not cover the shop panel (after a player trade it stays on the left, where
        # drops "onto the shop" land on the bag itself): drag it to its usual place by the title
        p = t.find_image(t.screenshot(d), "mbinvtitle.png")
        if p and p[0] < 300:
            drag(d, p[0], p[1], 342, 110, steps=20)
            time.sleep(0.6)
        n = sell_all(state, d)
        close_all(state, i, d)
        return n


# --- quest log ---------------------------------------------------------------------------------------
QLOG_LIST = (-511, 40, -271, 430)       # quest list panel, relative to the Quest Log's (x)
QLOG_OBJ = (-246, 288, -10, 362)        # Objectives text box
QLOG_DESC = (-246, 100, -10, 262)       # Description text box


def open_quest_log(state, i, d):
    im = t.screenshot(d)
    for _ in range(2):
        x = _qlog_x(im)
        if x:
            return x
        alt(d, "q")
        time.sleep(1.0)
        im = t.screenshot(d)
    return None


def _qlog_x(im):
    hit = t._find_close(im)
    if not hit:
        return None
    _, x, y = hit
    title = t.ocr(im.crop((max(0, x - 330), y - 9, x - 20, y + 9)), 7, 3).lower()
    return (x, y) if "log" in title or "quest" in title else None


def _box(anchor, rel):
    return (anchor[0] + rel[0], anchor[1] + rel[1], anchor[0] + rel[2], anchor[1] + rel[3])


def parse_objective(text):
    """{'raw', 'coords': (x, y) or None, 'target': name or None, 'count': (have, need) or None,
    'verb': talk/hunt/collect/deliver/other}"""
    flat = " ".join(text.split())
    out = {"raw": flat, "coords": None, "target": None, "count": None, "verb": "other"}
    m = re.search(r"\[\s*(\d{2,4})\s*[,.]\s*(\d{2,4})\s*\]", flat)
    if m:
        out["coords"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(?:for|to|Find|Visit|Hunt|Kill|Defeat|Slay|Collect|Gather|Obtain|Bring)\s+"
                  r"(?:\d+\s+)?(?:x\s*)?([A-Z][\w' ]*?(?:\s*[-–—]\s*[A-Z][\w']*)?)\s+(?:at|in|near|from)\b", flat)
    if m:
        out["target"] = m.group(1).strip()
    m = re.search(r"(\d+)\s*/\s*(\d+)", flat)
    if m:
        out["count"] = (int(m.group(1)), int(m.group(2)))
    low = flat.lower()
    if re.search(r"\b(hunt|kill|defeat|slay|eliminate)\b", low):
        out["verb"] = "hunt"
    elif re.search(r"\b(collect|gather|obtain|bring|get)\b", low):
        out["verb"] = "collect"
    elif re.search(r"\b(deliver|give|hand)\b", low):
        out["verb"] = "deliver"
    elif re.search(r"\b(look for|talk|speak|visit|find|report|meet|return to|go to)\b", low):
        out["verb"] = "talk"
    return out


COORD_RE = re.compile(r"(\d{3,4})\s*[,.]\s*(\d{3,4})")
DIGIT_FIX = str.maketrans({"T": "7", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8"})


def coords_in(text):
    """Coordinate pairs in OCR text, tolerating misread digits inside the [x,y] brackets."""
    # "Sennal2 223, 2785]": a space inside a number (digits, space, digits, then a comma)
    text = re.sub(r"(?<![\d,])(\d{1,3})\s(\d{1,3})(?=\s*[,.]\s*\d)", lambda m: m.group(1) + m.group(2)
                  if len(m.group(1) + m.group(2)) == 4 else m.group(0), text)
    fixed = re.sub(r"\[([^\]]{5,12})\]", lambda m: "[" + m.group(1).translate(DIGIT_FIX) + "]", text)
    # a pair whose "[" was lost: "Sennal 2223,2785]"
    fixed = re.sub(r"(?<!\d)(\d{3,4}\s*,\s*\w{3,4})\]", lambda m: " " + m.group(1).translate(DIGIT_FIX) + "]", fixed)
    return [(int(a), int(b)) for a, b in COORD_RE.findall(fixed)]


def read_objective(im, box):
    """Objective text plus coordinates, voted over several OCR variants (red text on beige:
    each variant misreads different digits)."""
    from collections import Counter
    c = im.crop(box)
    g = c.split()[1].convert("RGB")
    bw = c.split()[1].point(lambda v: 0 if v < 150 else 255).convert("RGB")
    texts = [t.ocr(img, 6, sc) for img in (c, g, bw) for sc in (3, 4)]
    lists = [coords_in(tx) for tx in texts]
    n = Counter(len(l) for l in lists).most_common(1)[0][0]
    coords = []
    for k in range(n):
        votes = Counter(l[k] for l in lists if len(l) == n)
        # a dropped digit ("223" for "2223") is a common slip: a reading that is another one
        # with a digit added in front wins over it
        def full(v):
            return sum(c for (a, b), c in votes.items()
                       if (str(a).endswith(str(v[0])) and len(str(a)) > len(str(v[0])) and b == v[1]) or
                          (str(b).endswith(str(v[1])) and len(str(b)) > len(str(v[1])) and a == v[0]))
        best = max(votes, key=lambda v: (votes[v] - full(v) * 2, len(str(v[0])) + len(str(v[1]))))
        coords.append(best)
    coords = [c for c in coords if 100 <= c[0] <= 4200 and 100 <= c[1] <= 4200 and
              len(str(c[0])) == len(str(c[1])) or min(c) >= 1000]
    best = max(texts, key=lambda tx: len(coords_in(tx)) == n and len(tx))
    return best, coords


def npc_names(text, n):
    """For each [x,y] in an objective, the NPC name written just before it ("Blacksmith - Goldie
    [2193,2706]", "Tailor - Granny Nila at [2267,2704]"): the last 'Title - Name' in the text
    between the previous coordinates and these. None where there is none."""
    flat = " ".join(text.split()).replace("\u2014", "-").replace("\u2013", "-")
    out, prev = [], 0
    for m in re.finditer(r"(\d{3,4})\s*[,.]\s*(\d{3,4})", flat):
        seg = flat[prev:m.start()]
        prev = m.end()
        hits = re.findall(r"([A-Z][\w']*(?:\s+[A-Z][\w']*)?\s*-\s*[A-Z][\w']*(?:\s+[A-Z][\w']+)?)", seg)
        out.append(hits[-1].strip() if hits else None)
    return (out + [None] * n)[:n]


def read_quests(state, i, d):
    """[(quest name, status, objective dict)] for every quest in the log (clicks each one)."""
    x = open_quest_log(state, i, d)
    if not x:
        return []
    im = t.screenshot(d)
    lb = _box(x, QLOG_LIST)
    import io
    import subprocess
    buf = io.BytesIO()
    crop = im.crop(lb)
    crop.convert("L").resize((crop.width * 2, crop.height * 2)).save(buf, format="PNG")
    tsv = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6", "tsv"], input=buf.getvalue(),
                         capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    rows = {}
    for line in tsv.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and f[11].strip():
            k = (f[2], f[3], f[4])
            rows.setdefault(k, [int(f[6]) // 2, int(f[7]) // 2, []])[2].append(f[11])
    quests, section = [], "primary"
    for xx, yy, words in sorted(rows.values(), key=lambda r: r[1]):
        line = " ".join(words)
        if "primary" in line.lower():
            section = "primary"
        elif "normal" in line.lower() or "daily" in line.lower() or "side" in line.lower():
            section = "normal"
        m = re.match(r"(.+?)\.{2,}.*\[(.+?)\]", line) or re.match(r"(.+?)\s*\[(In progress|Complete\w*)\]", line) \
            or re.match(r"(.+?)\s*\W?\s*(In ?progress|Complet\w*)", line)   # "[" misread on the highlight
        if not m:
            continue
        name, status = m.group(1).strip(" .-"), m.group(2)
        click(d, lb[0] + xx + 15, lb[1] + yy + 5)
        time.sleep(0.6)
        im2 = t.screenshot(d)
        obj, coords = read_objective(im2, _box(x, QLOG_OBJ))
        o = parse_objective(obj)
        o["coords_all"] = coords
        o["coords"] = coords[0] if coords else o["coords"]
        o["section"] = section
        o["npcs"] = npc_names(obj, len(coords))
        quests.append((name, status, o))
    return sorted(quests, key=lambda q: q[2]["section"] != "primary")


# --- quest engine --------------------------------------------------------------------------------
# Promotion quests are only taken when wanted (an Ami must not become a Herbalist by accident).
AVOID = ("promotion", "advance", "class change", "job change")


def quest_window(d):
    """('complete'|'accept'|'quest', (x, y) of its button) when a Quest window is open, else None."""
    im = t.screenshot(d)
    for tpl, kind in (("mbqcomplete.png", "complete"), ("mbqaccept.png", "accept")):
        p = t.find_image(im, tpl, 0.9)
        if p:
            return kind, p
    p = t.find_image(im, "mbqtitle.png", 0.9)
    return ("quest", p) if p else None


def quest_window_text(d):
    im = t.screenshot(d)
    p = t.find_image(im, "mbqtitle.png", 0.9)
    if not p:
        return ""
    return t.ocr(im.crop((p[0] - 12, p[1] + 25, p[0] + 195, p[1] + 350)), 6, 3)


def allowed(option, want=()):
    low = option.lower()
    if any(fuzzy_in(w, option) for w in want):
        return True
    return not any(a in low for a in AVOID)


def converse(state, i, d, prefer=(), want=(), max_steps=12):
    """Work through whatever an NPC offers: pick quest options (the quest log's quests first, then
    wanted ones, then any non-promotion quest), press Complete / Accept in Quest windows. Returns
    a list of what happened."""
    log, chain, picked = [], False, False
    for _ in range(max_steps):
        qw = quest_window(d)
        if qw:
            kind, p = qw
            text = quest_window_text(d)
            name = text.strip().splitlines()[0] if text.strip() else "?"
            if kind == "complete":
                click(d, *p)
                log.append(f"completed {name}")
                chain = True                    # the NPC's follow-up quest may be accepted
                time.sleep(1.2)
                continue
            if kind == "accept":
                in_log = any(fuzzy_in(n, name) or fuzzy_in(name[:12], n) for n in prefer if n)
                if (chain or in_log or picked or any(fuzzy_in(w, name) for w in want)) and (picked or allowed(name, want)):
                    click(d, *p)
                    log.append(f"accepted {name}")
                else:
                    log.append(f"declined {name}")
                    cp = t.find_image(t.screenshot(d), "mbqclose.png", 0.9)
                    if cp:
                        click(d, *cp)
                time.sleep(1.2)
                continue
            cp = t.find_image(t.screenshot(d), "mbqclose.png", 0.9)       # info only: close
            if cp:
                click(d, *cp)
                time.sleep(0.8)
                continue
        title = t.find_image(t.screenshot(d), "mbdialogue.png")
        if not title:
            break
        # a long NPC speech pushes the options below the fold: scroll the dialog to the bottom
        t.move(d, t.ORIGIN[0] + title[0], t.ORIGIN[1] + title[1] + 220)
        t.key(d, *(["click", "5"] * 6))
        time.sleep(0.5)
        _, lines = dialog_text(d, title)
        opts = [(txt, xy) for txt, xy in lines[1:] if len(txt) > 3 and not re.fullmatch(r"[\W_RBE ]+", txt)]
        pick = None
        for group in (prefer, want):
            for kw in group:
                for txt, xy in opts:
                    if fuzzy_in(kw, txt, 0.7):         # both sides are OCR (Weleome / Helcome)
                        pick = (txt, xy)
                        break
                if pick:
                    break
            if pick:
                break
        if not pick and chain:
            # right after completing a quest the NPC may offer its follow-up: take a quest-icon
            # option that isn't a class promotion
            for txt, xy in opts:
                if re.match(r"^[?!9gj]\s+\w", txt) and allowed(txt, want):
                    pick = (txt, xy)
                    break
        if not pick:
            for kw in ("Next", "Continue", "Yes", "OK", "Okay"):
                for txt, xy in opts:
                    if fuzzy_in(kw, txt) and len(txt) < 20:
                        pick = (txt, xy)
                        break
                if pick:
                    break
        if not pick:
            break
        click(d, *pick[1])
        log.append(f"chose {pick[0]!r}")
        picked = any(fuzzy_in(k, pick[0], 0.7) for k in tuple(prefer) + tuple(want))
        time.sleep(1.0)
    close_all(state, i, d)
    return log


STAND_OFFSETS = ((4, 0), (-4, 0), (4, 3), (-4, 3), (0, 4), (4, -3), (-4, -3), (5, 1), (-5, 1), (0, 5))
_npc_locks, _npc_locks_guard = {}, threading.Lock()


def npc_lock(xy):
    """One of our windows at a time talks to a given NPC (five of us crowd it and cover it)."""
    with _npc_locks_guard:
        return _npc_locks.setdefault(tuple(xy), threading.Lock())


def visit(state, i, d, xy, prefer=(), want=(), npc=None):
    """Auto-trace to an NPC at xy, talk to it (checking it's `npc` when given), converse."""
    close_all(state, i, d)
    chat_off(d)
    # stand beside the NPC, each window on its own tile: five of us on the NPC's tile cover it
    # (the cursor then shows our own players, not the NPC)
    k = i % len(STAND_OFFSETS)
    order = STAND_OFFSETS[k:] + STAND_OFFSETS[:k]
    for attempt in range(3):
        spot = None
        for dx, dy in order:                    # blocked tiles are refused: try the next spot
            if t.trace(d, xy[0] + dx, xy[1] + dy):
                spot = (xy[0] + dx, xy[1] + dy)
                break
        if spot is None:
            if not t.trace(d, *xy):
                say(f"window {i + 1}: auto-trace to {xy} refused")
                return []
            spot = xy
        if not walk_to(state, d, *spot, near=2, limit=240):
            t.trace(d, *spot)                   # stopped short (crowd, a click): walk on
            walk_to(state, d, *spot, near=2, limit=120)
        time.sleep(0.6)
        with npc_lock(xy):
            title = talk(state, d, expect=npc, npc_xy=xy)
            if title:
                return converse(state, i, d, prefer, want)
        say(f"window {i + 1}: no NPC to talk to at {xy} (attempt {attempt + 1})")
    return []


MAX_TRIP = int(os.environ.get("MB_MAX_TRIP", "350"))   # tiles; farther objectives need a group trip


def quest_step(state, i, d, want=()):
    """Do the next talk/deliver objective of this character. Returns (what, details) or None."""
    with Pause(state, d):
        quests = read_quests(state, i, d)
        close_all(state, i, d)
    here = mbcoords.read(d)
    for name, status, obj in quests:
        if here and obj["coords"] and max(abs(obj["coords"][0] - here[0]), abs(obj["coords"][1] - here[1])) > MAX_TRIP \
                and not any(fuzzy_in(w, name) for w in want):
            say(f"window {i + 1}: skipping {name!r}: {obj['coords']} is far ({MAX_TRIP}+ tiles), not a wanted quest")
            continue
        if obj["verb"] in ("talk", "deliver", "other") and obj["coords"]:
            # a hand-in: the LAST coordinates in the text are where to return
            coords = obj.get("coords_all") or [obj["coords"]]
            npcs = obj.get("npcs") or [None] * len(coords)
            xy = coords[0]
            with Pause(state, d):
                log = visit(state, i, d, xy, prefer=(name,), want=want, npc=npcs[0])
                if len(coords) > 1 and coords[-1] != xy:   # "...and then return to X": go on to X
                    log += visit(state, i, d, coords[-1], prefer=(name,), want=want, npc=npcs[-1])
            return ("talk", name, xy, log)
        if obj["verb"] in ("hunt", "collect"):
            return ("hunt", name, obj["coords"], obj)
    return None


def main(argv):
    state, mode = argv[1], argv[2]
    os.environ.setdefault("MB_STATE", state)
    if mode == "sell":
        displays = argv[3:]
        for i, d in enumerate(displays):
            if i in t.excluded(state):
                continue
            n = sell_everything(state, int(d.lstrip(":")) - 61, d)
            print(f"window {int(d.lstrip(':')) - 60}: sold {n}")
    elif mode == "quests":
        d = argv[3]
        i = int(d.lstrip(":")) - 61
        with Pause(state, d):
            for qn in read_quests(state, i, d):
                print(qn)
    elif mode == "step":         # one quest step in each window (parallel); prints the outcome
        from concurrent.futures import ThreadPoolExecutor
        want = tuple(w for w in os.environ.get("MB_WANT", "").split(",") if w)
        displays = argv[3:]
        def one(d):
            i = int(d.lstrip(":")) - 61
            try:
                return d, quest_step(state, i, d, want)
            except Exception as e:                  # noqa: BLE001 - report, keep the others going
                return d, ("error", repr(e))
        with ThreadPoolExecutor(len(displays)) as ex:
            for d, r in ex.map(one, displays):
                print(d, r)
    elif mode == "run":          # quest steps in every window until no talk steps are left
        from concurrent.futures import ThreadPoolExecutor
        want = tuple(w for w in os.environ.get("MB_WANT", "").split(",") if w)
        displays = argv[3:]
        def loop(d):
            i = int(d.lstrip(":")) - 61
            done, idle = [], 0
            with Pause(state, d):
                close_all(state, i, d)
                t.key(d, "key", "Home")             # default camera: NPC screen positions are predictable
                time.sleep(0.5)
                say(f"window {i + 1}: player stalls hidden: {hide_stalls(d)}")
            for n in range(30):
                try:
                    r = quest_step(state, i, d, want)
                except Exception as e:          # noqa: BLE001
                    say(f"window {i + 1}: step failed: {e!r}")
                    idle += 1
                    if idle >= 3:
                        break
                    continue
                if r is None:
                    say(f"window {i + 1}: no more quests to do here")
                    break
                if r[0] == "hunt":
                    say(f"window {i + 1}: next is a hunt: {r[1]} at {r[2]} ({r[3].get('raw', '')[:80]})")
                    done.append(r[:3])
                    break
                say(f"window {i + 1}: {r[1]}: {'; '.join(r[3]) or 'nothing happened'}")
                done.append(r[:3])
                idle = idle + 1 if not any("completed" in l or "accepted" in l for l in r[3]) else 0
                if idle >= 2:
                    say(f"window {i + 1}: {r[1]!r} isn't moving forward; stopping here")
                    break
            return d, done
        with ThreadPoolExecutor(len(displays)) as ex:
            for d, done in ex.map(loop, displays):
                print(d, done)
    elif mode == "scan-npcs":    # read the whole NPC list into npcs.json
        d = argv[3]
        with Pause(state, d):
            rows = scan_npc_list(d)
            close_all(state, int(d.lstrip(":")) - 61, d)
        print(len(rows), "NPCs")
        for r in rows[:200]:
            print(r)
    elif mode == "talk":
        d = argv[3]
        title = talk(state, d)
        print("dialogue at", title)
        if title:
            text, options = dialog_text(d, title)
            print(text)
            print(options)
    else:
        sys.exit(f"unknown mode {mode}")


if __name__ == "__main__":
    main(sys.argv)
