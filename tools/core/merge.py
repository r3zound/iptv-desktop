# merge.py -- multi-source m3u dedup, tier sorting, per-channel cap.
#
# Workflow:
#   1. URL-level dedup: same URL across sources = keep one (first seen).
#   2. Channel-name dedup: same normalized name = collapse to group;
#      prefer the entry from the source with the highest priority
#      (Collect-IPTV > iptv-org > others) AND the best resolution_score.
#   3. Classify every entry into a 6-tier bucket.
#   4. Within each tier, sort by:
#      - resolution_score desc
#      - source priority (Collect-IPTV first)
#      - name (Chinese pinyin would be nicer, but stdlib has no pinyin;
#        fall back to Unicode codepoint order -- stable enough)
#   5. Per-channel cap: keep at most ``per_channel_cap`` entries per
#      normalized name (default 3).
#
# Returns a fresh list of Entry objects; inputs are not mutated.
from __future__ import annotations

from typing import Iterable, List, Optional

from .channel_name import classify, normalize, resolution_score, tier_label
from .filters import (
    FilterConfig,
    apply_url_blocklist,
    apply_whitelist,
    exclude_geo_blocked,
    exclude_non_chinese,
    probe_all_tiers,
)
from .playlist import Entry

import re

# Source priority -- lower number = preferred.
# iptv-org intentionally outranks collect-iptv for CCTV/卫视:
#   - iptv-org URLs are mostly real CDNs (gmxw.7766.org, cztv.com, ...)
#   - collect-iptv URLs lean on relay services (t.061899.xyz) that
#     flap frequently and now sit on the URL blocklist.
#   - For non-CCTV channels, the priority is less important because
#     both sources rate the entry by URL-domain quality (see
#     _URL_QUALITY_BONUS below).
SOURCE_PRIORITY = {
    "iptv-org": 0,
    "collect-iptv": 1,
}

# Pattern used to extract a numeric channel number from CCTV names.
_CCTV_NUM_RE = re.compile(r"CCTV\s*-?\s*0*(\d{1,2})")

# URL-domain quality scoring. When multiple URLs share the same
# normalized channel name (e.g. "CCTV1" appears 4 times across
# sources), per_channel_cap picks the top N. These constants bias
# the sort so stable CDNs outrank relay / naked-IP hosts even when
# both happen to be live at probe time.
_TRUSTED_DOMAINS = (
    "gmxw.7766.org",         # popular CN IPTV mirror CDN
    "ali-m-l.cztv.com",      # Alibaba CDN (Zhejiang IPTV)
    "live.264788.xyz",       # community CDN
    "74.91.26.218",          # stable mirror (CCTV)
    "aliyuncs.com",
    "alicdn.com",
    "myqcloud.com",
    "tencentyun.com",
    "cdn-go.cn",
)
_LOW_QUALITY_DOMAINS = (
    "t.061899.xyz",          # relay service -- already in blocklist
    "myip.pdtvhd.com",       # relay service
    "cctvnews.cctv.com",     # overseas CCTV CDN -- blocklisted
    "112.30.73.119",         # China Telecom IPTV private network
    "39.134.",               # China Telecom IPTV private network
    "101.66.198.207",        # China Telecom IPTV private network
    "120.76.248.139",        # China Telecom IPTV private network
    "222.169.85.8",          # China Telecom IPTV private network
)


def _url_quality_bonus(url: str) -> int:
    """Return -2 / 0 / +2 based on the URL host reputation.

    Called from the per-channel sort so that for a single channel name
    (e.g. "CCTV1") with several candidate URLs, the most reliable
    CDN wins the top slots within per_channel_cap.
    """
    host = ""
    try:
        # Cheap: find "://" then take until the next "/" or ":".
        if "://" in url:
            tail = url.split("://", 1)[1]
            host = tail.split("/", 1)[0].split(":", 1)[0]
    except Exception:
        return 0
    if not host:
        return 0
    for d in _TRUSTED_DOMAINS:
        if host == d or host.endswith("." + d):
            return 2
    for d in _LOW_QUALITY_DOMAINS:
        if host == d or host.endswith("." + d):
            return -2
    return 0


