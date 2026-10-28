"""The release check's own copy of the sample data.

The activation script runs the release checks against the idle slot before visitors reach it.
They use the API like a visitor would, in a copy marked `check`: exempt from the visitor and
address caps (site caps still apply), deleted at the next purge like any copy. Only a caller
holding the API's secret can make one: the token is an HMAC of the release's sha.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from kaskazi.api.app import Services, services
from kaskazi.api.sessions import COPY_LIFETIME_S, VISITOR_COOKIE, visitor_cookie
from kaskazi.audit.log import record
from kaskazi.corebank.seed import seed_copy
from kaskazi.db.pool import visitor_tx
from kaskazi.release import release_id

router = APIRouter(prefix="/api/check")
Svc = Annotated[Services, Depends(services)]


def check_token(secret: bytes, release: str) -> str:
    return hmac.new(secret, f"release-check:{release}".encode(), hashlib.sha256).hexdigest()


@router.post("/session")
async def check_session(
    response: Response, svc: Svc, x_kaskazi_check: Annotated[str, Header()] = ""
) -> dict[str, Any]:
    expected = check_token(svc.settings.secret, release_id())
    if not hmac.compare_digest(x_kaskazi_check.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Not a release check.")
    sandbox = uuid.uuid4()
    conversation = uuid.uuid4()
    async with visitor_tx(svc.pool, sandbox) as conn:
        await seed_copy(conn, sandbox, "teacher")
        await conn.execute("UPDATE app.sandboxes SET kind = 'check' WHERE id = $1", sandbox)
        await conn.execute(
            "INSERT INTO app.conversations (id, sandbox_id) VALUES ($1, $2)", conversation, sandbox
        )
        await record(
            conn, "copy_created", {"persona": "teacher", "kind": "check", "release": release_id()}
        )
    response.set_cookie(
        VISITOR_COOKIE,
        visitor_cookie(svc.settings.secret, sandbox),
        max_age=COPY_LIFETIME_S,
        httponly=True,
        secure=svc.settings.secure_cookies,
        samesite="strict",
        path="/api",
    )
    return {"conversation_id": str(conversation)}
