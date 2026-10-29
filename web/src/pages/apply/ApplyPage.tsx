import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Link, Navigate } from "react-router";

import { ApiError, api } from "../../api/client";
import type { DeskDocument, DocKind, LoanApplication, LoanQuote, SampleDocument, Session } from "../../api/types";
import { Wordmark } from "../../components/Brand";
import { Breeze } from "../../components/Breeze";
import { useOnScreen } from "../../hooks/useOnScreen";
import { money } from "../../lib/format";
import { DocChecks, DocFields, DocStatus } from "./DocumentParts";

/** A payslip and an identity card, in depth, with the reading line passing over them: decoration. */
function Papers() {
  return (
    <div className="papers" aria-hidden="true">
      <div className="paper paper--id">
        <span className="paper-photo" />
        {[64, 82, 46].map((w, i) => (
          <span key={i} className="paper-line" style={{ width: `${w}%` }} />
        ))}
      </div>
      <div className="paper paper--slip">
        <span className="paper-title">Payslip</span>
        {[78, 52, 88, 60, 72, 40].map((w, i) => (
          <span key={i} className="paper-line" style={{ width: `${w}%` }} />
        ))}
        <span className="paper-total" />
        <span className="paper-scan" />
      </div>
    </div>
  );
}

const KIND_TITLE: Record<DocKind, string> = { payslip: "Your latest payslip", id: "Your identity card", statement: "A bank statement" };
const TERMS = [6, 12, 18, 24, 36, 48, 60];

export function ApplyPage() {
  const session = useQuery({ queryKey: ["session"], queryFn: () => api.get<Session>("/api/session") });
  if (session.isLoading) return <p className="page-loading">Opening your sample copy…</p>;
  if (!session.data?.signed_in) return <Navigate to="/" replace />;
  return <Apply firstName={session.data.first_name} />;
}

function Apply({ firstName }: { firstName: string }) {
  const current = useQuery({ queryKey: ["application"], queryFn: () => api.get<LoanApplication | null>("/api/applications/current") });
  const open = current.data && current.data.status !== "withdrawn" ? current.data : null;
  const startNew = !open || open.status === "approved" || open.status === "declined";
  // The papers in the header stop floating once the header is scrolled away.
  const intro = useRef<HTMLDivElement>(null);
  const introSeen = useOnScreen(intro);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">Skip to the main content</a>
      <div className="app-top">
        <header className="app-header">
          <Wordmark to="/" />
          <div className="app-header-meta">
            <span className="who">
              <span className="wide-only">Signed in as </span>
              <strong>{firstName}</strong> <span className="muted wide-only">(sample customer)</span>
            </span>
            <Link className="btn btn-quiet btn-small" to="/chat">
              Back to the chat
            </Link>
          </div>
        </header>
      </div>
      <main id="main" className="apply">
        <div ref={intro} className="apply-intro" data-away={introSeen ? undefined : "yes"}>
          <Breeze />
          <Papers />
          <p className="eyebrow">Kaskazi Personal Loan</p>
          <h1>Apply with your documents</h1>
          <p className="lede">
            The AI reads your payslip and identity card and checks them against each other, the bank's records and the 2026 statutory rates. A person then decides. It never approves a loan
            itself.
          </p>
          <p className="notice-inline">Use the sample documents below. This is a demonstration: please don't upload real documents.</p>
        </div>
        {current.isLoading ? <p className="muted">Looking for your application…</p> : null}
        {current.isError ? <p className="field-error" role="alert">Your application couldn't be loaded.</p> : null}
        {!current.isLoading && startNew ? <StartStep finished={open} /> : null}
        {open && !startNew ? <ApplicationSteps application={open} /> : null}
      </main>
    </div>
  );
}

