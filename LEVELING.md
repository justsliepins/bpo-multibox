# Leveling a fresh set: 4 Voyagers + 1 Champion, Lv1 → Lv40 → 2nd class

What worked on the first PC (2026-10-09 → 10-18). Read HANDOFF.md first (setup and the hard
rules). Coordinates are map tiles; Ascaron = map "garner", Icicle = "darkblue". When something
here disagrees with the live game, the live game wins - and write down what you found.

The server files the first PC used (a ToP source close to BPO) are NOT in this repo. Without them,
check spawns, drops and quest steps in-game or on a ToP database site; BPO also changed some
payouts and NPC spots (see the "live BPO differs" notes below).

## 1. Characters

- Windows 1-4: **Explorer → Voyager**. Explorer is allowed for Lance, Phyllis and Ami; the first
  PC used **Ami** for all four.
- Window 5: **Swordsman → Champion** (the tank).
- Names don't matter. Put them in `accounts.conf`.

## 2. First class at Lv10

- Explorer: **Little Daniel**, Argent (2193,2730).
- Swordsman: **Castle Guard Peter**, Argent (2192,2767).
- Lv1-10: the newbie mobs around Argent with broadcast ON (everyone attacks the same mob). This
  part was done before the bots existed: find Lv1-10 passive mobs near Argent in-game.

## 3. Points

- **Stats, every level:** Voyagers all **SPR**; tank all **CON** (`mbgrind.allocate_stats`).
  Never open the stat window mid-fight: three characters died within a minute of a level-up that
  way. Spend points only while the party sits resting.
- **Skills:** Explorers put every point into **Lightning Bolt** (F1; at LV10 the cooldown is
  4.4 s) - `mbgrind.allocate_skills`. Later **Conch Ray** (F2, line area attack) was learned and
  is what kills piles. The tank: **Taunt** on F1, and later Tiger Roar on F2 and a howl on F5.
  Learn skills from the class skill books/trainers as levels allow, and ask the user which skills
  to buy before spending gold on books.
- Keep F1/F2 on the same slots in every window: broadcast sends the same keys.

## 4. Party and fighting style

- One party, window 1 leads, broadcast ON for fighting: window 1 casts and all four Voyagers cast
  the same skill at the same mob at the same moment.
- The tank is excluded from the broadcast and runs its own loop: **Taunt the mob hitting a
  Voyager** the moment a Voyager loses HP (taunting passive mobs that nobody fights only started
  extra fights).
- Every window defends itself from its own screen (`mbhunt.py`, `mbleader.py`).
- Rest below **70% HP / 12% SP** (tank excluded from both). A Voyager walking alone or at 0 SP is
  the most common way to die.
- **Passive mobs only** (CharacterInfo AI 5) while leveling: aggressive packs wiped the
  Explorers several times. Full EXP for mobs up to 9 levels above you.
- The user prefers a constant pace over perfect safety (an occasional death is OK), but no long
  standing around and no relocating mid-session unless the level plan says so.
- Each death: ~2.4% EXP and **5% durability**. At 0 durability a weapon silently stops casting
  skills. Repair at Blacksmith Goldie, Argent (2193,2706) - `mbrepair.py` - and check the weapon
  tooltip says 200/200.

## 5. Where (bands that worked)

