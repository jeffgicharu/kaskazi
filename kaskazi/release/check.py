"""The release checks, run against the idle slot before any visitor reaches it.

Each check prints one line. A release goes live only if every check passes.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import httpx

from kaskazi.release.golden import ten_golden_questions
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
        if name == "gateway" and response.json().get("routes_override"):
            raise CheckFailed("the gateway is running with another routes file than the release's")
    return "API and gateway both report the release"


async def pinned_models_exist(
    api: httpx.AsyncClient, gateway: httpx.AsyncClient, release: str
) -> str:
    response = await gateway.get(
        "/v1/models", headers={"Authorization": f"Bearer {os.environ['KASKAZI_GATEWAY_TOKEN']}"}
    )
    if response.status_code == 502:
        body = response.json()
        raise CheckFailed(f"the gateway can't reach {body['unreachable']}: {body['reason']}")
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


CHECKS: list[Check] = [
    both_report_the_release,
    pinned_models_exist,
    api_says_noindex,
    *JOURNEYS,
    ten_golden_questions,
]
# With stand-in providers there is no real model to check against a model's list or the
# golden set.
STAND_IN_SKIPS = {"both_report_the_release", "pinned_models_exist", "ten_golden_questions"}


class SessionCookies:
    """Carries the API's session cookies over plain HTTP to 127.0.0.1.

    On the server the API marks its cookies Secure (visitors reach it over HTTPS through nginx),
    and an HTTP client won't send a Secure cookie over plain HTTP. The check talks to the idle
    slot directly, so it keeps the two session cookies itself.
    """

    NAMES = ("kz_copy", "kz_staff")

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def on_request(self, request: httpx.Request) -> None:
        if self.values:
            request.headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.values.items())

    async def on_response(self, response: httpx.Response) -> None:
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            if name in self.NAMES:
                self.values[name] = rest.split(";", 1)[0]


async def run_checks(*, api: str, gateway: str, release: str, stand_in: bool = False) -> bool:
    from kaskazi.release.journeys import CheckCopy, Mode

    Mode.stand_in = stand_in
    CheckCopy.conversation = None
    ok = True
    cookies = SessionCookies()
    async with (
        httpx.AsyncClient(
            base_url=api,
            timeout=60,
            event_hooks={"request": [cookies.on_request], "response": [cookies.on_response]},
        ) as api_client,
        httpx.AsyncClient(base_url=gateway, timeout=15) as gateway_client,
    ):
        for check in CHECKS:
            if stand_in and check.__name__ in STAND_IN_SKIPS:
                print(f"skip {check.__name__} (stand-in providers)")
                continue
            try:
                message = await check(api_client, gateway_client, release)
            except Exception as exc:
                ok = False
                print(f"FAIL {check.__name__}: {type(exc).__name__}: {exc}")
                break
            print(f"ok   {message}")
    return ok
