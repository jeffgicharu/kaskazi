-- The document desk: loan applications, the documents given with them, and reviewers' decisions.
--
-- Everything belongs to the visitor's copy, is row-secured like the rest of it, and goes when the
-- copy is purged. A document's content is kept here (3 MB at most), not on disk, so the copy's
-- deletion takes it too. The audit trail records a document's hash and its masked fields, never
-- its content.

CREATE TABLE app.loan_applications (
    id           uuid PRIMARY KEY,
    sandbox_id   uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    customer_id  uuid NOT NULL,
    amount       integer NOT NULL CHECK (amount > 0),
    term_months  smallint NOT NULL CHECK (term_months BETWEEN 6 AND 60),
    status       text NOT NULL DEFAULT 'documents_requested' CHECK (status IN (
                     'documents_requested', 'under_review', 'approved', 'declined', 'withdrawn')),
    assessment   jsonb,  -- the desk's work for the reviewer: affordability, checks across documents
    decision     jsonb,  -- the reviewer's decision, reason and the loan paid out
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX loan_applications_sandbox ON app.loan_applications (sandbox_id, created_at DESC);

CREATE TABLE app.documents (
    id              uuid PRIMARY KEY,
    sandbox_id      uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    application_id  uuid NOT NULL REFERENCES app.loan_applications ON DELETE CASCADE,
    kind            text NOT NULL CHECK (kind IN ('payslip', 'id', 'statement')),
    source          text NOT NULL CHECK (source IN ('sample', 'upload')),
    sample_key      text,
    filename        text NOT NULL,
    media_type      text NOT NULL CHECK (media_type IN ('application/pdf', 'image/png', 'image/jpeg')),
    content         bytea NOT NULL CHECK (octet_length(content) <= 3145728),
    sha256          text NOT NULL,
    path            text CHECK (path IN ('text', 'image')),
    status          text NOT NULL DEFAULT 'received' CHECK (status IN (
                        'received', 'verified', 'needs_review', 'approved', 'corrected',
                        'rejected', 'unreadable')),
    extraction      jsonb,
    checks          jsonb,
    review_reasons  jsonb,
    hidden_text     jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_application ON app.documents (application_id);

-- A reviewer's corrections are kept as labelled data: what the desk read, and what was right.
CREATE TABLE app.document_reviews (
    id           bigserial PRIMARY KEY,
    sandbox_id   uuid NOT NULL REFERENCES app.sandboxes ON DELETE CASCADE,
    document_id  uuid NOT NULL REFERENCES app.documents ON DELETE CASCADE,
    decision     text NOT NULL CHECK (decision IN ('approved', 'corrected', 'rejected')),
    corrections  jsonb NOT NULL DEFAULT '{}',
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['app.loan_applications', 'app.documents', 'app.document_reviews'] LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY own_copy ON %s USING (sandbox_id = app.visitor()) WITH CHECK (sandbox_id = app.visitor())', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %s TO kaskazi_app', t);
    END LOOP;
END
$$;
GRANT USAGE ON SEQUENCE app.document_reviews_id_seq TO kaskazi_app;
