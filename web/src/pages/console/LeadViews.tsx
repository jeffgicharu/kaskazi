import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { shortDate, when } from "../../lib/format";

// --- Shapes the console API returns ---------------------------------------------------------------

type Breaker = "closed" | "open" | "half_open";
type Monitoring = {
  days: number;
  gateway: null | {
    ai_enabled: boolean;
    caps: { day_usd: number; month_usd: number; visitor_day_usd: number; address_day_usd: number };
    spent: { day_usd: number; month_usd: number };
    breakers: Record<string, Breaker>;
  };
  turns: { day: string; outcome: string; turns: number; p50_ms: number; p90_ms: number; cost_usd: number; fallbacks: number }[];
  feedback: { rating: number; reason: string; ratings: number }[];
  calls_24h: { route: string; provider: string; model: string; calls: number; failures: number; p50_ms: number; p90_ms: number; cost_usd: number; cache_share: number | null }[];
  refusals_24h: { reason: string; refusals: number }[];
};

type Criterion = { text: string; verdict: "met" | "missed" | "contradicted"; why: string };
type AnswersReport = {
  name: string;
  run_on: string;
  label: string | null;
  fingerprint: string;
  covers_this_release: boolean;
  baseline: string | null;
  metrics: Record<string, number | null>;
  gate: { passed: boolean; reasons: string[] };
  judge_cost_usd: number | null;
  misses: { id: string; question: string; outcome: string | null; answer: string; criteria: Criterion[] }[];
};
type Evaluations = {
  this_release: string | null;
  answers: AnswersReport[];
  other: { name: string; run_on: string | null; report: Record<string, unknown> }[];
};

type PromptVersion = { version: string; owner: string; route: string; hash: string; change: string; changed_on: string; first_release: string | null; live: boolean; body: string };
type Prompts = { prompts: { id: string; versions: PromptVersion[] }[] };

// --- Formatting ------------------------------------------------------------------------------------