| Levels | Spot | Notes |
|---|---|---|
| ~10-18 | Passive mobs near Argent/Andes | find in-game |
| 18-23 | Barbaric Bees near Rockery Haven | Smugglers there are a dense aggressive pack: don't walk through them |
| 18-30 | **Rookie Boxeroo L27, Andes field (Forest 4A)**, camp **(1070,2935)**, next to Andes Forest Haven (1010,2962) | the best spot: steady one-volley kills. Danger: Killer Shrooms (hit ~795, Voyagers had ~740 HP at 25) - kill them first. Record the spawn at Andes. |
| 25-30 (alt.) | Argent 8A (1440-1567 x 2640-2757): Meadow Deer L25, Rookie Boxeroo L27 | passive but sparse |
| 31-35 | Argent 9A: Grassland Elk L31 / White Owlie L32 | too strong before 31 (9 deaths in 50 min at Lv25). Battle Zone: players show the attack cursor - target only bars with a "Lv" mob name. |
| 31-40 | **Slowpoke Snail L35, Belda Meadow** (1195-1260 x 2755-2805), camp (1228,2770) | pink the snail (`mbpink.py 286`). Keep the north edge below y 2755: Grassland Wolves (L33, aggressive) killed Voyagers there. |
| 40+ | Great Polar Bears, Icicle (HANDOFF.md section 7) | after the class change |

The supervisor/leader pair from the first PC is `mbsuper.py` + `mbleader.py` (level moves, point
spending, Daily Buff popup); `mbgrind.py` has the bands, gear and selling. Read them before
reusing them - several hard-coded coordinates are the first PC's characters and spots.

## 6. Gear and gold

- Level-up chests (Flowery Chest at Lv15, Winged Chest, and the ones at 25/30/35/40) contain class
  gear: open them at each milestone and equip the newest gear everyone can wear. Chests need **5
  free bag slots**.
- When bags fill, sell junk to an NPC (Accessory - Rikka at Andes Forest Haven (997,2968)). Keep
  blueprints (Blurry/Encrypted/Sealed), New Sheepskin Scroll, chests, skill books, and gear for
  the next band. Equip first, then sell - selling first sold a fresh Lv25 dagger once.
- Tank shields at Goldie: Steel L20, Tower L30 (9,800 g), Feather L35 (13,050 g).

## 7. Second class at Lv40

**Champion (window 5):** accept "Champion's Valor" from Castle Guard Peter (2192,2767), hand in:
- 1 Criss Sword - buy at Goldie (~83,600 g)
- 1 Ninja Mask - Ninja Mole L38 (~1%), eastgoaf map via the Silver Mine portal (1786,2647):
  dangerous
- 5 Great Bear Tooth - Polar Bear L38, Icicle (1886-2079 x 520-540) - pink texture 205 helps
- 5 Solid Rock - Rock Golem L40, Ascaron (632-733 x 2533-2651), via Andes Forest Haven
Needs 1 free bag slot.

**Voyager (windows 1-4):** accept "Journey of the Voyager" from Little Daniel (2193,2730) - it is
**below the fold** of his long dialog: scroll the dialog. Then talk to four traders and pick the
quest in each one's list:
- Trader Burgess, Zephyr (3254,3278)
- Trader Sacenis, Glacier (2279,1112)
- Trader Dilady, Outlaw Isle (3595,739)
- Harbor Operator Whitney, Sara Haven, **at sea** - reachable only from a ship within ~3 tiles.
  Each Voyager builds a Guppy at Builder Sinbad, Argent (2255,2801) ("Build Ship" → "Build
  Guppy", ~10k g), sets sail with Harbor Operator Shirley (2243,2826). Aggressive Lv51-53 sharks
  sit either side of her (x 2438-3038 and 3238-3838, y 3344-3672): approach from the north.
- Then hand in to Daniel (1 free bag slot).
The isles via Island Teleporter Silvius, Argent (2248,2853), free at ≤ Lv40.
`mbvoyager.py WINDOW accept|zephyr|glacier|outlaw|finish` does the land steps; ship building and sailing are functions in it
(first PC; check the coordinates still hold).

## 8. Live BPO findings (what the files got wrong)

- Sea trade (cargo between ports) is disabled.
- "An Explorer's Life" (Daniel, repeatable) pays ~1.8k gold on BPO, not 35k: not worth farming.
- Teleports cost 500 g from Lv41.
- Lv40 chests give a "Tier 2 Promotion Manual" - an info item only.
- Gold at Lv40 was ~20-60k per character: plan the Criss Sword (83k) and 4 ships (40k) early.
