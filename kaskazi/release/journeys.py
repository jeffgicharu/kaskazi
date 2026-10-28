"""The customer and staff journeys the release check walks in a throwaway check copy.

Each journey takes the API client, the gateway client and the release sha, and returns a line
describing what it proved, or raises.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

Journey = Callable[[httpx.AsyncClient, httpx.AsyncClient, str], Awaitable[str]]

JOURNEYS: list[Journey] = []
