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


def test_an_image_too_large_to_decode_is_refused_before_it_is_decoded() -> None:
    import io

    from PIL import Image

    from kaskazi.documents import desk

    small_file = io.BytesIO()
    Image.new("L", (12000, 12000), 255).save(small_file, "PNG", optimize=True)
    assert len(small_file.getvalue()) < desk.MAX_BYTES  # small on disk, 144 megapixels inside
    # Pillow only warns at this size; the desk refuses it.
    with pytest.warns(Image.DecompressionBombWarning), pytest.raises(desk.DeskError, match="large"):
        desk._validate(small_file.getvalue())

    photo = io.BytesIO()
    Image.new("RGB", (4000, 3000), (200, 200, 200)).save(photo, "JPEG")
    assert desk._validate(photo.getvalue()) == "image/jpeg"


def _expanding_pdf(expands_to: int) -> bytes:
    """A one-page PDF whose drawing instructions are a small file that inflates to `expands_to`."""
    import zlib

    packed = zlib.compress(b" " * expands_to, 9)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        f"<< /Length {len(packed)} /Filter /FlateDecode >>\nstream\n".encode()
        + packed
        + b"\nendstream",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


async def test_a_pdf_built_to_expand_is_refused_without_being_expanded() -> None:
    from kaskazi.documents import desk

    data = _expanding_pdf(30_000_000)  # about 30 KB on disk
    assert len(data) < 100_000 and desk._validate(data) == "application/pdf"
    with pytest.raises(desk.DeskError, match="can't be read"):
        await desk.read_text(data)
