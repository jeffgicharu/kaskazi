"""The pretend core-banking service: accounts, cards, loans, transfers and complaints.

It stands in for the system of record a real bank would already have, and is built like one: a
separate service with its own contract, called over HTTP by the assistant's tools. Here the
"network" is an in-process transport, and nginx never routes to it. Every request names the
visitor's copy and the customer; nothing can reach another customer's accounts. Writes carry an
idempotency key, so a retried confirmation never moves money twice.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

import asyncpg
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kaskazi.corebank import rules
from kaskazi.db.pool import visitor_tx


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class Context(BaseModel):
    sandbox_id: uuid.UUID
    customer_id: uuid.UUID
    idempotency_key: str | None


def context(
    x_kaskazi_copy: Annotated[uuid.UUID, Header()],
    x_kaskazi_customer: Annotated[uuid.UUID, Header()],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Context:
    return Context(
        sandbox_id=x_kaskazi_copy, customer_id=x_kaskazi_customer, idempotency_key=idempotency_key
    )


Ctx = Annotated[Context, Depends(context)]


class BlockCard(BaseModel):
    mode: Literal["temporary", "lost_or_stolen"]


class Replacement(BaseModel):
    branch_code: str = Field(pattern=r"^\d{3}$")


class LoanRequest(BaseModel):
    amount: int | None = Field(default=None, ge=1)
    term_months: int | None = Field(default=None, ge=1, le=120)


class EligibilityRequest(LoanRequest):
    # A what-if with the customer's own figures, in place of what the bank has on record.
    net_monthly_pay: int | None = Field(default=None, ge=0, le=10_000_000)
    other_monthly_repayments: int | None = Field(default=None, ge=0, le=10_000_000)


class OwnTransfer(BaseModel):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class MobileMoneyTransfer(BaseModel):
    from_account_id: uuid.UUID
    recipient_id: uuid.UUID | None = None
    phone: str | None = Field(default=None, pattern=r"^0[17]\d{8}$")
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class NewComplaint(BaseModel):
    category: Literal["card", "transfer", "loan", "fees", "service", "app", "other"]
    summary: str = Field(min_length=10, max_length=1000)


def create_app(pool: asyncpg.Pool) -> FastAPI:
    app = FastAPI(
        title="Kaskazi core banking (sample)", version="1.0.0", docs_url=None, redoc_url=None
    )
    app.state.pool = pool
    app.state.faults = {"delay_ms": 0, "fail_next": 0}

    @app.exception_handler(Problem)
    async def problem(_: Request, exc: Problem) -> JSONResponse:
        return JSONResponse(
            {"type": exc.code, "title": exc.message, "status": exc.status},
            status_code=exc.status,
            media_type="application/problem+json",
        )

    @app.middleware("http")
    async def faults(request: Request, call_next: Any) -> Any:
        f = app.state.faults
        if f["delay_ms"]:
            await asyncio.sleep(f["delay_ms"] / 1000)
        if f["fail_next"]:
            f["fail_next"] -= 1
            return JSONResponse(
                {
                    "type": "unavailable",
                    "title": "The core banking system is unavailable.",
                    "status": 503,
                },
                status_code=503,
            )
        return await call_next(request)

    def tx(ctx: Context) -> Any:
        return visitor_tx(app.state.pool, ctx.sandbox_id)

    async def remembered(conn: asyncpg.Connection, ctx: Context) -> dict[str, Any] | None:
        if not ctx.idempotency_key:
            raise Problem(400, "idempotency_key_required", "Writes need an Idempotency-Key header.")
        row = await conn.fetchrow(
            "SELECT result FROM corebank.idempotency WHERE sandbox_id = $1 AND key = $2",
            ctx.sandbox_id,
            ctx.idempotency_key,
        )
        return dict(row["result"]) if row else None

    async def remember(
        conn: asyncpg.Connection, ctx: Context, result: dict[str, Any]
    ) -> dict[str, Any]:
        await conn.execute(
            "INSERT INTO corebank.idempotency (sandbox_id, key, result) VALUES ($1, $2, $3)",
            ctx.sandbox_id,
            ctx.idempotency_key,
            result,
        )
        return result

    # --- Customer and accounts ------------------------------------------------------------------

    @app.get("/v1/customer")
    async def customer(ctx: Ctx) -> dict[str, Any]:
        async with tx(ctx) as conn:
            row = await conn.fetchrow(
                "SELECT full_name, first_name, segment, branch_code, employer, net_pay, basic_pay, salary_day "
                "FROM corebank.customers WHERE id = $1",
                ctx.customer_id,
            )
        if row is None:
            raise Problem(404, "not_found", "No such customer.")
        branch = next(
            (b["name"] for b in rules.bank()["branches"] if b["code"] == row["branch_code"]), None
        )
        return {**_plain(dict(row)), "home_branch": branch}

    @app.get("/v1/accounts")
    async def accounts(ctx: Ctx) -> list[dict[str, Any]]:
        async with tx(ctx) as conn:
            rows = await conn.fetch(
                "SELECT id, product, number, currency, balance, status FROM corebank.accounts "
                "WHERE customer_id = $1 ORDER BY opened_on",
                ctx.customer_id,
            )
        return [_plain(dict(r)) for r in rows]

    @app.get("/v1/accounts/{account_id}/transactions")
    async def transactions(
        ctx: Ctx, account_id: uuid.UUID, limit: Annotated[int, Query(ge=1, le=50)] = 10
    ) -> dict[str, Any]:
        async with tx(ctx) as conn:
            await _own_account(conn, ctx, account_id)
            rows = await conn.fetch(
                "SELECT at, description, amount, balance_after, reference, channel FROM corebank.transactions "
                "WHERE account_id = $1 ORDER BY at DESC, id DESC LIMIT $2",
                account_id,
                limit,
            )
        return {"account_id": str(account_id), "transactions": [_plain(dict(r)) for r in rows]}

    # --- Cards ----------------------------------------------------------------------------------

    @app.get("/v1/cards")
    async def cards(ctx: Ctx) -> list[dict[str, Any]]:
        async with tx(ctx) as conn:
            rows = await conn.fetch(
                """SELECT c.id, c.kind, c.last4, c.status, c.expires, c.credit_limit, c.replacement_ordered,
                          a.product AS account_product
                   FROM corebank.cards c JOIN corebank.accounts a ON a.id = c.account_id
                   WHERE a.customer_id = $1 ORDER BY c.kind, c.last4""",
                ctx.customer_id,
            )
        return [_plain(dict(r)) for r in rows]

    @app.post("/v1/cards/{card_id}/block")
    async def block_card(ctx: Ctx, card_id: uuid.UUID, body: BlockCard) -> dict[str, Any]:
        async with tx(ctx) as conn:
            if (done := await remembered(conn, ctx)) is not None:
                return done
            card = await _own_card(conn, ctx, card_id)
            if card["status"] == "cancelled":
                raise Problem(
                    409,
                    "card_cancelled",
                    "This card was blocked as lost or stolen and is cancelled.",
                )
            if body.mode == "temporary" and card["status"] == "blocked":
                raise Problem(409, "already_blocked", "This card is already blocked.")
            status = "blocked" if body.mode == "temporary" else "cancelled"
            await conn.execute(
                "UPDATE corebank.cards SET status = $2 WHERE id = $1", card_id, status
            )
            return await remember(
                conn, ctx, {"card_id": str(card_id), "last4": card["last4"], "status": status}
            )

    @app.post("/v1/cards/{card_id}/unblock")
    async def unblock_card(ctx: Ctx, card_id: uuid.UUID) -> dict[str, Any]:
        async with tx(ctx) as conn:
            if (done := await remembered(conn, ctx)) is not None:
                return done
            card = await _own_card(conn, ctx, card_id)
            if card["status"] == "cancelled":
                raise Problem(
                    409,
                    "card_cancelled",
                    "A card blocked as lost or stolen can't be unblocked. Order a replacement.",
                )
            if card["status"] != "blocked":
                raise Problem(409, "not_blocked", "This card isn't blocked.")
            await conn.execute("UPDATE corebank.cards SET status = 'active' WHERE id = $1", card_id)
            return await remember(
                conn, ctx, {"card_id": str(card_id), "last4": card["last4"], "status": "active"}
            )

    @app.post("/v1/cards/{card_id}/replacement")
    async def replacement(ctx: Ctx, card_id: uuid.UUID, body: Replacement) -> dict[str, Any]:
        branches = {b["code"]: b["name"] for b in rules.bank()["branches"]}
        if body.branch_code not in branches:
            raise Problem(422, "unknown_branch", "There is no Kaskazi branch with that code.")
        async with tx(ctx) as conn:
            if (done := await remembered(conn, ctx)) is not None:
                return done
            card = await _own_card(conn, ctx, card_id)
            if card["status"] != "cancelled":
                raise Problem(
                    409, "not_cancelled", "Only a card blocked as lost or stolen is replaced."
                )
            fee = rules.bank()["cards"]["debit"]["replacement_fee"]
            account = await conn.fetchrow(
                "SELECT id, balance FROM corebank.accounts WHERE id = $1", card["account_id"]
            )
            if account["balance"] < fee:
                raise Problem(
                    409,
                    "insufficient_funds",
                    f"The account needs KES {fee:,} for the replacement fee.",
                )
            await _post(conn, ctx, account["id"], -Decimal(fee), "Card replacement fee", "fee")
            await conn.execute(
                "UPDATE corebank.cards SET replacement_ordered = true WHERE id = $1", card_id
            )
            days = rules.bank()["cards"]["blocking"]["replacement_delivery_working_days"]
            return await remember(
                conn,
                ctx,
                {
                    "card_id": str(card_id),
                    "fee": fee,
                    "branch": branches[body.branch_code],
                    "ready_within_working_days": days,
                },
            )

    # --- Loans ----------------------------------------------------------------------------------

    @app.get("/v1/loans")
    async def loans(ctx: Ctx) -> list[dict[str, Any]]:
        async with tx(ctx) as conn:
            rows = await conn.fetch(
                "SELECT id, product, principal, outstanding, rate, term_months, instalment, next_due, status "
                "FROM corebank.loans WHERE customer_id = $1",
                ctx.customer_id,
            )
        return [_plain(dict(r)) for r in rows]

    @app.post("/v1/loans/personal/quote")
    async def quote(body: LoanRequest) -> dict[str, Any]:
        if body.amount is None or body.term_months is None:
            raise Problem(422, "amount_and_term_needed", "A quote needs an amount and a term.")
        try:
            return rules.personal_loan_quote(body.amount, body.term_months).as_dict()
        except rules.QuoteError as exc:
            raise Problem(422, "outside_limits", str(exc)) from exc

    @app.post("/v1/loans/personal/eligibility")
    async def eligibility(ctx: Ctx, body: EligibilityRequest) -> dict[str, Any]:
        async with tx(ctx) as conn:
            person, repayments, salaries = await _lending_facts(conn, ctx)
        what_if = body.net_monthly_pay is not None or body.other_monthly_repayments is not None
        try:
            result = rules.personal_loan_eligibility(
                date_of_birth=person["date_of_birth"],
                today=datetime.now(UTC).date(),
                net_monthly_pay=float(
                    body.net_monthly_pay
                    if body.net_monthly_pay is not None
                    else person["net_pay"] or 0
                ),
                salary_credits_last_3_months=salaries,
                existing_monthly_repayments=float(
                    body.other_monthly_repayments
                    if body.other_monthly_repayments is not None
                    else repayments
                ),
                amount=body.amount,
                term_months=body.term_months,
            )
        except rules.QuoteError as exc:
            raise Problem(422, "outside_limits", str(exc)) from exc
        return {
            **result.as_dict(),
            "based_on": "figures the customer gave"
            if what_if
            else "the customer's pay and loans on the bank's records",
        }

    @app.post("/v1/loans/salary-advance/limit")
    async def advance_limit(ctx: Ctx) -> dict[str, Any]:
        async with tx(ctx) as conn:
            person, _, salaries = await _lending_facts(conn, ctx)
        return rules.salary_advance_limit(
            net_monthly_pay=float(person["net_pay"] or 0),
            basic_monthly_pay=float(person["basic_pay"] or 0),
            consecutive_salary_credits=salaries,
            has_open_advance=False,
        ).as_dict()

    # --- Payments -------------------------------------------------------------------------------

    @app.get("/v1/recipients")
    async def recipients(ctx: Ctx) -> list[dict[str, Any]]:
        async with tx(ctx) as conn:
            rows = await conn.fetch(
                "SELECT id, name, kind, phone, account_number, bank_name, added_at FROM corebank.recipients "
                "WHERE customer_id = $1 ORDER BY name",
                ctx.customer_id,
            )
        return [_plain(dict(r)) for r in rows]

    @app.post("/v1/transfers/own")
    async def own_transfer(ctx: Ctx, body: OwnTransfer) -> dict[str, Any]:
        if body.from_account_id == body.to_account_id:
            raise Problem(422, "same_account", "Choose two different accounts.")
        async with tx(ctx) as conn:
            if (done := await remembered(conn, ctx)) is not None:
                return done
            src = await _own_account(conn, ctx, body.from_account_id)
            dst = await _own_account(conn, ctx, body.to_account_id)
            await _check_digital_limit(conn, body.from_account_id, body.amount, own=True)
            if src["balance"] < body.amount:
                raise Problem(409, "insufficient_funds", "There isn't enough money in the account.")
            ref = await _post(
                conn, ctx, src["id"], -body.amount, f"Transfer to {dst['product']}", "app"
            )
            await _post(conn, ctx, dst["id"], body.amount, f"Transfer from {src['product']}", "app")
            return await remember(
                conn,
                ctx,
                {
                    "reference": ref,
                    "amount": str(body.amount),
                    "fee": 0,
                    "from": src["product"],
                    "to": dst["product"],
                },
            )

    @app.post("/v1/transfers/mobile-money")
    async def mobile_money(ctx: Ctx, body: MobileMoneyTransfer) -> dict[str, Any]:
        t = rules.bank()["transfers"]
        if body.amount > t["mobile_money_maximum_per_transfer"]:
            raise Problem(
                422,
                "over_maximum",
                f"The most per transfer is KES {t['mobile_money_maximum_per_transfer']:,}.",
            )
        async with tx(ctx) as conn:
            if (done := await remembered(conn, ctx)) is not None:
                return done
            src = await _own_account(conn, ctx, body.from_account_id)
            name, phone, added = "a new recipient", body.phone, None
            if body.recipient_id is not None:
                r = await conn.fetchrow(
                    "SELECT name, phone, added_at FROM corebank.recipients WHERE id = $1 AND customer_id = $2 AND kind = 'mobile_money'",
                    body.recipient_id,
                    ctx.customer_id,
                )
                if r is None:
                    raise Problem(404, "not_found", "No such saved recipient.")
                name, phone, added = r["name"], r["phone"], r["added_at"]
            if phone is None:
                raise Problem(422, "recipient_needed", "Say who to send it to.")
            new_recipient = added is None or added > datetime.now(UTC) - timedelta(hours=24)
            if new_recipient and body.amount > t["new_recipient_first_24_hours_limit"]:
                raise Problem(
                    409,
                    "new_recipient_limit",
                    f"For a new recipient, the most in the first 24 hours is KES {t['new_recipient_first_24_hours_limit']:,}.",
                )
            sent_today = await conn.fetchval(
                "SELECT coalesce(-sum(amount), 0) FROM corebank.transactions WHERE account_id = $1 "
                "AND channel = 'app' AND description LIKE 'Mobile money to %' AND at >= date_trunc('day', now())",
                src["id"],
            )
            if sent_today + body.amount > t["mobile_money_daily_limit"]:
                raise Problem(
                    409,
                    "daily_limit",
                    f"Mobile money transfers are limited to KES {t['mobile_money_daily_limit']:,} a day.",
                )
            await _check_digital_limit(conn, src["id"], body.amount, own=False)
            fee = Decimal(rules.tiered_fee(t["mobile_money"], float(body.amount)) or 0)
            if src["balance"] < body.amount + fee:
                raise Problem(
                    409,
                    "insufficient_funds",
                    "There isn't enough money for the transfer and its fee.",
                )
            ref = await _post(conn, ctx, src["id"], -body.amount, f"Mobile money to {name}", "app")
            await _post(conn, ctx, src["id"], -fee, "Mobile money transfer fee", "fee")
            return await remember(
                conn,
                ctx,
                {
                    "reference": ref,
                    "amount": str(body.amount),
                    "fee": int(fee),
                    "to": name,
                    "phone": phone,
                },
            )

    @app.get("/v1/fees/{service}")
    async def fees(service: str, amount: float | None = None) -> dict[str, Any]:
        if service not in rules.FEE_SERVICES:
            raise Problem(404, "unknown_service", "No such service in the tariff.")
        return rules.fee(service, amount)

    # --- Complaints -----------------------------------------------------------------------------

    @app.post("/v1/complaints")
    async def new_complaint(ctx: Ctx, body: NewComplaint) -> dict[str, Any]:
        c = rules.bank()["complaints"]
        async with tx(ctx) as conn:
            if (done := await remembered(conn, ctx)) is not None:
                return done
            reference = f"KZC-{uuid.uuid4().int % 1_000_000:06d}"
            respond_by = datetime.now(UTC) + timedelta(hours=c["acknowledge_within_hours"])
            await conn.execute(
                """INSERT INTO corebank.complaints (id, sandbox_id, customer_id, reference, category, summary, respond_by)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                uuid.uuid4(),
                ctx.sandbox_id,
                ctx.customer_id,
                reference,
                body.category,
                body.summary,
                respond_by,
            )
            return await remember(
                conn,
                ctx,
                {
                    "reference": reference,
                    "status": "open",
                    "acknowledge_within_hours": c["acknowledge_within_hours"],
                    "resolve_within_working_days": c["resolve_within_working_days"],
                },
            )

    @app.get("/v1/complaints")
    async def complaints(ctx: Ctx) -> list[dict[str, Any]]:
        async with tx(ctx) as conn:
            rows = await conn.fetch(
                "SELECT reference, category, status, created_at, respond_by FROM corebank.complaints "
                "WHERE customer_id = $1 ORDER BY created_at DESC",
                ctx.customer_id,
            )
        return [_plain(dict(r)) for r in rows]

    return app


