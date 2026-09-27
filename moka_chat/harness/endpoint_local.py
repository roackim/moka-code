""".local (mDNS) hostname resolution.

``.local`` hosts are rewritten to a routable IPv4 address because
``getaddrinfo`` can hand back a bare IPv6 link-local for them. Resolutions are
cached for the process lifetime and refreshed when a connection fails. Lifted
out of ``endpoint.py``; free functions, no state coupling.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .local (mDNS) hostname resolution
# ---------------------------------------------------------------------------

# https://stackoverflow.com/questions/106179/regular-expression-to-match-hostname-or-ip-address
_HOSTNAME_RE = re.compile(
    r"(?=^.{1,253}$)(^((?!-)[a-zA-Z0-9-]{1,63}(?<!-)\.)+[a-zA-Z]{2,63}$)"
)

# hostname -> IP resolution cache for .local hosts. Resolutions persist for the
# process lifetime and are only refreshed when a connection failure invalidates
# them. This avoids a getent subprocess on every connect while keeping stale
# entries self-healing.
_local_cache: dict[str, Optional[str]] = {}

# Hostnames currently being resolved in a background prewarm thread. Used by the
# UI to show an animated "resolving" indicator in the status bar.
_resolving: set[str] = set()


def _getent_host(hostname: str) -> Optional[str]:
    """Resolve a hostname to an IPv4 address.

    Tries ``socket.getaddrinfo`` in-process first (same libc resolver as the
    shell), then ``getent hosts`` as a fallback. Returns the first IPv4 address,
    or None if unresolvable.
    """
    import socket

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip:
                return ip
    except Exception as e:
        logger.warning("socket.getaddrinfo failed for %s: %s", hostname, e)

    try:
        result = subprocess.run(
            ["getent", "hosts", hostname],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.splitlines()[0].strip() if result.stdout.splitlines() else ""
        return line.split()[0] if line else None
    except Exception as e:
        logger.warning("getent failed for %s: %s", hostname, e)
        return None


def _cached_ip_for(hostname: str) -> Optional[str]:
    """Return the cached IP for hostname, or None if not yet resolved."""
    return _local_cache.get(hostname)


def _resolve_once(hostname: str) -> Optional[str]:
    """Resolve hostname via getent (uncached) and cache the result."""
    ip = _getent_host(hostname)
    if ip:
        _local_cache[hostname] = ip
        logger.info("Resolved %s via getent → %s", hostname, ip)
    else:
        _local_cache.pop(hostname, None)
        logger.warning("getent could not resolve %s", hostname)
    return ip


def _resolve_local_hostname(url: str) -> str:
    """Resolve a ``.local`` (mDNS/Bonjour) hostname to a routable address.

    httpx/OpenAI connect through ``getaddrinfo``, which can return a bare IPv6
    link-local (``fe80::``) address for ``.local`` names. For ``.local`` hosts we
    instead ask ``getent hosts`` (which follows nsswitch and prefers IPv4) and
    swap in the returned address. Cached for the process lifetime; refreshed on
    connection failure. If anything goes wrong, the original URL is returned.
    """
    try:
        hostname = urlsplit(url).hostname
        if not hostname or not _HOSTNAME_RE.match(hostname) or not hostname.endswith(".local"):
            return url

        ip = _cached_ip_for(hostname) or _resolve_once(hostname)
        if not ip:
            return url

        parts = urlsplit(url)
        # Preserve scheme, path, query, fragment — only swap the host.
        netloc = f"{ip}:{parts.port}" if parts.port else ip
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception as e:
        logger.warning("Failed to resolve %s host via getent: %s — using original URL", url, e)
        return url


def invalidate_local_hostname(url: str) -> None:
    """Drop any cached getent resolution for ``url``'s hostname."""
    try:
        hostname = urlsplit(url).hostname
        if hostname:
            _local_cache.pop(hostname, None)
    except Exception:
        pass


def prewarm_local_resolution(url: str) -> None:
    """Kick off ``.local`` resolution for ``url`` in a background thread.

    ``socket.getaddrinfo`` (used for mDNS) can block for a moment. Running it
    off the event loop and populating the cache means the resolved IP is cached
    by the time the user sends a message. Non-``.local`` hosts are no-ops.
    """
    hostname = urlsplit(url).hostname
    if not hostname or not hostname.endswith(".local"):
        return
    if _cached_ip_for(hostname):
        return
    if hostname in _resolving:
        return

    import threading

    _resolving.add(hostname)

    def _resolve():
        try:
            _resolve_once(hostname)
        except Exception as e:
            logger.warning("prewarm resolution failed for %s: %s", hostname, e)
        finally:
            _resolving.discard(hostname)

    threading.Thread(target=_resolve, daemon=True).start()


def _resolve_local_hostname_async(url: str) -> str:
    """Resolve a ``.local`` hostname without blocking the event loop.

    Returns the resolved URL if the address is already cached, otherwise kicks
    off a background resolution and returns the original URL unchanged. Never
    performs blocking I/O on the calling thread.
    """
    hostname = urlsplit(url).hostname
    if not hostname or not hostname.endswith(".local"):
        return url
    ip = _cached_ip_for(hostname)
    if ip:
        parts = urlsplit(url)
        netloc = f"{ip}:{parts.port}" if parts.port else ip
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    prewarm_local_resolution(url)
    return url


async def _resolve_local_hostname_await(url: str) -> str:
    """Resolve a ``.local`` hostname to a routable URL, off the event loop."""
    hostname = urlsplit(url).hostname
    if not hostname or not hostname.endswith(".local"):
        return url
    ip = _cached_ip_for(hostname)
    if not ip:
        ip = await asyncio.to_thread(_resolve_once, hostname)
    if not ip:
        return url
    parts = urlsplit(url)
    netloc = f"{ip}:{parts.port}" if parts.port else ip
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def is_local_resolution_pending(url: str) -> bool:
    """True if ``.local`` resolution for ``url`` is currently in progress."""
    hostname = urlsplit(url).hostname
    if not hostname or not hostname.endswith(".local"):
        return False
    return hostname in _resolving


