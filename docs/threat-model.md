# Threat model

What could go wrong with Kaskazi Bank's AI platform, who might make it go wrong, and what stops
them. It is organised around the [OWASP Top 10 for LLM Applications, 2025
edition](https://genai.owasp.org/llm-top-10/), read on 29 October 2026, followed by the risks
any web application carries. Each control names the file that implements it and the test or
evaluation that checks it. What is left over is said plainly.

Kaskazi runs on sample data, so no real customer can be harmed here.
The model is written as if the data were real, because the point is to show how a bank would
reason about it.

## The system in one picture

```
 visitor's browser ── Cloudflare ── nginx ──┬── static web app (no keys, no data)
                                            │
                                            └── API  (kaskazi-api, 127.0.0.1 only, no internet)
                                                 │  assistant, retrieval, tools, controls,
                                                 │  document desk, console, audit trail
                                                 ├── PostgreSQL (row-level security per visitor copy)
                                                 ├── pretend core-banking service (in-process, own API)
                                                 └── model gateway (kaskazi-gateway)
                                                       holds the provider keys, the spending caps,
                                                       the personal-data tripwire, the fallback
                                                       ├── api.anthropic.com   (primary)
                                                       └── api.openai.com      (fallback, embeddings)
```

**Trust boundaries**, where data crosses from less trusted to more trusted:

1. The visitor's browser to the API: everything a visitor types or uploads is untrusted.
2. The bank's documents, the core-banking service's answers and uploaded documents to the model:
   all of it is information for the model, never instructions.
3. The model's output to the customer and to the tools: the model proposes; code decides.
4. The API to the gateway, and the gateway to the providers: personal data must not cross the
   last one unmasked.

## What is worth protecting

| Asset | Why it matters |
|---|---|
| Customers' personal data (names, ID, phone and account numbers, documents) | the Data Protection Act's first concern, and the hardest harm to undo |
| Customers' money and accounts | a model that can move money is a model that can be talked into it |
| The truth of what the assistant says | a wrong fee or rate from a bank is a promise the bank must keep or retract |
| The provider keys and the spending budget | $40 a month funds the whole demonstration |
| The audit trail | the record a bank answers to when something goes wrong |
| Availability | one shared server, eight other sites |

## Who might cause harm

- **A curious or hostile visitor**, the most likely: trying to make the assistant leave its
  role, reveal its instructions, act on another customer's account, run up the bill or knock
  the site over.
- **The author of a document**: a payslip or statement can carry instructions written for the
  model or the reviewer, in visible text or hidden.
- **A compromised dependency or provider**: a poisoned package, or an incident at a model
  provider.
- **Ourselves**: a prompt change that makes answers worse, or a release that breaks a control.
  Most real incidents are this kind.

---

## LLM01 Prompt injection

**Here:** a customer's message, a passage from the bank's documents, a tool result, or the text of
an uploaded document tells the model to ignore its rules, reveal them, or act differently.

**Controls**
- Every message is normalised first (`kaskazi/controls/normalize.py`): zero-width, bidirectional
  and Unicode tag characters are removed and the text NFKC-normalised, so hidden or look-alike
  characters can't smuggle text past the checks. Messages are limited to 2,000 characters.
- A deterministic screen (`kaskazi/controls/injection.py`) looks for the shapes of an attempt:
  overrides in English and Swahili, requests for the hidden instructions, role-play framing,
  fake system or tool turns, payloads hidden in encodings.
- The router model gives its own verdict (`prompts/assistant.router`). A high risk from either
  gets a polite refusal and no main model call at all (`kaskazi/assistant/turn.py`, `_decide`).
- Passages, tool results and document text reach the model as data (`document` and
  `tool_result` blocks), and the system prompt says they are information, never instructions.
- Even a successful injection has little to work with: the tools act only for the signed-in
  customer, a question gets no write tools, and nothing changes without the customer's tap
  (LLM06).
- **Inside documents** (`kaskazi/documents/text.py`, `kaskazi/documents/checks.py`): text drawn in
  white or under 4 points is separated from what a reader sees, screened, and never extracted
  from. A planted instruction sends the document to a person with the reason shown. Extraction
  runs with no tools and a fixed output schema, so a document can't make the model do
  anything but fill in fields.

**Tested by**
- `tests/test_controls.py` (the screen and normalisation) and
  `tests/test_assistant_api.py::test_injection_and_off_topic_are_refused_without_a_model_answer`.
- The router evaluation (`evals/reports/router-2026-10-29.json`): the chosen set-up caught 5 of 5
  injection attempts, with no false alarm on the other 44 messages.
- The golden set's behaviour cases include four injection attempts (an override, fake markup
  claiming the customer is pre-approved, an "administrator" asking for another customer's
  balance, and an override in Swahili). The gate requires every behaviour case to pass.