const usd = (n: number, digits = 2) => `$${n.toFixed(digits)}`;
const seconds = (ms: number | null | undefined) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)} s`);
const percent = (share: number | null | undefined) => (share == null ? "–" : `${Math.round(share * 1000) / 10}%`);

const ROUTE_NAMES: Record<string, string> = {
  "assistant.answer": "Answer",
  "assistant.router": "Router",
  embeddings: "Search vectors",
  "handover.summary": "Handover summary",
  "documents.extract_text": "Document reading (text)",
  "documents.extract_image": "Document reading (image)",
  "evals.judge": "Evaluation judge",
};

const BREAKER: Record<Breaker, { label: string; tone: string }> = {
  closed: { label: "calls go through", tone: "chip-ok" },
  half_open: { label: "trying one call", tone: "chip-warn" },
  open: { label: "paused: the fallback answers", tone: "chip-danger" },
};

// --- Monitoring ------------------------------------------------------------------------------------

function CapMeter({ label, spent, cap }: { label: string; spent: number; cap: number }) {
  const share = cap > 0 ? Math.min(spent / cap, 1) : 0;
  const near = share >= 0.8;
  return (
    <div className="tile">
      <p className="tile-label">{label}</p>
      <p className="tile-value tabular">
        {usd(spent)} <span className="tile-of">of {usd(cap)}</span>
      </p>
      <div
        className={`meter${near ? " meter--near" : ""}`}
        role="meter"
        aria-label={`${label}: ${usd(spent)} of a ${usd(cap)} cap`}
        aria-valuemin={0}
        aria-valuemax={cap}
        aria-valuenow={Math.round(spent * 100) / 100}
      >
        <span className="meter-fill" style={{ width: `${share * 100}%` }} />
      </div>
      <p className="tile-note">{near ? "Close to the cap: the assistant stops when it is reached." : `${percent(share)} of the cap`}</p>
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <p className="tile-label">{label}</p>
      <p className="tile-value tabular">{value}</p>
      {note ? <p className="tile-note">{note}</p> : null}
    </div>
  );
}

const REFUSED = new Set(["off_topic", "injection_refused"]);
const NOT_ANSWERED = new Set(["safe_answer", "unavailable", "model_refused", "cap"]);

export function MonitoringView() {
  const data = useQuery({ queryKey: ["monitoring"], queryFn: () => api.get<Monitoring>("/api/console/monitoring?days=7"), refetchInterval: 30_000 });
  if (data.isLoading) return <p className="muted">Reading the figures…</p>;
  if (data.isError || !data.data) return <p className="field-error" role="alert">The figures didn't load.</p>;
  const m = data.data;

  const days = new Map<string, { turns: number; answered: number; handover: number; refused: number; notAnswered: number; p50?: number; p90?: number; cost: number; fallbacks: number }>();
  for (const r of m.turns) {
    const d = days.get(r.day) ?? { turns: 0, answered: 0, handover: 0, refused: 0, notAnswered: 0, cost: 0, fallbacks: 0 };
    d.turns += r.turns;
    d.cost += r.cost_usd ?? 0;
    d.fallbacks += r.fallbacks;
    if (r.outcome === "answered") Object.assign(d, { answered: d.answered + r.turns, p50: r.p50_ms, p90: r.p90_ms });
    else if (r.outcome === "handover") d.handover += r.turns;
    else if (REFUSED.has(r.outcome)) d.refused += r.turns;
    else if (NOT_ANSWERED.has(r.outcome)) d.notAnswered += r.turns;
    days.set(r.day, d);
  }
  const total = [...days.values()].reduce((a, d) => ({ turns: a.turns + d.turns, answered: a.answered + d.answered, handover: a.handover + d.handover, cost: a.cost + d.cost }), { turns: 0, answered: 0, handover: 0, cost: 0 });
  const helpful = m.feedback.filter((f) => f.rating > 0).reduce((a, f) => a + f.ratings, 0);
  const unhelpful = m.feedback.filter((f) => f.rating < 0);

  return (
    <div className="lead-view">
      <div className="lead-head">
        <h1 className="console-h1">Monitoring</h1>
        <p className="muted small">Every visitor, in numbers only: no message, copy or address leaves the database for this page. Refreshes every 30 seconds.</p>
      </div>

      <h2 className="console-h3">Spending against the caps</h2>
      {m.gateway ? (
        <>
          <div className="tiles">
            <CapMeter label="Today (Nairobi time)" spent={m.gateway.spent.day_usd} cap={m.gateway.caps.day_usd} />
            <CapMeter label="This month" spent={m.gateway.spent.month_usd} cap={m.gateway.caps.month_usd} />
            <Tile label="Each visitor, a day" value={usd(m.gateway.caps.visitor_day_usd)} note={`and ${usd(m.gateway.caps.address_day_usd)} for each network address`} />
          </div>
          <ul className="breakers" aria-label="Model providers">
            <li>
              <span className={`chip ${m.gateway.ai_enabled ? "chip-ok" : "chip-danger"}`}>{m.gateway.ai_enabled ? "AI switched on" : "AI switched off"}</span>
            </li>
            {Object.entries(m.gateway.breakers).map(([name, state]) => (
              <li key={name}>
                <span className={`chip ${BREAKER[state].tone}`}>
                  {name === "anthropic" ? "Anthropic" : name === "openai" ? "OpenAI" : name}: {BREAKER[state].label}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="field-error" role="alert">The model gateway didn't answer, so the caps can't be shown.</p>
      )}

      <h2 className="console-h3">Visitors' turns, last {m.days} days</h2>
      <div className="tiles">
        <Tile label="Turns" value={String(total.turns)} />
        <Tile label="Answered" value={total.turns ? percent(total.answered / total.turns) : "–"} note={`${total.answered} turns`} />
        <Tile label="Passed to a person" value={String(total.handover)} />
        <Tile label="Model spending" value={usd(total.cost, 4)} note="visitors' turns only" />
      </div>
      {days.size === 0 ? (
        <p className="empty">No visitor has asked anything in this period.</p>
      ) : (
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Turns by day">
          <table className="data-table">
            <caption className="visually-hidden">Turns by day</caption>
            <thead>
              <tr>
                <th scope="col">Day</th>
                <th scope="col" className="num">Turns</th>
                <th scope="col" className="num">Answered</th>
                <th scope="col" className="num">To a person</th>
                <th scope="col" className="num">Refused</th>
                <th scope="col" className="num">Not answered</th>
                <th scope="col" className="num">Reply p50</th>
                <th scope="col" className="num">Reply p90</th>
                <th scope="col" className="num">Cost</th>
                <th scope="col" className="num">Fallbacks</th>
              </tr>
            </thead>
            <tbody>
              {[...days.entries()].reverse().map(([day, d]) => (
                <tr key={day}>
                  <th scope="row">{shortDate(day)}</th>
                  <td className="num">{d.turns}</td>
                  <td className="num">{d.answered}</td>
                  <td className="num">{d.handover}</td>
                  <td className="num">{d.refused}</td>
                  <td className="num">{d.notAnswered}</td>
                  <td className="num">{seconds(d.p50)}</td>
                  <td className="num">{seconds(d.p90)}</td>
                  <td className="num">{usd(d.cost, 4)}</td>
                  <td className="num">{d.fallbacks}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="console-h3">Model calls, last 24 hours</h2>
      <p className="muted small">Every call the gateway made, the release checks' included.</p>
      {m.calls_24h.length === 0 ? (
        <p className="empty">No calls in the last 24 hours.</p>
      ) : (
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Model calls by route">
          <table className="data-table">
            <caption className="visually-hidden">Model calls by route</caption>
            <thead>
              <tr>
                <th scope="col">Route</th>
                <th scope="col">Model</th>
                <th scope="col" className="num">Calls</th>
                <th scope="col" className="num">Failed</th>
                <th scope="col" className="num">p50</th>
                <th scope="col" className="num">p90</th>
                <th scope="col" className="num">Prompt from cache</th>
                <th scope="col" className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {m.calls_24h.map((c) => (
                <tr key={`${c.route}-${c.model}`}>
                  <th scope="row">{ROUTE_NAMES[c.route] ?? c.route}</th>
                  <td>
                    <code>{c.model}</code>
                  </td>
                  <td className="num">{c.calls}</td>
                  <td className="num">{c.failures}</td>
                  <td className="num">{seconds(c.p50_ms)}</td>
                  <td className="num">{seconds(c.p90_ms)}</td>
                  <td className="num">{percent(c.cache_share)}</td>
                  <td className="num">{usd(c.cost_usd, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="two-up">
        <section aria-labelledby="refusals-title">
          <h2 id="refusals-title" className="console-h3">Stopped at the gateway, last 24 hours</h2>
          {m.refusals_24h.length === 0 ? (
            <p className="empty">Nothing was stopped.</p>
          ) : (
            <ul className="count-list">
              {m.refusals_24h.map((r) => (
                <li key={r.reason}>
                  <span>{r.reason.replaceAll("_", " ")}</span>
                  <span className="tabular">{r.refusals}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section aria-labelledby="ratings-title">
          <h2 id="ratings-title" className="console-h3">Visitors' ratings, last {m.days} days</h2>
          {m.feedback.length === 0 ? (
            <p className="empty">No ratings yet.</p>
          ) : (
            <ul className="count-list">
              <li>
                <span>Helpful</span>
                <span className="tabular">{helpful}</span>
              </li>
              {unhelpful.map((f) => (
                <li key={f.reason}>
                  <span>Not helpful: {f.reason.replaceAll("_", " ")}</span>
                  <span className="tabular">{f.ratings}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

// --- Evaluations -----------------------------------------------------------------------------------

const METRICS: { key: string; label: string; format: (v: number | null) => string; hint: string }[] = [
  { key: "key_fact", label: "Key fact stated", format: percent, hint: "answers giving the fact the question turns on" },
  { key: "fact_coverage", label: "All criteria met", format: percent, hint: "secondary details included" },
  { key: "outcome_ok", label: "Answered", format: percent, hint: "document and record questions" },
  { key: "grounded", label: "Cites a passage", format: percent, hint: "answers from the documents" },
  { key: "unanswerable_ok", label: "No invention", format: percent, hint: "when the documents can't answer" },
  { key: "behaviour_ok", label: "Refusals and handovers", format: percent, hint: "off-topic, injection, fraud, a person" },
  { key: "card_offered", label: "Waits to confirm", format: percent, hint: "every change" },
  { key: "contradicted", label: "Contradictions", format: (v) => (v == null ? "–" : String(v)), hint: "criteria an answer got wrong" },
  { key: "latency_ms_p50", label: "Reply p50", format: seconds, hint: "server time" },
  { key: "cost_usd_per_case", label: "Cost per question", format: (v) => (v == null ? "–" : usd(v, 4)), hint: "model calls" },
];

function Misses({ report }: { report: AnswersReport }) {
  const [open, setOpen] = useState(false);
  if (report.misses.length === 0) return null;
  return (
    <div className="misses">
      <button type="button" className="link-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Read"} the {report.misses.length} answers that missed something
      </button>
      {open ? (
        <ol className="miss-list">
          {report.misses.map((m) => (
            <li key={m.id} className="miss">
              <p className="miss-q">
                <span className="tabular muted">{m.id}</span> {m.question}
              </p>
              <p className="miss-a">{m.answer || <em>(no answer: {m.outcome})</em>}</p>
              <ul className="miss-criteria">
                {m.criteria.map((c) => (
                  <li key={c.text}>
                    <span className={`chip ${c.verdict === "contradicted" ? "chip-danger" : "chip-warn"}`}>{c.verdict}</span> {c.text}
                    <span className="muted small"> · {c.why}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function OtherReport({ name, report }: { name: string; report: Record<string, unknown> }) {
  if (name.startsWith("router-")) {
    const variants = report.variants as Record<string, { model: string; effort: string; thinking: boolean; ms_p50: number; ms_p90: number; cost_usd: number; accuracy: Record<string, number> }>;
    return (
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Router measurements">
        <table className="data-table">
          <caption>Router: {String(report.cases)} labelled messages, each set-up once</caption>
          <thead>
            <tr>
              <th scope="col">Set-up</th>
              <th scope="col" className="num">p50</th>
              <th scope="col" className="num">p90</th>
              <th scope="col" className="num">Intent</th>
              <th scope="col" className="num">Sensitive</th>
              <th scope="col" className="num">Injections caught</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(variants).map(([key, v]) => (
              <tr key={key}>
                <th scope="row">
                  <code>{v.model}</code> {v.model.startsWith("claude-") ? (v.thinking ? "adaptive thinking" : "no thinking") : `${v.effort} reasoning`}
                </th>
                <td className="num">{seconds(v.ms_p50)}</td>
                <td className="num">{seconds(v.ms_p90)}</td>
                <td className="num">{percent(v.accuracy.intent)}</td>
                <td className="num">{percent(v.accuracy.sensitive)}</td>
                <td className="num">{percent(v.accuracy.injection_caught)}</td>
                <td className="num">{usd(v.cost_usd, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (name.startsWith("judge-")) {
    const setups = report.setups as { judge: { model: string; effort: string; prompt: string }; agreement: number; false_contradictions: number; cost_usd: number }[];
    return (
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Judge measurements">
        <table className="data-table">
          <caption>The judge: {String(report.set).split(":")[1] ?? ""}</caption>
          <thead>
            <tr>
              <th scope="col">Judge</th>
              <th scope="col" className="num">Agrees with the labels</th>
              <th scope="col" className="num">False contradictions</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {setups.map((j) => (
              <tr key={`${j.judge.model}-${j.judge.effort}-${j.judge.prompt}`}>
                <th scope="row">
                  <code>{j.judge.model}</code> {j.judge.effort} effort, <code>{j.judge.prompt}</code>
                </th>
                <td className="num">{percent(j.agreement)}</td>
                <td className="num">{j.false_contradictions}</td>
                <td className="num">{usd(j.cost_usd, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (name.startsWith("retrieval-")) {
    const results = report.results as Record<string, { document_recall: number; section_recall: number; mrr: number }>;
    return (
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Retrieval measurements">
        <table className="data-table">
          <caption>Retrieval: {String(report.questions)} questions, top {String(report.k)} passages</caption>
          <thead>
            <tr>
              <th scope="col">Search with</th>
              <th scope="col" className="num">Document found</th>
              <th scope="col" className="num">Section found</th>
              <th scope="col" className="num">MRR</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(results).map(([key, r]) => (
              <tr key={key}>
                <th scope="row">{key}</th>
                <td className="num">{percent(r.document_recall)}</td>
                <td className="num">{percent(r.section_recall)}</td>
                <td className="num">{r.mrr.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return null;
}

export function EvaluationsView() {
  const data = useQuery({ queryKey: ["evaluations"], queryFn: () => api.get<Evaluations>("/api/console/evaluations") });
  if (data.isLoading) return <p className="muted">Reading the reports…</p>;
  if (data.isError || !data.data) return <p className="field-error" role="alert">The reports didn't load.</p>;
  const e = data.data;
  return (
    <div className="lead-view">
      <div className="lead-head">
        <h1 className="console-h1">Evaluations</h1>
        <p className="muted small">
          The golden set is run against the real models before every release that changes the AI, and a judge grades each answer against criteria written beforehand. CI refuses a release unless a passing report covers its AI fingerprint
          {e.this_release ? (
            <>
              {" "}
              (this release: <code>{e.this_release}</code>)
            </>
          ) : null}
          . The server asks ten of the questions again, live, before every activation.
        </p>
      </div>
      <ol className="reports">
        {e.answers.map((r) => (
          <li key={r.name} className="report">
            <div className="report-head">
              <h2 className="console-h2">{r.label ? `Variant: ${r.label}` : "Golden set"}</h2>
              <span className="muted small">{when(r.run_on)}</span>
              <code className="small">{r.fingerprint}</code>
              {r.covers_this_release ? <span className="chip chip-ok">Covers this release</span> : null}
              <span className={`chip ${r.gate.passed ? "chip-ok" : "chip-danger"}`}>{r.gate.passed ? "Passes the gate" : "Fails the gate"}</span>
            </div>
            {r.gate.reasons.length ? (
              <ul className="gate-reasons">
                {r.gate.reasons.map((why) => (
                  <li key={why}>{why}</li>
                ))}
              </ul>
            ) : null}
            <dl className="metric-grid">
              {METRICS.map((m) => (
                <div key={m.key} className="metric">
                  <dt>{m.label}</dt>
                  <dd className="tabular">{m.format(r.metrics[m.key] ?? null)}</dd>
                  <dd className="muted small">{m.hint}</dd>
                </div>
              ))}
            </dl>
            <p className="muted small">
              {r.metrics.cases} questions{r.baseline ? ` · compared with ${r.baseline}` : ""}
              {r.judge_cost_usd != null ? ` · judging cost ${usd(r.judge_cost_usd, 3)}` : ""}
            </p>
            <Misses report={r} />
          </li>
        ))}
      </ol>
      <h2 className="console-h3">Other measurements</h2>
      {e.other.map((o) => (
        <section key={o.name} className="report" aria-label={o.name}>
          <OtherReport name={o.name} report={o.report} />
          <p className="muted small">
            {o.name}
            {o.run_on ? ` · ${when(o.run_on)}` : ""}
          </p>
        </section>
      ))}
    </div>
  );
}

// --- Prompts ---------------------------------------------------------------------------------------

export function PromptsView() {
  const data = useQuery({ queryKey: ["prompts"], queryFn: () => api.get<Prompts>("/api/console/prompts") });
  const [open, setOpen] = useState<string | null>(null);
  if (data.isLoading) return <p className="muted">Reading the prompts…</p>;
  if (data.isError || !data.data) return <p className="field-error" role="alert">The prompts didn't load.</p>;
  return (
    <div className="lead-view">
      <div className="lead-head">
        <h1 className="console-h1">Prompts</h1>
        <p className="muted small">Every prompt is a versioned file with an owner and a reason for each change. A version can't be edited once locked; a change is a new version, and the model call records which one it used.</p>
      </div>
      {data.data.prompts.map((p) => (
        <section key={p.id} className="report" aria-labelledby={`prompt-${p.id}`}>
          <h2 id={`prompt-${p.id}`} className="console-h2">
            <code>{p.id}</code>
          </h2>
          <ol className="versions">
            {p.versions.map((v) => {
              const key = `${p.id}@${v.version}`;
              return (
                <li key={key} className="version">
                  <div className="version-head">
                    <strong className="tabular">{v.version}</strong>
                    {v.live ? <span className="chip chip-ok">In use</span> : null}
                    <span className="muted small">
                      {v.changed_on} · {v.owner} · <code>{v.hash}</code>
                      {v.first_release ? (
                        <>
                          {" "}
                          · first live in release <code>{v.first_release}</code>
                        </>
                      ) : (
                        " · never live: replaced before a release used it"
                      )}
                    </span>
                  </div>
                  <p>{v.change}</p>
                  <button type="button" className="link-btn" aria-expanded={open === key} onClick={() => setOpen(open === key ? null : key)}>
                    {open === key ? "Hide the text" : "Show the text"}
                  </button>
                  {open === key ? (
                    <pre className="payload" tabIndex={0} aria-label={`The text of ${key}`}>
                      {v.body}
                    </pre>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}
