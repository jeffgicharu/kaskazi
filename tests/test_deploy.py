"""The server files: the rules they must keep, checked on every change."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"


def test_the_csp_allows_exactly_the_unavailable_pages_style() -> None:
    html = (DEPLOY / "unavailable.html").read_text()
    style = re.search(r"<style>(.*?)</style>", html, re.S)
    assert style is not None
    digest = base64.b64encode(hashlib.sha256(style.group(1).encode()).digest()).decode()
    headers = (DEPLOY / "security-headers.conf").read_text()
    assert f"'sha256-{digest}'" in headers
    assert "'unsafe-inline'" not in headers


def test_every_static_response_is_noindex() -> None:
    assert (
        'X-Robots-Tag "noindex, nofollow" always' in (DEPLOY / "security-headers.conf").read_text()
    )
    assert (
        '<meta name="robots" content="noindex, nofollow">'
        in (DEPLOY / "unavailable.html").read_text()
    )


def test_nginx_has_no_regex_under_a_prefix_and_exact_stream_locations() -> None:
    conf = (DEPLOY / "nginx.conf").read_text()
    blocks = re.findall(r"location \^~ [^{]+\{([^}]*)\}", conf)
    assert all("location ~" not in b for b in blocks)
    for path in ("/api/stream", "/api/console/stream", "/api/chat", "/api/documents"):
        assert f"location = {path} {{" in conf
    for exact in re.findall(r"location = /api/(?:console/)?stream \{([^}]*)\}", conf):
        assert "proxy_buffering off;" in exact
    assert "error_page 502 503 504 =503 /unavailable.html;" in conf
    # Cloudflare replaces an origin's 502 and 504, so nginx's own failures go out as 503.
    assert "error_page 502 503 504 @api_unavailable;" not in conf
    assert conf.count("error_page 502 503 504 =503 @api_unavailable;") == 5
    assert "/var/log/nginx/kaskazi.access.log" in conf


@pytest.mark.parametrize("unit", ["kaskazi-api@.service", "kaskazi-gateway@.service"])
def test_units_carry_the_servers_protections(unit: str) -> None:
    text = (DEPLOY / unit).read_text()
    for line in (
        "MemorySwapMax=0",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "PrivateTmp=true",
        "NoNewPrivileges=true",
        "CapabilityBoundingSet=\n",
        "RestrictAddressFamilies=",
        "MemoryDenyWriteExecute=true",
        "SystemCallArchitectures=native",
        "IPAddressDeny=any",
    ):
        assert line in text, line
    assert re.search(r"^MemoryMax=\d+M$", text, re.M)


def test_only_the_gateway_reaches_outside() -> None:
    api = (DEPLOY / "kaskazi-api@.service").read_text()
    gateway = (DEPLOY / "kaskazi-gateway@.service").read_text()
    assert re.findall(r"^IPAddressAllow=(.*)$", api, re.M) == ["localhost"]
    assert "160.79.104.0/23" in gateway and "read on 29 October 2026" in gateway
    # The server resolves names through 8.8.8.8, not systemd-resolved on 127.0.0.53.
    assert "IPAddressAllow=8.8.8.8" in gateway.splitlines()


def test_activation_refuses_anything_but_a_real_release_folder() -> None:
    script = (DEPLOY / "activate.sh").read_text()
    assert "=~ ^[0-9a-f]{40}$" in script
    assert '[[ ! -d "$release" || -L "$release" ]]' in script
    assert "-perm /022" in script and "readlink -f" in script
    assert script.index("flock -n 9") < script.index("nginx -t")


def test_activation_keeps_only_this_release_and_the_one_before() -> None:
    script = (DEPLOY / "activate.sh").read_text()
    assert "KEEP_RELEASES" not in script  # no count: releases that never went live go too
    assert 'previous="${live_release##*/}"' in script
    assert '"$old" != "$sha" && "$old" != "$previous"' in script