function StartStep({ finished }: { finished: LoanApplication | null }) {
  const queryClient = useQueryClient();
  const amountId = useId();
  const termId = useId();
  const [amount, setAmount] = useState(200_000);
  const [term, setTerm] = useState(24);
  const [debounced, setDebounced] = useState({ amount, term });
  useEffect(() => {
    const t = setTimeout(() => setDebounced({ amount, term }), 350);
    return () => clearTimeout(t);
  }, [amount, term]);
  const inRange = debounced.amount >= 50_000 && debounced.amount <= 3_000_000;
  const quote = useQuery({
    queryKey: ["quote", debounced.amount, debounced.term],
    queryFn: () => api.get<LoanQuote>(`/api/applications/quote?amount=${debounced.amount}&term_months=${debounced.term}`),
    enabled: inRange,
  });
  const start = useMutation({
    mutationFn: () => api.post<LoanApplication>("/api/applications", { amount, term_months: term }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["application"] }),
  });

  return (
    <>
      {finished ? <Outcome application={finished} /> : null}
      <section className="apply-step" aria-labelledby="step-1">
        <h2 id="step-1" className="apply-step-title">
          <span className="step-n">1</span> How much, and over how long
        </h2>
        <div className="apply-form">
          <label htmlFor={amountId}>
            Amount (KES)
            <input
              id={amountId}
              type="number"
              inputMode="numeric"
              min={50_000}
              max={3_000_000}
              step={5_000}
              value={amount}
              onChange={(e) => setAmount(Number(e.target.value))}
              aria-describedby={`${amountId}-hint`}
            />
            <span id={`${amountId}-hint`} className="hint">
              KES 50,000 to 3,000,000
            </span>
          </label>
          <label htmlFor={termId}>
            Repay over
            <select id={termId} value={term} onChange={(e) => setTerm(Number(e.target.value))}>
              {TERMS.map((t) => (
                <option key={t} value={t}>
                  {t} months
                </option>
              ))}
            </select>
          </label>
        </div>
        {!inRange ? <p className="field-error">Choose an amount from KES 50,000 to KES 3,000,000.</p> : null}
        {quote.data ? (
          <dl className="ledger quote" aria-live="polite">
            <div className="ledger-row ledger-row--strong">
              <dt>Each month</dt>
              <dd className="amount">{money(quote.data.monthly_instalment)}</dd>
            </div>
            <div className="ledger-row">
              <dt>Interest</dt>
              <dd>{quote.data.interest_rate}% a year, reducing balance</dd>
            </div>
            <div className="ledger-row">
              <dt>Arrangement fee and credit life insurance</dt>
              <dd className="amount">{money(quote.data.arrangement_fee + quote.data.credit_life_insurance)}</dd>
            </div>
            <div className="ledger-row">
              <dt>You receive</dt>
              <dd className="amount">{money(quote.data.amount_received)}</dd>
            </div>
            <div className="ledger-row">
              <dt>Total cost of credit</dt>
              <dd className="amount">{money(quote.data.total_cost_of_credit)}</dd>
            </div>
          </dl>
        ) : null}
        <button type="button" className="btn btn-primary" disabled={!inRange || start.isPending} onClick={() => start.mutate()}>
          {start.isPending ? "Starting…" : "Start the application"}
        </button>
        {start.error ? (
          <p className="field-error" role="alert">
            {start.error instanceof ApiError ? start.error.message : "That didn't work."}
          </p>
        ) : null}
      </section>
    </>
  );
}

function ApplicationSteps({ application }: { application: LoanApplication }) {
  const queryClient = useQueryClient();
  const submit = useMutation({
    mutationFn: () => api.post<LoanApplication>(`/api/applications/${application.id}/submit`),
    onSuccess: (a) => queryClient.setQueryData(["application"], a),
  });
  const taking = application.status === "documents_requested";
  const byKind = new Map(application.documents.map((d) => [d.kind, d]));

  return (
    <>
      <section className="apply-step apply-step--done" aria-labelledby="step-1">
        <h2 id="step-1" className="apply-step-title">
          <span className="step-n">1</span> {money(application.amount)} over {application.term_months} months
        </h2>
      </section>
      {application.decision?.decision === "more_documents" && taking ? (
        <p className="notice-inline" role="status">
          A colleague asked for more: {application.decision.reason}
        </p>
      ) : null}
      <section className="apply-step" aria-labelledby="step-2">
        <h2 id="step-2" className="apply-step-title">
          <span className="step-n">2</span> Your documents
        </h2>
        <div className="doc-slots">
          {(["payslip", "id"] as DocKind[]).map((kind) => (
            <DocumentSlot key={kind} applicationId={application.id} kind={kind} document={byKind.get(kind) ?? null} editable={taking} />
          ))}
        </div>
      </section>
      <section className="apply-step" aria-labelledby="step-3">
        <h2 id="step-3" className="apply-step-title">
          <span className="step-n">3</span> A person decides
        </h2>
        {taking ? (
          <>
            <p>A colleague checks what the AI read and decides. You'll get a text on your sample phone when they have.</p>
            <button type="button" className="btn btn-primary" disabled={application.still_needed.length > 0 || submit.isPending} onClick={() => submit.mutate()}>
              {submit.isPending ? "Sending…" : "Send for a decision"}
            </button>
            {application.still_needed.length > 0 ? (
              <p className="muted small">Add {application.still_needed.map((k) => KIND_TITLE[k].replace("Your ", "your ")).join(" and ")} first.</p>
            ) : null}
            {submit.error ? (
              <p className="field-error" role="alert">
                {submit.error instanceof ApiError ? submit.error.message : "That didn't work."}
              </p>
            ) : null}
          </>
        ) : (
          <Outcome application={application} />
        )}
      </section>
    </>
  );
}