async def _own_account(
    conn: asyncpg.Connection, ctx: Context, account_id: uuid.UUID
) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, product, balance, status FROM corebank.accounts WHERE id = $1 AND customer_id = $2 FOR UPDATE",
        account_id,
        ctx.customer_id,
    )
    if row is None:
        raise Problem(404, "not_found", "No such account.")
    if row["status"] != "active":
        raise Problem(409, "account_not_active", "That account isn't active.")
    return row


async def _own_card(conn: asyncpg.Connection, ctx: Context, card_id: uuid.UUID) -> asyncpg.Record:
    row = await conn.fetchrow(
        """SELECT c.id, c.last4, c.status, c.account_id FROM corebank.cards c
           JOIN corebank.accounts a ON a.id = c.account_id
           WHERE c.id = $1 AND a.customer_id = $2 FOR UPDATE OF c""",
        card_id,
        ctx.customer_id,
    )
    if row is None:
        raise Problem(404, "not_found", "No such card.")
    return row


async def _post(
    conn: asyncpg.Connection,
    ctx: Context,
    account_id: uuid.UUID,
    amount: Decimal,
    description: str,
    channel: str,
) -> str:
    if amount == 0:
        return ""
    balance = await conn.fetchval(
        "UPDATE corebank.accounts SET balance = balance + $2 WHERE id = $1 RETURNING balance",
        account_id,
        amount,
    )
    reference = "KZ" + uuid.uuid4().hex[:8].upper()
    await conn.execute(
        """INSERT INTO corebank.transactions (sandbox_id, account_id, at, description, amount, balance_after, reference, channel)
           VALUES ($1, $2, now(), $3, $4, $5, $6, $7)""",
        ctx.sandbox_id,
        account_id,
        description,
        amount,
        balance,
        reference,
        channel,
    )
    return reference


