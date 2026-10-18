#!/usr/bin/env python3
"""Multibox broadcast button: a small always-on-top overlay. Click it to toggle broadcast.

It is a layer-shell surface (like waybar), so the game windows can never cover it and
clicking it never takes keyboard focus away from a game box.

    mbbutton.py MULTIBOX_SCRIPT STATE_DIR MONITOR
"""
import os
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

MULTIBOX, STATE, MONITOR = sys.argv[1], sys.argv[2], sys.argv[3]
TIMER_SECONDS = 30 * 60

CSS = b"""
button { border-radius: 8px; padding: 6px 14px; font-weight: bold; font-size: 13px;
         border: 2px solid rgba(0,0,0,0.5); box-shadow: none; background-image: none; }
button.on  { background-color: #2e7d32; color: white; }
button.off { background-color: #c62828; color: white; }
button.align { background-color: #1565c0; color: white; }
button.fix   { background-color: #5d4037; color: white; }
button.sit   { background-color: #6a1b9a; color: white; }
button.kill { background-color: #b71c1c; color: white; }
button.reload { background-color: #455a64; color: white; }
button.fixing { background-color: #6d6d6d; color: white; }
button.busy  { background-color: #ef6c00; color: white; }
button.timer { background-color: #37474f; color: white; }
button.timer-run  { background-color: #00838f; color: white; }
button.timer-done { background-color: #d50000; color: white; }
button:hover { opacity: 0.85; }
"""


def read(name, default=""):
    try:
        with open(os.path.join(STATE, name)) as f:
            return f.read().strip()
    except OSError:
        return default


def running():
    try:
        os.kill(int(read("mbcast.pid", "0")), 0)
        return True
    except (ValueError, OSError):
        return False


