#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Poll until Plex finishes scanning + metadata refresh.

JSON gotcha: /library/sections returns MediaContainer.Directory (not .Metadata).
Busy-job types must include library.update.item.metadata / library.scan.parts,
otherwise the poller declares "idle" while a refresh is still running.
"""
import json, sys, time, urllib.request
import _config

BASE = _config.base()
TOKEN = _config.token()
TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 3600

BUSY_PREFIX = ("library.",)
IGNORE = ("media.generate.intros", "media.generate.credits", "media.generate.mediaIndex",
          "media.generate.bif", "media.notify")


def get(path):
    req = urllib.request.Request(BASE + path, headers={
        "X-Plex-Token": TOKEN, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


t0 = time.time()
last = None
idle = 0
while True:
    dirs = get("/library/sections").get("MediaContainer", {}).get("Directory", []) or []
    state = [(d["title"], str(d.get("refreshing")), str(d.get("scanning"))) for d in dirs]
    acts = get("/activities").get("MediaContainer", {}).get("Activity", []) or []
    jobs = [(a.get("type"), a.get("progress")) for a in acts
            if a.get("type", "").startswith(BUSY_PREFIX) and a.get("type") not in IGNORE]
    sig = (tuple(state), tuple(jobs))
    if sig != last:
        print("[%5.0fs] %s | jobs=%s" % (time.time() - t0,
              " ".join("%s:ref=%s/scan=%s" % s for s in state), jobs or "none"), flush=True)
        last = sig
    busy = any(r == "1" or sc == "1" for _, r, sc in state) or bool(jobs)
    idle = 0 if busy else idle + 1
    if idle >= 4 and time.time() - t0 > 30:
        print("\nALL IDLE after %.0fs" % (time.time() - t0))
        break
    if time.time() - t0 > TIMEOUT:
        print("\nTIMEOUT after %.0fs -- still busy: %s" % (time.time() - t0, jobs or state))
        break
    time.sleep(15)
