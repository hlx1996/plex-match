#!/usr/bin/env python3
"""Batch match all unmatched items across all Plex media libraries."""
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import argparse
import time
import re
import sys
import ssl

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
            if e.code == 503 and attempt < retries - 1:
                wait = (attempt + 1) * 5
                print(f"  Server busy (503), retry in {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 5
                print(f"  Retry in {wait}s: {e}", flush=True)
                time.sleep(wait)
            else:
                raise


def api_get(base, token, path):
    _, data = api(base, token, "GET", path)
    return data


def api_put(base, token, path):
    status, _ = api(base, token, "PUT", path)
    return status


def get_libraries(base, token):
    data = api_get(base, token, "/library/sections")
    root = ET.fromstring(data)
    libs = []
    for d in root.findall(".//Directory"):
        libs.append({
            "key": d.get("key"),
            "title": d.get("title"),
            "type": d.get("type"),
            "agent": d.get("agent", ""),
        })
    return libs


def get_unmatched(base, token, section_key, section_type):
    data = api_get(base, token, f"/library/sections/{section_key}/all")
    root = ET.fromstring(data)
    unmatched = []
    if section_type == "movie":
        items = root.findall(".//Video")
    elif section_type == "show":
        items = root.findall(".//Directory")
    else:
        items = root.findall(".//Video") + root.findall(".//Directory")
    for item in items:
        guid = item.get("guid", "")
        if "local://" in guid or not guid:
            unmatched.append({
                "ratingKey": item.get("ratingKey"),
                "title": item.get("title"),
                "year": item.get("year", ""),
                "type": item.get("type", section_type.rstrip("s")),
            })
    return unmatched


def clean_title(title):
    title = re.sub(r"^(Top)?\d+\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^SP\s+", "", title)
    title = re.sub(r"^3D\s*", "", title)
    title = re.sub(r"导演剪辑(加长)?版", "", title)
    title = re.sub(r"加长版", "", title)
    title = re.sub(r"CC标准收藏版", "", title)
    title = re.sub(r"\d+周年纪念版", "", title)
    title = re.sub(r"意大利加长版", "", title)
    title = re.sub(r"IMAX(全屏版)?", "", title)
    title = re.sub(r"Open Matte", "", title, flags=re.IGNORECASE)
    title = re.sub(r"Remastered Edition", "", title, flags=re.IGNORECASE)
    title = re.sub(r"Collector's Edition", "", title, flags=re.IGNORECASE)
    title = re.sub(r"Director'?s? Cut", "", title, flags=re.IGNORECASE)
    title = re.sub(r"国台英\d语", "", title)
    title = re.sub(r"出屏特效国配字幕", "", title)
    title = re.sub(r"Trilogy", "", title, flags=re.IGNORECASE)
    eng_match = re.search(r"[A-Za-z][A-Za-z\s'\.\:\-\,\!\?0-9]+[A-Za-z0-9]", title)
    if eng_match:
        eng = eng_match.group().strip()
        eng = re.sub(r"^(Aka|AKA)\s+", "", eng)
        if len(eng) > 2:
            return eng
    chn = re.sub(r"[A-Za-z\s\'\.\:\-]+", " ", title).strip()
    chn = re.sub(r"\s+", " ", chn)
    return chn if chn else title.strip()


def agent_for_type(section_type):
    if section_type == "movie":
        return "tv.plex.agents.movie"
    return "tv.plex.agents.series"


def is_documentary(name):
    kw = [
        "making of", "behind the scenes", "symphony", "composer",
        "journey through", "a primer for", "the story of",
        "documentary", "纪录片", "幕后",
    ]
    return any(k in name.lower() for k in kw)


def find_match(base, token, rating_key, title, year, agent):
    clean = clean_title(title)
    params = urllib.parse.urlencode({
        "manual": "1",
        "title": clean,
        "year": year or "",
        "agent": agent,
        "language": "zh-CN",
    })
    path = f"/library/metadata/{rating_key}/matches?{params}"
    try:
        data = api_get(base, token, path)
        root = ET.fromstring(data)
        results = root.findall(".//SearchResult")
        if not results:
            return None

        candidates = []
        for r in results:
            rname = r.get("name", "")
            ryear = r.get("year", "")
            rguid = r.get("guid", "")
            candidates.append({
                "guid": rguid, "name": rname, "year": ryear,
            })

        non_doc = [c for c in candidates if not is_documentary(c["name"])]
        pool = non_doc if non_doc else candidates

        if year:
            same_year = [c for c in pool if c["year"] == year]
            if same_year:
                return same_year[0]
            close_year = [c for c in pool if c["year"] and abs(int(c["year"]) - int(year)) <= 2]
            if close_year:
                return close_year[0]

        return pool[0]
    except Exception as e:
        print(f"  ERROR searching: {e}", flush=True)
        return None


def apply_match(base, token, rating_key, guid, name, year):
    params = urllib.parse.urlencode({
        "guid": guid,
        "name": name or "",
        "year": year or "",
    })
    path = f"/library/metadata/{rating_key}/match?{params}"
    try:
        status = api_put(base, token, path)
        return status == 200
    except Exception as e:
        print(f"  ERROR applying: {e}", flush=True)
        return False


def main():
    parser = argparse.ArgumentParser(description="Batch match unmatched Plex media")
    parser.add_argument("--base", required=True, help="Plex server URL (no trailing slash)")
    parser.add_argument("--token", required=True, help="Plex X-Plex-Token")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests (seconds)")
    parser.add_argument("--library", type=str, default=None, help="Only process specific library by key (e.g. 1)")
    parser.add_argument("--dry-run", action="store_true", help="List unmatched without matching")
    args = parser.parse_args()

    base = args.base.rstrip("/")

    print("Connecting to Plex server...", flush=True)
    try:
        libs = get_libraries(base, args.token)
    except Exception as e:
        print(f"ERROR: Cannot connect to Plex: {e}", flush=True)
        sys.exit(1)

    print(f"Found {len(libs)} libraries:\n", flush=True)
    for lib in libs:
        print(f"  [{lib['key']}] {lib['title']} ({lib['type']})", flush=True)
    print(flush=True)

    if args.library:
        libs = [l for l in libs if l["key"] == args.library]
        if not libs:
            print(f"ERROR: Library '{args.library}' not found", flush=True)
            sys.exit(1)

    total_success = 0
    total_failed = 0
    total_unmatched = 0
    all_errors = []

    for lib in libs:
        print(f"{'='*50}", flush=True)
        print(f"Library: {lib['title']} ({lib['type']})", flush=True)
        print(f"{'='*50}", flush=True)

        unmatched = get_unmatched(base, args.token, lib["key"], lib["type"])
        total_unmatched += len(unmatched)
        print(f"Unmatched: {len(unmatched)}\n", flush=True)

        if not unmatched:
            print("All matched!\n", flush=True)
            continue

        if args.dry_run:
            for item in unmatched:
                print(f"  [{item['ratingKey']}] {item['title']} ({item['year']})", flush=True)
            print()
            continue

        agent = agent_for_type(lib["type"])
        success = 0
        failed = 0

        for i, item in enumerate(unmatched):
            rk = item["ratingKey"]
            title = item["title"]
            year = item["year"]

            print(f"[{i+1}/{len(unmatched)}] {title} ({year})", flush=True)

            match = find_match(base, args.token, rk, title, year, agent)
            if not match:
                print(f"  -> No match found", flush=True)
                failed += 1
                all_errors.append(f"{lib['title']}: {title} ({year})")
                time.sleep(0.5)
                continue

            if apply_match(base, args.token, rk, match["guid"], match["name"], match["year"]):
                print(f"  -> OK: {match['name']} ({match['year']})", flush=True)
                success += 1
            else:
                print(f"  -> FAILED to apply", flush=True)
                failed += 1
                all_errors.append(f"{lib['title']}: {title} ({year})")

            time.sleep(args.delay)

        total_success += success
        total_failed += failed
        print(f"\n  Library done: {success} matched, {failed} failed\n", flush=True)

    print(f"{'='*50}", flush=True)
    print(f"TOTAL: {total_unmatched} unmatched across all libraries", flush=True)
    if args.dry_run:
        print("(dry-run, no matches applied)", flush=True)
    else:
        print(f"Matched: {total_success}", flush=True)
        print(f"Failed:  {total_failed}", flush=True)
    if all_errors:
        print(f"\nFailed items:", flush=True)
        for e in all_errors:
            print(f"  - {e}", flush=True)
    print(flush=True)


if __name__ == "__main__":
    main()
