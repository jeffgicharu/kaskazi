"""The document desk: a loan application, its documents, and a person's decision.

An application moves from documents_requested (the customer adds a payslip and an ID card), to
under_review (submitted: the desk has checked the documents against each other and the bank's
records, and worked out affordability from the verified payslip), to approved or declined by a
reviewer. The AI reads and checks; a person decides every loan, and an approved loan is paid out
(in the pretend core bank) only then.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from kaskazi.audit.log import record
from kaskazi.controls.pii import Kind, Vault, display_form, mask
from kaskazi.corebank.client import CoreBank
from kaskazi.db.pool import visitor_tx
from kaskazi.documents import checks
from kaskazi.documents.extract import FIELDS, Extraction, Field, amount, extract
from kaskazi.documents.extract import Kind as DocKind
from kaskazi.documents.text import read_pdf
from kaskazi.llm.client import GatewayClient
from kaskazi.llm.contract import BudgetContext
from kaskazi.release.fingerprint import ROOT

SAMPLES = ROOT / "samples" / "visitor"
MAX_BYTES = 3 * 1024 * 1024
MAX_PAGES = 3
NEEDED: tuple[DocKind, ...] = ("payslip", "id")
TITLES = {"payslip": "Payslip", "id": "Identity card", "statement": "Bank statement"}
LABELS = {
    "employer": "Employer",
    "employer_kra_pin": "Employer's KRA PIN",
    "employee_name": "Employee",
    "employee_kra_pin": "Employee's KRA PIN",
    "pay_period": "Pay period",
    "pay_date": "Pay date",
    "basic_pay": "Basic pay",
    "gross_pay": "Gross pay",
    "paye": "PAYE",
    "nssf": "NSSF",
    "shif": "SHIF",
    "housing_levy": "Housing Levy",
    "total_deductions": "Total deductions",
    "net_pay": "Net pay",
    "full_name": "Full name",
    "id_number": "ID number",
    "date_of_birth": "Date of birth",
    "sex": "Sex",
    "date_of_issue": "Date of issue",
    "bank": "Bank",
    "account_holder": "Account holder",
    "account_number": "Account number",
    "period_start": "From",
    "period_end": "To",
    "opening_balance": "Opening balance",
    "closing_balance": "Closing balance",
}
# Fields a customer sees partly hidden, as everywhere else in the app.
SHOWN_AS = {
    "employee_kra_pin": Kind.KRA_PIN,
    "employer_kra_pin": Kind.KRA_PIN,
    "id_number": Kind.ID,
}


class DeskError(Exception):
    """Something the customer or reviewer can put right; the message says what."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Who:
    sandbox_id: uuid.UUID
    customer_id: uuid.UUID
    ip_hash: str | None


# --- Samples and intake -------------------------------------------------------------------------


def samples() -> list[dict[str, Any]]:
    """The sample documents a visitor can give, without their ground truth."""
    index = SAMPLES / "index.json"
    if not index.exists():
        return []
    return [
        {
            **{k: s[k] for k in ("key", "kind", "title", "about", "persona", "file")},
            "has_fault": bool(s["defects"]),
        }
        for s in json.loads(index.read_text())
    ]


def sample_bytes(key: str) -> tuple[dict[str, Any], bytes]:
    found = next((s for s in samples() if s["key"] == key), None)
    if found is None:
        raise DeskError("There is no such sample document.", 404)
    return found, (SAMPLES / found["file"]).read_bytes()


