#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot show+episode identity so matches can be repaired after a rename.

Renaming episode files can make Plex rebuild a show entry; if that happens the
show-level guid is what has to be re-applied (episode matching follows the
show). Record both levels.
"""
import sqlite3, urllib.parse, json, os, sys
import _config

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/show_identity_pre.json"

con = sqlite3.connect(_config.db_uri(), uri=True)
c = con.cursor()

snap = {"shows": {}, "episodes": {}}
c.execute("""select sh.id, sh.title, sh.year, sh.guid, sh.title_sort, ls.name
             from metadata_items sh join library_sections ls on ls.id = sh.library_section_id
             where sh.metadata_type = 2""")
for sid, title, year, guid, tsort, lib in c.fetchall():
    snap["shows"][str(sid)] = dict(title=title, year=year, guid=guid,
                                   title_sort=tsort, lib=lib)
    c.execute("""select se.[index], e.[index], e.guid, mp.file
                 from metadata_items se
                 join metadata_items e on e.parent_id = se.id and e.metadata_type = 4
                 left join media_items m on m.metadata_item_id = e.id
                 left join media_parts mp on mp.media_item_id = m.id
                 where se.parent_id = ? and se.metadata_type = 3""", (sid,))
    for sidx, eidx, eguid, f in c.fetchall():
        if f:
            snap["episodes"][os.path.basename(f)] = dict(
                show=sid, show_title=title, s=sidx, e=eidx, guid=eguid, path=f)

con.close()
json.dump(snap, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("shows=%d  episode files=%d  -> %s"
      % (len(snap["shows"]), len(snap["episodes"]), OUT))
