-- The foundation: visitor copies, the pretend core-banking data, the knowledge base, the audit
-- trail and the gateway's ledger.
--
-- Roles (created by the provision script):
--   kaskazi_owner    owns every object; runs migrations at activation; not subject to row
--                    security on tables it owns, which is how the aggregate views see every copy.
--   kaskazi_app      the API. No schema changes, no BYPASSRLS, and INSERT/SELECT only on the
--                    audit trail.
--   kaskazi_gateway  the model gateway. Only its own schema.
--
-- Every table holding a visitor's data carries sandbox_id and a row-security policy that reads
-- the visitor from `app.visitor`. The API sets it with set_config('app.visitor', id, true) inside
-- every transaction, never for the session, so a pooled connection can't carry one visitor into
-- the next request.

CREATE SCHEMA app;
CREATE SCHEMA corebank;
CREATE SCHEMA kb;
CREATE SCHEMA audit;
CREATE SCHEMA gateway;

REVOKE ALL ON SCHEMA app, corebank, kb, audit, gateway FROM PUBLIC;
GRANT USAGE ON SCHEMA app, corebank, kb, audit TO kaskazi_app;
GRANT USAGE ON SCHEMA gateway TO kaskazi_gateway, kaskazi_app;

-- The visitor this transaction acts for. NULL when unset, which no policy matches.
CREATE FUNCTION app.visitor() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT nullif(current_setting('app.visitor', true), '')::uuid
$$;

-- ---------------------------------------------------------------------------------------------
-- Visitor copies
-- ---------------------------------------------------------------------------------------------

CREATE TABLE app.sandboxes (
    id            uuid PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN ('visitor', 'check')),
    persona       text NOT NULL,
    customer_id   uuid NOT NULL,
    ip_hash       text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz NOT NULL DEFAULT now(),
    simulate_primary_outage boolean NOT NULL DEFAULT false
);
CREATE INDEX sandboxes_last_used ON app.sandboxes (last_used_at);
CREATE INDEX sandboxes_ip_created ON app.sandboxes (ip_hash, created_at);

CREATE TABLE app.conversations (
    id          uuid PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'with_agent', 'closed')),
    sample      boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_sandbox ON app.conversations (sandbox_id, updated_at DESC);

-- The conversation as the models saw it (masked, append-only) and as the customer saw it.
CREATE TABLE app.messages (
    id              bigserial PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES app.conversations ON DELETE CASCADE,
    sandbox_id      uuid NOT NULL,
    seq             integer NOT NULL,
    author          text NOT NULL CHECK (author IN ('customer', 'assistant', 'agent', 'bank', 'tool')),
    model_message   jsonb,          -- the neutral Message sent to models, after masking
    display         jsonb NOT NULL, -- what the customer or agent sees
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, seq)
);
CREATE INDEX messages_sandbox ON app.messages (sandbox_id);

-- Placeholders and the values behind them, encrypted with AES-256-GCM. Deleted with the copy.
CREATE TABLE app.vault_entries (
    conversation_id uuid NOT NULL REFERENCES app.conversations ON DELETE CASCADE,
    sandbox_id      uuid NOT NULL,
    placeholder     text NOT NULL,
    kind            text NOT NULL,
    nonce           bytea NOT NULL,
    ciphertext      bytea NOT NULL,
    PRIMARY KEY (conversation_id, placeholder)
);

CREATE TABLE app.pending_actions (
    id              uuid PRIMARY KEY,
    sandbox_id      uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES app.conversations ON DELETE CASCADE,
    tool            text NOT NULL,
    arguments       jsonb NOT NULL,   -- as the model gave them, with placeholders
    preview         jsonb NOT NULL,   -- what the confirmation card shows, built by the server
    needs_code      boolean NOT NULL DEFAULT false,
    code_hash       text,
    token_hash      text NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'confirmed', 'cancelled', 'expired', 'done', 'failed')),
    result          jsonb,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_at      timestamptz
);
CREATE INDEX pending_actions_conversation ON app.pending_actions (conversation_id, created_at);

CREATE TABLE app.handovers (
    id              uuid PRIMARY KEY,
    sandbox_id      uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES app.conversations ON DELETE CASCADE,
    reason          text NOT NULL,
    trigger         text NOT NULL,
    priority        text NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal', 'urgent')),
    status          text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'active', 'resolved')),
    summary         jsonb,
    outcome         text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    picked_at       timestamptz,
    resolved_at     timestamptz
);
CREATE INDEX handovers_queue ON app.handovers (sandbox_id, status, created_at);

