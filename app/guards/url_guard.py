# What: decides whether a URL is safe for the app to download (SSRF protection).
# Why: if the app fetched any URL a user (or a web page) gave it, an attacker could make it read
#      internal addresses like http://127.0.0.1 or cloud metadata at http://169.254.169.254.
#      We only allow http(s) URLs whose host resolves to public IP addresses.
# Used by: app/services/web_fetch.py (checked again for every redirect), later the
#          fetch_job_posting tool.

import ipaddress
import socket
from urllib.parse import urlsplit

from app.utils.errors import GuardBlockedError

ALLOWED_SCHEMES = {"http", "https"}


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve_host(host: str) -> list[str]:
    """Return all IP addresses a host name resolves to (IP literals return themselves)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise GuardBlockedError(
            f"Cannot resolve {host}", user_message="I couldn't find that website."
        ) from exc
    return sorted({info[4][0] for info in infos})


def validate_url(url: str) -> str:
    """Check that `url` is safe to fetch.

    Rules: scheme is http or https, there is a host name, and every IP address the host
    resolves to is public (not private, loopback, link-local, multicast or reserved).

    Returns:
        The URL, unchanged, if it is safe.

    Raises:
        GuardBlockedError: the URL is not allowed.
    """
    blocked = GuardBlockedError(
        f"Blocked URL: {url}",
        user_message="I can't open that link. Please paste the job text instead.",
    )
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.hostname:
        raise blocked

    for address in resolve_host(parts.hostname):
        # Strip an IPv6 zone like "fe80::1%en0" before parsing.
        if not _is_public(ipaddress.ip_address(address.split("%")[0])):
            raise blocked
    return url.strip()
