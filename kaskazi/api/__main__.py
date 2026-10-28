"""Runs the API: python -I -m kaskazi.api serve"""

from __future__ import annotations

import sys

import uvicorn


def main() -> int:
    if sys.argv[1:] != ["serve"]:
        print("usage: python -m kaskazi.api serve", file=sys.stderr)
        return 64
    from kaskazi.api.app import create_app
    from kaskazi.api.settings import Settings

    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=settings.port,
        loop="uvloop",
        http="httptools",
        proxy_headers=False,
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=15,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
