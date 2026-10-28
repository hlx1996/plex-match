#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot Plex library health from the live DB (read-only) + REST API."""
import sqlite3, urllib.parse, json, sys, os

import _config

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/plex_state_snapshot.json"
import _config


con = sqlite3.connect(_config.db_uri(), uri=True)
c = con.cursor()


def q(sql, *a):
    c.execute(sql, a)
    return c.fetchall()


snap = {}

# sections
snap["sections"] = [dict(key=r[0], name=r[1], type=r[2], scanned_at=r[3])
                    for r in q("select id, name, section_type, scanned_at from library_sections order by id")]

# item counts per section per type
snap["counts"] = {}
for r in q("""select ls.name, mi.metadata_type, count(*)
              from metadata_items mi join library_sections ls on ls.id = mi.library_section_id
              group by ls.name, mi.metadata_type order by ls.name, mi.metadata_type"""):
    snap["counts"].setdefault(r[0], {})[r[1]] = r[2]

# unmatched (local: guid) per section per type
snap["unmatched"] = {}
for r in q("""select ls.name, mi.metadata_type, count(*)
              from metadata_items mi join library_sections ls on ls.id = mi.library_section_id
              where mi.guid like 'local:%'
              group by ls.name, mi.metadata_type order by ls.name, mi.metadata_type"""):
    snap["unmatched"].setdefault(r[0], {})[r[1]] = r[2]

# episodes total / unmatched
snap["episodes"] = dict(
    total=q("select count(*) from metadata_items where metadata_type=4")[0][0],
    unmatched=q("select count(*) from metadata_items where metadata_type=4 and guid like 'local:%'")[0][0],
)

# watch state
snap["watch"] = dict(
    rows=q("select count(*) from metadata_item_settings")[0][0],
    viewed=q("select count(*) from metadata_item_settings where last_viewed_at is not null")[0][0],
    rating=q("select count(*) from metadata_item_settings where rating is not null")[0][0],
)

# dead media parts (file no longer on disk)
dead = {}
missing = []
for r in q("""select mp.file from media_parts mp
              where mp.file is not null and mp.file != ''"""):
    p = r[0]
    if not os.path.exists(p):
        top = "/".join(p.split("/")[:4])
        dead[top] = dead.get(top, 0) + 1
        if len(missing) < 40:
            missing.append(p)
snap["dead_media"] = dict(by_root=dead, total=sum(dead.values()), sample=missing)

# media_parts total
snap["media_parts"] = q("select count(*) from media_parts")[0][0]

# items with missing/empty title_sort (CLP target)
snap["no_title_sort"] = {}
for r in q("""select ls.name, count(*)
              from metadata_items mi join library_sections ls on ls.id = mi.library_section_id
              where mi.metadata_type in (1,2) and (mi.title_sort is null or mi.title_sort = '')
              group by ls.name"""):
    snap["no_title_sort"][r[0]] = r[1]

con.close()

with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(snap, fh, ensure_ascii=False, indent=1)

# ---- console report ----
TYPE = {1: "movie", 2: "show", 3: "season", 4: "episode"}
STYPE = {1: "movie", 2: "show", 3: "artist", 4: "photo"}
print("=== sections ===")
for s in snap["sections"]:
    print("  %-3s %-8s %s" % (s["key"], s["name"], STYPE.get(s["type"], s["type"])))
print("\n=== counts / unmatched ===")
print("  %-10s %8s %8s %8s %8s   %s" % ("library", "movie", "show", "season", "episode", "UNMATCHED"))
for name in sorted(snap["counts"]):
    cc = snap["counts"].get(name, {})
    uu = snap["unmatched"].get(name, {})
    ustr = ", ".join("%s=%d" % (TYPE[k], v) for k, v in sorted(uu.items())) or "-"
    print("  %-10s %8d %8d %8d %8d   %s" % (
        name, cc.get(1, 0), cc.get(2, 0), cc.get(3, 0), cc.get(4, 0), ustr))
print("\n=== episodes ===  total=%d  unmatched=%d  (%.2f%% matched)" % (
    snap["episodes"]["total"], snap["episodes"]["unmatched"],
    100.0 * (snap["episodes"]["total"] - snap["episodes"]["unmatched"]) / max(1, snap["episodes"]["total"])))
print("=== watch ===     rows=%d  viewed=%d  rated=%d" % (
    snap["watch"]["rows"], snap["watch"]["viewed"], snap["watch"]["rating"]))
print("=== media_parts=%d  dead=%d" % (snap["media_parts"], snap["dead_media"]["total"]))
for k, v in sorted(snap["dead_media"]["by_root"].items(), key=lambda x: -x[1]):
    print("      %-45s %d" % (k, v))
print("=== no title_sort ===")
for k, v in sorted(snap["no_title_sort"].items()):
    print("      %-12s %d" % (k, v))
print("\nsnapshot -> %s" % OUT)
