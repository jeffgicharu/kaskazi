# Working on Kaskazi

## What you need

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Node 22 and pnpm 10
- Docker, for PostgreSQL 16 with pgvector

## Set up

```sh
uv sync
(cd web && pnpm install)

# PostgreSQL 16 with pgvector, on localhost only
docker run -d --name kaskazi-pg -e POSTGRES_PASSWORD=local-dev-only \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16

# The three roles the server has, and a development database
docker exec -i kaskazi-pg psql -U postgres -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE kaskazi_owner LOGIN NOINHERIT CONNECTION LIMIT 3 PASSWORD 'test-only-password';
CREATE ROLE kaskazi_app LOGIN NOINHERIT CONNECTION LIMIT 12 PASSWORD 'test-only-password';
CREATE ROLE kaskazi_gateway LOGIN NOINHERIT CONNECTION LIMIT 6 PASSWORD 'test-only-password';
CREATE DATABASE kaskazi_dev OWNER kaskazi_owner;
SQL
docker exec -i kaskazi-pg psql -U postgres -d kaskazi_dev -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION vector" -c "REVOKE ALL ON DATABASE kaskazi_dev FROM PUBLIC" \
  -c "GRANT CONNECT ON DATABASE kaskazi_dev TO kaskazi_owner, kaskazi_app, kaskazi_gateway"

# The schema, then the bank's documents and the prompts
export KASKAZI_OWNER_DATABASE_URL=postgresql://kaskazi_owner:test-only-password@127.0.0.1:55432/kaskazi_dev
uv run python -m kaskazi.release migrate
uv run python -m kaskazi.release load --release local
```

The passwords are for a container on your own machine only. The server's are generated there
and never leave it.

## Run it

```sh
scripts/dev.sh            # the gateway on 8802 and the API on 8800, with stand-in models
(cd web && pnpm dev)      # the app on http://localhost:5173
```

The stand-in models answer from the documents with fixed phrasing and cost nothing. To use the
real ones, with the provider keys in your environment (`ANTHROPIC_KEY`,
`ANTHROPIC_WORKSPACE_ID`, `OPENAI_API_KEY`):

```sh
KASKAZI_DEV_REAL=1 scripts/dev.sh
```

Spending is still capped locally, at a few dollars a day.

## Checks

The same checks CI runs:

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -q          # makes and drops its own database in the container

cd web
pnpm lint && pnpm typecheck && pnpm test
pnpm build && pnpm exec vite preview --port 4173 &   # needs scripts/dev.sh running
pnpm e2e                  # the journeys in Chromium, Firefox, WebKit and a phone, with axe
```

## Changing the AI

Anything that changes what the assistant or the document desk does (a prompt, a model or
setting, a tool, the core-banking rules, the controls, the bank's documents, the golden set)
changes the release's AI fingerprint, and CI won't release it until committed evaluation reports
cover the new fingerprint and pass their gates. With the real models running locally:

```sh
uv run python -m evals.documents
uv run python -m evals.answers
```

Then commit the reports from `evals/reports`. `evals/README.md` explains what each evaluation
measures, and `docs/runbook.md` the order to do things in.

- **Prompts** are versioned files in `prompts/<name>/<version>.md`. Never edit a version that is
  in `prompts/prompts.lock`: add a new one, with the owner, the date and what changed and why in
  its header, then run `uv run python -m kaskazi.prompts.lock`.
- **Migrations** only ever add (`kaskazi/db/migrations`): the release before keeps running on the
  same database while the next one starts. Rename or drop in a later release, once nothing uses
  the old name.
- **The bank's documents** (`corpus/documents`) are this project's own writing. Don't copy or
  paraphrase any real bank's terms, tariffs or FAQs, and don't use any real bank's name, colours
  or products.

## Commits

Small commits that each say what changed and why, in plain English.
