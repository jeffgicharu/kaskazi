"""The customer's side of the document desk: a loan application and its documents.

A visitor gives the sample documents made for this demonstration, or uploads a file of their own
(PDF, PNG or JPEG, 3 MB and 3 pages at most). The page asks them not to upload real documents,
and an image is only read after they agree that an AI service will see it.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from kaskazi.api.customer import Me, Svc, Write
from kaskazi.documents import desk

router = APIRouter(prefix="/api/applications")
DocKindName = Literal["payslip", "id", "statement"]


def _who(me: Me) -> desk.Who:
    return desk.Who(me.sandbox_id, me.customer_id, me.ip_hash)


def _fail(error: desk.DeskError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=str(error))


@router.get("/samples")
async def samples(me: Me) -> list[dict[str, Any]]:
    """The sample documents made for this customer."""
    return [s for s in desk.samples() if s["persona"] == me.persona]


@router.get("/quote")
async def quote(
    me: Me,
    svc: Svc,
    amount: Annotated[int, Query(ge=50_000, le=3_000_000)],
    term_months: Annotated[int, Query(ge=6, le=60)],
) -> dict[str, Any]:
    """What a loan would cost, by the bank's rules; no AI involved."""
    result: dict[str, Any] = await svc.bank.call(
        "POST",
        "/v1/loans/personal/quote",
        sandbox_id=me.sandbox_id,
        customer_id=me.customer_id,
        json={"amount": amount, "term_months": term_months},
    )
    return result


@router.get("/current")
async def current(me: Me, svc: Svc) -> dict[str, Any] | None:
    return await desk.application(svc.pool, me.sandbox_id)


class NewApplication(BaseModel):
    amount: int = Field(ge=50_000, le=3_000_000)
    term_months: int = Field(ge=6, le=60)


@router.post("", dependencies=[Write])
async def start(body: NewApplication, me: Me, svc: Svc) -> dict[str, Any]:
    try:
        app_id = await desk.create_application(svc.pool, _who(me), body.amount, body.term_months)
    except desk.DeskError as error:
        raise _fail(error) from error
    found = await desk.application(svc.pool, me.sandbox_id, app_id)
    assert found is not None
    return found


class SampleDocument(BaseModel):
    key: str = Field(max_length=80)


@router.post("/{application_id}/documents/sample", dependencies=[Write])
async def add_sample(
    application_id: uuid.UUID, body: SampleDocument, me: Me, svc: Svc
) -> dict[str, Any]:
    try:
        sample, data = desk.sample_bytes(body.key)
        if sample["persona"] != me.persona:
            raise desk.DeskError("That sample belongs to another customer.", 403)
        return await desk.add_document(
            svc.pool,
            svc.gateway,
            svc.bank,
            _who(me),
            application_id,
            kind=sample["kind"],
            data=data,
            filename=sample["file"],
            source="sample",
            sample_key=sample["key"],
            image_consent=True,
        )
    except desk.DeskError as error:
        raise _fail(error) from error


# nginx gives exactly this path a 4 MB body limit and its own rate limit (deploy/nginx.conf).
uploads = APIRouter()


@uploads.post("/api/documents", dependencies=[Write])
async def upload(
    me: Me,
    svc: Svc,
    application_id: Annotated[uuid.UUID, Form()],
    kind: Annotated[DocKindName, Form()],
    file: Annotated[UploadFile, File()],
    image_consent: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    data = await file.read(desk.MAX_BYTES + 1)
    try:
        return await desk.add_document(
            svc.pool,
            svc.gateway,
            svc.bank,
            _who(me),
            application_id,
            kind=kind,
            data=data,
            filename=file.filename or "document",
            source="upload",
            image_consent=image_consent,
        )
    except desk.DeskError as error:
        raise _fail(error) from error


@router.post("/{application_id}/submit", dependencies=[Write])
async def submit(application_id: uuid.UUID, me: Me, svc: Svc) -> dict[str, Any]:
    try:
        return await desk.submit(svc.pool, svc.bank, _who(me), application_id)
    except desk.DeskError as error:
        raise _fail(error) from error


@router.get("/documents/{doc_id}/file")
async def document_file(doc_id: uuid.UUID, me: Me, svc: Svc) -> Response:
    try:
        media_type, data, _ = await desk.file(svc.pool, me.sandbox_id, doc_id)
    except desk.DeskError as error:
        raise _fail(error) from error
    # Shown in the page, never run: a document is data, whatever it holds.
    return Response(
        data,
        media_type=media_type,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
        },
    )
