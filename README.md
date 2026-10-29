# Kaskazi Bank

Kaskazi is an AI banking platform: a customer assistant, a document desk and a staff console,
with the controls, evaluations and monitoring a bank would expect around them.

**Live at [kaskazi.jeffgicharu.com](https://kaskazi.jeffgicharu.com).** It runs on sample data:
the bank, its products and its customers aren't real. The AI is real: Claude, with OpenAI as the
fallback.

![The customer assistant answering with a citation and a confirmation card](docs/screenshots/chat.png)

## What it does

**The customer assistant** answers questions about accounts, cards, loans, payments and fees
from the bank's own documents, and every answer cites the passages it used, which the customer
can open. It works on the customer's own accounts through tools on a pretend core-banking
service: balances, statements, card blocks, transfers, loan quotes, complaints, product
suggestions and the state of a loan application. Nothing that changes an account happens until
the customer taps a confirmation card. Fraud, bereavement and money troubles reach a person,
and so does anyone who asks.

**The document desk** reads payslips, ID cards and bank statements for a personal loan
application. Every field gets a confidence; the desk recomputes PAYE, NSSF, SHIF and the
Housing Levy from the published rates, checks the documents against each other and against the
bank's records, and finds instructions planted in a document, hidden or not. Anything doubtful
goes to a reviewer. A person decides every loan.

**The staff console** has the handover queue for agents, the document review queue for
reviewers, and, for the AI lead, the append-only audit trail of every turn, monitoring (usage,
reply times at p50 and p95, cost per feature and per day, error, fallback and handover rates,
ratings), feedback, every release's evaluation and the versioned prompts.

Each visitor gets their own copy of the sample data, and signs in as a sample customer, or as
a member of staff, with one click. There are no passwords and no real accounts anywhere.

## Measured

On sample data, for the release now live (`docs/system-card.md` has the full picture and the
limits):

| | |
|---|---|
| Golden set: the key fact stated, of 106 questions | **92.5%** |
| Answers citing a passage or resting on the bank's records | **96.8%** |
| Questions the documents can't answer, met without invention | **11 of 11** |
| Refusals, injection attempts, handovers and warnings as expected | **13 of 13** |
| Changes that waited for the customer's confirmation | **3 of 3** |
| The key fact stated every time, asked three times / asked three ways | **100% / 91.7%** |
| Document fields the desk called confident that were right | **266 of 266** |
| Planted faults and instructions caught | **11 of 11** |
| Reply time, median and 95th percentile | **5.3 s / 10.4 s** |
| Cost per question | **$0.00086** |

A release goes live only if committed reports for its exact prompts, models, tools, rules,
controls, documents and golden set pass the gate (`evals/README.md`).

### What the measuring found

The evaluations were built to stop bad releases, and they changed the product along the way:

- **The measurement itself was wrong once.** Until this release, the check for "changes waited
  for the customer's confirmation" passed on any card in the reply, so a balance card counted,
  and earlier reports showed 100% where the true figure was one case in three. The path that
  changes an account was never affected: only the customer's tap on a confirmation card runs a
  change. Tightening the check exposed a masking fault ("send 500 to Rose" couldn't be matched to
  the saved Rose Kiprop) and a golden case that asked for a payment the chat can't make. Both are
  fixed (`docs/evaluation-report.md`).
- **The consistency run caught a prompt regression the golden set passed.** A prompt meant to save
  a model round on statement questions (it did: three rounds became two) also made the assistant tell
  customers, two times in three, that it couldn't see their credit limit, without calling the
  tool that would have told it. The next prompt version kept the gain and fixed it.
- **"80k" wasn't a figure.** The check that refuses invented figures didn't read the shorthand
  customers write, so a correct answer about sending "80k" was replaced by a safe answer.
- **Two uploads could exhaust the server's memory.** Found while writing the threat model: a
  168 KB PNG holding a 12,000 by 12,000 pixel image, and a 600 KB PDF whose drawing instructions
  inflate to 70 MB a page, each took the API past its 256 MB limit. Both are refused now.

## How it is built

```
browser ── Cloudflare ── nginx ─┬─ static web app (React, TypeScript)
                                └─ API (FastAPI) ─┬─ PostgreSQL 16 + pgvector, row-level security per visitor
                                                  ├─ pretend core-banking service, behind its own API
                                                  └─ model gateway ── Anthropic (primary)
                                                     keys, caps,       OpenAI (fallback, search vectors)
                                                     fallback, PII
                                                     tripwire
```

| Part | Where |
|---|---|
| The model layer: one provider-neutral contract, an adapter per provider, routes and the circuit breaker | `kaskazi/llm` |
| The gateway: the only process with provider keys or internet access; spending caps, the personal-data tripwire, the call log | `kaskazi/gateway` |
| Retrieval: hybrid vector and keyword search, by audience | `kaskazi/retrieval` |
| Controls: masking and the encrypted vault, the injection screen, the figures check | `kaskazi/controls` |
| Tools, with risk levels, and the pretend core bank they call | `kaskazi/tools`, `kaskazi/corebank` |
| One assistant turn, confirmations and handovers | `kaskazi/assistant` |
| The document desk | `kaskazi/documents` |
| The audit trail | `kaskazi/audit`, `kaskazi/db/migrations` |
| Release checks, the AI fingerprint and the evaluation gate | `kaskazi/release` |
| Prompts, versioned, each with an owner and a change history | `prompts` |
| The bank's documents | `corpus/documents` |
| Evaluations and their reports | `evals` |
| Server files: the units, nginx, the activation script | `deploy` |

Models: Claude Haiku 5.5 routes each message, answers and reads documents; Claude Sonnet 5.5
grades the golden set; gpt-5.4-mini takes over if Anthropic fails. Each choice was measured
(`evals/README.md`, *What the measurements chose*).

### Controls

- Personal identifiers are masked before any model sees a message, and the gateway refuses any
  call that still carries one.
- Retrieved passages, tool results and document text reach the model as data, never as
  instructions; a deterministic screen and the router catch injection attempts first.
- A question gets read-only tools; a change only prepares a pending action that the customer
  confirms.
- Every figure in an answer must come from a passage, a tool result or the customer's own words,
  or the answer isn't shown.
- If the main provider fails, the other answers in the same turn; if both fail, the customer gets
  a safe answer and the AI-free quick actions.
- Spending is capped in the gateway per day, month, visitor and address.

`docs/threat-model.md` maps them to the OWASP Top 10 for LLM applications.

## Documents

- [`docs/system-card.md`](docs/system-card.md): what it does, how well, its limits and how it fails
- [`docs/threat-model.md`](docs/threat-model.md): threats and controls, mapped to the OWASP Top 10 for LLM applications
- [`docs/data-protection.md`](docs/data-protection.md): notes on Kenya's Data Protection Act, 2019 and the Central Bank of Kenya's expectations
- [`docs/runbook.md`](docs/runbook.md): what to do when something goes wrong
- [`docs/evaluation-report.md`](docs/evaluation-report.md): this release's measurements, every run before it, and what the measuring found
- [`evals/README.md`](evals/README.md): the evaluations, the gate, and what each measurement chose
- [`CONTRIBUTING.md`](CONTRIBUTING.md): running it locally, the checks, changing the AI

## Screenshots

Taken on the development stack with the real models and the sample data.

| | |
|---|---|
| ![The landing page](docs/screenshots/landing.png) | ![The landing page on a phone](docs/screenshots/landing-phone.png) |
| The landing page: one turn of the assistant, with the monsoon wind behind it (WebGL, on capable devices) | The same, on a phone |
| ![The document desk's result](docs/screenshots/desk.png) | ![A reviewer checking a payslip](docs/screenshots/review.png) |
| The document desk, after reading a payslip and an ID card | A reviewer's view, with the evidence for each field highlighted |
| ![Monitoring](docs/screenshots/monitoring.png) | ![Evaluations](docs/screenshots/evaluations.png) |
| The AI lead's monitoring, on the development stack after a day of evaluation runs (its cap is $3 a day; the live site's is $1.30) | Each release's evaluation: this one passes the gate, the run before it failed |

<img src="docs/screenshots/phone.png" alt="The assistant on a phone" width="320">

## Tests

- **147 Python tests:** unit, integration against a real PostgreSQL with row-level security, the
  assistant end to end with scripted models, the release journeys, the gate.
- **4 web unit tests**, and **9 browser journeys** run in Chromium, Firefox, WebKit and on a
  phone, with an axe accessibility check on every page.
- **The evaluations** above, run with the real models before every release that changes the AI.

CI runs all of it, plus ruff, mypy in strict mode, ESLint, a dependency audit, a secrets scan of
the whole history and an import check on the server's CPU, and deploys only from `main`.

## Cost

Building it, every evaluation run included, cost about $6.80 in model calls. Running the
demonstration is capped at $40 a month.

## Notes

No real bank's name, products or look is used, and nothing here is certified or endorsed by
anyone.
