# Runbook

What to do when something goes wrong with Kaskazi Bank's platform, and the routine work that
keeps it right. It is written for whoever is on call. On the demonstration's shared server,
anything that needs root (restarting a unit, changing an env file, running the activation script
by hand) goes through the server's administrator; a bank would give its on-call engineers those
rights through its own change process.

## Where to look

| What | Where |
|---|---|
| Is it up, and which release? | `https://kaskazi.jeffgicharu.com/api/healthz` returns `{"ok": true, "release": "<sha>"}`; every response carries `x-release` |
| Reply times (p50 and p95), outcomes, handover and fallback rates, failed model calls, cost by feature and day, the caps and what's spent, the breakers | console, **AI lead → Monitoring** |
| One conversation, step by step: the masked input, what was retrieved, the prompt version, each model call with its tokens, cost and time, each tool call and result, every check's decision | console, **AI lead → Audit trail**, which checks its hash chain as it opens |
| Ratings and the answers they rate | console, **AI lead → Feedback** |
| What each release was measured at | console, **AI lead → Evaluations**, and `evals/reports` |
| The services | `journalctl -u kaskazi-api@blue -u kaskazi-gateway@blue` (or `@green`); `systemctl status 'kaskazi-*'` |
| The web server | `/var/log/nginx/kaskazi.access.log` and `kaskazi.error.log` |
| A model call refused for unmasked personal data, or by a cap | **Monitoring → Stopped at the gateway**, the `gateway.refusals` table, and the turn's audit events |

Blue is API 8800 with gateway 8802; green is API 8801 with gateway 8803. Only one slot is live;
the other holds the release before, or nothing.

---

## The site shows "unavailable"

nginx shows its own unavailable page (with `Retry-After`) when the live API doesn't answer.

1. `curl -s https://kaskazi.jeffgicharu.com/api/healthz`. A 503 means the API isn't answering.
2. `systemctl status 'kaskazi-*'`. Each unit restarts itself 3 seconds after a failure. A unit
   cycling over and over usually shows why in the first lines of `journalctl -u <unit> -n 80`.
3. **Killed for memory?** The journal says `oom-kill` or `Memory limit reached`. The API is held
   at 220 MB and stopped at 256 MB, the gateway at 130 and 160 MB. Find what it was doing:
   the last requests in the access log, and the last audit events. Uploads are the usual
   suspect; the document desk refuses images over 16 megapixels and reads PDFs under tight
   limits, so a new way past those is a defect to fix, with a test.
4. **The database?** `journalctl` shows connection errors. Check PostgreSQL is running and that the
   kaskazi roles haven't reached their connection limits.
5. If the live release itself is broken, **go back to the release before** (below).

## A release didn't go live

The activation script only switches nginx after the new release passes its checks on the idle
slot, and switches back by itself if the checks then fail through nginx. Visitors keep the
release before throughout. The CI job's log, and the activation's journal, name the check that
failed:

| Check | Usual cause |
|---|---|
| a compiled module doesn't import | a dependency built for a newer CPU than the server's |
| `pinned_models_exist` | a provider retired a model named in `kaskazi/llm/routes.toml` |
| `ten_golden_questions` | a question went wrong twice; the log names it and shows the answer |
| the journeys | a front-end or API defect the tests missed |
| name resolution in the gateway | the server's resolver changed; the gateway's network fence names it (see `deploy/kaskazi-gateway@.service`) |

Fix it on a branch, and push again. Nothing needs undoing on the server.

## Going back to the release before

The server keeps the live release and the one before it. Run the activation script with the
earlier release's 40-character sha (`sudo /usr/local/bin/kaskazi-activate <sha>`, or re-run that
commit's CI job). It goes through the same checks as any release.

Migrations only ever add, so the release before always runs against the newer database.

## A model provider is failing

The fallback is automatic. After 3 failures in 60 seconds the breaker sends calls straight to
OpenAI for 60 seconds, then tries Anthropic again. If both fail, customers get a safe answer, the
quick actions and the offer of a person.

1. **Monitoring → Model calls, last 24 hours**: which route and provider are failing, and how.
2. Check the provider's status page.
3. If the fallback rate stays high for more than an hour, say so on the AI lead's channel: the
   fallback answers well (the golden set measures it) but costs more, and cites by passage ids
   rather than the citations feature.
4. Nothing needs restarting: the breaker recovers by itself.

## Turning the AI off

For a provider incident, an injection that got through, or any reason to stop the models at
once: set `KASKAZI_AI_ENABLED=0` in the gateway's env file and restart the live gateway. Every
model call is then refused, customers see the friendly message and the quick actions (balances,
statements, card blocks), which use no AI, and the console keeps working. Set it back to `1` and
restart to turn the AI on again.

