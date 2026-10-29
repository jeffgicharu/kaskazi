#!/usr/bin/env bash
# Runs the gateway (8802) and the API (8800) locally against the development database, the way
# a slot runs on the server. Stand-in providers by default; KASKAZI_DEV_REAL=1 uses the real
# ones, with the keys from your environment. Then run `pnpm dev` in web/.
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${KASKAZI_DEV_DB:-postgresql://%s:test-only-password@127.0.0.1:55432/kaskazi_dev}"
token="dev-$(openssl rand -hex 8)"

export KASKAZI_GATEWAY_TOKEN="$token" KASKAZI_GATEWAY_PORT=8802
export KASKAZI_GATEWAY_DATABASE_URL="$(printf "$DB" kaskazi_gateway)"
if [[ "${KASKAZI_DEV_REAL:-0}" != 1 ]]; then
    export KASKAZI_GATEWAY_FAKE_PROVIDERS=1
fi
# Every local request comes from one address, so its daily cap would stop an evaluation run
# within a few dozen questions. Real spending locally is still capped, at a few dollars a day.
export KASKAZI_CAP_ADDRESS_DAY_USD="${KASKAZI_CAP_ADDRESS_DAY_USD:-3}"
export KASKAZI_CAP_DAY_USD="${KASKAZI_CAP_DAY_USD:-3}"
.venv/bin/python -m kaskazi.gateway serve &
gateway=$!

export KASKAZI_SITE_URL=http://localhost:5173
export KASKAZI_DATABASE_URL="$(printf "$DB" kaskazi_app)"
export KASKAZI_SECRET="$(openssl rand -hex 32)" KASKAZI_VAULT_KEY="$(openssl rand -base64 32)"
export KASKAZI_GATEWAY_URL=http://127.0.0.1:8802 KASKAZI_API_PORT=8800
export KASKAZI_COPIES_PER_HOUR=1000 KASKAZI_COPIES_PER_DAY=1000
.venv/bin/python -m kaskazi.api serve &
api=$!

trap 'kill $gateway $api 2>/dev/null || true' EXIT INT TERM
wait
