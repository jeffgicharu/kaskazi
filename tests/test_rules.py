"""The rules engine against the worked examples the bank's documents print."""

from __future__ import annotations

from datetime import date

import pytest

from kaskazi.corebank.rules import (
    QuoteError,
    fee,
    largest_loan,
    personal_loan_eligibility,
    personal_loan_quote,
    salary_advance_limit,
)


@pytest.mark.parametrize(
    ("amount", "months", "monthly", "fee_", "insurance", "received", "total", "cost"),
    [
        (200_000, 24, 9_745, 5_000, 2_000, 193_000, 233_880, 40_880),
        (500_000, 36, 17_455, 12_500, 7_500, 480_000, 628_380, 148_380),
        (1_000_000, 60, 24_053, 25_000, 25_000, 950_000, 1_443_180, 493_180),
        (50_000, 6, 8_714, 2_000, 250, 47_750, 52_284, 4_534),
    ],
)
def test_quotes_match_the_personal_loan_guide(
    amount: int,
    months: int,
    monthly: int,
    fee_: int,
    insurance: int,
    received: int,
    total: int,
    cost: int,
) -> None:
    q = personal_loan_quote(amount, months)
    assert (q.monthly_instalment, q.arrangement_fee, q.credit_life_insurance) == (
        monthly,
        fee_,
        insurance,
    )
    assert (q.amount_received, q.total_repaid, q.total_cost_of_credit) == (received, total, cost)


def test_quotes_outside_the_limits_are_refused() -> None:
    with pytest.raises(QuoteError):
        personal_loan_quote(40_000, 12)
    with pytest.raises(QuoteError):
        personal_loan_quote(100_000, 72)


def test_largest_loan_matches_the_lending_policy_examples() -> None:
    # "How we decide on a loan": Amina, net 80,000 and no loans, room 32,000.
    assert largest_loan(32_000, 36) == 916_639
    # Brian, room 17,000.
    assert (largest_loan(17_000, 36), largest_loan(17_000, 48), largest_loan(17_000, 60)) == (
        486_971,
        605_326,
        706_787,
    )


def test_eligibility_with_an_existing_loan() -> None:
    result = personal_loan_eligibility(
        date_of_birth=date(1990, 5, 1),
        today=date(2026, 10, 29),
        net_monthly_pay=80_000,
        salary_credits_last_3_months=3,
        existing_monthly_repayments=15_000,
        amount=600_000,
        term_months=36,
    )
    assert result.eligible and result.room_for_new_instalment == 17_000
    assert result.requested is not None and result.requested.monthly_instalment == 20_946
    assert result.requested_fits is False
    assert result.largest_loan_by_term[48] == 605_326


def test_ineligible_reasons_are_given() -> None:
    result = personal_loan_eligibility(
        date_of_birth=date(2006, 1, 1),
        today=date(2026, 10, 29),
        net_monthly_pay=24_000,
        salary_credits_last_3_months=1,
        existing_monthly_repayments=0,
    )
    assert not result.eligible and len(result.reasons) == 3


def test_salary_advance_one_third_rule() -> None:
    # "Kaskazi Salary Advance": Wanjiru, basic 90,000, net 50,600: the one-third rule limits her to 20,000.
    wanjiru = salary_advance_limit(
        net_monthly_pay=50_600,
        basic_monthly_pay=90_000,
        consecutive_salary_credits=3,
        has_open_advance=False,
    )
    assert wanjiru.maximum_advance == 20_000 and wanjiru.limited_by == "the one-third rule"
    # Otieno, basic 60,000, net 48,000: half of net pay applies.
    otieno = salary_advance_limit(
        net_monthly_pay=48_000,
        basic_monthly_pay=60_000,
        consecutive_salary_credits=3,
        has_open_advance=False,
    )
    assert otieno.maximum_advance == 24_000 and otieno.limited_by == "half of net pay"


@pytest.mark.parametrize(
    ("service", "amount", "expected"),
    [
        ("mobile_money", 1_000, 15),
        ("mobile_money", 1_001, 35),
        ("mobile_money", 150_000, 85),
        ("instant_other_bank", 10_000, 30),
        ("instant_other_bank", 250_000, 120),
        ("agent_withdrawal", 2_501, 50),
        ("agent_withdrawal", 50_000, 200),
        ("atm_other_bank_kenya", None, 35),
        ("rtgs_same_day", None, 500),
    ],
)
def test_fees_follow_the_tariff(service: str, amount: int | None, expected: int) -> None:
    assert fee(service, amount)["fee"] == expected


def test_amounts_over_a_maximum_are_not_allowed() -> None:
    assert fee("mobile_money", 150_001)["allowed"] is False
