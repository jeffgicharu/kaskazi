"""The bank's rules, computed from bank.toml: loan quotes, eligibility and fees.

These are the figures the documents quote, so a tool result and a cited passage always agree.
Everything is deterministic; no model is involved in any number here.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

BANK_FILE = Path(__file__).with_name("data") / "bank.toml"


@cache
def bank() -> dict[str, Any]:
    return tomllib.loads(BANK_FILE.read_text())


def shillings(value: float | Decimal) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tiered_fee(bands: list[dict[str, int]], amount: float) -> int | None:
    for band in bands:
        if amount <= band["up_to"]:
            return band["fee"]
    return None


# --- Personal loans ------------------------------------------------------------------------------


@dataclass(frozen=True)
class LoanQuote:
    amount: int
    term_months: int
    interest_rate: float
    monthly_instalment: int
    arrangement_fee: int
    credit_life_insurance: int
    amount_received: int
    total_repaid: int
    total_interest: int
    total_cost_of_credit: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuoteError(ValueError):
    pass


def instalment(amount: float, term_months: int, annual_rate: float) -> int:
    r = annual_rate / 100 / 12
    return shillings(amount * r / (1 - (1 + r) ** -term_months))


def personal_loan_quote(amount: int, term_months: int) -> LoanQuote:
    p = bank()["loans"]["personal"]
    if not p["minimum_amount"] <= amount <= p["maximum_amount"]:
        raise QuoteError(
            f"Personal loans are from KES {p['minimum_amount']:,} to KES {p['maximum_amount']:,}."
        )
    if not p["minimum_term_months"] <= term_months <= p["maximum_term_months"]:
        raise QuoteError(
            f"Terms are from {p['minimum_term_months']} to {p['maximum_term_months']} months."
        )
    monthly = instalment(amount, term_months, p["interest_rate"])
    fee = max(shillings(amount * p["arrangement_fee_percent"] / 100), p["arrangement_fee_minimum"])
    years = math.ceil(term_months / 12)
    insurance = shillings(amount * p["credit_life_insurance_percent_per_year"] / 100 * years)
    total = monthly * term_months
    return LoanQuote(
        amount=amount,
        term_months=term_months,
        interest_rate=p["interest_rate"],
        monthly_instalment=monthly,
        arrangement_fee=fee,
        credit_life_insurance=insurance,
        amount_received=amount - fee - insurance,
        total_repaid=total,
        total_interest=total - amount,
        total_cost_of_credit=total - amount + fee + insurance,
    )


def largest_loan(max_instalment: float, term_months: int) -> int:
    """The largest whole-shilling loan whose rounded instalment fits."""

    p = bank()["loans"]["personal"]
    if max_instalment <= 0:
        return 0
    r = p["interest_rate"] / 100 / 12
    estimate = int(max_instalment * (1 - (1 + r) ** -term_months) / r)
    while estimate > 0 and instalment(estimate, term_months, p["interest_rate"]) > max_instalment:
        estimate -= 1
    while instalment(estimate + 1, term_months, p["interest_rate"]) <= max_instalment:
        estimate += 1
    return int(min(estimate, p["maximum_amount"]))


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reasons: list[str]
    net_monthly_pay: int
    existing_monthly_repayments: int
    affordability_limit: int
    room_for_new_instalment: int
    largest_loan_by_term: dict[int, int]
    requested: LoanQuote | None
    requested_fits: bool | None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["requested"] = self.requested.as_dict() if self.requested else None
        return out


def personal_loan_eligibility(
    *,
    date_of_birth: date,
    today: date,
    net_monthly_pay: float | None,
    salary_credits_last_3_months: int,
    existing_monthly_repayments: float,
    amount: int | None = None,
    term_months: int | None = None,
) -> Eligibility:
    p = bank()["loans"]["personal"]
    reasons: list[str] = []
    age = (
        today.year
        - date_of_birth.year
        - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
    )
    if age < p["minimum_age"]:
        reasons.append(f"You need to be at least {p['minimum_age']}.")
    net = shillings(net_monthly_pay or 0)
    if net < p["minimum_net_monthly_pay"]:
        reasons.append(f"Net monthly pay must be at least KES {p['minimum_net_monthly_pay']:,}.")
    if salary_credits_last_3_months < p["salary_history_months"]:
        reasons.append(
            f"We need {p['salary_history_months']} months of salary paid into Kaskazi, or "
            f"{p['other_bank_statement_months']} months of statements from your bank."
        )
    limit = shillings(net * p["affordability_limit_percent"] / 100)
    existing = shillings(existing_monthly_repayments)
    room = max(0, limit - existing)
    by_term = {
        t: largest_loan(room, t)
        for t in (12, 24, 36, 48, 60)
        if _age_at_end_ok(age, t, p["maximum_age_at_end_of_term"])
    }
    if room <= 0:
        reasons.append("Your current loan repayments already reach 40% of your net pay.")
    if by_term and max(by_term.values()) < p["minimum_amount"] and room > 0:
        reasons.append(
            f"The largest loan that fits is below the KES {p['minimum_amount']:,} minimum."
        )

    requested = None
    fits = None
    if amount is not None and term_months is not None:
        requested = personal_loan_quote(amount, term_months)
        fits = requested.monthly_instalment <= room
        if not _age_at_end_ok(age, term_months, p["maximum_age_at_end_of_term"]):
            fits = False
            reasons.append(f"The loan must end by age {p['maximum_age_at_end_of_term']}.")
    return Eligibility(
        eligible=not reasons,
        reasons=reasons,
        net_monthly_pay=net,
        existing_monthly_repayments=existing,
        affordability_limit=limit,
        room_for_new_instalment=room,
        largest_loan_by_term=by_term,
        requested=requested,
        requested_fits=fits,
    )


def _age_at_end_ok(age: int, term_months: int, maximum: int) -> bool:
    return age + math.ceil(term_months / 12) <= maximum


# --- Salary advance ------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdvanceLimit:
    eligible: bool
    reasons: list[str]
    maximum_advance: int
    fee_on_maximum: int
    limited_by: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def salary_advance_limit(
    *,
    net_monthly_pay: float | None,
    basic_monthly_pay: float | None,
    consecutive_salary_credits: int,
    has_open_advance: bool,
) -> AdvanceLimit:
    a = bank()["loans"]["salary_advance"]
    reasons: list[str] = []
    if consecutive_salary_credits < a["salary_credits_needed"]:
        reasons.append(
            f"You need {a['salary_credits_needed']} salaries in a row paid into Kaskazi."
        )
    if has_open_advance:
        reasons.append("You can only have one salary advance at a time.")
    net = float(net_monthly_pay or 0)
    basic = float(basic_monthly_pay or 0)
    by_pay = net * a["maximum_percent_of_net_pay"] / 100
    # After the advance and its fee come out of the salary, a third of basic pay must remain.
    by_third = (net - basic / 3) / (1 + a["fee_percent"] / 100)
    candidates = {
        "half of net pay": by_pay,
        "the KES 100,000 cap": a["maximum_amount"],
        "the one-third rule": by_third,
    }
    limited_by = min(candidates, key=lambda k: candidates[k])
    maximum = max(0, math.floor(candidates[limited_by]))
    if maximum < a["minimum_amount"]:
        reasons.append(f"The smallest advance is KES {a['minimum_amount']:,}.")
    return AdvanceLimit(
        eligible=not reasons,
        reasons=reasons,
        maximum_advance=maximum,
        fee_on_maximum=shillings(maximum * a["fee_percent"] / 100),
        limited_by=limited_by,
    )


# --- Fees ----------------------------------------------------------------------------------------

FEE_SERVICES = (
    "mobile_money",
    "instant_other_bank",
    "eft_next_day",
    "rtgs_same_day",
    "own_accounts",
    "other_kaskazi_accounts",
    "bill_payment",
    "standing_order_payment",
    "international_outward",
    "agent_withdrawal",
    "agent_deposit",
    "atm_kaskazi",
    "atm_other_bank_kenya",
    "atm_abroad",
    "card_replacement",
    "printed_statement",
)


def fee(service: str, amount: float | None = None) -> dict[str, Any]:
    t = bank()["transfers"]
    agents = bank()["agents"]
    debit = bank()["cards"]["debit"]
    if service == "mobile_money":
        if amount is None:
            return {"service": service, "bands": t["mobile_money"]}
        if amount > t["mobile_money_maximum_per_transfer"]:
            return {
                "service": service,
                "allowed": False,
                "reason": f"The most per transfer is KES {t['mobile_money_maximum_per_transfer']:,}.",
            }
        return {"service": service, "amount": amount, "fee": tiered_fee(t["mobile_money"], amount)}
    if service == "instant_other_bank":
        if amount is None:
            return {"service": service, "bands": t["instant_other_bank"]}
        if amount > t["instant_other_bank_maximum"]:
            return {
                "service": service,
                "allowed": False,
                "reason": f"The most per instant transfer is KES {t['instant_other_bank_maximum']:,}.",
            }
        return {
            "service": service,
            "amount": amount,
            "fee": tiered_fee(t["instant_other_bank"], amount),
        }
    if service == "agent_withdrawal":
        if amount is None:
            return {"service": service, "bands": agents["withdrawal"]}
        if amount > agents["withdrawal_maximum"]:
            return {
                "service": service,
                "allowed": False,
                "reason": f"The most per agent withdrawal is KES {agents['withdrawal_maximum']:,}.",
            }
        return {
            "service": service,
            "amount": amount,
            "fee": tiered_fee(agents["withdrawal"], amount),
        }
    flat = {
        "eft_next_day": t["eft_next_day"],
        "rtgs_same_day": t["rtgs_same_day"],
        "own_accounts": t["own_accounts"],
        "other_kaskazi_accounts": t["other_kaskazi_accounts"],
        "bill_payment": t["bill_payment"],
        "standing_order_payment": t["standing_order_each_payment"],
        "international_outward": t["international_outward"],
        "agent_deposit": agents["deposit"],
        "atm_kaskazi": debit["atm_kaskazi"],
        "atm_other_bank_kenya": debit["atm_other_bank_kenya"],
        "atm_abroad": debit["atm_abroad"],
        "card_replacement": debit["replacement_fee"],
        "printed_statement": bank()["digital"]["printed_statement"],
    }
    if service not in flat:
        raise ValueError(f"unknown service {service}")
    return {"service": service, "fee": flat[service]}


# --- Product suggestions --------------------------------------------------------------------------
#
# Rules on the customer's own accounts, each naming what fired and the figures behind it, so the
# customer can see why. Credit (loans, cards, advances) is never suggested: lending starts with
# the customer, not with the bank.


@dataclass(frozen=True)
class Suggestion:
    rule: str
    product: str
    title: str
    why: str
    benefit_kes_a_year: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def suggestions(
    accounts: list[dict[str, Any]],
    history: dict[str, list[tuple[date, Decimal]]],
    today: date,
) -> list[Suggestion]:
    """What would suit the customer, from their accounts and the last 60 days of each one's
    transactions (date, amount: negative out, positive in)."""
    b = bank()["accounts"]
    gs, fd = b["goal_saver"], b["fixed_deposit"]
    found: list[Suggestion] = []
    savers = [a for a in accounts if a["product"] == gs["name"]]
    for saver in savers:
        balance = Decimal(str(saver["balance"]))
        quiet_since = today - timedelta(days=60)
        withdrawn = any(
            amt < 0 and when >= quiet_since for when, amt in history.get(saver["id"], [])
        )
        if balance >= fd["minimum_deposit"] and not withdrawn:
            gain = balance * Decimal(str(fd["rate_12_months"] - gs["interest_rate_high"])) / 100
            found.append(
                Suggestion(
                    rule="idle_savings",
                    product=fd["name"],
                    title=f"Fix your savings for 12 months at {fd['rate_12_months']}%",
                    why=(
                        f"Your {gs['name']} holds KES {balance:,.0f} and nothing has been taken out "
                        f"for 60 days. A {fd['name']} of 12 months pays {fd['rate_12_months']}% a "
                        f"year, against {gs['interest_rate_high']}% now, if you won't need it before "
                        "then."
                    ),
                    benefit_kes_a_year=shillings(gain),
                )
            )
    if not savers:
        for account in accounts:
            if account["product"] not in (b["everyday"]["name"], b["student"]["name"]):
                continue
            balance = Decimal(str(account["balance"]))
            low = balance
            recent = [
                t for t in history.get(account["id"], []) if t[0] >= today - timedelta(days=30)
            ]
            for _, amt in sorted(recent, key=lambda t: t[0], reverse=True):
                balance -= amt  # walk back through the last 30 days' balances
                low = min(low, balance)
            if low >= gs["interest_threshold"]:
                gain = low * Decimal(str(gs["interest_rate_high"])) / 100
                found.append(
                    Suggestion(
                        rule="unused_balance",
                        product=gs["name"],
                        title=f"Earn {gs['interest_rate_high']}% on money you aren't using",
                        why=(
                            f"Your {account['product']} never fell below KES {low:,.0f} in the last "
                            f"30 days, and it pays no interest. In a {gs['name']} that amount would "
                            f"earn {gs['interest_rate_high']}% a year, and one withdrawal a month is "
                            "free."
                        ),
                        benefit_kes_a_year=shillings(gain),
                    )
                )
    return found