def sniff(data: bytes) -> str:
    """The file's type from its first bytes, never from its name."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise DeskError("Only PDF, PNG and JPEG files can be read.")


def _validate(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise DeskError("A document can be 3 MB at most.")
    media_type = sniff(data)
    if media_type == "application/pdf":
        from pypdf import PdfReader

        try:
            pages = len(PdfReader(io.BytesIO(data)).pages)
        except Exception as exc:  # pypdf raises many kinds on a broken file
            raise DeskError("That PDF can't be opened.") from exc
        if pages > MAX_PAGES:
            raise DeskError(f"A document can have {MAX_PAGES} pages at most.")
    else:
        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
        except Exception as exc:
            raise DeskError("That image can't be opened.") from exc
    return media_type


# --- Applications -------------------------------------------------------------------------------


async def create_application(
    pool: asyncpg.Pool, who: Who, amount_kes: int, term_months: int
) -> uuid.UUID:
    app_id = uuid.uuid4()
    async with visitor_tx(pool, who.sandbox_id) as conn:
        open_one = await conn.fetchval(
            "SELECT id FROM app.loan_applications WHERE status IN ('documents_requested', 'under_review')"
        )
        if open_one is not None:
            raise DeskError("You already have an application in progress.", 409)
        await conn.execute(
            "INSERT INTO app.loan_applications (id, sandbox_id, customer_id, amount, term_months) "
            "VALUES ($1, $2, $3, $4, $5)",
            app_id,
            who.sandbox_id,
            who.customer_id,
            amount_kes,
            term_months,
        )
        await record(
            conn,
            "application_started",
            {"application": str(app_id), "amount": amount_kes, "term_months": term_months},
        )
    return app_id


async def add_document(
    pool: asyncpg.Pool,
    gateway: GatewayClient,
    bank: CoreBank,
    who: Who,
    application_id: uuid.UUID,
    *,
    kind: DocKind,
    data: bytes,
    filename: str,
    source: str,
    sample_key: str | None = None,
    image_consent: bool = False,
) -> dict[str, Any]:
    """Takes a document, reads it, checks it, and keeps the result. Returns it as the customer
    sees it."""
    media_type = _validate(data)
    if media_type != "application/pdf" and not image_consent:
        raise DeskError(
            "Reading an image means sending it to an AI service. Tick the box to agree, or give a PDF."
        )
    async with visitor_tx(pool, who.sandbox_id) as conn:
        status = await conn.fetchval(
            "SELECT status FROM app.loan_applications WHERE id = $1", application_id
        )
        if status is None:
            raise DeskError("There is no such application.", 404)
        if status != "documents_requested":
            raise DeskError("This application isn't taking documents now.", 409)

    pdf = read_pdf(data) if media_type == "application/pdf" else None
    if pdf is not None and not pdf.visible.strip():
        raise DeskError(
            "This PDF has no text to read (it may be a scan). Give a photo of it instead."
        )
    identity = await bank.call(
        "GET", "/v1/customer/identity", sandbox_id=who.sandbox_id, customer_id=who.customer_id
    )
    names = [*identity["full_name"].split(), identity["full_name"]]
    doc_id = uuid.uuid4()
    x = await extract(
        gateway.generate,
        kind,
        pdf_text=pdf.visible if pdf else None,
        image=None if pdf else data,
        vault=Vault(),
        known_names=names,
        budget=BudgetContext(sandbox_id=str(who.sandbox_id), ip_hash=who.ip_hash),
        request_id=f"document-{doc_id.hex[:12]}",
    )
    found = checks.CHECKS[kind](x, date.today()) + checks.integrity(
        x, pdf.hidden if pdf else [], pdf.visible if pdf else None
    )
    checks.apply(x, found)
    reasons = checks.review_reasons(x, found)
    doc_status = "unreadable" if x.failure else ("needs_review" if reasons else "verified")
    async with visitor_tx(pool, who.sandbox_id) as conn:
        # One document of each kind: a new payslip replaces the one before.
        await conn.execute(
            "UPDATE app.documents SET status = 'rejected', review_reasons = '[\"replaced by a newer one\"]' "
            "WHERE application_id = $1 AND kind = $2 AND status <> 'rejected'",
            application_id,
            kind,
        )
        await conn.execute(
            """INSERT INTO app.documents (id, sandbox_id, application_id, kind, source, sample_key,
                   filename, media_type, content, sha256, path, status, extraction, checks,
                   review_reasons, hidden_text)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)""",
            doc_id,
            who.sandbox_id,
            application_id,
            kind,
            source,
            sample_key,
            filename[:120],
            media_type,
            data,
            hashlib.sha256(data).hexdigest(),
            x.path,
            doc_status,
            json.dumps(x.to_json()),
            json.dumps([c.to_json() for c in found]),
            json.dumps(reasons),
            json.dumps(pdf.hidden if pdf else []),
        )
        await record(conn, "document_read", _audit(doc_id, kind, data, x, found, reasons, names))
    return await document(pool, who.sandbox_id, doc_id)


def _audit(
    doc_id: uuid.UUID,
    kind: str,
    data: bytes,
    x: Extraction,
    found: list[checks.Check],
    reasons: list[str],
    names: list[str],
) -> dict[str, Any]:
    """What the audit keeps of a document: its hash and masked fields, never its content."""
    vault = Vault()

    def hide(text: str | None) -> str | None:
        return mask(text, vault, names).text if text else text

    return {
        "document": str(doc_id),
        "kind": kind,
        "sha256": hashlib.sha256(data).hexdigest(),
        "path": x.path,
        "model": x.model,
        "cost_usd": x.cost_usd,
        "fields": {
            n: {"value": hide(f.value), "confidence": round(f.confidence, 3)}
            for n, f in x.fields.items()
        },
        "failed_checks": [c.name for c in found if c.ok is False],
        "instructions_found": len(x.instructions_found),
        "review_reasons": [hide(r) for r in reasons],
    }


def _fields_for_people(extraction: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    shown = []
    for name in FIELDS[kind]:
        f = extraction.get("fields", {}).get(name)
        if f is None:
            continue
        value = f["value"]
        if value and name in SHOWN_AS:
            value = display_form(SHOWN_AS[name], value)
        shown.append(
            {
                "name": name,
                "label": LABELS.get(name, name.replace("_", " ").capitalize()),
                "value": value,
                # The words the value was read from; for a reviewer to find it on the page.
                "evidence": f.get("evidence") if name not in SHOWN_AS else None,
                "confidence": f["confidence"],
                "problems": f["problems"],
            }
        )
    return shown


async def document(pool: asyncpg.Pool, sandbox_id: uuid.UUID, doc_id: uuid.UUID) -> dict[str, Any]:
    async with visitor_tx(pool, sandbox_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, kind, source, sample_key, filename, media_type, path, status, extraction, "
            "checks, review_reasons, created_at FROM app.documents WHERE id = $1",
            doc_id,
        )
    if row is None:
        raise DeskError("There is no such document.", 404)
    return _document_json(row)


def _document_json(row: Any) -> dict[str, Any]:
    extraction = _load(row["extraction"]) or {}
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "title": TITLES[row["kind"]],
        "source": row["source"],
        "filename": row["filename"],
        "media_type": row["media_type"],
        "path": row["path"],
        "status": row["status"],
        "fields": _fields_for_people(extraction, row["kind"]),
        "checks": _load(row["checks"]) or [],
        "review_reasons": _load(row["review_reasons"]) or [],
        "model": extraction.get("model"),
        "created_at": row["created_at"].isoformat(),
    }


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def application(
    pool: asyncpg.Pool, sandbox_id: uuid.UUID, application_id: uuid.UUID | None = None
) -> dict[str, Any] | None:
    """An application with its documents; the latest one if no id is given."""
    async with visitor_tx(pool, sandbox_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app.loan_applications WHERE ($1::uuid IS NULL OR id = $1) "
            "ORDER BY created_at DESC LIMIT 1",
            application_id,
        )
        if row is None:
            return None
        docs = await conn.fetch(
            "SELECT id, kind, source, sample_key, filename, media_type, path, status, extraction, "
            "checks, review_reasons, created_at FROM app.documents "
            "WHERE application_id = $1 AND status <> 'rejected' ORDER BY created_at",
            row["id"],
        )
    have = {d["kind"] for d in docs}
    return {
        "id": str(row["id"]),
        "amount": row["amount"],
        "term_months": row["term_months"],
        "status": row["status"],
        "documents": [_document_json(d) for d in docs],
        "still_needed": [k for k in NEEDED if k not in have],
        "assessment": _load(row["assessment"]),
        "decision": _load(row["decision"]),
        "created_at": row["created_at"].isoformat(),
    }


async def submit(
    pool: asyncpg.Pool, bank: CoreBank, who: Who, application_id: uuid.UUID
) -> dict[str, Any]:
    """Sends a complete application to a reviewer, with the desk's assessment."""
    current = await application(pool, who.sandbox_id, application_id)
    if current is None:
        raise DeskError("There is no such application.", 404)
    if current["status"] != "documents_requested":
        raise DeskError("This application has already been sent.", 409)
    if current["still_needed"]:
        raise DeskError(
            "Add a "
            + " and an ".join(TITLES[k].lower() for k in current["still_needed"])
            + " first."
        )
    assessment = await assess(pool, bank, who, application_id)
    async with visitor_tx(pool, who.sandbox_id) as conn:
        await conn.execute(
            "UPDATE app.loan_applications SET status = 'under_review', assessment = $2, updated_at = now() "
            "WHERE id = $1",
            application_id,
            json.dumps(assessment),
        )
        await record(
            conn,
            "application_submitted",
            {
                "application": str(application_id),
                "failed_checks": [c["name"] for c in assessment["checks"] if c["ok"] is False],
                "fits": assessment["affordability"].get("requested_fits"),
            },
        )
    result = await application(pool, who.sandbox_id, application_id)
    assert result is not None
    return result


