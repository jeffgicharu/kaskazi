"""The sample customers a visitor can be, and the data each copy starts with.

Every person, employer and number here is invented. Dates are relative to the day the copy is
made, so statements always look current. Each account's history is built backwards from its
balance today, so every running balance adds up.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import asyncpg

from kaskazi.controls.pii import luhn_ok
from kaskazi.corebank.rules import bank, personal_loan_quote, tiered_fee


@dataclass(frozen=True)
class Recipient:
    name: str
    kind: str
    phone: str | None = None
    account_number: str | None = None
    bank_name: str | None = None


@dataclass(frozen=True)
class Persona:
    key: str
    label: str
    summary: str
    full_name: str
    first_name: str
    date_of_birth: date
    segment: str
    branch_code: str
    phone: str
    email: str
    national_id: str
    kra_pin: str
    employer: str | None
    basic_pay: int | None
    gross_pay: int | None
    net_pay: int | None
    salary_day: int | None
    accounts: list[tuple[str, int]]  # (product key, balance today)
    credit_limit: int | None = None
    loan: tuple[int, int, int] | None = None  # (principal, term months, instalments already paid)
    recipients: list[Recipient] = field(default_factory=list)


PERSONAS: dict[str, Persona] = {
    "teacher": Persona(
        key="teacher",
        label="Amina, a teacher in Nakuru",
        summary="Salary paid into Kaskazi, a Goal Saver for school fees, a debit and a credit card, and a personal loan she is repaying.",
        full_name="Amina Wanjiru Kariuki",
        first_name="Amina",
        date_of_birth=date(1988, 3, 14),
        segment="salaried",
        branch_code="302",
        phone="0700412377",
        email="amina.kariuki@example.com",
        national_id="27450913",
        kra_pin="A004519273K",
        employer="Kilimo Heights Secondary School",
        basic_pay=72_000,
        gross_pay=86_500,
        net_pay=68_940,
        salary_day=25,
        accounts=[("everyday", 41_230), ("goal_saver", 128_500)],
        credit_limit=120_000,
        loan=(300_000, 24, 10),
        recipients=[
            Recipient("Peter Kariuki", "mobile_money", phone="0700518264"),
            Recipient("Njeri Wambui", "mobile_money", phone="0700677105"),
            Recipient("Grace Muthoni", "kaskazi", account_number=""),
        ],
    ),
    "trader": Persona(
        key="trader",
        label="Joseph, a hardware trader in Machakos",
        summary="A Business Account for his shop with card and mobile money takings, an Everyday Account, and suppliers he pays often.",
        full_name="Joseph Mutua Musyoka",
        first_name="Joseph",
        date_of_birth=date(1979, 7, 2),
        segment="business",
        branch_code="402",
        phone="0700239841",
        email="joseph.musyoka@example.com",
        national_id="20914586",
        kra_pin="A001837460P",
        employer=None,
        basic_pay=None,
        gross_pay=None,
        net_pay=None,
        salary_day=None,
        accounts=[("business", 412_800), ("everyday", 23_450)],
        recipients=[
            Recipient(
                "Mutiso Building Supplies",
                "other_bank",
                account_number="",
                bank_name="another bank",
            ),
            Recipient("Kioko Mwende", "mobile_money", phone="0700364952"),
            Recipient("Mutua Musyoka", "mobile_money", phone="0700150376"),
        ],
    ),
    "student": Persona(
        key="student",
        label="Faith, a student in Eldoret",
        summary="A Student Account with a small balance, money from home by mobile money, and a debit card.",
        full_name="Faith Chebet Kiprop",
        first_name="Faith",
        date_of_birth=date(2004, 11, 9),
        segment="student",
        branch_code="303",
        phone="0700871420",
        email="faith.kiprop@example.com",
        national_id="38127764",
        kra_pin="A015628347M",
        employer=None,
        basic_pay=None,
        gross_pay=None,
        net_pay=None,
        salary_day=None,
        accounts=[("student", 3_870)],
        recipients=[
            Recipient("Rose Kiprop", "mobile_money", phone="0700926613"),
            Recipient("Brian Kibet", "mobile_money", phone="0700403158"),
        ],
    ),
}


def account_number(branch_code: str, rng: random.Random) -> str:
    body = f"0{branch_code}{rng.randrange(10**7, 10**8):08d}"
    return next(body + str(d) for d in range(10) if luhn_ok(body + str(d)))


def _reference(rng: random.Random) -> str:
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "KZ" + "".join(rng.choice(letters + "23456789") for _ in range(8))


def _history(
    persona: Persona, product: str, today: date, rng: random.Random
) -> list[tuple[datetime, str, int, str]]:
    """Transactions over the last 60 days, newest first: (when, description, amount, channel)."""

    t = bank()["transfers"]
    rows: list[tuple[datetime, str, int, str]] = []

    def at(days_ago: int, hour: int) -> datetime:
        return datetime.combine(
            today - timedelta(days=days_ago), time(hour, rng.randrange(60)), UTC
        )

    if product == "everyday" and persona.net_pay:
        for months_ago in range(3):
            pay_day = (today.replace(day=1) - timedelta(days=30 * months_ago)).replace(
                day=persona.salary_day or 25
            )
            if pay_day <= today:
                rows.append(
                    (
                        datetime.combine(pay_day, time(6, 5), UTC),
                        f"Salary from {persona.employer}",
                        persona.net_pay,
                        "salary",
                    )
                )
        if persona.loan:
            principal, term, _ = persona.loan
            monthly = personal_loan_quote(principal, term).monthly_instalment
            for months_ago in range(2):
                due = (today.replace(day=1) - timedelta(days=30 * months_ago)).replace(day=28)
                if due <= today:
                    rows.append(
                        (
                            datetime.combine(due, time(7, 0), UTC),
                            "Personal loan instalment",
                            -monthly,
                            "loan",
                        )
                    )
        rows.append((at(18, 9), "Transfer to Goal Saver: School fees", -10_000, "app"))
    if product in ("everyday", "student", "business"):
        shops = [
            "Supermarket",
            "Fuel station",
            "Pharmacy",
            "Hardware supplies",
            "Bookshop",
            "Restaurant",
        ]
        for _ in range(rng.randrange(12, 18)):
            days = rng.randrange(1, 58)
            kind = rng.random()
            if kind < 0.45:
                amount = rng.choice([450, 820, 1_250, 2_300, 3_600, 5_400])
                rows.append(
                    (
                        at(days, rng.randrange(8, 20)),
                        f"Card payment: {rng.choice(shops[1:])}",
                        -amount,
                        "card",
                    )
                )
            elif kind < 0.75 and persona.recipients:
                r = rng.choice(
                    [x for x in persona.recipients if x.kind == "mobile_money"]
                    or persona.recipients
                )
                amount = rng.choice([500, 1_000, 2_000, 3_500, 5_000])
                fee = tiered_fee(t["mobile_money"], amount) or 0
                when = at(days, rng.randrange(7, 21))
                rows.append((when, f"Mobile money to {r.name}", -amount, "app"))
                rows.append((when + timedelta(seconds=1), "Mobile money transfer fee", -fee, "fee"))
            elif kind < 0.9:
                amount = rng.choice([1_000, 2_000, 3_000, 5_000])
                rows.append(
                    (at(days, rng.randrange(8, 20)), "Cash withdrawal, Kaskazi ATM", -amount, "atm")
                )
            else:
                amount = rng.choice([1_500, 2_500, 4_000])
                rows.append(
                    (
                        at(days, rng.randrange(8, 20)),
                        "Mobile money received",
                        amount,
                        "mobile_money",
                    )
                )
    if product == "business":
        for days in range(1, 58, 3):
            rows.append(
                (
                    at(days, 18),
                    "Till collections, card and mobile money",
                    rng.randrange(18_000, 46_000),
                    "till",
                )
            )
        rows.append((at(9, 10), "Payment to Mutiso Building Supplies", -186_500, "eft"))
        rows.append(
            (at(9, 10) + timedelta(seconds=1), "Next-day transfer fee", -t["eft_next_day"], "fee")
        )
    if product == "student":
        for days in (4, 34):
            rows.append(
                (at(days, 12), "Mobile money received from Rose Kiprop", 6_000, "mobile_money")
            )
    if product == "goal_saver":
        rows.append((at(18, 9), "Transfer from Everyday Account", 10_000, "app"))
        rows.append((at(48, 9), "Transfer from Everyday Account", 10_000, "app"))
        rows.append((at(30, 23), "Interest, after tax", 438, "interest"))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


async def seed_copy(
    conn: asyncpg.Connection, sandbox_id: uuid.UUID, persona_key: str, today: date | None = None
) -> uuid.UUID:
    """Creates the sample customer's data in a new copy. Returns the customer's id."""

    persona = PERSONAS[persona_key]
    today = today or datetime.now(UTC).date()
    # Sample data, not secrets: seeded so a copy's history is reproducible.
    rng = random.Random(f"{persona.key}:{sandbox_id}")  # noqa: S311
    customer_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO app.sandboxes (id, kind, persona, customer_id) VALUES ($1, 'visitor', $2, $3) "
        "ON CONFLICT (id) DO NOTHING",
        sandbox_id,
        persona.key,
        customer_id,
    )
    await conn.execute(
        """INSERT INTO corebank.customers
               (id, sandbox_id, full_name, first_name, phone, email, national_id, kra_pin,
                date_of_birth, segment, employer, basic_pay, net_pay, salary_day, branch_code)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)""",
        customer_id,
        sandbox_id,
        persona.full_name,
        persona.first_name,
        persona.phone,
        persona.email,
        persona.national_id,
        persona.kra_pin,
        persona.date_of_birth,
        persona.segment,
        persona.employer,
        persona.basic_pay,
        persona.net_pay,
        persona.salary_day,
        persona.branch_code,
    )
    names = bank()["accounts"]
    first_account: uuid.UUID | None = None
    for product, balance in persona.accounts:
        account_id = uuid.uuid4()
        first_account = first_account or account_id
        await conn.execute(
            """INSERT INTO corebank.accounts (id, sandbox_id, customer_id, number, product, balance, opened_on)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            account_id,
            sandbox_id,
            customer_id,
            account_number(persona.branch_code, rng),
            names[product]["name"],
            balance,
            today - timedelta(days=rng.randrange(400, 2400)),
        )
        running = balance
        rows = []
        for when, description, amount, channel in _history(persona, product, today, rng):
            rows.append(
                (
                    sandbox_id,
                    account_id,
                    when,
                    description,
                    amount,
                    running,
                    _reference(rng),
                    channel,
                )
            )
            running -= amount
        await conn.executemany(
            """INSERT INTO corebank.transactions
                   (sandbox_id, account_id, at, description, amount, balance_after, reference, channel)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            rows,
        )
        if product in ("everyday", "student", "business"):
            await conn.execute(
                """INSERT INTO corebank.cards (id, sandbox_id, account_id, kind, last4, status, expires)
                   VALUES ($1, $2, $3, 'debit', $4, 'active', $5)""",
                uuid.uuid4(),
                sandbox_id,
                account_id,
                f"{rng.randrange(10_000):04d}",
                f"{(today.month % 12) + 1:02d}/{(today.year + 3) % 100:02d}",
            )
    if persona.credit_limit and first_account:
        await conn.execute(
            """INSERT INTO corebank.cards (id, sandbox_id, account_id, kind, last4, status, expires, credit_limit)
               VALUES ($1, $2, $3, 'credit', $4, 'active', $5, $6)""",
            uuid.uuid4(),
            sandbox_id,
            first_account,
            f"{rng.randrange(10_000):04d}",
            f"{((today.month + 5) % 12) + 1:02d}/{(today.year + 2) % 100:02d}",
            persona.credit_limit,
        )
    if persona.loan:
        principal, term, paid = persona.loan
        quote = personal_loan_quote(principal, term)
        rate = quote.interest_rate / 100 / 12
        outstanding = principal * (1 + rate) ** paid - quote.monthly_instalment * (
            ((1 + rate) ** paid - 1) / rate
        )
        await conn.execute(
            """INSERT INTO corebank.loans
                   (id, sandbox_id, customer_id, product, principal, outstanding, rate, term_months,
                    instalment, next_due, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'active')""",
            uuid.uuid4(),
            sandbox_id,
            customer_id,
            bank()["loans"]["personal"]["name"],
            principal,
            round(outstanding, 2),
            quote.interest_rate,
            term,
            quote.monthly_instalment,
            _next_28th(today),
        )
    for r in persona.recipients:
        await conn.execute(
            """INSERT INTO corebank.recipients
                   (id, sandbox_id, customer_id, name, kind, phone, account_number, bank_name, added_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            uuid.uuid4(),
            sandbox_id,
            customer_id,
            r.name,
            r.kind,
            r.phone,
            r.account_number or (account_number("201", rng) if r.kind == "kaskazi" else None),
            r.bank_name,
            datetime.now(UTC) - timedelta(days=rng.randrange(60, 600)),
        )
    return customer_id


def _next_28th(today: date) -> date:
    candidate = today.replace(day=28)
    if candidate <= today:
        candidate = (today.replace(day=1) + timedelta(days=32)).replace(day=28)
    return candidate
