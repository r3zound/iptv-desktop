# filters.py -- URL blocklist + tier-pruning probe.
#
# Two filters run between fetch and merge:
#
#   1. URL blocklist (cheap, deterministic):
#      Drop any entry whose URL matches one of the regex patterns in
#      ``source.ini [filters] exclude_url_pattern``. Used to nuke known
#      dead domains (e.g. jmp2.uk short-link service, expired CDN
#      hosts) before they pollute the final playlist.
#
#   2. Per-tier probe (cheap-ish, network):
#      For each tier, sample up to ``probe_sample_size`` entries and
#      HEAD each URL with a short timeout. If the success rate is
#      below ``probe_min_success_rate`` (default 20%), the whole tier
#      is dropped. This protects the user from accidentally selecting
#      a tier that's mostly Geo-blocked or expired.
#
# Both filters are optional and configurable. Setting
# ``probe = 0`` skips the network probe entirely (URL blocklist still
# runs).
from __future__ import annotations

import concurrent.futures
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Iterable, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from .channel_name import classify, normalize, tier_label  # noqa: E402
from .playlist import Entry  # noqa: E402

import re


# CJK Unified Ideographs (basic + ext-A) + CJK Symbols. Matches a
# single Chinese character. Used to detect "this channel name has
# at least one Chinese character" for the exclude_non_chinese filter.
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def has_cjk(text: str) -> bool:
    """Return True if ``text`` contains at least one CJK character."""
    return bool(_CJK_RE.search(text or ""))


# ---------------- defaults baked into source.ini comments ----------------

# Domains / substrings known to return 4xx or be unreachable for IPTV.
# Users can extend this list in source.ini [filters] exclude_url_pattern.
DEFAULT_URL_BLOCKLIST = (
    # Short-link service that 100% returns 400 on direct GET
    r"jmp2\.uk/plu-",
    # Expired or geo-blocked CDN paths observed in 2026-09-17 probe
    r"cdn-globecast\.akamaized\.net",
    r"streamlock\.net",
    # Geo-blocked Chinese IPTV private network (always timed out from
    # public internet in CN)
    r"39\.134\.\d+\.\d+:8080",
    r"39\.134\.\d+\.\d+/dbiptv",
    # Long-dead free hosting platforms (very high 4xx rate)
    r"cdn10jtedge\.indihometv\.com",
    r"srde\.akamaized\.net",
    # --- CCTV-specific dead links observed in 2026-09-17 probe ---
    # CCTV's official overseas CDN; CN mainland gets HTTP 403
    r"cctvnews\.cctv\.com",
    # Third-party relay services that go offline frequently
    r"t\.061899\.xyz",
    r"myip\.pdtvhd\.com",
)


# ---------------- config parsing ----------------------------------------

class FilterConfig:
    """Resolved [filters] section from source.ini."""

    def __init__(
        self,
        url_patterns: List[re.Pattern],
        probe_mode: str,                # "off" | "sample" | "full"
        probe_sample_size: int,
        probe_timeout: float,
        probe_concurrency: int,
        probe_min_success_rate: float,
        exclude_non_chinese: bool = False,
        exclude_geo_blocked: bool = True,
        whitelist: Optional[List[re.Pattern]] = None,
    ) -> None:
        self.url_patterns = url_patterns
        if probe_mode not in ("off", "sample", "full"):
            probe_mode = "full"
        self.probe_mode = probe_mode
        self.probe_sample_size = max(2, probe_sample_size)
        self.probe_timeout = max(0.5, probe_timeout)
        self.probe_concurrency = max(1, probe_concurrency)
        self.probe_min_success_rate = min(1.0, max(0.0, probe_min_success_rate))
        self.exclude_non_chinese = exclude_non_chinese
        self.exclude_geo_blocked = exclude_geo_blocked
        self.whitelist = whitelist or []

    def __repr__(self) -> str:
        return (
            "FilterConfig(patterns=%d, probe=%s sample=%d, "
            "timeout=%.1fs, conc=%d, min_rate=%.2f, "
            "exclude_non_chinese=%s, exclude_geo_blocked=%s, "
            "whitelist=%d)"
            % (
                len(self.url_patterns), self.probe_mode,
                self.probe_sample_size, self.probe_timeout,
                self.probe_concurrency, self.probe_min_success_rate,
                self.exclude_non_chinese, self.exclude_geo_blocked,
                len(self.whitelist),
            )
        )


