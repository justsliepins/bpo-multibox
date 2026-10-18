#!/usr/bin/env python3
"""Activity panel: the latest lines of STATE/kill.log, small and translucent, beside the game.

    mblog.py STATE MONITOR

A GTK layer-shell surface (like the buttons) that never takes input: the input region is empty,
so clicks pass through to whatever is below. Repeated status lines that only differ in their
last field (e.g. "waiting:3/20:94/100 HP" every second) replace each other instead of scrolling.
Hidden while STATE/log-hidden exists. Polls the file's size twice a second: next to no CPU.
"""
import json
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

STATE, MONITOR = sys.argv[1], sys.argv[2]
LOG = os.path.join(STATE, "kill.log")
LINES = 14

CSS = b"""
window { background-color: rgba(10, 14, 20, 0.62); border-radius: 8px; }
label { color: #d8dee9; font-family: monospace; font-size: 10px; padding: 6px 8px; }
"""


def key(line):
    body = line[9:] if len(line) > 9 and line[2] == ":" and line[5] == ":" else line
    return body.rsplit(":", 1)[0] if body.count(":") >= 2 else None


class Panel(Gtk.Window):
    def __init__(self):
        super().__init__()
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(self, "multibox-log")
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 112)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 8)
        try:
            mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                                             text=True).stdout)
            want = next((m["x"], m["y"]) for m in mons if m["name"] == MONITOR)
            disp = Gdk.Display.get_default()
            for i in range(disp.get_n_monitors()):
                g = disp.get_monitor(i).get_geometry()
                if (g.x, g.y) == want:
                    GtkLayerShell.set_monitor(self, disp.get_monitor(i))
        except (StopIteration, ValueError, OSError):
            pass
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.label = Gtk.Label(xalign=0)
        self.label.set_size_request(250, -1)
        self.label.set_line_wrap(True)
        self.label.set_max_width_chars(36)
        self.add(self.label)
        self.connect("realize", lambda w: w.input_shape_combine_region(cairo.Region()))
        self.size = -1
        self.hidden = None
        self.tick()
        GLib.timeout_add(500, self.tick)

    def boxes_shown(self):
        """False while MONITOR shows another workspace than the game boxes': the user works there
        and doesn't want the panel (2026-10-14). Checked once a second."""
        import time
        now = time.monotonic()
        if now - getattr(self, "ws_at", 0) < 1:
            return self.ws_ok
        self.ws_at, self.ws_ok = now, True
        try:
            mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                                             text=True, timeout=2).stdout)
            shown = next(m["activeWorkspace"]["id"] for m in mons if m["name"] == MONITOR)
            boxes = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True,
                                              text=True, timeout=2).stdout)
            ws = {c["workspace"]["id"] for c in boxes if c["title"].startswith("Xwayland on :")}
            self.ws_ok = not ws or shown in ws
        except Exception:
            pass
        return self.ws_ok

    def tick(self):
        if not os.path.exists(os.path.join(STATE, "session")):
            Gtk.main_quit()
            return False
        hidden = os.path.exists(os.path.join(STATE, "log-hidden")) or not self.boxes_shown()
        if hidden != self.hidden:
            self.hidden = hidden
            self.set_visible(not hidden)
            if not hidden:
                self.input_shape_combine_region(cairo.Region())
        try:
            size = os.path.getsize(LOG)
        except OSError:
            size = 0
        if size == self.size or hidden:
            return True
        self.size = size
        try:
            with open(LOG, "rb") as f:
                f.seek(max(0, size - 8000))
                raw = f.read().decode("utf-8", "replace").splitlines()[-200:]
        except OSError:
            raw = []
        shown = []
        for line in raw:
            line = line.replace("multibox: ", "")
            k = key(line)
            if shown and k is not None and key(shown[-1]) == k:
                shown[-1] = line                 # same status, newer value: update in place
            else:
                shown.append(line)
        text = "\n".join(l[:120] for l in shown[-LINES:]) or "(no activity yet)"
        self.label.set_text(text)
        return True


provider = Gtk.CssProvider()
provider.load_from_data(CSS)
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                         Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
Panel().show_all()
Gtk.main()