## Spending

The gateway enforces the caps itself: $1.30 a day and $40 a month for the site, $0.04 a day per
visitor, $0.12 a day per address. It reserves the worst case before each call, so it can't
overshoot.

- **The site's daily cap reached:** visitors see "Today's demo allowance is used up" until
  midnight Nairobi time. Check **Monitoring → Cost by feature**: a normal day is a few cents. A
  spike in one feature, or from one address, is either a busy day or someone working the caps;
  the access log shows which.
- **The month's cap is close:** decide whether to raise it. Raising a cap is an env file change
  and a gateway restart.
- The release checks spend a little at every activation (ten golden questions, a few cents),
  and it is included in the cost table.

## An answer was wrong

A rating with a comment, a complaint, or a reviewer's report that the assistant stated something
false.

1. Find the conversation in the **Audit trail**. The events show exactly what the model was given
   (the retrieved passages, the tool results) and what it said.
2. Decide which it was:
   - **the documents are wrong or missing** a fact: fix `corpus/documents`, which changes the AI
     fingerprint, so the golden set must be run again before CI will release it;
   - **the model ignored or misread** what it was given: add the question to the golden set
     with its expected facts, then change the prompt (below) and measure;
   - **a check let it through**, such as a figure the figures check should have caught: fix the
     check, with a test.
3. If the wrong answer could have cost a customer money, treat it as an incident: tell the
   customer and correct the record.

## A suspected injection, or data where it shouldn't be

1. Find the conversation in the **Audit trail**; its chain check ("Chain intact") confirms the
   events haven't been altered.
2. Look at the `screen` event (the deterministic screen's and the router's verdicts), every
   `tool_call`, and the `output` event's checks.
3. **If the assistant did something it shouldn't have**, remember that nothing changes an
   account without the customer's tap on a confirmation card: look for an `action_confirmed` event. If one
   exists without the customer meaning it, that is a serious incident.
4. **If unmasked personal data reached a provider**, the gateway should have refused it; look in
   `gateway.refusals`. A payload that got past both the masking and the tripwire is a breach to
   assess (below).
5. Turn the AI off if the problem can repeat before it is fixed.
6. Add the case to the router set, the golden set or the tests, so it is caught from then on.

## A suspected personal data breach

The demonstration holds only sample data. For a bank, the clocks start when the bank becomes
aware:

- **the Data Commissioner within 72 hours** (Data Protection Act, s.43(1)(a)); a processor must
  tell the bank within 48 hours (s.43(3));
- **CBK within 24 hours** of a significant cyber incident (CBK Guidance Note on Cybersecurity,
  2017), and, under the draft AI guidance note, within 24 hours of an AI incident.

The first hour: contain (turn the AI off, or take the site down, if the leak continues);
preserve the audit trail and the logs; establish what data, whose, since when, and through which
path; then hand over to the bank's incident and data protection officers.
`docs/data-protection.md` has the sources.

## Changing a prompt or a model

1. **A prompt:** add a new version file under `prompts/<name>/`, never edit a locked one, with
   the owner, the date and what changed and why in its header. Run
   `uv run python -m kaskazi.prompts.lock`. CI refuses a prompt whose text changed without a new
   version.
2. **A model or a setting:** change `kaskazi/llm/routes.toml`. To measure it first without
   committing, run a labelled variant (`evals/README.md`, *Measuring a variant*).
3. Either changes the AI fingerprint. Run the evaluations on the committed tree with the real
   models, against the local stack:

       KASKAZI_DEV_REAL=1 scripts/dev.sh
       uv run python -m evals.documents
       uv run python -m evals.answers

   Commit the reports. CI refuses the release unless reports covering the new fingerprint pass
   their gates, and compares the golden set with the last passing run graded by the same judge.
4. Read the misses on **Evaluations** before releasing, not only the gate's verdict.

## The evaluation gate refused a release

CI names the metric and the floor, or the regression. Open the report's misses on the
**Evaluations** page. A case asked again after a timeout is counted in the report; a run where
the network at the runner kept failing is better re-run than argued with. A real regression means
the change isn't ready.

## Routine

- **Daily:** a look at Monitoring (fallback rate, failed calls, spend) and the handover and review
  queues.
- **With each release that changes the AI:** the evaluations above, and the consistency run
  (`uv run python -m evals.consistency`) when the prompts or models change.
- **When a statutory rate changes** (PAYE, NSSF, SHIF, the Housing Levy): update
  `kaskazi/documents/statutory.toml` with the source and the date it was read; the document
  desk's checks use it from that date.
- **When a provider publishes new API addresses:** update the gateway's network fence, with the
  date read.
- **Keys:** rotate a provider key at the provider, write the new one into the gateway's env file
  without printing it, and restart the gateway.