def _to_float(s: str, default: float) -> float:
    try:
        return float(s.strip())
    except (ValueError, TypeError):
        return default


def _to_int(s: str, default: int) -> int:
    try:
        return int(s.strip())
    except (ValueError, TypeError):
        return default


def _to_bool(s: str, default: bool) -> bool:
    s = s.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def parse_filter_config(cfg) -> FilterConfig:
    """Read [filters] section from a configparser. Returns FilterConfig
    with defaults applied for any missing key."""
    if not cfg.has_section("filters"):
        return _default_config()

    patterns_raw = cfg.get(
        "filters", "exclude_url_pattern", fallback=""
    ).strip()
    user_patterns = [p.strip() for p in patterns_raw.split("|") if p.strip()]

    # Combine user patterns with built-in defaults (user patterns win
    # on first match because regex.match is leftmost).
    all_patterns = list(DEFAULT_URL_BLOCKLIST)
    all_patterns.extend(user_patterns)
    compiled: List[re.Pattern] = []
    for pat in all_patterns:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            # Skip malformed patterns rather than aborting the run.
            continue

    return FilterConfig(
        url_patterns=compiled,
        probe_mode=cfg.get(
            "filters", "probe_mode", fallback="full"
        ).strip().lower() or "full",
        probe_sample_size=_to_int(
            cfg.get("filters", "probe_sample_size", fallback="8"), 8
        ),
        probe_timeout=_to_float(
            cfg.get("filters", "probe_timeout", fallback="1.5"), 1.5
        ),
        probe_concurrency=_to_int(
            cfg.get("filters", "probe_concurrency", fallback="16"), 16
        ),
        probe_min_success_rate=_to_float(
            cfg.get("filters", "probe_min_success_rate", fallback="0.20"),
            0.20,
        ),
        exclude_non_chinese=_to_bool(
            cfg.get("filters", "exclude_non_chinese", fallback="1"), True
        ),
        exclude_geo_blocked=_to_bool(
            cfg.get("filters", "exclude_geo_blocked", fallback="1"), True
        ),
        whitelist=parse_whitelist(
            cfg.get("filters", "whitelist", fallback="")
        ),
    )


def _default_config() -> FilterConfig:
    compiled = []
    for pat in DEFAULT_URL_BLOCKLIST:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            pass
    return FilterConfig(
        url_patterns=compiled,
        probe_mode="full",
        probe_sample_size=8,
        probe_timeout=1.5,
        probe_concurrency=16,
        probe_min_success_rate=0.20,
        exclude_non_chinese=True,
        exclude_geo_blocked=True,
        whitelist=[],
    )


# ---------------- URL blocklist -----------------------------------------

def is_blocked(url: str, patterns: List[re.Pattern]) -> bool:
    """Return True if URL matches any blocklist pattern."""
    if not url or not patterns:
        return False
    for p in patterns:
        if p.search(url):
            return True
    return False


def apply_url_blocklist(
    entries: List[Entry], patterns: List[re.Pattern], sink=None
) -> List[Entry]:
    """Return a new list with blocked URLs removed."""
    if not patterns:
        return entries
    kept = []
    blocked_count = 0
    for e in entries:
        if is_blocked(e.url, patterns):
            blocked_count += 1
            continue
        kept.append(e)
    if sink is not None and blocked_count:
        sink.emit((
            "status",
            "URL \u9ed1\u540d\u5355\u8fc7\u6ee4: \u5254\u9664 %d \u6761"
            % blocked_count,
        ))
    return kept


