#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find shows whose media parts come from folders that are NOT variants of the
same series -- i.e. unrelated releases absorbed into one Plex entry.

This is the failure mode behind 来自深渊 swallowing Madoka Magica / Summer Time
Rendering / Scissor Seven S2: folders whose names are entirely [bracket] groups
reduce to an empty show title, so the scanner piles them onto a neighbour.

Multi-version duplicates (same series, two encodes) are expected and get
reported separately from genuinely unrelated folders.
"""
import sqlite3, urllib.parse, os, re, sys, json, collections
import _config

con = sqlite3.connect(_config.db_uri(), uri=True)
c = con.cursor()

BRACKET = re.compile(r"\[[^\[\]]*\]|\([^\(\)]*\)")
NOISE = re.compile(r"(?i)\b(1080p|2160p|720p|4k|bluray|bdrip|bd|web-?dl|webrip|hdtv|x264|x265|"
                   r"h\.?264|h\.?265|hevc|avc|10bit|hdr|dolby|atmos|dts|aac|ac3|flac|ddp?[\d.]*|"
                   r"truehd|remux|complete|proper|repack|multi|dual|chs|jap|gb|big5|jp|sc|tc|"
                   r"season|specials?|s\d{1,2}|e\d{1,3}|fin|ova|oad|sp)\b")
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def toks(name):
    """Meaningful title tokens from a release folder name."""
    s = BRACKET.sub(" ", name)
    s = YEAR.sub(" ", s)
    s = NOISE.sub(" ", s)
    s = re.sub(r"[-_.]+", " ", s)
    out = set()
    for t in s.split():
        t = t.strip(" -·:：!！?？~～")
        if len(t) >= 2 and not t.isdigit():
            out.add(t.lower())
        # CJK has no spaces; split into 2-grams so 刺客伍六七 matches 伍六七
        if re.search(r"[\u4e00-\u9fff]", t) and len(t) >= 2:
            for i in range(len(t) - 1):
                out.add(t[i:i + 2])
    return out


c.execute("""select mi.id, mi.title, mi.year, mi.library_section_id, ls.name
             from metadata_items mi join library_sections ls on ls.id = mi.library_section_id
             where mi.metadata_type = 2 order by ls.name, mi.title""")
shows = c.fetchall()

suspect, multiver = [], []
for sid, title, year, secid, secname in shows:
    c.execute("""select distinct mp.file from metadata_items e
                 join media_items m on m.metadata_item_id = e.id
                 join media_parts mp on mp.media_item_id = m.id
                 where e.metadata_type = 4 and e.parent_id in
                   (select id from metadata_items where parent_id = ?)
                   and mp.file is not null""", (sid,))
    files = [r[0] for r in c.fetchall()]
    if not files:
        continue
    # show-level folder = the path element right under a library root
    roots = [r[0] for r in c.execute(
        "select root_path from section_locations where library_section_id=?", (secid,))]
    folders = collections.Counter()
    for f in files:
        for rt in roots:
            if f.startswith(rt.rstrip("/") + "/"):
                rel = f[len(rt.rstrip("/")) + 1:]
                folders[rel.split("/")[0]] += 1
                break
    if len(folders) < 2:
        continue
    # does every folder share a token with the Plex title?
    tt = toks(title or "") | toks(str(year or ""))
    orphans = [f for f in folders if not (toks(f) & tt)]
    # folders that share tokens with each other but not the title are still
    # suspicious; folders that share nothing with ANY other folder are worse
    alltok = {f: toks(f) for f in folders}
    unrelated = []
    for f in folders:
        others = set()
        for g in folders:
            if g != f:
                others |= alltok[g]
        if not (alltok[f] & others) and not (alltok[f] & tt):
            unrelated.append(f)
    rec = dict(sec=secname, title=title, year=year, id=sid,
               folders={k: v for k, v in folders.most_common()},
               no_title_token=orphans, unrelated=unrelated)
    if unrelated:
        suspect.append(rec)
    elif orphans:
        multiver.append(rec)

print("=" * 74)
print("UNRELATED FOLDERS ABSORBED INTO ONE SHOW: %d" % len(suspect))
for r in suspect:
    print("\n  [%s] %s (%s)  id=%s" % (r["sec"], r["title"], r["year"], r["id"]))
    for f, n in r["folders"].items():
        mark = "  <<< UNRELATED" if f in r["unrelated"] else ""
        print("     %-4d %s%s" % (n, f[:88], mark))

print("\n" + "=" * 74)
print("FOLDER NAME SHARES NO TOKEN WITH PLEX TITLE (review, may be fine): %d" % len(multiver))
for r in multiver[:40]:
    print("  [%s] %-28s %-5s  %s" % (r["sec"], (r["title"] or "")[:28], r["year"],
                                    " | ".join("%dx %s" % (v, k[:44]) for k, v in
                                               list(r["folders"].items())[:3])))
if len(multiver) > 40:
    print("  ... +%d more" % (len(multiver) - 40))

json.dump(dict(suspect=suspect, review=multiver),
          open("/tmp/plex_absorb_audit.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n-> /tmp/plex_absorb_audit.json")
