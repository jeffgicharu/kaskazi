"""The API's client for its slot's model gateway."""

from __future__ import annotations

import httpx

from kaskazi.llm.contract import (
    Allowance,
    BudgetContext,
    EmbedRequest,
    EmbedResult,
    GatewayError,
    GatewayRefusal,
    GenerateRequest,
    GenerateResult,
)


class GatewayClient:
    def __init__(
        self, base_url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(45.0, connect=2.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def generate(self, request: GenerateRequest) -> GenerateResult | GatewayError:
        try:
            response = await self._http.post("/v1/generate", content=request.model_dump_json())
        except httpx.HTTPError as exc:
            return GatewayError(
                refusal=GatewayRefusal.ALL_PROVIDERS_FAILED,
                detail=f"gateway unreachable: {type(exc).__name__}",
            )
        if response.status_code == 200:
            return GenerateResult.model_validate_json(response.content)
        try:
            return GatewayError.model_validate_json(response.content)
        except ValueError:
            return GatewayError(
                refusal=GatewayRefusal.ALL_PROVIDERS_FAILED,
                detail=f"gateway answered {response.status_code}",
            )

    async def embed(self, request: EmbedRequest) -> EmbedResult | GatewayError:
        try:
            response = await self._http.post(
                "/v1/embed", content=request.model_dump_json(), timeout=8.0
            )
        except httpx.HTTPError as exc:
            return GatewayError(
                refusal=GatewayRefusal.ALL_PROVIDERS_FAILED, detail=type(exc).__name__
            )
        if response.status_code == 200:
            return EmbedResult.model_validate_json(response.content)
        try:
            return GatewayError.model_validate_json(response.content)
        except ValueError:
            return GatewayError(
                refusal=GatewayRefusal.ALL_PROVIDERS_FAILED, detail=str(response.status_code)
            )

    async def allowance(self, budget: BudgetContext) -> Allowance | None:
        try:
            response = await self._http.post(
                "/v1/allowance", content=budget.model_dump_json(), timeout=3.0
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        return Allowance.model_validate_json(response.content)
