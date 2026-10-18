# Handoff: set up the multibox on a second Omarchy PC

For a new Claude Code session on another Omarchy (Arch + Hyprland) machine. Goal: install everything,
start 5 Black Pearl Online (BPO) clients, log in 5 **new** accounts (the user gives you the
credentials when setup is done), and run the same Great Polar Bear farm as the first PC.

Read `README.md` for how the multibox itself works. This file is the setup and the hard-won rules.

## Rules (from the user, non-negotiable)

- **Credentials never go into git, chat logs, or any file except `accounts.conf`** (git-ignored,
  `chmod 600`). Don't send them anywhere else.
- **Never press Escape** in a game window. Close dialogs with their own buttons.
- **Captcha → alert the user, never solve it.**
- The user plays too: if they say "stop using window N", stop all automation at once.
- Keep the user's own workspace: the boxes live on Hyprland workspace 2; never switch the user's
  view there (commands that focus a box can - switch back with `hyprctl dispatch workspace N`).
- Ask before anything outward-facing or hard to undo. Commit messages end with
  `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Never `pkill -f` / `pgrep -f` with a pattern that also matches your own command line (it killed
  the session's own shell twice). Find pids with `ps -eo pid,args | grep ... | grep -v grep`.

## 1. Packages

```bash
sudo pacman -S --needed wine-staging wine-mono wine-gecko winetricks xorg-xwayland libxtst libxi \
  xdotool tesseract tesseract-data-eng python-numpy python-pillow python-opencv python-xlib \
  python-gobject python-cairo gcc make imagemagick
