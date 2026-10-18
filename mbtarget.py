#!/usr/bin/env python3
"""Capture a mob label and issue one guarded click through the normal leader input path."""
import os
import re
import io
import subprocess
import sys
import threading
import time

import struct
from concurrent.futures import ThreadPoolExecutor
import zlib

import cv2
import numpy as np
from Xlib import display as xdisplay
from PIL import Image, ImageOps

import mbcoords

ORIGIN = (8, 37)
GAME_W, GAME_H = 1366, 768
TARGET_PANEL = (212, 0, 400, 68)
HP_CROP = (238, 20, 395, 43)
WORLD = (205, 110, 1210, 672)       # down to just above the skill bars
CHAT_BOX = (0, 575, 300, 768)        # chat panel, bottom left: never a mob
MIN_BAR_WIDTH = 20
MAX_BAR_WIDTH = 140
MAX_BAR_DRIFT = 90
CLICK_ABOVE_BAR = 14
PLAYER_BAR_X = GAME_W // 2
PLAYER_BAR_Y = GAME_H // 2 + 14


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def write(path, value):
    if path.endswith("target-status"):          # keep a timestamped trail in kill.log
        try:
            with open(os.path.join(os.path.dirname(path), "kill.log"), "a") as f:
                f.write(time.strftime("%H:%M:%S ") + value.rstrip("\n") + "\n")
        except OSError:
            pass
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"    # threads write at once
    with open(tmp, "w") as f:
        f.write(value)
    os.replace(tmp, path)


def screenshot(display_name):
    return mbcoords.grab(display_name, (ORIGIN[0], ORIGIN[1],
                                        ORIGIN[0] + GAME_W, ORIGIN[1] + GAME_H))


OCR_TIMEOUT = float(os.environ.get("OCR_TIMEOUT", "5"))   # 1.8 s timed out under load (another job used 8 cores)
OCR_SLOTS = None           # optional threading.Semaphore limiting concurrent tesseracts


