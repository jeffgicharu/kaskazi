import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, api } from "../api/client";
import type { Persona, Session } from "../api/types";
import { DemoBanner, WindLines, Wordmark } from "../components/Brand";

const ROLES = [
  { key: "agent", title: "Agent", text: "Take conversations the assistant hands over, with the summary and what it looked at." },
  { key: "ai_lead", title: "AI lead", text: "Read the audit trail of every turn, verify its hash chain, and switch on a provider outage." },
] as const;

export function Landing() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const personas = useQuery({ queryKey: ["personas"], queryFn: () => api.get<Persona[]>("/api/personas") });
  const session = useQuery({ queryKey: ["session"], queryFn: () => api.get<Session>("/api/session") });

  const start = useMutation({
    mutationFn: (persona: Persona["key"]) => api.post<{ conversation_id: string }>("/api/session", { persona }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session"] });
      navigate("/chat");
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "That didn't work. Please try again."),
  });

  const enter = useMutation({
    mutationFn: (role: string) => api.post("/api/console/enter", { role }),
    onSuccess: () => navigate("/console"),
    onError: (e) => setError(e instanceof ApiError ? e.message : "That didn't work. Please try again."),
  });

  const signedIn = session.data?.signed_in === true;

  return (
    <>
      <a className="skip-link" href="#main">Skip to the main content</a>
      <DemoBanner />
      <header className="site-header">
        <Wordmark />
        {signedIn ? (
          <button type="button" className="btn btn-quiet" onClick={() => navigate("/chat")}>Back to the app</button>
        ) : null}
      </header>
      <main id="main" className="landing">
        <section className="landing-hero" aria-labelledby="hero-title">
          <WindLines className="landing-wind" />
          <p className="eyebrow">An AI platform for an invented Kenyan bank</p>
          <h1 id="hero-title">
            A banking assistant that cites its sources, asks before it acts, and knows when to call a person.
          </h1>
          <p className="lede">
            Kaskazi Bank isn't real: its products, prices and customers are sample data. The AI is real. Every
            answer comes from the bank's own documents, every change waits for your confirmation, and personal
            details are hidden before any AI service sees them.
          </p>
        </section>

        {error ? (
          <p className="form-error" role="alert">{error}</p>
        ) : null}

        <section className="landing-section" aria-labelledby="choose-title">
          <h2 id="choose-title">Be one of the bank's sample customers</h2>
          <p className="section-lede">You get your own copy of their accounts. Nothing you do is seen by anyone else, and the copy is deleted a day after you last use it.</p>
          <ul className="persona-grid">
            {(personas.data ?? []).map((p, i) => (
              <li key={p.key} className="persona-card" style={{ animationDelay: `${i * 70}ms` }}>
                <span className="persona-initial" aria-hidden="true">{p.first_name.slice(0, 1)}</span>
                <h3>{p.label}</h3>
                <p>{p.summary}</p>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={start.isPending}
                  onClick={() => start.mutate(p.key)}
                  aria-label={`Continue as ${p.first_name}`}
                >
                  {start.isPending && start.variables === p.key ? "Opening…" : `Continue as ${p.first_name}`}
                </button>
              </li>
            ))}
            {personas.isLoading ? <li className="persona-card persona-card--loading" aria-busy="true">Loading the sample customers…</li> : null}
          </ul>
        </section>

        <section className="landing-section" aria-labelledby="staff-title">
          <h2 id="staff-title">Or open the staff console</h2>
          <p className="section-lede">
            {signedIn
              ? "It opens on your own copy, so you can hand a conversation over and pick it up as staff."
              : "Choose a sample customer first: the console works on your own copy."}
          </p>
          <div className="role-grid">
            {ROLES.map((r) => (
              <div key={r.key} className="role-card">
                <h3>{r.title}</h3>
                <p>{r.text}</p>
                <button type="button" className="btn btn-quiet" disabled={!signedIn || enter.isPending} onClick={() => enter.mutate(r.key)}>
                  Open as {r.title.toLowerCase()}
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="landing-section landing-facts" aria-labelledby="facts-title">
          <h2 id="facts-title">What is real here, and what isn't</h2>
          <div className="facts-grid">
            <div>
              <h3>Real</h3>
              <ul>
                <li>The AI models: Claude, with OpenAI taking over if Claude fails.</li>
                <li>The controls: masking, injection screening, confirmations, spending caps.</li>
                <li>The audit trail, which can't be edited.</li>
              </ul>
            </div>
            <div>
              <h3>Sample</h3>
              <ul>
                <li>The bank, its products, fees and rules.</li>
                <li>The customers, their money and their documents.</li>
                <li>Phone numbers, codes and messages: nothing is ever sent.</li>
              </ul>
            </div>
          </div>
        </section>
      </main>
      <footer className="site-footer">
        <p>Kaskazi Bank is invented for this demonstration. It isn't regulated by anyone and has no customers.</p>
      </footer>
    </>
  );
}