```
(Wine 11.17 Staging was used on the first PC. The home filesystem was btrfs: `multibox start`
makes reflink copies of the Wine prefix per box - on ext4 they become full copies, which works but
uses disk space.)

## 2. Code

```bash
git clone https://github.com/justsliepins/bpo-multibox ~/multibox
cd ~/multibox && make                 # builds mbcast (input broadcaster) and mbprime
sed "s|@HERE@|$HOME/multibox|" black-pearl-multibox.desktop > ~/.local/share/applications/black-pearl-multibox.desktop
cp accounts.example.conf accounts.conf && chmod 600 accounts.conf   # filled in at step 6
```

## 3. The game

1. Download the BPO Windows installer (`BlackPearlOnline_Setup_1.0.0.exe`) from the official Black
   Pearl Online site (ask the user for the link if it isn't obvious).
2. Install it into the prefix the multibox expects:
   ```bash
   export WINEPREFIX=$HOME/Games/BlackPearlOnline/prefix
   wine BlackPearlOnline_Setup_1.0.0.exe       # install to C:\BlackPearlOnline
   mkdir -p ~/Games/BlackPearlOnline && cp ~/multibox/tools/bpo-play.sh ~/Games/BlackPearlOnline/play.sh
   ~/Games/BlackPearlOnline/play.sh            # the launcher patches the client (version 45 on 2026-10-18)
   ```
   The game must end up at `~/Games/BlackPearlOnline/prefix/drive_c/BlackPearlOnline/system64/Game.exe`.
3. **Patching later:** the boxes share the game folder. Stop the boxes (`./multibox stop`), run the
   launcher (`play.sh`), then start the boxes again.

## 4. multibox.conf (already in the repo, check it fits the machine)

`COUNT=5`, `FPS_LIMIT=10`, `RENDERER=wined3d`. Why: on the first PC (AMD Renoir iGPU, 512 MB
VRAM) 5 boxes at 30 fps with DXVK hung the GPU ("ring gfx timeout ... dxvk-submit") and crashed
Hyprland several times. With a stronger GPU, DXVK (`RENDERER=dxvk`, see README for `vendor/dxvk`)
and a higher FPS cap are fine - but at low FPS key presses get missed; `mbtarget.key()` stretches
holds to 1.3/FPS for that reason.

Also set in the game's `user/system.ini` (first PC): `framerate=0`, `vsync=1`, other players'
effects off.

## 5. Pink textures (how the bots see mobs)

The bots find mobs by a solid magenta texture. Pink the ones you farm (framework numbers: column 6
of the mob in the server files' `CharacterInfo.txt`), then restart the clients:

```bash
python3 mbpink.py 504 205        # Great Polar Bear (both kinds) - the bear farm needs these
python3 mbpink.py --restore 504   # back to normal
```
Others used: 286 Slowpoke Snail, 544 Horrific Snowman, 501 Steel-shell Snail, 103/517 Yeti.
Pink Yetis are a trap on the bear route: the walker must never attack them (it reads names and
skips Yetis).

## 6. Start, log in, party

```bash
cd ~/multibox && ./multibox start bpo 5      # boxes :61..:65 on workspace 2, broadcast ON
./multibox toggle                            # broadcast OFF before logging in
```
- Fill `accounts.conf` with the user's 5 new accounts (window, login, character name). Window 5 is
  the tank. **The password is not stored**: on each box's login screen type it once (or have the
  user do it); the client keeps it filled in afterwards. `mbrelog.login(w)` then logs a box in.
  On the first PC the password field was pre-filled - check on this PC where the password field is
  before automating anything around it.
- Window order can swap after a restart (window 3/4 were LuckyForth/LuckyThird): check each
  window's name plate (top left) before trusting the mapping.
- **Party: use the "+" button** under window 1's portrait (screen 13,72) → "Invite Party" → click
  the name field (694,393), clear it (End + Backspaces), type the character name, Confirm
  (684,421). The invitee's "invites you to party!" prompt appears top left; find its Accept button
  by OCR (its position varies) and click within 30 s. Verify: every window shows 4 party frames.
  **Never use `mbreconnect.invite` / `mbrelog.invite`**: when a name isn't on screen they walk the
  invitee to the inviter - that walked the tank off its spot and two Voyagers into the bears.
  Re-form the party after every relog.
- **Autograb**: Ctrl+Alt+A toggles it per client. The screen shows no indicator: ask the user to
  confirm it is on (drops then show as "Obtain [...]" in chat).

## 7. The bear farm (what runs on the first PC)

Party: 4 Voyagers (Lv40-41, F1 Lightning Bolt, F2 Conch Ray) + 1 Champion tank (F1 Taunt). Map
Icicle (darkblue), Great Polar Bears Lv49 (spawn 3057-3145 x 647-686, ~98 s respawn).

- **Tank** sits exactly at **(3132,669)** and never moves; it's excluded from the broadcast
  (`$XDG_RUNTIME_DIR/multibox/excluded` = `4`). Bears pile onto it.
- **Voyagers** sit on the safe spot **(3119,685)**. When pink bears are on the tank and all four
  are ≥95% HP: Alt+R to **(3128,674)**, one Conch Ray on the pile, Alt+R back, sit.
- `mbbearloop.py` does exactly that (the user's loop, recorded with `mbwatchplay.py`). A guard
  thread makes any Voyager that loses HP Conch its attacker at once from its own window.
- `mbbuffwatch.py` closes the "Daily Buff" popup (18:00 server time) that blocks clicks.

```bash
python3 mbtoicespire.py 1 2 3 4     # from Argent: teleporters to Icespire Haven (500 g per hop)
python3 mbwalkbears.py 1,2,3,4 --loop   # walk to (3119,685) fighting back, then start the loop
setsid nohup python3 mbbuffwatch.py >/dev/null 2>&1 &
```
The tank gets to (3132,669) by hand (the user put it there), then sits (Insert).
After a reboot: re-form the party, then `setsid nohup python3 mbbearloop.py 10 >/dev/null 2>&1 &`.
Deaths: the loop waits (logs "DEAD ... needs the user"); revive (they respawn at Icespire Haven if
the spawn is recorded there - record it with Artemis), rest, walk back with `mbwalkbears.py`.
Logs: `$XDG_RUNTIME_DIR/multibox/kill.log` (`bearloop:` lines), `bearloop.out`.

## 8. Alt+R / input gotchas (cost characters on the first PC)

- **Movement never on broadcast.** The broadcaster shifts mid-screen clicks for followers standing a
  tile off (so mob clicks hit the same mob) - the Alt+R dialog's clicks then land on the ground and
  walk them into mobs. Pause the broadcast (`$STATE/pause.<pid>` file) and Alt+R in each window.
  Sitting (Insert) and the Conch cast are fine on broadcast.
- The Alt+R dialog has an **"Auto trace" checkbox**: unticked, the trace is accepted but nobody
  walks.
- **Enter does not confirm the dialog - it opens the chat line**, and text typed there swallows the
  next Alt+R. Clear it with a click on the chat field, Backspaces, Enter.
- OCR of the dialog digits reads 8 as 3 and the text cursor as 1 (handled in `mbtarget._field_reads`).
- Never sit outside a safe zone with a mob near; rest early (75% HP), not at 50%.
- In-game ping spikes (300-1200 ms) make hits land late and mobs win: pause fighting then.
- Repair: Blacksmith Goldie in Argent (`mbrepair.py`); each death costs 5% durability and at 0 a
  weapon can't cast. Check the weapon tooltip says 200/200.

## 9. Research notes

Sea trade is disabled on BPO. The "Explorer's Life" quest pays only ~1.8k on BPO (the ToP server
files say 35.5k): always verify numbers from server files in the live game before building on them.