def ocr(image, psm, scale=3, whitelist=None):
    scaled = ImageOps.grayscale(image).resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    scaled.save(buf, format="PNG")
    cmd = ["tesseract", "stdin", "stdout", "--psm", str(psm)]
    if whitelist:
        cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
    if OCR_SLOTS:
        with OCR_SLOTS:
            p = subprocess.run(cmd, input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    else:
        p = subprocess.run(cmd, input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    return p.stdout.decode("utf-8", "replace")


TARGET_HP_BAR = (255, 21, 396, 37)   # x0, y0, x1, y1 of the target panel's HP row
TARGET_SP_BAR = (250, 33, 398, 49)   # the target panel's SP row (monsters show a full blue bar)


def panel_present(im):
    """True when a target panel is shown. Its SP row is a long saturated blue bar (monsters: full
    SP). The old test (share of dark pixels) also fired on dark trees and shadows, so dead/absent
    targets read as '0 HP' and KILL counted kills that never happened."""
    x0, y0, x1, y1 = TARGET_SP_BAR
    a = np.asarray(im.crop((x0, y0, x1, y1)).convert("RGB")).astype(int)
    blue = (a[:, :, 2] > 150) & (a[:, :, 0] < 90) & (a[:, :, 1] < 170)
    return int(blue.sum(1).max()) >= 60

def hp_value(im):
    """Target HP as (percent, 100) from the red fill of the panel's HP bar; (0, 100) when it is
    empty (dead target still shown). No text recognition: the "0/90" OCR failed often, and a
    failed read was treated as "still in combat", so KILL refused to start."""
    x0, y0, x1, y1 = TARGET_HP_BAR
    a = np.asarray(im.crop((x0, y0, x1, y1)).convert("RGB")).astype(int)
    red = (a[:, :, 0] > 110) & (a[:, :, 1] < 95) & (a[:, :, 2] < 85)
    row = red[int(np.argmax(red.sum(1)))]
    xs = np.where(row)[0]
    if len(xs) <= 3:
        return (0, 100)
    return (max(1, round((xs.max() + 1) / (x1 - x0) * 100)), 100)


def in_combat(im):
    if not panel_present(im):
        return False
    hp = hp_value(im)
    # If the target panel is present but HP cannot be read reliably, fail closed.
    return hp is None or hp[0] > 0


def green_bar_candidates(im):
    """Return horizontal green health bars, independent of a mob's facing direction."""
    x0, y0, x1, y1 = WORLD
    rgb = np.asarray(im.convert("RGB"))[y0:y1, x0:x1]
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # The bar fill is a deeply saturated green, about (15, 130, 13). Grass in green areas (Belmont
    # Plains: ~(57, 121, 47)) passed the old looser test and merged with the bars into big blobs.
    green = ((g > 95) & (r < 45) & (b < 45) & (g > 2.5 * np.maximum(r, b))).astype(np.uint8)
    green[max(0, CHAT_BOX[1] - y0):, :max(0, CHAT_BOX[2] - x0)] = 0
    # Fill tiny rendering gaps without joining neighboring bars.
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(green, connectivity=8)
    bars = []
    for i in range(1, count):
        bx, by, width, height, _ = map(int, stats[i])
        if not MIN_BAR_WIDTH <= width <= MAX_BAR_WIDTH or not 2 <= height <= 8 or width < height * 5:
            continue
        cx = x0 + float(centers[i][0])
        cy = y0 + float(centers[i][1])
        # Bars of the party itself are kept: a monster fighting the group stands right there, and
        # the hover check (attack cursor) rejects the party's own bars.
        bars.append((cx, cy))
    return bars


# --- hover check -------------------------------------------------------------------------------
# The game shows a static cursor over ground and UI, other animated cursors over players and NPCs,
# and an animated attack cursor over a monster it can attack. A spot counts as "on the monster"
# when the attack cursor's main frames show up (CRC of the cursor image, measured on BPO 2026-10-07;
# a party member's body shows different frames).
ATTACK_FRAMES = {885427748, 917890159}   # (558161692 also shows over party members: not attack-only)
PLAYER_FRAMES = {1034542934, 2463071752}   # cursor over a player character
# Heights above the bar to hover, in this order. Mapped in Belmont (bees, forest spirits, shrubs):
# each mob is clickable in its own band, some only 8 px tall, anywhere from 14 to 62 px above the
# bar; 38-46 px hits most. So probe every 8 px, the likeliest first. (Guessing from the name
# label was wrong too often: overlapping labels.)
HOVER_DY = (38, 46, 30, 54, 22, 62, 14)
HUD_POINT = (100, 420)               # game coords: left side under the party frames
_dpys = {}


def _dpy(name):
    # one X connection per thread: python-xlib is not thread-safe (the hunter's per-window threads
    # sharing one died with "Request reply to unknown request")
    key = (name, threading.get_ident())
    if key not in _dpys:
        d = xdisplay.Display(name)
        d.xfixes_query_version()
        _dpys[key] = d
    return _dpys[key]


def cursor_id(name):
    c = _dpy(name).xfixes_get_cursor_image(_dpy(name).screen().root)
    px = [p & 0xffffffff for p in c.cursor_image]
    return zlib.crc32(struct.pack("%dI" % len(px), *px))


_in_world = {}


def move_fast(name, x, y):
    """XTEST motion on the open Xlib connection (~1 ms; xdotool costs ~45 ms a call). For probing
    inside the game world only: the 0.3 s 'coming from the HUD' wait of move() is not done here."""
    from Xlib import X
    from Xlib.ext import xtest
    dpy = _dpy(name)
    xtest.fake_input(dpy, X.MotionNotify, x=int(x), y=int(y))
    dpy.sync()


def move(name, x, y):
    # xdotool (XTEST motion): the game updates its hover/attack cursor for that. A plain
    # XWarpPointer moved the pointer but the game never re-picked what's under it.
    subprocess.run(["xdotool", "mousemove", str(int(x)), str(int(y))],
                   env={**os.environ, "DISPLAY": name}, check=False)
    # Coming from the HUD/UI, the game ignores the world under the cursor for ~0.3 s (measured
    # 2026-10-08: 0/40 attack cursors right after, 4/4 after 0.3 s). Wait once on entering it.
    gx, gy = x - ORIGIN[0], y - ORIGIN[1]
    inside = WORLD[0] - 60 <= gx <= WORLD[2] + 60 and WORLD[1] - 60 <= gy <= WORLD[3]
    if inside and not _in_world.get(name, False):
        time.sleep(0.3)
    _in_world[name] = inside


def plain_cursor(name):
    move(name, ORIGIN[0] + HUD_POINT[0], ORIGIN[1] + HUD_POINT[1])
    time.sleep(0.08)
    return cursor_id(name)


def label_dys(im, bx, by):
    """Heights to probe for the bar at (bx, by), from its white name label: the body is between the
    label and the bar, usually 12-22 px under the label. None if there's no white label above."""
    if im is None:
        return None
    a = np.asarray(im.crop((int(bx) - 45, max(0, int(by) - 95), int(bx) + 45, int(by) - 13)).convert("RGB")).astype(int)
    white = (a.min(2) > 200) & (a.max(2) - a.min(2) < 35)
    rows = np.where(white.sum(1) >= 6)[0]
    if not len(rows):
        return None
    gap = (a.shape[0] - 1 - rows.max()) + 13                # bar to label bottom, px
    dys = [gap - o for o in (14, 22, 7, 30, 38) if 5 <= gap - o]
    return tuple(dict.fromkeys(dys)) or None


def hover_point(name, bx, by, plain, first_dy=None, im=None, patient=False, wide=False):
    """Screen point above bar (bx, by) where the attack cursor shows, or None. first_dy: height
    to try first (the same mob type has its body at the same height in every window). im: the
    screenshot the bar came from, to aim under its name label."""
    base = HOVER_DY
    if first_dy is not None:                    # the leader's height, then its neighbours
        near_first = (first_dy, first_dy + 8, first_dy - 8)
        order = near_first + tuple(d for d in base if d not in near_first)
    else:
        order = base
    # straight above the bar first; small mobs (Piglets) are narrow and often stand a little to
    # one side of their bar's centre, so then the same heights 8 px left and right
    # (wide: a second pass for when the straight one found nothing; it costs ~1 s per bar)
    points = [(0, dy) for dy in order] + ([(sx, dy) for sx in (-8, 8) for dy in order[:5]] if wide else [])
    for dx, dy in points:
        x, y = ORIGIN[0] + bx + dx, ORIGIN[1] + by - dy
        move(name, x, y)
        hits = 0
        time.sleep(0.07)                        # the game picks what's under the cursor once a frame (30 fps: 35 ms missed half)
        for _ in range(2):
            time.sleep(0.02)
            c = cursor_id(name)
            if c in PLAYER_FRAMES:
                if patient:                     # a mob in the middle of the party: look past us
                    break
                return None                     # a player (own party): skip this bar at once
            hits += c in ATTACK_FRAMES
            if hits >= 1:                       # the attack cursor's main frames never show elsewhere
                return int(round(x)), int(round(y))
    return None


# --- party HP (window 1's own frame + the three party frames), read from the red bar fill ----------
# own frame, then up to 4 party frames 65 px apart (5 clients = own + 4)
HP_BARS = [(22, 37, 62, 198)] + [(154 + 65 * k, 171 + 65 * k, 56, 192) for k in range(4)]
SP_BARS = [(34, 46, 62, 198)] + [(167 + 65 * k, 180 + 65 * k, 56, 192) for k in range(4)]


def _bar_fill(a, bars, colour):
    out = []
    for y0, y1, x0, x1 in bars:
        band = a[y0:y1, x0:x1]
        if colour == "red":
            hit = (band[:, :, 0] > 110) & (band[:, :, 1] < 95) & (band[:, :, 2] < 85)
        else:   # SP: blue
            hit = (band[:, :, 2] > 150) & (band[:, :, 0] < 90)
        row = hit[int(np.argmax(hit.sum(1)))]
        xs = np.where(row)[0]
        # any red counts (5% HP after a revive is only a few pixels); a dead bar has none at all
        out.append((xs.max() + 1) / (x1 - x0) if len(xs) > 0 else None)
    return out


def party_hp(im):
    """HP fraction (0..1) of window 1's character and the party members; None if no such frame.
    A frame that is there (its name shows) with an empty bar is 0.0: that character is dead."""
    hp = _bar_fill(np.asarray(im.convert("RGB")).astype(int), HP_BARS, "red")
    for k, f in enumerate(hp):
        if f is None and _name_mask(im, OWN_NAME if k == 0 else PARTY_NAME[k - 1]) is not None:
            hp[k] = 0.0
    return hp


def party_sp(im):
    """SP fraction (0..1), same frames as party_hp."""
    return _bar_fill(np.asarray(im.convert("RGB")).astype(int), SP_BARS, "blue")


def party_low(im, with_sp, skip_frames=()):
    """Lowest of HP (and SP) over the frames that show an HP bar. Dead characters (0) and
    skip_frames (characters travelling back after a death) don't count: they recover alone."""
    hp = party_hp(im)
    hp = [None if (k in skip_frames or f == 0.0) else f for k, f in enumerate(hp)]
    vals = [f for f in hp if f is not None]
    if with_sp:
        vals += [f for f, h in zip(party_sp(im), hp) if f is not None and h is not None]
    return min(vals) if vals else None


# Right-click on empty ground clears a selected target (no combat, but the target panel stays).
CLEAR_SPOTS = [(683, 470), (560, 470), (800, 470), (683, 300)]


def clear_selection(state, display_name):
    for gx, gy in CLEAR_SPOTS:
        x, y = ORIGIN[0] + gx, ORIGIN[1] + gy
        move(display_name, x, y)
        time.sleep(0.12)
        if cursor_id(display_name) in ATTACK_FRAMES:
            continue                            # a mob there: try the next spot
        subprocess.run(["xdotool", "mousedown", "3", "sleep", "0.1", "mouseup", "3"],
                       env={**os.environ, "DISPLAY": display_name}, check=False)
        write(os.path.join(state, "target-status"), "waiting:cleared a stale selection\n")
        time.sleep(0.4)
        return True
    return False


# --- FIXATE: stay within a radius of a fixed spot --------------------------------------------------
HOME_NEAR = 5      # walk back until window 1 is this close (tiles)
HOME_STEP = 8      # tiles per walking click (stays on screen)


def return_home(state, displays, radius=None):
    """FIXATE: if window 1 is outside the radius, everyone auto-traces (Alt+R) to the spot.
    radius overrides the FIXATE radius (KILL's start: go to the spot itself, e.g. from the safespot)."""
    try:
        hx, hy, fix_radius = map(int, read(os.path.join(state, "anchor")).split())
    except ValueError:
        return                                   # not fixated
    radius = fix_radius if radius is None else radius
    p = mbcoords.read(displays[0])
    if p is None:
        raise RuntimeError("can't read coordinates to check the FIXATE area")
    dist = max(abs(p[0] - hx), abs(p[1] - hy))
    if dist <= radius:
        return
    status(state, f"homing:{dist} tiles from the FIXATE spot, tracing back")
    ex_ = excluded(state)
    # the dead can't walk: KILL's death handling revives them (and brings them back) next
    displays = [d for i, d in enumerate(displays) if i not in ex_ and own_hp(d) != 0.0]
    if not displays:
        return
    pause = os.path.join(state, f"pause.{os.getpid()}")   # mbcast: don't mirror window 1's typing
    write(pause, "1\n")
    time.sleep(0.25)
    try:
        with ThreadPoolExecutor(len(displays)) as ex:
            ok = list(ex.map(lambda d: trace(d, hx, hy), displays))
        if not all(ok):
            raise RuntimeError("auto-trace (Alt+R) refused the FIXATE spot in window(s) " +
                               ", ".join(str(int(d.lstrip(":")) - 60) for d, o in zip(displays, ok) if not o))
        _arrive(state, displays, hx, hy, "walking back to the FIXATE spot", limit=300, near=HOME_NEAR)
    finally:
        os.remove(pause)
    status(state, f"homing:back at the FIXATE spot")

def name_colour(im, bx, by):
    """Colour of a mob's name label: 'white' (about our level), 'green' (far below, ~no EXP),
    'other' (yellow/orange/red/purple: above our level, dangerous), or None if not readable.
    Each bright pixel above the bar is sorted into white text, green text, coloured text or
    foliage (sunlit leaves are bright yellow-green and used to swamp the name), then voted."""
    x0, x1 = int(bx) - 50, int(bx) + 50
    y0, y1 = max(0, int(by) - 95), int(by) - 17
    if y1 <= y0:
        return None
    a = np.asarray(im.crop((x0, y0, x1, y1)).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    bright = a.max(2) > 200
    white = bright & (a.min(2) > 200)
    green = bright & (g > 200) & (r < 80) & (b < 80)
    red = bright & (r > 200) & (g < r - 40)                     # red / orange
    yellow = bright & (r > 200) & (g > 200) & (b < 120) & (abs(r - g) < 25)
    purple = bright & (b > 200) & (r > 150) & (g < 140)
    other = int((red | yellow | purple).sum())
    votes = {"white": int(white.sum()), "green": int(green.sum()), "other": other}
    best = max(votes, key=votes.get)
    if votes[best] < 10:
        return None
    return best


THREAT_PX = int(os.environ.get("THREAT_PX", "300"))   # a red-named mob this close to the party


def threat_bar(im):
    """Bar of the nearest above-level (red/orange/...) mob within THREAT_PX of the party, or None.
    Aggressive and strong: everyone hits it at once, before it picks one of us off."""
    near = [b for b in green_bar_candidates(im)
            if 45 ** 2 < (b[0] - PLAYER_BAR_X) ** 2 + (b[1] - PLAYER_BAR_Y) ** 2 <= THREAT_PX ** 2
            and name_colour(im, *b) == "other"]     # (within 45 px: our own stacked characters)
    return min(near, key=lambda b: (b[0] - PLAYER_BAR_X) ** 2 + (b[1] - PLAYER_BAR_Y) ** 2) if near else None


def green_named(im, bx, by):
    return name_colour(im, bx, by) == "green"

def nearest_bar(bars, x, y):
    return sorted(bars, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)


# --- which window is which party frame (names, matched as pixel masks) ----------------------------
OWN_NAME = (80, 6, 202, 22)                                           # name in a window's own frame
PARTY_NAME = [(74, 140 + 65 * k, 202, 158 + 65 * k) for k in range(4)]   # names in window 1's party frames


def _name_mask(im, box):
    a = np.asarray(im.crop(box).convert("RGB")).astype(int)
    m = (a.min(2) > 190) & (a.max(2) - a.min(2) < 40)                 # white name text
    ys, xs = np.where(m)
    if len(xs) < 10:
        return None
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _same(a, b):
    if a is None or b is None or a.shape != b.shape:
        return False
    return (a & b).sum() / max(1, (a | b).sum()) > 0.85


def party_map(displays, front):
    """frame index -> window index (cached per KILL run in STATE/party-map)."""
    cache = os.path.join(os.environ.get("MB_STATE", "/nonexistent"), "party-map")
    try:
        m = {int(k): int(v) for k, v in (l.split() for l in read(cache).splitlines())}
        if m:
            return m
    except ValueError:
        pass
    m = _party_map(displays, front)
    if os.path.isdir(os.path.dirname(cache)) and len(m) == len(displays):
        write(cache, "".join(f"{k} {v}\n" for k, v in m.items()))
    return m


def _party_map(displays, front):
    """frame index -> window index: frame 0 is window 1's own frame, frames 1.. are its party
    frames. A window's own name is looked up among the party frame names."""
    lead = screenshot(displays[front])
    frames = [_name_mask(lead, b) for b in PARTY_NAME]
    out = {0: front}
    for i, d in enumerate(displays):
        if i == front:
            continue
        own = _name_mask(screenshot(d), OWN_NAME)
        for k, fm in enumerate(frames):
            if _same(own, fm):
                out[k + 1] = i
                break
    return out


def excluded(state):
    """Windows out of broadcast, plus windows whose client is gone (STATE/gone: crashed clients,
    never touched again until the user brings them back)."""
    try:
        out = {int(x) for x in read(os.path.join(state, "excluded")).split()}
    except ValueError:
        out = set()
    try:
        out |= {int(x) for x in read(os.path.join(state, "gone")).split()}
    except ValueError:
        pass
    return out


def set_excluded(state, idx, on):
    with open(os.path.join(state, "excluded.lock"), "w") as lk:
        import fcntl
        fcntl.flock(lk, fcntl.LOCK_EX)
        ex = excluded(state)
        (ex.add if on else ex.discard)(idx)
        path = os.path.join(state, "excluded")
        if ex:
            write(path, "".join(f"{i}\n" for i in sorted(ex)))
        elif os.path.exists(path):
            os.remove(path)


def status(state, text):
    # a side job (the tank walking alone) must not overwrite what KILL reads as its own status
    write(os.path.join(state, os.environ.get("MB_STATUS_FILE", "target-status")), text + "\n")


def own_hp(display_name):
    """Own HP share; 0 while the "Revive in Town?" dialog is open: a dead character can still
    show some HP (LuckyOne lay dead at "82/965" for 10 minutes and nobody revived it, 2026-10-14)."""
    # only the top-left part: party frames and where the Revive dialog opens (a full screenshot
    # made this the slowest step of the leader's follower check)
    im = mbcoords.grab(display_name, (ORIGIN[0], ORIGIN[1], ORIGIN[0] + 750, ORIGIN[1] + 550))
    hp = party_hp(im)[0]
    if hp and hp < 0.5 and find_image(im.crop((150, 150, 750, 550)), "mbrevive.png") is not None:
        return 0.0
    return hp


def crashed_windows(state, n):
    """Windows whose game process is gone (STATE/boxN.pid): taken out of broadcast for good."""
    out = []
    for i in range(n):
        try:
            os.kill(int(read(os.path.join(state, f"box{i + 1}.pid"))), 0)
        except (ValueError, ProcessLookupError):
            out.append(i)
        except PermissionError:
            pass
    return out


def dead_windows(state, displays, front):
    """Windows whose character is dead (HP 0 in window 1's frames), not already being recovered."""
    mapping = party_map(displays, front)
    ex = excluded(state)
    dead = [mapping[k] for k, f in enumerate(party_hp(screenshot(displays[front])))
            if f == 0.0 and k in mapping and mapping[k] not in ex]
    fake = os.path.join(state, "fake-dead")     # test hook: pretend this window died (once)
    if os.path.exists(fake):
        dead = sorted(set(dead) | {int(read(fake))})
        os.remove(fake)
    return dead


def _min_hold():
    """Shortest xdotool sleep that still spans one game frame: the boxes run at FPS_LIMIT
    (multibox.conf), and a key held shorter than a frame is never seen (at 10-15 fps Alt+R,
    Insert and F1 with 0.04-0.06 s holds stopped working, 2026-10-17)."""
    fps = 30
    try:
        for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "multibox.conf")):
            if line.startswith("FPS_LIMIT="):
                fps = int(line.split("=", 1)[1].strip() or 30)
    except (OSError, ValueError):
        pass
    return 0.0 if fps <= 0 or fps >= 30 else 1.3 / fps


MIN_HOLD = _min_hold()


def key(display_name, *keys):
    keys = list(keys)
    if MIN_HOLD:
        for k in range(len(keys) - 1):
            try:
                if keys[k] == "sleep" and float(keys[k + 1]) < MIN_HOLD:
                    keys[k + 1] = f"{MIN_HOLD:.3f}"
                elif keys[k] == "--delay" and int(keys[k + 1]) < MIN_HOLD * 1000:
                    keys[k + 1] = str(int(MIN_HOLD * 1000))     # typed text loses letters too
            except ValueError:
                pass
    subprocess.run(["xdotool", *keys], env={**os.environ, "DISPLAY": display_name}, check=False)


def click_at(display_name, x, y, button="1"):
    key(display_name, "mousemove", str(int(x)), str(int(y)), "sleep", "0.12",
        "mousedown", button, "sleep", "0.1", "mouseup", button)


def find_text(im, words, lowest=False):
    """Screen box (x, y, w, h) of an OCR word containing one of words (the lowest on screen if
    lowest, e.g. a button under a same-named title), or None."""
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=buf.getvalue(),
                         capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
    hits = []
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and any(w.lower() in f[11].lower() for w in words):
            hits.append((int(f[6]), int(f[7]), int(f[8]), int(f[9])))
    if not hits:
        return None
    return max(hits, key=lambda b: b[1]) if lowest else hits[0]


# Revive dialog (frmRelive: "Do you want to Revive in town", one button ~38 px below the text).
REVIVE_BTN_BELOW = 38


def find_image(im, template_file, threshold=0.8):
    """Centre (game coords) of a UI element cut from a real screenshot, or None."""
    tpl = cv2.imread(os.path.join(os.path.dirname(os.path.abspath(__file__)), template_file), cv2.IMREAD_GRAYSCALE)
    scr = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < threshold:
        return None
    return loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2


RECAP_X = (122, -1)     # Death Recap's close (x) button, from its title (template mbrecap.png)


def close_recap(display_name):
    """Close a Death Recap panel left open (it covers the middle of the screen: target clicks and
    the character). Its Close button can sit under the skill bars; the title's (x) is always free."""
    for _ in range(3):
        p = find_image(screenshot(display_name), "mbrecap.png")
        if p is None:
            return
        click_at(display_name, ORIGIN[0] + p[0] + RECAP_X[0], ORIGIN[1] + p[1] + RECAP_X[1])
        time.sleep(0.6)


def revive(state, idx, display_name):
    """Dead: close the Death Recap, click the dialog's Revive button (both found by image templates
    cut from a real death screen), check HP > 0."""
    if (own_hp(display_name) or 0) > 0:
        close_recap(display_name)
        return                                  # alive already (revived by hand, or a test)
    im = screenshot(display_name)
    im.save(os.path.join(state, f"death-window{idx + 1}.png"))
    for attempt in range(4):
        close = find_image(im, "mbclose.png")
        if close:
            click_at(display_name, ORIGIN[0] + close[0], ORIGIN[1] + close[1])
            time.sleep(0.6)
            im = screenshot(display_name)
        btn = find_image(im, "mbrevive.png")
        if btn is None:
            time.sleep(1)
            im = screenshot(display_name)
            continue
        click_at(display_name, ORIGIN[0] + btn[0], ORIGIN[1] + btn[1])
        for _ in range(12):
            time.sleep(0.5)
            if (own_hp(display_name) or 0) > 0:
                close_recap(display_name)
                return
        im = screenshot(display_name)
    raise RuntimeError(f"window {idx + 1}: could not revive (screenshot: death-window{idx + 1}.png)")

SIT_CHECK_S = 8          # a sitting character regains several % HP in this time; standing, ~0


def rest_until_full(state, windows, what, limit=600, stagger=1.0, with_sp=False, watch=None):
    """Sit windows [(idx, display)] down and wait until each one's own HP (and SP) is full, then
    stand them up. Insert toggles sitting and the server drops presses that come too fast, so
    every window is checked: no HP gain in SIT_CHECK_S seconds -> it isn't sitting, press again.
    watch(): optional check called every round; returns a reason to stop early (attacked)."""
    def full(d):
        im = screenshot(d)
        hp = party_hp(im)[0] or 0
        sp = party_sp(im)[0] if with_sp else 1.0
        return hp, min(hp, sp if sp is not None else 1.0)
    for n, (i, d) in enumerate(windows):
        if n:
            time.sleep(stagger)
        key(d, "keydown", "Insert", "sleep", "0.12", "keyup", "Insert")
    t0 = time.monotonic()
    since = {d: (t0, full(d)[0]) for _, d in windows}
    done, presses, reason = set(), {d: 1 for _, d in windows}, None
    sitting = {d: True for _, d in windows}     # best knowledge, corrected by HP regen
    while time.monotonic() - t0 < limit and len(done) < len(windows):
        time.sleep(2)
        if watch and (reason := watch()):
            break
        lows = []
        for i, d in windows:
            if d in done:
                continue
            hp, low = full(d)
            if low >= float(os.environ.get("REST_FULL", "0.97")):
                done.add(d)
                continue
            lows.append(f"{i + 1}:{round(low * 100)}%")
            t1, hp1 = since[d]
            if time.monotonic() - t1 >= SIT_CHECK_S:
                if hp - hp1 >= 0.02:
                    sitting[d] = True
                elif hp < 0.97 and presses[d] < 8:
                    # not regenerating: standing (a lost press) -> press sit again
                    key(d, "keydown", "Insert", "sleep", "0.12", "keyup", "Insert")
                    presses[d] += 1
                    sitting[d] = True
                    print(f"{what}: window {i + 1} isn't resting (HP {round(hp * 100)}%), pressing sit again",
                          file=sys.stderr)
                    time.sleep(stagger)
                since[d] = (time.monotonic(), hp)
        status(state, f"{what}: resting, {' '.join(lows) or 'all full'}")
    # stand up the ones that are sitting
    for n, (i, d) in enumerate(windows):
        if sitting[d]:
            if n:
                time.sleep(stagger)
            key(d, "keydown", "Insert", "sleep", "0.12", "keyup", "Insert")
    time.sleep(1.5)
    return reason


def rest_one(state, idx, display_name):
    """Sit (Insert), wait until own HP (and SP, in F1 mode: Explorers revive with 0 SP and can't
    cast) is full (max 4 min, sitting checked), stand up."""
    # FAST: back in the fight at 60% (the camp is ~40 tiles from the respawn; HP keeps regenerating)
    old = os.environ.get("REST_FULL")
    os.environ["REST_FULL"] = os.environ.get("RECOVER_REST", "0.6")
    try:
        rest_until_full(state, [(idx, display_name)], f"recover:window {idx + 1}", limit=120,
                        with_sp=F1_AT_CLICK)
    finally:
        if old is None:
            os.environ.pop("REST_FULL", None)
        else:
            os.environ["REST_FULL"] = old


# Alt+R auto-trace dialog (BPO, 1366x768), game coordinates measured 2026-10-08.
TRACE_X_FIELD, TRACE_Y_FIELD, TRACE_CONFIRM = (413, 275), (462, 275), (422, 311)


# Alt+R dialog layout, relative to its "Coordinates" label (template mbtrace.png), which sits at
# (346, 276) when the dialog is in its default place. The dialog can be dragged (window 2 had it at
# (309, 241)), so everything is positioned from where the label is found.
TRACE_LABEL = (346, 276)
TRACE_X_FIELD, TRACE_Y_FIELD, TRACE_CONFIRM, TRACE_CANCEL = (413, 275), (462, 275), (422, 311), (470, 312)
TRACE_FIELDS = ((385, 268, 432, 285), (438, 268, 484, 285))   # the X and Y text fields (read-back)
BADCOORDS_OK = (682, 421)       # "Please input proper coordinates!" popup's Confirm


def _rel(label, pt):
    return pt[0] - TRACE_LABEL[0] + label[0], pt[1] - TRACE_LABEL[1] + label[1]


def _close_trace(display_name):
    im = screenshot(display_name)
    if find_image(im, "mbbadcoords.png"):
        click_at(display_name, ORIGIN[0] + BADCOORDS_OK[0], ORIGIN[1] + BADCOORDS_OK[1])
        time.sleep(0.4)
        im = screenshot(display_name)
    label = find_image(im, "mbtrace.png")
    if label:
        x, y = _rel(label, TRACE_CANCEL)
        click_at(display_name, ORIGIN[0] + x, ORIGIN[1] + y)
        time.sleep(0.4)


def _field_reads(crop, want):
    """Does this dialog field show exactly `want`? Measured 2026-10-08 on 10 values x 16 OCR
    variants: scale 3 was right every time, scales 4-6 misread (1986 -> 1966, 2706 -> 27086). So:
    scale 3 on four preprocessings (raw, inverted, thresholded both ways); at least 3 must agree."""
    g = ImageOps.grayscale(crop)
    imgs = (crop, ImageOps.invert(g).convert("RGB"),
            g.point(lambda v: 255 if v > 120 else 0).convert("RGB"),
            g.point(lambda v: 0 if v > 120 else 255).convert("RGB"))
    # scale 3 read 2686 as 2636 every time (2026-10-14: the walk retyped it in a loop), scale 4
    # read it right: a second opinion at scale 4 when scale 3 disagrees
    for scale in (3, 4):
        reads = []
        for img in imgs:
            try:
                r = re.sub(r"\D", "", ocr(img, 7, scale, "0123456789"))
                # the blinking text cursor right after the digits reads as a "1" ("684|" ->
                # "6841": the bear loop retyped the Y field forever, 2026-10-18)
                if len(r) == len(want) + 1 and r.startswith(want) and r[-1] in "17":
                    r = want
                # the dialog font's 8 reads as 3 at every scale ("684" -> "634" in all 8 reads,
                # 2026-10-18): 3 and 8 count as the same digit
                if r.replace("8", "3") == want.replace("8", "3"):
                    r = want
                reads.append(r)
            except subprocess.TimeoutExpired:
                pass                            # machine too busy for OCR: decide on what we have
        if len(reads) < 2:
            return True                         # no reads at all: trust the typing (it's reliable)
        if reads.count(want) >= max(1, len(reads) - 1):
            return True
    return False


SAFE_HOP = 55            # longer auto-traces go via the safe path (mbpath), one hop at a time


def party_level():
    try:
        return int(read(os.path.join(os.environ.get("MB_STATE", ""), "party-level")) or 13)
    except ValueError:
        return 13


def safe_hop(display_name, tx, ty, here=None):
    """The next waypoint of a safe route to (tx, ty): the game's own auto-trace takes the direct
    way, which walked the party into Silver Mine 1. Short distances go straight."""
    here = here or mbcoords.read(display_name)
    if not here:
        return tx, ty
    try:
        import mbpath
        d = max(abs(here[0] - tx), abs(here[1] - ty))
        if d <= 12 or (d <= SAFE_HOP and mbpath.clear(here, (tx, ty))):   # a few steps: always straight
            return tx, ty
        wp = mbpath.plan(here, (tx, ty), party_level())
    except Exception as exc:                    # no map data: the old direct trace
        print(f"safe path failed ({exc}); tracing directly", file=sys.stderr)
        return tx, ty
    if not wp:
        return tx, ty
    hop = wp[0]
    for x, y in wp:                             # the furthest waypoint within one hop, straight line clear
        if max(abs(here[0] - x), abs(here[1] - y)) > SAFE_HOP or (hop != wp[0] and not mbpath.clear(here, (x, y))):
            break
        hop = (x, y)
    return hop


LAST_HOP = {}             # display -> the hop its last trace went to
PREFILL = {}              # display -> (hop typed into an open Alt+R dialog, dialog label position)


def prefill(display_name, tx, ty):
    """While walking to the current hop: open Alt+R and type the NEXT hop (from the current hop's
    end point), so arriving only costs a Confirm click. Walking goes on with the dialog open."""
    cur = LAST_HOP.get(display_name)
    if not cur or cur == (tx, ty) or display_name in PREFILL:
        return
    nx, ny = safe_hop(display_name, tx, ty, here=cur)
    key(display_name, "keydown", "alt", "sleep", "0.05", "key", "r", "sleep", "0.05", "keyup", "alt")
    time.sleep(0.6)
    label = find_image(screenshot(display_name), "mbtrace.png")
    if not label:
        return
    for field, value in ((TRACE_X_FIELD, nx), (TRACE_Y_FIELD, ny)):
        fx, fy = _rel(label, field)
        click_at(display_name, ORIGIN[0] + fx, ORIGIN[1] + fy)
        key(display_name, "key", "End", *(["key", "BackSpace"] * 6), "key", "Home",
            *(["key", "Delete"] * 6), "type", "--delay", "60", str(value))
    PREFILL[display_name] = ((nx, ny), label)


def confirm_prefill(display_name):
    """Arrived at the hop: Confirm the pre-typed next hop. False if there was none / it's gone."""
    item = PREFILL.pop(display_name, None)
    if not item:
        return False
    (nx, ny), label = item
    if find_image(screenshot(display_name), "mbtrace.png") != label:
        return False                            # the dialog was closed meanwhile
    cx, cy = _rel(label, TRACE_CONFIRM)
    click_at(display_name, ORIGIN[0] + cx, ORIGIN[1] + cy)
    time.sleep(0.4)
    if find_image(screenshot(display_name), "mbbadcoords.png"):
        _close_trace(display_name)
        return False
    LAST_HOP[display_name] = (nx, ny)
    return True


def trace(display_name, tx, ty):
    PREFILL.pop(display_name, None)             # _trace closes any open dialog first
    hx, hy = safe_hop(display_name, tx, ty)
    LAST_HOP[display_name] = (hx, hy)
    if _trace(display_name, hx, hy):
        return True
    if (hx, hy) == (tx, ty):
        return False
    # the game refused the hop (a blocked tile): a few tiles around it, then the final target
    for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3)):
        if _trace(display_name, hx + dx, hy + dy):
            LAST_HOP[display_name] = (hx + dx, hy + dy)
            return True
    # never fall back to the straight line to a far target: it walks through danger (a lone
    # traveller died to Whacky Lambs that way); the caller re-traces from the next position
    return False


