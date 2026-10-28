"""The API's settings, from its env file and its slot's ports file."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    site_url: str
    database_url: str
    secret: bytes
    vault_key: str
    gateway_url: str
    gateway_token: str
    port: int
    secure_cookies: bool

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        site = env["KASKAZI_SITE_URL"]
        return cls(
            site_url=site,
            database_url=env["KASKAZI_DATABASE_URL"],
            secret=bytes.fromhex(env["KASKAZI_SECRET"]),
            vault_key=env["KASKAZI_VAULT_KEY"],
            gateway_url=env["KASKAZI_GATEWAY_URL"],
            gateway_token=env["KASKAZI_GATEWAY_TOKEN"],
            port=int(env.get("KASKAZI_API_PORT", "8800")),
            secure_cookies=site.startswith("https://"),
        )
