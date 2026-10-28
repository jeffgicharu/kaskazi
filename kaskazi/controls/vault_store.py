"""Keeping a conversation's vault: placeholders and the values behind them, encrypted at rest.

Each value is sealed with AES-256-GCM under the API's vault key, with the conversation and the
placeholder as associated data, so a sealed value can't be moved to another conversation or
placeholder. Vault rows are deleted with the visitor's copy.
"""

from __future__ import annotations

import base64
import os
import uuid

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kaskazi.controls.pii import Kind, Vault


class VaultKey:
    def __init__(self, key_b64: str) -> None:
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("the vault key must be 32 bytes, base64-encoded")
        self._aead = AESGCM(key)

    def seal(self, conversation_id: uuid.UUID, placeholder: str, value: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return nonce, self._aead.encrypt(nonce, value.encode(), _aad(conversation_id, placeholder))

    def open(
        self, conversation_id: uuid.UUID, placeholder: str, nonce: bytes, ciphertext: bytes
    ) -> str:
        return self._aead.decrypt(nonce, ciphertext, _aad(conversation_id, placeholder)).decode()


def _aad(conversation_id: uuid.UUID, placeholder: str) -> bytes:
    return f"{conversation_id}|{placeholder}".encode()


async def load_vault(conn: asyncpg.Connection, key: VaultKey, conversation_id: uuid.UUID) -> Vault:
    rows = await conn.fetch(
        "SELECT placeholder, kind, nonce, ciphertext FROM app.vault_entries WHERE conversation_id = $1",
        conversation_id,
    )
    return Vault.from_entries(
        {
            r["placeholder"]: (
                Kind(r["kind"]),
                key.open(conversation_id, r["placeholder"], r["nonce"], r["ciphertext"]),
            )
            for r in rows
        }
    )


async def save_new_entries(
    conn: asyncpg.Connection,
    key: VaultKey,
    sandbox_id: uuid.UUID,
    conversation_id: uuid.UUID,
    vault: Vault,
    placeholders: list[str],
) -> None:
    for placeholder in placeholders:
        kind, value = vault.entries[placeholder]
        nonce, sealed = key.seal(conversation_id, placeholder, value)
        await conn.execute(
            """INSERT INTO app.vault_entries (conversation_id, sandbox_id, placeholder, kind, nonce, ciphertext)
               VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING""",
            conversation_id,
            sandbox_id,
            placeholder,
            kind.value,
            nonce,
            sealed,
        )