async def _check_digital_limit(
    conn: asyncpg.Connection, account_id: uuid.UUID, amount: Decimal, *, own: bool
) -> None:
    if own:
        return  # transfers between your own accounts don't count towards the digital limit
    limit = rules.bank()["transfers"]["digital_daily_limit"]
    sent = await conn.fetchval(
        "SELECT coalesce(-sum(amount), 0) FROM corebank.transactions WHERE account_id = $1 "
        "AND channel = 'app' AND amount < 0 AND description NOT LIKE 'Transfer to %' AND at >= date_trunc('day', now())",
        account_id,
    )
    if sent + amount > limit:
        raise Problem(
            409,
            "digital_limit",
            f"Payments in the app are limited to KES {limit:,} a day; larger payments are made at a branch.",
        )


async def _lending_facts(
    conn: asyncpg.Connection, ctx: Context
) -> tuple[asyncpg.Record, float, int]:
    person = await conn.fetchrow(
        "SELECT date_of_birth, net_pay, basic_pay FROM corebank.customers WHERE id = $1",
        ctx.customer_id,
    )
    repayments = await conn.fetchval(
        "SELECT coalesce(sum(instalment), 0) FROM corebank.loans WHERE customer_id = $1 AND status = 'active'",
        ctx.customer_id,
    )
    salaries = await conn.fetchval(
        """SELECT count(*) FROM corebank.transactions t JOIN corebank.accounts a ON a.id = t.account_id
           WHERE a.customer_id = $1 AND t.channel = 'salary' AND t.at > now() - interval '92 days'""",
        ctx.customer_id,
    )
    return person, float(repayments), int(salaries)


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, Decimal):
            out[key] = float(value)
        elif isinstance(value, datetime | date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
