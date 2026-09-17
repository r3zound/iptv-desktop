# iptv-desktop.py -- main entry point.
#
# Flow:
#   1. Open a Tk progress window (unless --silent flag passed).
#   2. Load source config (multi-source: Collect-IPTV primary + others).
#   3. Fetch each enabled source IN PARALLEL via ThreadPoolExecutor.
#      Each source gets its own budget (15s by default; the window
#      shows progress + status per source).
#   4. Parse each successful source's m3u into List[Entry].
#   5. Merge: URL-level dedup + name-level dedup + tier sort +
#      per-channel cap.
#   6. Write merged playlist to cache/best_sorted.m3u + meta.json.
#   7. Launch PotPlayer with the merged file.
#   8. On total network failure: fall back to last cached merged
#      playlist; launch PotPlayer; notify via window + (optional)
#      MessageBox.
#
# Two UI modes:
#   - Default (GUI): Tk progress window shows fetch + merge progress.
#   - --silent (legacy): no window, no console; MessageBox on error.
#   - --no-window: same as --silent (alias).
#
# Both modes share the same business logic; only the progress sink
# differs.
from __future__ import annotations

import datetime
import os
import queue
import sys
import threading
import time

# Allow `from core import ...` regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from core import cache, fetcher, merge, player, playlist, progress, source  # noqa: E402

OUTPUT_FILENAME = "best_sorted.m3u"
# Wall-clock budget for one source, matching fetcher's worst case:
#   (passes * mirrors * PER_REQUEST_TIMEOUT) + sum(backoff)
#   = (2 * 3 * 4s) + 1s = 25s
# The executor wait below adds headroom on top of this.
PER_SOURCE_TIMEOUT = 25.0


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------- message boxes (silent mode only) -----------------------

def _show_error(title: str, body: str) -> None:
    if sys.platform != "win32":
        print("[ERR]", title, body, file=sys.stderr)
        return
    try:
        import ctypes
        MB_ICONERROR = 0x10
        MB_OK = 0x0
        ctypes.windll.user32.MessageBoxW(0, body, title, MB_OK | MB_ICONERROR)
    except Exception:
        cache.log("%s: %s" % (title, body))


def _show_warning(title: str, body: str) -> None:
    if sys.platform != "win32":
        print("[WARN]", title, body, file=sys.stderr)
        return
    try:
        import ctypes
        MB_ICONWARNING = 0x30
        MB_OK = 0x0
        ctypes.windll.user32.MessageBoxW(0, body, title, MB_OK | MB_ICONWARNING)
    except Exception:
        cache.log("%s: %s" % (title, body))


# ---------------- per-source fetch (with timing) ------------------------

def _fetch_one(src: "source.SourceConfig") -> tuple:
    """Fetch + parse one source. Returns (name, entries_or_None, err, elapsed_ms)."""
    name = src.name
    t0 = time.time()
    try:
        candidates = src.all_candidates()
        data, url = fetcher.fetch_with_fallback(candidates)
        elapsed_ms = int((time.time() - t0) * 1000)
        entries = playlist.parse(data, source=name)
        return name, entries, None, elapsed_ms
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((time.time() - t0) * 1000)
        return name, None, "%s: %s" % (type(e).__name__, e), elapsed_ms


