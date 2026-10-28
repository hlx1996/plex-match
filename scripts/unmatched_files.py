#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accurate unmatched inventory: count FILES, not episode slots.

The naive per-episode count hides the worst cases, because when the scanner
can't find an episode number it piles every file in the folder onto ONE phantom
slot (17.3 关于性: 9 files -> S1E17; 白色巨塔: 21 files -> S1E2003). Those read
as "1 unmatched episode" but are really "the whole folder is unindexed".
"""
import sqlite3, urllib.parse, os, re, sys, json, collections
import _config

con = sqlite3.connect(_config.db_uri(), uri=True)
c = con.cursor()

rows = c.execute("""select ls.name, sh.id, sh.title, sh.year, se.[index], e.[index],
                           mp.file
                    from metadata_items e
                    join metadata_items se on se.id = e.parent_id
                    join metadata_items sh on sh.id = se.parent_id
                    join library_sections ls on ls.id = sh.library_section_id
                    join media_items m on m.metadata_item_id = e.id
                    join media_parts mp on mp.media_item_id = m.id
                    where e.metadata_type = 4 and e.guid like 'local:%'
                    order by ls.name, sh.title, se.[index], e.[index]""").fetchall()

# which files carry NO usable SxxEyy / Exx token at all?
HAS_EP = re.compile(r"(?i)(?<![a-z0-9])s\d{1,2}[ ._-]?e\d{1,3}|(?<![a-z0-9])e(p)?\d{1,3}(?!\d)|第\s*\d{1,3}\s*话")

byshow = collections.OrderedDict()
for lib, sid, title, year, sidx, eidx, f in rows:
    k = (lib, sid, title, year)
    byshow.setdefault(k, collections.defaultdict(list))[(sidx, eidx)].append(f)

total_files = sum(len(v) for s in byshow.values() for v in s.values())
print("未匹配集槽位: %d 个；受影响文件: %d 个；涉及剧: %d 部\n" % (
    sum(len(s) for s in byshow.values()), total_files, len(byshow)))
print("=" * 92)

stacked, singles = [], []
for (lib, sid, title, year), slots in byshow.items():
    nfiles = sum(len(v) for v in slots.values())
    # a slot holding >1 file = scanner gave up on numbering
    st = {k: v for k, v in slots.items() if len(v) > 1}
    rec = dict(lib=lib, id=sid, title=title, year=year, files=nfiles,
               slots=len(slots), stacked={("S%sE%s" % k): v for k, v in st.items()},
               singles=["S%sE%s" % k for k, v in slots.items() if len(v) == 1],
               sample=[os.path.basename(v[0]) for v in list(slots.values())[:2]])
    (stacked if st else singles).append(rec)

print("\n【A 类】整个文件夹被塞进一个幻影集号 —— 文件名里没有可用集号，全部未索引")
print("        这类是**我们自己的命名问题**，改名就能修，不是上游缺口\n")
for r in sorted(stacked, key=lambda x: -x["files"]):
    print("  [%s] %s (%s)  id=%s" % (r["lib"], r["title"], r["year"], r["id"]))
    for slot, fs in r["stacked"].items():
        noep = sum(1 for f in fs if not HAS_EP.search(os.path.basename(f)))
        print("     %s 上叠了 %d 个文件（其中 %d 个文件名里没有任何 Exx 记号）" % (slot, len(fs), noep))
        for f in fs[:3]:
            print("        %s" % os.path.basename(f)[:88])
        if len(fs) > 3:
            print("        ... 共 %d 个" % len(fs))

print("\n" + "=" * 92)
print("\n【B 类】单集未匹配 —— 需要逐个判断是特辑归位(可修) 还是上游缺口\n")
grp = collections.defaultdict(list)
for r in singles:
    grp[r["lib"]].append(r)
for lib in grp:
    for r in sorted(grp[lib], key=lambda x: x["title"]):
        print("  [%s] %-22s %-5s id=%-6s 未匹配槽位: %s" % (
            lib, (r["title"] or "")[:22], r["year"], r["id"], ", ".join(r["singles"])))
        for s in r["sample"]:
            print("        %s" % s[:88])

json.dump(dict(stacked=stacked, singles=singles),
          open("/tmp/unmatched_files.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n-> /tmp/unmatched_files.json")
