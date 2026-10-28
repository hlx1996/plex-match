#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Move every non-year-bucket folder at the Movies root into Movies/Collections/.

Year buckets stay at the root; anything else at the root is assumed to be a
multi-film collection (franchise box sets, top-N lists, director retrospectives).
Adjust BUCKET to your own bucket naming scheme.
"""
import json, os, re, sys

import _config

APPLY = "--apply" in sys.argv
# argv[1] (or PLEX_MOVIES_DIR) = the movie library root
M = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
     else os.environ.get("PLEX_MOVIES_DIR"))
if not M or not os.path.isdir(M):
    sys.exit("usage: movies_collections.py <Movies 库根目录> [--apply]\n"
             "       (or export PLEX_MOVIES_DIR=<path>)")
DEST = os.path.join(M, "Collections")
MAP = os.path.join(_config.backup_dir(), "plex-movies-collections-map.json")

BUCKET = re.compile(r"^(19XX|200X|201X|20(2[0-6]))$")
SKIP = re.compile(r"^(\.|@Recycle$)")

plan, keep, errors = [], [], []
for name in sorted(os.listdir(M)):
    p = os.path.join(M, name)
    if not os.path.isdir(p) or SKIP.match(name):
        continue
    if BUCKET.match(name):
        keep.append(name)
        continue
    if name == "Collections":
        continue
    dst = os.path.join(DEST, name)
    if os.path.exists(dst):
        errors.append("TARGET EXISTS %s" % dst)
    else:
        plan.append((p, dst))

loose = [f for f in os.listdir(M) if os.path.isfile(os.path.join(M, f))]
if loose:
    errors.append("LOOSE FILES AT ROOT: %s" % loose)

print("=== %s ===" % ("APPLY" if APPLY else "DRY-RUN"))
for e in errors:
    print("  !! " + e)
print("\n留在 Movies/ 顶层的年份桶 (%d): %s" % (len(keep), ", ".join(keep)))
print("\n移入 Collections/ 的合集目录 (%d):" % len(plan))
for s, d in plan:
    n = sum(1 for x in os.listdir(s) if not x.startswith("."))
    print("   [%3d 项] %s" % (n, os.path.basename(s)))
print("\n合计 %d 个目录，前置错误 %d" % (len(plan), len(errors)))
if errors or not APPLY:
    sys.exit(1 if errors else 0)

os.makedirs(DEST, exist_ok=True)
done = []
for s, d in plan:
    os.rename(s, d)
    done.append(dict(src=s, dst=d))
    print("  OK %s" % os.path.basename(s))
json.dump(done, open(MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n已移动 %d 个目录，回退映射 -> %s" % (len(done), MAP))