def _cctv_sort_key(name: str) -> tuple:
    """Sort key for CCTV channels: (major, sub) -- lower wins.

    Goal: CCTV1, CCTV2, ... CCTV17 in numeric order, with variants
    grouped after their parent number:

      CCTV-4K        -> (0, -1)   -- 4K special edition goes first
      CCTV1          -> (1, 0)
      CCTV2          -> (2, 0)
      ...
      CCTV5          -> (5, 0)
      CCTV5+         -> (5, 1)    -- + sub-channel right after parent
      CCTV6          -> (6, 0)
      CCTV-4 Europe  -> (4, 2)    -- region variants right after parent
      CCTV-4 America -> (4, 3)    -- ordered by suffix text
      CCTV 致富/纪录  -> (13, 9)   -- specialized CCTV channels at end
      CCTV电影频道   -> (6, 9)    -- "电影" -> grouped after CCTV6
      CCTV科教       -> (99, 0)   -- unknown -> bottom of tier 1

    Anything that doesn't start with "CCTV" gets (99, 0) which sorts
    after every numbered CCTV (this path is only hit inside tier 1).
    """
    s = name.strip()
    if not s.upper().startswith("CCTV"):
        return (99, 0, s)

    # 4K special: CCTV-4K / CCTV 4K / CCTV4K
    if "4K" in s.upper() and "CCTV" in s.upper():
        return (0, -1, s)

    m = _CCTV_NUM_RE.match(s)
    if not m:
        # Unrecognized CCTV variant -- bottom of tier 1.
        return (99, 0, s)

    major = int(m.group(1))
    tail = s[m.end():].strip()  # everything after the digits

    # Bare "CCTV5" or "CCTV-5": sub = 0
    if not tail or tail in ("+", "＋", "-", "—"):
        sub_main = 1 if "+" in tail or "＋" in tail else 0
        return (major, sub_main, s)

    # Sub-channel: starts with + or ＋
    if tail[0] in "+＋":
        return (major, 1, s)

    # Region/special variant: grouped by suffix alphabetical order.
    # Examples: "CCTV-4 Europe", "CCTV-4 America", "CCTV-6 电影"
    sub = 2
    return (major, sub, s)


def _src_priority(src: str) -> int:
    return SOURCE_PRIORITY.get(src, 99)


def _entry_sort_key(e: Entry) -> tuple:
    """Sort key: tier asc, then tier-specific sub-order, then
    resolution desc, then URL quality bonus, then source priority,
    then display name.

    Sub-order by tier:
      1 (CCTV)      : numeric CCTV sort key (CCTV-4K first, then 1..17)
      2 (卫视)       : alphabetical by province name
      3 (热门地方)   : alphabetical
      4 (港澳台)     : alphabetical
      5 (其他中文)   : alphabetical
      6 (外文)       : alphabetical
    """
    tier = classify(e.display_name, e.group_title)
    rscore = resolution_score(e.display_name + " " + e.url)
    sprio = _src_priority(e.source)
    name = e.display_name
    uq = _url_quality_bonus(e.url)

    if tier == 1:
        sub = _cctv_sort_key(name)
    else:
        sub = (name,)

    return (tier, sub, -rscore, -uq, sprio, name)


