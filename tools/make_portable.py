#!/usr/bin/env python3
# make_portable.py -- bundle the PyInstaller onedir output into a
# portable folder with source.ini, cache/, and 使用说明.txt.
#
# Reads:
#   dist/iptv-desktop/iptv-desktop.exe  (built by build.bat)
#   tools/source.ini
#
# Writes:
#   dist/iptv-desktop-portable/
#       iptv-desktop.exe
#       *.dll / *.pyd / base_library.zip / python38.dll  (from onedir)
#       source.ini
#       使用说明.txt
#       cache/    (empty; populated on first run)
#
# Also produces a zip in dist/iptv-desktop-portable.zip for sharing.
from __future__ import annotations

import os
import shutil
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _src_dir() -> str:
    return os.path.join(_ROOT, "dist", "iptv-desktop")


def _portable_dir() -> str:
    return os.path.join(_ROOT, "dist", "iptv-desktop-portable")


def _zip_path() -> str:
    return os.path.join(_ROOT, "dist", "iptv-desktop-portable.zip")


README_TEXT = r"""\
iptv-desktop v1.0
=================

What this is
------------
A personal fork of collect-iptv-desktop. Fetches the latest IPTV
playlist from public m3u feeds (Collect-IPTV + iptv-org + optionally
your own Guovin/iptv-api fork), applies 4-layer filtering (URL
blocklist + exclude_non_chinese + exclude_geo_blocked + full HEAD
probe), and hands the result to PotPlayer.

It does NOT do local aggregation or fancy probing. The upstream
projects already aggregate and probe -- this tool merges their
output and validates each URL before delivering the playlist.

How to use
----------
Double-click iptv-desktop.exe. Wait ~1-2 minutes (full probe on
~10k channels). PotPlayer opens with the filtered playlist.

If all mirrors fail (no internet), the tool falls back to the most
recently cached playlist and still opens PotPlayer. You'll see a
warning dialog saying "using local cache".

CLI flags:
  iptv-desktop.exe           default: console progress window
  iptv-desktop.exe --silent  no window, no log to console

Files in this folder
--------------------
  iptv-desktop.exe    the launcher
  *.dll / *.pyd       Python + PyInstaller runtime
  source.ini          config (sources / filters / player path)
  cache\              populated on first run:
    best_sorted.m3u   the playlist (CCTV+卫视+中文优先排序)
    meta.json         {saved_at, source_url, channel_count, by_tier}
    iptv-desktop.log  last-run log (errors only)

Configuration (source.ini)
--------------------------
  [source]
  url       = primary URL (tried first)
  format    = m3u | m3u8  (default: m3u)
  mirrors   = pipe-separated fallback list

  [player]
  path      = leave empty to auto-detect PotPlayer, or set an explicit
              path like  C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe

Mirror fallback order
---------------------
  1. raw.githubusercontent.com   (fastest, often blocked in CN)
  2. cdn.jsdelivr.net            (CDN, very reliable)
  3. zilong7728.github.io        (GitHub Pages, slowest but most open)

Each tier has a 5s timeout. If all three fail, the cached playlist
is used. If no cache exists either, a dialog shows the error.

Supported OS
------------
Designed and tested for Windows 7 SP1 x64 (via Python 3.8.10).
Also runs on Win10 / Win11.

Disclaimer
----------
This tool only fetches and hands off a publicly available playlist.
It does not store, host, or modify any media. All channel copyrights
belong to their respective owners. Use at your own discretion.
"""


def _copy_onedir(src: str, dst: str) -> None:
    """Copy the onedir folder (exe + dlls + python runtime) verbatim.

    Uses dirs_exist_ok so a locked dst (e.g. an old exe being scanned
    by AV) doesn't block a fresh build -- new files overwrite, stale
    ones get pruned by ``_prune_stale`` below.
    """
    if not os.path.isdir(src):
        sys.stderr.write(
            "[ERROR] %s not found. Run build.bat first.\n" % src
        )
        sys.exit(1)
    if os.path.isdir(dst):
        _prune_stale(src, dst)
    shutil.copytree(src, dst, dirs_exist_ok=True)


# Paths that must NEVER be pruned from the portable folder, even
# though they don't exist in the PyInstaller onedir output.
#
# Why: ``_prune_stale`` removes files in dst that aren't in src to
# drop stale runtime DLLs. But the portable folder also contains
# RUNTIME DATA (the user's fetched playlist, config, readme) that has
# no counterpart in src. Without this whitelist every rebuild wipes
# the user's cache, and the next launch dies with
# "no cache; aborting" whenever the network is down.
_PRESERVE_PREFIXES = (
    "cache/",            # runtime data: best_sorted.m3u, meta.json, logs
    "cache",             # (defensive: bare dir name)
    "source.ini",        # user-editable config
    "使用说明.txt",        # end-user readme
)


def _is_preserved(rel: str) -> bool:
    """Return True if the dst-relative path must survive pruning."""
    norm = rel.replace(os.sep, "/")
    for p in _PRESERVE_PREFIXES:
        if norm == p.rstrip("/") or norm.startswith(p):
            return True
    return False


