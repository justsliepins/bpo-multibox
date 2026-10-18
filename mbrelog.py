#!/usr/bin/env python3
"""Bring a disconnected / crashed client back: restart it, log in, pick its character, rejoin the
party. Accounts and characters come from accounts.conf; the login screen keeps the password filled in.

usage: mbrelog.py WINDOW            (1..5)
       mbrelog.py check             relog every window that is gone (black / no game for 90 s)
"""
import os
import subprocess
import sys
import time

import numpy as np

import mbcoords
import mbtarget as t

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("MB_STATE", os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "multibox"))
DISPLAYS = [":61", ":62", ":63", ":64", ":65"]
# window -> (account, character)
def _load_chars():
    """window -> (account, character) from accounts.conf (not in git: see accounts.example.conf)."""
    chars = {}
    path = os.path.join(HERE, "accounts.conf")
    if os.path.exists(path):
        for line in open(path):
            f = line.split("#")[0].split()
            if len(f) >= 3 and f[0].isdigit():
                chars[int(f[0])] = (f[1], f[2])          # (a 4th column, the password, is for AUTOPILOT.md runs)
    return chars


CHARS = _load_chars()
LEADER = 4                                  # LuckyThird leads the party
PLAYER_CURSOR = 2463071752


def log(msg):
    with open(os.path.join(STATE, "kill.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + "relog: " + msg + "\n")


def click(d, x, y, double=False):
    O = t.ORIGIN
    t.click_at(d, O[0] + x, O[1] + y)
    if double:
        time.sleep(0.15)
        t.key(d, "mousedown", "1", "sleep", "0.05", "mouseup", "1")


def ocr_has(d, box, word):
    try:
        return word.lower() in t.ocr(t.screenshot(d).crop(box), 6, 2).lower()
    except Exception:
        return False


def in_game(d):
    return mbcoords.read(d) is not None


def login(w):
    d = DISPLAYS[w - 1]
    acct, char = CHARS[w]
    log(f"window {w}: logging in {acct} / {char}")
    for _ in range(40):                     # region list
        if ocr_has(d, (480, 240, 890, 310), "region"):
            break
        time.sleep(2)
    click(d, 568, 295, double=True)
    time.sleep(3)
    click(d, 580, 293, double=True)         # server: Worldwide
    time.sleep(3)
    click(d, 703, 478)                      # login field (the password stays filled in)
    time.sleep(0.3)
    t.key(d, "key", "End", *(["key", "BackSpace"] * 14), "type", "--delay", "80", acct)
    time.sleep(0.4)
    click(d, 695, 527)                      # Confirm
    for _ in range(30):                     # character select
        time.sleep(2)
        if ocr_has(d, (560, 715, 890, 745), "enter"):
            break
    im = t.screenshot(d)
    spot = None
    for x in (350, 730, 1040, 520, 900):    # the characters' name plates
        if char.lower()[:6] in t.ocr(im.crop((x - 120, 250, x + 120, 410)), 6, 2).lower():
            spot = x
            break
    if spot is None:
        # the name plate OCR misses often (slow first load, "Cx abKingKebab"); on every account
        # the played character is the right-hand slot (seen 2026-10-12 and -14)
        log(f"window {w}: {char} not read on {acct}'s character list: the right-hand slot")
        spot = 1040
    click(d, spot, 500)
    time.sleep(1)
    click(d, 707, 730)                      # Enter
    for _ in range(30):
        time.sleep(2)
        if in_game(d):
            break
    else:
        log(f"window {w}: did not get into the game")
        return False
    time.sleep(2)
    click(d, 681, 338)                      # the news dialog's Exit
    t.key(d, "keydown", "ctrl", "sleep", "0.05", "key", "b", "sleep", "0.05", "keyup", "ctrl")
    log(f"window {w}: {char} is in the game at {mbcoords.read(d)}")
    return True


def invite(w):
    """The leader invites window w's character (it must be close by), w accepts."""
    import mbhunt
    lead, d = DISPLAYS[LEADER - 1], DISPLAYS[w - 1]
    O = t.ORIGIN
    A = mbhunt.A0
    for attempt in range(3):
        a, b = mbcoords.read(lead), mbcoords.read(d)
        if not a or not b:
            return False
        dx, dy = b[0] - a[0], b[1] - a[1]
        bx = t.PLAYER_BAR_X + A[0][0] * dx + A[0][1] * dy
        by = t.PLAYER_BAR_Y + A[1][0] * dx + A[1][1] * dy
        hit = None
        for oy in (-22, -30, -14, -38, -6):
            for ox in (0, -8, 8):
                t.move(lead, O[0] + bx + ox, O[1] + by + oy)
                time.sleep(0.15)
                if t.cursor_id(lead) == PLAYER_CURSOR:
                    hit = (bx + ox, by + oy)
                    break
            if hit:
                break
        if not hit:
            time.sleep(2)
            continue
        t.key(lead, "mousedown", "3", "sleep", "0.08", "mouseup", "3")
        time.sleep(0.8)
        click(lead, hit[0] + 40, hit[1] + 55)  # "Party Invite" (third item: Trade, Add Friend, Party Invite)
        time.sleep(1.5)
        for y in (217, 197, 206):            # Accept (the prompt sits at one of these heights)
            click(d, 104, y)
            time.sleep(0.4)
        log(f"window {w}: invited into the party")
        return True
    log(f"window {w}: could not invite (not on the leader's screen)")
    return False


def gone(w):
    d = DISPLAYS[w - 1]
    try:
        im = t.screenshot(d)
    except Exception:
        return True
    return np.asarray(im.convert("L")).max() < 10 or not in_game(d)


def relog(w):
    subprocess.run([os.path.join(HERE, "multibox"), "restart", str(w)], timeout=200)
    time.sleep(10)
    return login(w)


def check():
    """Relog windows that have been out of the game for 90 s (a disconnect or a crash)."""
    out = [w for w in CHARS if gone(w)]
    if not out:
        return []
    time.sleep(90)
    out = [w for w in out if gone(w)]
    done = []
    for w in out:
        log(f"window {w}: out of the game for 90 s: restarting and logging in")
        if relog(w):
            done.append(w)
    return done


if __name__ == "__main__":
    if sys.argv[1] == "check":
        print(check())
    else:
        w = int(sys.argv[1])
        print(relog(w))