- The document desk evaluation plants instructions in four documents; every report so far has
  caught all of them (`evals/reports/documents-*.json`).

**What is left:** no screen catches every phrasing. The design assumes some injection will get
through and limits what it can do, which is the part that matters.

## LLM02 Sensitive information disclosure

**Here:** personal data reaching a model provider, appearing where it shouldn't, or one visitor
seeing another's.

**Controls**
- **Masking before any model sees text** (`kaskazi/controls/pii.py`): Kenyan phone numbers,
  ID and passport numbers, KRA PINs, account numbers, card numbers (with a Luhn check), emails,
  dates of birth, the customer's own name and their saved recipients' names become typed
  placeholders (`[PHONE_1]`). The originals sit in a vault for that conversation, sealed with
  AES-256-GCM (`kaskazi/controls/vault_store.py`). A PIN, password or one-time code typed into
  the chat is hidden, and the customer is told never to share it.
- **Tool results are masked too**, and tools receive placeholders that are resolved on the
  server, inside that conversation only.
- **The gateway's tripwire** (`kaskazi/gateway/service.py`) rescans every outgoing payload and
  refuses the call if anything unmasked remains. The refusal is recorded.
- **Restoring:** in chat, a value comes back in a partly hidden form ("account ending 4821").
  Full values appear only where the customer must check them, on cards drawn by the server from
  the vault, never from model text.
- **Isolation:** each visitor gets their own copy of the sample data, and PostgreSQL row-level
  security keys every table to it (`kaskazi/db/migrations/0001_foundation.sql`). The API's
  database role can't see across copies even if the code asked it to.
- **The audit trail holds masked text only.** Copies, their vaults and their documents are
  deleted after a day without use.
- **OpenAI** is called with `store: false`. That stops responses being kept for later
  retrieval; OpenAI's retention of API traffic for abuse monitoring still applies.

**Tested by** `tests/test_pii.py`;
`tests/test_assistant_api.py::test_personal_data_never_reaches_a_model_and_comes_back_hidden`
(checks every payload the stand-in provider received);
`::test_the_audit_trail_is_intact_and_holds_no_raw_identifiers`;
`tests/test_isolation_and_audit.py` (one copy can't read another's rows).

**What is left**
- **Names typed freely** ("send it to John") are not detected: there is no name-recognition
  model on the server. Names the bank knows are caught.
- **Document photos are the stated exception.** An image can't have details hidden before a
  model reads it, so the desk asks the visitor to agree that an AI service will see it, and PDFs
  go by the text path instead. A bank would read images on its own premises or under a
  zero-retention agreement (see `docs/data-protection.md`).
- **Both providers process data outside Kenya.** Masking keeps identifiers out, but the content
  of a conversation is still personal data under the Act. `docs/data-protection.md` covers what
  a bank would need.

## LLM03 Supply chain

**Here:** a malicious or vulnerable Python or JavaScript package, a tampered build, a model that
changes underneath the application.

**Controls**
- Python dependencies are locked with hashes (`uv.lock`, installed with `uv sync --frozen`), the
  web app's with `pnpm-lock.yaml`.
- CI checks the Python dependencies a release ships for known vulnerabilities
  (`.github/workflows/release.yml`), and scans the whole history for secrets with gitleaks.
- The CI actions are pinned to commit SHAs, not tags.
- Every compiled module is imported on a baseline x86-64 CPU before a release is packed, and
  again on the server at activation, so a wheel the server can't run never goes live.
- **Models are pinned** by exact name in `kaskazi/llm/routes.toml`, and the release check
  confirms each one still exists before a release goes live (`kaskazi/release/check.py`).
- The web app is static, self-hosts its fonts, and loads nothing from third parties; the
  content security policy only allows its own origin (`deploy/security-headers.conf`).

**What is left:** a provider can change a model's behaviour behind the same name. The golden set
run before each release that changes the AI, and the ten questions asked live at every
activation, are how a change would show.

## LLM04 Data and model poisoning

**Here:** someone changes what the assistant knows, so it gives wrong answers to everyone.

**Controls**
- No model is trained or fine-tuned on anything here.
- The bank's documents are written in the repository (`corpus/documents`), reviewed like code,
  embedded at build time, and read-only at run time. **Nothing a visitor writes or uploads is
  ever added to them**, so one visitor can't poison another's answers.
- The corpus is part of the AI fingerprint: a change to any document needs a new golden-set run
  before CI will release it.
- Reviewers' corrections on the document desk are kept as labelled data. They are not fed back
  into anything automatically.

## LLM05 Improper output handling

**Here:** model output treated as trusted: rendered as HTML, used to build a query, or passed
straight to a system that acts on it.

