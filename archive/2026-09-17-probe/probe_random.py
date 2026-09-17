#!/usr/bin/env python3
# probe_random.py -- sample 30 channels from cache/best_sorted.m3u
# and measure reachability / liveness.
#
# Probe strategy (per channel):
#   1. HEAD/GET url with 3s timeout -- count as "reach" if HTTP 200
#      or 3xx (some IPTV servers redirect)
#   2. If url ends in .m3u8 (HLS) OR content-type says m3u8:
#      - GET manifest with 3s timeout
#      - parse first non-comment line as a slice URL
#      - resolve relative -> absolute
#      - HEAD/GET slice with 3s timeout
#      - count as "playable" only if slice is reachable
#   3. Final verdict:
#      - playable   = head ok + (non-HLS) OR (HLS + slice ok)
#      - reachable  = head ok + (HLS but slice failed)
#      - dead       = head failed / non-200 / timeout
#
# Sampling: stratified by tier so the report is meaningful:
#   - tier 1 (CCTV):           5 samples (if available)
#   - tier 2 (satellite):      5 samples (if available)
#   - tier 4 (HK/MO/TW):       5 samples
#   - tier 5 (other CN):      15 samples
from __future__ import annotations

import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "tools")))

from core.channel_name import classify, tier_label  # noqa: E402
from core.playlist import parse, Entry  # noqa: E402


CACHE_M3U = os.path.normpath(os.path.join(
    _HERE, "..", "..", "dist", "collect-iptv-portable", "cache", "best_sorted.m3u"
))

PROBE_TIMEOUT = 3.0

# A "slice URL" inside an m3u8 manifest: the first line that isn't
# blank / comment / EXTINF / another manifest.
_SLICE_RE = re.compile(r'^(?!\s*#)(?P<url>\S+)', re.M)