def _trace(display_name, tx, ty):
    """Alt+R auto-trace to (tx, ty). Typing into the dialog can lose keys or keep old digits, so both
    fields are read back before Confirm and retyped if wrong; the 'proper coordinates' popup is
    handled; all positions follow the dialog wherever it is. Returns True if accepted."""
    _close_trace(display_name)                  # leftovers from an earlier failed attempt
    for attempt in range(3):
        key(display_name, "keydown", "alt", "sleep", "0.05", "key", "r", "sleep", "0.05", "keyup", "alt")
        time.sleep(0.8)
        label = find_image(screenshot(display_name), "mbtrace.png")
        if not label:
            continue
        ok = False
        for retype in range(3):
            for field, value in ((TRACE_X_FIELD, tx), (TRACE_Y_FIELD, ty)):
                fx, fy = _rel(label, field)
                click_at(display_name, ORIGIN[0] + fx, ORIGIN[1] + fy)
                key(display_name, "key", "End", *(["key", "BackSpace"] * 6), "key", "Home",
                    *(["key", "Delete"] * 6), "type", "--delay", "90", str(value))
            time.sleep(0.2)
            im = screenshot(display_name)
            ok = True
            for (x0, y0, x1, y1), want in zip(TRACE_FIELDS, (str(tx), str(ty))):
                a0, b0 = _rel(label, (x0, y0))
                a1, b1 = _rel(label, (x1, y1))
                if not _field_reads(im.crop((a0, b0, a1, b1)), want):
                    ok = False
            if ok:
                break
        if not ok:
            _close_trace(display_name)
            continue
        cx, cy = _rel(label, TRACE_CONFIRM)
        click_at(display_name, ORIGIN[0] + cx, ORIGIN[1] + cy)
        time.sleep(0.6)
        if find_image(screenshot(display_name), "mbbadcoords.png"):
            _close_trace(display_name)
            continue
        return True
    _close_trace(display_name)
    return False

def recover(state, idx, display_name, tx, ty):
    """Dead window: take it out of broadcast, revive in town, rest, auto-trace back to (tx, ty),
    wait until it is within 4 tiles (max 8 min), then put it back into broadcast."""
    set_excluded(state, idx, True)
    try:
        status(state, f"recover:window {idx + 1}: died, reviving in town")
        revive(state, idx, display_name)
        rest_one(state, idx, display_name)
        status(state, f"recover:window {idx + 1}: auto-trace to {tx},{ty}")
        trace(display_name, tx, ty)
        t0, last, still = time.monotonic(), None, 0
        while time.monotonic() - t0 < 480:
            time.sleep(3)
            p = mbcoords.read(display_name)
            if p is None:
                continue
            d = max(abs(p[0] - tx), abs(p[1] - ty))
            status(state, f"recover:window {idx + 1}: {d} tiles to go")
            if d <= 4:
                return
            still = still + 1 if p == last else 0
            last = p
            if still >= 5:                      # stopped walking (dialog closed, path blocked): trace again
                retries = locals().get("retries", 0) + 1
                if retries > 2:
                    raise RuntimeError(f"window {idx + 1} can't reach {tx},{ty} (stuck at {p}; water or blocked?)")
                trace(display_name, tx, ty)
                still = 0
        raise RuntimeError(f"window {idx + 1} did not get back in 8 minutes")
    finally:
        set_excluded(state, idx, False)


