#!/usr/bin/env python3
# probe_100.py -- stratified sample of 100 channels from cache, with
# HLS-aware reach + slice probe per channel.
#
# Same probe logic as probe_random.py but with a different sampling
# plan that over-samples the priority tiers (CCTV / 卫视 / 港澳台)
# so the per-tier success rates are meaningful, even though the
# overall playlist is 99% tier-5 ("other CN" + foreign).
#
# Tier plan (sums to 100):
#   tier 1 CCTV:           12 samples  (over-sample)
#   tier 2 卫视:           12 samples  (over-sample -- expected all-dead)
#   tier 4 港澳台:          12 samples  (over-sample)
#   tier 5 其他中文:        58 samples  (the bulk)
#   tier 3 热门地方:        3 samples  (small pool, take whatever exists)
#   tier 6 外文:            3 samples
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
from core.playlist import parse  # noqa: E402


CACHE_M3U = os.path.normpath(os.path.join(
    _HERE, "..", "..", "dist", "collect-iptv-portable", "cache", "best_sorted.m3u"
))

PROBE_TIMEOUT = 3.0
PROBE_CONCURRENCY = 16  # parallel probes to keep wall time sane

_SLICE_RE = re.compile(r'^(?!\s*#)(?P<url>\S+)', re.M)


def _http_get(url, timeout, range_only=False):
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
    if url.lower().endswith(".m3u8"):
        return True
    if content_type and "mpegurl" in content_type.lower():
        return True
    return False


def probe_one(entry):
    """Return dict with verdict + timings."""
    t0 = time.time()
    code, ctype, err = _http_get(entry.url, PROBE_TIMEOUT, range_only=True)
    head_ms = int((time.time() - t0) * 1000)

    reach = 200 <= code < 400
    is_hls = _looks_like_hls(entry.url, ctype)
    playable = False
    slice_ms = 0
    slice_status = ""
    slice_err = ""

    if reach and is_hls:
        code2, ctype2, manifest = _http_get(entry.url, PROBE_TIMEOUT)
        if 200 <= code2 < 300 and manifest:
            m = _SLICE_RE.search(manifest.decode("utf-8", errors="replace"))
            if m:
                slice_url = m.group("url")
                if not slice_url.startswith(("http://", "https://")):
                    base = entry.url.rsplit("/", 1)[0] + "/"
                    slice_url = base + slice_url
                t1 = time.time()
                sc, sct, serr = _http_get(slice_url, PROBE_TIMEOUT, range_only=True)
                slice_ms = int((time.time() - t1) * 1000)
                slice_status = str(sc)
                if serr and not 200 <= sc < 400:
                    slice_err = serr[:60]
                playable = 200 <= sc < 400

    verdict = "playable" if playable else ("reachable" if reach else "dead")
    return {
        "name": entry.display_name,
        "url": entry.url,
        "verdict": verdict,
        "code": code,
        "head_ms": head_ms,
        "is_hls": is_hls,
        "slice_code": slice_status,
        "slice_ms": slice_ms,
        "err": (err or slice_err)[:80] if not reach or not playable else "",
    }