def _urlopen_head_or_get(url, timeout):
    """Try HEAD first (cheap); fall back to Range GET if HEAD is refused."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.headers.get("Content-Type", ""), None
    except urllib.error.HTTPError as e:
        # Some servers refuse HEAD with 405/501 -- fall back to GET.
        if e.code in (405, 501):
            return _urlopen_get(url, timeout, range_only=True)
        return int(e.code), "", str(e)
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        # OSError covers ConnectionResetError (10054), ConnectionRefused
        # (10061), and other socket-level failures that don't bubble up
        # as URLError on Python 3.
        return 0, "", type(e).__name__


def _urlopen_get(url, timeout, range_only=False):
    req = urllib.request.Request(url)
    if range_only:
        req.add_header("Range", "bytes=0-2047")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(2048 if range_only else None)
            return int(r.status), r.headers.get("Content-Type", ""), data
    except urllib.error.HTTPError as e:
        return int(e.code), "", str(e)
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        return 0, "", type(e).__name__


def _looks_like_hls(url, content_type):
    """Return True if URL/content-type suggests HLS."""
    if url.lower().endswith(".m3u8"):
        return True
    if content_type and "mpegurl" in content_type.lower():
        return True
    return False


def probe_one(entry):
    """Return dict with reach/playable verdict and timing."""
    t0 = time.time()
    code, ctype, err = _urlopen_head_or_get(entry.url, PROBE_TIMEOUT)
    head_ms = int((time.time() - t0) * 1000)

    # 200 or 3xx redirect counts as reachable.
    reach = 200 <= code < 400

    is_hls = _looks_like_hls(entry.url, ctype)
    playable = False
    slice_ms = 0
    slice_status = ""
    slice_errmsg = ""
    manifest_data = b""

    if reach and is_hls:
        # Pull manifest.
        code2, ctype2, manifest_data = _urlopen_get(entry.url, PROBE_TIMEOUT)
        if 200 <= code2 < 300 and manifest_data:
            # Find first slice URL.
            m = _SLICE_RE.search(manifest_data.decode("utf-8", errors="replace"))
            if m:
                slice_url = m.group("url")
                # Resolve relative.
                if not slice_url.startswith(("http://", "https://")):
                    base = entry.url.rsplit("/", 1)[0] + "/"
                    slice_url = base + slice_url
                t1 = time.time()
                sc, sct, serr = _urlopen_head_or_get(slice_url, PROBE_TIMEOUT)
                slice_ms = int((time.time() - t1) * 1000)
                slice_status = str(sc)
                if serr and not 200 <= sc < 400:
                    slice_errmsg = serr[:60]
                playable = 200 <= sc < 400

    verdict = "playable" if playable else ("reachable" if reach else "dead")
    return {
        "name": entry.display_name,
        "url": entry.url[:80],
        "verdict": verdict,
        "code": code,
        "head_ms": head_ms,
        "is_hls": is_hls,
        "slice_code": slice_status,
        "slice_ms": slice_ms,
        "err": (err or slice_errmsg)[:80] if not reach or not playable else "",
    }


def main():
    if not os.path.isfile(CACHE_M3U):
        print("[probe] cache m3u not found:", CACHE_M3U)
        return 1

    print("[probe] loading", CACHE_M3U)
    with open(CACHE_M3U, "rb") as f:
        data = f.read()
    entries = parse(data, source="cache")
    print("[probe] total entries:", len(entries))

    # Stratified sampling by tier.
    by_tier: dict = {}
    for e in entries:
        t = classify(e.display_name, e.group_title)
        by_tier.setdefault(t, []).append(e)

    sample_plan = {1: 5, 2: 5, 4: 5, 5: 15}
    samples = []
    rng = random.Random(42)  # deterministic for reproducibility
    for tier, n in sample_plan.items():
        pool = by_tier.get(tier, [])
        if not pool:
            print("[probe] tier %d: empty" % tier)
            continue
        take = min(n, len(pool))
        picks = rng.sample(pool, take)
        for e in picks:
            samples.append((tier, e))
        print("[probe] tier %d (%s): %d sampled (of %d)" % (
            tier, tier_label(tier), take, len(pool)
        ))

    print("[probe] sampling %d total; probing (timeout=%.1fs each)..." % (
        len(samples), PROBE_TIMEOUT
    ))

    results = []
    for i, (tier, e) in enumerate(samples):
        t0 = time.time()
        r = probe_one(e)
        elapsed = time.time() - t0
        # Defensive: coerce types we feed into %d.
        try:
            line = "  [%2d/%2d t%d %-12s] %-12s %s ms=%4d %s" % (
                i + 1, len(samples), int(tier),
                r["verdict"][:12].ljust(12),
                int(r["code"]), int(r["head_ms"]), r["url"][:60],
            )
        except (TypeError, ValueError) as fe:
            line = "  [%2d/%2d t%d %-12s] types=%s r=%r err=%s" % (
                i + 1, len(samples), tier,
                r["verdict"][:12].ljust(12),
                {k: type(v).__name__ for k, v in r.items()}, r, fe,
            )
        print(line)
        results.append((tier, r))

    # Summary.
    print()
    print("=" * 70)
    print("SUMMARY (by tier):")
    by_tier_stats: dict = {}
    for tier, r in results:
        st = by_tier_stats.setdefault(
            tier, {"total": 0, "playable": 0, "reachable": 0, "dead": 0,
                   "ms": []}
        )
        st["total"] += 1
        st[r["verdict"]] += 1
        st["ms"].append(r["head_ms"])

    for tier in sorted(by_tier_stats.keys()):
        st = by_tier_stats[tier]
        p = 100 * st["playable"] / st["total"]
        ra = 100 * st["reachable"] / st["total"]
        d = 100 * st["dead"] / st["total"]
        avg = sum(st["ms"]) / max(1, len(st["ms"]))
        print(
            "  tier %d (%-12s): %d/%d playable (%4.1f%%), %d/%d reachable (%4.1f%%), "
            "%d/%d dead (%4.1f%%)  avg HEAD %4d ms" % (
                tier, tier_label(tier),
                st["playable"], st["total"], p,
                st["reachable"], st["total"], ra,
                st["dead"], st["total"], d,
                avg,
            )
        )

    # Overall.
    total = sum(s["total"] for s in by_tier_stats.values())
    playable = sum(s["playable"] for s in by_tier_stats.values())
    reachable = sum(s["reachable"] for s in by_tier_stats.values())
    dead = sum(s["dead"] for s in by_tier_stats.values())
    print("-" * 70)
    print(
        "  TOTAL: %d/%d playable (%4.1f%%), %d/%d reachable (%4.1f%%), %d/%d dead (%4.1f%%)" % (
            playable, total, 100 * playable / total,
            reachable, total, 100 * reachable / total,
            dead, total, 100 * dead / total,
        )
    )
    print("=" * 70)

    # Recommendation.
    print()
    print("RECOMMENDATION (for source.ini merge rules):")
    for tier in sorted(by_tier_stats.keys()):
        st = by_tier_stats[tier]
        p = st["playable"] / max(1, st["total"])
        ra = st["reachable"] / max(1, st["total"])
        if p >= 0.5:
            verdict = "KEEP -- 高存活率,优先入库"
        elif p + ra >= 0.5:
            verdict = "MIXED  -- 半数可达但切片不稳,降权或观察"
        else:
            verdict = "DROP  -- 大量死链,建议过滤掉"
        print("  tier %d (%s): %.0f%% playable -> %s" % (
            tier, tier_label(tier), p * 100, verdict
        ))

    return 0


if __name__ == "__main__":
    sys.exit(main())