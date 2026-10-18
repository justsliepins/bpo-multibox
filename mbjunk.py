#!/usr/bin/env python3
"""Junk list from the server files: what the current hunt can put into a bag and nobody wants.

Junk = every item the band's monsters drop (CharacterInfo.txt drop columns) or the newbie
chests give (ItemEffect.lua NewbieChest GiveItem calls) that is
  * not equipment (ItemInfo type 1-26: weapons, armour, jewellery) - that is sold or worn, and
  * not kept: Blurry Blueprint (1000), New Sheepskin Scroll (684), chests (436-439).
Deleted in place, only when a bag is full (the user's rule, 2026-10-10).

usage: mbjunk.py "Angelic Panda" ["Rookie Boxeroo" ...]   prints the junk names
"""
import os
import re
import sys

RES = os.path.expanduser("~/Work/TopServerfiles/TalesOfPirateDX9-main/server/GameServer/resource")
KEEP_IDS = {1000, 684, 436, 437, 438, 439}
EQUIPMENT_TYPES = set(range(1, 27))
# useful even if not asked for: EXP boosts, fairies, 2nd-class manuals, chests
KEEP_WORDS = ("blueprint", "sheepskin", "chest", "heaven", "fairy", "promotion")


def _items():
    out = {}
    for line in open(os.path.join(RES, "ItemInfo.txt"), encoding="latin-1"):
        f = line.split("\t")
        if len(f) > 11 and f[0].isdigit():
            try:
                out[int(f[0])] = (f[1].strip(), int(f[10]))
            except ValueError:
                pass
    return out


def _mob_drops(names):
    want = {n.lower() for n in names}
    ids = set()
    for line in open(os.path.join(RES, "CharacterInfo.txt"), encoding="latin-1"):
        f = line.split("\t")
        if len(f) > 52 and f[0].isdigit() and f[1].strip().lower() in want:
            for col in (46, 51):                 # normal drops, quest drops
                ids |= {int(x) for x in f[col].split(",") if x.strip().isdigit() and int(x) > 0}
    return ids


def _chest_items():
    src = open(os.path.join(RES, "script/calculate/ItemEffect.lua"), encoding="latin-1").read()
    m = re.search(r"function NewbieChest\(.*?\nend\n", src, re.S)
    return {int(x) for x in re.findall(r"GiveItem\(role,\s*0,\s*(\d+)", m.group(0))} if m else set()


def junk_names(mob_names):
    items = _items()
    ids = (_mob_drops(mob_names) | _chest_items()) - KEEP_IDS
    names = set()
    for i in ids:
        if i in items:
            name, typ = items[i]
            low = name.lower()
            if typ not in EQUIPMENT_TYPES and not any(k in low for k in KEEP_WORDS):
                names.add(name)
    return sorted(names)


def is_junk(tip, names):
    """The tooltip's item name (its first readable line) is one of the junk names."""
    import difflib
    first = " ".join(re.sub(r"[^A-Za-z' ]", " ", tip).split())
    low = first.lower()
    if any(k in low for k in KEEP_WORDS + ("requirement",)):
        return False                            # kept items and wearable gear: never
    for n in names:
        if n.lower() in low:
            return True
        words = low.split()
        L = len(n.split())
        for k in range(len(words)):
            if difflib.SequenceMatcher(None, n.lower(), " ".join(words[k:k + L])).ratio() >= 0.88:
                return True
    return False


if __name__ == "__main__":
    for n in junk_names(sys.argv[1:]):
        print(n)