def main():
    if not os.path.isfile(CACHE_M3U):
        print("[probe] cache m3u not found:", CACHE_M3U)
        return 1

    with open(CACHE_M3U, "rb") as f:
        data = f.read()
    entries = parse(data, source="cache")
    print("[probe] cache has %d entries" % len(entries))

    by_tier = {}
    for e in entries:
        t = classify(e.display_name, e.group_title)
        by_tier.setdefault(t, []).append(e)

    sample_plan = {1: 12, 2: 12, 4: 12, 5: 58, 3: 3, 6: 3}
    rng = random.Random(20260917)  # different seed from probe_random.py
    samples = []
    for tier, n in sorted(sample_plan.items()):
        pool = by_tier.get(tier, [])
        if not pool:
            print("[probe] tier %d: empty -- skipping" % tier)
            continue
        take = min(n, len(pool))
        picks = rng.sample(pool, take)
        for e in picks:
            samples.append((tier, e))
        print("[probe] tier %d (%s): %d sampled of %d" % (
            tier, tier_label(tier), take, len(pool)
        ))

    print("[probe] sampling %d total" % len(samples))
    print("[probe] probing (timeout=%.1fs, concurrency=%d)..." % (
        PROBE_TIMEOUT, PROBE_CONCURRENCY
    ))
    t0 = time.time()

    # Probe in parallel via ThreadPoolExecutor to keep wall time low.
    import concurrent.futures as cf
    results = []
    with cf.ThreadPoolExecutor(max_workers=PROBE_CONCURRENCY) as ex:
        future_map = {
            ex.submit(probe_one, e): (tier, e) for tier, e in samples
        }
        for fut in cf.as_completed(future_map):
            tier, e = future_map[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001
                r = {"name": e.display_name, "url": e.url, "verdict": "dead",
                     "code": 0, "head_ms": 0, "is_hls": False,
                     "slice_code": "", "slice_ms": 0, "err": type(exc).__name__}
            results.append((tier, r))
    elapsed = time.time() - t0
    print("[probe] all probes done in %.1fs" % elapsed)

    # Per-channel one-line output, ordered by tier then index for stability.
    results.sort(key=lambda x: (x[0], samples.index(
        next((t, e) for t, e in samples if e is x[1] or
             (e.url == x[1]["url"] and e.display_name == x[1]["name"]))
    )))

    print()
    print("=" * 70)
    print("PER-CHANNEL RESULTS:")
    for i, (tier, r) in enumerate(results, 1):
        try:
            line = ("  [%3d/%d t%d %-10s] code=%3d ms=%5d  %s" % (
                i, len(results), tier,
                r["verdict"][:10].ljust(10),
                int(r["code"]), int(r["head_ms"]),
                r["url"][:55],
            ))
        except (TypeError, ValueError):
            line = "  [%3d/%d t%d %-10s] r=%r" % (
                i, len(results), tier, r["verdict"][:10].ljust(10), r,
            )
        print(line)

    # Summary by tier.
    print()
    print("=" * 70)
    print("SUMMARY (by tier):")
    by_tier_stats = {}
    for tier, r in results:
        st = by_tier_stats.setdefault(tier, {
            "n": 0, "play": 0, "reach": 0, "dead": 0, "ms": []
        })
        st["n"] += 1
        if r["verdict"] == "playable":
            st["play"] += 1
        elif r["verdict"] == "reachable":
            st["reach"] += 1
        else:
            st["dead"] += 1
        st["ms"].append(int(r["head_ms"]))

    for tier in sorted(by_tier_stats.keys()):
        st = by_tier_stats[tier]
        if not st["n"]:
            continue
        p = 100.0 * st["play"] / st["n"]
        ra = 100.0 * st["reach"] / st["n"]
        d = 100.0 * st["dead"] / st["n"]
        avg = sum(st["ms"]) / max(1, len(st["ms"]))
        mx = max(st["ms"])
        print("  tier %d (%-12s) n=%2d  playable=%2d (%4.1f%%)  "
              "reachable=%2d (%4.1f%%)  dead=%2d (%4.1f%%)  "
              "avg HEAD %5d ms  max %5d ms" % (
                  tier, tier_label(tier),
                  st["n"], st["play"], p, st["reach"], ra, st["dead"], d,
                  avg, mx,
              ))

    total = sum(s["n"] for s in by_tier_stats.values())
    playable = sum(s["play"] for s in by_tier_stats.values())
    reachable = sum(s["reach"] for s in by_tier_stats.values())
    dead = sum(s["dead"] for s in by_tier_stats.values())
    print("-" * 70)
    print("  TOTAL n=%d  playable=%d (%4.1f%%)  reachable=%d (%4.1f%%)  "
          "dead=%d (%4.1f%%)" % (
              total, playable, 100 * playable / total,
              reachable, 100 * reachable / total,
              dead, 100 * dead / total,
          ))
    print("=" * 70)

    # Latency buckets.
    all_ms = [int(r["head_ms"]) for _, r in results]
    print()
    print("LATENCY DISTRIBUTION (HEAD ms):")
    if all_ms:
        all_ms.sort()
        for pct in (10, 25, 50, 75, 90, 95):
            idx = min(len(all_ms) - 1, int(pct / 100.0 * len(all_ms)))
            print("  p%-2d: %5d ms" % (pct, all_ms[idx]))

    # Final recommendation summary.
    print()
    print("RECOMMENDATION (for merge.py default rules):")
    for tier in sorted(by_tier_stats.keys()):
        st = by_tier_stats[tier]
        if not st["n"]:
            continue
        p = st["play"] / st["n"]
        if p >= 0.5:
            verdict = "KEEP"
        elif p + st["reach"] / st["n"] >= 0.4:
            verdict = "LIMIT (cap entries)"
        else:
            verdict = "DROP"
        print("  tier %d (%-12s): %2d/%2d playable (%.0f%%) -> %s" % (
            tier, tier_label(tier),
            st["play"], st["n"], p * 100, verdict,
        ))

    return 0


if __name__ == "__main__":
    sys.exit(main())