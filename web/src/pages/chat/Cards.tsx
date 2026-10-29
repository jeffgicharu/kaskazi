import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { Card, ConfirmCard } from "../../api/types";
import { money, shortDate, time } from "../../lib/format";

const APPLICATION_STATUS: Record<string, string> = {
  documents_requested: "Waiting for your documents",
  under_review: "With a colleague for a decision",
  approved: "Approved and paid out",
  declined: "Not approved",
  withdrawn: "Withdrawn",
};

export function CardView({ card, conversationId }: { card: Card; conversationId: string }) {
  switch (card.type) {
    case "confirm":
      return <ConfirmSlip card={card} conversationId={conversationId} />;
    case "accounts":
      return (
        <div className="slip slip--plain">
          <p className="slip-kicker">Your accounts</p>
          <dl className="ledger">
            {card.accounts.map((a) => (
              <div key={a.number} className="ledger-row">
                <dt>
                  {a.product} <span className="muted">{a.number}</span>
                </dt>
                <dd className="amount">{money(a.balance, a.currency)}</dd>
              </div>
            ))}
          </dl>
        </div>
      );
    case "statement":
      return (
        <div className="slip slip--plain">
          <p className="slip-kicker">
            {card.product} <span className="muted">{card.number}</span>
          </p>
          <table className="statement">
            <caption className="visually-hidden">Latest transactions on your {card.product}</caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Description</th>
                <th scope="col" className="num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {card.transactions.map((t, i) => (
                <tr key={i}>
                  <td>{shortDate(t.at)}</td>
                  <td>{t.description}</td>
                  <td className={`num amount ${t.amount < 0 ? "out" : "in"}`}>{money(t.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "quote":
      return (
        <div className="slip slip--plain">
          <p className="slip-kicker">
            Personal loan quote · {money(card.amount)} over {card.term_months} months
          </p>
          <dl className="ledger">
            <Row label="Monthly instalment" value={money(card.monthly_instalment)} strong />
            <Row label={`Interest, ${card.interest_rate}% a year`} value={money(card.total_repaid - card.amount)} />
            <Row label="Arrangement fee" value={money(card.arrangement_fee)} />
            <Row label="Credit life insurance" value={money(card.credit_life_insurance)} />
            <Row label="You receive" value={money(card.amount_received)} />
            <Row label="Total cost of credit" value={money(card.total_cost_of_credit)} />
          </dl>
          <p className="slip-note">A quote, not an offer. A person decides every loan.</p>
        </div>
      );
    case "eligibility":
      return (
        <div className="slip slip--plain">
          <p className="slip-kicker">What you could borrow</p>
          {card.eligible ? (
            <dl className="ledger">
              <Row label="Room for a new instalment" value={money(card.room_for_new_instalment)} strong />
              {Object.entries(card.largest_loan_by_term).map(([term, amount]) => (
                <Row key={term} label={`Largest loan over ${term} months`} value={money(amount)} />
              ))}
            </dl>
          ) : (
            <ul className="reasons">
              {card.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          <p className="slip-note">Indicative only. A person decides every loan.</p>
        </div>
      );
    case "suggestions":
      if (card.suggestions.length === 0) return null;
      return (
        <div className="slip slip--plain">
          <p className="slip-kicker">Suggested for you</p>
          <ul className="suggestions-list">
            {card.suggestions.map((s) => (
              <li key={s.rule}>
                <p className="suggestion-title">{s.title}</p>
                <p className="muted small">
                  {s.product} · about {money(s.benefit_kes_a_year)} more a year, before tax
                </p>
                <details className="why">
                  <summary>Why am I seeing this?</summary>
                  <p>{s.why}</p>
                  <p className="muted small">Worked out by the bank's rules from your own accounts. Kaskazi never suggests borrowing.</p>
                </details>
              </li>
            ))}
          </ul>
        </div>
      );
    case "application":
      return (
        <div className="slip slip--plain">
          <p className="slip-kicker">
            Your loan application · {money(card.amount)} over {card.term_months} months
          </p>
          <dl className="ledger">
            <Row label="Where it stands" value={APPLICATION_STATUS[card.status] ?? card.status} strong />
            {card.documents.map((d) => (
              <Row key={d.kind} label={d.kind === "id" ? "Identity card" : d.kind === "payslip" ? "Payslip" : "Bank statement"} value={d.status.replaceAll("_", " ")} />
            ))}
            <Row label="Started" value={shortDate(card.started)} />
          </dl>
          {card.decision_reason ? <p className="slip-note">{card.decision_reason}</p> : null}
        </div>
      );
    default:
      return null;
  }
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`ledger-row${strong ? " ledger-row--strong" : ""}`}>
      <dt>{label}</dt>
      <dd className="amount">{value}</dd>
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  done: "Done",
  failed: "Didn't go through",
  cancelled: "Cancelled",
  expired: "Expired",
  confirmed: "Working on it",
};

function ConfirmSlip({ card, conversationId }: { card: ConfirmCard; conversationId: string }) {
  const queryClient = useQueryClient();
  const codeId = useId();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState(card.status ?? "pending");

  const decide = useMutation({
    mutationFn: (confirm: boolean) =>
      api.post<{ status: string; notice?: string }>(confirm ? "/api/actions/confirm" : "/api/actions/cancel", {
        action_id: card.action_id,
        token: card.token,
        code: card.needs_code ? code : null,
      }),
    onSuccess: async (result) => {
      setStatus(result.status as ConfirmCard["status"] & string);
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e) => {
      setError(e instanceof ApiError ? e.message : "That didn't go through. Please try again.");
      if (e instanceof ApiError && (e.status === 409 || e.status === 410)) setStatus("expired");
    },
  });

  const open = status === "pending" && Boolean(card.token) && new Date(card.expires_at) > new Date();
  const finished = status !== "pending";

  return (
    <div className={`slip slip--confirm${finished ? " slip--finished" : ""}`}>
      <p className="slip-kicker">Check and confirm</p>
      <h3 className="slip-title">{card.title}</h3>
      <dl className="ledger">
        {card.rows.map(([label, value]) => (
          <div key={label} className="ledger-row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {card.note ? <p className="slip-note">{card.note}</p> : null}
      {finished ? (
        <p className={`chip ${status === "done" ? "chip-ok" : status === "failed" ? "chip-danger" : ""}`} role="status">
          {STATUS_TEXT[status] ?? status}
        </p>
      ) : open ? (
        <form
          className="slip-actions"
          onSubmit={(e) => {
            e.preventDefault();
            decide.mutate(true);
          }}
        >
          {card.needs_code ? (
            <div className="field">
              <label htmlFor={codeId}>One-time code from your text messages</label>
              <input
                id={codeId}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="\d{6}"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              />
            </div>
          ) : null}
          <div className="slip-buttons">
            <button type="submit" className="btn btn-primary" disabled={decide.isPending}>
              {decide.isPending ? "Confirming…" : "Confirm"}
            </button>
            <button type="button" className="btn btn-quiet" disabled={decide.isPending} onClick={() => decide.mutate(false)}>
              Cancel
            </button>
          </div>
          <p className="slip-expiry muted">Ready until {time(card.expires_at)}</p>
        </form>
      ) : (
        <p className="chip">{card.token ? "Expired" : "Open the conversation where it was prepared to confirm"}</p>
      )}
      {error ? (
        <p className="field-error" role="alert">{error}</p>
      ) : null}
    </div>
  );
}
