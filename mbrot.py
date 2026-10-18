#!/usr/bin/env python3
"""Rotation of a window's view relative to window 1's, by matching terrain features (ORB) in the
world area of both screenshots. Every camera must look the same way (aligned on broadcast).

usage: mbrot.py [align] DISPLAY...
"""
import math
import sys
import time

import cv2
import numpy as np

import mbcam
import mbtarget as t

WORLD = (210, 60, 1215, 570)              # away from the party frames, chat, bars and minimap
REF = ":61"


def gray(d):
    x0, y0, x1, y1 = WORLD
    return cv2.cvtColor(np.asarray(t.screenshot(d).convert("RGB"))[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)


def rotation(d, ref=None):
    """Degrees window d's view is rotated relative to window 1's (None if no reliable match)."""
    a = ref if ref is not None else gray(REF)
    b = gray(d)
    sift = cv2.SIFT_create(4000)
    ka, da = sift.detectAndCompute(a, None)
    kb, db = sift.detectAndCompute(b, None)
    if da is None or db is None:
        return None
    m = [x for x, y in cv2.BFMatcher().knnMatch(da, db, k=2) if x.distance < 0.75 * y.distance]
    if len(m) < 12:
        return None
    pa = np.float32([ka[x.queryIdx].pt for x in m])
    pb = np.float32([kb[x.trainIdx].pt for x in m])
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=4)
    if M is None or inl is None or inl.sum() < 300:   # fewer: a false match (w4 "aligned" at -32)
        return None
    return math.degrees(math.atan2(M[1, 0], M[0, 0]))


def align(d, tol=2.0, log=print):
    ref = gray(REF)
    for _ in range(8):
        r = rotation(d, ref)
        if r is None:
            log(f"{d}: no terrain match")
            return None
        if abs(r) <= tol:
            log(f"{d}: aligned ({r:+.1f} deg)")
            return r
        # measured: dragging right lowers the rotation ~0.2 degree per pixel
        mbcam.drag(d, max(-400, min(400, int(round(r / 0.2)))))
    r = rotation(d, ref)
    log(f"{d}: left at {r}")
    return r


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "align":
        for d in args[1:]:
            align(d)
    else:
        ref = gray(REF)
        for d in args:
            print(d, rotation(d, ref))
