#!/usr/bin/env python3
"""Story quest steps for one window: talk to an NPC, pick the quest line (scrolling the dialog if it
is below the fold), press Accept/Complete. Broadcast must be off.

    mbstory.py WINDOW STEP [STEP...]
    STEP = npc:quest   e.g. senna:"Secretary Message"   (see NPCS)
         = argent      teleport to Argent from Andes Forest Haven (Wendy)

Quest chain and rewards: ~/multibox/story-quests.txt; memory bpo-story-quests.
"""
import sys

import mbcoords
import mbquest as q
import mbvoyager as v
import mbgrind as g

NPCS = {
    "senna": ("Newbie Guide - Senna", (2223, 2785)),
    "salvier": ("Argent Secretary - Salvier", (2219, 2749)),
    "william": ("General - William", (2277, 2831)),
    "ditto": ("Physican - Ditto", (2250, 2770)),
    "tommy": ("Youth - Tommy", (1962, 2694)),
}


def step(w, npc, quest):
    i, d = w - 1, f":6{w}"
    name, xy = NPCS[npc]
    title = v.talk_to(i, d, name, xy)
    if not title:
        v.log(f"window {w}: couldn't talk to {name} at {mbcoords.read(d)}")
        return False
    if not v.pick(d, title, quest):
        txt = q.dialog_text(d, title)[0].replace("\n", " | ")
        v.log(f"window {w}: {name} has no '{quest}': {txt[:160]}")
        q.close_all(v.STATE, i, d)
        return False
    import time
    time.sleep(1.2)
    v.log(f"window {w}: {name} / {quest}: clicked {v.button(d)}")
    time.sleep(1.5)
    q.close_all(v.STATE, i, d)
    return True


def main(w, steps):
    i, d = w - 1, f":6{w}"
    with q.Pause(v.STATE, d):
        q.close_all(v.STATE, i, d)
        for s in steps:
            if s == "argent":
                q.teleport(v.STATE, i, d, g.WENDY[0], g.WENDY[1], ["Teleport: Argent City"])
                continue
            npc, quest = s.split(":", 1)
            step(w, npc, quest)
    v.log(f"window {w}: story steps done at {mbcoords.read(d)}")


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2:])