class Button(Gtk.Window):
    def __init__(self):
        super().__init__()
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(self, "multibox-button")
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 34)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 12)
        # GTK3 can't look monitors up by connector name, so match Hyprland's position for MONITOR.
        try:
            import json
            mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                                             text=True).stdout)
            want = next((m["x"], m["y"]) for m in mons if m["name"] == MONITOR)
            display = Gdk.Display.get_default()
            for i in range(display.get_n_monitors()):
                mon = display.get_monitor(i)
                g = mon.get_geometry()
                if (g.x, g.y) == want:
                    GtkLayerShell.set_monitor(self, mon)
        except (StopIteration, ValueError, OSError):
            pass   # fall back to the compositor's choice

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.align = Gtk.Button(label="⌖ ALIGN")
        self.align.get_style_context().add_class("align")
        self.align.set_tooltip_text("Walk all characters onto the same spot")
        self.align.connect("clicked", self.do_align)
        self.button = Gtk.Button()
        self.button.connect("clicked", self.toggle)
        self.fix = Gtk.Button(label="🔧 FIX")
        self.fix.get_style_context().add_class("fix")
        self.fix.set_tooltip_text("Broadcast misbehaving? Click to reset all windows to the same state")
        self.fix.connect("clicked", self.do_fix)
        self.sit = Gtk.Button(label="🪑 SIT")
        self.sit.get_style_context().add_class("sit")
        self.sit.set_tooltip_text("Sit down / stand up in every window (presses Insert in each, one after another)")
        self.sit.connect("clicked", self.do_sit)
        self.kill = Gtk.Button(label="⚔ KILL")
        self.kill.get_style_context().add_class("kill")
        self.kill.set_tooltip_text("Kill this many green-bar mobs in sequence; waits for each one to die")
        self.kill.connect("clicked", self.do_kill)
        self.reload = Gtk.Button(label="↻ RELOAD")
        self.reload.get_style_context().add_class("reload")
        self.reload.set_tooltip_text("Restart the multibox input broadcaster and reload its buttons")
        self.reload.connect("clicked", self.do_reload)
        self.kill_count = Gtk.SpinButton.new_with_range(0, 99, 1)
        self.kill_count.set_value(1)
        self.kill_count.set_width_chars(2)
        self.kill_count.set_tooltip_text("Number of mobs to kill in sequence; 0 = ∞ (until STOP)")
        self.kill_count.connect("output", self.count_output)
        self.kill_count_label = Gtk.Label(label="COUNT")
        # Respawn timer: left click starts/restarts 30:00, right click stops it. No timer of its own:
        # refresh() (already 4x a second) works out the time left from an end time kept in
        # STATE/timer, and only touches the label when the shown second changes.
        self.timer = Gtk.Button(label="⏱ 30:00")
        self.timer.get_style_context().add_class("timer")
        self.timer.set_tooltip_text("Respawn timer\nLeft click: start / restart 30 min\nRight click: stop")
        self.timer.connect("button-press-event", self.timer_click)
        self.timer_shown = None
        self.timer_alerted = False
        self.logbtn = Gtk.Button(label="📜")
        self.logbtn.get_style_context().add_class("reload")
        self.logbtn.set_tooltip_text("Show / hide the activity log beside the game")
        self.logbtn.connect("clicked", self.toggle_log)
        box.pack_start(self.logbtn, False, False, 0)
        box.pack_start(self.timer, False, False, 0)
        self.fixate = Gtk.Button(label="📍 FIXATE")
        self.fixate.get_style_context().add_class("reload")
        self.fixate.connect("clicked", self.do_fixate)
        self.fix_radius = Gtk.SpinButton.new_with_range(5, 500, 10)
        self.fix_radius.set_value(100)
        self.fix_radius.set_width_chars(3)
        self.fix_radius.set_tooltip_text("FIXATE radius in map tiles")
        self.fixate_state = None
        self.safe = Gtk.Button(label="🛡 SAFE")
        self.safe.get_style_context().add_class("reload")
        self.safe.connect("clicked", lambda _: subprocess.Popen([MULTIBOX, "safespot"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.safe_state = None
        self.go_safe = Gtk.Button(label="➜🛡")
        self.go_target = Gtk.Button(label="➜📍")
        for btn, where in ((self.go_safe, "safe"), (self.go_target, "target")):
            btn.get_style_context().add_class("reload")
            btn.connect("clicked", lambda _, w=where: subprocess.Popen([MULTIBOX, "goto", w],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.go_state = None
        box.pack_start(self.safe, False, False, 0)
        box.pack_start(self.go_safe, False, False, 0)
        box.pack_start(self.fixate, False, False, 0)
        box.pack_start(self.go_target, False, False, 0)
        box.pack_start(self.fix_radius, False, False, 0)
        self.f1 = Gtk.CheckButton(label="F1")
        self.f1.set_tooltip_text("KILL presses F1 (skill) in every window before each target click,\n"
                                 "waits for its cooldown, and rests when HP or SP is low")
        box.pack_start(self.f1, False, False, 0)
        box.pack_start(self.kill_count_label, False, False, 0)
        box.pack_start(self.kill_count, False, False, 0)
        box.pack_start(self.kill, False, False, 0)
        box.pack_start(self.reload, False, False, 0)
        box.pack_start(self.sit, False, False, 0)
        box.pack_start(self.fix, False, False, 0)
        box.pack_start(self.align, False, False, 0)
        box.pack_start(self.button, False, False, 0)
        self.add(box)
        self.state = None
        self.align_state = None
        self.fix_state = None
        self.sit_state = None
        self.kill_busy = False
        self.kill_started = None
        self.refresh()
        GLib.timeout_add(250, self.refresh)
        GLib.timeout_add(1000, self.follow_workspace)

    def follow_workspace(self):
        """Shown only while the monitor shows the game boxes' workspace: the user works on
        another workspace while the bot plays and doesn't want the buttons there (2026-10-14)."""
        try:
            import json
            mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                                             text=True, timeout=2).stdout)
            shown = next(m["activeWorkspace"]["id"] for m in mons if m["name"] == MONITOR)
            boxes = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True,
                                              text=True, timeout=2).stdout)
            ws = {c["workspace"]["id"] for c in boxes if c["title"].startswith("Xwayland on :")}
            visible = not ws or shown in ws
            if visible and not self.get_visible():
                self.show()
            elif not visible and self.get_visible():
                self.hide()
        except Exception:
            pass
        return True

    def toggle(self, _):
        subprocess.Popen([MULTIBOX, "toggle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def toggle_log(self, _):
        flag = os.path.join(STATE, "log-hidden")
        if os.path.exists(flag):
            os.remove(flag)
        else:
            open(flag, "w").close()

    def timer_click(self, _widget, event):
        path = os.path.join(STATE, "timer")
        if event.button == 3:
            try:
                os.remove(path)
            except OSError:
                pass
        else:
            with open(path, "w") as f:
                f.write(f"{time.time() + TIMER_SECONDS:.0f}\n")
        self.timer_alerted = False
        self.timer_shown = None
        return True

    def refresh_timer(self):
        try:
            end = float(read("timer"))
        except ValueError:
            end = None
        if end is None:
            shown, cls = "⏱ 30:00", "timer"
        else:
            left = end - time.time()
            if left > 0:
                m, sec = divmod(int(left + 0.999), 60)
                shown, cls = f"⏱ {m:02d}:{sec:02d}", "timer-run"
            else:
                # blink once a second until reset
                shown = "⏱ RESPAWN" if int(time.time()) % 2 else "⏱ 00:00"
                cls = "timer-done"
                if not self.timer_alerted:
                    self.timer_alerted = True
                    # no notification: it would pop up over these buttons; the button blinks red
                    subprocess.Popen(["pw-play", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if (shown, cls) != self.timer_shown:
            self.timer_shown = (shown, cls)
            ctx = self.timer.get_style_context()
            for c in ("timer", "timer-run", "timer-done"):
                ctx.remove_class(c)
            ctx.add_class(cls)
            self.timer.set_label(shown)

    def do_sit(self, _):
        subprocess.Popen([MULTIBOX, "sit"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def do_reload(self, _):
        self.reload.set_sensitive(False)
        subprocess.Popen([MULTIBOX, "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        GLib.timeout_add(1500, self.enable_reload)

    def enable_reload(self):
        self.reload.set_sensitive(True)
        return False

    def do_fixate(self, _):
        self.fix_radius.update()
        args = [MULTIBOX, "fixate"]
        if not read("anchor"):
            args.append(str(self.fix_radius.get_value_as_int()))
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def refresh_fixate(self):
        anchor = read("anchor")
        if anchor == self.fixate_state:
            return
        self.fixate_state = anchor
        if anchor:
            x, y, r = anchor.split()
            self.fixate.set_label(f"📍 {x},{y}")
            self.fixate.set_tooltip_text(f"Fixated: KILL stays within {r} tiles of {x},{y}.\nClick to release.")
        else:
            self.fixate.set_label("📍 FIXATE")
            self.fixate.set_tooltip_text("Remember window 1's spot: KILL walks back when the group\n"
                                         "gets farther than the radius (tiles) next to this button")

    def count_output(self, spin):
        n = spin.get_value_as_int()
        spin.set_text("∞" if n == 0 else str(n))
        return True

    def kill_running(self):
        try:
            pid = int(read("kill.pid") or 0)
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return b"multibox\0kill\0" in f.read()
        except (ValueError, OSError):
            return False

    def do_kill(self, _):
        if self.kill_busy or self.kill_running():   # the button is STOP while a run is going
            subprocess.Popen([MULTIBOX, "kill-stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.kill_busy = False
            return
        # Commit a typed spinner value before reading it (e.g. entering 3 then clicking KILL).
        self.kill_count.update()
        self.kill_busy = True
        self.kill_started = time.monotonic()
        self.kill.set_label("… KILL")
        with open(os.path.join(STATE, "target-status"), "w") as f:
            f.write("killing\n")
        count = str(self.kill_count.get_value_as_int())
        args = [MULTIBOX, "kill"] + (["--f1"] if self.f1.get_active() else []) + [count]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def refresh_kill(self):
        status = read("target-status")
        running = self.kill_running()
        if running:
            self.kill_busy = True
        elif self.kill_busy and self.kill_started and time.monotonic() - self.kill_started > 5:
            self.kill_busy = False                    # the run ended (finished, stopped, or failed)
        if self.kill_busy:
            self.kill.set_label("■ STOP " + (status.split(":")[1] if status.startswith("waiting:") else ""))
            self.kill.set_tooltip_text("KILL is running: " + status + "\nClick to stop it")
            return
        self.kill.set_sensitive(True)
        if status.startswith("error:"):
            self.kill_busy = False
            self.kill.set_label("⚔ KILL")
            self.kill.set_tooltip_text(status[6:])
        elif status.startswith("done:"):
            self.kill_busy = False
            self.kill.set_label("⚔ KILL")
            self.kill.set_tooltip_text(f"Finished {status[5:]} mob(s)")
        elif status.startswith("waiting:"):
            parts = status.split(":", 2)
            self.kill.set_label(f"WAIT {parts[1]}" if len(parts) > 1 else "WAIT…")
            self.kill.set_tooltip_text("Waiting for the current target to reach zero HP")
        elif status.startswith("homing:"):
            self.kill.set_label("HOME…")
            self.kill.set_tooltip_text("Walking back to the FIXATE spot: " + status[7:])
        elif status.startswith("resting:"):
            self.kill.set_label("REST…")
            self.kill.set_tooltip_text("Resting until everyone's HP is full: " + status[8:])
        elif status.startswith("aligning:"):
            self.kill.set_label("ALIGN…")
            self.kill.set_tooltip_text("A follower missed the target bar; aligning before retry")
        elif not self.kill_busy:
            self.kill.set_label("⚔ KILL")

    def do_fix(self, _):
        if read("fix") != "fixing":
            subprocess.Popen([MULTIBOX, "fix"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def do_align(self, _):
        # while aligning, the same button stops it (multibox align cancels a running align)
        subprocess.Popen([MULTIBOX, "align"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def refresh(self):
        if not running():
            Gtk.main_quit()
            return False
        self.refresh_timer()
        self.refresh_fixate()
        safe = read("safespot")
        if safe != self.safe_state:
            self.safe_state = safe
            self.safe.set_label(f"🛡 {safe.replace(' ', ',')}" if safe else "🛡 SAFE")
            self.safe.set_tooltip_text(
                f"Safespot {safe}: if anyone dies during KILL, everyone retreats here, rests,\n"
                "and returns to the FIXATE spot. Click to clear." if safe else
                "Set window 1's spot as the safespot: on a death during KILL, everyone retreats here")
        go = (read("goto"), bool(read("safespot")), bool(read("anchor")))
        if go != self.go_state:
            self.go_state = go
            going, has_safe, has_target = go
            for btn, where, has, name in ((self.go_safe, "safe", has_safe, "safespot"),
                                          (self.go_target, "target", has_target, "FIXATE spot")):
                btn.set_label(("… STOP" if going == where else {"safe": "➜🛡", "target": "➜📍"}[where]))
                btn.set_sensitive(has or going == where)
                btn.set_tooltip_text(f"Walking to the {name}; click to stop" if going == where else
                                     f"Everyone walks to the {name} (Alt+R), then aligns" if has else
                                     f"No {name} set")
        self.refresh_kill()
        st = read("sit")
        if st != self.sit_state:
            self.sit_state = st
            self.sit.set_label({"sitting": "🧍 STAND", "busy": "… WAIT"}.get(st, "🪑 SIT"))
            tip = ("Stand everyone up" if st == "sitting" else
                   "Sit everyone down (Insert in each window, 1 s apart: the server rejects faster sits)")
            self.sit.set_tooltip_text(tip)
        fix = read("fix")
        if fix != self.fix_state:
            self.fix_state = fix
            busy = fix == "fixing"
            ctx = self.fix.get_style_context()
            (ctx.add_class if busy else ctx.remove_class)("fixing")
            self.fix.set_label("… FIXING" if busy else "🔧 FIX")
            self.fix.set_tooltip_text("Fixing… closes open game windows, re-syncs, aligns" if busy else
                                      "Broadcast misbehaving? Click to reset all windows to the same state"
                                      + (f"\nLast time: {fix}" if fix else ""))
        align = (read("align"), read("aligned"))
        if align != self.align_state:
            self.align_state = align
            align, aligned = align
            ctx = self.align.get_style_context()
            busy = align == "aligning"
            (ctx.add_class if busy else ctx.remove_class)("busy")
            mark = {"yes": " ✓", "": "", "?": " ?"}.get(aligned, f" ({aligned})")
            self.align.set_label("■ STOP ALIGN" if busy else "⌖ ALIGN" + mark)
            self.align.set_tooltip_text("Aligning… click to stop" if busy else
                                        "Walk all characters onto the same spot\n"
                                        "✓ = all on one tile, (n) = up to n tiles apart: clicks on the "
                                        "game world are shifted to match, the interface isn't"
                                        + (f"\nLast time: {align}" if align else ""))
        state = read("broadcast", "on")
        if state != self.state:
            self.state = state
            ctx = self.button.get_style_context()
            ctx.remove_class("on")
            ctx.remove_class("off")
            ctx.add_class(state)
            self.button.set_label("● BROADCAST ON" if state == "on" else "○ BROADCAST OFF")
            self.button.set_tooltip_text(
                "Click to stop: each box gets only its own input, boxes spread out"
                if state == "on" else
                "Click to start: input in one box goes to all, boxes stack up")
        return True


provider = Gtk.CssProvider()
provider.load_from_data(CSS)
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                         Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
# the activity panel lives as long as the buttons do
_log_panel = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mblog.py"),
                               STATE, MONITOR], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
win = Button()
win.show_all()
Gtk.main()
_log_panel.terminate()
