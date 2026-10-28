#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-8 renames: put specials into the season TMDB actually uses, and fix
two folders whose names defeat the scanner.

Every mapping below was verified against the TMDB season page -- see SKILL.md
round 8. Three look-alike specials are deliberately NOT touched because the
local file gives no way to tell which TMDB special it is:
  摩登家庭 S11E00 (42min, TMDB S0 has 32 candidates)
  窥探 S01E00 x2 (59min recap, TMDB S0 has 回顾特辑1/2)
  命运石之门 S01E00 (4m30s, TMDB S00E02-E05 are four similar shorts)
"""
import fnmatch, json, os, re, sys

import _config

APPLY = "--apply" in sys.argv
# This script is a WORKED EXAMPLE, not a generic tool: the SUBS table below
# is one library's specials, each mapping verified against its TMDB season
# page. Copy it and rewrite the table for your own library.
TV = os.environ.get("PLEX_TV_DIR")    # TVSeries library root
AN = os.environ.get("PLEX_ANIME_DIR") # Anime library root
MAP = os.path.join(_config.backup_dir(), "plex-round8-rename-map.json")

# (label, root, filename glob, regex on filename, replacement)
SUBS = [
    # --- 唐顿庄园：圣诞特辑在 TMDB Season 0，逐集对号 ---
    ("唐顿庄园 S2圣诞", TV, "Downton.Abbey.S02E09.Christmas*", r"S02E09", "S00E02"),
    ("唐顿庄园 S3圣诞", TV, "Downton.Abbey.S03E09.Christmas*", r"S03E09", "S00E03"),
    ("唐顿庄园 S4圣诞", TV, "Downton.Abbey.S04E09.Christmas*", r"S04E09", "S00E05"),
    ("唐顿庄园 S5圣诞", TV, "Downton.Abbey.S05E09.Christmas*", r"S05E09", "S00E06"),
    ("唐顿庄园 S6圣诞", TV, "Downton.Abbey.S06E09.Christmas*", r"S06E09", "S00E11"),
    # --- 单集特辑 ---
    ("IT狂人",       TV, "The.It.Crowd.S05E01*",        r"S05E01", "S00E01"),
    ("黑镜(无S0,进S2)", TV, "Black.Mirror.S03E00*",      r"S03E00", "S02E04"),
    ("神探夏洛克",     TV, "Sherlock.S01E00*",            r"S01E00", "S00E01"),
    ("是，大臣",       TV, "Yes Minister S03E08*",        r"S03E08", "S00E02"),
    ("半泽直树",       TV, "Hanzawa.Naoki.2020.S02E00*",  r"S02E00", "S00E01"),
    ("摩斯探长前传",    TV, "Endeavour.S01E00*",           r"S01E00", "S00E01"),
    ("9号秘事",        TV, "Inside.No.9.S05E00*",         r"S05E00", "S00E02"),
    ("流星花园",       TV, "F4.Thailand.Boys.Over.Flowers.E00*",
                                                    r"(?<![A-Za-z0-9])E00(?![0-9])", "S00E01"),
    # --- 兄弟连花絮：TMDB S00E03=The Making of, S00E04=We Stand Alone Together ---
    ("兄弟连 纪录片A",  TV, "Band.of.Brothers.E01.*We.Stand.Alone*",
                                                    r"(?<![A-Za-z0-9])E01(?![0-9])", "S00E04"),
    ("兄弟连 纪录片B",  TV, "Band.of.Brothers.E02.*The.Making*",
                                                    r"(?<![A-Za-z0-9])E02(?![0-9])", "S00E03"),
]

# 命运石之门 S01E25 = TMDB S00E01「Egoistic Poriomania」，同时要挪出 "Season 01"
STEINS_SRC = os.path.join(AN, "JP/[SumiSora&CASO][Steins_Gate][BDRip]/Season 01/Steins Gate - S01E25.mkv")
STEINS_DST = os.path.join(AN, "JP/[SumiSora&CASO][Steins_Gate][BDRip]/Season 00/Steins Gate - S00E01.mkv")

# 生活大爆炸 剧终幕后特辑：TMDB S0 只有 3 条且都不是它 -> 收进 .Extras
BBT_GLOB = "剧终幕后特辑.The.Big.Bang.Theory.S12E25.Extras*"

# 17.3 关于性：目录名以数字开头 + 文件只有 EPxx，导致 9 个文件全叠在 S1E17
SEX_DIR_OLD = os.path.join(TV, "JP/17.3 关于性 17.3 about a sex （全9集）2020")
SEX_DIR_NEW = os.path.join(TV, "JP/关于性.17.3.about.a.Sex.S01.2020.1080p.WEB-DL")

# 兄弟连：花絮子目录被 Plex 当成了第 3 季，改名成显式季目录
BOB_DIR_OLD = os.path.join(
    TV, "US/兄弟连.Band.of.Brothers.E01-E10.2001.1080p.BluRay.x265.10bit.AC3￡cXcY@FRDS",
    "兄弟连(花絮).Band.of.Brothers.2001.1080p.BluRay.x265.10bit.AC3￡cXcY@FRDS")
BOB_DIR_NEW = os.path.join(
    TV, "US/兄弟连.Band.of.Brothers.E01-E10.2001.1080p.BluRay.x265.10bit.AC3￡cXcY@FRDS",
    "Season 00")

# 白色巨塔：21 个 .ts + 21 个 .srt，文件名字节不是合法 UTF-8，Plex 把 2003 当集号
SHIROI_DIR = os.path.join(TV, "JP/白色巨塔")

plan, errors = [], []

# ---------- 1) 常规 SxxEyy 替换 ----------
for label, root, glob, rx, rep in SUBS:
    hits = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith((".", "@"))]
        for f in fn:
            if fnmatch.fnmatch(f, glob):
                new = re.sub(rx, rep, f, count=1)
                if new != f:
                    hits.append((os.path.join(dp, f), os.path.join(dp, new)))
    if not hits:
        errors.append("%s: 没找到匹配 %s 的文件" % (label, glob))
        continue
    for s, d in hits:
        if os.path.exists(d):
            errors.append("%s: 目标已存在 %s" % (label, os.path.basename(d)))
        else:
            plan.append(dict(label=label, src=s, dst=d))

# ---------- 2) 命运石之门 ----------
if os.path.isfile(STEINS_SRC):
    if os.path.exists(STEINS_DST):
        errors.append("命运石之门: 目标已存在")
    else:
        plan.append(dict(label="命运石之门 S01E25->S00E01", src=STEINS_SRC, dst=STEINS_DST))
else:
    errors.append("命运石之门: 找不到 %s" % STEINS_SRC)

# ---------- 3) 生活大爆炸 -> .Extras ----------
for dp, dn, fn in os.walk(TV):
    dn[:] = [d for d in dn if not d.startswith((".", "@"))]
    for f in fn:
        if fnmatch.fnmatch(f, BBT_GLOB):
            s = os.path.join(dp, f)
            plan.append(dict(label="生活大爆炸 幕后特辑->.Extras", src=s,
                             dst=os.path.join(dp, ".Extras", f)))

# ---------- 4) 17.3 关于性 ----------
if os.path.isdir(SEX_DIR_OLD):
    files = sorted(f for f in os.listdir(SEX_DIR_OLD) if f.lower().endswith((".mp4", ".mkv")))
    if len(files) != 9:
        errors.append("17.3 关于性: 预期 9 个视频，实际 %d" % len(files))
    for f in files:
        m = re.search(r"EP(\d{1,3})", f)
        if not m:
            errors.append("17.3 关于性: %s 里没有 EPxx" % f)
            continue
        ext = os.path.splitext(f)[1]
        plan.append(dict(label="17.3 关于性", src=os.path.join(SEX_DIR_OLD, f),
                         dst=os.path.join(SEX_DIR_NEW,
                                          "关于性.17.3.about.a.Sex.S01E%s%s"
                                          % (m.group(1).zfill(2), ext))))
    plan.append(dict(label="17.3 关于性 目录", src=SEX_DIR_OLD, dst=SEX_DIR_NEW, dironly=True))
else:
    errors.append("17.3 关于性: 目录不存在 %s" % SEX_DIR_OLD)

# ---------- 5) 白色巨塔 ----------
if os.path.isdir(SHIROI_DIR):
    names = os.listdir(SHIROI_DIR)
    pairs = {}
    for f in names:
        m = re.search(r"第(\d{1,2})", f)
        if not m:
            continue
        n = int(m.group(1))
        ext = os.path.splitext(f)[1].lower()
        if ext not in (".ts", ".srt"):
            continue
        pairs.setdefault(n, {})[ext] = f
    if len(pairs) != 21:
        errors.append("白色巨塔: 预期 21 集，实际解析出 %d" % len(pairs))
    for n in sorted(pairs):
        base = "白い巨塔.2003.S01E%02d.HDTV-TS" % n
        for ext, old in pairs[n].items():
            plan.append(dict(label="白色巨塔", src=os.path.join(SHIROI_DIR, old),
                             dst=os.path.join(SHIROI_DIR, base + ext)))
else:
    errors.append("白色巨塔: 目录不存在 %s" % SHIROI_DIR)

# ---------- 6) 兄弟连 花絮目录 ----------
if os.path.isdir(BOB_DIR_OLD):
    if os.path.exists(BOB_DIR_NEW):
        errors.append("兄弟连: 目标目录已存在 %s" % BOB_DIR_NEW)
    else:
        plan.append(dict(label="兄弟连 花絮目录->Season 00", src=BOB_DIR_OLD,
                         dst=BOB_DIR_NEW, dirmv=True))
else:
    errors.append("兄弟连: 花絮目录不存在")

# ---------- report ----------
print("=== 计划 (%s) ===" % ("APPLY" if APPLY else "DRY-RUN"))
for e in errors:
    print("  !! " + e)
bylabel = {}
for p in plan:
    bylabel.setdefault(p["label"], []).append(p)
for label in sorted(bylabel):
    ps = bylabel[label]
    print("\n-- %s (%d 项)" % (label, len(ps)))
    for p in ps[:3]:
        print("   %s" % os.path.basename(p["src"])[:96])
        print("   -> %s" % os.path.relpath(p["dst"], os.path.dirname(TV or AN or "/"))[:110])
    if len(ps) > 3:
        print("   ... 共 %d 项" % len(ps))
print("\n合计 %d 项，前置错误 %d 项" % (len(plan), len(errors)))
if errors or not APPLY:
    sys.exit(1 if errors else 0)

# ---------- apply: files first, then directories ----------
os.makedirs(os.path.dirname(MAP), exist_ok=True)
done = []
files = [p for p in plan if not p.get("dironly")]
dirs = [p for p in plan if p.get("dironly")]
for p in files:
    if os.path.exists(p["dst"]):
        print("SKIP (exists): %s" % p["dst"]); continue
    os.makedirs(os.path.dirname(p["dst"]), exist_ok=True)
    os.rename(p["src"], p["dst"])
    done.append(dict(label=p["label"], src=p["src"], dst=p["dst"]))
# 17.3: the source dir is now empty except non-video files; move the rest over
for p in dirs:
    if not os.path.isdir(p["src"]):
        continue
    os.makedirs(p["dst"], exist_ok=True)
    for f in os.listdir(p["src"]):
        s, d = os.path.join(p["src"], f), os.path.join(p["dst"], f)
        if not os.path.exists(d):
            os.rename(s, d)
            done.append(dict(label=p["label"] + " 附带文件", src=s, dst=d))
    try:
        os.rmdir(p["src"])
        done.append(dict(label=p["label"], src=p["src"], dst=p["dst"], kind="rmdir"))
    except OSError as e:
        print("  保留空目录 %s (%s)" % (p["src"], e))
json.dump(done, open(MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n已执行 %d 项，回退映射 -> %s" % (len(done), MAP))
