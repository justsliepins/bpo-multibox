#!/usr/bin/env python3
"""Windows the server dropped ("Disconnected with server", or back at the login screen): log them
in again, walk them back to the party and re-invite them. Network drops on the laptop's Wi-Fi
(2026-10-14) left windows on the dialog for half an hour while the leader waited for them.

Used by mbsuper: gone() finds the windows, recover() brings them back (leader stopped,
broadcast off - the caller handles both).
"""
import difflib
import io
import os
import subprocess
import time

import numpy as np
from PIL import Image

import mbcoords
import mbrelog as r
import mbtarget as t

STATE = r.STATE
D = r.DISPLAYS
INVITER = 1                                 # LuckyOne leads the party (2026-10-15 evening)


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "reconnect: " + msg + "\n")


def state(w):
    """'dialog' (the disconnect message), 'out' (login / character screen) or 'ok'."""
    d = D[w - 1]
    try:
        im = t.screenshot(d)
    except Exception:
        return "ok"                         # a dead X server is the rebox job, not this one
    if "isconnect" in t.ocr(im.crop((470, 355, 900, 400)), 7, 2):
        return "dialog"
    if mbcoords.read(d) is None and np.asarray(im.convert("L")).max() > 10:
        return "out"
    return "ok"


def gone():
    """Windows dropped by the server. 'out' must be seen twice 20 s apart (loading screens)."""
    first = {w: state(w) for w in r.CHARS}
    out = [w for w, s in first.items() if s == "dialog"]
    maybe = [w for w, s in first.items() if s == "out"]
    if maybe:
        time.sleep(20)
        out += [w for w in maybe if state(w) != "ok"]
    return sorted(out)


def login(w):
    d = D[w - 1]
    if state(w) == "dialog":
        r.click(d, 682, 420)                # Confirm: back to the login screen
        time.sleep(5)
    if r.login(w):
        return True
    for _ in range(3):                      # the character slot click sometimes needs a repeat
        r.click(d, 1060, 520)
        time.sleep(1.2)
        r.click(d, 707, 730)
        for _ in range(20):
            time.sleep(2)
            if r.in_game(d):
                time.sleep(2)
                r.click(d, 681, 338)        # the news dialog's Exit
                t.key(d, "keydown", "ctrl", "sleep", "0.05", "key", "b", "sleep", "0.05", "keyup", "ctrl")
                log(f"window {w}: in the game at {mbcoords.read(d)}")
                return True
    return False


