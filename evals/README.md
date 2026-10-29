# Evaluations

Everything the assistant does that can be measured is measured here. Each measurement writes a
report to `evals/reports`, and every model, setting and threshold in the code cites the report
that chose it. The staff console shows them all under **AI lead → Evaluations**.

| What | Set | Command | Cost a run |
|---|---|---|---|
| The whole assistant, end to end | `golden/questions.jsonl`, 135 cases | `python -m evals.answers` | about $0.15 |
| The router | `router_cases.jsonl`, 49 messages | `python -m evals.router` | about $0.06 |
| Retrieval | the golden set's 98 document questions | `python -m evals.retrieval` | free (cached vectors) |
| The judge | `judge/calibration.jsonl`, 51 hand-labelled criteria | `python -m evals.judge_calibration measure` | under $0.01 |

Run them with `uv run` from the repository root, against the local stack with the real models
(`KASKAZI_DEV_REAL=1 scripts/dev.sh`).

## The golden set

135 cases, each asked as one of the three sample customers in a fresh conversation:

- **98 answerable questions** about the bank's products, fees and rules, written in the way
  customers write: short, misspelt, in English, Swahili and a mix of the two. Each has the
  document it was written from and the facts a good answer states. The first fact is the one
  the question turns on; the rest are details a thorough answer would add.
- **11 the documents can't answer** (mortgages, treasury bonds, phone wallets). The criteria are
  behaviour: say so, invent nothing, offer a person.
- **10 that need the bank's records** through the tools: balances, a loan worked out from the
  customer's own figures, and three changes that must wait for the customer's confirmation.
- **13 about behaviour**: off-topic requests, injection attempts in English, Swahili and fake
  markup, fraud, bereavement, money troubles, asking for a person, a PIN or card number typed
  into the chat, and a greeting.
- **3 follow-ups** that only make sense with the question before them.

Every figure in an expected fact was checked against the document the question came from
(one exception: a salary the customer states in the question).

## Grading

Each answer is graded twice.

**In code**, for what can be checked without reading: the outcome (answered, refused, handed
over), whether a document answer cites a passage, whether a change produced a confirmation
card, whether a warning appeared.

**By a judge** for the facts: Claude Haiku 5.5 with the `evals.judge` prompt reads the question,
the answer, the passages it cited and the records the bank's systems returned to it, and gives
each criterion one of *met*, *missed* or *contradicted*, with a sentence quoting what it relied
on.

A judge is only useful if it agrees with a careful reader, so it was calibrated. Criteria from
real answers were labelled by hand (`judge/calibration.jsonl`), chosen to include every answer
an earlier judge had called contradicted, and each set-up was measured against the labels
(`reports/judge-2026-10-29.json`):

| Judge | Criteria | Agrees | False "contradicted" | Cost |
|---|---|---|---|---|
| Haiku 5.5, medium effort, prompt 1.0.0 | 51 | 92.2% | 1 | $0.002 |
| Haiku 5.5, medium effort, prompt 1.1.0 | 51 | 98.0% | 0 | $0.002 |
| Haiku 5.5, high effort, prompt 1.1.0 | 51 | 100% | 0 | $0.004 |
| Sonnet 5.5, medium effort, prompt 1.1.0 | 51 | 100% | 0 | $0.036 |
| Haiku 5.5, medium effort, prompt 1.2.0 | 56 | 98.2% | 0 | $0.004 |
| **Haiku 5.5, high effort, prompt 1.2.0** | **56** | **100%** | **0** | $0.004 |

Prompt 1.1.0 shows the judge the cited passages (the first judge called a fee from the bank's
own documents "invented") and keeps *contradicted* for answers that say the opposite (three of
the first judge's four contradictions were answers that said nothing on the point). Prompt
1.2.0 also shows it the records the assistant read (it had called a mini-statement's real
transactions invented); five criteria from answers that use the records were added to measure
it. The set is small and each set-up was measured once: enough to choose between them, not to
quote a precise rate. The labels were written in development review and have not yet been
reviewed by a second person.

## The gate

`kaskazi.release.evalgate` decides whether a report allows a release. It recomputes the
metrics from the report's per-case results, then applies:

- **floors** every release must clear: the key fact stated in at least 85% of answers; at
  least 92% of answerable questions answered (fraud, bereavement and money troubles are
  answered and handed over, which counts); at least 90% of document answers citing a passage;
  at least 90% of unanswerable questions met without invention (saying the documents don't
  cover it, and passing the customer to a person, are both right); every refusal,
  handover and warning (100%); every change waiting for confirmation (100%); at most two
  contradicted criteria in the whole set; no errors.
- **no regression** against the last report that passed: the key fact may not fall by more
  than 5 points, all criteria together by more than 4, citing by more than 5, answering by more
  than 4, and contradictions may not rise by more than one.

The floors sit a little under what the assistant achieves, so ordinary variation between runs
doesn't fail a good release, while the regression rules catch a real step down. *All criteria*
is gated only against regression: the golden set lists every relevant detail, and a concise,
correct answer leaves some out.

A case where no model provider could be reached is asked again after 20 seconds; if it fails
again it counts as an error, and an error fails the gate. That is a fact about the network, not
about the answers, and the run should be repeated.

## Why the run happens before release, not in CI

The golden set calls the real models, so it needs provider keys. The keys live on the laptop
that builds the project and in the server's gateway, and nowhere else. A copy in the CI service
would add exposure for little gain.

So the run happens before a release, and its report is committed. Each report records the
**AI fingerprint** it covers (`kaskazi.release.fingerprint`): one hash over the prompts, the
model routes and their settings, the adapters, the tools and the core-banking rules, retrieval,
the controls, the assistant's turn, the bank's documents, and the golden set with its grader.
CI computes the fingerprint of the commit it is building (`python -m kaskazi.release gate`) and
refuses to release unless a committed report covers it and passes the gate. A change to the web
app or the server files leaves the fingerprint alone and needs no new run; a one-character
change to a prompt needs one.

Then, at every activation, the server asks ten of the golden questions again through the new
release before visitors reach it (`kaskazi.release.golden`), and checks in code what can be
checked without a judge.

## Measuring a variant

To try a different setting without committing it, start the gateway with another routes file and
label the run:

    KASKAZI_DEV_REAL=1 KASKAZI_GATEWAY_ROUTES=/path/to/routes.toml scripts/dev.sh
    uv run python -m evals.answers --routes /path/to/routes.toml --label answer-no-thinking

The report's fingerprint covers that file, so it can only ever count for a release that adopts
it word for word. A gateway started with another routes file says so on `/healthz`, and the
server's release check refuses it.

## What the measurements chose

| Setting | Choice | Evidence |
|---|---|---|
| Router | Haiku 5.5, low effort, no thinking | `router-2026-10-29.json`: as fast and as accurate as adaptive thinking on 49 labelled messages; gpt-5.4-mini cost 20 times as much |
| Retrieval | a first message searched by its own words for vectors and by the router's rewrite for keywords | `retrieval-2026-10-29.json`: document recall 0.969, against 0.929 for the rewrite alone |
| Answers | Haiku 5.5, low effort, adaptive thinking | `answers-2026-10-29-variant-answer-no-thinking.json`: without thinking, replies were a second faster at the median, but two of three changes produced no confirmation card and fewer answers cited a passage; it fails the gate |
| Judge | Haiku 5.5, high effort, prompt 1.2.0 | `judge-2026-10-29.json` |

The first full run of the golden set (`answers-2026-10-29-10831e846c63.json`) also changed the
product: customers describing a bereavement or money troubles had been handed to a person with
nothing else, though the documents answer their questions; they now get an answer and a person.