CREATE TABLE app.feedback (
    id          bigserial PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    message_id  bigint NOT NULL REFERENCES app.messages ON DELETE CASCADE,
    rating      smallint NOT NULL CHECK (rating IN (-1, 1)),
    reason      text,
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id)
);

-- One row per assistant turn: what happened, how long it took, what it cost. No content.
CREATE TABLE app.turns (
    id              bigserial PRIMARY KEY,
    sandbox_id      uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    conversation_id uuid NOT NULL,
    at              timestamptz NOT NULL DEFAULT now(),
    outcome         text NOT NULL,
    intent          text,
    confidence      real,
    latency_ms      integer NOT NULL,
    model_calls     smallint NOT NULL DEFAULT 0,
    fallback        boolean NOT NULL DEFAULT false,
    cost_usd        numeric(12, 6) NOT NULL DEFAULT 0,
    cache_read_tokens  integer NOT NULL DEFAULT 0,
    input_tokens       integer NOT NULL DEFAULT 0
);
CREATE INDEX turns_at ON app.turns (at);

CREATE TABLE app.jobs (
    id          bigserial PRIMARY KEY,
    sandbox_id  uuid REFERENCES app.sandboxes ON DELETE CASCADE,
    kind        text NOT NULL,
    payload     jsonb NOT NULL,
    status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts    smallint NOT NULL DEFAULT 0,
    run_after   timestamptz NOT NULL DEFAULT now(),
    locked_at   timestamptz,
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_ready ON app.jobs (run_after) WHERE status = 'queued';

-- Requests the API served, for availability and latency. No content and no visitor.
CREATE TABLE app.requests (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    route       text NOT NULL,
    status      smallint NOT NULL,
    latency_ms  integer NOT NULL
);
CREATE INDEX requests_at ON app.requests (at);

CREATE TABLE app.prompt_versions (
    prompt_id     text NOT NULL,
    version       text NOT NULL,
    owner         text NOT NULL,
    route         text NOT NULL,
    content_hash  text NOT NULL,
    change_note   text NOT NULL,
    changed_on    date NOT NULL,
    body          text NOT NULL,
    first_release text NOT NULL,
    PRIMARY KEY (prompt_id, version)
);

CREATE TABLE app.releases (
    sha          text PRIMARY KEY,
    activated_at timestamptz NOT NULL DEFAULT now(),
    eval_report  jsonb
);

-- ---------------------------------------------------------------------------------------------
-- The pretend core-banking system
-- ---------------------------------------------------------------------------------------------

CREATE TABLE corebank.customers (
    id            uuid PRIMARY KEY,
    sandbox_id    uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    full_name     text NOT NULL,
    first_name    text NOT NULL,
    phone         text NOT NULL,
    email         text NOT NULL,
    national_id   text NOT NULL,
    kra_pin       text NOT NULL,
    date_of_birth date NOT NULL,
    segment       text NOT NULL,
    employer      text,
    basic_pay     numeric(14, 2),
    net_pay       numeric(14, 2),
    salary_day    smallint,
    branch_code   text NOT NULL
);

CREATE TABLE corebank.accounts (
    id          uuid PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    customer_id uuid NOT NULL REFERENCES corebank.customers ON DELETE CASCADE,
    number      text NOT NULL,
    product     text NOT NULL,
    currency    text NOT NULL DEFAULT 'KES',
    balance     numeric(14, 2) NOT NULL,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'dormant', 'closed')),
    opened_on   date NOT NULL,
    UNIQUE (sandbox_id, number)
);

CREATE TABLE corebank.cards (
    id          uuid PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    account_id  uuid NOT NULL REFERENCES corebank.accounts ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('debit', 'credit')),
    last4       text NOT NULL,
    status      text NOT NULL CHECK (status IN ('active', 'blocked', 'cancelled')),
    expires     text NOT NULL,
    credit_limit numeric(14, 2),
    replacement_ordered boolean NOT NULL DEFAULT false
);

CREATE TABLE corebank.transactions (
    id          bigserial PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    account_id  uuid NOT NULL REFERENCES corebank.accounts ON DELETE CASCADE,
    at          timestamptz NOT NULL,
    description text NOT NULL,
    amount      numeric(14, 2) NOT NULL,
    balance_after numeric(14, 2) NOT NULL,
    reference   text NOT NULL,
    channel     text NOT NULL
);
CREATE INDEX transactions_account_at ON corebank.transactions (account_id, at DESC);

CREATE TABLE corebank.recipients (
    id          uuid PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    customer_id uuid NOT NULL REFERENCES corebank.customers ON DELETE CASCADE,
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('mobile_money', 'kaskazi', 'other_bank')),
    phone       text,
    account_number text,
    bank_name   text,
    added_at    timestamptz NOT NULL
);

