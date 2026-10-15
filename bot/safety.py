"""Reject direct links that would make Lavalink fetch an internal address."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


def is_http_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(ip.is_global) and not ip.is_multicast


def host_is_disallowed(host: str) -> bool:
    """True for loopback, private, link-local, and Railway-internal names."""

    cleaned = host.strip().lower().rstrip(".")
    if not cleaned:
        return True
    if cleaned == "localhost" or cleaned.endswith((".localhost", ".local", ".internal")):
        return True
    if cleaned == "metadata.google.internal" or cleaned.endswith(".railway.internal"):
        return True
    try:
        ip = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    return not _ip_is_public(ip)


async def url_is_allowed(url: str) -> bool:
    """Allow public http(s) media links. Block private and internal targets."""

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname
    if host is None or host_is_disallowed(host):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _ip_is_public(ip):
            return False
    return True