def _prune_stale(src: str, dst: str) -> None:
    """Remove files in dst that no longer exist in src (or got renamed).

    This avoids leaving stale PyInstaller runtime DLLs in the portable
    folder when the build's module set changes between revisions.

    Runtime data (``cache/``, ``source.ini``, the bundled readme) is
    explicitly preserved -- see ``_PRESERVE_PREFIXES``.
    """
    src_set = set()
    for root, dirs, files in os.walk(src):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, src)
            src_set.add(rel.replace(os.sep, "/"))

    for root, dirs, files in os.walk(dst):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, dst).replace(os.sep, "/")
            if rel in src_set:
                continue
            if _is_preserved(rel):
                continue
            try:
                os.remove(full)
            except OSError:
                # Best effort -- if AV is locking it, leave it; the
                # next build will eventually clean it up.
                pass


def _seed_cache(dst: str) -> int:
    """Copy a working playlist into the portable cache/ if the source
    project has one.

    Rationale: a freshly extracted zip has an empty cache, so the very
    first launch fails hard if the network is unavailable. Shipping a
    recent playlist makes the "no internet" path degrade to "opens the
    last known playlist" instead of "error dialog".

    Seed candidates, in priority order:
      1. <root>/seed_cache/   -- explicit, hand-curated seed
      2. <root>/cache/        -- frozen-mode cache
      3. <root>/tools/cache/  -- source-mode cache (where dev runs write)

    Returns the number of files copied.
    """
    candidates = (
        os.path.join(_ROOT, "seed_cache"),
        os.path.join(_ROOT, "cache"),
        os.path.join(_ROOT, "tools", "cache"),
    )
    seed_dir = None
    for c in candidates:
        if os.path.isfile(os.path.join(c, "best_sorted.m3u")):
            seed_dir = c
            break
    if seed_dir is None:
        return 0

    dst_cache = os.path.join(dst, "cache")
    os.makedirs(dst_cache, exist_ok=True)

    copied = 0
    for fn in ("best_sorted.m3u", "meta.json"):
        src_f = os.path.join(seed_dir, fn)
        if not os.path.isfile(src_f):
            continue
        dst_f = os.path.join(dst_cache, fn)
        # Never clobber a newer cache that already lives in the
        # portable folder (preserves a cache written by a recent run).
        if os.path.isfile(dst_f) and os.path.getmtime(dst_f) >= os.path.getmtime(src_f):
            continue
        shutil.copy2(src_f, dst_f)
        copied += 1
    if copied:
        print("[make_portable] seed source: %s" % seed_dir)
    return copied


def _add_aux(dst: str) -> None:
    """Copy source.ini, create empty cache/, write 使用说明.txt."""
    ini_src = os.path.join(_HERE, "source.ini")
    if os.path.isfile(ini_src):
        shutil.copy2(ini_src, os.path.join(dst, "source.ini"))

    cache_dst = os.path.join(dst, "cache")
    os.makedirs(cache_dst, exist_ok=True)

    # Remove dev-only artifacts that testing redirects may have left
    # behind; they have no meaning in a shipped folder.
    for junk in ("stdout.log", "stderr.log"):
        p = os.path.join(cache_dst, junk)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass

    # .gitkeep so the empty cache/ travels in the zip.
    with open(os.path.join(cache_dst, ".gitkeep"), "w") as f:
        f.write("")

    readme_path = os.path.join(dst, "使用说明.txt")
    # CRLF for cmd.exe Notepad friendliness.
    with open(readme_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(README_TEXT)


def _make_zip(folder: str, zip_out: str) -> None:
    if os.path.isfile(zip_out):
        os.remove(zip_out)
    base = os.path.dirname(folder)
    name = os.path.basename(folder)
    with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(folder):
            # Skip __pycache__ if any leaked in.
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, base)
                # Write the top folder as the zip root.
                arc = os.path.join(name, os.path.relpath(full, folder))
                zf.write(full, arcname=arc)


def main() -> int:
    src = _src_dir()
    dst = _portable_dir()
    zip_out = _zip_path()

    print("[make_portable] copying %s -> %s" % (src, dst))
    _copy_onedir(src, dst)

    print("[make_portable] adding source.ini + cache/ + 使用说明.txt")
    _add_aux(dst)

    # Seed cache with a working playlist so a freshly extracted zip
    # can still open something when the network is unavailable.
    seeded = _seed_cache(dst)
    if seeded:
        print("[make_portable] seeded cache with %d file(s) "
              "(offline fallback)" % seeded)
    else:
        print("[make_portable] cache already current (or no seed available)")

    print("[make_portable] writing zip: %s" % zip_out)
    _make_zip(dst, zip_out)

    size_mb = os.path.getsize(zip_out) / (1024 * 1024)
    print("[make_portable] done. portable=%s zip=%.1fMB" % (dst, size_mb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