CREATE TABLE corebank.loans (
    id          uuid PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    customer_id uuid NOT NULL REFERENCES corebank.customers ON DELETE CASCADE,
    product     text NOT NULL,
    principal   numeric(14, 2) NOT NULL,
    outstanding numeric(14, 2) NOT NULL,
    rate        numeric(6, 3) NOT NULL,
    term_months smallint NOT NULL,
    instalment  numeric(14, 2) NOT NULL,
    next_due    date,
    status      text NOT NULL CHECK (status IN ('active', 'repaid'))
);

CREATE TABLE corebank.complaints (
    id          uuid PRIMARY KEY,
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    customer_id uuid NOT NULL REFERENCES corebank.customers ON DELETE CASCADE,
    reference   text NOT NULL,
    category    text NOT NULL,
    summary     text NOT NULL,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    respond_by  timestamptz NOT NULL,
    UNIQUE (sandbox_id, reference)
);

-- Writes carry an idempotency key, so a retried confirmation never runs twice.
CREATE TABLE corebank.idempotency (
    sandbox_id  uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    key         text NOT NULL,
    result      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sandbox_id, key)
);

-- ---------------------------------------------------------------------------------------------
-- The knowledge base: shared, read-only at run time, one version per release
-- ---------------------------------------------------------------------------------------------

CREATE TABLE kb.corpus_versions (
    version    text PRIMARY KEY,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE kb.documents (
    corpus_version text NOT NULL REFERENCES kb.corpus_versions ON DELETE CASCADE,
    id           text NOT NULL,
    title        text NOT NULL,
    audience     text NOT NULL CHECK (audience IN ('customer', 'staff')),
    topics       text[] NOT NULL,
    version      text NOT NULL,
    effective    date NOT NULL,
    owner        text NOT NULL,
    content_hash text NOT NULL,
    body         text NOT NULL,
    PRIMARY KEY (corpus_version, id)
);

CREATE TABLE kb.chunks (
    corpus_version text NOT NULL,
    id           text NOT NULL,           -- "<document id>#<n>"
    document_id  text NOT NULL,
    ord          integer NOT NULL,
    heading_path text NOT NULL,
    text         text NOT NULL,
    audience     text NOT NULL,
    tokens       integer NOT NULL,
    content_hash text NOT NULL,
    embedding    vector(512),
    tsv          tsvector GENERATED ALWAYS AS (
                     setweight(to_tsvector('english', heading_path), 'A') ||
                     setweight(to_tsvector('english', text), 'B')
                 ) STORED,
    PRIMARY KEY (corpus_version, id),
    FOREIGN KEY (corpus_version, document_id) REFERENCES kb.documents ON DELETE CASCADE
);
CREATE INDEX chunks_embedding ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv ON kb.chunks USING gin (tsv);

-- ---------------------------------------------------------------------------------------------
-- The audit trail: append-only, hash-chained per visitor copy
-- ---------------------------------------------------------------------------------------------

CREATE TABLE audit.chain_heads (
    sandbox_id  uuid PRIMARY KEY REFERENCES app.sandboxes ON DELETE CASCADE,
    last_seq    bigint NOT NULL DEFAULT 0,
    last_hash   text NOT NULL
);

CREATE TABLE audit.events (
    sandbox_id      uuid NOT NULL,
    seq             bigint NOT NULL,
    at              timestamptz NOT NULL DEFAULT clock_timestamp(),
    conversation_id uuid,
    turn_id         text,
    kind            text NOT NULL,
    payload         jsonb NOT NULL,     -- masked; never holds a raw identifier
    prev_hash       text NOT NULL,
    hash            text NOT NULL,
    PRIMARY KEY (sandbox_id, seq)
);
CREATE INDEX audit_events_conversation ON audit.events (conversation_id, seq);
CREATE INDEX audit_events_at ON audit.events (at);

-- The genesis hash of a chain is derived from the copy's id, so a chain can't be swapped
-- between copies.
CREATE FUNCTION audit.genesis(p_sandbox uuid) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to('kaskazi-audit:' || p_sandbox::text, 'UTF8')), 'hex')
$$;

CREATE FUNCTION audit.event_hash(p_prev text, p_seq bigint, p_at timestamptz, p_conversation uuid,
                                 p_turn text, p_kind text, p_payload jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(
        p_prev || '|' || p_seq::text || '|' || to_char(p_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' ||
        coalesce(p_conversation::text, '') || '|' || coalesce(p_turn, '') || '|' || p_kind || '|' ||
        p_payload::text, 'UTF8')), 'hex')
