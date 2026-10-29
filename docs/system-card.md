# System card

What Kaskazi Bank's AI does, how well it does it as measured, where it falls short, and how it
fails. Every figure here comes from a committed report in `evals/reports`, named next to it. They
were measured on 29 October 2026, on release fingerprint `8d86fcd13f54`, against sample data.

## What it is

Three things built on one platform, for an invented Kenyan bank:

- **The customer assistant** answers questions about the bank's products, fees and rules from
  the bank's own documents, with citations the customer can open. It does tasks through tools on
  a pretend core-banking service (balances, statements, card blocks, transfers, loan quotes,
  complaints, product suggestions, the state of a loan application), always waiting for the
  customer's confirmation before anything changes. It hands over to a person on request, for
  fraud, bereavement and money troubles, and when it can't help.
- **The document desk** reads payslips, national ID cards and bank statements, gives every field
  a confidence, checks the documents against the statutory rates, against each other and
  against the bank's records, and sends anything doubtful to a reviewer. It feeds a personal
  loan application; a person decides every loan.
- **The staff console** gives agents the handover queue, reviewers the document queue, and the
  AI lead the audit trail, monitoring, feedback, the evaluation reports and the prompts.

### Models

| Job | Model | Why (report) |
|---|---|---|
| Reading each message: intent, topic, injection risk, sensitive situations | Claude Haiku 5.5, low effort, no thinking | as accurate as adaptive thinking on 49 labelled messages, without its latency spikes (`router-2026-10-29.json`) |
| Answering, with tools | Claude Haiku 5.5, low effort, adaptive thinking | without thinking, two of three changes produced no confirmation card (`answers-2026-10-29-variant-answer-no-thinking.json`) |
| Reading documents, text and images | Claude Haiku 5.5, medium effort | Sonnet 5.5 read more of the hard images right, but only images that go to a person either way, at nine times the cost (`documents-2026-10-29-variant-image-claude-sonnet-5-5.json`) |
| Handover summaries | Claude Haiku 5.5, low effort | |
| Fallback for all of the above | gpt-5.4-mini | |
| Search vectors | text-embedding-3-small, 512 dimensions | |
| Grading the golden set (not part of the product) | Claude Sonnet 5.5, medium effort | agreed with all 56 hand labels; not the model that answers (`judge-2026-10-29.json`) |

Models are pinned by exact name in `kaskazi/llm/routes.toml`, and prompts are versioned files
with an owner and a change history (`prompts/`).

## Intended use

A demonstration of how a bank could build and run these things: the design, the controls and
the measurement. It is not for real customers or real documents. Kaskazi Bank isn't a bank,
and every product, price, customer and document in it is invented.

## How well it works

### The assistant, end to end

The golden set: 131 questions asked as the three sample customers in fresh conversations,
graded in code and by the judge (`answers-2026-10-29-8d86fcd13f54.json`). The release gate's
floors are in brackets.

| Measure | Result |
|---|---|
| The fact the question turns on, stated (107 questions) | **92.5%** (floor 85%) |
| Answerable questions answered, or answered and handed over | **100%** (floor 92%) |
| Document answers that cite a passage or rest on the bank's records | **96.8%** (floor 90%) |
| Questions the documents can't answer, met without invention (11) | **100%** (floor 90%) |
| Refusals, injection attempts, handovers and warnings, as expected (13) | **100%** (must be 100%) |
| Changes that produced a confirmation card (3) | **100%** (must be 100%) |
| Criteria the answers contradicted, of 358 | **1** (at most 2) |
| All criteria of the answered questions, the secondary details included | 76.7% met |
| Answers that came from the fallback provider | 0 |

*All criteria* is low by design: the golden set lists every detail a thorough answer could add,
and a concise, correct answer leaves some out. It is watched only for regression.

**The one contradiction:** asked for a copy of the data the bank holds, the assistant gave the
time limit but said it couldn't take the request in the chat, though the bank's privacy notice
says it can, and offered a person instead.

**Earlier reports overstated confirmations.** Until this release, the check behind "changes that
produced a confirmation card" passed on any card in the reply, so a balance card counted. Those
reports show 100%; the true figure was one case in three. The path that changes an account was
never affected: only the customer's tap on a confirmation card runs a change. The full account,
and the two faults the corrected check found, are in `docs/evaluation-report.md`.

**Run to run.** The last five full runs, on successive versions, put the key fact between 91.3%
and 94.3%: differences of that size are variation, not change, which is why the gate allows a
fall of 5 points before calling a regression.

**Reply time**, from the customer's message to the checked answer (the same run, against the
local stack on the development laptop): **median 5.3 seconds, 95th percentile 10.4 seconds**.
Questions that use the bank's records: median 6.1 s, 95th percentile 7.6 s. The behaviour cases
took a median 2.6 s: an off-topic message or an injection attempt is refused before any answer
is generated. Most of a reply is model time: the router (about 1.7 s), then one model round to
read the customer's records if needed, then the answer.

**Cost:** $0.00086 per question on average, the router, search vectors and answer included. The
whole run of 131 questions cost $0.11 to answer and $0.38 to judge.

