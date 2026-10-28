#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot every movie's identity so matches can be re-applied after a move.

Moving a file inside a movie library does NOT reliably re-link: Plex often
creates a brand-new `local://` entry and leaves the old one behind, so the same
film shows up twice and the total count goes UP. Which ones break is
unpredictable, so always snapshot before a bulk move.

Record file path -> (guid, title, year, ratingKey, title_sort, watch state),
keyed by BASENAME, which survives the move.
"""
import sqlite3, urllib.parse, json, os, sys
import _config

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/movie_identity_snapshot.json"

con = sqlite3.connect(_config.db_uri(), uri=True)
c = con.cursor()

snap = {}
c.execute("""select mi.id, mi.title, mi.year, mi.guid, mi.title_sort, mi.original_title,
                    mi.library_section_id
             from metadata_items mi where mi.metadata_type = 1""")
for mid, title, year, guid, tsort, otit, sec in c.fetchall():
    c.execute("""select mp.file from media_items m join media_parts mp on mp.media_item_id = m.id
                 where m.metadata_item_id = ?""", (mid,))
    files = [r[0] for r in c.fetchall() if r[0]]
    # this library has no Plex Collection objects (verified: no collection
    # tables, tags_collection set on exactly one row), so nothing to preserve
    colls = []
    c.execute("""select ms.last_viewed_at, ms.view_count, ms.view_offset, ms.rating
                 from metadata_item_settings ms where ms.guid = ?""", (guid,))
    watch = [list(r) for r in c.fetchall()]
    for f in files:
        snap[os.path.basename(f)] = dict(
            ratingKey=mid, title=title, year=year, guid=guid, title_sort=tsort,
            original_title=otit, section=sec, path=f, collections=colls, watch=watch)

con.close()
json.dump(snap, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

byrk = {}
for v in snap.values():
    byrk.setdefault(v["ratingKey"], v)
print("files=%d  distinct movies=%d  with collections=%d  with watch state=%d"
      % (len(snap), len(byrk),
         sum(1 for v in byrk.values() if v["collections"]),
         sum(1 for v in byrk.values() if v["watch"])))
print("-> %s" % OUT)