def fetch_all_parallel(
    sources: list, sink, max_workers: int = 4
) -> tuple:
    """Fetch all enabled sources in parallel, emitting progress events.

    Deliberately uses raw DAEMON threads rather than a
    ThreadPoolExecutor:

      - ``socket.getaddrinfo`` has no timeout in Python 3.8, so a slow
        or blackholed DNS server can block a worker for tens of
        seconds regardless of the urllib timeout.
      - ``ThreadPoolExecutor.__exit__`` calls ``shutdown(wait=True)``,
        which JOINS those stuck workers -- so an outer ``wait(timeout)``
        buys nothing and the whole fetch phase drags on long past its
        budget (measured: 128s for a 25s budget).
      - Daemon threads are not joined at interpreter exit, so an
        abandoned DNS lookup can never hold the process open.

    We therefore collect results from a queue with a hard deadline and
    simply abandon whatever has not reported in by then. A source that
    arrives late is not used this run; the cache fallback covers it.

    Returns:
        entries_by_source -- list[list[Entry]] in source order
        errors            -- dict[name -> error_string]
        successful_names  -- list[str]
    """
    entries_by_source = []
    errors = {}
    successful_names = []

    sink.emit(("status", "\u62c9\u53d6\u6e90\u4e2d (\u5e76\u53d1 %d)..." % len(sources)))

    for s in sources:
        sink.emit(("source_start", s.name))

    result_q: "queue.Queue" = queue.Queue()
    workers = max(1, min(max_workers, len(sources)))

    def _worker(cfg):
        try:
            result_q.put(_fetch_one(cfg))
        except Exception as exc:  # noqa: BLE001 -- never let a thread die silent
            result_q.put((cfg.name, None, "worker: %s" % exc, 0))

    pending = list(sources)
    # Cap concurrent workers; the remainder starts as slots free up.
    running = []
    results: dict = {}
    deadline = time.time() + PER_SOURCE_TIMEOUT

    while pending or running:
        while pending and len(running) < workers:
            s = pending.pop(0)
            t = threading.Thread(target=_worker, args=(s,), daemon=True)
            t.start()
            running.append(t)

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            name, entries, err, elapsed_ms = result_q.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            # Reap finished threads so the pool keeps moving.
            running = [t for t in running if t.is_alive()]
            continue

        results[name] = (entries, err, elapsed_ms)
        running = [t for t in running if t.is_alive()]

    # Report in declared order so downstream source-priority is stable.
    for s in sources:
        if s.name in results:
            entries, err, elapsed_ms = results[s.name]
        else:
            entries, err, elapsed_ms = (
                None,
                "fetch timeout (no result within %.0fs)" % PER_SOURCE_TIMEOUT,
                0,
            )
        if entries is not None and len(entries) > 0:
            sink.emit(("source_done", s.name, len(entries), elapsed_ms))
            entries_by_source.append(entries)
            successful_names.append(s.name)
        else:
            sink.emit(("source_fail", s.name, err or "empty"))
            errors[s.name] = err or "empty"

    return entries_by_source, errors, successful_names


# ------------------------------ main --------------------------------------

def main(argv) -> int:
    # Parse CLI flags.
    silent = False
    fast = False       # --fast: skip full HEAD probe (use sample mode)
    offline = False    # --offline: don't fetch any sources, use cache only
    for a in argv:
        if a in ("--silent", "--no-window", "-s"):
            silent = True
        elif a in ("--fast", "--quick"):
            fast = True
        elif a in ("--offline", "--cache-only"):
            offline = True
        elif a in ("-h", "--help"):
            print(
                "usage: iptv-desktop.exe [OPTIONS]\n"
                "\n"
                "OPTIONS:\n"
                "  --silent       no console output, no MessageBox on success\n"
                "  --fast         skip full HEAD probe, use 5s sample probe\n"
                "  --offline      don't fetch any source, use last cached m3u\n"
                "  -h, --help     show this help\n"
            )
            return 0

    # Build sink: default = ConsoleSink (shows progress in cmd window);
    # --silent = NullSink (no output).
    if silent:
        sink = progress._NullSink()
    else:
        sink = progress.ConsoleSink()

    # Always banner so the user knows what happened even in silent mode.
    cache.log(
        "start; silent=%s fast=%s offline=%s" % (silent, fast, offline)
    )

    rc = 0
    try:
        if offline:
            rc = _run_offline(cfg_loader=lambda: source.load_source(),
                              sink=sink)
        else:
            rc = _run(sink, fast=fast)
    finally:
        sink.close()

    # On failure, hold the console open so the user can actually read
    # what went wrong. Without this the cmd window vanishes the moment
    # the process exits -- which users perceive as a "flash crash"
    # even when the tool behaved correctly and merely reported an error.
    #
    # Skipped when:
    #   - the run succeeded (rc == 0)
    #   - --silent was passed (user explicitly wants no interaction)
    #   - stdout is redirected (scripted / piped usage)
    if rc != 0 and not silent and _is_interactive_console():
        try:
            print()
            input("Press Enter to close...")
        except (EOFError, KeyboardInterrupt, OSError):
            pass

    return rc


