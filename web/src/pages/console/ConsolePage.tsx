import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError, api } from "../../api/client";
import { DemoBanner, Wordmark } from "../../components/Brand";
import { useEventStream } from "../../hooks/useEventStream";
import { when } from "../../lib/format";

type Role = "agent" | "reviewer" | "ai_lead";
type Handover = {
  id: string;
  conversation_id: string;
  reason: string;
  trigger: string;
  priority: "normal" | "urgent";
  status: "waiting" | "active" | "resolved";
  summary: null | { summary?: string; reason?: string; facts?: string[]; open_questions?: string[]; suggested_first_reply?: string; urgency?: string };
  created_at: string;
};
type Transcript = { id: string; status: string; entries: { seq: number; author: string; masked_text: string | null; at: string }[] };
type AuditEvent = { seq: number; at: string; conversation: string | null; turn: string | null; kind: string; payload: Record<string, unknown>; hash: string };

const ROLE_NAMES: Record<Role, string> = { agent: "Agent", reviewer: "Reviewer", ai_lead: "AI lead" };

export function ConsolePage() {
  const me = useQuery({ queryKey: ["staff"], queryFn: () => api.get<{ role: Role }>("/api/console/me"), retry: false });
  useEffect(() => {
    document.documentElement.dataset.theme = "dark";
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, []);

  if (me.isLoading) return <p className="page-loading">Opening the console…</p>;
  if (me.isError || !me.data) {
    return (
      <div className="console-shell">
        <DemoBanner />
        <main className="console-empty" id="main">
          <h1>The staff console</h1>
          <p>Open it from the home page, after choosing a sample customer: it works on your own copy.</p>
          <Link className="btn btn-primary" to="/">Go to the home page</Link>
        </main>
      </div>
    );
  }
  return <Console role={me.data.role} />;
}

function Console({ role }: { role: Role }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const switchRole = useMutation({
    mutationFn: (next: Role) => api.post("/api/console/enter", { role: next }),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  useEventStream("/api/console/stream", (e) => {
    if (e.type === "queue_changed") void queryClient.invalidateQueries({ queryKey: ["handovers"] });
  });

  return (
    <div className="console-shell">
      <a className="skip-link" href="#main">Skip to the main content</a>
      <div className="app-top">
        <DemoBanner />
        <header className="console-header">
          <Wordmark to="/" />
          <span className="console-title">Staff console</span>
          <nav className="role-switch" aria-label="Role">
            {(["agent", "ai_lead"] as Role[]).map((r) => (
              <button key={r} type="button" className={`role-tab${r === role ? " is-current" : ""}`} aria-current={r === role ? "page" : undefined} onClick={() => switchRole.mutate(r)}>
                {ROLE_NAMES[r]}
              </button>
            ))}
          </nav>
          <button type="button" className="btn btn-quiet btn-small" onClick={() => navigate("/chat")}>
            Back to the customer app
          </button>
        </header>
      </div>
      <main id="main" className="console-main">
        {role === "ai_lead" ? <AuditTrail /> : <HandoverQueue />}
      </main>
    </div>
  );
}

function HandoverQueue() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const queue = useQuery({ queryKey: ["handovers"], queryFn: () => api.get<Handover[]>("/api/console/handovers") });
  const current = queue.data?.find((h) => h.id === selected) ?? queue.data?.[0] ?? null;

  return (
    <div className="queue-layout">
      <section className="queue" aria-labelledby="queue-title">
        <h1 id="queue-title" className="console-h1">Handovers</h1>
        <p className="muted small">Conversations the assistant passed to a person. Urgent ones first.</p>
        {queue.data?.length === 0 ? (
          <p className="empty">Nothing waiting. Ask the assistant for a person in the customer app, and it lands here.</p>
        ) : null}
        <ul className="queue-list">
          {queue.data?.map((h) => (
            <li key={h.id}>
              <button type="button" className={`queue-item${current?.id === h.id ? " is-current" : ""}`} onClick={() => setSelected(h.id)} aria-current={current?.id === h.id}>
                <span className={`chip ${h.priority === "urgent" ? "chip-danger" : ""}`}>{h.priority === "urgent" ? "Urgent" : h.status === "active" ? "In progress" : "Waiting"}</span>
                <span className="queue-reason">{h.summary?.summary ?? h.reason}</span>
                <span className="muted small">{when(h.created_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      {current ? (
        <HandoverDetail key={current.id} handover={current} onDone={() => void queryClient.invalidateQueries({ queryKey: ["handovers"] })} />
      ) : null}
    </div>
  );
}

function HandoverDetail({ handover, onDone }: { handover: Handover; onDone: () => void }) {
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  const transcript = useQuery({
    queryKey: ["transcript", handover.conversation_id],
    queryFn: () => api.get<Transcript>(`/api/console/conversations/${handover.conversation_id}`),
    refetchInterval: 5000,
  });
  const send = useMutation({
    mutationFn: () => api.post(`/api/console/handovers/${handover.id}/reply`, { text: reply }),
    onSuccess: () => {
      setReply("");
      void transcript.refetch();
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "That didn't send."),
  });
  const resolve = useMutation({
    mutationFn: (handBack: boolean) => api.post(`/api/console/handovers/${handover.id}/resolve`, { outcome: handBack ? "Resolved; handed back to the assistant" : "Resolved and closed", hand_back: handBack }),
    onSuccess: onDone,
  });
  // A summary written by a model: read every field as optional.
  const s = handover.summary;
  const facts = s?.facts ?? [];
  const questions = s?.open_questions ?? [];

  return (
    <section className="handover" aria-labelledby="handover-title">
      <h2 id="handover-title" className="console-h2">{handover.reason}</h2>
      {s ? (
        <div className="summary">
          <p>{s.summary ?? "No summary was written."}</p>
          {facts.length ? (
            <>
              <h3>Facts</h3>
              <ul>{facts.map((f) => <li key={f}>{f}</li>)}</ul>
            </>
          ) : null}
          {questions.length ? (
            <>
              <h3>Still to find out</h3>
              <ul>{questions.map((q) => <li key={q}>{q}</li>)}</ul>
            </>
          ) : null}
          {s.suggested_first_reply ? (
            <button type="button" className="link-btn" onClick={() => setReply(s.suggested_first_reply ?? "")}>
              Use the suggested reply
            </button>
          ) : null}
        </div>
      ) : (
        <p className="muted small">The summary is being written…</p>
      )}
      <h3 className="console-h3">Transcript (as the AI saw it, personal details masked)</h3>
      <ol className="transcript">
        {transcript.data?.entries.map((e) => (
          <li key={e.seq} className={`t-entry t-${e.author}`}>
            <span className="t-author">{e.author}</span>
            <p>{e.masked_text}</p>
          </li>
        ))}
      </ol>
      {handover.status !== "resolved" ? (
        <form
          className="reply"
          onSubmit={(e) => {
            e.preventDefault();
            if (reply.trim()) send.mutate();
          }}
        >
          <label htmlFor="reply-box">Reply to the customer</label>
          <textarea id="reply-box" rows={3} maxLength={1500} value={reply} onChange={(e) => setReply(e.target.value)} />
          <div className="reply-actions">
            <button type="submit" className="btn btn-primary" disabled={!reply.trim() || send.isPending}>Send</button>
            <button type="button" className="btn btn-quiet" onClick={() => resolve.mutate(true)} disabled={resolve.isPending}>Resolve and hand back</button>
            <button type="button" className="btn btn-quiet" onClick={() => resolve.mutate(false)} disabled={resolve.isPending}>Resolve and close</button>
          </div>
          {error ? <p className="field-error" role="alert">{error}</p> : null}
        </form>
      ) : null}
    </section>
  );
}

function AuditTrail() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<number | null>(null);
  const events = useQuery({ queryKey: ["audit"], queryFn: () => api.get<{ events: AuditEvent[] }>("/api/console/audit?limit=500") });
  const verify = useQuery({ queryKey: ["audit-verify"], queryFn: () => api.get<{ events: number; intact: boolean; first_broken: number | null }>("/api/console/audit/verify") });
  const [outage, setOutage] = useState(false);
  const toggle = useMutation({
    mutationFn: (on: boolean) => api.post<{ simulate_primary_outage: boolean }>("/api/console/controls/outage", { simulate_primary_outage: on }),
    onSuccess: (r) => setOutage(r.simulate_primary_outage),
  });

  const turns = new Map<string, AuditEvent[]>();
  for (const e of events.data?.events ?? []) {
    const key = e.turn ?? `event-${e.seq}`;
    turns.set(key, [...(turns.get(key) ?? []), e]);
  }

  return (
    <div className="audit">
      <div className="audit-head">
        <div>
          <h1 className="console-h1">Audit trail</h1>
          <p className="muted small">Every step of every turn in your copy, append-only and hash-chained.</p>
        </div>
        <div className="audit-tools">
          <p className={`chip ${verify.data?.intact ? "chip-ok" : verify.data ? "chip-danger" : ""}`} role="status">
            {verify.data ? (verify.data.intact ? `Chain intact · ${verify.data.events} events` : `Chain broken at event ${verify.data.first_broken}`) : "Checking the chain…"}
          </p>
          <button type="button" className="btn btn-quiet btn-small" onClick={() => void queryClient.invalidateQueries({ queryKey: ["audit"] }).then(() => queryClient.invalidateQueries({ queryKey: ["audit-verify"] }))}>
            Refresh and verify
          </button>
          <label className="switch">
            <input type="checkbox" checked={outage} onChange={(e) => toggle.mutate(e.target.checked)} />
            <span>Simulate an outage of the primary model provider (your copy only)</span>
          </label>
        </div>
      </div>
      <ol className="turns">
        {[...turns.entries()].reverse().map(([turn, list]) => {
          const output = list.find((e) => e.kind === "output");
          const input = list.find((e) => e.kind === "input");
          return (
            <li key={turn} className="turn">
              <div className="turn-head">
                {output ? <span className="chip">{String(output.payload.outcome)}</span> : null}
                <span className="turn-text">
                  {input ? String(input.payload.masked_text) : (list[0]?.kind ?? "").replaceAll("_", " ")}
                </span>
                <span className="muted small">{when(list[0]?.at ?? "")}</span>
              </div>
              <ol className="events">
                {list.map((e) => (
                  <li key={e.seq}>
                    <button type="button" className="event" aria-expanded={open === e.seq} onClick={() => setOpen(open === e.seq ? null : e.seq)}>
                      <span className="event-kind">{e.kind.replaceAll("_", " ")}</span>
                      <span className="event-hash tabular">#{e.seq} · {e.hash}</span>
                    </button>
                    {open === e.seq ? <pre className="payload">{JSON.stringify(e.payload, null, 2)}</pre> : null}
                  </li>
                ))}
              </ol>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
