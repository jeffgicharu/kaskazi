import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router";

import { ApiError, api } from "../../api/client";
import type { Conversation, Overview, Session, Source, ThreadMessage, TurnResult } from "../../api/types";
import { Wordmark } from "../../components/Brand";
import { useEventStream, type StreamEvent } from "../../hooks/useEventStream";
import { money, time } from "../../lib/format";
import { MessageView } from "./Messages";
import { SourceDrawer } from "./SourceDrawer";

// What the core-banking service says about each card, in the customer's words.
const CARD_STATUS: Record<string, string> = { active: "Active", blocked: "Blocked", cancelled: "Cancelled" };

const SUGGESTIONS = [
  "How much does it cost to send KES 5,000 to mobile money?",
  "What's my balance?",
  "Could I get a personal loan of KES 200,000 over 2 years?",
  "I've lost my debit card",
];

export function ChatPage() {
  const session = useQuery({ queryKey: ["session"], queryFn: () => api.get<Session>("/api/session") });
  if (session.isLoading) return <p className="page-loading">Opening your sample copy…</p>;
  if (!session.data?.signed_in) return <Navigate to="/" replace />;
  return <Chat session={session.data} />;
}

function Chat({ session }: { session: Extract<Session, { signed_in: true }> }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const conversationId = session.conversation_id;
  const [draft, setDraft] = useState("");
  const [progress, setProgress] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [railOpen, setRailOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api.get<Conversation>(`/api/conversation?id=${conversationId}`),
  });
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/api/quick/overview") });

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
  }, [queryClient, conversationId]);

  useEventStream(`/api/stream?conversation=${conversationId}`, (event: StreamEvent) => {
    if (event.type === "progress" && typeof event.label === "string") setProgress(event.label);
    if (event.type === "agent_message" || event.type === "agent_done") refresh();
  });

  const send = useMutation({
    mutationFn: (text: string) => api.post<TurnResult>("/api/chat", { conversation_id: conversationId, text }),
    onMutate: (text) => {
      setPending(text);
      setError(null);
      setProgress("Reading your message");
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
      await queryClient.invalidateQueries({ queryKey: ["session"] });
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e, text) => {
      setError(e instanceof ApiError ? e.message : "That didn't send. Please try again.");
      setDraft(text);
    },
    onSettled: () => {
      setPending(null);
      setProgress(null);
      inputRef.current?.focus();
    },
  });

  const askForPerson = useMutation({
    mutationFn: () => api.post("/api/handover", { conversation_id: conversationId }),
    onSuccess: refresh,
  });

  const messages: ThreadMessage[] = conversation.data?.messages ?? [];
  const chat = messages.filter((m) => m.author !== "sms");
  const texts = messages.filter((m) => m.author === "sms");
  const withAgent = conversation.data?.status === "with_agent";

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.length, pending]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || send.isPending) return;
    setDraft("");
    send.mutate(text);
  };

  const leave = async () => {
    await api.del("/api/session");
    await queryClient.invalidateQueries();
    navigate("/");
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#composer">Skip to the message box</a>
      <div className="app-top">
        <header className="app-header">
          <Wordmark to="/" />
          <div className="app-header-meta">
            <span className="who">
              <span className="wide-only">Signed in as </span>
              <strong>{session.first_name}</strong> <span className="muted wide-only">(sample customer)</span>
            </span>
            {session.questions_left_today !== null ? (
              <span className="chip" title="Today's allowance for this demonstration">
                <span className="wide-only">about {session.questions_left_today} questions left today</span>
                <span className="narrow-only">{session.questions_left_today} left</span>
              </span>
            ) : null}
            <button type="button" className="btn btn-quiet btn-small" onClick={() => void leave()}>
              Leave
            </button>
          </div>
        </header>
      </div>

      <div className="chat-layout">
        <button
          type="button"
          className="rail-toggle"
          aria-expanded={railOpen}
          aria-controls="rail"
          onClick={() => setRailOpen((open) => !open)}
        >
          <span>Accounts, quick actions and texts</span>
          {texts.length ? <span className="chip">{texts.length} new</span> : null}
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" className="rail-toggle-chevron">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        <main className="chat-main" id="main" aria-label="Conversation with the Kaskazi assistant">
          <div className="thread-log" role="log" aria-live="polite" aria-relevant="additions" aria-label="Messages">
          <ol className="thread">
            <li className="msg msg--assistant msg--welcome">
              <div className="bubble bubble--assistant">
                <p>
                  Hello {session.first_name}. I'm Kaskazi's AI assistant. I answer from the bank's own documents and show you
                  where each answer comes from, and I'll always ask you to confirm before anything on your account changes.
                </p>
              </div>
              {chat.length === 0 ? (
                <div className="suggestions" aria-label="Things you could ask">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" className="suggestion" onClick={() => send.mutate(s)} disabled={send.isPending}>
                      {s}
                    </button>
                  ))}
                </div>
              ) : null}
            </li>
            {chat.map((m) => (
              <MessageView
                key={m.id}
                message={m}
                conversationId={conversationId}
                onOpenSource={setSource}
                onAskForPerson={() => askForPerson.mutate()}
              />
            ))}
            {pending ? (
              <li className="msg msg--customer msg--pending">
                <div className="bubble bubble--customer">
                  <p>{pending}</p>
                </div>
              </li>
            ) : null}
          </ol>
          </div>
          <p className="progress" role="status" aria-live="polite">
            {send.isPending ? (
              <>
                <span className="dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                {progress ?? "Working on it"}…
              </>
            ) : null}
          </p>
          <div ref={endRef} className="scroll-anchor" />

          {withAgent ? (
            <p className="with-agent" role="status">
              A colleague is looking after this conversation. They'll reply here.
            </p>
          ) : null}
          {error ? (
            <p className="form-error" role="alert">{error}</p>
          ) : null}
          <form className="composer" id="composer" onSubmit={submit}>
            <label htmlFor="message" className="visually-hidden">
              Your message
            </label>
            <textarea
              id="message"
              ref={inputRef}
              rows={1}
              maxLength={2000}
              value={draft}
              placeholder={withAgent ? "Write to our colleague…" : "Ask about your accounts, cards, loans or fees"}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) submit(e);
              }}
            />
            <button type="submit" className="btn btn-primary send" disabled={!draft.trim() || send.isPending} aria-label="Send">
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path d="M4 12l16-8-6 16-2.5-6.5L4 12z" fill="currentColor" />
              </svg>
            </button>
          </form>
          <p className="composer-hint muted">Don't share real details here: this is a demonstration with sample data.</p>
        </main>

        <aside id="rail" className={`rail${railOpen ? " rail--open" : ""}`} aria-label="Your accounts and quick actions">
          <QuickActions overview={overview.data} conversationId={conversationId} onAskForPerson={() => askForPerson.mutate()} withAgent={withAgent} />
          <section className="rail-card phone" aria-labelledby="phone-title">
            <h2 id="phone-title" className="rail-title">Your phone (sample)</h2>
            {texts.length === 0 ? (
              <p className="muted small">Text messages from the bank, such as one-time codes, appear here. Nothing is really sent.</p>
            ) : (
              <ol className="texts">
                {texts.map((t) => (
                  <li key={t.id}>
                    <p>{t.display.text}</p>
                    <span className="muted small">{time(t.at)}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </aside>
      </div>
      {source ? <SourceDrawer source={source} onClose={() => setSource(null)} /> : null}
    </div>
  );
}

function QuickActions({
  overview,
  conversationId,
  onAskForPerson,
  withAgent,
}: {
  overview: Overview | undefined;
  conversationId: string;
  onAskForPerson: () => void;
  withAgent: boolean;
}) {
  const queryClient = useQueryClient();
  const [blocking, setBlocking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const block = useMutation({
    mutationFn: (body: { card: string; mode: "temporary" | "lost_or_stolen" }) =>
      api.post("/api/quick/block-card", { conversation_id: conversationId, ...body }),
    onSuccess: async () => {
      setBlocking(false);
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "That didn't work."),
  });
  const active = overview?.cards.filter((c) => c.status === "active") ?? [];

  return (
    <section className="rail-card" aria-labelledby="accounts-title">
      <h2 id="accounts-title" className="rail-title">Your accounts</h2>
      <p className="muted small">These work without the AI.</p>
      <dl className="ledger ledger--rail">
        {(overview?.accounts ?? []).map((a) => (
          <div key={a.number} className="ledger-row">
            <dt>
              {a.product.replace("Kaskazi ", "")} <span className="muted">{a.number}</span>
            </dt>
            <dd className="amount">{money(a.balance, a.currency)}</dd>
          </div>
        ))}
        {(overview?.cards ?? []).map((c) => (
          <div key={c.ref} className="ledger-row">
            <dt>
              {c.kind === "credit" ? "Credit card" : "Debit card"} <span className="muted tabular">••••{c.last4}</span>
            </dt>
            <dd>
              <span className={`card-status card-status--${c.status}`}>{CARD_STATUS[c.status] ?? c.status}</span>
            </dd>
          </div>
        ))}
      </dl>
      <div className="quick">
        {blocking ? (
          <div className="quick-block" role="group" aria-label="Block a card">
            {active.length === 0 ? <p className="muted small">No active cards to block.</p> : null}
            {active.map((c) => (
              <div key={c.ref} className="quick-card">
                <p>
                  {c.kind === "credit" ? "Credit" : "Debit"} card ending <span className="tabular">{c.last4}</span>
                </p>
                <div className="quick-card-actions">
                  <button type="button" className="btn btn-quiet btn-small" onClick={() => block.mutate({ card: c.ref, mode: "temporary" })} disabled={block.isPending}>
                    Block for now
                  </button>
                  <button type="button" className="btn btn-quiet btn-small" onClick={() => block.mutate({ card: c.ref, mode: "lost_or_stolen" })} disabled={block.isPending}>
                    Lost or stolen
                  </button>
                </div>
              </div>
            ))}
            <button type="button" className="link-btn" onClick={() => setBlocking(false)}>
              Never mind
            </button>
          </div>
        ) : (
          <button type="button" className="btn btn-quiet" onClick={() => setBlocking(true)}>
            Block a card
          </button>
        )}
        <button type="button" className="btn btn-quiet" onClick={onAskForPerson} disabled={withAgent}>
          {withAgent ? "With a colleague now" : "Talk to a person"}
        </button>
        <Link className="btn btn-quiet" to="/apply">
          Apply for a loan
        </Link>
      </div>
      {error ? (
        <p className="field-error" role="alert">{error}</p>
      ) : null}
    </section>
  );
}