**Controls**
- The web app renders answers through its own small formatter (`web/src/lib/richText.tsx`) into
  React elements. There is no `innerHTML` anywhere in the app, and the content security policy
  forbids inline scripts.
- Tool arguments are checked against strict schemas and then against the bank's rules in the
  core-banking service. The model names accounts by reference (`A1`), which the executor
  resolves only among the signed-in customer's own accounts (`kaskazi/tools/executor.py`).
- **Every answer is checked before the customer sees it** (`kaskazi/assistant/turn.py`, `_check`):
  each figure must appear in a passage, a tool result or the customer's own words
  (`kaskazi/controls/figures.py`); the system prompt's canary must be absent; nothing unmasked
  may appear. An answer that fails is regenerated once with a note; a second failure gets a safe
  answer and the offer of a person. No unchecked sentence reaches the screen.
- Confirmation cards, statements and quotes are drawn by the server from the bank's data, never
  from model text.

## LLM06 Excessive agency

**Here:** the assistant does something nobody asked for: moves money, blocks the wrong card, or
approves a loan.

**Controls**
- **Least privilege per message** (`kaskazi/tools/registry.py`, `tools_for`): a question gets
  read-only tools. Only a message the router reads as a task gets the tools that change things.
- **The model can't change anything.** A write tool only prepares a *pending action* holding the
  exact details (`kaskazi/assistant/actions.py`). The customer sees a confirmation card, and only
  their tap, from the same session, with the card's token, before it expires after 5 minutes,
  runs it. A token is used once. Unblocking a card also needs a one-time code, shown on the
  pretend phone.
- At most 4 tool rounds a turn.
- **A person decides every loan.** The document desk prepares an application and the reviewer
  approves or declines it; only an approval pays out. Product suggestions never suggest credit
  (`kaskazi/corebank/rules.py`).
- Handover to a person on request, for fraud, bereavement and money troubles, and when the
  assistant can't help.

**Tested by** `tests/test_assistant_api.py::test_a_change_waits_for_the_customer_and_runs_once`
(a wrong token is refused, a used one is refused, nothing changes before the tap); the golden
set's three confirmation cases (the gate requires a card every time);
`::test_a_loan_application_from_documents_to_a_persons_decision`.

## LLM07 System prompt leakage

**Here:** the assistant reveals its instructions, or the instructions hold something that must
not leak.

**Controls**
- The system prompt holds no secrets, keys, internal addresses or other customers' data. The
  prompts are versioned files in the repository (`prompts/`), so leaking one costs nothing.
- Requests for the instructions are caught by the screen and the router (LLM01).
- The system prompt carries a canary; an answer containing it is never shown
  (`kaskazi/assistant/turn.py`, `CANARY`).

## LLM08 Vector and embedding weaknesses

**Here:** retrieval returns what a user shouldn't see, or crafted content steers the search.

**Controls**
- **Audience filter** (`kaskazi/retrieval/store.py`): each document is marked for customers or
  staff, and a customer's search only ever sees customer passages. Staff procedures (credit
  decisions, document checks, complaint handling) are never retrieved for the assistant. The
  golden set's staff questions were moved out of the customer run for exactly this reason (see
  `evals/README.md`).
- The vectors are made at build time from the reviewed corpus and committed; at run time only
  the masked question is embedded. No visitor content enters the index (LLM04).
- If the embedding call fails, retrieval carries on with keyword search alone and the fallback
  is recorded.

## LLM09 Misinformation

**Here:** the assistant states a wrong fee, rate, limit or rule, confidently.

**Controls**
- Answers come from the bank's documents with citations the customer can open, or from the
  bank's records through the tools. The system prompt forbids figures from anywhere else and
  sends loan figures to the tools that apply the bank's rules exactly.
- The figures check (LLM05) refuses an answer with a figure from nowhere.
- Questions the documents can't answer get "I don't have that" and the offer of a person.
- The document desk's statutory checks recompute PAYE, NSSF, SHIF and the Housing Levy from a
  rules file that cites its sources and the date each was read
  (`kaskazi/documents/statutory.toml`).

**Measured** by the golden set before every release that changes the AI (`evals/README.md`): the
key fact stated, answers grounded in a citation or the bank's records, nothing contradicted,
unanswerable questions met without invention. CI refuses a release whose report fails the gate.
`docs/system-card.md` gives the latest figures.

**What is left:** the judge is a model too. It was calibrated against 56 hand-labelled criteria
(labelled in development review, not yet independently reviewed), and the run-to-run variation
of the assistant itself is measured by the consistency evaluation.

## LLM10 Unbounded consumption

**Here:** a visitor, or a bug, runs up the bill or exhausts the server.

