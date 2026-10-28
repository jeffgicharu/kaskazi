"""The release checks, run against the idle slot before any visitor reaches it.

Each check prints one line. A release goes live only if every check passes.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import httpx

from kaskazi.release.journeys import JOURNEYS

Check = Callable[[httpx.AsyncClient, httpx.AsyncClient, str], Awaitable[str]]


class CheckFailed(Exception):
    pass


async def both_report_the_release(
    api: httpx.AsyncClient, gateway: httpx.AsyncClient, release: str
) -> str:
    for name, client, path in (("API", api, "/api/healthz"), ("gateway", gateway, "/healthz")):
        response = await client.get(path)
        if response.status_code != 200 or response.headers.get("x-release") != release:
            raise CheckFailed(
                f"the {name} reports {response.headers.get('x-release')!r}, not {release[:7]}"
            )
        if name == "gateway" and response.json().get("fake_providers"):
            raise CheckFailed("the gateway is running with stand-in providers")
    return "API and gateway both report the release"


async def pinned_models_exist(
    api: httpx.AsyncClient, gateway: httpx.AsyncClient, release: str
) -> str:
    response = await gateway.get(
        "/v1/models", headers={"Authorization": f"Bearer {os.environ['KASKAZI_GATEWAY_TOKEN']}"}
    )
    response.raise_for_status()
    found: dict[str, bool] = response.json()
    missing = [model for model, ok in found.items() if not ok]
    if missing:
        raise CheckFailed("pinned models no longer offered: " + ", ".join(missing))
    return f"all {len(found)} pinned models are offered"


async def api_says_noindex(api: httpx.AsyncClient, gateway: httpx.AsyncClient, release: str) -> str:
    response = await api.get("/api/healthz")
    if response.headers.get("x-robots-tag") != "noindex, nofollow":
        raise CheckFailed("the API's responses lack the noindex header")
    return "the API sends noindex"


CHECKS: list[Check] = [both_report_the_release, pinned_models_exist, api_says_noindex, *JOURNEYS]


async def run_checks(*, api: str, gateway: str, release: str) -> bool:
    ok = True
    async with (
        httpx.AsyncClient(base_url=api, timeout=45) as api_client,
        httpx.AsyncClient(base_url=gateway, timeout=15) as gateway_client,
    ):
        for check in CHECKS:
            try:
                message = await check(api_client, gateway_client, release)
            except Exception as exc:
                ok = False
                print(f"FAIL {check.__name__}: {exc}")
                break
            print(f"ok   {message}")
    return ok
