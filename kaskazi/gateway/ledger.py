"""The spending ledger: hard caps on what the demo may spend, enforced before every call.

Before a call, the gateway reserves the most it could cost. The reservation is checked against
every cap at once, under a lock, so two requests arriving together can't both squeeze under a
cap that only one of them fits. After the call the reservation is settled at the real cost. A
reservation that is never settled (the process died mid-call) stops counting after 10 minutes.

Days and months are in Nairobi time (UTC+3, no daylight saving), so the caps reset at midnight
where the visitors are.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import asyncpg

from kaskazi.llm.contract import Allowance, BudgetContext, GatewayRefusal

NAIROBI = timezone(timedelta(hours=3), "EAT")
LOCK_ID = 0x7370656E  # "spen"
STALE_RESERVATION = "10 minutes"


@dataclass(frozen=True)
class Caps:
    site_day_usd: float = 1.30
    site_month_usd: float = 40.0
    visitor_day_usd: float = 0.04
    address_day_usd: float = 0.12


def nairobi_day(now: datetime | None = None) -> tuple[date, date]:
    local = (now or datetime.now(UTC)).astimezone(NAIROBI)
    return local.date(), local.date().replace(day=1)


# Built from a module constant, never from input.
_TOTALS = f"""
    WITH live AS (
        SELECT day, sandbox_id, ip_hash, exempt,
               CASE WHEN state = 'settled' THEN cost_usd ELSE reserved_usd END AS amount
        FROM gateway.spend
        WHERE month = $1 AND (state = 'settled' OR at > now() - interval '{STALE_RESERVATION}')
    )
    SELECT
        coalesce(sum(amount) FILTER (WHERE day = $2), 0) AS day_total,
        coalesce(sum(amount), 0) AS month_total,
        coalesce(sum(amount) FILTER (WHERE day = $2 AND sandbox_id = $3 AND NOT exempt), 0)
            AS visitor_total,
        coalesce(sum(amount) FILTER (WHERE day = $2 AND ip_hash = $4 AND NOT exempt), 0)
            AS address_total
    FROM live
"""  # noqa: S608


class Ledger:
    def __init__(self, pool: asyncpg.Pool, caps: Caps, *, ai_enabled: bool = True) -> None:
        self.pool = pool
        self.caps = caps
        self.ai_enabled = ai_enabled

    async def allowance(self, budget: BudgetContext) -> Allowance:
        day, month = nairobi_day()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_TOTALS, month, day, _uuid(budget.sandbox_id), budget.ip_hash)
        return self._allowance(row, budget)

    def _allowance(self, row: asyncpg.Record, budget: BudgetContext) -> Allowance:
        exempt = budget.exempt_from_visitor_caps
        return Allowance(
            ai_enabled=self.ai_enabled,
            site_day_left_usd=round(self.caps.site_day_usd - float(row["day_total"]), 6),
            site_month_left_usd=round(self.caps.site_month_usd - float(row["month_total"]), 6),
            visitor_left_usd=None
            if exempt or budget.sandbox_id is None
            else round(self.caps.visitor_day_usd - float(row["visitor_total"]), 6),
            address_left_usd=None
            if exempt or budget.ip_hash is None
            else round(self.caps.address_day_usd - float(row["address_total"]), 6),
        )

    async def reserve(
        self, budget: BudgetContext, *, purpose: str, route: str, request_id: str, amount_usd: float
    ) -> int | GatewayRefusal:
        """Reserves `amount_usd` if every cap still has room for it, and returns the reservation's
        id; otherwise returns the cap that would be broken."""

        if not self.ai_enabled:
            return GatewayRefusal.AI_DISABLED
        day, month = nairobi_day()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_ID)
            row = await conn.fetchrow(_TOTALS, month, day, _uuid(budget.sandbox_id), budget.ip_hash)
            left = self._allowance(row, budget)
            checks = [
                (left.site_month_left_usd, GatewayRefusal.SITE_MONTH_CAP),
                (left.site_day_left_usd, GatewayRefusal.SITE_DAY_CAP),
                (left.visitor_left_usd, GatewayRefusal.VISITOR_CAP),
                (left.address_left_usd, GatewayRefusal.ADDRESS_CAP),
            ]
            for room, refusal in checks:
                if room is not None and amount_usd > room:
                    return refusal
            return int(
                await conn.fetchval(
                    """INSERT INTO gateway.spend
                           (day, month, sandbox_id, ip_hash, exempt, purpose, route, request_id,
                            state, reserved_usd)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'reserved', $9) RETURNING id""",
                    day,
                    month,
                    _uuid(budget.sandbox_id),
                    budget.ip_hash,
                    budget.exempt_from_visitor_caps,
                    purpose,
                    route,
                    request_id,
                    Decimal(str(round(amount_usd, 6))),
                )
            )

    async def settle(self, reservation_id: int, cost_usd: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE gateway.spend SET state = 'settled', cost_usd = $2 WHERE id = $1",
                reservation_id,
                Decimal(str(round(cost_usd, 6))),
            )


def _uuid(value: str | None) -> object:
    import uuid

    return uuid.UUID(value) if value else None
