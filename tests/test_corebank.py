"""The core-banking service: sample data, ownership, idempotency and the bank's limits."""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from kaskazi.corebank.client import CoreBank, CoreBankError
from kaskazi.corebank.seed import PERSONAS, seed_copy
from kaskazi.corebank.service import create_app
from kaskazi.db.pool import visitor_tx


@pytest.fixture(scope="module")
async def bank(app_pool: asyncpg.Pool) -> AsyncIterator[CoreBank]:
    client = CoreBank(httpx.ASGITransport(app=create_app(app_pool)))
    yield client
    await client.aclose()


async def new_copy(pool: asyncpg.Pool, persona: str = "teacher") -> tuple[uuid.UUID, uuid.UUID]:
    sandbox = uuid.uuid4()
    async with visitor_tx(pool, sandbox) as conn:
        customer = await seed_copy(conn, sandbox, persona)
    return sandbox, customer


@pytest.mark.parametrize("persona", list(PERSONAS))
async def test_sample_histories_add_up(app_pool: asyncpg.Pool, persona: str) -> None:
    sandbox, _ = await new_copy(app_pool, persona)
    async with visitor_tx(app_pool, sandbox) as conn:
        for account in await conn.fetch("SELECT id, balance FROM corebank.accounts"):
            rows = await conn.fetch(
                "SELECT amount, balance_after FROM corebank.transactions WHERE account_id = $1 ORDER BY at DESC, id",
                account["id"],
            )
            assert rows and rows[0]["balance_after"] == account["balance"]
            for newer, older in itertools.pairwise(rows):
                assert older["balance_after"] == newer["balance_after"] - newer["amount"]


async def test_a_retried_transfer_moves_money_once(app_pool: asyncpg.Pool, bank: CoreBank) -> None:
    sandbox, customer = await new_copy(app_pool)
    accounts = await bank.call("GET", "/v1/accounts", sandbox_id=sandbox, customer_id=customer)
    everyday, saver = accounts[0], accounts[1]
    body = {"from_account_id": everyday["id"], "to_account_id": saver["id"], "amount": "1000"}
    first = await bank.call(
        "POST",
        "/v1/transfers/own",
        sandbox_id=sandbox,
        customer_id=customer,
        json=body,
        idempotency_key="k1",
    )
    again = await bank.call(
        "POST",
        "/v1/transfers/own",
        sandbox_id=sandbox,
        customer_id=customer,
        json=body,
        idempotency_key="k1",
    )
    assert first == again
    after = await bank.call("GET", "/v1/accounts", sandbox_id=sandbox, customer_id=customer)
    assert after[0]["balance"] == everyday["balance"] - 1000
    assert after[1]["balance"] == saver["balance"] + 1000


async def test_writes_without_a_key_are_refused(app_pool: asyncpg.Pool, bank: CoreBank) -> None:
    sandbox, customer = await new_copy(app_pool)
    card = (await bank.call("GET", "/v1/cards", sandbox_id=sandbox, customer_id=customer))[0]
    with pytest.raises(CoreBankError) as err:
        await bank.call(
            "POST",
            f"/v1/cards/{card['id']}/block",
            sandbox_id=sandbox,
            customer_id=customer,
            json={"mode": "temporary"},
        )
    assert err.value.code == "idempotency_key_required"


async def test_a_lost_card_cannot_be_unblocked(app_pool: asyncpg.Pool, bank: CoreBank) -> None:
    sandbox, customer = await new_copy(app_pool)
    card = next(
        c
        for c in await bank.call("GET", "/v1/cards", sandbox_id=sandbox, customer_id=customer)
        if c["kind"] == "debit"
    )
    path = f"/v1/cards/{card['id']}"
    await bank.call(
        "POST",
        f"{path}/block",
        sandbox_id=sandbox,
        customer_id=customer,
        json={"mode": "temporary"},
        idempotency_key="b1",
    )
    unblocked = await bank.call(
        "POST", f"{path}/unblock", sandbox_id=sandbox, customer_id=customer, idempotency_key="u1"
    )
    assert unblocked["status"] == "active"
    await bank.call(
        "POST",
        f"{path}/block",
        sandbox_id=sandbox,
        customer_id=customer,
        json={"mode": "lost_or_stolen"},
        idempotency_key="b2",
    )
    with pytest.raises(CoreBankError) as err:
        await bank.call(
            "POST",
            f"{path}/unblock",
            sandbox_id=sandbox,
            customer_id=customer,
            idempotency_key="u2",
        )
    assert err.value.code == "card_cancelled"
    ordered = await bank.call(
        "POST",
        f"{path}/replacement",
        sandbox_id=sandbox,
        customer_id=customer,
        json={"branch_code": "302"},
        idempotency_key="r1",
    )
    assert ordered["fee"] == 500 and ordered["ready_within_working_days"] == 5