def exclude_non_chinese(
    entries: List[Entry], sink=None
) -> List[Entry]:
    """Drop entries that classify() places in tier 6 (Foreign / 外文).

    Uses classify() rather than a raw CJK character check so that
    channels like "CCTV1" / "CCTV-4K" / "TVB Jade" / "MTV Taiwan" --
    whose names contain no Chinese ideographs but are still regional
    Chinese-language or recognized Chinese broadcasters -- are NOT
    dropped. Only pure-foreign tier-6 channels are removed.

    Examples:
      "CCTV1"           -> tier 1 -> KEEP
      "CCTV-4K"         -> tier 1 -> KEEP
      "湖南卫视 (1080p)" -> tier 2 -> KEEP
      "TVB Jade"        -> tier 4 -> KEEP
      "TVBS News"       -> tier 4 -> KEEP
      "凤凰中文台"      -> tier 4 -> KEEP
      "Candelaria TV"   -> tier 6 -> DROP
      "TRIVU-TV"        -> tier 6 -> DROP
      "Radio 350"       -> tier 6 -> DROP
      "Nickelodeon Toons"-> tier 6 -> DROP

    Effectively this trims iptv-org's default index (which is ~99%
    foreign / Spanish / Latin American channels) down to the small
    Chinese-relevant subset.
    """
    kept = []
    dropped = 0
    for e in entries:
        if classify(e.display_name, e.group_title) == 6:
            dropped += 1
            continue
        kept.append(e)
    if sink is not None and dropped:
        sink.emit((
            "status",
            "\u8fc7\u6ee4\u975e\u4e2d\u6587\u9891\u9053: \u5254\u9664 %d \u6761"
            % dropped,
        ))
    return kept


# ---------------- [Geo-blocked] filter -------------------------------

# iptv-org and similar sources tag channels that are geo-blocked from
# the user's region by appending "[Geo-blocked]" to the channel name.
# These entries are 100% useless -- the source itself has confirmed the
# stream is unreachable from the user's IP. Filter them out so we
# don't waste probe slots on them.
_GEO_BLOCKED_RE = re.compile(r"\[geo[\s\-_]?blocked\]", re.IGNORECASE)


def has_geo_blocked_marker(name: str) -> bool:
    """Return True if name contains a geo-blocked marker."""
    return bool(_GEO_BLOCKED_RE.search(name or ""))


def exclude_geo_blocked(
    entries: List[Entry], sink=None
) -> List[Entry]:
    """Drop entries whose display name contains '[Geo-blocked]'.

    iptv-org tags a channel "[Geo-blocked]" in its name when the
    source has been confirmed unreachable from the user's region.
    Keeping these entries wastes probe slots and pollutes the final
    playlist with entries that never play.
    """
    kept = []
    dropped = 0
    for e in entries:
        if has_geo_blocked_marker(e.display_name):
            dropped += 1
            continue
        kept.append(e)
    if sink is not None and dropped:
        sink.emit((
            "status",
            "\u8fc7\u6ee4 [Geo-blocked] \u9891\u9053: \u5254\u9664 %d \u6761"
            % dropped,
        ))
    return kept


# ---------------- channel whitelist ------------------------------------

def parse_whitelist(raw: str) -> List[re.Pattern]:
    """Compile the pipe-separated whitelist into regexes.

    Each item is matched case-insensitively against the *normalized*
    channel name (so "CCTV-1" and "CCTV1" both match the pattern
    "CCTV1"). Items are plain regex, which makes both broad and exact
    selections possible:

        CCTV            -> every CCTV channel
        ^CCTV1$         -> exactly CCTV1
        卫视             -> every satellite channel
        湖南|浙江|江苏    -> three provinces
    """
    out: List[re.Pattern] = []
    for item in (raw or "").split("|"):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(re.compile(item, re.IGNORECASE))
        except re.error:
            # Skip malformed patterns instead of aborting the run.
            continue
    return out


