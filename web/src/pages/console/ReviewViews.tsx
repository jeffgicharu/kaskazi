import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { DeskDocument, DocField, LoanApplication } from "../../api/types";
import { money, when } from "../../lib/format";
import { DocChecks, DocStatus, certainty, fieldValue } from "../apply/DocumentParts";

// Shown partly hidden; a correction is typed in full, never edited from the hidden form.
const HIDDEN_FIELDS = new Set(["employee_kra_pin", "employer_kra_pin", "id_number"]);

export function ReviewQueue() {
  const reviews = useQuery({ queryKey: ["reviews"], queryFn: () => api.get<{ applications: LoanApplication[] }>("/api/console/reviews") });
  const [chosen, setChosen] = useState<string | null>(null);
  const apps = reviews.data?.applications ?? [];
  const current = apps.find((a) => a.id === chosen) ?? apps[0] ?? null;

  return (
    <div className="queue-layout">
      <section aria-labelledby="reviews-title">
        <h1 id="reviews-title" className="console-h1">
          Loan applications
        </h1>
        <p className="muted small">Read by the AI, decided by you. Every change is in the audit trail.</p>
        {reviews.isLoading ? <p className="muted">Loading…</p> : null}
        {reviews.isSuccess && apps.length === 0 ? (
          <p className="empty">
            Nothing waiting. Apply for a loan in the customer app (Apply for a loan, under the accounts), send it for a decision, and it appears here.
          </p>
        ) : null}
        <ul className="queue-list">
          {apps.map((a) => (
            <li key={a.id}>
              <button type="button" className={`queue-item${a.id === current?.id ? " is-current" : ""}`} aria-current={a.id === current?.id ? "true" : undefined} onClick={() => setChosen(a.id)}>
                <span className="queue-reason">
                  {money(a.amount)} over {a.term_months} months
                </span>
                <span className="muted small">
                  {a.documents.filter((d) => d.status === "needs_review").length} document(s) to check · {when(a.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      {current ? <ApplicationReview key={current.id} application={current} /> : null}
    </div>
  );
}

function ApplicationReview({ application }: { application: LoanApplication }) {
  const queryClient = useQueryClient();
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "decline" | "more_documents") =>
      api.post<LoanApplication>(`/api/console/applications/${application.id}/decide`, { decision, reason }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reviews"] }),
  });
  const a = application.assessment?.affordability;
  const waiting = application.documents.filter((d) => !["verified", "approved", "corrected"].includes(d.status));

  return (
    <article className="handover review" aria-label={`Application for ${money(application.amount)}`}>
      <h2 className="console-h2">
        {money(application.amount)} over {application.term_months} months
      </h2>
      {a ? (
        <section className="summary" aria-labelledby="afford-title">
          <h3 id="afford-title">Affordability, from the payslip</h3>
          <dl className="ledger">
            <div className="ledger-row">
              <dt>Net pay on the payslip</dt>
              <dd className="amount">{a.net_pay_from_payslip ? money(Number(a.net_pay_from_payslip)) : "—"}</dd>
            </div>
            {a.affordability_limit != null ? (
              <>
                <div className="ledger-row">
                  <dt>40% of net pay, for all loan repayments</dt>
                  <dd className="amount">{money(a.affordability_limit)}</dd>
                </div>
                <div className="ledger-row">
                  <dt>Repayments already on the bank's records</dt>
                  <dd className="amount">{money(a.existing_monthly_repayments ?? 0)}</dd>
                </div>
                <div className="ledger-row">
                  <dt>Room for this loan's instalment</dt>
                  <dd className="amount">{money(a.room_for_new_instalment ?? 0)}</dd>
                </div>
                <div className="ledger-row ledger-row--strong">
                  <dt>The requested loan fits</dt>
                  <dd>
                    <span className={`chip ${a.requested_fits ? "chip-ok" : "chip-danger"}`}>{a.requested_fits ? "Yes" : "No"}</span>
                  </dd>
                </div>
              </>
            ) : null}
          </dl>
          {a.reasons?.length ? (
            <ul className="review-reasons">
              {a.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
      {application.assessment?.checks.length ? (
        <section aria-labelledby="across-title">
          <h3 id="across-title" className="console-h3">
            Across the documents and the bank's records
          </h3>
          <DocChecks checks={application.assessment.checks} />
        </section>
      ) : null}
      {application.documents.map((d) => (
        <DocumentReview key={d.id} document={d} />
      ))}
      <section className="decision" aria-labelledby="decide-title">
        <h3 id="decide-title" className="console-h3">
          Your decision
        </h3>
        <label htmlFor={reasonId} className="reply">
          Reason the customer will read (needed to decline or to ask for documents)
          <textarea id={reasonId} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
        </label>
        <div className="reply-actions">
          <button type="button" className="btn btn-primary" disabled={decide.isPending || waiting.length > 0} onClick={() => decide.mutate("approve")}>
            Approve and pay out
          </button>
          <button type="button" className="btn btn-quiet" disabled={decide.isPending || !reason.trim()} onClick={() => decide.mutate("more_documents")}>
            Ask for documents
          </button>
          <button type="button" className="btn btn-quiet" disabled={decide.isPending || !reason.trim()} onClick={() => decide.mutate("decline")}>
            Decline
          </button>
        </div>
        {waiting.length > 0 ? <p className="muted small">Check {waiting.map((d) => d.title.toLowerCase()).join(" and ")} first.</p> : null}
        {decide.error ? (
          <p className="field-error" role="alert">
            {decide.error instanceof ApiError ? decide.error.message : "That didn't work."}
          </p>
        ) : null}
      </section>
    </article>
  );
}

type Segment = { text: string; mark: boolean };

function highlight(text: string, evidence: string[]): Segment[] {
  const patterns = evidence
    .filter((e) => e.trim().length > 2)
    .map((e) => e.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"));
  if (patterns.length === 0) return [{ text, mark: false }];
  const re = new RegExp(`(${patterns.join("|")})`, "g");
  return text.split(re).map((part, i) => ({ text: part, mark: i % 2 === 1 }));
}

function DocumentReview({ document }: { document: DeskDocument }) {
  const queryClient = useQueryClient();
  const [edits, setEdits] = useState<Record<string, string>>({});
  const text = useQuery({
    queryKey: ["document-text", document.id],
    queryFn: () => api.get<{ visible: string | null; hidden: string[] }>(`/api/console/documents/${document.id}/text`),
    enabled: document.media_type === "application/pdf",
  });
  // Wide column gaps are narrowed for the screen; the evidence matches across any run of spaces.
  const segments = useMemo(
    () => (text.data?.visible ? highlight(text.data.visible.replace(/ {6,}/g, "     "), document.fields.map((f) => f.evidence ?? "")) : []),
    [text.data, document.fields],
  );
  const review = useMutation({
    mutationFn: (decision: "approved" | "corrected" | "rejected") =>
      api.post<DeskDocument>(`/api/console/documents/${document.id}/review`, { decision, corrections: decision === "corrected" ? edits : {} }),
    onSuccess: async () => {
      setEdits({});
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
    },
  });
  const changed = Object.keys(edits).length > 0;
  const decided = ["approved", "corrected", "rejected"].includes(document.status);

  return (
    <section className="doc-review" aria-labelledby={`doc-${document.id}`}>
      <div className="doc-slot-head">
        <h3 id={`doc-${document.id}`}>{document.title}</h3>
        <DocStatus status={document.status} />
      </div>
      <p className="muted small">
        {document.filename} · read {document.path === "image" ? "from the image" : "from the PDF's text"} by <code>{document.model ?? "—"}</code>
      </p>
      {document.review_reasons.length ? (
        <ul className="review-reasons">
          {document.review_reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}
      <div className="doc-review-body">
        <div className="doc-preview">
          {document.media_type === "application/pdf" ? (
            <>
              {text.data?.hidden.length ? (
                <p className="hidden-text" role="note">
                  <strong>Hidden on the page:</strong> {text.data.hidden.join(" ")}
                </p>
              ) : null}
              <pre className="payload doc-text" tabIndex={0} aria-label={`The text of the ${document.title.toLowerCase()}, with what was read marked`}>
                {segments.map((s, i) => (s.mark ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
              </pre>
            </>
          ) : (
            <img src={`/api/console/documents/${document.id}/file`} alt={`The ${document.title.toLowerCase()} as given`} className="doc-image" />
          )}
        </div>
        <div className="doc-correct">
          <table className="data-table doc-edit">
            <caption className="visually-hidden">Fields read from the {document.title.toLowerCase()}</caption>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Read as</th>
                <th scope="col">Correct to</th>
              </tr>
            </thead>
            <tbody>
              {document.fields.map((f: DocField) => {
                const c = certainty(f);
                return (
                  <tr key={f.name}>
                    <th scope="row">{f.label}</th>
                    <td>
                      <span className="tabular">{fieldValue(f)}</span> <span className={`certainty ${c.tone}`}>{c.label}</span>
                    </td>
                    <td>
                      <input
                        aria-label={`Correct ${f.label.toLowerCase()}`}
                        className="field-input"
                        disabled={decided}
                        placeholder={HIDDEN_FIELDS.has(f.name) ? "type it in full" : ""}
                        value={edits[f.name] ?? (HIDDEN_FIELDS.has(f.name) ? "" : (f.value ?? ""))}
                        onChange={(e) => {
                          const v = e.target.value;
                          const unchanged = v === (HIDDEN_FIELDS.has(f.name) ? "" : (f.value ?? ""));
                          setEdits((now) => {
                            const others = Object.fromEntries(Object.entries(now).filter(([name]) => name !== f.name));
                            return unchanged ? others : { ...others, [f.name]: v };
                          });
                        }}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!decided ? (
            <div className="reply-actions">
              <button type="button" className="btn btn-primary btn-small" disabled={review.isPending || changed} onClick={() => review.mutate("approved")}>
                Approve as read
              </button>
              <button type="button" className="btn btn-quiet btn-small" disabled={review.isPending || !changed} onClick={() => review.mutate("corrected")}>
                Save {Object.keys(edits).length || ""} correction{Object.keys(edits).length === 1 ? "" : "s"}
              </button>
              <button type="button" className="btn btn-quiet btn-small" disabled={review.isPending} onClick={() => review.mutate("rejected")}>
                Reject
              </button>
            </div>
          ) : null}
          {review.error ? (
            <p className="field-error" role="alert">
              {review.error instanceof ApiError ? review.error.message : "That didn't work."}
            </p>
          ) : null}
          <details className="doc-details">
            <summary>Checks on this document</summary>
            <DocChecks checks={document.checks} />
          </details>
        </div>
      </div>
    </section>
  );
}
