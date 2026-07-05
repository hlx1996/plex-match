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
from functools import lru_cache

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
    eng = set(w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9']*", text) if len(w) > 1)
    chn = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return eng, chn


STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "been",
    "was", "were", "are", "not", "but", "all", "can", "had", "her", "his",
    "one", "our", "out", "you", "she", "will", "has", "its", "than", "of",
    "to", "in", "on", "by", "at",
    "bluray", "remux", "web", "1080p", "2160p", "720p", "x264", "x265",
    "10bit", "dd5", "aac", "dts", "atmos", "truehd", "multi", "audio",
    "sub", "eng", "chs", "cht", "mkv", "mp4", "avi", "bd", "dvd",
    "proper", "limited", "theatrical", "unrated", "extended", "edition",
    "collectors", "collector", "hybrid", "repack", "imax", "criterion",
    "complete", "uncut", "remastered", "restored", "dubbed", "dub",
    "dual", "audio", "audios", "atmos", "hdr", "dv", "nf", "webdl",
    "web", "dl", "blu", "ray", "mnhd", "frds", "leagueweb", "bthd",
    "hdbthd", "btshd", "minepad", "minisd", "taikatalvi", "ipad",
}


def normalize_source_name(source):
    def keep_cjk_bracket(match):
        inner = match.group(1).strip()
        if re.search(r"[\u4e00-\u9fff]", inner):
            return " {} ".format(inner)
        return " "

    cleaned = re.sub(r"\[([^\]]+)\]", keep_cjk_bracket, source)
    cleaned = re.sub(r"\(([^)]+)\)", " ", cleaned)
    cleaned = cleaned.replace(".", " ").replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"^\d+\s+", "", cleaned)
    cleaned = re.sub(r"\b(19|20)\d{2}\b.*$", "", cleaned)
    cleaned = re.sub(r"\b(S\d+|Season\s*\d+|SP\d*)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or source


def basename(path):
    if not path:
        return ""
    return path.rstrip("/").split("/")[-1]


def token_sets(text):
    eng, chn = extract_names(text)
    return eng - STOP_WORDS, chn


def token_similarity(source_text, candidate_text):
    src_eng_clean, src_chn = token_sets(source_text)
    cand_eng_clean, cand_chn = token_sets(candidate_text)

    if not src_eng_clean and not src_chn:
        return 1.0

    eng_union = src_eng_clean | cand_eng_clean
    chn_union = src_chn | cand_chn
    eng_score = len(src_eng_clean & cand_eng_clean) / max(len(eng_union), 1) if src_eng_clean else 0
    chn_score = len(src_chn & cand_chn) / max(len(chn_union), 1) if src_chn else 0

    return max(eng_score, chn_score)


def candidate_similarity(src, candidate_text):
    return token_similarity(normalize_source_name(src), candidate_text)


def name_similarity(src, plex_title, orig_title="", slug=""):
    source_text = normalize_source_name(src)
    candidates = [plex_title, orig_title]
    if slug:
        candidates.append(slug.replace("-", " "))
    return max(token_similarity(source_text, candidate) for candidate in candidates if candidate)


def franchise_subset_risk(src, title, orig_title="", slug=""):
    source_text = normalize_source_name(src)
    src_eng, src_chn = token_sets(source_text)
    if src_chn or not src_eng:
        return False

    for candidate in filter(None, [orig_title, slug.replace("-", " ") if slug else ""]):
        cand_eng, cand_chn = token_sets(candidate)
        if cand_chn or not cand_eng:
            continue
        if src_eng < cand_eng and len(cand_eng - src_eng) >= 2 and candidate_similarity(src, title) == 0:
            return True
    return False


def weak_overlap_risk(src, title, orig_title="", slug=""):
    if candidate_similarity(src, title) > 0:
        return False

    source_text = normalize_source_name(src)
    src_eng, src_chn = token_sets(source_text)
    if src_chn or len(src_eng) < 2:
        return False

    for candidate in filter(None, [orig_title, slug.replace("-", " ") if slug else ""]):
        cand_eng, cand_chn = token_sets(candidate)
        if cand_chn or not cand_eng:
            continue
        if len(src_eng & cand_eng) == 1:
            return True
    return False


def generic_english_alias_risk(src, title, orig_title=""):
    source_text = normalize_source_name(src)
    title_text = normalize_source_name(title)
    src_eng, src_chn = token_sets(source_text)
    title_eng, title_chn = token_sets(title_text)
    if src_chn or title_chn or not src_eng or src_eng != title_eng:
        return False
    if not (1 <= len(src_eng) <= 3):
        return False

    orig_eng, orig_chn = token_sets(orig_title)
    if orig_chn:
        return True
    if orig_eng and not (src_eng & orig_eng):
        return True
    return False


@lru_cache(maxsize=2048)
def get_metadata_paths(base, token, rating_key):
    data = api_get(base, token, f"/library/metadata/{rating_key}")
    root = ET.fromstring(data)
    locations = [loc.get("path", "") for loc in root.findall(".//Location") if loc.get("path")]
    files = [part.get("file", "") for part in root.findall(".//Part") if part.get("file")]
    return locations, files


def source_candidates_for_item(base, token, rating_key, section_type, filepath):
    candidates = []
    if filepath:
        filename = extract_filename(filepath)
        folder = extract_folder_name(filepath, section_type)
        if filename:
            candidates.append(filename)
        if folder:
            candidates.append(folder)

    locations, files = get_metadata_paths(base, token, rating_key)
    if section_type == "show":
        for path in locations:
            name = basename(path)
            if name:
                candidates.append(name)
    else:
        for path in files:
            filename = extract_filename(path)
            folder = extract_folder_name(path, section_type)
            if filename:
                candidates.append(filename)
            if folder:
                candidates.append(folder)

    deduped = []
    seen = set()
    for candidate in candidates:
        key = candidate.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


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
    parser.add_argument("--result-file", default="/tmp/plex_verify_result.json", help="Where to save the JSON result")
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
    library_summary = []

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
            slug = item.get("slug", "")
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
            source_candidates = source_candidates_for_item(base, args.token, rk, lib["type"], filepath)
            if not source_candidates:
                continue

            non_collection = [src for src in source_candidates if not (lib["type"] == "movie" and is_collection_folder(src))]
            source_candidates = non_collection if non_collection else source_candidates
            source = max(
                source_candidates,
                key=lambda src: name_similarity(src, title, orig, slug),
            )

            if is_collection_folder(source) and lib["type"] == "movie":
                continue

            total_checked += 1
            normalized_source = normalize_source_name(source)
            sim = name_similarity(source, title, orig, slug)
            src_years = re.findall(r"[\(\[]?((?:19|20)\d{2})[\]\)]?", source)
            year_mismatch = bool(
                year and src_years and not any(abs(int(y) - int(year)) <= 1 for y in src_years)
            )
            subset_risk = franchise_subset_risk(source, title, orig, slug) and year_mismatch
            weak_risk = weak_overlap_risk(source, title, orig, slug)
            alias_risk = generic_english_alias_risk(source, title, orig)

            if sim < args.threshold or subset_risk or weak_risk or alias_risk:
                reasons = []
                if subset_risk:
                    reasons.append("franchise subset mismatch")
                if weak_risk:
                    reasons.append("weak single-token overlap")
                if alias_risk:
                    reasons.append("ambiguous short English alias")
                if not reasons and sim == 0:
                    reasons.append("completely different")
                elif not reasons:
                    reasons.append(f"low similarity ({sim:.2f})")
                if year:
                    if year_mismatch:
                        reasons.append(f"year ({src_years} vs {year})")

                suspicious.append({
                    "lib": lib["title"], "key": rk,
                    "source": source, "plex_title": title,
                    "source_candidates": source_candidates,
                    "normalized_source": normalized_source,
                    "originalTitle": orig, "slug": slug, "year": year,
                    "similarity": sim, "reasons": reasons,
                })
                print(f"  SUSPECT: {source[:60]} -> {title} ({year})", flush=True)

            time.sleep(args.delay)

        print(f"  {lib_count} items, {lib_unmatched} unmatched", flush=True)
        library_summary.append({
            "key": lib["key"],
            "title": lib["title"],
            "type": lib["type"],
            "items": lib_count,
            "unmatched": lib_unmatched,
        })

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

            clean = normalize_source_name(source)

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
                    new_sim = name_similarity(source, match["name"], "", match["name"])
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
        "fixed": fixed, "updated": fixed, "updated_count": len(fixed),
        "unfixable": unfixable,
        "library_summary": library_summary,
        "unmatched_remaining": unmatched_remaining,
    }
    with open(args.result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nFull results saved to {args.result_file}", flush=True)


if __name__ == "__main__":
    main()