async def test_new_recipients_have_a_first_day_limit(
    app_pool: asyncpg.Pool, bank: CoreBank
) -> None:
    sandbox, customer = await new_copy(app_pool, "trader")
    business = (await bank.call("GET", "/v1/accounts", sandbox_id=sandbox, customer_id=customer))[0]
    with pytest.raises(CoreBankError) as err:
        await bank.call(
            "POST",
            "/v1/transfers/mobile-money",
            sandbox_id=sandbox,
            customer_id=customer,
            json={"from_account_id": business["id"], "phone": "0700111222", "amount": "60000"},
            idempotency_key="m1",
        )
    assert err.value.code == "new_recipient_limit"
    sent = await bank.call(
        "POST",
        "/v1/transfers/mobile-money",
        sandbox_id=sandbox,
        customer_id=customer,
        json={"from_account_id": business["id"], "phone": "0700111222", "amount": "5000"},
        idempotency_key="m2",
    )
    assert sent["fee"] == 35


async def test_one_copy_cannot_touch_another_copys_card(
    app_pool: asyncpg.Pool, bank: CoreBank
) -> None:
    a_sandbox, a_customer = await new_copy(app_pool)
    b_sandbox, b_customer = await new_copy(app_pool)
    b_card = (await bank.call("GET", "/v1/cards", sandbox_id=b_sandbox, customer_id=b_customer))[0]
    with pytest.raises(CoreBankError) as err:
        await bank.call(
            "POST",
            f"/v1/cards/{b_card['id']}/block",
            sandbox_id=a_sandbox,
            customer_id=a_customer,
            json={"mode": "temporary"},
            idempotency_key="x",
        )
    assert err.value.status == 404


async def test_eligibility_uses_the_customers_own_figures(
    app_pool: asyncpg.Pool, bank: CoreBank
) -> None:
    sandbox, customer = await new_copy(app_pool)
    result = await bank.call(
        "POST",
        "/v1/loans/personal/eligibility",
        sandbox_id=sandbox,
        customer_id=customer,
        json={"amount": 200000, "term_months": 24},
    )
    # Amina: net 68,940, so 40% is 27,576; her existing loan takes 14,617 a month.
    assert result["affordability_limit"] == 27_576
    assert result["existing_monthly_repayments"] == 14_617
    assert result["room_for_new_instalment"] == 12_959
    assert result["based_on"] == "the customer's pay and loans on the bank's records"

    # A what-if with figures she gives: 40% of 80,000 is 32,000, less 15,000 elsewhere.
    what_if = await bank.call(
        "POST",
        "/v1/loans/personal/eligibility",
        sandbox_id=sandbox,
        customer_id=customer,
        json={"net_monthly_pay": 80_000, "other_monthly_repayments": 15_000},
    )
    assert what_if["based_on"] == "figures the customer gave"
    assert what_if["room_for_new_instalment"] == 17_000
    assert what_if["largest_loan_by_term"]["36"] == 486_971
    assert what_if["largest_loan_by_term"]["60"] == 706_787


async def test_suggestions_come_from_rules_on_the_customers_own_accounts(
    app_pool: asyncpg.Pool, bank: CoreBank
) -> None:
    found = {}
    for persona in PERSONAS:
        sandbox, customer = await new_copy(app_pool, persona)
        found[persona] = await bank.call(
            "GET", "/v1/suggestions", sandbox_id=sandbox, customer_id=customer
        )
    # Amina's Goal Saver holds 128,500 untouched: a 12-month deposit pays 9.5% against 5.0%.
    (fd,) = found["teacher"]
    assert fd["rule"] == "idle_savings" and fd["product"] == "Kaskazi Fixed Deposit"
    assert fd["benefit_kes_a_year"] == 5_783  # 4.5% of 128,500, before tax
    assert "128,500" in fd["why"]
    # Faith's student account is too small to suggest anything.
    assert found["student"] == []
    # Nothing that lends is ever suggested.
    products = {s["product"] for each in found.values() for s in each}
    assert products <= {"Kaskazi Fixed Deposit", "Kaskazi Goal Saver"}


async def test_application_status_reads_the_latest_application(
    app_pool: asyncpg.Pool, bank: CoreBank
) -> None:
    sandbox, customer = await new_copy(app_pool)
    none = await bank.call(
        "GET", "/v1/loans/applications/latest", sandbox_id=sandbox, customer_id=customer
    )
    assert none == {"application": None}
