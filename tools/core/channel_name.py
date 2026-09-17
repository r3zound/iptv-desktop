# channel_name.py -- normalize channel display names and classify
# them into the 6-tier priority scheme.
#
# Normalize rules:
#   - "CCTV-1" / "CCTV 1" / "CCTV1综合" / "央1" / "CCTV1 HD" -> CCTV1
#   - "北京卫视HD" / "北京卫视高清" -> 北京卫视
#   - Drop "(备用)" / "[V2]" / trailing whitespace
#   - Preserve everything else as-is.
#
# Classification tiers (lower = higher priority in playlist order):
#   1 = CCTV
#   2 = province satellite (卫视频道)
#   3 = hot local (热门地方台)
#   4 = HK / Macau / Taiwan
#   5 = other Chinese
#   6 = foreign / unknown
from __future__ import annotations

import re
from typing import Iterable, List, Set, Tuple


# ----------------------------- normalize ----------------------------------

_BRACKET_TAIL_RE = re.compile(
    r'[\(\[【\{]\s*[^\)\]】\}\s]+\s*[\)\]】\}]\s*$'
)
_RES_TAG_RE = re.compile(
    r'\s*(?:HD|FHD|UHD|4K|高清|超清|标清|4KHDR|8K|HEVC|H\.?265)'
    r'\s*$',
    re.IGNORECASE,
)
_TRAILING_DASH_RE = re.compile(r'[\s\-_]+$')
_CCTV_RE = re.compile(
    r'^(?:CCTV|央视|中央|央)\s*[-－—]?\s*0*(\d{1,2})'
    r'(?P<plus>[＋\+])?'
    r'(?P<suffix>.*)$',
    re.IGNORECASE,
)


def _strip_decorations(s: str) -> str:
    """Drop trailing brackets, resolution tags, trailing dashes."""
    s = _BRACKET_TAIL_RE.sub('', s)
    s = _RES_TAG_RE.sub('', s)
    s = _TRAILING_DASH_RE.sub('', s)
    return s.strip()


def normalize(name: str) -> str:
    """Return the canonical display name.

    Returns the input unchanged if no pattern matches (so foreign
    channels survive untouched). Empty string if input is empty.
    """
    if not name:
        return ""
    s = _strip_decorations(name).strip()
    if not s:
        return ""

    # CCTV pattern (highest priority -- catch before generic).
    m = _CCTV_RE.match(s)
    if m:
        digits = m.group(1)
        plus = m.group("plus")
        if plus:
            return "CCTV%s+" % digits
        return "CCTV%s" % digits

    # Province satellite: "北京卫视" / "湖南卫视高清" -> "北京卫视"
    # Only if input already contains 卫视; otherwise leave alone.
    if "卫视" in s:
        # Strip anything after the first 卫视.
        s = s.split("卫视", 1)[0] + "卫视"

    # Catch common English aliases like "TVB-Jade", "TVB-Pearl" -> "TVB"
    # by keeping only the prefix up to the first - or space.
    if "-" in s or " " in s:
        # Only apply if the prefix looks like a brand (uppercase acronym).
        prefix = re.split(r'[-_\s]', s, 1)[0]
        if len(prefix) <= 6 and prefix.upper() == prefix and prefix.isalpha():
            s = prefix

    return s


# --------------------------- resolution scoring ----------------------------

_RES_SCORES = (
    (re.compile(r'\b(?:4K|2160p|UHD)\b', re.I), 4),
    (re.compile(r'\b(?:1080p|FHD|全2K)\b', re.I), 3),
    (re.compile(r'\b(?:720p|HD|高清)\b', re.I), 2),
)


def resolution_score(text: str) -> int:
    """Return 0..4 -- higher = better resolution.

    Looks at both display name and URL; first match wins.
    """
    if not text:
        return 0
    for pat, score in _RES_SCORES:
        if pat.search(text):
            return score
    return 1  # baseline SD


# ----------------------------- classification -----------------------------

# 31 province satellite channels (the official 31 省 + 直辖市/自治区).
_PROVINCE_SATELLITE: Set[str] = {
    "北京卫视", "上海卫视", "天津卫视", "重庆卫视",
    "河北卫视", "山西卫视", "内蒙古卫视",
    "辽宁卫视", "吉林卫视", "黑龙江卫视",
    "江苏卫视", "浙江卫视", "安徽卫视", "福建卫视", "江西卫视", "山东卫视",
    "河南卫视", "湖北卫视", "湖南卫视", "广东卫视",
    "广西卫视", "海南卫视", "四川卫视", "贵州卫视", "云南卫视",
    "陕西卫视", "甘肃卫视", "青海卫视", "宁夏卫视",
    "新疆卫视", "西藏卫视",
}

# A handful of popular non-satellite local channels (市/地面频道).
_HOT_LOCAL: Set[str] = {
    "湖南经视", "湖南都市", "湖南娱乐",
    "北京文艺", "北京科教", "北京影视",
    "上海新闻综合", "上海娱乐",
    "广州新闻", "深圳卫视", "深圳都市",
    "成都新闻综合", "杭州新闻",
}

# HK / Macau / Taiwan keywords. CJK = substring match OK (no word
# boundary issue for Chinese). English acronyms must use word
# boundary -- see _HK_MO_TW_EN below.
_HK_MO_TW_CJK: Set[str] = {
    "凤凰", "TVB", "翡翠", "明珠", "本港", "互动", "星河",
    "华娱", "新城", "澳门", "澳亚", "澳视",
    "中天", "东森", "TVBS", "三立", "民视", "年代", "八大",
    "壹电视", "非凡", "JET", "纬来", "台视", "华视", "公视",
}
_HK_MO_TW_EN = re.compile(r'\b(?:TVB|Jade|Pearl|RTHK|ViuTV|VIUTV)\b', re.I)


def _is_hk_mo_tw(name: str, group: str) -> bool:
    hay = (name or "") + " " + (group or "")
    for kw in _HK_MO_TW_CJK:
        if kw in hay:
            return True
    if _HK_MO_TW_EN.search(hay):
        return True
    return False


_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')


def _has_cjk(name: str, group: str) -> bool:
    return bool(_CJK_RE.search((name or "") + " " + (group or "")))


def classify(name: str, group: str = "") -> int:
    """Return 1..6 priority tier for the channel (lower = higher)."""
    norm = normalize(name)
    if not norm:
        return 6

    # Tier 1: CCTV.
    if norm.startswith("CCTV"):
        return 1

    # Tier 2: province satellite.
    if norm in _PROVINCE_SATELLITE:
        return 2
    # Some sources say "卫视-北京" / "北京 卫视" -- normalize then check.
    # (Already normalized above.)

    # Tier 3: hot local.
    if norm in _HOT_LOCAL:
        return 3

    # Tier 4: HK/Macau/Taiwan.
    if _is_hk_mo_tw(name, group):
        return 4

    # Tier 5 vs 6: based on whether the name contains CJK.
    if _has_cjk(name, group):
        return 5
    return 6


def tier_label(tier: int) -> str:
    return {
        1: "央视频道",
        2: "卫视频道",
        3: "热门地方",
        4: "港澳台",
        5: "其他中文",
        6: "外文/其他",
    }.get(tier, "其他")


# ------------------------------- dedup -------------------------------------

def merge_same_name(entries: List["Entry"]) -> List["Entry"]:  # noqa: F821
    """Not implemented here -- see merge.py. Imported as a thin alias
    to keep channel_name.py dependency-free."""
    return entries


__all__ = [
    "normalize",
    "classify",
    "tier_label",
    "resolution_score",
]