def _is_interactive_console() -> bool:
    """True when stdin+stdout are attached to a real console.

    False when output is piped/redirected, so the pause never blocks
    automation.
    """
    try:
        if sys.stdin is None or sys.stdout is None:
            return False
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def _run_offline(cfg_loader, sink) -> int:
    """Offline mode: skip fetch entirely, just verify cache + launch
    PotPlayer. Used by --offline."""
    sink.emit(("status", "\u4ec5\u4f7f\u7528\u672c\u5730\u7f13\u5b58..."))
    cfg = cfg_loader()
    return _fallback_to_cache(cfg, sink)


def _run(sink, fast: bool = False) -> int:
    cfg = source.load_source()
    enabled = cfg.enabled_sources()

    sink.set_expected_sources(len(enabled))
    sink.emit(("status", "loading config..."))
    # Show what we already have on disk, so the user can tell at a
    # glance whether a failed fetch is about to lose them anything.
    sink.emit(("status", cache.cache_summary(OUTPUT_FILENAME)))

    # --fast forces probe_mode="sample" for this run, overriding the
    # ini default of "full". 5s probe instead of 1-2 minutes.
    effective_filter_mode = "sample" if fast else None  # None = use ini

    cache.log(
        "start; %d sources enabled: %s; per_channel_cap=%d fast=%s" % (
            len(enabled), [s.name for s in enabled], cfg.per_channel_cap,
            fast,
        )
    )

    if not enabled:
        cache.log("no enabled sources")
        sink.emit(("status", "\u9519\u8bef:\u672a\u542f\u7528\u4efb\u4f55\u6e90"))
        sink.emit(("error", "iptv-desktop -- \u9519\u8bef",
                   "source.ini \u4e2d\u6ca1\u6709\u4efb\u4f55 enabled \u7684\u6e90\u3002\n\n"
                   "\u8bf7\u68c0\u67e5 [sources.*] \u4e2d\u7684 enabled = 1\u3002"))
        return 1

    # -------- parallel fetch + parse --------
    t0 = time.time()
    entries_by_source, errors, successful = fetch_all_parallel(enabled, sink)
    cache.log(
        "fetch+parse done in %.1fs; successful=%s; errors=%s" % (
            time.time() - t0, successful, errors
        )
    )

    if not entries_by_source:
        cache.log("ALL sources failed")
        return _fallback_to_cache(cfg, sink)

    # -------- merge + tier sort + per-channel cap --------
    sink.emit(("status", "\u5408\u5e76\u53bb\u91cd\u4e2d..."))
    filter_cfg = source.load_filter_config()
    if effective_filter_mode is not None:
        # Override probe mode at runtime (--fast flag)
        from core import filters as _filters_mod
        filter_cfg = _filters_mod.FilterConfig(
            url_patterns=filter_cfg.url_patterns,
            probe_mode=effective_filter_mode,
            probe_sample_size=filter_cfg.probe_sample_size,
            probe_timeout=filter_cfg.probe_timeout,
            probe_concurrency=filter_cfg.probe_concurrency,
            probe_min_success_rate=filter_cfg.probe_min_success_rate,
            exclude_non_chinese=filter_cfg.exclude_non_chinese,
            exclude_geo_blocked=filter_cfg.exclude_geo_blocked,
        )
        sink.emit(("status", "  --fast: \u4f7f\u7528\u91c7\u6837\u6a21\u5f0f (5s)"))
    cache.log("filters: %s" % filter_cfg)
    merged = merge.merge(
        entries_by_source,
        per_channel_cap=cfg.per_channel_cap,
        filter_cfg=filter_cfg,
        sink=sink,
    )
    s = merge.stats(merged)
    sink.emit(("merge", len(merged)))
    # Per-tier counts for the lower panel.
    for t, c in sorted(s["by_tier"].items()):
        sink.emit(("tier", int(t), c))
    cache.log(
        "merged: %d entries; by_tier=%s; by_source=%s" % (
            s["total"], s["by_tier"], s["by_source"]
        )
    )

    if not merged:
        cache.log("merged playlist empty")
        return _fallback_to_cache(cfg, sink)

    # -------- serialize + write cache --------
    sink.emit(("status", "\u5199\u5165\u7f13\u5b58..."))
    data = playlist.serialize(
        merged, header="# generated by iptv-desktop at %s" % _now_iso()
    )
    meta = {
        "source_url": ", ".join(successful),
        "fetched_at": _now_iso(),
        "channel_count": len(merged),
        "per_channel_cap": cfg.per_channel_cap,
        "by_tier": s["by_tier"],
        "by_source": s["by_source"],
        "fetch_errors": errors,
    }
    m3u_path = cache.save_m3u(OUTPUT_FILENAME, data, meta)
    cache.log("saved %s (%d bytes, %d entries)" % (
        m3u_path, len(data), len(merged)
    ))

    # -------- launch player --------
    sink.emit(("status", "\u542f\u52a8\u64ad\u653e\u5668..."))
    if not player.launch(m3u_path, cfg.player_path):
        cache.log("PotPlayer not found")
        sink.emit(("status", "\u64ad\u653e\u5668\u672a\u627e\u5230"))
        sink.emit(("error", "iptv-desktop -- \u64ad\u653e\u5668\u672a\u627e\u5230",
                   "\u5df2\u751f\u6210\u6700\u65b0 m3u\uff0c\u4f46\u672a\u627e\u5230 PotPlayer\u3002\n\n"
                   "m3u \u5df2\u4fdd\u5b58\u81f3\uff1a%s\n\n"
                   "\u8bf7\u786e\u8ba4\u5df2\u5b89\u88c5 PotPlayer\uff0c\u6216\u5728 source.ini "
                   "[player] path= \u6307\u5b9a\u8def\u5f84\u540e\u53cc\u51fb\u91cd\u8bd5\u3002" % m3u_path))
        return 2

    sink.emit(("done", m3u_path, len(merged)))
    if errors:
        cache.log("partial success; failed: %s" % errors)
    return 0


