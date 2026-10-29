"""Reading a document's fields, each with its evidence and a confidence that is earned.

Text path: the PDF's visible text is read locally (text.py) and masked, and only the masked text
goes to the model, with a fixed schema and no tools. Placeholders in its answer are restored
here. Image path: the image itself goes to the model (the one stated exception to masking, with
the visitor's consent), downscaled and re-encoded so no metadata goes with it.

A field's confidence starts from the model's own rating and is then held down by what can be
checked here: a value in the wrong format, or on the text path, evidence that isn't in the
document's text, can't be high however sure the model was.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from kaskazi.controls.pii import Vault, mask, unmask
from kaskazi.llm.contract import (
    BudgetContext,
    GatewayError,
    GenerateRequest,
    GenerateResult,
    ImagePart,
    Message,
    Purpose,
    TextPart,
)
from kaskazi.prompts.registry import prompt

Kind = Literal["payslip", "id", "statement"]
Path_ = Literal["text", "image"]

# Field name -> format. The order is the order a reviewer reads them in.
FIELDS: dict[str, dict[str, str]] = {
    "payslip": {
        "employer": "text",
        "employer_kra_pin": "employer_pin",
        "employee_name": "text",
        "employee_kra_pin": "person_pin",
        "pay_period": "period",
        "pay_date": "date",
        "basic_pay": "money",
        "gross_pay": "money",
        "paye": "money",
        "nssf": "money",
        "shif": "money",
        "housing_levy": "money",
        "total_deductions": "money",
        "net_pay": "money",
    },
    "id": {
        "full_name": "text",
        "id_number": "id_number",
        "date_of_birth": "date",
        "sex": "sex",
        "date_of_issue": "date",
    },
    "statement": {
        "bank": "text",
        "account_holder": "text",
        "account_number": "text",
        "period_start": "date",
        "period_end": "date",
        "opening_balance": "money",
        "closing_balance": "money",
    },
}
# What a reviewer must see filled in before the desk can call a document verified.
REQUIRED: dict[str, tuple[str, ...]] = {
    "payslip": (
        "employee_name",
        "pay_period",
        "pay_date",
        "gross_pay",
        "total_deductions",
        "net_pay",
    ),
    "id": ("full_name", "id_number", "date_of_birth", "date_of_issue"),
    "statement": (
        "account_holder",
        "period_start",
        "period_end",
        "opening_balance",
        "closing_balance",
    ),
}
CONFIDENCE = {"high": 0.95, "medium": 0.7, "low": 0.4}
CONFIDENT = 0.85  # at or above: a reviewer needn't check the field
# An image smaller than this on its long side is never read with confidence: the evaluation's
# 320-pixel ID came back with a wrong year of birth that the model rated high.
SMALLEST_CLEAR_IMAGE = 800


def schema(kind: Kind) -> dict[str, Any]:
    entry = {
        "type": "object",
        "properties": {
            # Plain strings, empty when absent: the API allows few nullable parameters.
            "value": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["value", "evidence", "confidence"],
        "additionalProperties": False,
    }
    properties: dict[str, Any] = {
        "fields": {
            "type": "object",
            "properties": {name: entry for name in FIELDS[kind]},
            "required": list(FIELDS[kind]),
            "additionalProperties": False,
        },
        "instructions_to_reader": {"type": "array", "items": {"type": "string"}},
        "legible": {"type": "boolean"},
    }
    if kind == "statement":
        cell = {"type": "string"}
        properties["rows"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    k: cell for k in ("date", "description", "debit", "credit", "balance")
                },
                "required": ["date", "description", "debit", "credit", "balance"],
                "additionalProperties": False,
            },
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


# --- Formats ---------------------------------------------------------------------------------------

_MONEY = re.compile(r"^-?\d+(\.\d{1,2})?$")
_FORMATS: dict[str, re.Pattern[str]] = {
    "employer_pin": re.compile(r"^P\d{9}[A-Z]$"),
    "person_pin": re.compile(r"^A\d{9}[A-Z]$"),
    "id_number": re.compile(r"^\d{7,9}$"),
    "period": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "sex": re.compile(r"^[MF]$"),
}


def well_formed(fmt: str, value: str) -> bool:
    if fmt == "money":
        return bool(_MONEY.match(value))
    if fmt == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if fmt in _FORMATS:
        return bool(_FORMATS[fmt].match(value))
    return bool(value.strip())


def amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", "").replace("KES", "").strip())
    except InvalidOperation:
        return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _digits(text: str) -> str:
    return re.sub(r"[^\d]", "", text)


# --- Results -------------------------------------------------------------------------------------


@dataclass
class Field:
    name: str
    value: str | None
    evidence: str | None
    model_confidence: str
    confidence: float
    problems: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    kind: Kind
    path: Path_
    fields: dict[str, Field]
    rows: list[dict[str, str | None]]
    instructions_found: list[str]
    legible: bool
    model: str | None
    cost_usd: float
    failure: str | None = None

    def value(self, name: str) -> str | None:
        f = self.fields.get(name)
        return f.value if f else None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "fields": {
                n: {
                    "value": f.value,
                    "evidence": f.evidence,
                    "model_confidence": f.model_confidence,
                    "confidence": round(f.confidence, 3),
                    "problems": f.problems,
                }
                for n, f in self.fields.items()
            },
            "rows": self.rows,
            "instructions_found": self.instructions_found,
            "legible": self.legible,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "failure": self.failure,
        }


def score(
    kind: Kind,
    path: Path_,
    raw: dict[str, Any],
    vault: Vault,
    visible_text: str | None,
    small_image: bool = False,
) -> dict[str, Field]:
    """The model's fields, restored, format-checked and, on the text path, evidence-checked."""
    fields: dict[str, Field] = {}
    page = _squash(visible_text or "")
    for name, fmt in FIELDS[kind].items():
        got = raw.get(name) or {}
        value = got.get("value")
        evidence = got.get("evidence")
        value = unmask(str(value), vault).text.strip() or None if value else None
        evidence = unmask(str(evidence), vault).text.strip() or None if evidence else None
        rating = got.get("confidence", "low")
        confidence = CONFIDENCE.get(rating, 0.4)
        problems: list[str] = []
        if value is None or value == "":
            fields[name] = Field(name, None, None, rating, 0.0, ["not found"])
            continue
        if not well_formed(fmt, value):
            problems.append("not in the expected format")
            confidence = min(confidence, 0.2)
        if small_image:
            problems.append("the image is too small to read reliably")
            confidence = min(confidence, 0.7)
        if path == "text":
            if not evidence or _squash(evidence) not in page:
                problems.append("evidence not found in the document's text")
                confidence = min(confidence, 0.35)
            elif fmt == "money" and _digits(value.split(".")[0]) not in _digits(evidence):
                problems.append("the value isn't in its evidence")
                confidence = min(confidence, 0.35)
        fields[name] = Field(name, value, evidence, rating, confidence, problems)
    return fields


