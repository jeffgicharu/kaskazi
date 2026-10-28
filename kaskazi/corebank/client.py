"""The tools' client for the core-banking service.

It speaks HTTP like any client of a remote system would; only the transport is in-process.
Swapping `transport` for a real network transport is all it would take to point it elsewhere.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx


class CoreBankError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class CoreBankUnavailable(Exception):
    pass


class CoreBank:
    def __init__(self, transport: httpx.AsyncBaseTransport, *, timeout_s: float = 5.0) -> None:
        self._http = httpx.AsyncClient(
            transport=transport, base_url="http://corebank.internal", timeout=timeout_s
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(
        self,
        method: str,
        path: str,
        *,
        sandbox_id: uuid.UUID,
        customer_id: uuid.UUID,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"X-Kaskazi-Copy": str(sandbox_id), "X-Kaskazi-Customer": str(customer_id)}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self._http.request(
                method, path, json=json, params=params, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise CoreBankUnavailable("the core banking system timed out") from exc
        if response.status_code >= 500:
            raise CoreBankUnavailable(f"the core banking system answered {response.status_code}")
        body = response.json()
        if response.status_code >= 400:
            if isinstance(body, dict) and "type" in body:
                raise CoreBankError(response.status_code, body["type"], body["title"])
            raise CoreBankError(response.status_code, "invalid_request", str(body))
        return body