def _fallback_to_cache(cfg, sink) -> int:
    cached = cache.load_cached(OUTPUT_FILENAME)
    if not cached:
        cache.log("no cache; aborting")
        sink.emit(("status", "\u62a5\u9519\uff1a\u65e0\u7f13\u5b58\u53ef\u4ee5\u5151\u5e95"))
        sink.emit(("error", "iptv-desktop -- \u62a5\u9519",
                   "\u65e0\u6cd5\u4ece\u4efb\u4f55\u6e90\u62c9\u53d6\u6700\u65b0 m3u\uff0c\u4e14"
                   "\u672c\u5730\u6ca1\u6709\u7f13\u5b58\u3002\n\n"
                   "\u8bf7\u68c0\u67e5\u7f51\u7edc\u3001GitHub \u53ef\u8fbe\u6027\uff0c"
                   "\u6216\u4fee\u6539 source.ini \u540e\u91cd\u8bd5\u3002"))
        return 1
    data, meta = cached
    saved_at = meta.get("saved_at")
    when = (
        datetime.datetime.fromtimestamp(saved_at).isoformat(timespec="seconds")
        if saved_at else "\u672a\u77e5"
    )
    m3u_path = os.path.join(cache.cache_dir(), OUTPUT_FILENAME)
    cache.log("using cached m3u from %s" % when)
    sink.emit(("status", "\u4f7f\u7528\u672c\u5730\u7f13\u5b58 (\u751f\u6210\u4e8e %s)" % when))
    sink.emit(("warning", "iptv-desktop -- \u4f7f\u7528\u672c\u5730\u7f13\u5b58",
               "\u65e0\u6cd5\u8054\u7f51\uff0c\u5df2\u4f7f\u7528\u672c\u5730\u7f13\u5b58\u7684 m3u\u3002\n\n"
               "\u6700\u540e\u6210\u529f\u62c9\u53d6\u65f6\u95f4\uff1a%s\n"
               "\u539f\u59cb\u6e90\uff1a%s\n\n"
               "\u4e0b\u6b21\u8054\u7f51\u53ef\u7528\u65f6\u53cc\u51fb exe \u91cd\u8bd5\u3002" % (
                   when, meta.get("source_url", "\u672a\u77e5")
               )))
    if not player.launch(m3u_path, cfg.player_path):
        sink.emit(("error", "iptv-desktop -- \u64ad\u653e\u5668\u672a\u627e\u5230",
                   "\u672a\u627e\u5230 PotPlayer\u3002"))
        return 2
    sink.emit(("done", m3u_path, meta.get("channel_count", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))