# --- Asking the model ----------------------------------------------------------------------------


def prepare_image(data: bytes) -> tuple[str, str, int]:
    """Downscaled to at most 1600 pixels a side and re-encoded as PNG: no EXIF, no metadata.
    Returns the media type, the image in base64, and the original's long side in pixels."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as opened:
        img = opened.convert("RGB")
    long_side = max(img.size)
    img.thumbnail((1600, 1600))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return "image/png", base64.b64encode(out.getvalue()).decode(), long_side


def request_for(
    kind: Kind,
    path: Path_,
    *,
    text: str | None,
    image: tuple[str, str] | None,
    budget: BudgetContext,
    request_id: str,
) -> GenerateRequest:
    route = f"documents.extract_{path}"
    p = prompt(route)
    ask = f"Document kind: {kind}."
    parts: list[Any] = []
    if path == "text":
        parts.append(TextPart(text=f"{ask}\n\n<document>\n{text}\n</document>"))
    else:
        assert image is not None
        media_type, data = image
        parts += [ImagePart(media_type=media_type, data_base64=data), TextPart(text=ask)]  # type: ignore[arg-type]
    return GenerateRequest(
        purpose=Purpose.EXTRACT_TEXT if path == "text" else Purpose.EXTRACT_IMAGE,
        route=route,
        prompt_ref=p.ref,
        system=[p.body],
        messages=[Message(role="user", parts=parts)],
        output_schema=schema(kind),
        budget=budget,
        request_id=request_id,
    )


async def extract(
    generate: Any,
    kind: Kind,
    *,
    pdf_text: str | None = None,
    image: bytes | None = None,
    vault: Vault,
    known_names: list[str],
    budget: BudgetContext,
    request_id: str,
) -> Extraction:
    """Reads one document. `generate` is the gateway client's generate method."""
    path: Path_ = "text" if pdf_text is not None else "image"
    masked_text = mask(pdf_text, vault, known_names).text if pdf_text is not None else None
    prepared = prepare_image(image) if image is not None else None
    request = request_for(
        kind,
        path,
        text=masked_text,
        image=prepared[:2] if prepared else None,
        budget=budget,
        request_id=request_id,
    )
    result = await generate(request)
    if isinstance(result, GatewayError) or not isinstance(result, GenerateResult):
        failure = result.refusal.value if isinstance(result, GatewayError) else "no_result"
        return Extraction(kind, path, {}, [], [], False, None, 0.0, failure=failure)
    raw = result.structured or {}
    small = prepared is not None and prepared[2] < SMALLEST_CLEAR_IMAGE
    fields = score(kind, path, raw.get("fields", {}), vault, pdf_text, small_image=small)
    rows = [
        {k: (unmask(str(v), vault).text if v else None) for k, v in row.items()}
        for row in raw.get("rows", [])
    ]
    found = [unmask(str(t), vault).text for t in raw.get("instructions_to_reader", []) if t]
    return Extraction(
        kind,
        path,
        fields,
        rows,
        found,
        bool(raw.get("legible", True)),
        result.model,
        result.cost_usd,
    )