def _arrive(state, displays, tx, ty, what, limit=600, near=4):
    """Wait until every display is within near tiles of (tx, ty); re-trace any that stopped."""
    t0 = time.monotonic()
    last, still, retraced, start, retrace_at = {}, {}, {}, {}, {}
    while time.monotonic() - t0 < limit:
        time.sleep(1.5)
        pos = [mbcoords.read(d) for d in displays]
        for d, p in zip(displays, pos):
            start.setdefault(d, p)
        far, early = [], []
        for d, p in zip(displays, pos):
            # a long way goes hop by hop: give the next hop as soon as this one is nearly reached,
            # instead of waiting for the character to stand still
            h = LAST_HOP.get(d)
            if p and h and h != (tx, ty) and max(abs(p[0] - h[0]), abs(p[1] - h[1])) <= 6:
                early.append(d)
        for d, p in zip(displays, pos):
            if p is None or max(abs(p[0] - tx), abs(p[1] - ty)) > near:
                far.append(d)
                if p is not None and p == last.get(d) and own_hp(d) == 0.0:
                    # died on the way (an aggressive mob): revive, rest at the respawn, go again
                    i = int(d.lstrip(":")) - 61        # window number from the display (:61 = 1)
                    status(state, f"{what}: a traveller died on the way, reviving")
                    revive(state, i, d)
                    rest_one(state, i, d)
                    trace(d, tx, ty)
                    still[d] = retraced[d] = 0
                    last[d] = None
                    continue
                if d in early:
                    last[d] = p
                    continue
                still[d] = still.get(d, 0) + 1 if p is not None and p == last.get(d) else 0
                last[d] = p
                far_off = p is not None and max(abs(p[0] - tx), abs(p[1] - ty)) > SAFE_HOP
                if still[d] >= (2 if far_off else 3):   # stopped short (end of a hop, dialog, blocked)
                    if p and retrace_at.get(d) and max(abs(p[0] - retrace_at[d][0]),
                                                       abs(p[1] - retrace_at[d][1])) > 5:
                        retraced[d] = 0         # it got somewhere: the end of a safe-path hop
                    retrace_at[d] = p
                    retraced[d] = retraced.get(d, 0) + 1
                    if retraced[d] > 3:        # not moving even after 3 re-traces: give up, say why
                        raise RuntimeError(f"{what}: window {int(d.lstrip(':')) - 60} can't reach {tx},{ty} "
                                           f"(stuck at {p}; water or blocked?)")
                    if p == start.get(d):      # never moved at all: probably sitting. Insert toggles
                        status(state, f"{what}: window {int(d.lstrip(':')) - 60} doesn't move; Insert (stand up)")
                        key(d, "keydown", "Insert", "sleep", "0.1", "keyup", "Insert")
                        time.sleep(1.5)
                    early.append(d)             # re-traced below, in parallel with the others
        status(state, f"safespot:{what}: {len(displays) - len(far)}/{len(displays)} arrived")
        if early:                               # next hops: the pre-typed one if any, else a trace
            with ThreadPoolExecutor(len(early)) as pool:
                list(pool.map(lambda d: confirm_prefill(d) or trace(d, tx, ty), early))
            for d in early:
                still[d] = 0
        walking = [d for d, p in zip(displays, pos)
                   if p and d not in early and d not in PREFILL and LAST_HOP.get(d) not in (None, (tx, ty))]
        if walking:                             # type the following hop while they walk
            with ThreadPoolExecutor(len(walking)) as pool:
                list(pool.map(lambda d: prefill(d, tx, ty), walking))
        if not far:
            return
    raise RuntimeError(f"{what}: not everyone arrived within {limit // 60} minutes")


def group_recover(state, front, displays, safe, target, with_sp):
    """Somebody died and a safespot is set: everyone goes to the safespot (the dead revive in
    town first), rests until HP (and SP) are full, then everyone traces back to the target."""
    ex = ex_ = excluded(state)
    dead = set(dead_windows(state, displays, front)) - ex
    live_displays = [d for i, d in enumerate(displays) if i not in ex]
    why = f"windows {', '.join(str(i + 1) for i in sorted(dead))} died" if dead else "retreating"
    status(state, f"safespot:{why}; everyone to {safe[0]},{safe[1]}")
    alive = [d for i, d in enumerate(displays) if i not in dead and i not in ex]
    with ThreadPoolExecutor(max(1, len(alive))) as ex:
        list(ex.map(lambda d: trace(d, *safe), alive))
    # the dead: revive, rest at the respawn point until full, then travel. In parallel; sit
    # presses spread 1.2 s apart (the server accepts about one sit per half second).
    def back(item):
        n, i = item
        revive(state, i, displays[i])
        time.sleep(n * 1.2)
        rest_one(state, i, displays[i])
        if not trace(displays[i], *safe):
            raise RuntimeError(f"window {i + 1}: auto-trace to the safespot was refused")
    if dead:
        with ThreadPoolExecutor(len(dead)) as ex:
            list(ex.map(back, enumerate(sorted(dead))))
    _arrive(state, live_displays, *safe, "going to the safespot")
    prev = [party_hp(screenshot(displays[front]))]

    def attacked():                             # HP going down while sitting: attacked here
        hp = party_hp(screenshot(displays[front]))
        hit = any(a is not None and b is not None and b < a - 0.02 for a, b in zip(prev[0], hp))
        prev[0] = hp
        return "attacked while resting at the safespot" if hit else None
    rest_windows = [(i, d) for i, d in enumerate(displays) if i not in ex_]
    reason = rest_until_full(state, rest_windows, "safespot", limit=600, with_sp=with_sp, watch=attacked)
    if reason:
        raise RuntimeError(reason)
    linger = float(os.environ.get("MB_LINGER", "0"))   # retreated from a strong mob: let it wander off
    if linger > 0:
        status(state, f"safespot: waiting {linger:.0f}s for the {AVOID_NAME or 'strong mob'} to wander off")
        time.sleep(linger)
    with ThreadPoolExecutor(len(live_displays)) as pool:
        list(pool.map(lambda d: trace(d, *target), live_displays))
    _arrive(state, live_displays, *target, "returning to the target")
    status(state, f"safespot:back at {target[0]},{target[1]}")


def read_offsets(state, n):
    try:
        rows = read(os.path.join(state, "offsets")).splitlines()
        offs = [(0, 0)] + [tuple(map(int, r.split())) for r in rows if len(r.split()) == 2]
    except ValueError:
        offs = [(0, 0)]
    return (offs + [(0, 0)] * n)[:n]


def danger_window(displays, front, mapping=None):
    """Index of a window in danger, or -1: one whose own target panel shows a living mob (it is
    fighting something other than the group), else the lowest HP below 90%. HP comes from window
    1's own and party frames in one screenshot; party frames are mapped to windows by name."""
    ex = excluded(os.environ.get("MB_STATE", ""))
    for i, d in enumerate(displays):
        if i != front and i not in ex:
            im = screenshot(d)
            if panel_present(im) and hp_value(im)[0] > 0:
                return i
    mapping = mapping or party_map(displays, front)
    worst, worst_hp = -1, 0.9
    for k, f in enumerate(party_hp(screenshot(displays[front]))):
        if f is not None and 0 < f < worst_hp and k in mapping and mapping[k] not in ex:
            worst, worst_hp = mapping[k], f
    return worst

SKIP_GREEN = os.environ.get("SKIP_GREEN", "1") == "1"
ATTACK_HIGHER = os.environ.get("ATTACK_HIGHER", "1") == "1"  # yellow/orange/red names: on (you hunt above your level)
NATO_PX = 110          # an above-level mob this close to the attacked character is its attacker
BLACKLIST_S = 45       # skip a spot whose click didn't start a fight for this long
BLACKLIST_PX = 30


def blacklisted(state):
    now, out = time.time(), []
    for line in read(os.path.join(state, "target-blacklist")).splitlines():
        try:
            t, x, y = line.split()
            if now - float(t) < BLACKLIST_S:
                out.append((float(x), float(y)))
        except ValueError:
            pass
    return out


F1_SLOT = (302, 742, 322, 758)  # inner area of the F1 hotbar slot (empty: plain beige, std ~2.5)
EXP_BOX = (110, 43, 180, 57)    # window 1's own frame: the "81.20%" EXP text


def exp_signature(im):
    """Exact pixel shapes of the EXP text: any change means EXP went up (a kill, also a level-up)."""
    return "/".join(mbcoords.segment(im.crop(EXP_BOX)))


def was_kill(state, display_name):
    """A 'miss' is really a fast kill (target died before we saw it alive) if EXP changed."""
    before = read(os.path.join(state, "exp-before"))
    return bool(before) and exp_signature(screenshot(display_name)) != before


def save_miss(state, display_name):
    """Keep a screenshot of every miss (last 12) for diagnosis."""
    d = os.path.join(state, "misses")
    os.makedirs(d, exist_ok=True)
    shots = sorted(os.listdir(d))
    for old in shots[:-11]:
        os.remove(os.path.join(d, old))
    try:
        last = read(os.path.join(state, "target-last-bar")).replace(" ", "_")
        screenshot(display_name).save(os.path.join(d, time.strftime("%H%M%S") + f"_bar{last}.png"))
    except Exception:
        pass


def blacklist_last(state):
    """The last target click didn't start a fight: skip that spot for a while."""
    try:
        x, y = map(float, read(os.path.join(state, "target-last-bar")).split())
    except ValueError:
        return
    keep = [l for l in read(os.path.join(state, "target-blacklist")).splitlines()
            if l and time.time() - float(l.split()[0]) < BLACKLIST_S]
    write(os.path.join(state, "target-blacklist"), "\n".join(keep + [f"{time.time():.0f} {x} {y}"]) + "\n")


ONLY_NAME = os.environ.get("MB_ONLY", "")      # KILL only monsters whose label contains this


def label_mask(a):
    """Name-label text pixels: white / pale yellow (about our level), green (far below), red (far
    above). Names take their colour from the level difference, so all of them count."""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return ((r > 190) & (g > 190)) | ((g > 190) & (r < 90) & (b < 90)) | ((r > 200) & (g < 110) & (b < 110))


def label_matches(im, bar, name):
    """The name label above a mob's bar (white / pale-yellow text, OCR'd on a bright-pixel mask)
    fuzzily contains `name` (e.g. "Cuddly Lamb" in "Lv8 Cuddly Lamb"). The label's height above
    the bar depends on the mob's size: a few bands are tried, lowest first."""
    import difflib
    bx, by = bar
    want = re.sub(r"[^a-z]", "", name.lower())
    for top, bottom in ((44, 26), (58, 40), (74, 56), (92, 74)):
        if by - top < 0:
            break
        box = (int(bx) - 80, int(by) - top, int(bx) + 80, int(by) - bottom)
        a = np.asarray(im.crop(box).convert("RGB")).astype(int)
        m = label_mask(a)
        if m.sum() < 40:
            continue
        mask = Image.fromarray(np.where(m, 0, 255).astype("uint8")).convert("RGB")
        have = re.sub(r"[^a-z]", "", ocr(mask, 7, 2).lower())
        if not have:
            continue
        LAST_LABEL[bar] = have
        if want in have:
            return True
        L = len(want)
        if max(difflib.SequenceMatcher(None, want, have[j:j + L]).ratio()
               for j in range(max(1, len(have) - L + 1))) >= 0.85:   # 0.75 let "Forest Spirit" pass
            return True
    return False


LAST_LABEL = {}         # bar -> OCR'd label text (logged with the chosen target)


