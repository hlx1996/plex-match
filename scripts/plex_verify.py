#!/usr/bin/env python3
"""Verify all Plex media matches by comparing file names to Plex titles."""
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import argparse
import time
import re
import sys
import ssl
import json
import os

ctx = ssl.create_default_context()


def api(base, token, method, path, retries=3):
    url = f"{base}{path}{'&' if '?' in path else '?'}X-Plex-Token={token}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                data = resp.read().decode("utf-8")
                return resp.status, data
        except urllib.error.HTTPError as e:
            if e.code in (503, 429) and attempt < retries - 1:
                time.sleep((attempt + 1) * 5)
            else:
                raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 5)
            else:
                raise


def api_get(base, token, path):
    _, data = api(base, token, "GET", path)
    return data


def api_put(base, token, path):
    status, _ = api(base, token, "PUT", path)
    return status


def extract_filename(filepath):
    if not filepath:
        return ""
    name = filepath.split("/")[-1]
    name = re.sub(r"\.[a-zA-Z0-9]+$", "", name)
    return name


def extract_folder_name(filepath, section_type):
    if not filepath:
        return ""
    parts = filepath.split("/")
    if section_type == "movie" and len(parts) >= 2:
        return parts[-2]
    return ""


def extract_names(text):
    eng = set(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z']+", text) if len(w) > 2)
    chn = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return eng, chn


STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "been",
    "was", "were", "are", "not", "but", "all", "can", "had", "her", "his",
    "one", "our", "out", "you", "she", "will", "has", "its", "than",
    "bluray", "remux", "web", "1080p", "2160p", "720p", "x264", "x265",
    "10bit", "dd5", "aac", "dts", "atmos", "truehd", "multi", "audio",
    "sub", "eng", "chs", "cht", "mkv", "mp4", "avi", "bd", "dvd",
    "proper", "limited", "theatrical", "unrated",
}


def name_similarity(src, plex_title, orig_title=""):
    src_eng, src_chn = extract_names(src)
    plex_eng, plex_chn = extract_names(plex_title)
    orig_eng, orig_chn = extract_names(orig_title) if orig_title else (set(), set())

    src_eng_clean = src_eng - STOP_WORDS
    plex_eng_clean = plex_eng - STOP_WORDS

    common_eng = (src_eng_clean & plex_eng_clean) | (src_eng_clean & orig_eng)
    common_chn = (src_chn & plex_chn) | (src_chn & orig_chn)

    if not src_eng_clean and not src_chn:
        return 1.0

    eng_score = len(common_eng) / max(len(src_eng_clean), 1) if src_eng_clean else 0
    chn_score = len(common_chn) / max(len(src_chn), 1) if src_chn else 0

    return max(eng_score, chn_score)


def is_collection_folder(name):
    patterns = [
        r"合集", r"系列", r"collection", r"complete", r"全集",
        r"\d+部", r"all\s*seasons", r"全\d+季",
    ]
    return any(re.search(p, name, re.IGNORECASE) for p in patterns)


def is_documentary(name):
    kw = [
        "making of", "behind the scenes", "symphony", "composer",
        "journey through", "a primer for", "the story of",
        "documentary", "纪录片", "幕后",
    ]
    return any(k in name.lower() for k in kw)


def search_match(base, token, rating_key, title, year, agent):
    params = urllib.parse.urlencode({
        "manual": "1", "title": title, "year": year or "",
        "agent": agent, "language": "zh-CN",
    })
    path = f"/library/metadata/{rating_key}/matches?{params}"
    try:
        data = api_get(base, token, path)
        root = ET.fromstring(data)
        results = root.findall(".//SearchResult")
        non_doc = [r for r in results if not is_documentary(r.get("name", ""))]
        pool = non_doc if non_doc else results

        if year:
            for r in pool:
                if r.get("year") == year:
                    return {"guid": r.get("guid"), "name": r.get("name"), "year": r.get("year")}
            for r in pool:
                ry = r.get("year", "")
                if ry and abs(int(ry) - int(year)) <= 2:
                    return {"guid": r.get("guid"), "name": r.get("name"), "year": ry}
        if pool:
            r = pool[0]
            return {"guid": r.get("guid"), "name": r.get("name"), "year": r.get("year")}
        return None
    except Exception:
        return None


