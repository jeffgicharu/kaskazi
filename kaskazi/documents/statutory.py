"""Kenya's statutory deductions from monthly pay, worked out from the cited rules file.

The document desk recomputes a payslip's NSSF, SHIF, Housing Levy and PAYE from its gross pay and
compares them with what the payslip says. The rates, their sources and the dates they were read
are in statutory.toml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

RULES = Path(__file__).with_name("statutory.toml")
CENT = Decimal("0.01")


@cache
def rules() -> dict[str, Any]:
    return tomllib.loads(RULES.read_text())


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _percent(amount: Decimal, rate: float) -> Decimal:
    return amount * Decimal(str(rate)) / 100


@dataclass(frozen=True)
class Deductions:
    gross: Decimal
    nssf: Decimal
    shif: Decimal
    housing_levy: Decimal
    taxable: Decimal
    paye: Decimal

    @property
    def total(self) -> Decimal:
        return self.nssf + self.shif + self.housing_levy + self.paye

    @property
    def net(self) -> Decimal:
        return self.gross - self.total


def covers(period: date) -> bool:
    """Whether the rates on file are the ones in force for a pay period starting on `period`."""
    start: date = rules()["checked_from"]
    return period >= start


def nssf(gross: Decimal) -> Decimal:
    r = rules()["nssf"]
    lower, upper = Decimal(r["lower_earnings_limit"]), Decimal(r["upper_earnings_limit"])
    tier_one = _percent(min(gross, lower), r["rate"])
    tier_two = _percent(max(Decimal(0), min(gross, upper) - lower), r["rate"])
    return _money(tier_one + tier_two)


def shif(gross: Decimal) -> Decimal:
    r = rules()["shif"]
    return _money(max(_percent(gross, r["rate"]), Decimal(r["minimum"])))


def housing_levy(gross: Decimal) -> Decimal:
    return _money(_percent(gross, rules()["housing_levy"]["rate"]))


def paye(taxable: Decimal) -> Decimal:
    r = rules()["paye"]
    tax, lower = Decimal(0), Decimal(0)
    for band in r["bands"]:
        upper = Decimal(band["up_to"]) if "up_to" in band else None
        if taxable <= lower:
            break
        top = taxable if upper is None else min(taxable, upper)
        tax += _percent(top - lower, band["rate"])
        if upper is None:
            break
        lower = upper
    return _money(max(Decimal(0), tax - Decimal(r["personal_relief"])))


def deductions(gross: Decimal | int | float) -> Deductions:
    """The statutory deductions from a month's gross pay, by the rates on file."""
    g = _money(Decimal(str(gross)))
    n, s, h = nssf(g), shif(g), housing_levy(g)
    pension = min(n, Decimal(rules()["paye"]["pension_deduction_limit"]))
    taxable = _money(g - pension - s - h)
    return Deductions(g, n, s, h, taxable, paye(taxable))


def sources() -> list[dict[str, str]]:
    """Where each rate was read, and when, for the reviewer and the documentation."""
    return [
        {"rate": name, "source": rules()[name]["source"], "read_on": str(rules()[name]["read_on"])}
        for name in ("paye", "shif", "housing_levy", "nssf")
    ]
