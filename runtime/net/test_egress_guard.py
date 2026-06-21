"""Tests for the SSRF egress guard.

Run:
    .venv-win314/Scripts/python.exe -m pytest runtime/net/test_egress_guard.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # type: ignore  # noqa: E402

from runtime.net import egress_guard as eg  # type: ignore  # noqa: E402


def _fake_resolve(ip: str):
    """Return a getaddrinfo stub that resolves any host to `ip`."""
    def _resolver(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (ip, port or 0))]
    return _resolver


# ── scheme allowlist ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "data:text/plain;base64,QQ==",
    "gopher://example.com",
    "ssh://example.com",
])
def test_non_http_schemes_blocked(url):
    with pytest.raises(eg.EgressBlocked):
        eg.assert_safe_url(url)


def test_no_host_blocked():
    with pytest.raises(eg.EgressBlocked):
        eg.assert_safe_url("http:///nohost")


# ── IP-literal hosts ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://127.0.0.1:8080/",                       # loopback
    "http://10.0.0.5/",                             # RFC1918
    "http://192.168.1.1/",                          # RFC1918
    "http://172.16.0.1/",                           # RFC1918
    "http://[::1]/",                                # IPv6 loopback
    "http://0.0.0.0/",                              # unspecified
    "http://2130706433/",                           # decimal form of 127.0.0.1
])
def test_private_and_metadata_ip_literals_blocked(url):
    with pytest.raises(eg.EgressBlocked):
        eg.assert_safe_url(url)


def test_public_ip_literal_allowed():
    eg.assert_safe_url("https://1.1.1.1/")  # no raise


def test_loopback_allowed_when_opted_in():
    eg.assert_safe_url("http://127.0.0.1:8642/", allow_loopback=True)  # no raise


def test_loopback_opt_in_still_blocks_metadata():
    with pytest.raises(eg.EgressBlocked):
        eg.assert_safe_url("http://169.254.169.254/", allow_loopback=True)


# ── hostname resolution ──────────────────────────────────────────────────────
def test_hostname_resolving_to_public_ip_allowed(monkeypatch):
    monkeypatch.setattr(eg.socket, "getaddrinfo", _fake_resolve("93.184.216.34"))
    eg.assert_safe_url("https://example.com/feed.xml")  # no raise


def test_hostname_resolving_to_private_ip_blocked(monkeypatch):
    # Classic DNS-based SSRF: attacker domain resolves to an internal address.
    monkeypatch.setattr(eg.socket, "getaddrinfo", _fake_resolve("10.1.2.3"))
    with pytest.raises(eg.EgressBlocked):
        eg.assert_safe_url("https://evil.example.com/")


def test_hostname_resolving_to_metadata_blocked(monkeypatch):
    monkeypatch.setattr(eg.socket, "getaddrinfo", _fake_resolve("169.254.169.254"))
    with pytest.raises(eg.EgressBlocked):
        eg.assert_safe_url("https://rebind.example.com/")


def test_unresolvable_host_blocked(monkeypatch):
    def _boom(*a, **k):
        raise eg.socket.gaierror("no such host")
    monkeypatch.setattr(eg.socket, "getaddrinfo", _boom)
    with pytest.raises(eg.EgressBlocked):
        eg.assert_safe_url("https://nonexistent.invalid/")


# ── safe_urlopen guards ──────────────────────────────────────────────────────
def test_safe_urlopen_requires_timeout():
    with pytest.raises(eg.EgressBlocked):
        eg.safe_urlopen("https://example.com", timeout=0)


def test_safe_urlopen_validates_before_opening(monkeypatch):
    # Even if a (mocked) opener would succeed, a blocked URL must never open.
    opened = {"called": False}

    class _Opener:
        def open(self, *a, **k):
            opened["called"] = True
            raise AssertionError("opener must not be reached for a blocked URL")

    monkeypatch.setattr(eg, "build_safe_opener", lambda **k: _Opener())
    with pytest.raises(eg.EgressBlocked):
        eg.safe_urlopen("file:///etc/passwd", timeout=5)
    assert opened["called"] is False