async def assess(
    pool: asyncpg.Pool, bank: CoreBank, who: Who, application_id: uuid.UUID
) -> dict[str, Any]:
    """The documents against each other and the bank's records, and affordability from the
    payslip's net pay by the bank's lending rules."""
    async with visitor_tx(pool, who.sandbox_id) as conn:
        app_row = await conn.fetchrow(
            "SELECT * FROM app.loan_applications WHERE id = $1", application_id
        )
        rows = await conn.fetch(
            "SELECT kind, extraction, checks FROM app.documents WHERE application_id = $1 AND status <> 'rejected'",
            application_id,
        )
    docs: list[tuple[Extraction, list[checks.Check]]] = [
        (_extraction(r["kind"], _load(r["extraction"])), []) for r in rows
    ]
    identity = await bank.call(
        "GET", "/v1/customer/identity", sandbox_id=who.sandbox_id, customer_id=who.customer_id
    )
    credits: list[tuple[date, Decimal]] = []
    for account in await bank.call(
        "GET", "/v1/accounts", sandbox_id=who.sandbox_id, customer_id=who.customer_id
    ):
        txs = await bank.call(
            "GET",
            f"/v1/accounts/{account['id']}/transactions",
            sandbox_id=who.sandbox_id,
            customer_id=who.customer_id,
            params={"limit": 50},
        )
        for t in txs["transactions"]:
            if float(t["amount"]) > 0:
                credits.append(
                    (
                        datetime.fromisoformat(t["at"]).astimezone(UTC).date(),
                        Decimal(str(t["amount"])),
                    )
                )
    across = checks.across(
        docs,
        customer_name=identity["full_name"],
        customer_id_number=identity["national_id"],
        credits=credits,
    )
    slip = next((x for x, _ in docs if x.kind == "payslip"), None)
    net = amount(slip.value("net_pay")) if slip else None
    affordability: dict[str, Any] = {"net_pay_from_payslip": str(net) if net is not None else None}
    if net is not None:
        result = await bank.call(
            "POST",
            "/v1/loans/personal/eligibility",
            sandbox_id=who.sandbox_id,
            customer_id=who.customer_id,
            json={
                "amount": app_row["amount"],
                "term_months": app_row["term_months"],
                "net_monthly_pay": int(net),
            },
        )
        affordability.update(result)
    return {"checks": [c.to_json() for c in across], "affordability": affordability}


