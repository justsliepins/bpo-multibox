#!/usr/bin/env python3
"""Turn a mob solid magenta (253,0,202) so the bots can see it: copy assets/pink.bmp over its
texture texture/character/<framework>000000.bmp in the BPO client (the framework number is column 6
of the mob's row in the server's CharacterInfo.txt). The original is kept as <name>-original.<ext>.
The client reads textures at start: restart the clients afterwards.

usage: mbpink.py FRAMEWORK...            e.g. mbpink.py 504 205   (Great Polar Bear, Polar Bear)
       mbpink.py --restore FRAMEWORK...

Pinked on the first PC (2026-10-18): 286 Slowpoke Snail, 205/504 polar bears, 544 Horrific Snowman,
501 Steel-shell Snail, 103/517 Cumbersome Yeti, story mobs 21 57 159 47 180 48 226 219 75.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHAR = os.path.expanduser("~/Games/BlackPearlOnline/prefix/drive_c/BlackPearlOnline/texture/character")


def find(fw):
    """The texture file in its real case (Wine is case-insensitive: .bmp and .BMP both occur)."""
    want = f"{int(fw):04d}000000.bmp"
    for name in os.listdir(CHAR):
        if name.lower() == want:
            return os.path.join(CHAR, name)
    return None


def main(args):
    restore = args and args[0] == "--restore"
    for fw in args[1:] if restore else args:
        path = find(fw)
        if not path:
            print(f"{fw}: no texture/character/{int(fw):04d}000000.bmp")
            continue
        base, ext = os.path.splitext(path)
        orig = f"{base}-original{ext}"
        if restore:
            if os.path.exists(orig):
                shutil.copyfile(orig, path)
                print(f"{fw}: restored")
            continue
        if not os.path.exists(orig):
            shutil.copyfile(path, orig)
        shutil.copyfile(os.path.join(HERE, "assets", "pink.bmp"), path)
        print(f"{fw}: pink ({os.path.basename(path)}; original kept)")


if __name__ == "__main__":
    main(sys.argv[1:])
