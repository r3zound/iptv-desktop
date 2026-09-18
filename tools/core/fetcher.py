# fetcher.py -- download best_sorted.m3u from GitHub via 3-tier mirror
# fallback. Standard library only (urllib), Python 3.8 / Win7 compatible.
#
# Tier 1: raw.githubusercontent.com          (fastest, often blocked in CN)
# Tier 2: cdn.jsdelivr.net                   (CDN-backed, very reliable)
# Tier 3: zilong7728.github.io               (GitHub Pages, slowest but most open)
#
# Each tier has its own per-request timeout (5s connect+read). If all
# tiers fail, the caller falls back to the local cache.
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

# Per-request timeout: connect + read combined. 4s is generous for a
# small m3u; on a healthy link these return in <1s.
PER_REQUEST_TIMEOUT = 4.0

# Min size: a valid #EXTM3U + at least one #EXTINF line is >100 bytes.
# Reject anything smaller as a corrupt response.
MIN_VALID_SIZE = 100

# Exponential backoff between full passes over the candidate list.
# A transient blip (DNS hiccup, momentary firewall reset) usually
# clears within a second or two; a hard block does not.
#
# Deliberately ONE extra pass: each pass costs up to
# len(candidates) * PER_REQUEST_TIMEOUT, and the outer per-source
# wait budget in iptv-desktop.py is sized for exactly this. Adding
# more passes makes a hard-blocked source take tens of seconds for
# no benefit.
RETRY_BACKOFF_SECONDS = (1.0,)


class FetchError(Exception):
    """All mirrors failed; the caller should consult the cache."""


def _http_get(url: str, timeout: float) -> bytes:
    """Single GET with a hard timeout. Returns bytes or raises.

    We deliberately do NOT retry inside this function -- the outer loop
    handles tier fallback and one extra retry per tier would double the
    worst-case latency (3 tiers * 2 * 5s = 30s) without much benefit.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "iptv-desktop/1.0",
            "Accept": "*/*",
        },
    )
    try:
        # Set a default timeout on the socket too -- urllib's timeout
        # parameter only covers the connect phase on some platforms.
        socket.setdefaulttimeout(timeout)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    finally:
        socket.setdefaulttimeout(None)
    return data


def fetch_with_fallback(
    candidates: List[str],
    timeout: float = PER_REQUEST_TIMEOUT,
    retries: Optional[int] = None,
) -> Tuple[bytes, str]:
    """Try each URL in order; return (data, url_that_succeeded).

    Retries the whole candidate list on transient failures with
    exponential backoff (1s, then 3s). A transient failure means a
    timeout / connection reset / 5xx -- i.e. something that often
    clears on a second attempt. Hard failures (404, DNS NXDOMAIN) are
    still retried because the mirror list is ordered by preference and
    a later pass may reach a different mirror.

    ``retries``: number of extra passes beyond the first. Defaults to
    ``len(RETRY_BACKOFF_SECONDS)`` so behaviour follows the backoff
    table.

    Raises FetchError if every pass over every candidate fails.
    """
    if retries is None:
        retries = len(RETRY_BACKOFF_SECONDS)

    last_err = "unknown"
    total_passes = retries + 1
    # A pass that consumed most of its theoretical budget means the
    # host is blackholed (or DNS is hanging -- socket.getaddrinfo has
    # no timeout in Python 3.8). Retrying would just double a wait
    # that is already long, so we only retry when the pass failed
    # FAST, which is the signature of a transient blip.
    budget = max(0.5, timeout * max(1, len(candidates)))
    fast_fail_threshold = budget * 0.5

    for attempt in range(total_passes):
        if attempt > 0:
            delay = RETRY_BACKOFF_SECONDS[min(
                attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1
            )]
            time.sleep(delay)

        pass_started = time.time()
        for url in candidates:
            try:
                data = _http_get(url, timeout=timeout)
            except urllib.error.HTTPError as e:
                last_err = "HTTP %d for %s" % (e.code, url)
                continue
            except urllib.error.URLError as e:
                # DNS failure, connection refused, SSL handshake timeout, etc.
                reason = getattr(e, "reason", e)
                last_err = "%s for %s: %s" % (
                    type(reason).__name__, url, reason
                )
                continue
            except (socket.timeout, TimeoutError):
                last_err = "timeout for %s" % url
                continue
            except Exception as e:  # noqa: BLE001 -- last-resort safety net
                last_err = "%s for %s: %s" % (type(e).__name__, url, e)
                continue

            if len(data) < MIN_VALID_SIZE:
                last_err = "response too small (%d bytes) from %s" % (
                    len(data), url
                )
                continue
            if not data.lstrip().startswith(b"#EXTM3U"):
                last_err = "not a valid m3u (no #EXTM3U header) from %s" % url
                continue
            return data, url

        pass_elapsed = time.time() - pass_started
        if attempt + 1 < total_passes and pass_elapsed < fast_fail_threshold:
            continue  # transient blip -- worth one more pass
        break

    raise FetchError(
        "all %d mirrors failed after %d pass(es); last error: %s" % (
            len(candidates), attempt + 1, last_err
        )
    )