def _extraction(kind: DocKind, data: dict[str, Any] | None) -> Extraction:
    data = data or {}
    fields = {
        n: Field(
            n,
            f["value"],
            f.get("evidence"),
            f.get("model_confidence", "low"),
            f["confidence"],
            f.get("problems", []),
        )
        for n, f in data.get("fields", {}).items()
    }
    return Extraction(
        kind,
        data.get("path", "text"),
        fields,
        data.get("rows", []),
        data.get("instructions_found", []),
        data.get("legible", True),
        data.get("model"),
        0.0,
    )


# --- The reviewer ---------------------------------------------------------------------------------


async def queue(pool: asyncpg.Pool, sandbox_id: uuid.UUID) -> list[dict[str, Any]]:
    """Applications waiting for a person, oldest first, with what needs looking at."""
    async with visitor_tx(pool, sandbox_id) as conn:
        rows = await conn.fetch(
            "SELECT id FROM app.loan_applications WHERE status = 'under_review' ORDER BY updated_at"
        )
    found = [await application(pool, sandbox_id, r["id"]) for r in rows]
    return [a for a in found if a is not None]


async def review_document(
    pool: asyncpg.Pool,
    sandbox_id: uuid.UUID,
    doc_id: uuid.UUID,
    *,
    decision: str,
    corrections: dict[str, str],
    note: str | None,
) -> dict[str, Any]:
    """A reviewer approves a document as read, corrects fields, or rejects it. Corrections are
    kept beside what the desk read, as labelled data for the next calibration."""
    async with visitor_tx(pool, sandbox_id) as conn:
        row = await conn.fetchrow(
            "SELECT d.kind, d.extraction, a.status FROM app.documents d "
            "JOIN app.loan_applications a ON a.id = d.application_id WHERE d.id = $1",
            doc_id,
        )
        if row is None:
            raise DeskError("There is no such document.", 404)
        if row["status"] != "under_review":
            raise DeskError("Only documents of an application under review can be reviewed.", 409)
        extraction = _load(row["extraction"]) or {}
        unknown = set(corrections) - set(FIELDS[row["kind"]])
        if unknown:
            raise DeskError(f"No such field: {', '.join(sorted(unknown))}.")
        was = {}
        for name, value in corrections.items():
            f = extraction.setdefault("fields", {}).setdefault(name, {})
            was[name] = f.get("value")
            f.update(
                {
                    "value": value.strip() or None,
                    "confidence": 1.0,
                    "problems": ["corrected by a reviewer"],
                }
            )
        status = {"approved": "approved", "corrected": "corrected", "rejected": "rejected"}[
            decision
        ]
        if decision == "corrected" and not corrections:
            raise DeskError("A correction needs at least one field.")
        await conn.execute(
            "UPDATE app.documents SET status = $2, extraction = $3 WHERE id = $1",
            doc_id,
            status,
            json.dumps(extraction),
        )
        await conn.execute(
            "INSERT INTO app.document_reviews (sandbox_id, document_id, decision, corrections, note) "
            "VALUES ($1, $2, $3, $4, $5)",
            sandbox_id,
            doc_id,
            decision,
            json.dumps({n: {"read": was[n], "right": v} for n, v in corrections.items()}),
            (note or "")[:500] or None,
        )
        await record(
            conn,
            "document_reviewed",
            {
                "document": str(doc_id),
                "decision": decision,
                "fields_corrected": sorted(corrections),
            },
        )
    return await document(pool, sandbox_id, doc_id)


