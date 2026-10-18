#!/usr/bin/env python3
"""Minimal window-manager duties for one multibox box (one per box, runs as long as the box).

    mbwm.py DISPLAY

The boxes have no real window manager. Wine (winex11, 11.x) maps a managed window, requests a
WM_STATE and then waits for the window manager to set that property. Until it sees WM_STATE, the
window's state stays "pending" and Wine ignores every FocusIn on it (event.c X11DRV_FocusIn). So
Wine never switches on XInput2 raw motion, and DirectInput never sees the mouse move. Clicks still
work, but dragging (skills to the hotbar, windows by their title) doesn't. This helper:

  * sets WM_STATE = NormalState on every mapped titled top-level window, and back to
    WithdrawnState when it is unmapped, as a window manager would;
  * keeps X focus on the game window when it falls back to PointerRoot or an untitled Wine
    helper window, and bounces focus once after acknowledging WM_STATE, so Wine processes a
    FocusIn and enables raw input.

It never moves windows: with no window manager Wine ignores outside moves (see README).
"""
import sys
import time

from Xlib import X, display, error

NORMAL, WITHDRAWN = 1, 0


def titled(win):
    try:
        name = win.get_wm_name()
    except error.XError:
        return False
    return bool(name and str(name).strip())


def main(disp, existing_done=False):
    d = display.Display(disp)
    root = d.screen().root
    WM_STATE = d.intern_atom("WM_STATE")
    game_name = "Black Pearl"   # largest titled window wins anyway; this just prefers the game
    acked = {}                  # window id -> state we last wrote
    # Wine discards a WM_STATE that arrives before its own request ("old ... expected"), and we
    # can't see when it asks. So after a window appears, acknowledge again a few times.
    REACK = (1.0, 4.0, 12.0)
    seen = {}                   # window id -> [time first seen mapped, number of re-acks done]
    if existing_done:
        # Restarted next to running clients (multibox fix): their windows were acknowledged long
        # ago. Re-acking would bounce focus, and a key pressed during a bounce gets lost.
        for w in root.query_tree().children:
            seen[w.id] = [0, len(REACK)]

    while True:
        bounce = False
        now = time.time()
        game, best = None, 0
        try:
            children = root.query_tree().children
        except error.ConnectionClosedError:
            return
        for w in children:
            try:
                a = w.get_attributes()
                if a.override_redirect or not titled(w):
                    continue
                viewable = a.map_state == X.IsViewable
                want = NORMAL if viewable else WITHDRAWN
                prop = w.get_full_property(WM_STATE, WM_STATE)
                have = prop.value[0] if prop and len(prop.value) else None
                if have != want and (viewable or have is not None):
                    w.change_property(WM_STATE, WM_STATE, 32, [want, 0])
                    acked[w.id] = want
                    if viewable:
                        bounce = True
                if viewable:
                    st = seen.setdefault(w.id, [now, 0])
                    if st[1] < len(REACK) and now - st[0] >= REACK[st[1]]:
                        w.change_property(WM_STATE, WM_STATE, 32, [NORMAL, 0])
                        st[1] += 1
                        bounce = True
                else:
                    seen.pop(w.id, None)
                if viewable:
                    g = w.get_geometry()
                    area = g.width * g.height + (10**7 if game_name in str(w.get_wm_name()) else 0)
                    if area > best:
                        game, best = w, area
            except error.XError:
                continue

        if game is not None:
            try:
                focus = d.get_input_focus().focus
                focus_ok = (not isinstance(focus, int)) and focus.id == game.id
                if not focus_ok and not isinstance(focus, int) and titled(focus) and focus.id != root.id:
                    focus_ok = True   # one of the game's own titled dialogs
                if bounce or not focus_ok:
                    if bounce:
                        root.set_input_focus(X.RevertToParent, X.CurrentTime)   # FocusOut ...
                        d.sync()
                        time.sleep(0.1)
                    game.set_input_focus(X.RevertToParent, X.CurrentTime)      # ... FocusIn
                    d.sync()
            except error.XError:
                pass
        d.flush()
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main(sys.argv[1], "--existing-done" in sys.argv[2:])
    except (error.ConnectionClosedError, BrokenPipeError):
        pass
