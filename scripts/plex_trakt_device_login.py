#!/usr/bin/env python3
"""Manual Trakt device-auth fallback for PlexTraktSync."""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.trakt.tv"
CTX = ssl.create_default_context()

try:
    import requests
except Exception:  # pragma: no cover - optional dependency
    requests = None


def parse_error_message(raw):
    message = raw
    try:
        parsed = json.loads(raw) if raw else {}
        error = parsed.get("error")
        desc = parsed.get("error_description")
        if error or desc:
            message = f"{error or 'error'}: {desc or ''}".strip()
    except Exception:
        pass
    return message


def friendly_http_message(status_code, message):
    friendly = {
        400: "authorization still pending or request invalid",
        404: "invalid device code",
        409: "device code already approved",
        410: "device code expired; start again",
        418: "device code denied by user",
    }.get(status_code)
    if friendly:
        return f"{friendly} ({message})"
    return message


def request_json_requests(method, path, headers, payload=None):
    response = requests.request(
        method,
        f"{API_BASE}{path}",
        headers=headers,
        json=payload,
        timeout=60,
    )
    raw = response.text
    if response.status_code >= 400:
        message = friendly_http_message(response.status_code, parse_error_message(raw))
        raise RuntimeError(f"{method} {path} failed with HTTP {response.status_code}: {message}")
    return response.status_code, json.loads(raw) if raw else {}


def request_json(method, path, headers, payload=None):
    last_error = None
    for attempt in range(3):
        try:
            if requests is not None:
                return request_json_requests(method, path, headers, payload)

            data = None
            req_headers = dict(headers)
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                req_headers["Content-Type"] = "application/json"

            req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=60, context=CTX) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            message = friendly_http_message(e.code, parse_error_message(raw))
            raise RuntimeError(f"{method} {path} failed with HTTP {e.code}: {message}") from e
        except Exception as e:
            last_error = RuntimeError(f"{method} {path} failed: {e}")
            if "failed with HTTP 4" in str(last_error) or attempt == 2:
                raise last_error from e
            time.sleep((attempt + 1) * 5)

    raise last_error


def write_json(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_username(settings):
    user = settings.get("user") or {}
    account = settings.get("account") or {}
    ids = user.get("ids") or {}
    return user.get("username") or account.get("username") or ids.get("slug")


def upsert_env(env_path, values):
    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    else:
        lines = []

    if not lines:
        lines = ["# This is .env file for PlexTraktSync\n"]

    updated = []
    written = set()
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, _ = line.rstrip("\n").split("=", 1)
            if key in values:
                updated.append(f"{key}={values[key]}\n")
                written.add(key)
                continue
        updated.append(line if line.endswith("\n") else f"{line}\n")

    for key, value in values.items():
        if key not in written:
            updated.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as handle:
        handle.writelines(updated)


def start(args):
    headers = {
        "trakt-api-version": "2",
        "trakt-api-key": args.client_id,
    }
    _, data = request_json("POST", "/oauth/device/code", headers, {"client_id": args.client_id})
    now = int(time.time())
    state = {
        "client_id": args.client_id,
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_url": data["verification_url"],
        "interval": data["interval"],
        "expires_in": data["expires_in"],
        "requested_at": now,
        "expires_at": now + int(data["expires_in"]),
    }
    write_json(args.state_file, state)

    print(f"User code: {state['user_code']}")
    print(f"Verification URL: {state['verification_url']}")
    print(f"State file: {args.state_file}")
    print(json.dumps(state, ensure_ascii=False))


def finish(args):
    state = read_json(args.state_file)
    device_code = args.device_code or state.get("device_code")
    if not device_code:
        raise RuntimeError("No device_code found; run start first or pass --device-code")

    headers = {
        "trakt-api-version": "2",
        "trakt-api-key": args.client_id,
    }
    _, token = request_json(
        "POST",
        "/oauth/device/token",
        headers,
        {
            "code": device_code,
            "client_id": args.client_id,
            "client_secret": args.client_secret,
        },
    )

    _, settings = request_json(
        "GET",
        "/users/settings",
        {
            **headers,
            "Authorization": f"Bearer {token['access_token']}",
        },
    )
    username = resolve_username(settings)
    if not username:
        raise RuntimeError("Trakt login succeeded but username could not be resolved")

    config_dir = os.path.abspath(args.config_dir)
    os.makedirs(config_dir, exist_ok=True)

    pytrakt_path = os.path.join(config_dir, ".pytrakt.json")
    env_path = os.path.join(config_dir, ".env")
    summary_path = args.summary_file or os.path.join(config_dir, "trakt_auth_result.json")

    pytrakt = {
        "APPLICATION_ID": None,
        "CLIENT_ID": args.client_id,
        "CLIENT_SECRET": args.client_secret,
        "OAUTH_EXPIRES_AT": int(token["created_at"]) + int(token["expires_in"]),
        "OAUTH_REFRESH": token["refresh_token"],
        "OAUTH_TOKEN": token["access_token"],
    }
    write_json(pytrakt_path, pytrakt)

    env_updates = {"TRAKT_USERNAME": username}
    if args.plex_server_name:
        env_updates["PLEX_SERVER"] = args.plex_server_name
    upsert_env(env_path, env_updates)

    summary = {
        "username": username,
        "pytrakt_file": pytrakt_path,
        "env_file": env_path,
        "expires_at": pytrakt["OAUTH_EXPIRES_AT"],
    }
    write_json(summary_path, summary)

    print(f"Trakt login complete for {username}")
    print(f"Wrote {pytrakt_path}")
    print(f"Updated {env_path}")
    print(json.dumps(summary, ensure_ascii=False))


def build_parser():
    parser = argparse.ArgumentParser(description="Manual Trakt device-auth fallback for PlexTraktSync")
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="Request a Trakt device code and save it to a state file")
    start_parser.add_argument("--client-id", required=True, help="Trakt API client id")
    start_parser.add_argument(
        "--state-file",
        default="/tmp/plex_trakt_device_auth.json",
        help="Where to store the generated device code state",
    )
    start_parser.set_defaults(func=start)

    finish_parser = sub.add_parser("finish", help="Exchange an approved device code for tokens and write PTS config")
    finish_parser.add_argument("--client-id", required=True, help="Trakt API client id")
    finish_parser.add_argument("--client-secret", required=True, help="Trakt API client secret")
    finish_parser.add_argument(
        "--state-file",
        default="/tmp/plex_trakt_device_auth.json",
        help="State file created by the start command",
    )
    finish_parser.add_argument("--device-code", default=None, help="Override the device code from the state file")
    finish_parser.add_argument("--config-dir", required=True, help="PlexTraktSync config dir")
    finish_parser.add_argument(
        "--plex-server-name",
        default=None,
        help="Optional PLEX_SERVER value to upsert into .env (for example: default)",
    )
    finish_parser.add_argument("--summary-file", default=None, help="Optional JSON summary output path")
    finish_parser.set_defaults(func=finish)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
