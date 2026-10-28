"""Runs the model gateway: python -I -m kaskazi.gateway serve"""

from __future__ import annotations

import os
import sys

import uvicorn


def main() -> int:
    if sys.argv[1:] != ["serve"]:
        print("usage: python -m kaskazi.gateway serve", file=sys.stderr)
        return 64
    from kaskazi.gateway.service import GatewaySettings, create_app

    uvicorn.run(
        create_app(GatewaySettings.from_env()),
        host="127.0.0.1",
        port=int(os.environ["KASKAZI_GATEWAY_PORT"]),
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