$$;

-- Appends one event to the visitor's chain. The chain head is locked, so concurrent events in
-- one copy are chained in a single order.
CREATE FUNCTION audit.append(p_conversation uuid, p_turn text, p_kind text, p_payload jsonb)
RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    v_sandbox uuid := app.visitor();
    v_head    audit.chain_heads;
    v_seq     bigint;
    v_at      timestamptz := clock_timestamp();
    v_hash    text;
BEGIN
    IF v_sandbox IS NULL THEN
        RAISE EXCEPTION 'audit.append needs app.visitor';
    END IF;
    INSERT INTO audit.chain_heads (sandbox_id, last_seq, last_hash)
    VALUES (v_sandbox, 0, audit.genesis(v_sandbox))
    ON CONFLICT (sandbox_id) DO NOTHING;
    SELECT * INTO v_head FROM audit.chain_heads WHERE sandbox_id = v_sandbox FOR UPDATE;
    v_seq := v_head.last_seq + 1;
    v_hash := audit.event_hash(v_head.last_hash, v_seq, v_at, p_conversation, p_turn, p_kind, p_payload);
    INSERT INTO audit.events (sandbox_id, seq, at, conversation_id, turn_id, kind, payload, prev_hash, hash)
    VALUES (v_sandbox, v_seq, v_at, p_conversation, p_turn, p_kind, p_payload, v_head.last_hash, v_hash);
    UPDATE audit.chain_heads SET last_seq = v_seq, last_hash = v_hash WHERE sandbox_id = v_sandbox;
    RETURN v_seq;
END
$$;

-- Recomputes the visitor's chain. Returns the first broken sequence number, or NULL if intact.
CREATE FUNCTION audit.verify() RETURNS TABLE (events bigint, first_broken bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_sandbox uuid := app.visitor();
    v_prev    text := audit.genesis(v_sandbox);
    v_count   bigint := 0;
    r         audit.events;
BEGIN
    FOR r IN SELECT * FROM audit.events WHERE sandbox_id = v_sandbox ORDER BY seq LOOP
        v_count := v_count + 1;
        IF r.seq <> v_count OR r.prev_hash <> v_prev
           OR r.hash <> audit.event_hash(r.prev_hash, r.seq, r.at, r.conversation_id, r.turn_id, r.kind, r.payload) THEN
            events := v_count; first_broken := r.seq; RETURN NEXT; RETURN;
        END IF;
        v_prev := r.hash;
    END LOOP;
    events := v_count; first_broken := NULL; RETURN NEXT;
END
$$;

-- Nothing changes or removes an event, except the retention purge below.
CREATE FUNCTION audit.refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('kaskazi.retention_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'the audit trail is append-only (% refused)', TG_OP;
END
$$;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.refuse_change();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON audit.events
    FOR EACH STATEMENT EXECUTE FUNCTION audit.refuse_change();

CREATE TABLE audit.retention_log (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    sandboxes  integer NOT NULL,
    events     bigint NOT NULL
);

-- ---------------------------------------------------------------------------------------------
-- The gateway's ledger and call log (no content)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE gateway.spend (
    id           bigserial PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    day          date NOT NULL,      -- in Nairobi time
    month        date NOT NULL,      -- the first day of the month, in Nairobi time
    sandbox_id   uuid,
    ip_hash      text,
    exempt       boolean NOT NULL DEFAULT false,
    purpose      text NOT NULL,
    route        text NOT NULL,
    request_id   text NOT NULL,
    state        text NOT NULL CHECK (state IN ('reserved', 'settled')),
    reserved_usd numeric(12, 6) NOT NULL,
    cost_usd     numeric(12, 6) NOT NULL DEFAULT 0
);
CREATE INDEX spend_day ON gateway.spend (day);
CREATE INDEX spend_month ON gateway.spend (month);
CREATE INDEX spend_sandbox_day ON gateway.spend (sandbox_id, day);
CREATE INDEX spend_ip_day ON gateway.spend (ip_hash, day);

CREATE TABLE gateway.calls (
    id            bigserial PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    request_id    text NOT NULL,
    sandbox_id    uuid,
    purpose       text NOT NULL,
    route         text NOT NULL,
    prompt_ref    text NOT NULL,
    provider      text NOT NULL,
    model         text NOT NULL,
    outcome       text NOT NULL,
    error_kind    text,
    latency_ms    integer NOT NULL,
    input_tokens  integer NOT NULL DEFAULT 0,
    cache_read_tokens  integer NOT NULL DEFAULT 0,
    cache_write_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_usd      numeric(12, 6) NOT NULL DEFAULT 0,
    provider_request_id text
);
CREATE INDEX calls_at ON gateway.calls (at);

CREATE TABLE gateway.refusals (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    request_id  text NOT NULL,
    sandbox_id  uuid,
    purpose     text NOT NULL,
    reason      text NOT NULL
);
CREATE INDEX refusals_at ON gateway.refusals (at);

-- ---------------------------------------------------------------------------------------------
-- Row security and privileges
-- ---------------------------------------------------------------------------------------------

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'app.conversations', 'app.messages', 'app.vault_entries', 'app.pending_actions',
        'app.handovers', 'app.feedback', 'app.turns',
        'corebank.customers', 'corebank.accounts', 'corebank.cards', 'corebank.transactions',
        'corebank.recipients', 'corebank.loans', 'corebank.complaints', 'corebank.idempotency',
        'audit.events', 'audit.chain_heads'
    ] LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY own_copy ON %s USING (sandbox_id = app.visitor()) WITH CHECK (sandbox_id = app.visitor())', t);
    END LOOP;