def apply_whitelist(
    entries: List[Entry], patterns: List[re.Pattern], sink=None
) -> List[Entry]:
    """Keep only entries whose normalized name matches a whitelist item.

    An empty pattern list means "no whitelist configured" and returns
    the input untouched, so a blank ini value can never wipe the
    playlist by accident.
    """
    if not patterns:
        return entries

    kept = []
    dropped = 0
    for e in entries:
        norm = normalize(e.display_name)
        if any(p.search(norm) for p in patterns):
            kept.append(e)
        else:
            dropped += 1

    if sink is not None:
        sink.emit((
            "status",
            "\u767d\u540d\u5355\u8fc7\u6ee4: \u4fdd\u7559 %d \u6761, "
            "\u5254\u9664 %d \u6761" % (len(kept), dropped),
        ))
    return kept


# ---------------- probe helpers ----------------------------------------

def _head_check(url: str, timeout: float) -> int:
    """Verify URL reachability. Returns HTTP status code (200/206/4xx/5xx)
    or 0 on socket-level failure.

    Uses GET with Range: bytes=0-2047 instead of HEAD because many CDN
    servers used by CN IPTV sources return 405/501 on HEAD but serve
    content fine on GET. Range keeps the request cheap (max 2 KB)
    and follows 302 redirects automatically (urllib default).

    Retry policy (one retry max):
      - Retry only when the first attempt failed *fast* (< 60% of the
        timeout). A fast failure is a transient blip (connection reset,
        DNS hiccup, 5xx) and often succeeds on the second try.
      - Do NOT retry when the first attempt burned most of its budget:
        that means the host is blackholed / dead, and retrying would
        double the worst-case wall time for no benefit. This matters a
        lot for full probes over lists that are mostly dead links.
      - Never retry deterministic 4xx (except 408/425/429).
    """
    deadline_ratio = 0.6
    last_code = 0
    for attempt in (1, 2):
        t0 = time.time()
        req = urllib.request.Request(
            url,
            headers={"Range": "bytes=0-2047", "User-Agent": "iptv-desktop/1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = int(r.status)
                # Drain the body to free the connection (ignored).
                r.read(2048)
                if 200 <= code < 400:
                    return code
                last_code = code
        except urllib.error.HTTPError as e:
            last_code = int(e.code)
            # 4xx is a deterministic answer, don't retry.
            if 400 <= last_code < 500 and last_code not in (408, 425, 429):
                return last_code
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            last_code = 0

        # Decide whether a retry is worth it.
        elapsed = time.time() - t0
        if attempt == 1 and elapsed < timeout * deadline_ratio:
            continue
        break
    return last_code


# ---------------- tier probe -------------------------------------------

def probe_tier(
    entries: List[Entry],
    cfg: FilterConfig,
    rng: Optional[random.Random] = None,
) -> Tuple[bool, int, int, dict]:
    """Sample-probe a list of entries (one tier's worth).

    Returns:
        kept           True if the tier should be kept (pass rate ok)
        sample_size    how many we probed
        success_count  how many returned 200/206
        sample_stats   dict mapping entry -> status code, for logging
    """
    if rng is None:
        rng = random.Random()

    sample_size = min(cfg.probe_sample_size, len(entries))
    sample = rng.sample(entries, sample_size)
    sample_stats: dict = {}

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=cfg.probe_concurrency
    ) as ex:
        future_to_e = {ex.submit(_head_check, e.url, cfg.probe_timeout): e
                       for e in sample}
        for fut in concurrent.futures.as_completed(future_to_e):
            e = future_to_e[fut]
            try:
                code = fut.result()
            except Exception:  # noqa: BLE001
                code = 0
            sample_stats[e.url] = code

    success_count = sum(1 for c in sample_stats.values() if 200 <= c < 400)
    pass_rate = success_count / max(1, sample_size)
    kept = pass_rate >= cfg.probe_min_success_rate

    return kept, sample_size, success_count, sample_stats


def probe_all_tiers(
    entries_by_tier: dict, cfg: FilterConfig, sink=None
) -> Tuple[dict, dict]:
    """Run tier probe on a dict {tier: [entries]}.

    Behavior depends on ``cfg.probe_mode``:
      "full"   -- HEAD every entry; drop the ones that don't return
                  200/206. This is slow but accurate.
      "sample" -- sample N entries per tier; if pass rate is below
                  cfg.probe_min_success_rate, drop the whole tier.
      "off"    -- no probing, return all entries unchanged.

    Returns:
        kept_tiers  -- {tier: [entries]} for entries that passed
        dropped_entries -- list of (entry, reason) for ones dropped
    """
    if cfg.probe_mode == "off":
        return dict(entries_by_tier), {}

    if cfg.probe_mode == "full":
        return _probe_full(entries_by_tier, cfg, sink=sink)

    # default / "sample" -- original tier-sampling behavior
    return _probe_sample(entries_by_tier, cfg, sink=sink)


def _probe_sample(
    entries_by_tier: dict, cfg: FilterConfig, sink=None
) -> Tuple[dict, dict]:
    """Sample N entries per tier, drop tier if pass rate < min_rate."""
    kept: dict = {}
    dropped_entries: list = []
    rng = random.Random(20260917)
    for tier in sorted(entries_by_tier.keys()):
        pool = entries_by_tier[tier]
        if not pool:
            continue
        if sink is not None:
            sink.emit((
                "status",
                "\u62bd\u68c0 tier %d (%s): %d \u4e2a..." % (
                    tier, tier_label(tier), len(pool)
                ),
            ))
        ok, n, succ, stats = probe_tier(pool, cfg, rng)
        pass_rate = succ / max(1, n)
        if ok:
            kept[tier] = pool
            if sink is not None:
                sink.emit((
                    "status",
                    "  tier %d: %d/%d \u53ef\u8fbe (%.0f%%) -- KEEP" % (
                        tier, succ, n, pass_rate * 100
                    ),
                ))
        else:
            # Whole tier dropped -- mark all entries as dropped.
            label = ("probe: tier %d pass %.0f%%" % (
                tier, pass_rate * 100
            ))
            for e in pool:
                dropped_entries.append((e, label))
            if sink is not None:
                sink.emit((
                    "status",
                    "  tier %d: %d/%d \u53ef\u8fbe (%.0f%%) -- DROP" % (
                        tier, succ, n, pass_rate * 100
                    ),
                ))
    return kept, dropped_entries


def _probe_full(
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
            "\u5168\u91cf HEAD probe: %d URL -> %d host (\u8d85\u65f6 %.1fs, \u5e76\u53d1 %d)..." % (
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
                bar = "\u2588" * filled + "\u2591" * (bar_w - filled)
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
        # h_code == 0 means the GET never completed (DNS fail, connect
        # timeout, TCP reset). Anything else -- including 4xx/5xx --
        # proves the host answered, so its URLs are still worth probing
        # individually. Tightening this to `200..400` would mark 404
        # hosts as dead and drop every URL under them, even though some
        # of those URLs target a different path that may be 200.
        if h_code != 0:
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
            "  host \u7b5b\u9009: %d dead host \u8df3\u8fc7 %d URL, %d URL \u8fdb\u5165\u9010 URL \u63a2\u6d4b" % (
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
                bar = "\u2588" * filled + "\u2591" * (bar_w - filled)
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
                "  tier %d (%s): %d/%d \u53ef\u8fbe (%.0f%%)" % (
                    tier, tier_label(tier), n_kept, n_total, rate
                ),
            ))

    if sink is not None:
        sink.emit((
            "status",
            "\u5168\u91cf probe \u5b8c\u6210: \u4fdd\u7559 %d, \u5254\u9664 %d (\u5176\u4e2d host-dead %d)"
            % (
                sum(len(v) for v in kept.values()),
                len(dropped_entries),
                n_dropped_by_host,
            ),
        ))
    return kept, dropped_entries