def walk(ws, target):
    """The planned safe-path walk for the relogged windows (a few stood at the haven)."""
    far = [D[w - 1] for w in ws if (p := mbcoords.read(D[w - 1])) and max(abs(p[0] - target[0]), abs(p[1] - target[1])) > 6]
    if far:
        subprocess.run(["python3", os.path.join(r.HERE, "mbtarget.py"), "goto", STATE,
                        str(target[0]), str(target[1]), *far], cwd=r.HERE, timeout=1500,
                       env={**os.environ, "MB_STATE": STATE}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def names_on_screen(im):
    """(text, x, y) of white words (player names) on a game screenshot (tesseract's TSV)."""
    a = np.asarray(im.convert("RGB")).astype(int)
    m = (a[..., 0] > 200) & (a[..., 1] > 200) & (a[..., 2] > 200)
    m[:400, :300] = False                   # party frames
    m[570:, :300] = False                   # chat
    m[680:, :] = False
    m[:, 1215:] = False
    m[:60, :] = False
    img = Image.fromarray(np.where(m, 0, 255).astype(np.uint8)).resize((im.width * 2, im.height * 2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=buf.getvalue(),
                         capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    res = []
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and f[11].strip():
            res.append((f[11], (int(f[6]) + int(f[8]) / 2) / 2, (int(f[7]) + int(f[9]) / 2) / 2))
    return res


def in_party(w):
    """Window w's own screen shows party frames under its portrait."""
    im = t.screenshot(D[w - 1])
    txt = t.ocr(im.crop((50, 135, 200, 420)), 6, 2).lower()
    return any(difflib.SequenceMatcher(None, c.lower(), word).ratio() > 0.6
               for _, c in r.CHARS.values() for word in txt.split())


def menu_item(im, x0, y0, word):
    """Centre of the first menu word starting with `word` in the box right/below (x0, y0)."""
    box = (max(0, x0 - 20), max(0, y0 - 10), min(im.width, x0 + 180), min(im.height, y0 + 190))
    crop = im.crop(box).convert("L").resize(((box[2] - box[0]) * 3, (box[3] - box[1]) * 3))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=buf.getvalue(),
                         capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and f[11].strip().lower().startswith(word):
            return (box[0] + (int(f[6]) + int(f[8]) / 2) / 3, box[1] + (int(f[7]) + int(f[9]) / 2) / 3)
    return None


def click_party_invite(lead, hit):
    """Right-click the hovered player, read the menu, click "Party Invite" (its place varies: a
    fixed offset hit "Add Friend" now and then)."""
    t.key(lead, "mousedown", "3", "sleep", "0.08", "mouseup", "3")
    time.sleep(0.8)
    pos = menu_item(t.screenshot(lead), int(hit[0]), int(hit[1]), "party")
    if not pos:
        r.click(lead, *t.CLEAR_SPOTS[0])      # close the menu on open ground... (a click there walks)
        return False
    r.click(lead, int(pos[0]) + 10, int(pos[1]))
    time.sleep(1.5)
    return True


def invite(w, inviter=INVITER):
    """The inviter right-clicks w's character (found by its name label) -> Party Invite; w accepts."""
    lead, d = D[inviter - 1], D[w - 1]
    O = t.ORIGIN
    name = r.CHARS[w][1].lower()
    for attempt in range(4):
        found = [(x, y) for text, x, y in names_on_screen(t.screenshot(lead))
                 if difflib.SequenceMatcher(None, text.lower(), name).ratio() > 0.7]
        hit = None
        for x, y in found[:1]:
            for oy in range(14, 50, 5):         # the body is below its name label
                for ox in (0, -6, 6, -12, 12):
                    t.move(lead, O[0] + x + ox, O[1] + y + oy)
                    time.sleep(0.08)
                    if t.cursor_id(lead) == r.PLAYER_CURSOR:
                        hit = (x + ox, y + oy)
                        break
                if hit:
                    break
        if not hit:
            # not on the inviter's screen, or another player stands on it: step beside the inviter
            p = mbcoords.read(lead)
            if p:
                t._trace(d, p[0] + 3 + attempt, p[1] + 3 - attempt)
                time.sleep(4)
            continue
        if not click_party_invite(lead, hit):
            continue
        for y in (217, 197, 206):               # Accept (the prompt sits at one of these heights)
            r.click(d, 104, y)
            time.sleep(0.4)
        time.sleep(1.5)
        if in_party(w):
            log(f"window {w}: back in the party")
            return True
    log(f"window {w}: could not re-invite")
    return False


def toggle_players(w):
    t.key(D[w - 1], "keydown", "ctrl", "sleep", "0.05", "key", "b", "sleep", "0.05", "keyup", "ctrl")
    time.sleep(1.2)


def recover(ws, camp):
    """Log ws in again, walk them to camp, re-invite. Returns the windows that are back."""
    back = []
    for w in ws:
        log(f"window {w}: dropped by the server: logging in again")
        if login(w):
            back.append(w)
        else:
            log(f"window {w}: login failed")
    if not back:
        return back
    walk(back, camp)
    # the party: window 4 invites. When window 4 itself was dropped the old party lives on
    # without it, led by someone else: that window invites window 4 back instead
    inviters = [INVITER] if INVITER not in back else [k for k in r.CHARS if k not in back] + [INVITER]
    for inv in inviters:
        toggle_players(inv)                     # player models were hidden (Ctrl+B at login)
        try:
            ok = all(invite(w, inv) for w in back if w != inv)
        finally:
            toggle_players(inv)
        if ok:
            break
    return back


if __name__ == "__main__":
    print(gone())
