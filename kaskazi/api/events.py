"""Live events for open pages: the assistant's progress, staff replies, queue changes.

In memory, per process: each slot serves its own visitors, and a page reconnects to the live slot
after a release. Events carry no personal data beyond what that page already shows.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

KEEPALIVE_S = 20
STREAM_LIFETIME_S = 240


class Hub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(channel, ())):
            if queue.qsize() < 100:
                queue.put_nowait(event)

    async def stream(self, channels: list[str]) -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for c in channels:
            self._subscribers[c].add(queue)
        try:
            yield "retry: 3000\n\n"
            if not channels:
                yield 'event: hello\ndata: {"signed_in": false}\n\n'
                return
            yield 'event: hello\ndata: {"signed_in": true}\n\n'
            loop = asyncio.get_running_loop()
            ends = loop.time() + STREAM_LIFETIME_S
            while loop.time() < ends:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, default=str)}\n\n"
        finally:
            for c in channels:
                self._subscribers[c].discard(queue)
                if not self._subscribers[c]:
                    self._subscribers.pop(c, None)
