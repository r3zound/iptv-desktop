# -*- coding: utf-8 -*-
"""Compare legacy probe (per-URL) vs host-aware probe on real m3u data.

Run:  python archive/2026-09-17-probe-host-dedup/measure_speed.py
Output: timing table + summary.

Both implementations probe the SAME list of URLs, in random order, on
the same machine, with the same concurrency. The only difference is
how many probe tasks are submitted.
"""
from __future__ import annotations

import os
import random
import re
import sys
import time
from typing import Dict, List, Tuple
from urllib.parse import urlparse

# ensure project importable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from core.filters import (  # noqa: E402
    FilterConfig, _head_check, _probe_full,
)


def _import_legacy_probe_full() -> object:
    """1:1 inline copy of the pre-2026-09-17 _probe_full body.

    Kept here so the comparison runs without needing git. The body is
    an honest copy of the old logic (per-URL submit, as_completed,
    per-URL pass/fail classification). Diff vs git HEAD at commit
    40ffba0 (rename: iptv-r3zound -> iptv-desktop) is the source of truth.
    """
    import concurrent.futures

    def _legacy_probe_full(entries_by_tier, cfg, sink=None):
        kept: Dict[int, List] = {}
        dropped: List = []
        all_entries: List = []
        for tier, pool in entries_by_tier.items():
            all_entries.extend(pool)

        if sink is not None:
            sink.emit(("status",
                "legacy probe: %d URL, conc=%d" % (len(all_entries), cfg.probe_concurrency)))

        code_map: Dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.probe_concurrency) as ex:
            futs = {ex.submit(_head_check, e.url, cfg.probe_timeout): e for e in all_entries}
            for fut in concurrent.futures.as_completed(futs):
                e = futs[fut]
                try:
                    code = fut.result()
                except Exception:
                    code = 0
                code_map[e.url] = code

        for tier, pool in entries_by_tier.items():
            kept_pool = []
            for e in pool:
                code = code_map.get(e.url, 0)
                if 200 <= code < 400:
                    kept_pool.append(e)
                else:
                    dropped.append((e, "probe: HEAD %d" % code))
            if kept_pool:
                kept[tier] = kept_pool
        return kept, dropped

    return _legacy_probe_full


# ---- minimal Entry shim (playlist.Entry has the same shape we need) ----
class _Entry:
    __slots__ = ("name", "url", "tier")
    def __init__(self, name, url, tier):
        self.name = name
        self.url = url
        self.tier = tier


def _load_m3u_urls(path: str) -> List[str]:
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    return re.findall(r"^(https?://\S+)$", text, re.M)


def _make_entries(urls: List[str], tier: int = 2) -> Dict[int, List[_Entry]]:
    pool = []
    for i, u in enumerate(urls):
        pool.append(_Entry("ch-%04d" % i, u, tier))
    return {tier: pool}


class _Sink:
    def emit(self, ev):
        # Keep output minimal -- print only the final stats line.
        pass


def _measure(fn, entries_by_tier, cfg, label: str) -> dict:
    t0 = time.time()
    kept, dropped = fn(entries_by_tier, cfg, sink=_Sink())
    dt = time.time() - t0
    n_kept = sum(len(v) for v in kept.values())
    n_drop = len(dropped)
    return {
        "label": label,
        "wall": dt,
        "kept": n_kept,
        "dropped": n_drop,
    }


def main():
    seed = os.path.join(ROOT, "seed_cache", "best_sorted.m3u")
    if not os.path.exists(seed):
        raise SystemExit("missing seed cache: " + seed)
    urls = _load_m3u_urls(seed)
    if not urls:
        raise SystemExit("no URLs in seed m3u")

    # dedup & shuffle so both runs probe the same set in random order
    seen = set()
    uniq_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq_urls.append(u)
    random.Random(42).shuffle(uniq_urls)
    hosts = set()
    for u in uniq_urls:
        h = urlparse(u).hostname
        if h:
            hosts.add(h)
    print("dataset: %d unique URLs / %d unique hosts (ratio %.2fx)" % (
        len(uniq_urls), len(hosts), len(uniq_urls) / max(1, len(hosts))))
    print()

    cfg = FilterConfig(
        url_patterns=[],
        probe_mode="full",
        probe_timeout=1.5,
        probe_concurrency=16,
        probe_sample_size=8,
        probe_min_success_rate=0.2,
    )

    legacy = _import_legacy_probe_full()

    # run legacy first (cold cache), then host-aware
    by_tier = _make_entries(uniq_urls)
    r_legacy = _measure(legacy, by_tier, cfg, "legacy per-URL")
    print("[1/2] %-25s wall=%.2fs kept=%d dropped=%d" % (
        r_legacy["label"], r_legacy["wall"], r_legacy["kept"], r_legacy["dropped"]))

    # small pause so the network isn't just serving a warm cache
    time.sleep(2)

    by_tier = _make_entries(uniq_urls)
    r_new = _measure(_probe_full, by_tier, cfg, "host-aware dedup")
    print("[2/2] %-25s wall=%.2fs kept=%d dropped=%d" % (
        r_new["label"], r_new["wall"], r_new["kept"], r_new["dropped"]))

    print()
    if r_legacy["wall"] > 0:
        speedup = r_legacy["wall"] / r_new["wall"]
    else:
        speedup = float("inf")
    print("speedup: %.2fx  (legacy %.2fs -> host-aware %.2fs)" % (
        speedup, r_legacy["wall"], r_new["wall"]))


if __name__ == "__main__":
    main()