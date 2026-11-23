#!/usr/bin/env python3
"""Download the KDE Connect packages Frame Control ships for the Frame into
frame/kdeconnect/packages, checking each against its pinned SHA-256
(packages.json). Already-downloaded files that match are kept.

ios/scripts/make_frame_bundle.py runs this; app/build/fetch-deps.js does the same
in Node. Usage: python3 frame/kdeconnect/fetch.py
"""
import hashlib
import http.client
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGES = HERE / "packages"


def manifest():
    return json.loads((HERE / "packages.json").read_text(encoding="utf-8"))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _require_https(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"refusing non-HTTPS package URL: {url!r}")


def download_verified(url, expected_sha256):
    _require_https(url)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else url
                if isinstance(final_url, str) and final_url:
                    _require_https(final_url)
                data = response.read()
        except (OSError, http.client.HTTPException) as error:
            # GitHub release downloads occasionally close during connection or
            # transfer. Retry those failures, but fail fast on permanent HTTP errors.
            if isinstance(error, urllib.error.HTTPError) and error.code not in (408, 429) and error.code < 500:
                raise
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
            continue
        if sha256(data) != expected_sha256:
            raise SystemExit(f"checksum mismatch for {url}: {sha256(data)}")
        return data


def _safe_filename(name):
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"invalid package filename: {name!r}")
    return name


def fetch():
    m = manifest()
    PACKAGES.mkdir(exist_ok=True)
    for p in m["packages"]:
        name = _safe_filename(p["file"])
        path = PACKAGES / name
        if path.is_file() and sha256(path.read_bytes()) == p["sha256"]:
            continue
        url = m["release"] + name
        data = download_verified(url, p["sha256"])
        tmp = path.with_suffix(".part")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    wanted = {_safe_filename(p["file"]) for p in m["packages"]}
    for old in PACKAGES.iterdir():
        if old.name not in wanted:
            if old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)
            else:
                old.unlink()
    return [PACKAGES / _safe_filename(p["file"]) for p in m["packages"]]


if __name__ == "__main__":
    for path in fetch():
        print(path.relative_to(HERE.parent.parent), file=sys.stderr)
