#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared config resolution for the plex-match scripts.

Nothing machine-specific is hardcoded here. Every value comes from the
environment, with an auto-discovery fallback for the Plex SQLite path.

    PLEX_BASE        e.g. http://192.168.1.50:32400        (required by REST scripts)
    PLEX_TOKEN       the X-Plex-Token value                 (or use PLEX_TOKEN_FILE)
    PLEX_TOKEN_FILE  path to a file holding the token       (default /tmp/plex_token)
    PLEX_DB          path to com.plexapp.plugins.library.db (else auto-discovered)
    PLEX_BACKUP_DIR  where revert maps are written          (default ./plex-match-backups)

Auto-discovery walks the usual QNAP locations and, failing that, searches
`/share/*/.qpkg/PlexMediaServer` for the live database. The stub copy under
`Resources/` (~300KB, shipped inside the QPKG) is rejected by size.
"""
import glob
import os
import sys

DEFAULT_TOKEN_FILE = "/tmp/plex_token"
DB_NAME = "com.plexapp.plugins.library.db"
DB_RELPATH = ("Library/Plex Media Server/Plug-in Support/Databases/" + DB_NAME)


def base():
    b = os.environ.get("PLEX_BASE")
    if not b:
        sys.exit("PLEX_BASE is not set (e.g. export PLEX_BASE=http://nas:32400)")
    return b.rstrip("/")


def token():
    t = os.environ.get("PLEX_TOKEN")
    if t:
        return t.strip()
    path = os.environ.get("PLEX_TOKEN_FILE", DEFAULT_TOKEN_FILE)
    try:
        with open(path) as fh:
            t = fh.read().strip()
    except OSError:
        sys.exit("no token: set PLEX_TOKEN, or put it in %s (or PLEX_TOKEN_FILE)" % path)
    if not t:
        sys.exit("token file %s is empty" % path)
    # Plex tokens are pure ASCII; a look-alike Cyrillic char silently 401s
    if not t.isascii():
        sys.exit("token contains non-ASCII characters -- likely a look-alike typo")
    return t


def _candidates():
    env = os.environ.get("PLEX_DB")
    if env:
        yield env
    for root in sorted(glob.glob("/share/*/.qpkg/PlexMediaServer")) + \
            sorted(glob.glob("/var/lib/plexmediaserver/Library/Application Support")) + \
            ["/var/lib/plex/Library/Application Support"]:
        yield os.path.join(root, DB_RELPATH)
    yield DB_RELPATH


def db():
    for path in _candidates():
        try:
            # the QPKG ships a ~300KB stub under Resources/; the live DB is 100MB+
            if os.path.isfile(path) and os.path.getsize(path) > 5 * 1024 * 1024:
                return path
        except OSError:
            continue
    sys.exit("could not locate %s -- set PLEX_DB explicitly" % DB_NAME)


def db_uri():
    """Read-only SQLite URI. Always open the live DB read-only."""
    import urllib.parse
    return "file:" + urllib.parse.quote(db()) + "?mode=ro"


def backup_dir():
    d = os.environ.get("PLEX_BACKUP_DIR", "./plex-match-backups")
    os.makedirs(d, exist_ok=True)
    return d
