"""Visitor and staff sessions, and the visitor's address.

A visitor's session names their copy of the sample data. It is a signed value in an HttpOnly,
SameSite=Strict cookie, so another site can't use it and page scripts can't read it. Every write
also needs the `X-Kaskazi` header, which a cross-site form can't send.

Addresses are never stored. The API keeps a keyed hash of each, which is enough for the limits
on new copies and for the spending cap per address.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

VISITOR_COOKIE = "kz_copy"
STAFF_COOKIE = "kz_staff"
COPY_LIFETIME_S = 24 * 3600
STAFF_LIFETIME_S = 8 * 3600
Role = Literal["agent", "reviewer", "ai_lead"]
ROLES: tuple[Role, ...] = ("agent", "reviewer", "ai_lead")


def _sign(secret: bytes, value: str) -> str:
    mac = hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{value}.{mac}"


def _verify(secret: bytes, signed: str | None) -> str | None:
    if not signed or "." not in signed:
        return None
    value, mac = signed.rsplit(".", 1)
    expected = hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()[:32]
    return value if hmac.compare_digest(mac, expected) else None


def visitor_cookie(secret: bytes, sandbox_id: uuid.UUID) -> str:
    return _sign(secret, f"v1:{sandbox_id}")


def read_visitor(secret: bytes, request: Request) -> uuid.UUID | None:
    value = _verify(secret, request.cookies.get(VISITOR_COOKIE))
    if value is None or not value.startswith("v1:"):
        return None
    try:
        return uuid.UUID(value[3:])
    except ValueError:
        return None


@dataclass(frozen=True)
class Staff:
    sandbox_id: uuid.UUID
    role: Role


def staff_cookie(secret: bytes, sandbox_id: uuid.UUID, role: Role) -> str:
    return _sign(secret, f"s1:{sandbox_id}:{role}:{int(time.time()) + STAFF_LIFETIME_S}")


def read_staff(secret: bytes, request: Request) -> Staff | None:
    value = _verify(secret, request.cookies.get(STAFF_COOKIE))
    if value is None or not value.startswith("s1:"):
        return None
    try:
        _, sandbox, role, expires = value.split(":")
        if int(expires) < time.time() or role not in ROLES:
            return None
        return Staff(uuid.UUID(sandbox), role)
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    # nginx sets X-Real-IP from Cloudflare's header; it is believed only from this machine.
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        return request.headers.get("x-real-ip", peer)
    return peer


def ip_hash(secret: bytes, ip: str) -> str:
    return hmac.new(secret, f"ip:{ip}".encode(), hashlib.sha256).hexdigest()[:24]


def require_write_header(request: Request) -> None:
    if request.headers.get("x-kaskazi") != "1":
        raise HTTPException(status_code=403, detail="Missing the X-Kaskazi header.")