def screen_labels(im):
    """Every name label in the world area from ONE OCR pass: [(text, centre x, bottom y)].
    Per-bar OCR (label_matches) took seconds per round; flying mobs were gone by the click."""
    x0, y0, x1, y1 = WORLD
    a = np.asarray(im.crop(WORLD).convert("RGB")).astype(int)
    m = label_mask(a)
    mask = Image.fromarray(np.where(m, 0, 255).astype("uint8"))
    mask = mask.resize((mask.width * 2, mask.height * 2), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    p = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=buf.getvalue(),
                       capture_output=True, timeout=max(OCR_TIMEOUT, 6))   # whole screen: slower
    words = []
    for line in p.stdout.decode("utf-8", "replace").splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and re.sub(r"[^A-Za-z]", "", f[11]):
            l, t_, w, h = (int(v) // 2 for v in f[6:10])
            words.append([re.sub(r"[^a-z]", "", f[11].lower()), x0 + l, y0 + t_, x0 + l + w, y0 + t_ + h])
    # join words on the same text row that are close together: "lv9" "marsh" "spirit"
    words.sort(key=lambda w: (w[2] // 6, w[1]))
    labels = []
    for w in words:
        last = labels[-1] if labels else None
        if last and abs(last[2] - w[2]) <= 5 and 0 <= w[1] - last[3] <= 14:
            last[0] += w[0]
            last[3] = w[3]
            last[4] = max(last[4], w[4])
        else:
            labels.append(list(w))
    return [(t_, (l + r) / 2, b) for t_, l, _, r, b in labels]


def label_near(labels, bar, names):
    """The label above this bar (if any) fuzzily contains one of the names."""
    import difflib
    bx, by = bar
    for text, cx, bottom in labels:
        if abs(cx - bx) > 70 or not (by - 100 <= bottom <= by - 12):
            continue
        LAST_LABEL[bar] = text
        for name in names:
            want = re.sub(r"[^a-z]", "", name.lower())
            if want in text:
                return True
            L = len(want)
            if max(difflib.SequenceMatcher(None, want, text[j:j + L]).ratio()
                   for j in range(max(1, len(text) - L + 1))) >= 0.85:
                return True
    return False


AVOID_NAME = os.environ.get("MB_AVOID", "")    # monsters too strong for the party: keep away from them
AVOID_PX = int(os.environ.get("AVOID_PX", "350"))


def avoid_bar(im):
    """Bar of a MB_AVOID monster (e.g. "Whacky Lamb") within AVOID_PX of the party, or None.
    Only above-level (or unreadable) names are OCR'd: the avoided ones are strong by definition."""
    if not AVOID_NAME:
        return None
    labels = None
    for b in nearest_bar(green_bar_candidates(im), PLAYER_BAR_X, PLAYER_BAR_Y):
        if (b[0] - PLAYER_BAR_X) ** 2 + (b[1] - PLAYER_BAR_Y) ** 2 > AVOID_PX ** 2:
            break
        if name_colour(im, *b) in ("white", "green"):
            continue
        try:
            labels = screen_labels(im) if labels is None else labels   # one OCR pass for all bars
        except subprocess.TimeoutExpired:
            return None                         # busy machine: check again next time
        if label_near(labels, b, AVOID_NAME.split("|")):
            return b
    return None


F1_AT_CLICK = os.environ.get("MB_F1_AT_CLICK") == "1"
SKILL_FRAMES = {520781157, 2163807991, 44115853}   # 3-frame animation, same over mobs and ground      # cursor while a skill (Lightning Bolt) is selected


def cast_f1(state, displays):
    """F1 in every (not excluded) window, mirroring paused so window 1's press isn't doubled."""
    pause = os.path.join(state, f"pause.{os.getpid()}")
    write(pause, "1\n")
    time.sleep(0.05)
    try:
        ex = excluded(state)
        live = [d for i, d in enumerate(displays) if i not in ex]
        with ThreadPoolExecutor(len(live) or 1) as pool:       # all windows at once
            list(pool.map(lambda d: key(d, "keydown", "F1", "sleep", "0.08", "keyup", "F1"), live))
    finally:
        os.remove(pause)
    time.sleep(0.1)


def drop_skill(state, displays):
    """A skill left selected (cast refused: cooldown, no SP) keeps the skill cursor: right-click
    on empty ground in every window drops it."""
    ex = excluded(state)
    for i, d in enumerate(displays):
        if i not in ex:
            clear_selection(state, d)


def kill(state, front, displays, allow_combat=False):
    if not os.path.exists(os.path.join(state, "mbcast.pid")):
        raise RuntimeError("multibox broadcaster is not running")
    # cursor into the world first (just below the party): the game needs ~0.3 s there before it
    # picks mobs under the cursor, and the screenshot must be fresh when probing starts
    if F1_AT_CLICK and cursor_id(displays[front]) in SKILL_FRAMES:
        drop_skill(state, displays)             # leftover skill cursor from a refused cast
    move(displays[front], ORIGIN[0] + PLAYER_BAR_X, ORIGIN[1] + PLAYER_BAR_Y + 70)
    active = screenshot(displays[front])
    if not allow_combat and in_combat(active):
        raise RuntimeError("combat bar is visible in the active client")
    if read(os.path.join(state, "broadcast")) != "on":
        raise RuntimeError("broadcast is off; no click sent to the other clients")
    # anyone dead (window 1 included): recover first, don't go looking for mobs
    ex = excluded(state)
    mapping = party_map(displays, front)
    if any(f == 0.0 for k, f in enumerate(party_hp(active)) if k in mapping and mapping[k] not in ex):
        raise RuntimeError("a party member is dead; recovering first")
    # Defend: MB_FOCUS = a window in danger (hit, or fighting something else). Pick the attackable
    # mob nearest to *that* character, found in its own screen and mapped to window 1's screen.
    near = (PLAYER_BAR_X, PLAYER_BAR_Y)
    focus = int(os.environ.get("MB_FOCUS", "-1"))
    # NATO: when someone is actually losing HP right now, the attacker is the target even if its
    # name is red (above our level). Only next to the victim, so a passing strong mob that isn't
    # hitting anyone is still left alone.
    under_attack = False
    if 0 <= focus < len(displays):
        first = party_hp(active)
        time.sleep(0.8)
        active = screenshot(displays[front])
        under_attack = any(a is not None and b is not None and b < a - 0.01
                           for a, b in zip(first, party_hp(active)))
        if not under_attack and not (focus != front and panel_present(screenshot(displays[focus]))):
            focus = -1                          # just low on HP, nobody is hitting it: hunt normally
    # the tank just taunted a mob off an Explorer: everyone focuses it (it's fighting us)
    try:
        ts_, tx_, ty_ = map(float, read(os.path.join(state, "tank-target")).split())
        if time.time() - ts_ < 6:
            near, under_attack = (tx_, ty_), True
            write(os.path.join(state, "target-status"), f"focusing the tank's taunted mob at {tx_:.0f},{ty_:.0f}\n")
            os.remove(os.path.join(state, "tank-target"))
    except (ValueError, OSError):
        pass
    avoid = avoid_bar(active)
    if avoid:
        write(os.path.join(state, "target-threat"), "")
        write(os.path.join(state, "target-status"), f"AVOID: {AVOID_NAME} at {round(avoid[0])},{round(avoid[1])}, retreating\n")
        print(f"AVOID: {AVOID_NAME} at {round(avoid[0])},{round(avoid[1])}", file=sys.stderr)
        sys.exit(8)
    threat = threat_bar(active)
    if threat and (ONLY_NAME or F1_AT_CLICK) and not under_attack:
        # hunting above our level every name is "red": a threat only counts when someone is
        # really losing HP (it locked onto our own stack 5x per round, ~50 s wasted)
        # quest hunting: a red-ish name near us only counts when it's really hurting someone
        # (flower fields read as red/yellow names and turned snails into "threats")
        first = party_hp(active)
        time.sleep(0.6)
        active = screenshot(displays[front])
        if not any(a is not None and b is not None and b < a - 0.01 for a, b in zip(first, party_hp(active))):
            threat = None
    write(os.path.join(state, "target-threat"), "1\n" if threat else "")
    if threat:
        near, under_attack = threat, True       # NATO: attack it, all of us, whatever its colour
        write(os.path.join(state, "target-status"), f"THREAT: strong mob at {round(threat[0])},{round(threat[1])}, everyone attacks it\n")
        print(f"THREAT: strong mob at {round(threat[0])},{round(threat[1])}; everyone attacks it", file=sys.stderr)
    elif 0 <= focus < len(displays) and focus != front:
        offs = read_offsets(state, len(displays))
        fbars = nearest_bar(green_bar_candidates(screenshot(displays[focus])), PLAYER_BAR_X, PLAYER_BAR_Y)
        for b in fbars[:16]:
            if hover_point(displays[focus], b[0], b[1], None):
                near = (b[0] - (offs[focus][0] - offs[front][0]), b[1] - (offs[focus][1] - offs[front][1]))
                write(os.path.join(state, "target-status"), f"defending window {focus + 1}\n")
                break
    black = blacklisted(state)

    label_cache = {}

    def labels_of(im):                          # one OCR pass per screenshot
        if id(im) not in label_cache:
            label_cache.clear()
            label_cache[id(im)] = (im, screen_labels(im))
        return label_cache[id(im)][1]

    def usable(bars):
        out = []
        for b in bars:
            if any((b[0] - x) ** 2 + (b[1] - y) ** 2 <= BLACKLIST_PX ** 2 for x, y in black):
                continue
            colour = name_colour(active, b[0], b[1])
            if colour == "green" and SKIP_GREEN and not ONLY_NAME:   # quest hunting: named mobs count
                continue
            if colour == "other" and not ATTACK_HIGHER and not (
                    under_attack and (b[0] - near[0]) ** 2 + (b[1] - near[1]) ** 2 <= NATO_PX ** 2):
                continue                    # above our level: never pick a fight with it
            if ONLY_NAME and not (under_attack and (b[0] - near[0]) ** 2 + (b[1] - near[1]) ** 2 <= NATO_PX ** 2) \
                    and not label_near(labels_of(active), b, ONLY_NAME.split("|")):
                continue                    # quest hunting: only this monster (attackers still are)
            out.append(b)
        return out

    lead_bars = usable(nearest_bar(green_bar_candidates(active), *near))
    if not lead_bars:
        raise RuntimeError("no green mob health bar found; no click sent")
    lead, lead_pt = None, None
    # nearest first; with 5 clients the nearest 5-6 bars are the party itself, so look further.
    # Flying mobs move on while we probe: refresh the screenshot after every 3 misses.
    tried = set()
    for attempt in range(6):
        for bar in [b for b in lead_bars if (round(b[0]), round(b[1])) not in tried][:3]:
            tried.add((round(bar[0]), round(bar[1])))
            pt = hover_point(displays[front], bar[0], bar[1], None, im=active)
            if pt:
                lead, lead_pt = bar, pt
                break
        if lead is not None or len(tried) >= 16:
            break
        active = screenshot(displays[front])
        lead_bars = usable(nearest_bar(green_bar_candidates(active), *near))
    if lead is None:
        # Small mobs (Piglets) often stand beside their bar's centre: wide probe the nearest few.
        for bar in [b for b in lead_bars if (b[0] - PLAYER_BAR_X) ** 2 + (b[1] - PLAYER_BAR_Y) ** 2 > 40 ** 2][:4]:
            pt = hover_point(displays[front], bar[0], bar[1], None, im=active, wide=True)
            if pt:
                lead, lead_pt = bar, pt
                break
    if lead is None:
        # A mob standing among the party (attacking us) is covered by our own characters: the
        # quick pass gives up on it at the first player cursor. Probe bars near the party patiently.
        party_bars = [b for b in usable(green_bar_candidates(active))
                      if (b[0] - PLAYER_BAR_X) ** 2 + (b[1] - PLAYER_BAR_Y) ** 2 <= 90 ** 2]
        for bar in party_bars[:8]:
            pt = hover_point(displays[front], bar[0], bar[1], None, im=active, patient=True, wide=True)
            if pt:
                lead, lead_pt = bar, pt
                print(f"found a mob among the party at {round(bar[0])},{round(bar[1])}", file=sys.stderr)
                break
    if lead is None:
        raise RuntimeError("no attackable mob under any green bar (hover check); no click sent")
    if ONLY_NAME:
        print(f"target: {LAST_LABEL.get(lead, '?')}", file=sys.stderr)
    # Verify a unique green bar at the tracker's predicted location in each follower before
    # sending the leader click. This prevents partial attacks on different nearby mobs.
    try:
        raw_offsets = read(os.path.join(state, "offsets")).splitlines()
        offsets = [(0, 0)] + [tuple(map(int, row.split())) for row in raw_offsets if len(row.split()) == 2]
    except ValueError:
        offsets = [(0, 0)] * len(displays)
    offsets = (offsets + [(0, 0)] * len(displays))[:len(displays)]
    points = [lead_pt]
    def follower(i):
        display_name = displays[i]
        fim = screenshot(display_name)
        options = green_bar_candidates(fim)
        dx = offsets[i][0] - offsets[front][0]
        dy = offsets[i][1] - offsets[front][1]
        ranked = [b for b in nearest_bar(options, lead[0] + dx, lead[1] + dy)
                  if (b[0] - lead[0] - dx) ** 2 + (b[1] - lead[1] - dy) ** 2 <= MAX_BAR_DRIFT ** 2]
        lead_dy = int(round(lead[1] + ORIGIN[1] - lead_pt[1]))
        for bar in ranked[:3]:                  # nearest to the prediction that is attackable
            pt = hover_point(display_name, bar[0], bar[1], None, lead_dy, fim)
            if pt:
                return pt
        # Not confirmed in this window: click where it should be (window 1's point + this window's
        # measured offset, like normal broadcast) rather than cancelling the whole attack.
        return (int(round(lead_pt[0] + dx)), int(round(lead_pt[1] + dy)))

    ex = excluded(state)
    others = [i for i in range(len(displays)) if i != front and i not in ex]
    for i in others:
        _dpy(displays[i])                       # open the X connections before the threads start
    with ThreadPoolExecutor(len(others) or 1) as ex:   # each follower is its own X display
        results = list(ex.map(follower, others))
    for r in results:
        if isinstance(r, str):
            raise RuntimeError(r)
    points.extend(results)
    # Reorder into box order. The broadcaster reads one exact game point for each X display.
    by_box = {front: points[0]}
    follower_points = iter(points[1:])
    for i in others:
        by_box[i] = next(follower_points)
    write(os.path.join(state, "target-points"), "".join(f"{i} {p[0]} {p[1]}\n" for i, p in by_box.items()))
    if not allow_combat and in_combat(screenshot(displays[front])):
        raise RuntimeError("combat bar appeared in the active client; no click sent")
    # The followers' checks took ~1 s and flying mobs move: re-check window 1's spot right before
    # the click; if the attack cursor is gone, find the same mob near its old bar and re-aim.
    move(displays[front], *lead_pt)
    time.sleep(0.05)
    if not any(cursor_id(displays[front]) in ATTACK_FRAMES for _ in range(2) if not time.sleep(0.02)):
        fresh = nearest_bar(green_bar_candidates(screenshot(displays[front])), lead[0], lead[1])
        moved = [b for b in fresh[:3] if (b[0] - lead[0]) ** 2 + (b[1] - lead[1]) ** 2 <= 90 ** 2]
        repaired = None
        for b in moved:
            repaired = (hover_point(displays[front], b[0], b[1], None, int(round(lead[1] + ORIGIN[1] - lead_pt[1])))
                        or hover_point(displays[front], b[0], b[1], None))   # full probe: it turned/moved
            if repaired:
                dxm, dym = b[0] - lead[0], b[1] - lead[1]
                points = [repaired] + [(x + int(dxm), y + int(dym)) for x, y in points[1:]]
                by_box = {front: points[0]}
                for i, p in zip(others, points[1:]):
                    by_box[i] = p
                write(os.path.join(state, "target-points"), "".join(f"{i} {p[0]} {p[1]}\n" for i, p in by_box.items()))
                lead, lead_pt = b, repaired
                break
        if repaired is None:
            raise RuntimeError("no attackable mob under any green bar (target moved away); no click sent")
    px, py = lead_pt
    write(os.path.join(state, "exp-before"), exp_signature(screenshot(displays[front])))
    if F1_AT_CLICK:
        # skill on F1 (Lightning Bolt...): select it only now. A selected skill turns the cursor
        # into a skill cursor everywhere, so the hover checks above must run without it.
        cast_f1(state, displays)
        move(displays[front], px, py)
        time.sleep(0.1)
    try:
        write(os.path.join(state, "target-aim"), "1\n")
        # press, hold, release: a fast xdotool click can fall inside one game frame and be missed
        subprocess.run(["xdotool", "mousemove", str(px), str(py), "sleep", "0.06",
                        "mousedown", "1", "sleep", "0.1", "mouseup", "1"],
                       env={**os.environ, "DISPLAY": displays[front]},
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=2)
    except (OSError, subprocess.SubprocessError) as exc:
        try:
            os.remove(os.path.join(state, "target-aim"))
        except OSError:
            pass
        raise RuntimeError(f"could not move/click active client {displays[front]} at {px},{py}: {exc}") from exc
    # Wait for mbcast to consume the one-shot marker and use the validated per-box coordinates.
    aim_marker = os.path.join(state, "target-aim")
    deadline = time.monotonic() + 0.25
    while os.path.exists(aim_marker) and time.monotonic() < deadline:
        time.sleep(0.01)
    consumed = not os.path.exists(aim_marker)
    try:
        os.remove(aim_marker)
    except OSError:
        pass
    write(os.path.join(state, "target-last-bar"), f"{lead[0]} {lead[1]}\n")
    write(os.path.join(state, "target-status"),
          f"sent:green-bar target on {displays[front]} at {px},{py}; "
          f"{'all box points routed' if consumed else 'broadcaster did not consume target points'}\n")
    if not consumed:
        raise RuntimeError("broadcaster did not consume target click; reload multibox and retry")


# Mid-fight ALIGN only for real separation: it walks everyone out of the fight, and clicks are
# shifted per window anyway (3-tile mid-fight aligns lost the target and stopped a 20-kill run).
MIDFIGHT_DRIFT = 6


_last_pos = {}


def _walking(display_name):
    """True while the character's coordinates are still changing (walking to a far target)."""
    p = mbcoords.read(display_name)
    prev = _last_pos.get(display_name)
    _last_pos[display_name] = p
    return p is not None and prev is not None and p != prev


ASSIST_EVERY = 3.0      # s between checks that every follower is really fighting
ASSIST_MAX = 3          # direct clicks per follower per fight


def assist_followers(state, front, displays, bar, assists):
    """A follower without a living target isn't fighting (its broadcast click missed: the mob
    moved, or something covered it). Click the mob for it directly, in that window only: direct
    input into a follower is not mirrored. Window 1 died alone this way while 4 stood idle."""
    offs = read_offsets(state, len(displays))
    ex = excluded(state)
    for i, d in enumerate(displays):
        if i == front or i in ex or assists.get(i, 0) >= ASSIST_MAX:
            continue
        fim = screenshot(d)
        if panel_present(fim) and (hp_value(fim) or (1, 1))[0] > 0:
            assists.pop(("idle", i), None)
            continue                            # fighting
        # idle on two checks in a row: right at the end of a fight the follower already shows the
        # dead mob (0 HP) while window 1's panel lags a frame behind - that's not a missed click
        if not assists.get(("idle", i)):
            assists[("idle", i)] = True
            continue
        if party_hp(fim)[0] == 0.0:
            continue                            # dead: the death handling takes over
        px = bar[0] + offs[i][0] - offs[front][0]
        py = bar[1] + offs[i][1] - offs[front][1]
        cands = [b for b in nearest_bar(green_bar_candidates(fim), px, py)
                 if (b[0] - PLAYER_BAR_X) ** 2 + (b[1] - PLAYER_BAR_Y) ** 2 > 25 ** 2
                 and (b[0] - px) ** 2 + (b[1] - py) ** 2 <= 160 ** 2][:3]
        assists[i] = assists.get(i, 0) + 1
        # the mob may stand inside the party (covered by us): those bars need the patient probe
        inner = [b for b in green_bar_candidates(fim)
                 if (b[0] - PLAYER_BAR_X) ** 2 + (b[1] - PLAYER_BAR_Y) ** 2 <= 90 ** 2]
        near_party = (bar[0] - PLAYER_BAR_X) ** 2 + (bar[1] - PLAYER_BAR_Y) ** 2 <= 90 ** 2
        tries = ([(b, True) for b in inner] + [(b, False) for b in cands]) if near_party else \
                ([(b, False) for b in cands] + [(b, True) for b in inner])
        for b, patient in tries[:6]:
            pt = hover_point(d, b[0], b[1], None, im=fim, wide=True, patient=patient)
            if pt:
                click_at(d, *pt)
                print(f"assist: window {i + 1} wasn't fighting; clicked the mob for it", file=sys.stderr)
                break
        else:
            fim.save(os.path.join(state, f"assist-miss-window{i + 1}.png"))
            write(os.path.join(state, f"assist-miss-window{i + 1}.txt"),
                  f"predicted {px:.0f} {py:.0f} lead {bar[0]:.0f} {bar[1]:.0f} offs {offs[i]} "
                  f"bars {[tuple(map(round, b)) for b in green_bar_candidates(fim)]}\n")
            print(f"assist: window {i + 1} isn't fighting and its mob wasn't found near {round(px)},{round(py)}",
                  file=sys.stderr)


TANK = int(os.environ.get("MB_TANK", "0")) - 1      # 1-based window of the tank (Taunt on F1); 0 = none
TAUNT_EVERY = 6.0


def taunt_check(state, displays, tstate):
    """An Explorer (anyone but the tank) losing HP: the tank presses F1 (Taunt: pulls a single
    mob, 8 tiles) and clicks the mob standing next to that Explorer, on the tank's own screen."""
    if TANK < 0 or TANK >= len(displays):
        return
    ex = excluded(state)
    if TANK in ex:
        return
    hps = {i: own_hp(d) for i, d in enumerate(displays) if i != TANK and i not in ex}
    prev, tstate["hp"] = tstate.get("hp", {}), hps
    hit = [i for i, h in hps.items() if h and prev.get(i) and h < prev[i] - 0.01]
    now = time.monotonic()
    if not hit or now - tstate.get("last", 0) < TAUNT_EVERY:
        return
    d = displays[TANK]
    offs = read_offsets(state, len(displays))
    im = screenshot(d)
    for victim in sorted(hit, key=lambda i: hps[i]):            # the most hurt first
        vx = PLAYER_BAR_X + offs[TANK][0] - offs[victim][0]
        vy = PLAYER_BAR_Y + offs[TANK][1] - offs[victim][1]
        near = [b for b in nearest_bar(green_bar_candidates(im), vx, vy)
                if (b[0] - vx) ** 2 + (b[1] - vy) ** 2 <= 140 ** 2][:5]
        for b in near:
            pt = hover_point(d, b[0], b[1], None, im=im, wide=True, patient=True)
            if pt:
                key(d, "keydown", "F1", "sleep", "0.08", "keyup", "F1")
                time.sleep(0.15)
                click_at(d, *pt)
                tstate["last"] = now
                print(f"taunt: window {victim + 1} was hit; window {TANK + 1} taunted the mob at "
                      f"{round(b[0])},{round(b[1])}", file=sys.stderr)
                return


RECAST_EVERY = float(os.environ.get("RECAST_EVERY", "5.2"))   # Lightning Bolt Lv9: 8 s - 0.4 s per level above 1, + margin


def recast(state, front, displays, track):
    """The target survived the bolts: every Explorer casts again. Per window, on its own screen:
    find its mob near window 1's target (mapped through the offsets), verify the attack cursor
    with no skill selected, then F1 + click (a selected skill hides the attack cursor). Window 1's
    input is mirrored, so the broadcaster is paused for it; followers' direct input isn't."""
    offs = read_offsets(state, len(displays))
    ex = excluded(state)
    cast = 0
    for i, d in enumerate(displays):
        if i in ex or i == TANK:
            continue
        im = screenshot(d)
        if not panel_present(im) or (hp_value(im) or (0, 1))[0] == 0:
            continue                            # this one has no living target (dead mob / none)
        px = track[0] + offs[i][0] - offs[front][0]
        py = track[1] + offs[i][1] - offs[front][1]
        if cursor_id(d) in SKILL_FRAMES:
            clear_selection(state, d)
        pause = os.path.join(state, f"pause.{os.getpid()}") if i == front else None
        if pause:
            write(pause, "1\n")
            time.sleep(0.1)
        try:
            for b in [b for b in nearest_bar(green_bar_candidates(im), px, py)
                      if (b[0] - px) ** 2 + (b[1] - py) ** 2 <= 120 ** 2][:3]:
                pt = hover_point(d, b[0], b[1], None, im=im, wide=True)
                if pt:
                    key(d, "keydown", "F1", "sleep", "0.08", "keyup", "F1")
                    time.sleep(0.12)
                    move(d, *pt)
                    time.sleep(0.05)
                    click_at(d, *pt)
                    cast += 1
                    break
        finally:
            if pause and os.path.exists(pause):
                os.remove(pause)
    if cast:
        print(f"recast: target still alive, {cast} window(s) bolted again", file=sys.stderr)


def wait_death(state, front, displays, iteration, count):
    display_name = displays[front]
    tstate, taunt_checked = {}, 0.0
    recast_at = time.monotonic() + RECAST_EVERY
    try:
        bar_x, bar_y = map(float, read(os.path.join(state, "target-last-bar")).split())
    except ValueError as exc:
        raise RuntimeError("missing selected mob position; aborting kill count") from exc
    deadline = time.monotonic() + 240
    started = time.monotonic()
    seen_alive = False
    zero_reads = 0
    missing_bar_reads = 0
    last_report = 0.0
    drift_reads = 0
    realigned = False
    no_panel_reads = 0
    last_hp, hp_since = None, time.monotonic()
    dead_reads = 0
    ex = excluded(state)
    mapping = party_map(displays, front)
    fighting_threat = read(os.path.join(state, "target-threat")) == "1"
    threat_checked = 0.0
    assists, assist_checked = {}, time.monotonic()
    track = (bar_x, bar_y)                     # window 1's target bar, followed as the mob walks
    while time.monotonic() < deadline:
        im = screenshot(display_name)
        # Somebody in the party died: stop waiting on this target, the main loop recovers them.
        frames = party_hp(im)
        hurt = any(f is not None and f < 0.7 for k, f in enumerate(frames)
                   if k in mapping and mapping[k] not in ex)
        if any(f == 0.0 for k, f in enumerate(frames) if k in mapping and mapping[k] not in ex):
            dead_reads += 1
            if dead_reads >= 2:
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:a party member died\n")
                sys.exit(6)
        else:
            dead_reads = 0
        moved = [b for b in green_bar_candidates(im) if (b[0] - track[0]) ** 2 + (b[1] - track[1]) ** 2 <= 50 ** 2]
        if moved:
            track = min(moved, key=lambda b: (b[0] - track[0]) ** 2 + (b[1] - track[1]) ** 2)
        if (time.monotonic() - started > 2.5 and time.monotonic() - assist_checked > ASSIST_EVERY
                and hp_value(im) and hp_value(im)[0] > 0 and panel_present(im)):
            assist_checked = time.monotonic()
            assist_followers(state, front, displays, track, assists)
        if F1_AT_CLICK and time.monotonic() >= recast_at:
            recast_at = time.monotonic() + RECAST_EVERY
            if panel_present(im) and (hp_value(im) or (0, 1))[0] > 0:
                recast(state, front, displays, track)
        if TANK >= 0 and time.monotonic() - taunt_checked > 1.0:
            taunt_checked = time.monotonic()
            taunt_check(state, displays, tstate)
        check = time.monotonic() - threat_checked > 3.0
        if check and F1_AT_CLICK:
            # an Explorer out of SP can't cast and dies standing there (LuckyForth, 0/313 SP,
            # killed by an Owlie the rest weren't hitting): break off, rest, come back
            sp, hpn = party_sp(im), party_hp(im)
            dry = [k for k, (a, b) in enumerate(zip(sp, hpn))
                   if a is not None and b is not None and a < 0.1 and 0 < b < 0.6
                   and (k not in mapping or mapping[k] not in ex) and mapping.get(k) != TANK]
            if dry:
                write(os.path.join(state, "target-status"),
                      f"AVOID: a party member is out of SP and losing HP, breaking off\n")
                sys.exit(8)
        if check:
            threat_checked = time.monotonic()
            a = avoid_bar(im)
            if a is not None:
                write(os.path.join(state, "target-status"), f"AVOID: {AVOID_NAME} at {round(a[0])},{round(a[1])}, retreating\n")
                sys.exit(8)
        try:                                    # the tank taunted another mob: switch to it now
            ts_, tx_, ty_ = map(float, read(os.path.join(state, "tank-target")).split())
            if time.time() - ts_ < 6 and (tx_ - track[0]) ** 2 + (ty_ - track[1]) ** 2 > 60 ** 2:
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:tank taunted, switching\n")
                sys.exit(7)
        except (ValueError, OSError):
            pass
        if not fighting_threat and check and not ONLY_NAME and not F1_AT_CLICK:   # quest hunting / tank party: no mid-fight switch on red names
            t = threat_bar(im)
            if t is not None and (t[0] - bar_x) ** 2 + (t[1] - bar_y) ** 2 > 60 ** 2:
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:THREAT appeared, switching\n")
                sys.exit(7)
        panel = panel_present(im)
        hp = hp_value(im) if panel else None
        # No target panel at all after the grace period: not in combat. Don't wait on green bars
        # of other mobs that happen to stand near the old spot (this used to hang for 4 minutes).
        no_panel_reads = 0 if panel else no_panel_reads + 1
        walking = _walking(display_name)       # still walking to a far target: not a miss yet
        if time.monotonic() - started > (10 if walking else 2.5) and no_panel_reads >= 3:
            if not seen_alive and was_kill(state, display_name):
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:dead (fast kill, EXP rose)\n")
                return
            if not seen_alive:                  # never selected anything: a miss, not a kill
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:click missed (no target), retrying\n")
                blacklist_last(state)
                save_miss(state, display_name)
                sys.exit(4)
            write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:no combat\n")
            return
        # Selected but nothing happens (HP not dropping for 10 s): a stale selection. Clear it.
        if hp != last_hp:
            last_hp, hp_since = hp, time.monotonic()
        elif panel and time.monotonic() - hp_since > 10:
            clear_selection(state, display_name)
            return
        bar_near = any((x - bar_x) ** 2 + (y - bar_y) ** 2 <= 72 ** 2
                       for x, y in green_bar_candidates(im))
        spread = read(os.path.join(state, "aligned"))
        target_alive = (hp is not None and hp[0] > 0) or (hp is None and bar_near)
        # (not while anyone is badly hurt: they are fighting something else, and walking away
        # from it under ALIGN gets them killed; the defend logic handles that)
        if not realigned and not hurt and target_alive and spread.isdigit() and int(spread) > MIDFIGHT_DRIFT:
            drift_reads += 1
        else:
            drift_reads = 0
        if drift_reads >= 2:
            write(os.path.join(state, "target-status"),
                  f"aligning:{iteration}/{count}:party drifted {spread} tiles\n")
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multibox")
            try:
                subprocess.run([script, "align"], check=True, timeout=120, env={**os.environ, "MB_ALIGN_TOL": "1"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(f"auto-align failed during combat recovery: {exc}") from exc
            align_result = read(os.path.join(state, "align"))
            if not (align_result.startswith("aligned at ") or align_result.startswith("almost: off by 1 tile")):
                raise RuntimeError(f"auto-align did not finish successfully: {align_result}")
            # Wait for mbtrack to publish the new offsets before validating follower bars.
            for _ in range(20):
                aligned_now = read(os.path.join(state, "aligned"))
                if aligned_now == "yes" or (aligned_now.isdigit() and int(aligned_now) <= 1):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("ALIGN finished but the tracker still reports party separation")
            # ALIGN interrupts the attack. Reacquire and click once to resume this same
            # count slot; bypass only the combat guard because the target is still alive.
            try:
                kill(state, front, displays, allow_combat=True)
            except RuntimeError as exc:
                # target gone after walking away: retry this kill slot via the normal search
                write(os.path.join(state, "target-status"),
                      f"waiting:{iteration}/{count}:target lost after ALIGN ({exc}), retrying\n")
                sys.exit(4)
            try:
                bar_x, bar_y = map(float, read(os.path.join(state, "target-last-bar")).split())
            except ValueError:
                raise RuntimeError("could not reacquire target after ALIGN")
            realigned = True
            drift_reads = 0
            started = time.monotonic()
            seen_alive = False
            zero_reads = 0
            missing_bar_reads = 0
            last_report = 0.0
            continue
        if hp and hp[0] > 0:
            seen_alive = True
            zero_reads = 0
            missing_bar_reads = 0
        elif hp and hp[0] == 0:
            zero_reads += 1
            # The selected target panel is authoritative. Other nearby mobs can leave green
            # pixels close to the saved location after this target has died.
            if zero_reads >= 2 and (seen_alive or not bar_near):
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:dead\n")
                return
        elif not panel and seen_alive:
            missing_bar_reads += 1
            if missing_bar_reads >= 2:
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:dead\n")
                return
        # If HP OCR misses the panel, a green bar disappearing after the click is the visual
        # death signal. Require an initial grace period and several consecutive absent frames.
        if time.monotonic() - started > 2.0 and not bar_near:
            missing_bar_reads += 1
            if missing_bar_reads >= 4:
                write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:mob gone\n")
                return
        elif bar_near:
            missing_bar_reads = 0
        if time.monotonic() - started > (10 if walking else 3) and not seen_alive and hp and hp[0] == 0 \
                and was_kill(state, display_name):
            write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:dead (fast kill, EXP rose)\n")
            return
        if time.monotonic() - started > (10 if walking else 3) and not seen_alive and hp and hp[0] == 0:
            # Only the previous (dead) target's panel is showing: our click didn't start a fight.
            write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:click missed, retrying\n")
            blacklist_last(state)
            save_miss(state, display_name)
            sys.exit(4)
        if time.monotonic() - last_report >= 1:
            hp_text = f"{hp[0]}/{hp[1]} HP" if hp else (
                "target bar visible; HP unreadable" if bar_near else "waiting for target bar")
            write(os.path.join(state, "target-status"), f"waiting:{iteration}/{count}:{hp_text}\n")
            last_report = time.monotonic()
        time.sleep(0.5)
    raise RuntimeError("timed out waiting for target HP to reach zero; stopping the kill count")


def wait_out_of_combat(state, display_name, iteration, count):
    """Keep a manual KILL request pending until the active client's target is dead/cleared."""
    deadline = time.monotonic() + 240
    last_report = 0.0
    last_hp, hp_since = None, time.monotonic()
    while time.monotonic() < deadline:
        im = screenshot(display_name)
        if not panel_present(im) or party_hp(im)[0] == 0.0:   # no fight, or window 1 is dead
            return
        hp = hp_value(im)
        if hp and hp[0] == 0:
            return
        if hp != last_hp:
            last_hp, hp_since = hp, time.monotonic()
        elif time.monotonic() - hp_since > 8:     # selected, but no combat: clear and go on
            clear_selection(state, display_name)
            return
        if time.monotonic() - last_report >= 1:
            state_text = f"combat HP {hp[0]}/{hp[1]}" if hp else "reading combat HP"
            write(os.path.join(state, "target-status"),
                  f"waiting:{iteration}/{count}:{state_text}\n")
            last_report = time.monotonic()
        time.sleep(0.4)
    raise RuntimeError("timed out waiting for combat to end; no new mob was clicked")


# Captcha / unexpected dialog on a client's right side (BPO's occasional human check). Only
# detected and reported: KILL pauses and alerts you; solving it is up to you. Any BPO dialog has
# a large beige content area; the game view there never shows more than ~200 such pixels.
POPUP_REGION = (840, 60, 1320, 680)
POPUP_MIN_AREA = 4000


def captcha_box(im):
    """Centre of BPO's "Image Captcha" title bar (template from the user's PyBot), or None."""
    return find_image(im, "mbcaptcha.png", 0.85)


def popup_panel(im):
    """Bounding box (x, y, w, h) of a dialog panel open on the right side, or None."""
    x0, y0, x1, y1 = POPUP_REGION
    a = np.asarray(im.convert("RGB")).astype(int)[y0:y1, x0:x1]
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    beige = ((r > 215) & (g > 205) & (b > 180) & (r - b > 12) & (r - b < 60)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(beige, connectivity=8)
    recap = find_image(im, "mbrecap.png")      # a Death Recap is never the captcha
    for i in range(1, n):
        x, y, w, h, area = map(int, st[i])
        if area >= POPUP_MIN_AREA and w >= 100 and h >= 60:
            if recap and x0 + x - 60 <= recap[0] <= x0 + x + w + 60 and recap[1] < y0 + y:
                continue
            return x0 + x, y0 + y, w, h
    return None


CLOSE_TEMPLATES = ("mbx.png", "mbx2.png", "mbx3.png")   # dialog title-bar (x): Daily Buff style, Death Recap style
CLOSE_SCORE = 0.85                          # real buttons 1.0, game scenery <= 0.5


def _find_close(im):
    best = None
    g = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
    for f in CLOSE_TEMPLATES:
        tpl = cv2.imread(os.path.join(os.path.dirname(os.path.abspath(__file__)), f), cv2.IMREAD_GRAYSCALE)
        r = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(r)
        if score >= CLOSE_SCORE and (best is None or score > best[0]):
            best = (score, loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2)
    return best


# Dialog titles that are safe to close (OCR of the title bar, lowercase fragments)
KNOWN_DIALOGS = ("quest", "est log", "monster", "coordinat", "inventory", "trade", "dialogue",
                 "daily", "buff", "recap", "death", "party", "map", "search", "character", "skill",
                 "notice", "event", "reward", "temp bag", "mail", "friend", "guild", "ranking")


def close_popups(state, i, display_name):
    """Close any dialog with a title-bar (x) (Daily Buff, Death Recap, notices...). A right-side
    panel (the captcha) is left alone: that one is for the player. Returns how many were closed."""
    closed = 0
    for _ in range(4):
        im = screenshot(display_name)
        if captcha_box(im):                     # never close the captcha: it's for the player
            break
        hit = _find_close(im)
        if not hit:
            break
        _, x, y = hit
        title = ocr(im.crop((max(0, x - 330), y - 9, x - 20, y + 9)), 7, 3).lower()
        known = any(w in title for w in KNOWN_DIALOGS)
        panel = popup_panel(im)
        if not known and panel and panel[0] - 40 <= x <= panel[0] + panel[2] + 40 and y < panel[1] + panel[3]:
            break                               # an unknown right-side panel: maybe the captcha
        im.save(os.path.join(state, f"popup-window{i + 1}.png"))
        pause = os.path.join(state, f"pause.{os.getpid()}")
        if i == 0:                              # window 1's clicks are mirrored: pause that
            write(pause, "1\n")
            time.sleep(0.25)
        try:
            click_at(display_name, ORIGIN[0] + x, ORIGIN[1] + y)
            time.sleep(0.6)
        finally:
            if i == 0:
                os.remove(pause)
        closed += 1
    return closed


def main(argv):
    mode = argv[1]
    if len(argv) > 2 and os.path.isdir(argv[2]):
        os.environ.setdefault("MB_STATE", argv[2])   # party-map cache, excluded windows
    if mode == "kill":
        _, _, state, front, *displays = argv
        # wait-combat runs first and already waited for any real fight; a leftover selection that
        # isn't fighting (panel stays, HP unchanged) used to make this refuse forever
        kill(state, int(front), displays, allow_combat=True)
    elif mode == "revive-all":   # revive every dead window in town and rest there; no travel
        _, _, state, *displays = argv
        dead = [(i, d) for i, d in enumerate(displays) if i not in excluded(state) and own_hp(d) == 0.0]
        with ThreadPoolExecutor(max(1, len(dead))) as pool:
            list(pool.map(lambda w: revive(state, *w), dead))
        for _, d in enumerate(displays):
            close_recap(d)
        rest_until_full(state, [(i, d) for i, d in enumerate(displays) if i not in excluded(state)],
                        "town", limit=600)
    elif mode == "goto":         # everyone (not excluded) auto-traces to x,y and arrives
        _, _, state, x, y, *displays = argv
        ex = excluded(state)
        live = [d for i, d in enumerate(displays) if i not in ex]
        with ThreadPoolExecutor(len(live)) as pool:
            ok = list(pool.map(lambda d: trace(d, int(x), int(y)), live))
        if not all(ok):
            raise RuntimeError(f"auto-trace refused in window(s) "
                               f"{', '.join(str(displays.index(d) + 1) for d, o in zip(live, ok) if not o)}")
        _arrive(state, live, int(x), int(y), f"going to {x},{y}")
        status(state, f"goto:arrived at {x},{y}")
    elif mode == "popups":       # close stray dialogs in every window; prints what was closed
        _, _, state, *displays = argv
        for i, d in enumerate(displays):
            if i not in excluded(state):
                n = close_popups(state, i, d)
                if n:
                    print(f"closed {n} popup(s) in window {i + 1} (screenshot popup-window{i + 1}.png)")
    elif mode == "captcha":      # print "window x y w h" of the first client with a right-side dialog
        _, _, state, *displays = argv
        for i, d in enumerate(displays):
            if i in excluded(state):
                continue
            im = screenshot(d)
            box = captcha_box(im)   # exact title template; the old beige-panel guess misfired
            if box:
                im.save(os.path.join(state, f"captcha-window{i + 1}.png"))
                print(i + 1, *box)
                sys.exit(0)
        sys.exit(1)
    elif mode == "pickup":       # every window presses A: picks up all drops within 7 tiles
        _, _, state, *displays = argv
        for _ in range(2):      # the drop appears after the death animation (the tank fetches the rest)
            time.sleep(1.2)
            for i, d in enumerate(displays):
                if i not in excluded(state):
                    key(d, "keydown", "a", "sleep", "0.1", "keyup", "a")
    elif mode == "clear":        # drop a leftover target selection in one window
        _, _, state, display_name = argv
        clear_selection(state, display_name)
    elif mode == "wait-death":
        _, _, state, front, iteration, count, *displays = argv
        wait_death(state, int(front), displays, int(iteration), count)
    elif mode == "wait-combat":
        _, _, state, display_name, iteration, count = argv
        wait_out_of_combat(state, display_name, int(iteration), count)
    elif mode == "return-home":  # walk everyone back near the FIXATE spot if outside the radius
        _, _, state, *displays = argv
        radius = None
        if displays and displays[0].startswith("--radius="):
            radius = int(displays.pop(0).split("=")[1])
        return_home(state, displays, radius)
    elif mode == "danger":       # print the index of a window in danger, or -1
        _, _, state, front, *displays = argv
        os.environ["MB_STATE"] = state
        print(danger_window(displays, int(front)))
    elif mode == "deaths":       # print dead window indices (space separated)
        _, _, state, front, *displays = argv
        print(" ".join(map(str, dead_windows(state, displays, int(front)))))
    elif mode == "group-recover":   # safespot flow (see group_recover)
        state, front, sx, sy, tx, ty, sp = argv[2], int(argv[3]), int(argv[4]), int(argv[5]), int(argv[6]), int(argv[7]), argv[8] == "sp"
        os.environ["MB_STATE"] = state
        group_recover(state, front, argv[9:], (sx, sy), (tx, ty), sp)
    elif mode == "recover":      # recover a dead window: revive, rest, auto-trace back
        _, _, state, idx, display_name, tx, ty = argv
        recover(state, int(idx), display_name, int(tx), int(ty))
    elif mode == "cycle":        # wait-combat + FIXATE walk-home + precheck in one process
        state, front, threshold, sp, iteration, count = argv[2], int(argv[3]), float(argv[4]), argv[5] == "sp", argv[6], argv[7]
        displays = argv[8:]
        os.environ["MB_STATE"] = state
        wait_out_of_combat(state, displays[front], int(iteration), count)
        homed = 0
        if os.path.exists(os.path.join(state, "anchor")):
            before = read(os.path.join(state, "target-status"))
            return_home(state, displays)
            homed = int(read(os.path.join(state, "target-status")) != before and
                        read(os.path.join(state, "target-status")).startswith("homing:"))
        for i in crashed_windows(state, len(displays)):
            if i not in excluded(state):
                set_excluded(state, i, True)
                try:
                    os.remove(os.path.join(state, "party-map"))   # the party frames shifted
                except OSError:
                    pass
                print(f"window {i + 1}'s game client is gone (crashed?); continuing without it. "
                      f"Restart it with: multibox restart {i + 1}", file=sys.stderr)
        deaths = dead_windows(state, displays, front)
        danger = danger_window(displays, front)
        ex = excluded(state)
        mapping = party_map(displays, front)
        skip = tuple(k for k, w in mapping.items() if w in ex)
        low = party_low(screenshot(displays[front]), sp, skip)
        print(",".join(map(str, deaths)) or "-", danger, int(low is not None and low < threshold), homed)
    elif mode == "precheck":     # deaths, danger and low HP/SP in one process
        state, front, threshold, sp = argv[2], int(argv[3]), float(argv[4]), argv[5] == "sp"
        displays = argv[6:]
        os.environ["MB_STATE"] = state
        deaths = dead_windows(state, displays, front)
        danger = danger_window(displays, front)
        ex = excluded(state)
        mapping = party_map(displays, front)
        skip = tuple(k for k, w in mapping.items() if w in ex)
        low = party_low(screenshot(displays[front]), sp, skip)
        print(" ".join(map(str, deaths)) or "-", danger, int(low is not None and low < threshold))
    elif mode == "hp-low":       # exit 0 if anyone in the party is below the threshold
        # optional 6th arg "sp": also check SP (F1 mode)
        state, display_name, threshold = argv[2], argv[3], argv[4]
        with_sp = len(argv) > 5 and argv[5] == "sp"
        skip = ()
        if excluded(state):                      # frames of windows that are travelling back
            displays = [f":{int(display_name[1:]) + i}" for i in range(8)]
            mapping = party_map([d for d in displays if os.path.exists(f"/tmp/.X11-unix/X{d[1:]}")], 0)
            skip = tuple(k for k, w in mapping.items() if w in excluded(state))
        low = party_low(screenshot(display_name), with_sp, skip)
        sys.exit(0 if low is not None and low < float(threshold) else 3)
    elif mode == "f1-empty":     # print windows (1-based) whose F1 hotbar slot is empty
        _, _, *displays = argv
        empty = []
        for i, d in enumerate(displays):
            a = np.asarray(screenshot(d).crop(F1_SLOT).convert("L")).astype(float)
            if a.std() < 10:                    # plain beige: nothing assigned
                empty.append(str(i + 1))
        print(" ".join(empty))
    elif mode == "calm":         # exit 0 if nobody lost HP during the last 3 s (safe to sit)
        _, _, state, display_name = argv
        first = party_hp(screenshot(display_name))
        time.sleep(3)
        now = party_hp(screenshot(display_name))
        hit = any(a is not None and b is not None and b < a - 0.01 for a, b in zip(first, now))
        sys.exit(3 if hit else 0)
    elif mode == "rest-wait":    # wait until everyone is (nearly) full; give up after 5 minutes
        state, display_name, iteration, count = argv[2], argv[3], argv[4], argv[5]
        with_sp = len(argv) > 6 and argv[6] == "sp"
        what = "HP/SP" if with_sp else "HP"
        deadline = time.monotonic() + (600 if with_sp else 300)
        best = {}
        while time.monotonic() < deadline:
            im = screenshot(display_name)
            # attacked while sitting: someone's HP drops instead of refilling -> stop resting, defend
            for k, f in enumerate(party_hp(im)):
                if f is None:
                    continue
                if f < best.get(k, f) - 0.03:
                    write(os.path.join(state, "target-status"), f"resting:{iteration}/{count}:attacked while resting\n")
                    sys.exit(5)
                best[k] = max(best.get(k, f), f)
            low = party_low(im, with_sp)
            write(os.path.join(state, "target-status"),
                  f"resting:{iteration}/{count}:lowest {what} {round((low or 0) * 100)}%\n")
            if low is not None and low >= float(os.environ.get("REST_UNTIL", "0.8")):   # 80% = 7 bolts; full took 3 min
                return
            time.sleep(1.0)
        raise RuntimeError(f"{what} did not refill in time while resting")
    else:
        raise RuntimeError("usage: mbtarget.py kill STATE FRONT DISPLAY... | wait-death/wait-combat STATE DISPLAY ITERATION COUNT")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as exc:
        state = sys.argv[2] if len(sys.argv) > 2 else None
        if state:
            try:
                write(os.path.join(state, "target-status"), "error:" + str(exc) + "\n")
            except OSError:
                pass
        print(str(exc), file=sys.stderr)
        sys.exit(1)

# --- mobs by their name labels -------------------------------------------------------------------
# Every mob's label starts with "Lv<level>" (white for its level band, purple when well above us)
# and is drawn over trees; players' names never start with "Lv". A green bar is only a mob when
# such a label sits right above it: our own characters and other players are never targets.
_LV_TPL = None


def _text_mask(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    white = (r > 200) & (g > 200) & (b > 200)
    purple = (r > 150) & (b > 150) & (g < 110) & (r - g > 80)
    red = (r > 200) & (g < 60) & (b < 60)       # labels of mobs well above our level
    # (217,255,119): mobs a few levels below ours (Elk L31 for a level-34 party, 2026-10-14);
    # missing them left the party idle among Elks
    green = (g > 240) & (r > 190) & (r < 240) & (b > 90) & (b < 150)
    green |= (g > 220) & (r < 80) & (b < 80)    # (0,255,12): far below our level (Elk L31 for a level-36 party)
    # (250,200,0): mobs a few levels above ours (Steel-shell Snail L44 for a level-40/41 party, 2026-10-17)
    yellow = (r > 235) & (g > 185) & (g < 225) & (b < 50)
    return (white | purple | red | green | yellow).astype(np.float32)


def mob_labels(im):
    """(x, y) of the "Lv" at the start of every mob name label on screen."""
    import cv2
    global _LV_TPL
    if _LV_TPL is None:
        _LV_TPL = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mblv.npy")).astype(np.float32)
    m = _text_mask(np.asarray(im.convert("RGB")).astype(int))
    m[:400, :300] = 0                           # party frames
    m[:60, :] = 0
    m[570:, :300] = 0                           # chat
    m[680:, :] = 0                              # skill bar
    m[:, 1215:] = 0                             # minimap / right icons
    res = cv2.matchTemplate(m, _LV_TPL, cv2.TM_CCORR_NORMED)
    ys, xs = np.where(res > 0.85)
    out = []
    for x, y in sorted(zip(xs.tolist(), ys.tolist()), key=lambda p: -res[p[1], p[0]]):
        if all(abs(x - px) > 10 or abs(y - py) > 6 for px, py in out):
            out.append((x, y))
    return out


BAR_FULL_W = 56                     # a mob's health bar at 100% (px); the fill shrinks to the right


def hp_bars(im):
    """Health bars at ANY fill level: green when full, yellow/orange/red as HP drops (only green
    used to count: a damaged mob - the one attacking us - was invisible). A bar is a 2-4 px tall
    fill with a dark frame row above and below; its centre is its left edge + half a full bar."""
    x0, y0, x1, y1 = WORLD
    rgb = np.asarray(im.convert("RGB")).astype(int)[y0:y1, x0:x1]
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    green = (g > 95) & (r < 45) & (b < 45) & (g > 2.5 * np.maximum(r, b))
    warm = (r > 150) & (b < 70) & (r - b > 110) & (g > 40)
    fill = (green | warm).astype(np.uint8)
    fill[max(0, CHAT_BOX[1] - y0):, :max(0, CHAT_BOX[2] - x0)] = 0
    fill = cv2.morphologyEx(fill, cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8))
    lum = rgb.sum(axis=2) / 3
    count, _, stats, _ = cv2.connectedComponentsWithStats(fill, connectivity=8)
    bars = []
    for i in range(1, count):
        bx, by, w, h, _ = map(int, stats[i])
        if not (2 <= h <= 4 and 4 <= w <= MAX_BAR_WIDTH and w >= 2 * h):
            continue
        if by < 1 or by + h >= lum.shape[0]:
            continue
        xs = slice(bx, bx + w)
        if lum[by - 1, xs].mean() > 80 or lum[by + h, xs].mean() > 80:
            continue                            # no dark frame: grass, flowers, text
        bars.append((x0 + bx + BAR_FULL_W / 2, y0 + by + h / 2))
    bars.sort()
    out = []                                    # one bar split by its "%" text: keep the left piece
    for bx, by in bars:
        if any(abs(by - oy) <= 2 and 0 <= bx - ox < BAR_FULL_W - 4 for ox, oy in out):
            continue
        out.append((bx, by))
    return out


def low_level_bars(im):
    """Mob bars whose name label is WHITE (mobs at or below our level: here the aggressive Killer
    Shrooms) - they are killed first, before they walk into the party."""
    a = np.asarray(im.convert("RGB")).astype(int)
    out = []
    for b in mob_bars(im):
        x0, y0 = int(b[0] - 60), int(b[1] - 50)
        crop = a[max(0, y0):int(b[1] - 18), max(0, x0):int(b[0] + 40)]
        if crop.size == 0:
            continue
        r, g, bl = crop[..., 0], crop[..., 1], crop[..., 2]
        white = ((r > 200) & (g > 200) & (bl > 200)).sum()
        red = ((r > 200) & (g < 60) & (bl < 60)).sum()
        purple = ((r > 150) & (bl > 150) & (g < 110)).sum()
        if white > 25 and white > 2 * (red + purple):
            out.append(b)
    return out


def mob_bars(im):
    """Green bars that belong to mobs (a "Lv" label 22-75 px above, left of the bar's centre)."""
    labels = mob_labels(im)
    out = []
    for b in hp_bars(im):
        if any(10 <= b[0] - lx <= 120 and 22 <= b[1] - ly <= 85 for lx, ly in labels):   # (Elk: 76)
            out.append(b)
    return out


TRACE_BOX = (280, 180, 540, 350)          # where the Alt+R auto-trace dialog opens


def trace_open(im):
    """The auto-trace dialog is open (checked in its usual spot only: fast)."""
    x0, y0, x1, y1 = TRACE_BOX
    return find_image(im.crop(TRACE_BOX), "mbtrace.png") is not None


def pink_mobs(im, min_px=25):
    """Centres of the magenta mobs (the target mob's texture swapped for solid (253,0,202), the
    PyBot way: texture/character/<framework>000000.bmp), biggest first. Shading keeps them in
    r>150, g<90, b>100. The interface areas are masked like the label search."""
    import cv2
    a = np.asarray(im.convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = ((r > 150) & (b > 100) & (g < 90) & (r - g > 100) & (b - g > 60)).astype(np.uint8)
    m[:400, :300] = 0                           # party frames
    m[:60, :] = 0
    m[570:, :300] = 0                           # chat
    m[680:, :] = 0                              # skill bar
    m[:, 1215:] = 0                             # minimap / right icons
    m = cv2.dilate(m, np.ones((5, 5), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(m)
    out = [(float(cents[k][0]), float(cents[k][1]), int(stats[k][4])) for k in range(1, n)
           if stats[k][4] >= min_px]
    return [(x, y) for x, y, _ in sorted(out, key=lambda c: -c[2])]


def pink_points(im, min_px=25):
    """For each magenta mob: (centre, [points on it to try]) - the centre can be covered by a
    character standing on the mob, so a few more points spread over the blob."""
    import cv2
    a = np.asarray(im.convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    raw = ((r > 150) & (b > 100) & (g < 90) & (r - g > 100) & (b - g > 60)).astype(np.uint8)
    raw[:400, :300] = 0
    raw[:60, :] = 0
    raw[570:, :300] = 0
    raw[680:, :] = 0
    raw[:, 1215:] = 0
    m = cv2.dilate(raw, np.ones((5, 5), np.uint8))
    n, lab, stats, cents = cv2.connectedComponentsWithStats(m)
    out = []
    for k in range(1, n):
        if stats[k][4] < min_px:
            continue
        ys, xs = np.nonzero((lab == k) & (raw > 0))
        if len(xs) == 0:
            continue
        cx, cy = float(cents[k][0]), float(cents[k][1])
        pts = [(cx, cy)]
        for q in (0.2, 0.8, 0.5, 0.35, 0.65):           # pink pixels spread along the blob
            i = int(q * (len(xs) - 1))
            pts.append((float(xs[i]), float(ys[i])))
        out.append(((cx, cy), pts))
    return out
