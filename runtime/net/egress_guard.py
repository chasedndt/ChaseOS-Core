"""Central outbound-HTTP egress guard for ChaseOS (SSRF defense).

Every connector/adapter that fetches an operator- or content-supplied URL should
go through :func:`safe_urlopen` instead of calling ``urllib.request.urlopen``
directly. The guard enforces, on the initial request and on every redirect hop:

- **scheme allowlist** — only ``http`` / ``https`` (blocks ``file:``, ``ftp:``,
  ``gopher:``, ``data:``, ``ssh:``, …);
- **private/loopback/link-local/metadata IP block** — the host is DNS-resolved
  and rejected if *any* resolved address is loopback, private (RFC1918), CGNAT,
  link-local (incl. the ``169.254.169.254`` cloud-metadata address), unique-local
  IPv6 (``fc00::/7``), reserved, or multicast;
- **mandatory timeout** and **bounded response read** (anti-DoS).

``allow_loopback=True`` is for internal services that are *meant* to be local
(n8n local, CDP, the runtime gateways) — it permits loopback but still blocks
other private/link-local/metadata ranges.

Residual risk: this resolves-then-opens, so a DNS-rebinding attacker controlling
an authoritative server could still race the second resolution. That is a far
narrower window than the current "no validation at all"; IP-pinning is a future
enhancement. The guard is stdlib-only.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 MB hard cap on any single response body
_CLOUD_METADATA_IPS = frozenset({"169.254.169.254", "100.100.100.200", "fd00:ec2::254"})


class EgressBlocked(Exception):
    """Raised when a URL is rejected by the egress policy (fail-closed)."""


def _ip_is_disallowed(ip_text: str, *, allow_loopback: bool) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True  # unparseable address → reject
    if ip_text in _CLOUD_METADATA_IPS:
        return True
    if ip.is_loopback:
        return not allow_loopback
    return (
        ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(url: str, *, allow_loopback: bool = False) -> None:
    """Raise :class:`EgressBlocked` if ``url`` violates the egress policy.

    Validates scheme, then resolves the host and rejects if *any* resolved IP is
    in a disallowed range. Safe to call before opening a connection and on every
    redirect hop.
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise EgressBlocked(f"scheme not allowed: {scheme!r} (only http/https) — {url!r}")
    host = parts.hostname
    if not host:
        raise EgressBlocked(f"URL has no host: {url!r}")

    # A bare IP literal in the URL — check it directly (also catches decimal/hex
    # forms once normalized by ip_address).
    try:
        literal = ipaddress.ip_address(host)
        if _ip_is_disallowed(str(literal), allow_loopback=allow_loopback):
            raise EgressBlocked(f"host IP not allowed: {host!r} — {url!r}")
        return
    except ValueError:
        pass  # not a literal; resolve by name below

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if scheme == "https" else 80))
    except socket.gaierror as exc:
        raise EgressBlocked(f"host did not resolve: {host!r} ({exc})") from exc

    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise EgressBlocked(f"host resolved to no addresses: {host!r}")
    for ip_text in resolved:
        if _ip_is_disallowed(ip_text, allow_loopback=allow_loopback):
            raise EgressBlocked(
                f"host {host!r} resolved to a disallowed address {ip_text!r} — {url!r}"
            )


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate the target of every redirect so a 30x can't bounce to an
    internal/metadata host."""

    def __init__(self, *, allow_loopback: bool) -> None:
        super().__init__()
        self._allow_loopback = allow_loopback

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        assert_safe_url(newurl, allow_loopback=self._allow_loopback)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_safe_opener(*, allow_loopback: bool = False) -> urllib.request.OpenerDirector:
    """An opener whose redirect handler re-checks every hop against the policy."""
    return urllib.request.build_opener(_ValidatingRedirectHandler(allow_loopback=allow_loopback))


def safe_urlopen(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict | None = None,
    method: str | None = None,
    timeout: float = 30.0,
    allow_loopback: bool = False,
):
    """SSRF-guarded ``urlopen``.

    Validates the URL (and every redirect) against the egress policy, enforces a
    mandatory timeout, and returns the open response. Callers read it as usual
    (prefer :func:`safe_read` to bound the body). Raises :class:`EgressBlocked`
    on policy violation; network/HTTP errors propagate as ``urllib.error``.
    """
    if timeout is None or timeout <= 0:
        raise EgressBlocked("a positive timeout is required for outbound requests")
    assert_safe_url(url, allow_loopback=allow_loopback)
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    opener = build_safe_opener(allow_loopback=allow_loopback)
    return opener.open(req, timeout=timeout)


def safe_read(response, *, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    """Read at most ``max_bytes`` from a response body (anti-DoS)."""
    return response.read(max_bytes + 1)[:max_bytes]


def safe_fetch_bytes(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = 30.0,
    allow_loopback: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[bytes, dict]:
    """Convenience: guarded GET returning ``(body_bytes, response_headers)``."""
    with safe_urlopen(url, headers=headers, timeout=timeout, allow_loopback=allow_loopback) as resp:
        body = safe_read(resp, max_bytes=max_bytes)
        return body, dict(resp.headers)
