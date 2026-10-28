"""The figures check: an answer may only state figures its sources contain.

Every amount, percentage, period and count in an answer must appear in a passage the model was
given, in a tool result, or in the customer's own message. A figure from nowhere is the most
damaging thing a bank's assistant can say ("the fee is KES 50" when it is KES 35), and the
easiest to check without a model. An answer that fails is regenerated once with a note naming
the unsupported figures; if it fails again the customer gets a safe answer instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Amounts with or without "KES"/"Ksh", with thousands separators and decimals; percentages;
# bare numbers with a unit (days, months, years, hours, minutes, times); and plain numbers of
# 3+ digits. Small bare numbers ("2 cards", "step 3") are left alone.
_NUMBER = re.compile(
    r"(?i)(?:(?:kes|ksh|kshs|sh|usd|gbp|eur)\.?\s?)?"
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
    r"(\s?%|\s?(?:per\s?cent|percent))?"
    r"(\s?(?:working\s+)?(?:days?|months?|years?|hours?|minutes?|weeks?|times))?"
)
_ALWAYS_FINE = {Decimal(n) for n in range(0, 13)}  # small counts, months of the year
_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")


@dataclass(frozen=True)
class Figure:
    text: str
    value: Decimal
    kind: str  # "amount", "percent", "period", "number"


def figures(text: str) -> list[Figure]:
    text = _PLACEHOLDER.sub(" ", text)
    text = _DATE.sub(" ", text)
    out: list[Figure] = []
    for m in _NUMBER.finditer(text):
        whole, frac, percent, unit = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            value = Decimal(whole.replace(",", "") + (f".{frac}" if frac else ""))
        except InvalidOperation:
            continue
        has_currency = bool(re.match(r"(?i)(kes|ksh|kshs|sh|usd|gbp|eur)", m.group(0)))
        if percent:
            kind = "percent"
        elif unit:
            kind = "period"
        elif has_currency or "," in whole or frac:
            kind = "amount"
        else:
            kind = "number"
        if kind == "number" and len(whole) < 3:
            continue
        out.append(Figure(m.group(0).strip(), value.normalize(), kind))
    return out


@dataclass(frozen=True)
class FiguresResult:
    checked: int
    unsupported: list[str]

    @property
    def ok(self) -> bool:
        return not self.unsupported


def check(answer: str, sources: Iterable[str]) -> FiguresResult:
    """Which figures in `answer` don't appear in any of `sources`."""

    known: set[Decimal] = set(_ALWAYS_FINE)
    for source in sources:
        for f in figures(source):
            known.add(f.value)
    found = figures(answer)
    unsupported = [f.text for f in found if f.value not in known and f.value not in _ALWAYS_FINE]
    return FiguresResult(checked=len(found), unsupported=sorted(set(unsupported)))
