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
    copies_per_hour: int = 3
    copies_per_day: int = 10

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
            # New sample copies per visitor address. Development and CI raise these, since every
            # local test comes from 127.0.0.1.
            copies_per_hour=int(env.get("KASKAZI_COPIES_PER_HOUR", "3")),
            copies_per_day=int(env.get("KASKAZI_COPIES_PER_DAY", "10")),
        )