### Consistency

The same question asked three times, and asked in three wordings, in fresh conversations
(`consistency-2026-10-29-8d86fcd13f54.json`):

| | Questions | Same outcome | Same verdict on the key fact | Key fact every time |
|---|---|---|---|---|
| Asked three times | 20 | 95% | 100% | **100%** |
| Asked three ways | 15 | 100% | 91.7% | **91.7%** |

The one change of outcome was a question the documents can't answer, answered twice and handed
over once: all three right. The consistency runs found two defects the golden set had passed: a
figures check that misread "80k", and a prompt that made the assistant say it couldn't see a
credit limit without calling the tool that would have told it.

### The document desk

34 generated documents: 10 payslips, 10 ID cards and 10 statements, 8 with a planted fault and 4
with a planted instruction, and 4 images made hard on purpose (`documents-2026-10-29-8d86fcd13f54.json`).

| Measure | Result |
|---|---|
| Fields the desk called confident (0.85 and over) that were right | **266 of 266** |
| Fields it called unsure (0.5 to 0.85) that were right | 88% of 25 |
| Fields it called doubtful (under 0.5) that were right | 3 of 6 |
| Fields read right, text path (PDFs) | 100% |
| Fields read right, image path | 94.8% |
| Planted faults and instructions caught | **11 of 11** |
| Clean documents sent to a person | 3 of 23, all three among the four hard images |
| Time to read a document (median) | 3.9 seconds |
| Cost of the whole run | $0.020 |

The confidence is what the desk rests on: a field it calls confident has been right every time
measured, and the ones it doubts are often wrong, which is why they go to a person.

### The parts

- **Router:** caught 5 of 5 injection attempts, raised no false alarm on 44 other messages, and
  read every sensitive situation right (`router-2026-10-29.json`).
- **Retrieval:** the right document among the top 8 passages for 96.9% of 98 document questions
  (`retrieval-2026-10-29.json`).
- **The judge:** agreed with 56 of 56 hand-labelled criteria (`judge-2026-10-29.json`). The labels
  were written in development review and have not yet been independently reviewed.

## Limits

- **Sample data only.** The bank, its documents, its customers and the documents on the desk are
  all invented, and generated documents are cleaner than real ones. Every figure above is an
  upper bound on what a bank would see with real customers; a bank would measure again on its
  own documents and conversations, starting from the reviewers' corrections, which the desk
  keeps as labelled data.
- **Small sets.** 131 questions, 34 documents and 56 judge labels are enough to choose between
  settings and catch regressions, not to quote a rate to the decimal.
- **English first.** The golden set includes Swahili and mixed questions and Swahili injection
  attempts, but most of it is English.
- **Names typed freely aren't masked.** The customer's own name and saved recipients' are; "send
  it to John" is not, because there is no name-recognition model on the server.
- **Document photos are seen by the AI service**, with the visitor's agreement. PDFs are read
  locally and only masked text is sent.
- **Totals the model works out itself are refused.** An answer may only state figures from the
  documents, the bank's records or the customer's words. Asked what withdrawing KES 15,000 at an
  agent costs, the assistant added the fee to the amount ("KES 15,120"); the figures check
  refused the total twice and the customer got a safe answer instead (golden case `pay-016` in
  `answers-2026-10-29-df6ec0555c34.json`; in the release's own run it answered without the total).
  Strict is the right side to err on for a bank, but it costs an answer now and then.
- **The fallback cites by passage ids.** OpenAI's API has no citations feature for supplied
  documents, so answers from the fallback cite passages by id, checked against the passages sent.

## How it fails

It fails towards safety, and it says so:

- **An answer that fails its checks** (a figure from nowhere, the system prompt's canary, an
  unmasked identifier) is regenerated once; if it fails again the customer gets a safe answer
  and the offer of a person. No unchecked sentence is shown.
- **A provider that fails or times out** is replaced by the other within the same turn; after
  three failures in a minute, the breaker skips it for a minute. If both fail, the customer gets
  a safe answer, the quick actions (which use no AI) and the offer of a person.
- **A document the desk isn't sure of** goes to a reviewer with the reason shown, never to a
  refusal.
- **A spending cap reached** gives a friendly message and the quick actions.
- **Every turn is recorded** in the append-only audit trail: what the model was given, what it
  said, which checks passed, what it cost and how long it took.

## Human oversight

- A person decides every loan; the AI prepares the file.
- Nothing changes an account without the customer's tap on a confirmation card.
- The customer can ask for a person at any time; fraud, bereavement and money troubles always
  reach one.
- The AI lead sees every turn, every rating and every release's evaluation, and can turn the AI
  off for the whole site with one setting (`docs/runbook.md`).

## Versions

Each release records an AI fingerprint over its prompts, model settings, tools, rules, controls,
retrieval, documents and golden set (`kaskazi/release/fingerprint.py`). CI releases only a
fingerprint that committed evaluation reports cover and pass, so every release live on the site
has a report like the ones above.

See also: `docs/threat-model.md`, `docs/data-protection.md`, `docs/runbook.md`,
`evals/README.md`.
