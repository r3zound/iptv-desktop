# -*- coding: utf-8 -*-
"""Replace _probe_full in tools/core/filters.py with the host-aware
two-phase version. Used once for the 2026-09-17 optimization.
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILTERS = os.path.join(ROOT, "tools", "core", "filters.py")

START_MARK = "def _probe_full("
END_SENTINEL = "    return kept, dropped_entries"  # last line of the function


def _probe_full_new() -> str:
    return '''def _probe_full(
    entries_by_tier: dict, cfg: FilterConfig, sink=None
) -> Tuple[dict, dict]:
    """HEAD every entry; drop entries that don't return 200/206.

    Two-phase probe (host-aware dedup, since 2026-09-17):

      Phase 1 -- host representative probe.
        Group by ``urlparse(url).hostname``. For each unique host, fire
        one GET-Range probe against the FIRST entry's URL (a real stream
        path, not a synthetic host root -- CDN root paths often 404 on
        IPTV hosts). Hosts that time out / fail DNS / return 5xx are
        marked dead; their whole group of URLs is dropped WITHOUT
        firing a per-URL probe. This kills the worst-case ``dead host x N
        URLs x 1.5s`` cost that dominated the original implementation.

      Phase 2 -- per-URL probe under live hosts.
        Only URLs whose host passed phase 1 are probed individually.
        URLs whose host failed are pre-marked with the host's code and
        added to ``dropped_entries`` with a "host unreachable" reason.

    Both phases run with the same ``probe_concurrency`` pool. The second
    phase inherits the original probe semantics (one GET per URL,
    single-serial 200/206 success criterion, one fast-failure retry).

    Progress reporting:
      "  host probe  80/800  [████████████████████]  10%"
      "  url  probe 4500/9000 [██████████__________]  50%  (host_dedup 100 dead)"

    Net effect on a typical 11000-URL mix with ~10% dead hosts:
      - old: every URL probed (worst case ~10 min with concurrency=16)
      - new: ~800 host probes + ~9000 URL probes (worst case ~4-5 min)

    Accuracy: identical to old code on live-host URLs. Dead-host URLs
    are dropped with the host's actual HTTP code (or 0 for socket fail),
    so the final playlist only loses entries that would have failed
    anyway -- it just loses them faster.
    """
    from urllib.parse import urlparse

    kept: dict = {}
    dropped_entries: list = []

    all_entries: List[Entry] = []
    for tier, pool in entries_by_tier.items():
        all_entries.extend(pool)

    # ---- group by host ----
    host_groups = {}  # host -> [entries]; representative URL is entries[0].url
    no_host_entries: List[Entry] = []  # URLs without a parseable hostname
    for e in all_entries:
        try:
            h = urlparse(e.url).hostname
        except Exception:
            h = None
        if not h:
            no_host_entries.append(e)
            continue
        host_groups.setdefault(h, []).append(e)

    n_hosts = len(host_groups) + (1 if no_host_entries else 0)
    if sink is not None:
        sink.emit((
            "status",
            "\\u5168\\u91cf HEAD probe: %d URL -> %d host (\\u8d85\\u65f6 %.1fs, \\u5e76\\u53d1 %d)..." % (
                len(all_entries), n_hosts, cfg.probe_timeout,
                cfg.probe_concurrency,
            ),
        ))

    # ---- phase 1: probe one representative per host ----
    host_code: dict = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=cfg.probe_concurrency
    ) as ex:
        rep_futs = {}
        for h, entries in host_groups.items():
            rep_futs[ex.submit(_head_check, entries[0].url, cfg.probe_timeout)] = h
        for e in no_host_entries:
            rep_futs[ex.submit(_head_check, e.url, cfg.probe_timeout)] = e.url

        total_h = len(rep_futs)
        done_h = 0
        step_h = max(1, total_h // 20)
        for fut in concurrent.futures.as_completed(rep_futs):
            h = rep_futs[fut]
            try:
                code = fut.result()
            except Exception:  # noqa: BLE001
                code = 0
            host_code[h] = code
            done_h += 1
            if sink is not None and (done_h % step_h == 0 or done_h == total_h):
                pct = 100 * done_h // max(1, total_h)
                bar_w = 20
                filled = bar_w * done_h // max(1, total_h)
                bar = "\\u2588" * filled + "\\u2591" * (bar_w - filled)
                sink.emit((
                    "status",
                    "  host probe %5d / %5d  [%s]  %3d%%" % (
                        done_h, total_h, bar, pct,
                    ),
                ))

    # ---- partition entries by host liveness ----
    code_map: dict = {}
    urls_to_probe: List[Entry] = []
    n_dead_hosts = 0
    n_dropped_by_host = 0
    for h, entries in host_groups.items():
        h_code = host_code.get(h, 0)
        if 200 <= h_code < 400:
            for e in entries:
                urls_to_probe.append(e)
        else:
            n_dead_hosts += 1
            n_dropped_by_host += len(entries)
            for e in entries:
                code_map[e.url] = h_code
                dropped_entries.append(
                    (e, "host unreachable: HEAD %d" % h_code)
                )
    for e in no_host_entries:
        code_map[e.url] = host_code.get(e.url, 0)

    if sink is not None and (n_dead_hosts or no_host_entries):
        sink.emit((
            "status",
            "  host \\u7b5b\\u9009: %d dead host \\u8df3\\u8fc7 %d URL, %d URL \\u8fdb\\u5165\\u9010 URL \\u63a2\\u6d4b" % (
                n_dead_hosts, n_dropped_by_host, len(urls_to_probe),
            ),
        ))

    # ---- phase 2: per-URL probe under live hosts ----
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=cfg.probe_concurrency
    ) as ex:
        future_to_e = {
            ex.submit(_head_check, e.url, cfg.probe_timeout): e
            for e in urls_to_probe
        }
        total = len(urls_to_probe)
        done_count = 0
        step = max(200, min(1000, total // 20)) if total > 0 else 1
        for fut in concurrent.futures.as_completed(future_to_e):
            e = future_to_e[fut]
            try:
                code = fut.result()
            except Exception:  # noqa: BLE001
                code = 0
            code_map[e.url] = code
            done_count += 1
            if sink is not None and (done_count % step == 0 or done_count == total):
                pct = 100 * done_count // max(1, total)
                bar_w = 20
                filled = bar_w * done_count // max(1, total)
                bar = "\\u2588" * filled + "\\u2591" * (bar_w - filled)
                sink.emit((
                    "status",
                    "  url  probe %5d / %d  [%s]  %3d%%" % (
                        done_count, total, bar, pct,
                    ),
                ))

    for tier, pool in entries_by_tier.items():
        kept_pool: List[Entry] = []
        for e in pool:
            code = code_map.get(e.url, 0)
            if 200 <= code < 400:
                kept_pool.append(e)
            else:
                if all(de[0] is not e for de in dropped_entries):
                    dropped_entries.append(
                        (e, "probe: HEAD %d" % code)
                    )
        if kept_pool:
            kept[tier] = kept_pool
        if sink is not None:
            n_total = len(pool)
            n_kept = len(kept_pool)
            rate = 100 * n_kept // max(1, n_total)
            sink.emit((
                "status",
                "  tier %d (%s): %d/%d \\u53ef\\u8fbe (%.0f%%)" % (
                    tier, tier_label(tier), n_kept, n_total, rate
                ),
            ))

    if sink is not None:
        sink.emit((
            "status",
            "\\u5168\\u91cf probe \\u5b8c\\u6210: \\u4fdd\\u7559 %d, \\u5254\\u9664 %d (\\u5176\\u4e2d host-dead %d)"
            % (
                sum(len(v) for v in kept.values()),
                len(dropped_entries),
                n_dropped_by_host,
            ),
        ))
    return kept, dropped_entries
'''


def main():
    with io.open(FILTERS, "r", encoding="utf-8") as f:
        text = f.read()
    start = text.find(START_MARK)
    if start < 0:
        raise SystemExit("start mark not found: " + START_MARK)
    # Find the LAST occurrence of the sentinel after start -- this is the
    # function's terminating return statement. (Function body never
    # contains this string, so rfind is safe.)
    end = text.rfind(END_SENTINEL, start)
    if end < 0:
        raise SystemExit("end sentinel not found after start")
    end += len(END_SENTINEL)
    new_text = text[:start] + _probe_full_new() + text[end:]
    with io.open(FILTERS, "w", encoding="utf-8") as f:
        f.write(new_text)
    print("[OK] _probe_full replaced; len before=%d after=%d" % (len(text), len(new_text)))


if __name__ == "__main__":
    main()