def apply_match(base, token, rating_key, guid, name, year):
    params = urllib.parse.urlencode({"guid": guid, "name": name or "", "year": year or ""})
    path = f"/library/metadata/{rating_key}/match?{params}"
    try:
        return api_put(base, token, path) == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify Plex media matches")
    parser.add_argument("--base", required=True, help="Plex server URL")
    parser.add_argument("--token", required=True, help="Plex X-Plex-Token")
    parser.add_argument("--fix", action="store_true", help="Auto-fix clear errors")
    parser.add_argument("--library", type=str, default=None, help="Only check specific library")
    parser.add_argument("--threshold", type=float, default=0.15,
                        help="Similarity threshold below which a match is suspicious (default: 0.15)")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between API calls")
    args = parser.parse_args()

    base = args.base.rstrip("/")

    print("Fetching libraries...", flush=True)
    data = api_get(base, args.token, "/library/sections")
    root = ET.fromstring(data)
    libs = []
    for d in root.findall(".//Directory"):
        libs.append({"key": d.get("key"), "title": d.get("title"), "type": d.get("type")})

    if args.library:
        libs = [l for l in libs if l["key"] == args.library]

    total_items = 0
    total_unmatched = 0
    total_checked = 0
    suspicious = []
    unmatched_remaining = []

    for lib in libs:
        print(f"\n{'='*50}", flush=True)
        print(f"Checking: {lib['title']} ({lib['type']})", flush=True)
        print(f"{'='*50}", flush=True)

        data = api_get(base, args.token, f"/library/sections/{lib['key']}/all")
        root = ET.fromstring(data)
        items = root.findall(".//Video") if lib["type"] == "movie" else root.findall(".//Directory")

        lib_count = 0
        lib_unmatched = 0

        for item in items:
            lib_count += 1
            total_items += 1
            title = item.get("title", "")
            year = item.get("year", "")
            orig = item.get("originalTitle", "")
            guid = item.get("guid", "")
            rk = item.get("ratingKey", "")

            if "local://" in guid or not guid:
                lib_unmatched += 1
                total_unmatched += 1
                filepath = ""
                media = item.find(".//Media/Part")
                if media is not None:
                    filepath = media.get("file", "")
                unmatched_remaining.append({
                    "lib": lib["title"], "key": rk,
                    "title": title, "year": year, "file": filepath,
                })
                continue

            media = item.find(".//Media/Part")
            filepath = media.get("file", "") if media is not None else ""
            filename = extract_filename(filepath)
            folder = extract_folder_name(filepath, lib["type"])

            source = filename if filename else folder
            if not source:
                continue

            if is_collection_folder(source) and lib["type"] == "movie":
                continue

            total_checked += 1
            sim = name_similarity(source, title, orig)

            if sim < args.threshold:
                reasons = []
                if sim == 0:
                    reasons.append("completely different")
                else:
                    reasons.append(f"low similarity ({sim:.2f})")
                if year:
                    src_years = re.findall(r"[\(\[]?((?:19|20)\d{2})[\]\)]?", source)
                    if src_years and not any(abs(int(y) - int(year)) <= 1 for y in src_years):
                        reasons.append(f"year ({src_years} vs {year})")

                suspicious.append({
                    "lib": lib["title"], "key": rk,
                    "source": source, "plex_title": title,
                    "originalTitle": orig, "year": year,
                    "similarity": sim, "reasons": reasons,
                })
                print(f"  SUSPECT: {source[:60]} -> {title} ({year})", flush=True)

            time.sleep(args.delay)

        print(f"  {lib_count} items, {lib_unmatched} unmatched", flush=True)

    fixed = []
    unfixable = []

    if args.fix and suspicious:
        print(f"\n{'='*50}", flush=True)
        print(f"Attempting to fix {len(suspicious)} suspicious items...", flush=True)
        print(f"{'='*50}", flush=True)

        for s in suspicious:
            source = s["source"]
            agent = "tv.plex.agents.movie" if s["lib"] != "动漫" and s["lib"] != "电视剧" and s["lib"] != "纪录片" and s["lib"] != "综艺" else "tv.plex.agents.series"

            if s["lib"] in ("动漫", "电视剧", "纪录片", "综艺"):
                agent = "tv.plex.agents.series"
            else:
                agent = "tv.plex.agents.movie"

            clean = re.sub(r"[\(\[].*?[\)\]]", "", source).strip()
            clean = re.sub(r"\.(BluRay|WEB|BD|DVD|1080p|2160p|720p|x264|x265|DD5|DTS|AAC|Remux|Complete|NF).*", "", clean, flags=re.IGNORECASE).strip()
            clean = re.sub(r"\d+\.", "", clean, count=1).strip()
            clean = re.sub(r"(S\d+|Season\s*\d+|SP\d*)", "", clean, flags=re.IGNORECASE).strip()

            chn_parts = re.findall(r"[\u4e00-\u9fff]+", clean)
            chn_title = "".join(chn_parts) if chn_parts else ""

            eng_match = re.search(r"[A-Za-z][A-Za-z\s'\.\:\-]+[A-Za-z]", clean)
            eng_title = eng_match.group().strip() if eng_match else ""

            search_titles = []
            if chn_title and len(chn_title) >= 2:
                search_titles.append(chn_title)
            if eng_title and len(eng_title) > 2:
                search_titles.append(eng_title)
            if not search_titles:
                search_titles.append(clean)

            print(f"\n  [{s['lib']}] {source[:60]}", flush=True)
            print(f"  Current: {s['plex_title']} ({s['year']})", flush=True)

            match = None
            for st in search_titles:
                print(f"  Searching: '{st}'...", flush=True)
                match = search_match(base, args.token, s["key"], st, s["year"], agent)
                if match and match["name"] != s["plex_title"]:
                    new_sim = name_similarity(source, match["name"], "")
                    if new_sim > s["similarity"]:
                        break
                match = None
            if match:
                print(f"  Found: {match['name']} ({match['year']})", flush=True)
                if apply_match(base, args.token, s["key"], match["guid"], match["name"], match["year"]):
                    print(f"  -> FIXED", flush=True)
                    fixed.append({**s, "new_title": match["name"], "new_year": match["year"]})
                else:
                    print(f"  -> FIX FAILED", flush=True)
                    unfixable.append(s)
            else:
                print(f"  -> No better match found", flush=True)
                unfixable.append(s)

            time.sleep(args.delay)

    print(f"\n{'='*50}", flush=True)
    print(f"VERIFICATION SUMMARY", flush=True)
    print(f"{'='*50}", flush=True)
    print(f"Total items: {total_items}", flush=True)
    print(f"Checked: {total_checked}", flush=True)
    print(f"Suspicious: {len(suspicious)}", flush=True)
    if args.fix:
        print(f"Auto-fixed: {len(fixed)}", flush=True)
        print(f"Unfixable: {len(unfixable)}", flush=True)
    print(f"Still unmatched: {len(unmatched_remaining)}", flush=True)

    if unmatched_remaining:
        print(f"\nUnmatched items (need manual attention):", flush=True)
        for u in unmatched_remaining[:20]:
            fname = extract_filename(u["file"]) if u["file"] else u["title"]
            print(f"  [{u['lib']}] {fname[:60]} ({u['year']})", flush=True)
        if len(unmatched_remaining) > 20:
            print(f"  ... and {len(unmatched_remaining) - 20} more", flush=True)

    if unfixable:
        print(f"\nItems needing manual review:", flush=True)
        for u in unfixable:
            print(f"  [{u['lib']}] {u['source'][:50]} -> {u['plex_title']} ({u['year']})", flush=True)
            print(f"    Reasons: {', '.join(u['reasons'])}", flush=True)

    result = {
        "total": total_items, "checked": total_checked,
        "suspicious_count": len(suspicious), "suspicious": suspicious,
        "fixed": fixed, "unfixable": unfixable,
        "unmatched_remaining": unmatched_remaining,
    }
    with open("/tmp/plex_verify_result.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nFull results saved to /tmp/plex_verify_result.json", flush=True)


if __name__ == "__main__":
    main()