**Controls**
- **Spending caps in the gateway** (`kaskazi/gateway/ledger.py`), so even a bug in the API can't
  overspend: $1.30 a day and $40 a month for the whole site, $0.04 a day per visitor, $0.12 a day
  per address. Before each call the gateway reserves the worst case (the prompt plus the
  route's `max_tokens`), so concurrent requests can't overshoot; afterwards it settles the
  actual cost. A capped visitor gets a friendly message and the quick actions, which use no AI.
- **Rate limits in nginx** (`deploy/nginx.conf`), keyed so the server itself is never limited:
  12 chat messages a minute and 6 uploads a minute per address, with burst allowances, and
  limits on pages, API calls and open event streams.
- At most 3 new visitor copies an hour and 10 a day per address.
- Ceilings in code: 2,000 characters a message, 4 tool rounds a turn, timeouts on every model
  call, 3 MB and 3 pages a document.
- **Files built to exhaust memory are refused.** An image over 16 megapixels is refused from its
  header, before it is decoded, and PDFs are read under limits sized for real payslips (2 MB per
  stream). Images are decoded one at a time and PDF text in a worker thread, so one slow file
  doesn't hold up other visitors. This was found while writing this document, and measured: a
  168 KB PNG holding a 12,000 by 12,000 pixel image passed the old checks with only a warning
  from the image library, and decoding it would take about 430 MB; three PDFs of 200 to 800 KB,
  built so their drawing instructions inflate to 70 MB a page, took the reader to between 328
  and 426 MB, and one of them took 46 seconds. The API may use 256 MB. Under the new limits the
  same PDF is refused at once, at 67 MB (`tests/test_documents.py`).
- The API and gateway units have hard memory limits and no swap (`deploy/kaskazi-*.service`), so
  a runaway process is stopped before it affects the other sites.

---

## Beyond the LLM list

### Sessions and requests
- A visitor's session is a signed value in an HttpOnly, SameSite=Strict, Secure cookie
  (`kaskazi/api/sessions.py`). Every write also needs a custom header that a cross-site form
  can't send.
- Staff sign in with one-click links to a role in their own copy; a staff session is signed,
  tied to that copy and role, and lasts 8 hours. There are no real staff accounts and no
  passwords anywhere.
- Addresses are never stored; a keyed hash is kept for the limits.

### The audit trail
- Append-only three ways (`kaskazi/db/migrations/0001_foundation.sql`): the app's role can only
  insert and select; triggers reject updates, deletes and truncation; and each event carries
  the hash of the one before it in the visitor's chain, which the console can verify.
- Expired copies are removed only by an owner-held function that deletes whole chains and leaves
  a count in a retention log.

### The server
- Each unit runs as its own system user with no shell, a read-only system, no capabilities, a
  filtered set of system calls, and write access only to its own data folder
  (`deploy/kaskazi-api@.service`, `deploy/kaskazi-gateway@.service`).
- The API can reach only localhost. Only the gateway, which holds the provider keys and nothing
  else, can reach the internet.
- Releases are blue/green: a release is checked on the idle slot (health, the main journeys,
  noindex, pinned models, ten golden questions asked live) before one nginx reload switches to
  it, and the switch goes back automatically if the checks fail through nginx afterwards
  (`deploy/activate.sh`). CI deploys with two restricted keys: one can only upload into the
  releases folder, the other can only run the activation script with a commit SHA.
- Secrets live in env files readable only by each unit's group, generated on the server and
  never printed.

### The gateway's way out, and why it is wider than it should be
The gateway's network fence (`IPAddressAllow` in `deploy/kaskazi-gateway@.service`) allows
Anthropic's published API addresses and one name server. OpenAI's API is served through
Cloudflare, so **Cloudflare's published ranges are allowed too, and they are broad: they let the
gateway reach any site Cloudflare fronts**, not only OpenAI. A compromised gateway could carry
data out to such a site, and any name server can carry data out in its queries.

**The stronger option is a forward proxy** with an allow-list of host names
(`api.anthropic.com`, `api.openai.com`) in front of the gateway, with the gateway's fence
narrowed to the proxy alone. A bank would do that, or reach the providers over private
connectivity. The clients already pin their hosts in code and refuse redirects, which narrows
what a compromised dependency inside the process could do, but doesn't replace the fence.

## What a bank would add before real customers

- The forward proxy above, and private connectivity to the providers where offered.
- Zero-retention and data-processing agreements with each provider, and a lawful basis for the
  transfer out of Kenya (`docs/data-protection.md`).
- On-premises reading of document images, or a provider contract that covers it.
- Name recognition for free-typed names.
- Real staff identity: single sign-on, roles from the bank's directory, and four-eyes approval
  for credit decisions above a limit.
- Independent review of the evaluation labels, and a red-team exercise against the assistant and
  the document desk.
- Alerting on the monitoring figures (fallback rate, refusals, spend), wired to an on-call rota
  (`docs/runbook.md`).
