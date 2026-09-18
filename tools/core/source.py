# source.py -- parse source.ini to get a list of upstream m3u sources
# plus merge / player options.
#
# Layout:
#   [sources.collect-iptv]
#   url     = primary URL
#   mirrors = pipe-separated fallback list (optional)
#   enabled = 1 (default)
#
#   [sources.iptv-org]
#   url = https://iptv-org.github.io/iptv/index.m3u
#   ; mirrors = ...
#   enabled = 1
#
#   [sources.guovin]      ; example: user's own fork
#   url = https://github.com/<user>/iptv-api/releases/download/playlist-latest/result.m3u
#   enabled = 0           ; disabled by default; user must fill in their fork URL
#
#   [merge]
#   per_channel_cap = 3
#
#   [player]
#   path =                ; leave empty to auto-detect PotPlayer
from __future__ import annotations

import configparser
import os
import sys
from typing import List, Optional


class SourceConfig:
    """Resolved config: one named upstream source.

    Mirrors use ``{ext}`` placeholder which is substituted with the
    active format (m3u / m3u8) at fetch time.
    """

    def __init__(
        self,
        name: str,
        url: str,
        fmt: str,
        mirrors: List[str],
        enabled: bool,
    ) -> None:
        self.name = name
        self.url = url
        self.format = fmt
        self.mirrors = mirrors
        self.enabled = enabled

    def all_candidates(self) -> List[str]:
        ext = self.format
        out = [self.url]
        for m in self.mirrors:
            if "{ext}" in m:
                m = m.replace("{ext}", ext)
            out.append(m)
        return out


class Config:
    """Top-level resolved config."""

    def __init__(
        self,
        sources: List[SourceConfig],
        per_channel_cap: int,
        player_path: Optional[str],
    ) -> None:
        self.sources = sources
        self.per_channel_cap = per_channel_cap
        self.player_path = player_path

    def enabled_sources(self) -> List[SourceConfig]:
        return [s for s in self.sources if s.enabled]


# ----------------------------- defaults -----------------------------------

DEFAULT_SOURCES = {
    "collect-iptv": {
        "url": (
            "https://raw.githubusercontent.com/zilong7728/Collect-IPTV/"
            "main/best_sorted.m3u"
        ),
        "format": "m3u",
        "mirrors": (
            "https://cdn.jsdelivr.net/gh/zilong7728/Collect-IPTV@main/"
            "best_sorted.{ext}"
            "|https://zilong7728.github.io/Collect-IPTV/best_sorted.{ext}"
        ),
        "enabled": True,
    },
    "iptv-org": {
        "url": "https://iptv-org.github.io/iptv/index.m3u",
        "format": "m3u",
        "mirrors": "",
        "enabled": True,
    },
    "guovin-fork": {
        # User fills in their fork URL after running Guovin's Actions
        # workflow. Disabled by default.
        "url": "",
        "format": "m3u",
        "mirrors": "",
        "enabled": False,
    },
    "bjzhou-fork": {
        # bjzhou/iptv-collector does not publish a public m3u. Disabled.
        "url": "",
        "format": "m3u",
        "mirrors": "",
        "enabled": False,
    },
    "tmxk2020-fork": {
        # tmxk2020/ITV does not publish a public m3u. Disabled.
        "url": "",
        "format": "m3u",
        "mirrors": "",
        "enabled": False,
    },
}

DEFAULT_PER_CHANNEL_CAP = 3


def _ini_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "source.ini")


def _to_bool(s: str, default: bool) -> bool:
    s = s.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _build_one(name: str, cfg: configparser.ConfigParser) -> SourceConfig:
    sec = "sources." + name
    defaults = DEFAULT_SOURCES.get(name, {})
    if cfg.has_section(sec):
        url = cfg.get(sec, "url", fallback=defaults.get("url", "")).strip()
        fmt = cfg.get(
            sec, "format", fallback=defaults.get("format", "m3u")
        ).strip().lower()
        if fmt not in ("m3u", "m3u8"):
            fmt = "m3u"
        mirrors_raw = cfg.get(
            sec, "mirrors", fallback=defaults.get("mirrors", "")
        ).strip()
        mirrors = [m.strip() for m in mirrors_raw.split("|") if m.strip()]
        enabled = _to_bool(
            cfg.get(sec, "enabled", fallback=str(defaults.get("enabled", False))),
            defaults.get("enabled", False),
        )
    else:
        url = defaults.get("url", "")
        fmt = defaults.get("format", "m3u")
        mirrors_raw = defaults.get("mirrors", "")
        mirrors = [m.strip() for m in mirrors_raw.split("|") if m.strip()]
        enabled = defaults.get("enabled", False)
    return SourceConfig(
        name=name, url=url, fmt=fmt, mirrors=mirrors, enabled=enabled
    )


def load_source() -> Config:
    """Load source.ini and return a resolved Config.

    Missing file / missing keys / bad format -- all fall back to the
    baked-in defaults. The tool must run on a fresh portable folder
    with nothing but the exe + source.ini.
    """
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(_ini_path(), encoding="utf-8")

    sources: List[SourceConfig] = []
    # Preserve DEFAULT_SOURCES ordering so output is deterministic.
    for name in DEFAULT_SOURCES.keys():
        sources.append(_build_one(name, cfg))

    per_channel_cap = DEFAULT_PER_CHANNEL_CAP
    if cfg.has_section("merge"):
        try:
            v = cfg.get("merge", "per_channel_cap", fallback=str(per_channel_cap))
            per_channel_cap = int(v.strip())
            if per_channel_cap < 0:
                per_channel_cap = 0
        except (ValueError, TypeError):
            pass

    player_path: Optional[str] = None
    if cfg.has_section("player"):
        p = cfg.get("player", "path", fallback="").strip()
        if p:
            player_path = p

    return Config(
        sources=sources,
        per_channel_cap=per_channel_cap,
        player_path=player_path,
    )


def load_filter_config():
    """Read [filters] section from source.ini.

    Imports local to keep source.py dependency-light (filters.py
    imports re/concurrent which are otherwise unused here).
    """
    import configparser as _cp
    from . import filters as _filters  # local import to avoid cycle
    cfg = _cp.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(_ini_path(), encoding="utf-8")
    return _filters.parse_filter_config(cfg)