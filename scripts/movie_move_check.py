#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diff current movie state against a pre-move identity snapshot and report
anything that detached, then (with --fix) re-apply the recorded guid.

Re-matching by recorded guid is exact -- no /matches search, no guessing.
`PUT /library/metadata/<k>/match` needs guid + name + year + language or it
returns a bare 400 with an HTML body and no hint.
"""
import sqlite3, urllib.parse, urllib.request, json, os, sys, time
import _config

APPLY = "--fix" in sys.argv
SNAP = sys.argv[1] if len(sys.argv) > 1 else "/tmp/movie_identity_pre.json"
BASE = _config.base()
TOKEN = _config.token()

pre = json.load(open(SNAP, encoding="utf-8"))
con = sqlite3.connect(_config.db_uri(), uri=True)
c = con.cursor()

c.execute("""select mi.id, mi.title, mi.year, mi.guid,
               (select mp.file from media_items m join media_parts mp on mp.media_item_id=m.id
                 where m.metadata_item_id = mi.id limit 1)
             from metadata_items mi where mi.metadata_type = 1 and mi.library_section_id = 1""")
now = c.fetchall()
c.execute("""select count(*), sum(case when guid like 'local:%' then 1 else 0 end)
             from metadata_items where metadata_type=1 and library_section_id=1""")
tot, unm = c.fetchone()
print("电影库: total=%s  unmatched=%s   (移动前 total=698 unmatched=0)" % (tot, unm))

lost, changed_guid, missing_file = [], [], []
seen_rk = set()
for mid, title, year, guid, f in now:
    seen_rk.add(mid)
    if not f:
        missing_file.append(dict(id=mid, title=title))
        continue
    old = pre.get(os.path.basename(f))
    if not old:
        continue
    if guid and guid.startswith("local:"):
        lost.append(dict(id=mid, title=title, year=year, file=f, want=old["guid"],
                         want_title=old["title"], want_year=old["year"],
                         old_title_sort=old["title_sort"]))
    elif old["guid"] and guid != old["guid"]:
        changed_guid.append(dict(id=mid, title=title, was=old["guid"], now=guid))

gone = [dict(id=v["ratingKey"], title=v["title"], path=v["path"])
        for v in pre.values() if v["ratingKey"] not in seen_rk]

print("\n条目消失(旧 ratingKey 不在了): %d" % len(gone))
for g in gone[:15]:
    print("   %s  %s" % (g["id"], (g["title"] or "")[:50]))
print("\n掉匹配(guid 变 local://): %d" % len(lost))
for l in lost[:40]:
    print("   id=%-6s %-34s -> 应为 %s (%s)" % (l["id"], (l["title"] or "")[:34],
                                               l["want"], l["want_title"]))
if len(lost) > 40:
    print("   ... +%d more" % (len(lost) - 40))
print("\nguid 变化(可能是合并/重挑，需人工看): %d" % len(changed_guid))
for g in changed_guid[:15]:
    print("   %-30s %s -> %s" % ((g["title"] or "")[:30], g["was"][-12:], g["now"][-12:]))
print("\n无媒体文件: %d" % len(missing_file))

json.dump(dict(lost=lost, changed_guid=changed_guid, gone=gone,
               missing_file=missing_file),
          open("/tmp/movie_move_diff.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

if not APPLY:
    print("\n(dry-run；加 --fix 用快照里的 guid 精确补回)")
    sys.exit(0)

print("\n=== 补匹配 %d 项 ===" % len(lost))
ok = fail = 0
for l in lost:
    q = urllib.parse.urlencode({"guid": l["want"], "name": l["want_title"],
                                "year": l["want_year"] or "", "language": "zh-CN",
                                "X-Plex-Token": TOKEN})
    req = urllib.request.Request("%s/library/metadata/%s/match?%s" % (BASE, l["id"], q),
                                method="PUT")
    try:
        urllib.request.urlopen(req, timeout=60).read()
        ok += 1
    except Exception as e:
        fail += 1
        print("   FAIL %s (%s): %s" % (l["id"], l["want_title"], e))
    time.sleep(0.4)
print("成功 %d / 失败 %d" % (ok, fail))
