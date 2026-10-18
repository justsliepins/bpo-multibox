#!/usr/bin/env python3
"""Close the Daily Buff popup in any window whenever it appears (mbsuper.close_daily_buff), every 30 s."""
import time

import mbsuper

while True:
    try:
        mbsuper.close_daily_buff()
    except Exception as exc:
        print("buffwatch:", repr(exc), flush=True)
    time.sleep(30)
