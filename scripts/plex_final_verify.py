#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-reorg verification: is every item in the right library, right country
folder, right year bucket, and actually matched?

Reads the live DB read-only. Prints a report; writes JSON to argv[1].
"""
import sqlite3, urllib.parse, json, os, re, sys, collections

import _config

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/plex_final_verify.json"
import _config


con = sqlite3.connect(_config.db_uri(), uri=True)
c = con.cursor()


def q(sql, *a):
    c.execute(sql, a)
    return c.fetchall()


# ---------- section roots ----------
ROOTS = collections.defaultdict(list)
for sec, path in q("""select ls.name, sl.root_path from section_locations sl
                      join library_sections ls on ls.id = sl.library_section_id"""):
    ROOTS[sec].append(path.rstrip("/"))

COUNTRY_OK = {"China": {"CN"}, "Hong Kong": {"HK", "CN"},
              "Taiwan, Province of China": {"TW", "CN"}, "Japan": {"JP"},
              "Republic of Korea": {"KR"}, "United States of America": {"US"},
              "United Kingdom": {"EN"}}
KNOWN_DIRS = {"CN", "JP", "KR", "US", "EN", "TW", "HK", "Others"}
YEAR_BUCKET = lambda y: ("19XX" if y < 2000 else "200X" if y < 2010 else
                         "201X" if y < 2020 else str(y) if 2020 <= y <= 2026 else "2026")

report = {"wrong_library": [], "wrong_country": [], "wrong_year_bucket": [],
          "unmatched_shows": [], "unmatched_movies": [], "no_location": [],
          "dead_paths": [], "counts": {}, "sections": {}}

# ---------- shows ----------
shows = q("""select mi.id, mi.title, mi.year, mi.guid, ls.name,
                    (select group_concat(distinct t.tag) from taggings tg
                       join tags t on t.id = tg.tag_id
                      where tg.metadata_item_id = mi.id and t.tag_type = 8) as countries
             from metadata_items mi
             join library_sections ls on ls.id = mi.library_section_id
             where mi.metadata_type = 2 order by ls.name, mi.title""")

for sid, title, year, guid, sec, countries in shows:
    report["sections"].setdefault(sec, {"shows": 0})["shows"] = \
        report["sections"].get(sec, {}).get("shows", 0) + 1
    # the DB has no show-level path column; derive the show folder as the
    # shallowest common ancestor of all its episode file paths
    files = [r[0] for r in q("""select mp.file from media_parts mp
                                join media_items m on m.id = mp.media_item_id
                                join metadata_items e on e.id = m.metadata_item_id
                                where e.metadata_type = 4 and e.parent_id in
                                  (select id from metadata_items where parent_id = ?)
                                  and mp.file is not null and mp.file != ''""", sid)
             if r[0]]
    if not files:
        report["no_location"].append(dict(sec=sec, title=title, year=year))
        continue
    dirs = [os.path.dirname(f) for f in files]
    common = os.path.commonpath(dirs)
    # commonpath lands on the show dir only if every episode sits in a
    # "Season N" subfolder; otherwise it IS the show dir already
    if os.path.basename(common).lower().startswith("season") or \
            re.fullmatch(r"(?i)s\d{1,2}(\.\d)?|specials?", os.path.basename(common)):
        common = os.path.dirname(common)
    path = common
    if not os.path.exists(path):
        report["dead_paths"].append(dict(sec=sec, title=title, year=year, path=path))

    # 1) must live under one of this section's roots
    ok = any(path.startswith(r + "/") or path == r for r in ROOTS[sec])
    if not ok:
        owner = [s for s, rs in ROOTS.items() if any(path.startswith(r + "/") for r in rs)]
        report["wrong_library"].append(dict(title=title, year=year, sec=sec,
                                            path=path, actually_in=owner or ["<none>"]))
        continue

    # 2) country folder must match the DB country tag (skip PT/ download staging)
    if "/PT/" in path:
        continue
    rel = None
    for r in ROOTS[sec]:
        if path.startswith(r + "/"):
            rel = path[len(r) + 1:]
            break
    if rel is None:
        continue
    top = rel.split("/")[0]
    if top in KNOWN_DIRS:
        clist = [x.strip() for x in (countries or "").split(",") if x.strip()]
        if clist:
            allowed = set().union(*[COUNTRY_OK.get(x, set()) for x in clist]) | {"Others"}
            if top not in allowed:
                report["wrong_country"].append(dict(title=title, year=year, sec=sec,
                                                    folder=top, allowed=sorted(allowed),
                                                    countries=countries, path=path))

    if guid and guid.startswith("local:"):
        report["unmatched_shows"].append(dict(sec=sec, title=title, year=year, path=path))

# ---------- movies ----------
movies = q("""select mi.id, mi.title, mi.year, mi.guid, ls.name,
                     (select mp.file from media_parts mp
                        join media_items m on m.id = mp.media_item_id
                       where m.metadata_item_id = mi.id limit 1)
              from metadata_items mi
              join library_sections ls on ls.id = mi.library_section_id
              where mi.metadata_type = 1 order by ls.name, mi.title""")
report["sections"].setdefault("电影", {})["movies"] = 0
for mid, title, year, guid, sec, f in movies:
    report["sections"]["电影"]["movies"] += 1
    if not f:
        report["no_location"].append(dict(sec=sec, title=title, year=year))
        continue
    if not os.path.exists(f):
        report["dead_paths"].append(dict(sec=sec, title=title, year=year, path=f))
    ok = any(f.startswith(r + "/") for r in ROOTS[sec])
    if not ok:
        report["wrong_library"].append(dict(title=title, year=year, sec=sec, path=f,
                                            actually_in=["<none>"]))
        continue
    if guid and guid.startswith("local:"):
        report["unmatched_movies"].append(dict(sec=sec, title=title, year=year, path=f))
    # year bucket: only for the CACHEDEV2 Movies root (PT root is flat)
    for r in ROOTS[sec]:
        if f.startswith(r + "/") and "/Movies" in r:
            rel = f[len(r) + 1:]
            top = rel.split("/")[0]
            if top in ("19XX", "200X", "201X") or re.fullmatch(r"20(2[0-6])", top):
                if year and YEAR_BUCKET(int(year)) != top:
                    report["wrong_year_bucket"].append(
                        dict(title=title, year=year, folder=top,
                             want=YEAR_BUCKET(int(year)), path=f))
            else:
                report.setdefault("collections", []).append(top)
            break

con.close()

# ---------- print ----------
print("=" * 72)
print("LIBRARY ROOTS")
for s, rs in sorted(ROOTS.items()):
    print("  %-8s %d roots" % (s, len(rs)))
print("\nCOUNTS  " + json.dumps(report["sections"], ensure_ascii=False))
for key, label in [("wrong_library", "WRONG LIBRARY"),
                   ("wrong_country", "WRONG COUNTRY FOLDER"),
                   ("wrong_year_bucket", "WRONG YEAR BUCKET"),
                   ("unmatched_shows", "UNMATCHED SHOWS"),
                   ("unmatched_movies", "UNMATCHED MOVIES"),
                   ("no_location", "NO LOCATION (phantom)"),
                   ("dead_paths", "DEAD PATHS")]:
    items = report[key]
    print("\n%s: %d" % (label, len(items)))
    for it in items[:25]:
        print("   " + json.dumps(it, ensure_ascii=False)[:170])
    if len(items) > 25:
        print("   ... +%d more" % (len(items) - 25))
coll = collections.Counter(report.get("collections", []))
print("\nMOVIE COLLECTION FOLDERS (kept flat, not year-bucketed): %d" % len(coll))
for k, v in coll.most_common():
    print("   %-4d %s" % (v, k[:80]))

with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
print("\n-> %s" % OUT)
