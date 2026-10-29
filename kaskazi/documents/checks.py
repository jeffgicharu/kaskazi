"""The document desk's checks: what can be proved about a document without trusting the model.

Arithmetic, the statutory deductions recomputed from the cited rates, dates, formats, and the
same person across documents and the bank's own records. A check that can't be made says why
(a pay period before the rates on file, a missing field) instead of passing quietly.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from kaskazi.controls.injection import screen
from kaskazi.documents import statutory
from kaskazi.documents.extract import CONFIDENT, REQUIRED, Extraction, amount

TOLERANCE = Decimal("1.00")  # a shilling either way: payroll systems round differently


# What it means when a check fails, said plainly, for the review reasons.
FAULTS = {
    "net_adds_up": "Net pay doesn't equal gross pay less deductions",
    "statutory_nssf": "NSSF doesn't match the rates in force",
    "statutory_shif": "SHIF doesn't match the rates in force",
    "statutory_housing_levy": "The Housing Levy doesn't match the rates in force",
    "statutory_paye": "PAYE doesn't match the rates in force",
    "period_not_future": "The period is in the future",
    "recent": "The payslip is more than three months old",
    "paid_not_future": "The pay date is in the future",
    "employer_kra_pin_format": "The employer's KRA PIN isn't well formed",
    "employee_kra_pin_format": "The employee's KRA PIN isn't well formed",
    "id_number_format": "The ID number isn't 7 to 9 digits",
    "adult": "The holder is under 18",
    "issued_after_18": "The card was issued before the holder turned 18",
    "issued_not_future": "The issue date is in the future",
    "balances_add_up": "The opening and closing balances don't add up",
    "running_balance": "A row's balance doesn't add up",
    "dates_in_order": "Rows are out of order or outside the period",
    "no_hidden_text": "Text is hidden on the page",
    "no_instructions": "The document gives instructions to whoever processes it",
    "injection_screen": "The text looks like an attempt to instruct the AI",
    "id_number_on_record": "The ID number isn't the one on the bank's records",
    "net_pay_credited": "The payslip's net pay didn't arrive in the customer's account",
}


@dataclass
class Check:
    name: str
    label: str
    ok: bool | None  # None: couldn't be made
    detail: str
    fields: list[str] = field(default_factory=list)

    @property
    def fault(self) -> str:
        if self.name.startswith("name_on_"):
            return f"The name on the {self.name.removeprefix('name_on_')} isn't the customer's"
        return FAULTS.get(self.name, self.label)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "fault": self.fault,
            "ok": self.ok,
            "detail": self.detail,
            "fields": self.fields,
        }


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE


def _money(v: Decimal) -> str:
    return f"KES {v:,.2f}"


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _years(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def payslip(x: Extraction, today: date) -> list[Check]:
    checks: list[Check] = []
    gross, net = amount(x.value("gross_pay")), amount(x.value("net_pay"))
    total = amount(x.value("total_deductions"))
    if gross is not None and net is not None and total is not None:
        ok = _close(gross - total, net)
        checks.append(
            Check(
                "net_adds_up",
                "Net pay is gross pay less deductions",
                ok,
                f"{_money(gross)} less {_money(total)} is {_money(gross - total)}; the payslip says {_money(net)}",
                ["gross_pay", "total_deductions", "net_pay"],
            )
        )
    else:
        checks.append(
            Check(
                "net_adds_up", "Net pay is gross pay less deductions", None, "a figure is missing"
            )
        )

    period = _date((x.value("pay_period") or "") + "-01")
    if period is None or gross is None:
        checks.append(
            Check(
                "statutory",
                "Statutory deductions match the rates in force",
                None,
                "the pay period or gross pay is missing",
            )
        )
    elif not statutory.covers(period):
        checks.append(
            Check(
                "statutory",
                "Statutory deductions match the rates in force",
                None,
                f"the rates for {period:%B %Y} are not on file; the file starts in "
                f"{statutory.rules()['checked_from']:%B %Y}",
            )
        )
    else:
        expected = statutory.deductions(gross)
        for name, label, want in (
            ("nssf", "NSSF", expected.nssf),
            ("shif", "SHIF", expected.shif),
            ("housing_levy", "Housing Levy", expected.housing_levy),
            ("paye", "PAYE", expected.paye),
        ):
            got = amount(x.value(name))
            if got is None:
                checks.append(
                    Check(
                        f"statutory_{name}",
                        f"{label} matches the rates in force",
                        None,
                        f"{label} isn't on the payslip",
                        [name],
                    )
                )
                continue
            checks.append(
                Check(
                    f"statutory_{name}",
                    f"{label} matches the rates in force",
                    _close(got, want),
                    f"on {_money(gross)} gross, the rates give {_money(want)}; the payslip says {_money(got)}",
                    [name, "gross_pay"],
                )
            )

    month = today.replace(day=1)
    if period is not None:
        checks.append(
            Check(
                "period_not_future",
                "The pay period isn't in the future",
                period <= month,
                f"pay period {period:%B %Y}",
                ["pay_period"],
            )
        )
        back = (month.year - period.year) * 12 + month.month - period.month
        checks.append(
            Check(
                "recent",
                "The payslip is from the last three months",
                0 <= back <= 2,
                f"pay period {period:%B %Y}",
                ["pay_period"],
            )
        )
    paid = _date(x.value("pay_date"))
    if paid is not None:
        checks.append(
            Check(
                "paid_not_future",
                "The pay date isn't in the future",
                paid <= today,
                f"paid on {paid:%d %B %Y}",
                ["pay_date"],
            )
        )
    for name, label, pattern in (
        ("employer_kra_pin", "Employer's KRA PIN is well formed", r"^P\d{9}[A-Z]$"),
        ("employee_kra_pin", "Employee's KRA PIN is well formed", r"^A\d{9}[A-Z]$"),
    ):
        v = x.value(name)
        if v:
            checks.append(Check(f"{name}_format", label, bool(re.match(pattern, v)), v, [name]))
    return checks


def identity(x: Extraction, today: date) -> list[Check]:
    checks: list[Check] = []
    number = x.value("id_number")
    if number:
        checks.append(
            Check(
                "id_number_format",
                "The ID number is 7 to 9 digits",
                bool(re.match(r"^\d{7,9}$", number)),
                number,
                ["id_number"],
            )
        )
    born, issued = _date(x.value("date_of_birth")), _date(x.value("date_of_issue"))
    if born is not None:
        age = _years(born, today)
        checks.append(
            Check(
                "adult",
                "The holder is 18 or over",
                age >= 18,
                f"{age} years old",
                ["date_of_birth"],
            )
        )
    if born is not None and issued is not None:
        eighteen = (
            born.replace(year=born.year + 18)
            if not (born.month == 2 and born.day == 29)
            else date(born.year + 18, 3, 1)
        )
        checks.append(
            Check(
                "issued_after_18",
                "The card was issued after the holder turned 18",
                issued >= eighteen,
                f"issued {issued:%d %B %Y}; 18 on {eighteen:%d %B %Y}",
                ["date_of_issue", "date_of_birth"],
            )
        )
    if issued is not None:
        checks.append(
            Check(
                "issued_not_future",
                "The issue date isn't in the future",
                issued <= today,
                f"issued {issued:%d %B %Y}",
                ["date_of_issue"],
            )
        )
    return checks


def statement(x: Extraction, today: date) -> list[Check]:
    checks: list[Check] = []
    opening, closing = amount(x.value("opening_balance")), amount(x.value("closing_balance"))
    rows = [
        (
            r,
            amount(r.get("debit")) or Decimal(0),
            amount(r.get("credit")) or Decimal(0),
            amount(r.get("balance")),
        )
        for r in x.rows
    ]
    if opening is not None and closing is not None and rows:
        credits = sum((c for _, _, c, _ in rows), Decimal(0))
        debits = sum((d for _, d, _, _ in rows), Decimal(0))
        checks.append(
            Check(
                "balances_add_up",
                "Opening balance plus credits less debits is the closing balance",
                _close(opening + credits - debits, closing),
                f"{_money(opening)} plus {_money(credits)} less {_money(debits)} = {_money(opening + credits - debits)}; "
                f"the statement says {_money(closing)}",
                ["opening_balance", "closing_balance"],
            )
        )
        balance, broken = opening, []
        for row, debit, credit, shown in rows:
            balance = balance - debit + credit
            if shown is not None and not _close(balance, shown):
                broken.append(
                    f"{row.get('date')} {row.get('description')}: {_money(balance)} expected, {_money(shown)} shown"
                )
                balance = shown
        checks.append(
            Check(
                "running_balance",
                "Every row's balance adds up",
                not broken,
                "; ".join(broken[:3]) or f"{len(rows)} rows",
            )
        )
    else:
        checks.append(
            Check(
                "balances_add_up",
                "Opening balance plus credits less debits is the closing balance",
                None,
                "a balance or the rows are missing",
            )
        )
    start, end = _date(x.value("period_start")), _date(x.value("period_end"))
    dates = [_date(r.get("date")) for r, _, _, _ in rows]
    if start and end and dates and all(dates):
        in_order = all(a <= b for a, b in itertools.pairwise(dates))  # type: ignore[operator]
        inside = all(start <= d <= end for d in dates)  # type: ignore[operator]
        checks.append(
            Check(
                "dates_in_order",
                "Rows are in date order, inside the statement's period",
                in_order and inside,
                f"{start:%d %b} to {end:%d %b %Y}",
                ["period_start", "period_end"],
            )
        )
        checks.append(
            Check(
                "period_not_future",
                "The statement's period isn't in the future",
                end <= today,
                f"ends {end:%d %B %Y}",
                ["period_end"],
            )
        )
    return checks


CHECKS = {"payslip": payslip, "id": identity, "statement": statement}


def integrity(x: Extraction, hidden: list[str], visible_text: str | None) -> list[Check]:
    """Text planted for whoever processes the document: hidden on the page, or addressed to them."""
    checks = [
        Check(
            "no_hidden_text",
            "No text hidden on the page (white or too small to read)",
            not hidden,
            "; ".join(h[:160] for h in hidden) or "none",
        ),
        Check(
            "no_instructions",
            "No instructions addressed to whoever processes it",
            not x.instructions_found,
            "; ".join(i[:160] for i in x.instructions_found) or "none",
        ),
    ]
    if visible_text is not None:
        risk = screen(visible_text)
        checks.append(
            Check(
                "injection_screen",
                "The text passes the injection screen",
                risk.risk != "high",
                ", ".join(risk.matches) or "nothing found",
            )
        )
    return checks


FAILS_A_CHECK = "fails a check"


def apply(x: Extraction, found: list[Check]) -> None:
    """The cross-field checks feed back into confidence: a field a failed check rests on is no
    longer one a reviewer can skip, however sure the model was."""
    for check in found:
        if check.ok is False:
            for name in check.fields:
                f = x.fields.get(name)
                if f is not None and f.value is not None:
                    f.confidence = min(f.confidence, 0.5)
                    if FAILS_A_CHECK not in f.problems:
                        f.problems.append(FAILS_A_CHECK)


def review_reasons(x: Extraction, checks: list[Check]) -> list[str]:
    """Why a person must look at this document; empty when the desk can call it verified."""
    reasons = [f"{c.fault}: {c.detail}" for c in checks if c.ok is False]
    if x.failure:
        reasons.append(f"the document couldn't be read ({x.failure})")
    elif not x.legible:
        reasons.append("the image is too hard to read")
    for name in REQUIRED[x.kind]:
        f = x.fields.get(name)
        if f is not None and f.problems == [FAILS_A_CHECK]:
            continue  # the failed check is already a reason
        if f is None or f.confidence < CONFIDENT:
            why = ", ".join(f.problems) if f and f.problems else "low confidence"
            reasons.append(f"{name.replace('_', ' ')}: {why}")
    return reasons


def _name_tokens(name: str | None) -> set[str]:
    return set(re.sub(r"[^a-z ]", " ", (name or "").lower()).split())


def across(
    documents: list[tuple[Extraction, list[Check]]],
    *,
    customer_name: str,
    customer_id_number: str | None,
    credits: list[tuple[date, Decimal]],
) -> list[Check]:
    """Checks across an application's documents and the bank's own records of the customer."""
    checks: list[Check] = []
    names = {
        x.kind: x.value("employee_name") or x.value("full_name") or x.value("account_holder")
        for x, _ in documents
    }
    want = _name_tokens(customer_name)
    for kind, name in names.items():
        if name:
            checks.append(
                Check(
                    f"name_on_{kind}",
                    f"The name on the {kind} is the customer's",
                    _name_tokens(name) == want,
                    f"{name}; the bank has {customer_name}",
                )
            )
    card = next((x for x, _ in documents if x.kind == "id"), None)
    if card is not None and card.value("id_number") and customer_id_number:
        checks.append(
            Check(
                "id_number_on_record",
                "The ID number is the one the bank has on record",
                card.value("id_number") == customer_id_number,
                "compared with the customer's record",
            )
        )
    slip = next((x for x, _ in documents if x.kind == "payslip"), None)
    if slip is not None:
        net, paid = amount(slip.value("net_pay")), _date(slip.value("pay_date"))
        if net is not None and paid is not None:
            arrived = [
                d for d, a in credits if _close(a, net) and paid <= d <= paid + timedelta(days=3)
            ]
            checks.append(
                Check(
                    "net_pay_credited",
                    "The payslip's net pay arrived in the customer's account within 3 days",
                    bool(arrived),
                    f"{_money(net)} paid {paid:%d %B %Y}"
                    + (
                        f"; credited {arrived[0]:%d %B %Y}"
                        if arrived
                        else "; no matching credit found"
                    ),
                )
            )
    return checks
