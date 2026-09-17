#!/usr/bin/env python3
# smoke_test.py -- verify fetcher + cache without launching PotPlayer
# or showing MessageBox. Run from project root:
#     python tools/archive/2026-09-16-bootstrap/smoke_test.py
#
# Exits 0 on success, 1 on any failure.
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _TOOLS)

from core import cache, fetcher, source  # noqa: E402


def main() -> int:
    print("[smoke] loading source config...")
    cfg = source.load_source()
    print("[smoke] format=%s, primary=%s" % (cfg.format, cfg.url))
    print("[smoke] mirrors=%d" % len(cfg.mirrors))
    for i, c in enumerate(cfg.all_candidates()):
        print("  [%d] %s" % (i, c))

    print("[smoke] fetching...")
    try:
        data, url = fetcher.fetch_with_fallback(cfg.all_candidates())
    except fetcher.FetchError as e:
        print("[smoke] FETCH FAILED: %s" % e)
        return 1

    print("[smoke] fetched %d bytes from %s" % (len(data), url))
    ch = data.count(b"#EXTINF")
    print("[smoke] channel count (EXTINF lines) = %d" % ch)

    print("[smoke] saving to cache...")
    meta = {
        "source_url": url,
        "fetched_at": "smoke-test",
        "channel_count": ch,
    }
    path = cache.save_m3u(cfg.filename(), data, meta)
    print("[smoke] saved to %s (%d bytes)" % (path, os.path.getsize(path)))

    print("[smoke] reloading cache...")
    cached = cache.load_cached(cfg.filename())
    if not cached:
        print("[smoke] cache load FAILED")
        return 1
    data2, meta2 = cached
    print("[smoke] cache reload OK (%d bytes, saved_at=%s)" % (
        len(data2), meta2.get("saved_at")
    ))
    if data != data2:
        print("[smoke] cache bytes differ from saved!")
        return 1

    print("[smoke] all OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
