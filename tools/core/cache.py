# cache.py -- read/write the exe-sibling cache/.
#
# Layout:
#   cache/best_sorted.m3u     -- the bytes we last successfully fetched
#   cache/meta.json           -- provenance metadata
#
# Atomic writes: write to .tmp first, then os.replace() onto the real
# path. This avoids partial-file corruption if the process dies mid-write.
from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional, Tuple


META_FILENAME = "meta.json"
META_VERSION = 1


def _base_dir() -> str:
    """Return the exe-sibling root.

    Frozen: parent of sys.executable.
    Source: parent of tools/ (i.e. the project root).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cache_dir() -> str:
    """Return <exe_dir>/cache, creating it if missing."""
    d = os.path.join(_base_dir(), "cache")
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def _atomic_write(path: str, data: bytes) -> None:
    """Write bytes to path atomically (tmp + os.replace)."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_m3u(filename: str, data: bytes, meta: dict) -> str:
    """Write m3u bytes + meta.json to cache/.

    Returns the absolute path of the saved m3u.
    """
    cd = cache_dir()
    m3u_path = os.path.join(cd, filename)
    meta_path = os.path.join(cd, META_FILENAME)

    _atomic_write(m3u_path, data)

    full_meta = dict(meta)
    full_meta["meta_version"] = META_VERSION
    full_meta["saved_at"] = int(time.time())
    full_meta["file_size"] = len(data)

    # meta.json is small text; safe to write directly.
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(full_meta, f, ensure_ascii=False, indent=2)

    return m3u_path


def load_cached(filename: str) -> Optional[Tuple[bytes, dict]]:
    """Return (m3u_bytes, meta_dict) if both cache files exist, else None.

    Missing or partially written cache is treated as a cache miss.
    """
    cd = cache_dir()
    m3u_path = os.path.join(cd, filename)
    meta_path = os.path.join(cd, META_FILENAME)

    if not (os.path.isfile(m3u_path) and os.path.isfile(meta_path)):
        return None
    if os.path.getsize(m3u_path) == 0:
        return None

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    try:
        with open(m3u_path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    if not data:
        return None
    return data, meta


def log_path() -> str:
    """Path of the rolling log file (errors only, since UI is silent)."""
    return os.path.join(cache_dir(), "iptv-desktop.log")


def describe_age(meta: Optional[dict]) -> str:
    """Human-readable age of a cached playlist, e.g. "2h14m" or "3d".

    Returns "" when meta is missing or has no usable timestamp.
    """
    if not meta:
        return ""
    saved_at = meta.get("saved_at")
    if not saved_at:
        return ""
    try:
        age = max(0, int(time.time()) - int(saved_at))
    except (TypeError, ValueError):
        return ""
    if age < 60:
        return "%ds" % age
    if age < 3600:
        return "%dm" % (age // 60)
    if age < 86400:
        return "%dh%02dm" % (age // 3600, (age % 3600) // 60)
    return "%dd%02dh" % (age // 86400, (age % 86400) // 3600)


def cache_summary(filename: str) -> str:
    """One-line description of the cached playlist for status output.

    Example: "cache: 691 channels, 2h14m old"
             "cache: none"
    """
    cached = load_cached(filename)
    if not cached:
        return "cache: none"
    _data, meta = cached
    n = meta.get("channel_count", "?")
    age = describe_age(meta)
    if age:
        return "cache: %s channels, %s old" % (n, age)
    return "cache: %s channels" % n


def log(msg: str) -> None:
    """Append a single line to the log. Used for error reporting since
    silent mode hides the console window."""
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass
