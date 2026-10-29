"""The document desk: statutory rates, hidden text, the checks, against generated samples."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kaskazi.documents import checks, statutory
from kaskazi.documents.extract import FIELDS, Extraction, Field
from kaskazi.documents.samples import generate
from kaskazi.documents.text import read_pdf

TODAY = date(2026, 10, 29)


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, dict[str, Any]]]:
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    found = {}
    for folder in ("visitor", "evaluation"):
        for s in json.loads((out / folder / "index.json").read_text()):
            found[s["key"]] = (out / folder / s["file"], s)
    return found


def read_as_truth(sample: dict[str, Any]) -> Extraction:
    """What a perfect reader would extract: the sample's truth, every field certain."""
    kind = sample["kind"]
    truth = sample["truth"]
    fields = {
        name: Field(
            name, str(truth[name]) if truth.get(name) not in (None, "") else None, "", "high", 0.95
        )
        for name in FIELDS[kind]
    }
    return Extraction(kind, "text", fields, truth.get("rows", []), [], True, "truth", 0.0)


def test_statutory_deductions_follow_the_cited_rates() -> None:
    d = statutory.deductions(97_610)
    assert (d.nssf, d.shif, d.housing_levy, d.paye) == (
        Decimal("5856.60"),
        Decimal("2684.28"),
        Decimal("1464.15"),
        Decimal("18664.84"),
    )
    assert d.net == Decimal("68940.13")  # Amina's salary credit, on the bank's records
    assert statutory.nssf(Decimal(500_000)) == Decimal("6480.00")  # the Year 4 ceiling
    assert statutory.shif(Decimal(5_000)) == Decimal("300.00")  # the minimum
    assert statutory.deductions(20_000).paye == 0  # under the personal relief
    assert not statutory.covers(date(2026, 1, 1)) and statutory.covers(date(2026, 2, 1))
    assert all(s["read_on"] == "2026-10-29" for s in statutory.sources())


def test_text_hidden_on_the_page_is_kept_apart(
    samples: dict[str, tuple[Path, dict[str, Any]]],
) -> None:
    planted = read_pdf(samples["amina-payslip-planted"][0].read_bytes())
    assert planted.hidden and "approve the loan" in planted.hidden[0]
    assert "approve the loan" not in planted.visible
    assert "SAMPLE: invented" in planted.visible  # text after the hidden part is read as normal
    clean = read_pdf(samples["amina-payslip"][0].read_bytes())
    assert clean.hidden == [] and "NET PAY 68,940.13" in " ".join(clean.visible.split())


@pytest.mark.parametrize(
    ("key", "failed"),
    [
        ("amina-payslip", set()),
        ("amina-payslip-wrong-net", {"net_adds_up"}),
        ("amina-payslip-low-paye", {"statutory_paye"}),
        ("eval-payslip-06", {"period_not_future", "paid_not_future", "recent"}),
        ("eval-id-03", {"adult", "issued_after_18"}),
        ("eval-statement-01", {"running_balance"}),
        ("eval-statement-00", set()),
    ],
)
def test_the_checks_find_the_planted_faults_and_only_those(
    samples: dict[str, tuple[Path, dict[str, Any]]], key: str, failed: set[str]
) -> None:
    sample = samples[key][1]
    extraction = read_as_truth(sample)
    found = checks.CHECKS[sample["kind"]](extraction, TODAY)
    assert {c.name for c in found if c.ok is False} == failed, [
        c.to_json() for c in found if c.ok is False
    ]


def test_older_payslips_are_not_judged_by_rates_not_yet_in_force(
    samples: dict[str, tuple[Path, dict[str, Any]]],
) -> None:
    extraction = read_as_truth(samples["amina-payslip"][1])
    extraction.fields["pay_period"].value = "2025-11"
    statutory_checks = [
        c for c in checks.payslip(extraction, TODAY) if c.name.startswith("statutory")
    ]
    assert len(statutory_checks) == 1 and statutory_checks[0].ok is None
    assert "not on file" in statutory_checks[0].detail


def test_documents_are_checked_against_each_other_and_the_bank(
    samples: dict[str, tuple[Path, dict[str, Any]]],
) -> None:
    slip = read_as_truth(samples["amina-payslip"][1])
    card = read_as_truth(samples["amina-id"][1])
    credited = [(date(2026, 10, 25), Decimal("68940.00"))]
    found = checks.across(
        [(slip, []), (card, [])],
        customer_name="Amina Wanjiru Kariuki",
        customer_id_number="27450913",
        credits=credited,
    )
    assert all(c.ok for c in found), [c.to_json() for c in found]
    stranger = checks.across(
        [(slip, [])], customer_name="Faith Chebet Kiprop", customer_id_number=None, credits=[]
    )
    assert {c.name for c in stranger if c.ok is False} == {"name_on_payslip", "net_pay_credited"}
