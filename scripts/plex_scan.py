#!/usr/bin/env python3
"""Trigger and wait for Plex library scans."""
import argparse
import json
import ssl
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

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
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep((attempt + 1) * 5)
            else:
                raise
        except Exception:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 5)
            else:
                raise


def api_get(base, token, path):
    _, data = api(base, token, "GET", path)
    return data


def get_libraries(base, token):
    root = ET.fromstring(api_get(base, token, "/library/sections"))
    libs = []
    for item in root.findall(".//Directory"):
        libs.append({
            "key": item.get("key"),
            "title": item.get("title"),
            "type": item.get("type"),
            "refreshing": item.get("refreshing", "0"),
            "scannedAt": item.get("scannedAt", ""),
            "contentChangedAt": item.get("contentChangedAt", ""),
        })
    return libs


def parse_selected(keys):
    selected = set()
    for value in keys:
        for part in value.split(","):
            part = part.strip()
            if part:
                selected.add(part)
    return selected


def main():
    parser = argparse.ArgumentParser(description="Trigger Plex library scans and wait for completion")
    parser.add_argument("--base", required=True, help="Plex server URL (no trailing slash)")
    parser.add_argument("--token", required=True, help="Plex X-Plex-Token")
    parser.add_argument(
        "--library",
        action="append",
        default=[],
        help="Only process specific library key(s); repeat or use comma-separated values",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Polling interval in seconds")
    parser.add_argument("--timeout", type=int, default=7200, help="Max wait time in seconds")
    parser.add_argument("--result-file", default="/tmp/plex_scan_result.json", help="Where to save the JSON result")
    args = parser.parse_args()

    base = args.base.rstrip("/")

    print("Fetching libraries...", flush=True)
    try:
        libs = get_libraries(base, args.token)
    except Exception as e:
        print(f"ERROR: Cannot connect to Plex: {e}", flush=True)
        sys.exit(1)

    selected_keys = parse_selected(args.library)
    if selected_keys:
        libs = [lib for lib in libs if lib["key"] in selected_keys]
        missing = sorted(selected_keys - {lib["key"] for lib in libs})
        if missing:
            print(f"ERROR: Library not found: {', '.join(missing)}", flush=True)
            sys.exit(1)

    if not libs:
        print("ERROR: No libraries selected", flush=True)
        sys.exit(1)

    print(f"Queuing scan for {len(libs)} libraries:\n", flush=True)
    initial_state = {}
    for lib in libs:
        initial_state[lib["key"]] = {
            "scannedAt": lib["scannedAt"],
            "contentChangedAt": lib["contentChangedAt"],
        }
        print(f"  [{lib['key']}] {lib['title']} ({lib['type']})", flush=True)
        api_get(base, args.token, f"/library/sections/{lib['key']}/refresh")

    print("\nWaiting for scans to complete...", flush=True)
    start = time.time()
    poll_count = 0
    final_map = {}

    while True:
        poll_count += 1
        current = {lib["key"]: lib for lib in get_libraries(base, args.token)}
        pending = []
        finished = []

        for lib in libs:
            live = current[lib["key"]]
            final_map[lib["key"]] = live
            if live["refreshing"] == "1":
                pending.append(live["title"])
            else:
                finished.append(live["title"])

        elapsed = int(time.time() - start)
        if pending:
            print(
                f"  {elapsed}s elapsed | scanning: {', '.join(pending)} | finished: {len(finished)}/{len(libs)}",
                flush=True,
            )
        elif poll_count > 1:
            break

        if elapsed >= args.timeout:
            print(f"ERROR: Timed out after {elapsed}s waiting for scan completion", flush=True)
            sys.exit(1)

        time.sleep(args.poll_interval)

    results = []
    for lib in libs:
        live = final_map[lib["key"]]
        before = initial_state[lib["key"]]
        results.append({
            "key": lib["key"],
            "title": live["title"],
            "type": live["type"],
            "initial_scannedAt": before["scannedAt"],
            "final_scannedAt": live["scannedAt"],
            "initial_contentChangedAt": before["contentChangedAt"],
            "final_contentChangedAt": live["contentChangedAt"],
            "scan_timestamp_changed": before["scannedAt"] != live["scannedAt"],
            "content_timestamp_changed": before["contentChangedAt"] != live["contentChangedAt"],
        })

    result = {
        "library_count": len(results),
        "elapsed_seconds": int(time.time() - start),
        "libraries": results,
    }

    with open(args.result_file, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    print("\nScan complete.", flush=True)
    for lib in results:
        scan_flag = "yes" if lib["scan_timestamp_changed"] else "no"
        content_flag = "yes" if lib["content_timestamp_changed"] else "no"
        print(
            f"  [{lib['key']}] {lib['title']}: scannedAt changed={scan_flag}, contentChangedAt changed={content_flag}",
            flush=True,
        )
    print(f"\nScan result saved to {args.result_file}", flush=True)


if __name__ == "__main__":
    main()