def merge(
    entries_by_source: Iterable[List[Entry]],
    per_channel_cap: int = 3,
    prefer_source_order: Optional[List[str]] = None,
    filter_cfg: Optional[FilterConfig] = None,
    sink=None,
) -> List[Entry]:
    """Merge entries from N sources into one deduplicated list.

    ``per_channel_cap``: max number of URLs kept per normalized channel
    name. 0 or negative = no cap.

    ``prefer_source_order``: optional override of SOURCE_PRIORITY; the
    source name appearing earlier wins ties.

    ``filter_cfg``: when provided, applies URL blocklist (cheap) and
    tier-pruning probe (network) before merging. When None, no
    filtering.

    ``sink``: optional progress sink for filter/prune messages.
    """
    src_order = prefer_source_order or list(SOURCE_PRIORITY.keys())

    # ------- Step 0: flatten + URL blocklist (cheap) -------------
    all_entries: List[Entry] = []
    for src in entries_by_source:
        all_entries.extend(src)
    if filter_cfg is not None and filter_cfg.url_patterns:
        all_entries = apply_url_blocklist(
            all_entries, filter_cfg.url_patterns, sink=sink
        )
    # ------- Step 0.5: exclude non-Chinese channels (cheap) ----
    # Done BEFORE URL dedup so we don't waste dedup work on entries
    # that will be thrown out anyway. Done BEFORE probe so we don't
    # waste HEAD requests on names that won't make the cut.
    if filter_cfg is not None and filter_cfg.exclude_non_chinese:
        all_entries = exclude_non_chinese(all_entries, sink=sink)
    # ------- Step 0.6: drop [Geo-blocked] markers (cheap) --------
    if filter_cfg is not None and filter_cfg.exclude_geo_blocked:
        all_entries = exclude_geo_blocked(all_entries, sink=sink)
    # ------- Step 0.7: channel whitelist (cheap) -----------------
    # Applied last of the cheap filters so it narrows an already-clean
    # list. An empty whitelist is a no-op (never wipes the playlist).
    if filter_cfg is not None and filter_cfg.whitelist:
        all_entries = apply_whitelist(
            all_entries, filter_cfg.whitelist, sink=sink
        )

    # ------- Step 1+2: dedup by URL and by normalized name --------
    # Two passes because the same channel may appear with different
    # URL forms in different sources (e.g. different CDN). We keep all
    # distinct URLs per channel name, but within a channel name we cap.

    by_name: dict = {}  # normalized_name -> list[Entry]
    seen_urls: set = set()

    for e in all_entries:
        if not e.is_valid:
            continue
        norm = normalize(e.display_name)
        if not norm:
            continue

        # URL-level dedup: skip exact duplicate URLs entirely.
        if e.url in seen_urls:
            continue
        seen_urls.add(e.url)

        by_name.setdefault(norm, []).append(e)

    # ------- Step 2.5: tier-pruning probe (per tier) -------------
    # Bucket by tier before merging so we can drop low-success tiers.
    by_tier: dict = {}
    for name, group in by_name.items():
        canonical_tier = classify(group[0].display_name, group[0].group_title)
        by_tier.setdefault(canonical_tier, []).extend(group)

    if filter_cfg is not None and filter_cfg.probe_mode != "off":
        by_tier, _dropped = probe_all_tiers(by_tier, filter_cfg, sink=sink)

    # ------- Step 3: classify + tier -------------------------------
    # Re-sort within each tier group: prefer the source with higher
    # priority AND higher resolution_score AND higher URL quality.
    # Apply per-channel cap.
    flat: List[Entry] = []
    for tier, group in by_tier.items():
        group.sort(
            key=lambda e: (
                _src_priority(e.source),
                -resolution_score(e.display_name + " " + e.url),
                -_url_quality_bonus(e.url),
            )
        )
        # Apply per-channel cap. We group by normalized name so each
        # channel's variants stay together.
        per_channel_groups: dict = {}
        for e in group:
            n = normalize(e.display_name)
            per_channel_groups.setdefault(n, []).append(e)
        label = tier_label(tier)
        for n, ch_group in per_channel_groups.items():
            if per_channel_cap > 0 and len(ch_group) > per_channel_cap:
                ch_group = ch_group[:per_channel_cap]
            for e in ch_group:
                e.group_title = label
                flat.append(e)

    # ------- Step 4: sort by tier ----------------------------------
    flat.sort(key=_entry_sort_key)

    return flat


def stats(entries: List[Entry]) -> dict:
    """Summary stats for the merged playlist."""
    tiers = {}
    sources = {}
    for e in entries:
        t = classify(e.display_name, e.group_title)
        tiers[t] = tiers.get(t, 0) + 1
        sources[e.source] = sources.get(e.source, 0) + 1
    return {
        "total": len(entries),
        "by_tier": tiers,
        "by_source": sources,
    }