async def decide(
    pool: asyncpg.Pool,
    bank: CoreBank,
    sandbox_id: uuid.UUID,
    application_id: uuid.UUID,
    *,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    """A person's decision on the application: approve (and pay out), decline, or ask for
    documents again."""
    async with visitor_tx(pool, sandbox_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app.loan_applications WHERE id = $1", application_id
        )
    if row is None:
        raise DeskError("There is no such application.", 404)
    if row["status"] != "under_review":
        raise DeskError("This application isn't waiting for a decision.", 409)
    current = await application(pool, sandbox_id, application_id)
    assert current is not None
    who = Who(sandbox_id, row["customer_id"], None)
    outcome: dict[str, Any] = {
        "decision": decision,
        "reason": reason.strip()[:500],
        "decided_at": datetime.now(UTC).isoformat(),
    }
    if decision == "approve":
        waiting = [
            d["title"]
            for d in current["documents"]
            if d["status"] not in ("verified", "approved", "corrected")
        ]
        if waiting:
            raise DeskError("Review these documents first: " + ", ".join(waiting) + ".")
        # The decision rests on what the documents say now, corrections included.
        assessment = await assess(pool, bank, who, application_id)
        if assessment["affordability"].get("requested_fits") is False:
            raise DeskError(
                "The loan doesn't fit the lending rules on the verified pay. Decline it, or ask for other documents."
            )
        accounts = await bank.call(
            "GET", "/v1/accounts", sandbox_id=sandbox_id, customer_id=row["customer_id"]
        )
        into = next((a for a in accounts if "Saver" not in a["product"]), accounts[0])
        paid = await bank.call(
            "POST",
            "/v1/loans/personal/disburse",
            sandbox_id=sandbox_id,
            customer_id=row["customer_id"],
            json={
                "amount": row["amount"],
                "term_months": row["term_months"],
                "account_id": into["id"],
            },
            idempotency_key=f"application-{application_id}",
        )
        outcome["paid_out"] = paid
        status = "approved"
        message = (
            f"Kaskazi: your Personal Loan of KES {row['amount']:,} is approved. "
            f"KES {paid['amount_received']:,} has been paid into your {paid['account']}. "
            f"Your monthly instalment is KES {paid['monthly_instalment']:,}."
        )
    elif decision == "decline":
        if not outcome["reason"]:
            raise DeskError("Give the reason the customer will read.")
        status, message = (
            "declined",
            f"Kaskazi: your loan application was not approved. {outcome['reason']}",
        )
    elif decision == "more_documents":
        if not outcome["reason"]:
            raise DeskError("Say what the customer should send.")
        status = "documents_requested"
        message = f"Kaskazi: we need more from you for your loan application. {outcome['reason']}"
    else:
        raise DeskError("Unknown decision.")
    async with visitor_tx(pool, sandbox_id) as conn:
        await conn.execute(
            "UPDATE app.loan_applications SET status = $2, decision = $3, updated_at = now() WHERE id = $1",
            application_id,
            status,
            json.dumps(outcome),
        )
        conversation = await conn.fetchval(
            "SELECT id FROM app.conversations ORDER BY created_at DESC LIMIT 1"
        )
        if conversation is not None:
            seq = await conn.fetchval(
                "SELECT coalesce(max(seq), 0) + 1 FROM app.messages WHERE conversation_id = $1",
                conversation,
            )
            await conn.execute(
                "INSERT INTO app.messages (conversation_id, sandbox_id, seq, author, display) "
                "VALUES ($1, $2, $3, 'sms', $4)",
                conversation,
                sandbox_id,
                seq,
                {"from": "Kaskazi", "text": message},
            )
        await record(
            conn,
            "application_decided",
            {
                "application": str(application_id),
                "decision": decision,
                "status": status,
                "paid_out": bool(outcome.get("paid_out")),
            },
        )
    result = await application(pool, sandbox_id, application_id)
    assert result is not None
    return result


async def file(
    pool: asyncpg.Pool, sandbox_id: uuid.UUID, doc_id: uuid.UUID
) -> tuple[str, bytes, str]:
    """A document's own file, for the customer and the reviewer to look at."""
    async with visitor_tx(pool, sandbox_id) as conn:
        row = await conn.fetchrow(
            "SELECT media_type, content, filename FROM app.documents WHERE id = $1", doc_id
        )
    if row is None:
        raise DeskError("There is no such document.", 404)
    return row["media_type"], bytes(row["content"]), row["filename"]
