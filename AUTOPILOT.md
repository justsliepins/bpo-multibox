# AUTOPILOT: from a bare Omarchy install to 4 Voyagers + 1 Champion, with nobody watching

You are a Claude Code session on a fresh Omarchy (Arch + Hyprland) PC. The user is asleep and will
**not answer anything**. Everything you need is in the user's first message (sudo password,
account logins and passwords, the security code, maybe the game download link) and in this repo. Read **HANDOFF.md**
(setup + rules) and **LEVELING.md** (what worked when leveling) completely first; this file only
adds how to run it all unattended.

## How to run unattended

- **Never stop to ask.** When a decision would normally go to the user, choose the safer option,
  write it in `PROGRESS.md` under "Decisions", and continue. The user reads PROGRESS.md in the
  morning.
- **Keep `~/multibox/PROGRESS.md` current** (not committed): phase, each character's level and
  class, where they are, what is running, problems, decisions. Update it after every phase step
  and at least every 30 min. After a context compaction, read it first and continue from there.
- Long jobs run detached (`setsid nohup ... &`). Watch them with background monitors (log lines
  for deaths, errors, level-ups) and check the screens every ~10 minutes. Re-arm monitors when
  they expire. Never just wait idle: something is always either running or being fixed.
- **Credentials:** only in `~/multibox/accounts.conf` (`chmod 600`, git-ignored): `window login
  character password` (the security code in a comment line `# code: ...` there too). Never echo them in logs, commits, PROGRESS.md or output.
- **Never commit or push** anything during the run (the repo is public). Local notes only.
- Never press Escape in a game window. Never solve a captcha (see "Whatever happens").
- Work only on Hyprland workspace 2; after any command that switches workspaces, switch back to
  the one that was active before.

## Phase 0 - Setup (HANDOFF.md sections 1-4)

1. `sudo` with the password from the user's message (`echo "$PW" | sudo -S ...`; keep it in a
   shell variable, never in a file). Enable `[multilib]` in /etc/pacman.conf if needed, then
   install the packages.
2. Clone (already done if you are reading this from ~/multibox), `make`.
3. The game: if no link was given, search the web for the official Black Pearl Online site and
   download its Windows installer from there only (no mirrors). Install into
   `~/Games/BlackPearlOnline/prefix`, run the launcher to patch. Installer/launcher windows: drive
   them with screenshots + xdotool on their display; if a step needs a human (EULA tick boxes etc.)
   do it yourself by clicking.
4. `./multibox start bpo 5`, broadcast OFF, check 5 login screens.

## Phase 1 - Accounts, characters, party

1. Write accounts.conf (window, login, character, password).
2. Log each window in: login field, **password field** (find it on the login screen by
   screenshot - it is below the login field; the client may keep it filled in after the first
   time), Confirm. Use `mbrelog.login(w)` as the model; extend it locally to type the password.
   **First login of each account asks for a security code twice** (set it, then confirm it): use
   the code from the user's message, the same for all five. Later logins may ask for it again
   before entering the game: type it whenever the client asks.
3. Create the characters (LEVELING.md section 1): windows 1-4 **Ami**, named after **gods**
   (Zeus, Odin, Anubis, Thor, ...; if a name is taken add letters/digits, e.g. ZeusX, Odin45);
   window 5 a **Carsise** (it becomes Swordsman, then Champion), any free name. Write the names into
   accounts.conf. Enter the game with each.
4. Party with the **"+" button** (HANDOFF.md section 6). Verify 4 party frames in every window.
5. Autograb: press **Ctrl+Alt+A** once in each window and check the chat for a confirmation;
   if there is none, pick up drops with plain A after kills as a backup.
6. Turn off what slows bots down: in each client's options, other players' effects off.

## Phase 2 - Lv1 → 10, first class

- Find Lv1-10 **passive** mobs next to the starting town (screenshots: mob name labels "LvN").
  Fight on broadcast from window 1 (`./multibox kill N` and `multibox kill --f1` are the built-in
  helpers), tank included at this stage.
- At Lv10: Explorer at Little Daniel (2193,2730), Swordsman at Castle Guard Peter (2192,2767),
  Argent. The class quests at Lv10 may have small tasks: read the NPC dialog and do them.
- Spend stat points as LEVELING.md says (Voyagers SPR, tank CON) during rests.

## Phase 3 - Lv10 → 40

Follow LEVELING.md sections 3-6 and its band table. Use `mbsuper.py` + `mbleader.py` (the first
PC's leveling pair) where they fit; otherwise drive fights with broadcast from window 1 and the
tank's taunt loop (`mbtank.py`/`mbhunt.py`). Gear chests at 15/25/30/35/40, sell junk when bags
fill, repair after deaths.

**Skills without the user:** Lightning Bolt for every Explorer point; for the tank Taunt first.
Learning other skills: only what is free or cheap (< 5,000 g each), and at Lv40 make sure every
Voyager has Conch Ray if its book/trainer allows. Note every purchase in PROGRESS.md.

## Phase 4 - Second class (LEVELING.md section 7)

- Tank: Champion's Valor (Peter) - Criss Sword (Goldie ~83.6k g: pool gold to the tank by trade if
  needed), Ninja Mask, 5 Great Bear Tooth, 5 Solid Rock.
- Voyagers: Journey of the Voyager (Daniel, scroll his dialog) - Burgess, Sacenis, Dilady, then
  Whitney **from a ship** (Guppy ~10k g each at Sinbad). Mind the sharks.
- Verify the new class name on each character's status window.

## Phase 5 - Farm

When all five have their second class (or if Phase 4 is blocked for hours), go to the Great Polar
Bear farm (HANDOFF.md section 7) and leave `mbbearloop.py` + `mbbuffwatch.py` running.

## Phase 6 - End of run

Write the final state into PROGRESS.md and leave the farm running. Keep accounts.conf as it is
(the user wants the logins kept on this PC); it stays chmod 600 and out of git.

## Whatever happens

| Situation | Do this |
|---|---|
| Character died | Revive (town/haven), rest to full HP+SP, repair if durability < 30%, walk back as a group (never alone through aggressive mobs; `mbwalkbears.py` shows the fight-back walk). 3+ deaths in an hour at one spot: move one band lower and note it. |
| Captcha (beige dialog on the right of a client) | **Don't solve it.** Stop automation in that window only, keep the others safe (sit in a safe zone), note the time in PROGRESS.md, check that window every 10 min; continue once it's gone. If it blocks the party for > 1 h, park everyone in a town. |
| "Disconnected with server" / login screen | Wait 60 s, log that window in again (`mbrelog`), re-form the party with "+", continue. Server restarts happen. |
| Client crashed / window black | `./multibox restart N`, log in, party. |
| Game patch (launcher says update) | Stop the boxes, run the launcher (HANDOFF.md section 3), start the boxes, log in all, party. |
| Hyprland/GPU hang ("ring gfx timeout" in `journalctl -k`) | Lower `FPS_LIMIT`, keep `RENDERER=wined3d`, restart the boxes. |
| A bot script crashes | Read the traceback, fix it locally (small, careful change), restart it. Note the fix. |
| Stuck (nobody moves for 10 min) | Screenshot every window, close stray dialogs (their own buttons, never Escape), clear a chat line that has text in it, re-run. |
| Out of gold for a must-buy | Sell junk and spare gear; pool gold by trade to the buyer. Never buy from players. |
| Unsure | Choose the option that can't kill characters or lose items, write it down, continue. |