END
$$;
ALTER TABLE app.sandboxes ENABLE ROW LEVEL SECURITY;
CREATE POLICY own_copy ON app.sandboxes USING (id = app.visitor()) WITH CHECK (id = app.visitor());

-- The jobs queue has no row security: the worker claims jobs for every copy, and each job holds
-- only ids. The worker sets app.visitor to the job's copy while it runs it.

-- How many copies an address made recently, for the limit on new copies. Returns a count only.
CREATE FUNCTION app.copies_from_address(p_ip_hash text, p_since timestamptz) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT count(*)::integer FROM app.sandboxes WHERE ip_hash = p_ip_hash AND created_at >= p_since
$$;
REVOKE ALL ON FUNCTION app.copies_from_address(text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.copies_from_address(text, timestamptz) TO kaskazi_app;

-- Deletes copies unused for a day, with everything in them, and their audit chains. The only
-- way audit events are ever removed. It logs counts, never content.
CREATE FUNCTION app.purge_expired_copies(p_idle interval DEFAULT interval '24 hours')
RETURNS TABLE (copies integer, events bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_ids    uuid[];
    v_events bigint;
BEGIN
    SELECT coalesce(array_agg(id), '{}') INTO v_ids
    FROM app.sandboxes WHERE last_used_at < now() - p_idle;
    IF cardinality(v_ids) = 0 THEN
        copies := 0; events := 0; RETURN NEXT; RETURN;
    END IF;
    PERFORM set_config('kaskazi.retention_purge', 'on', true);
    DELETE FROM audit.events WHERE sandbox_id = ANY (v_ids);
    GET DIAGNOSTICS v_events = ROW_COUNT;
    PERFORM set_config('kaskazi.retention_purge', 'off', true);
    DELETE FROM app.jobs WHERE sandbox_id = ANY (v_ids);
    DELETE FROM app.sandboxes WHERE id = ANY (v_ids);
    INSERT INTO audit.retention_log (sandboxes, events) VALUES (cardinality(v_ids), v_events);
    copies := cardinality(v_ids); events := v_events; RETURN NEXT;
END
$$;
REVOKE ALL ON FUNCTION app.purge_expired_copies(interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.purge_expired_copies(interval) TO kaskazi_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    app.sandboxes, app.conversations, app.messages, app.vault_entries, app.pending_actions,
    app.handovers, app.feedback, app.turns, app.jobs,
    corebank.customers, corebank.accounts, corebank.cards, corebank.transactions,
    corebank.recipients, corebank.loans, corebank.complaints, corebank.idempotency
TO kaskazi_app;
GRANT INSERT ON app.requests TO kaskazi_app;
GRANT SELECT ON app.prompt_versions, app.releases TO kaskazi_app;
GRANT SELECT ON kb.corpus_versions, kb.documents, kb.chunks TO kaskazi_app;
GRANT SELECT, INSERT ON audit.events TO kaskazi_app;
GRANT SELECT, INSERT, UPDATE ON audit.chain_heads TO kaskazi_app;
GRANT EXECUTE ON FUNCTION audit.append(uuid, text, text, jsonb), audit.verify() TO kaskazi_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app, corebank TO kaskazi_app;

GRANT SELECT, INSERT, UPDATE ON gateway.spend TO kaskazi_gateway;
GRANT INSERT ON gateway.calls, gateway.refusals TO kaskazi_gateway;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA gateway TO kaskazi_gateway;
-- The API reads the gateway's call log (metadata only) for monitoring.
GRANT SELECT ON gateway.calls, gateway.refusals, gateway.spend TO kaskazi_app;
