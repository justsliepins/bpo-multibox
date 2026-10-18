#!/usr/bin/env python3
"""Watch the user play (read-only: no input is ever sent). Writes to STATE/play/:

  input.jsonl  - every key and mouse button press/release in window 1 (XRECORD), with the
                 pointer position, the cursor kind under it (attack / skill / ground / other)
  state.jsonl  - once a second: every window's map position, own HP/SP, party HP
  shots/       - a half-size screenshot of window 1 every 5 s and at each mouse click

usage: mbwatchplay.py [SECONDS]
"""
import json
import os
import sys
import threading
import time

from Xlib import X, XK, display
from Xlib.ext import record
from Xlib.protocol import rq

import mbcoords
import mbtarget as t

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "multibox")
OUT = os.path.join(STATE, "play")
SHOTS = os.path.join(OUT, "shots")
D = [":61", ":62", ":63", ":64", ":65"]
GROUND = 2089885924


def cursor_kind(d):
    c = t.cursor_id(d)
    if c in t.ATTACK_FRAMES:
        return "attack"
    if c in t.SKILL_FRAMES:
        return "skill"
    if c == GROUND:
        return "ground"
    return str(c)


def shot(name, win=0):
    try:
        im = t.screenshot(D[win])
        im.resize((im.width // 2, im.height // 2)).save(os.path.join(SHOTS, name + ".jpg"), quality=70)
    except Exception:
        pass


import queue
Q = queue.Queue()


def writer():
    """Adds the cursor kind and click screenshots outside the XRECORD callback (X requests made
    inside it froze the recorder after 3 minutes)."""
    out = open(os.path.join(OUT, "input.jsonl"), "a", buffering=1)
    while True:
        rec = Q.get()
        w = rec.get("win", 1) - 1
        if rec["ev"] in ("click", "key"):
            try:
                rec["cursor"] = cursor_kind(D[w])
            except Exception:
                pass
        if rec.get("shot"):
            shot(rec["shot"], w)
        out.write(json.dumps(rec) + "\n")


def record_input(stop, win=0):
    ctl = display.Display(D[win])
    data = display.Display(D[win])
    ctx = ctl.record_create_context(0, [record.AllClients], [{
        "core_requests": (0, 0), "core_replies": (0, 0), "ext_requests": (0, 0, 0, 0),
        "ext_replies": (0, 0, 0, 0), "delivered_events": (0, 0), "errors": (0, 0),
        "client_started": False, "client_died": False,
        "device_events": (X.KeyPress, X.ButtonRelease)}])

    def cb(reply):
        if reply.category != record.FromServer or reply.client_swapped or not reply.data:
            return
        buf = reply.data
        while len(buf):
            ev, buf = rq.EventField(None).parse_binary_value(buf, data.display, None, None)
            kind = {X.KeyPress: "key", X.KeyRelease: "keyup", X.ButtonPress: "click",
                    X.ButtonRelease: "clickup"}.get(ev.type)
            if not kind:
                continue
            rec = {"t": round(time.time(), 3), "win": win + 1, "ev": kind, "x": ev.root_x - t.ORIGIN[0],
                   "y": ev.root_y - t.ORIGIN[1]}
            if kind.startswith("key"):
                ks = data.keycode_to_keysym(ev.detail, 0)
                rec["key"] = XK.keysym_to_string(ks) or str(ks)
            else:
                rec["button"] = ev.detail
            if kind == "click":
                rec["shot"] = f"click_w{win + 1}_{rec['t']:.1f}"
            Q.put(rec)

    ctl.record_enable_context(ctx, cb)


def sample(stop, seconds):
    out = open(os.path.join(OUT, "state.jsonl"), "a", buffering=1)
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        t0 = time.time()
        try:
            res = {}

            def grab():
                im = t.screenshot(D[0])
                res["im"] = im
                res["rec"] = {"t": round(t0, 1),
                   "pos": [mbcoords.read(d) for d in D],
                   "own_hp": [round(t.own_hp(d) or 0, 2) for d in D],
                   "party_hp": [None if v is None else round(v, 2) for v in t.party_hp(im)],
                   "party_sp": [None if v is None else round(v, 2) for v in t.party_sp(im)]}
            th = threading.Thread(target=grab, daemon=True)
            th.start()
            th.join(3.0)
            if "rec" not in res:
                out.write(json.dumps({"t": round(t0, 1), "error": "sample timed out"}) + "\n")
            else:
                out.write(json.dumps(res["rec"]) + "\n")
                if n % 5 == 0:
                    im = res["im"]
                    im.resize((im.width // 2, im.height // 2)).save(
                        os.path.join(SHOTS, f"t_{t0:.0f}.jpg"), quality=70)
                    shot(f"t_w5_{t0:.0f}", 4)       # the tank's view too
        except Exception as exc:
            out.write(json.dumps({"t": round(t0, 1), "error": repr(exc)}) + "\n")
        n += 1
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))
    stop.set()


if __name__ == "__main__":
    os.makedirs(SHOTS, exist_ok=True)
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 3 * 3600
    stop = threading.Event()
    threading.Thread(target=writer, daemon=True).start()
    for w in [int(x) - 1 for x in os.environ.get("MB_WATCH_WINDOWS", "1").split(",")]:
        threading.Thread(target=record_input, args=(stop, w), daemon=True).start()
    sample(stop, secs)
