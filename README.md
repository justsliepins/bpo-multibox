# multibox

Run 4 Tales of Pirates / Black Pearl clients at once. Whatever you do in one box (mouse and keyboard) happens in all of them.

From the app menu: **Super+Space → "Black Pearl Multibox"** starts all four windows with broadcast OFF (spread out, so you can log each one in). If it's already running, it brings window 1 to the front. Install the menu entry with `cp black-pearl-multibox.desktop ~/.local/share/applications/`.

```
./multibox start bpo 4     # Black Pearl, 4 boxes   (or: start local 4  for your own server)
./multibox toggle          # broadcast on/off; same as Super+` or Pause
./multibox status
./multibox next            # bring the next box to the front; same as Super+Shift+`
./multibox sit             # sit everyone down / stand up (Insert, 1 s apart); same as the SIT button
./multibox camera          # reset the camera in every window (Home); automatic on broadcast ON and before ALIGN
./multibox align           # same as the ALIGN button
./multibox kill 3          # kill 3 green-bar mobs in sequence; wait for each to die
./multibox reload          # reload the broadcaster without closing game clients
./multibox restart 4       # restart the game in box 4 only (crashed or broken); the others keep running
./multibox layout          # put the windows back in place
./multibox stop
```

Buttons sit in the top-right corner of the screen, always on top:

**⏱ Respawn timer** (leftmost): left-click starts or restarts a 30-minute countdown, right-click stops it. At zero it blinks red and plays an alarm sound. It costs about 0.5% of one CPU core: it has no timer of its own, the overlay's existing refresh computes the time left from an end time stored in `$XDG_RUNTIME_DIR/multibox/timer`.

**⚔ KILL** has a count field. It selects the nearest green mob health bar and sends one click through the normal broadcaster so all four clients attack the same mob. If the leader is already in combat, it waits for that combat to end; it then waits for each selected mob to reach 0 HP before selecting the next one. It aligns only when bar matching fails or the tracker confirms the party is more than two tiles apart. If separation grows during a fight, it aligns once, reacquires the target, and resumes that count slot.

**↻ RELOAD** restarts the input broadcaster and overlay without closing the game clients. It also builds the updated broadcaster first when its C source has changed.

**⌖ ALIGN** walks all characters onto the same tile. Use it whenever they've spread out, for example after routing differently around trees, or when a click hit a monster in one window and the ground in another. It reads each character's coordinates under the minimap, picks a free spot 3 tiles from window 1's character (clicking a spot where a player stands only selects that player, it doesn't walk there), and clicks that spot in each window separately. Every round tries a new direction, so a tree or a monster on the spot in one window doesn't block it. It stops with a reason if a window doesn't walk (sitting? press STAND) or if everyone walks the wrong way (rotated camera). Usually 1–2 rounds, a few seconds. Broadcast is paused meanwhile and restored afterwards. Characters more than ~14 tiles apart have to be walked closer by hand first. It's deliberately not automatic: it would walk your characters away from a fight. While it runs the button turns into **■ STOP ALIGN**: click it to stop.

**Misaligned by a tile or two? Clicks still hit.** `mbtrack.py` measures, about twice a second, how far each follower's view is shifted against window 1's: a patch of scenery from window 1 (away from the characters) is looked up in each follower's screen. That is exact to a pixel or two (the minimap coordinates are whole tiles, up to ~20 px off) and works for any zoom or angle as long as all cameras match. mbcast shifts each follower's cursor by that much. If the match is poor (open game window, far apart), it falls back to the coordinate difference. A click on a monster in window 1 lands on the same monster in every window, and a ground click sends everyone to the same tile, so walking pulls them back together (to within a tile; ALIGN makes it exact). The interface (bottom bars, chat, minimap, party frames) is never shifted, but open game windows (inventory etc.) in the middle of the screen are, so align before using those. The ALIGN button shows **✓** when everyone stands on one tile, or **(n)** when they're up to n tiles apart. Like ALIGN, it assumes the default camera angle, so turning broadcast ON and every ALIGN first press **Home** (the game's camera reset) in each window. A camera you turned with broadcast OFF is straightened out that way; `multibox camera` does it by hand.

**Aimed clicks for ordinary input (off).** `mbaim.py` can look up the clicked monster in each follower's screen (template matching) and aim there (`AIM=1` in `multibox.conf`). It made ordinary clicks less accurate in some scenes, so it remains off for regular mouse input. KILL uses visual aiming for its one deliberate click only.

**Short clicks.** The game reads the mouse once per frame, so followers always hold a button at least 70 ms, even when your click is quicker.

**BROADCAST ON / OFF**: click it to switch:

- **ON** (green): all boxes are stacked on the same spot, so you see one window and the others are right behind it. Input in that window goes to all of them.
- **OFF** (red): the boxes spread out over the screen (overlapping where they don't fit), and each one gets only its own input. Click a box to bring it forward and set it up.

Switching back to ON stacks them again with **window 1 in front**. While broadcast is ON, only window 1 drives the others (`LEADER=1`; set `LEADER=any` in `multibox.conf` to let whichever box you use lead). Super+`, Pause and a left click on **MB ON/OFF** in the top bar do the same. Right-clicking it (or Super+Shift+`) brings the next box to the front. Layouts are `LAYOUT_ON` / `LAYOUT_OFF` in `multibox.conf` (`stack` or `grid`).

Broadcast starts **on**. To log each box into a different account, click the button (OFF), log in to each box, line the characters up, and click it again (ON).

## How it works

- Each box is a rootful Xwayland window (`:61` … `:64`) with one client inside. A client is always the focused window of its own X server, so its DirectInput keeps reading clicks and hotkeys. This doesn't work with several game windows in one X server, because there only the foreground window reads input.
- `mbcast` (C, XRECORD + XTEST) records input in the boxes. By default window 1 leads (`-1`). With `LEADER=any`, the box you're using leads: real input in a box makes it the leader (mbcast ignores the echoes of its own injected events). Its events are replayed in the other boxes at the same coordinates, and the game sees them as normal hardware input. So play in any box, even a partly hidden one.
- All boxes are the same size, so the game window lands at the same spot (8,37) in each of them. Mirrored clicks hit the same button or the same ground tile.
- Box 1 uses your real prefix. Boxes 2–4 use reflink clones in `prefixes/` (instant on btrfs, almost no disk space). The game folder is a symlink to the real one, so a patch applies to every box.

## Things to know

- **Patch with the normal launcher first** (`~/Games/BlackPearlOnline/play.sh`). multibox starts `Game.exe` directly.
- **Never move the game window inside a box.** These X servers have no window manager, and Wine ignores outside moves. It keeps the old position, so every click lands off target. multibox never moves it.
- Camera angle and zoom are mirrored too. Set them the same in every box before you turn broadcast on, or the same click will hit different ground spots.
- The boxes share `user/` (saved login, settings), because they share the game folder.
- Box size comes from the resolution in `user/system.ini`. Hidden boxes keep running. For side-by-side boxes instead, set `POSITIONS=(grid)` in `multibox.conf`.
- **Performance:** every box renders with DXVK (Vulkan) and is capped at `FPS_LIMIT=30` (set it in `multibox.conf`; 0 = no cap). All four share one iGPU, so **play plugged in**: on battery the GPU stays at its low clock (400 MHz vs 1600) and the battery drains fast. DXVK is in `vendor/dxvk` (not in git; to get it again: download `dxvk-3.1.1.tar.gz` from github.com/doitsujin/dxvk/releases and unpack it there with `--strip-components=1`). It only goes into multibox's own prefix copies (`prefixes/bpo-1..4`). Your normal install is untouched. `RENDERER=wined3d` switches back.
- The waybar module (`custom/multibox` in `~/.config/waybar/config.jsonc`) runs `multibox waybar`; it's hidden when multibox isn't running.
- Settings go in `multibox.conf` (sourced as bash): `PROFILE`, `COUNT`, `MONITOR`, `POSITIONS=("0,0" "960,0" ...)`.
- Debugging: `MB_WINEDEBUG=+dinput ./multibox start bpo 2`; logs are in `$XDG_RUNTIME_DIR/multibox/`.

## ALIGN details

- `mbcoords.py` reads the coordinate label at a fixed spot of the 1366x768 BPO window by matching each character pixel-exact against learned glyphs (`mbglyphs.json`, one shape per character, each trimmed to its outline). Only pure white pixels count (the digits); snow and the minimap's edge are bluish. In Icicle and Atlantis Snow Field bright snow next to the label used to merge into the digits, the reader fell back to tesseract, read wrong numbers ("242" for "1242") and kept three tesseract processes busy (lag), and the wrong offsets made the characters drift apart. tesseract is now limited to once per 5 s. For a glyph it hasn't seen yet (at first: 4, 5, 8, 9) it asks tesseract 12 times and learns from a clear majority. tesseract alone misread "2006" as "86". Another resolution needs a new `LABEL` rectangle.
- `mbalign.py` and the click shift assume the game's default camera (Home), which multibox restores before ALIGN and on broadcast ON. That view is turned about 33°: one map tile in x is 18 px right and 12 px up on screen, one tile in y is 13 px right and 17 px down (`TILE_X`/`TILE_Y`, least-squares fit of eight test walks on BPO). A different zoom or angle makes both miss.

## When one window stops following

Broadcast sends the same keys and clicks everywhere, so the four game UIs have to be in the same state. If one window has its inventory open, or the chat line active, the same click or key does something else there: the click lands on the inventory, the hotkey types into chat. Close it in that window with broadcast OFF, then turn broadcast ON again.

The focus watcher (`pin_box`) keeps the game window focused in every box. The prefix copies use Wine's `UseTakeFocus=N`, so a client that lost focus becomes active again as soon as it gets focus back. This takes effect for clients started after 2026-10-06 12:30; `multibox restart N` applies it to a running box.

## Why dragging didn't work (fixed 2026-10-06)

Wine 11 (winex11) requests a `WM_STATE` for each game window and waits for the window manager to set it. Until then it ignores every FocusIn, so it never turns on XInput2 raw motion, and DirectInput (which the game reads for "mouse moved while button held") never sees the mouse move. Clicks still worked; drags (skills to the hotbar, windows by their title) and right-drag camera turns didn't. The boxes have no window manager, so:

- `mbwm.py` (one per box) does the window-manager part: it sets `WM_STATE`, re-acknowledges it a few times after a window appears (Wine discards an answer that arrives before its request), and keeps the game focused.
- `mbprime` nudges the XTEST pointer right before a client starts, so Wine finds relative X/Y axes at startup ("X/Y axis valuators not found, ignoring RawMotion events" otherwise).
- `mbcast` copies window 1's raw mouse deltas to the other boxes as relative XTEST motion, so their DirectInput sees the same movement (camera turns stay in sync), and puts their cursor at window 1's exact position with a pointer warp, which produces no raw motion.

Clients started before this fix need a restart (`multibox restart N`, or stop + start) to pick it up.

## Sitting

**🪑 SIT / 🧍 STAND** presses Insert (the game's sit/stand key) in each window, **1 second apart**. The game server accepts only about one sit per half second across your characters: with a broadcast click on the hotbar's sit button (all at once), or presses 0.3 s apart, every second character stayed standing while its own client showed it seated. Such a client can't walk (clicks only turn it); press STAND, or ALIGN tells you which window it is. One second apart, all four sit, also on the same tile.

## Green-bar targeting

Each **KILL** action finds the nearest horizontal green health bar on the leader's screen, checks the same bar in each follower view, and sends one left click through the leader mouse. The broadcaster aims that click at the corresponding mob in each follower. Bar detection does not depend on the mob's facing direction. The central bars under the stacked player characters are ignored. For a count greater than one, it waits until the target reaches 0 HP before proceeding. If a follower cannot locate the bar, or the tracker reports more than two tiles of party separation, it runs ALIGN and retries the current count slot. If the bar remains uncertain, broadcast is off, or combat does not end within the timeout, it stops. Zero NPC HP counts as out of combat.

## Pausing broadcast (internal)

ALIGN, FIX and SIT pause broadcasting with SIGRTMIN / SIGRTMIN+1 (nested; mbcast counts them) instead of toggling it, so a BROADCAST click meanwhile is kept. Every pause is released when the command exits, and mbcast ends a pause by itself after 90 s, so broadcast can't stay dead.
