#!/usr/bin/env python3
# merge_test.py -- integration test for the multi-source merge flow
# without launching PotPlayer or showing MessageBox.
#
# Usage from project root:
#     python tools/archive/2026-09-16-bootstrap/merge_test.py
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _TOOLS)

from core import cache, fetcher, merge, playlist, source  # noqa: E402


def main() -> int:
    cfg = source.load_source()
    enabled = cfg.enabled_sources()
    print("[merge_test] enabled sources:")
    for s in enabled:
        print("  - %s: %s (mirrors=%d)" % (s.name, s.url[:80], len(s.mirrors)))

    # Fetch each source sequentially with a generous timeout so the
    # test is deterministic. Real exe uses ThreadPoolExecutor.
    entries_by_source = []
    errors = {}
    ok_names = []

    for s in enabled:
        print("[merge_test] fetching %s ..." % s.name)
        try:
            data, url = fetcher.fetch_with_fallback(
                s.all_candidates(), timeout=8.0
            )
            entries = playlist.parse(data, source=s.name)
            print("  -> %d entries from %s" % (len(entries), url[:80]))
            entries_by_source.append(entries)
            ok_names.append(s.name)
        except Exception as e:
            print("  -> FAIL: %s" % e)
            errors[s.name] = str(e)

    if not entries_by_source:
        print("[merge_test] all sources failed -- cannot test merge")
        return 1

    print("[merge_test] merging ...")
    merged = merge.merge(
        entries_by_source, per_channel_cap=cfg.per_channel_cap
    )
    s = merge.stats(merged)
    print("[merge_test] merged %d entries" % s["total"])
    print("  by_tier:", s["by_tier"])
    print("  by_source:", s["by_source"])

    # Inspect a few samples per tier.
    seen_tiers = set()
    for e in merged:
        from core.channel_name import classify, tier_label
        t = classify(e.display_name, e.group_title)
        if t not in seen_tiers:
            seen_tiers.add(t)
            print("  [tier %d / %s] %s -> %s" % (
                t, tier_label(t), e.display_name, e.url[:80]
            ))

    # Save to cache as best_sorted.m3u.
    out_bytes = playlist.serialize(
        merged, header="# merge_test at %d" % int(__import__('time').time())
    )
    meta = {
        "source_url": ", ".join(ok_names),
        "fetched_at": "merge_test",
        "channel_count": len(merged),
        "per_channel_cap": cfg.per_channel_cap,
        "by_tier": s["by_tier"],
        "by_source": s["by_source"],
        "fetch_errors": errors,
    }
    p = cache.save_m3u("best_sorted.m3u", out_bytes, meta)
    print("[merge_test] saved: %s (%d bytes)" % (p, len(out_bytes)))

    # Reload and sanity-check.
    cached = cache.load_cached("best_sorted.m3u")
    if not cached or cached[1].get("channel_count") != len(merged):
        print("[merge_test] cache round-trip FAILED")
        return 1
    print("[merge_test] cache round-trip OK; all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())