function Outcome({ application }: { application: LoanApplication }) {
  if (application.status === "under_review") {
    return (
      <div className="outcome" role="status">
        <p className="outcome-title">With a colleague for a decision</p>
        <p className="muted">
          Open the staff console as a reviewer, from the home page, to make the decision yourself.
        </p>
      </div>
    );
  }
  if (application.status === "approved" && application.decision?.paid_out) {
    const paid = application.decision.paid_out;
    return (
      <div className="outcome outcome--ok" role="status">
        <p className="outcome-title">Approved and paid out</p>
        <p>
          {money(paid.amount_received)} was paid into your {paid.account}. You repay {money(paid.monthly_instalment)} a month.
        </p>
      </div>
    );
  }
  if (application.status === "declined") {
    return (
      <div className="outcome outcome--bad" role="status">
        <p className="outcome-title">Not approved</p>
        <p>{application.decision?.reason}</p>
      </div>
    );
  }
  return null;
}

function DocumentSlot({ applicationId, kind, document, editable }: { applicationId: string; kind: DocKind; document: DeskDocument | null; editable: boolean }) {
  // The chooser shows when there is no document yet, or when the customer asks to replace one.
  const [replacing, setReplacing] = useState(false);
  const choosing = document === null || replacing;
  return (
    <div className="doc-slot">
      <div className="doc-slot-head">
        <h3>{KIND_TITLE[kind]}</h3>
        {document ? <DocStatus status={document.status} /> : null}
      </div>
      {document ? <DocumentResult document={document} /> : null}
      {editable && document && !choosing ? (
        <button type="button" className="link-btn" onClick={() => setReplacing(true)}>
          Give a different {kind === "id" ? "ID card" : kind}
        </button>
      ) : null}
      {editable && choosing ? <Chooser applicationId={applicationId} kind={kind} onDone={() => setReplacing(false)} /> : null}
    </div>
  );
}

function DocumentResult({ document }: { document: DeskDocument }) {
  return (
    <div className="doc-result">
      <p className="muted small">
        {document.source === "sample" ? "Sample" : "Uploaded"}: {document.filename} · read {document.path === "image" ? "from the image" : "from the text in the PDF"}
      </p>
      {document.review_reasons.length > 0 ? (
        <ul className="review-reasons">
          {document.review_reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}
      <DocFields fields={document.fields} />
      <details className="doc-details">
        <summary>What was checked ({document.checks.filter((c) => c.ok === true).length} passed)</summary>
        <DocChecks checks={document.checks} />
      </details>
    </div>
  );
}

function Chooser({ applicationId, kind, onDone }: { applicationId: string; kind: DocKind; onDone: () => void }) {
  const queryClient = useQueryClient();
  const samples = useQuery({ queryKey: ["samples"], queryFn: () => api.get<SampleDocument[]>("/api/applications/samples") });
  const fileId = useId();
  const consentId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const done = async (a: DeskDocument) => {
    await queryClient.invalidateQueries({ queryKey: ["application"] });
    if (a) onDone();
  };
  const addSample = useMutation({
    mutationFn: (key: string) => api.post<DeskDocument>(`/api/applications/${applicationId}/documents/sample`, { key }),
    onSuccess: done,
  });
  const addFile = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("application_id", applicationId);
      form.append("kind", kind);
      form.append("image_consent", String(consent));
      if (file) form.append("file", file);
      return api.upload<DeskDocument>("/api/documents", form);
    },
    onSuccess: done,
  });
  const busy = addSample.isPending || addFile.isPending;
  const failure = addSample.error ?? addFile.error;
  const isImage = file !== null && file.type !== "application/pdf";
  const mine = (samples.data ?? []).filter((s) => s.kind === kind);

  return (
    <div className="chooser">
      {busy ? (
        <p className="reading" role="status">
          Reading the document and checking it…
        </p>
      ) : null}
      <p className="chooser-label">Choose a sample</p>
      <ul className="sample-list">
        {mine.map((s) => (
          <li key={s.key}>
            <button type="button" className="sample" disabled={busy} onClick={() => addSample.mutate(s.key)}>
              <span className="sample-title">
                {s.title}
                {s.has_fault ? <span className="chip chip-warn">Has a fault</span> : null}
              </span>
              <span className="muted small">{s.about}</span>
            </button>
          </li>
        ))}
        {samples.isSuccess && mine.length === 0 ? <li className="muted small">No sample of this kind for this customer.</li> : null}
      </ul>
      <details className="upload">
        <summary>Or upload a file</summary>
        <p className="muted small">PDF, PNG or JPEG, 3 MB and 3 pages at most. Please don't upload a real document: this is a demonstration.</p>
        <label htmlFor={fileId} className="upload-file">
          File
          <input id={fileId} type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        {isImage ? (
          <label htmlFor={consentId} className="switch">
            <input id={consentId} type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>I agree that an AI service will see this image. Unlike a PDF's text, an image can't have personal details hidden first.</span>
          </label>
        ) : null}
        <button type="button" className="btn btn-quiet" disabled={!file || busy || (isImage && !consent)} onClick={() => addFile.mutate()}>
          Upload and read
        </button>
      </details>
      {failure ? (
        <p className="field-error" role="alert">
          {failure instanceof ApiError ? failure.message : "That didn't work."}
        </p>
      ) : null}
    </div>
  );
}
