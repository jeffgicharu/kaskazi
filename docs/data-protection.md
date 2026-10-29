# Data protection and supervisory notes

How the platform lines up with Kenya's Data Protection Act, 2019 and with the Central Bank of
Kenya's expectations of banks, and what a bank would still have to do. These are plain notes
written while building the platform. They are not legal advice, nothing here has been reviewed
by a lawyer, the regulator or an auditor, and the platform holds no certification of any kind.

Kaskazi Bank is invented, and the demonstration processes no real person's data: its customers
are sample data, and the site asks visitors not to upload real documents. The notes are written
as if a bank were running it for real customers.

Every source below was read from the regulator's own site on 29 October 2026. Quotes are
verbatim.

## Sources

| Short name | Document |
|---|---|
| the Act | [Data Protection Act, 2019 (No. 24 of 2019)](https://www.odpc.go.ke/wp-content/uploads/2024/02/TheDataProtectionAct__No24of2019.pdf), as published by the ODPC |
| General Regulations | [Data Protection (General) Regulations, 2021](https://www.odpc.go.ke/wp-content/uploads/2024/03/THE-DATA-PROTECTION-GENERAL-REGULATIONS-2021-1.pdf), Legal Notice 263 |
| Registration Regulations | [Data Protection (Registration of Data Controllers and Data Processors) Regulations, 2021](https://www.odpc.go.ke/wp-content/uploads/2024/03/THE-DATA-PROTECTION-REGISTRATION-OF-DATA-CONTROLLERS-AND-DATA-PROCESSORS-REGULATIONS-2021.pdf), Legal Notice 265 |
| ODPC transfers note | [ODPC Guidance Notes for Cross-border Data Transfers](https://www.odpc.go.ke/wp-content/uploads/2026/09/ODPC-%E2%80%93-Guidance-Notes-for-Cross-border-Data-Transfers.pdf) (2026) |
| ODPC AI note | [ODPC Guidance Note on Artificial Intelligence](https://www.odpc.go.ke/wp-content/uploads/2026/07/AI-Guidance-Note.July-2026.pdf), July 2026, published with a template for comments and not yet on the ODPC's list of final guidance, so treated here as a draft |
| CBK cybersecurity note | [CBK Guidance Note on Cybersecurity for the Banking Sector](https://www.centralbank.go.ke/uploads/banking_circulars/634077191_GUIDANCE%20NOTE%20ON%20CYBERSECURITY%20FOR%20THE%20BANKING%20SECTOR.pdf), August 2017 |
| CBK/PG/16 | Guideline on Outsourcing, CBK/PG/16, in the [CBK Prudential Guidelines](https://www.centralbank.go.ke/wp-content/uploads/2016/08/PRUDENTIAL-GUIDELINES.pdf), January 2013 |
| CBK AI survey | [CBK Survey on Artificial Intelligence in the Banking Sector](https://www.centralbank.go.ke/uploads/banking_sector_reports/595566510_Survey%20on%20Artificial%20Intelligence%20in%20the%20Banking%20Sector.pdf) (data at 31 December 2024) |
| CBK AI draft | Draft Guidance Note on Artificial Intelligence in the Banking Sector, August 2026, one of the drafts in CBK's [public notice of 10 September 2026](https://www.centralbank.go.ke/wp-content/uploads/2026/09/Public-Notice-Invitation-for-Comments-from-the-Public-on-the-Draft-Revised-Prudential-Guidelines-Risk-Management-Guidelines-Guidance-Notes-and-the-Domestic-Systemically-Important-Banks-.pdf) (comments due 7 November 2026). A draft: it may change, and it states no date from which it applies. |

## Who is who

- **The bank is the data controller.** It would have to be registered with the Data Commissioner
  (the Act, s.18(1)). The Registration Regulations' small-business exemption doesn't apply to
  "provision of financial services" (reg. 13 and the Third Schedule, item 8), so a bank must
  register whatever its size.
- **The model providers are data processors**: Anthropic (the main model) and OpenAI (the
  fallback model and the search vectors). Each needs a written contract under which it acts
  only on the bank's instructions (s.42(2); General Regulations, regs. 24 and 25, which also
  require the bank's authorisation for sub-processors). The ODPC AI note (§7.8) sets out what
  such a contract should cover for an AI vendor, and (§7.1) asks the bank to check that the
  vendor is registered.
- **The demonstration** has no contract with either provider beyond their standard API terms.
  That is one of the reasons it must never hold real customers' data.

## The principles, and how the design meets them

| The Act | What it asks | Here |
|---|---|---|
| s.25(d) minimisation | only what is needed | Identifiers are masked before any model sees a message (`kaskazi/controls/pii.py`): the model works with `[ACCOUNT_1]`, not the number. Tool results are masked too. The text path reads a PDF locally and sends only masked text. |
| s.25(f) storage limitation | kept no longer than needed | A visitor's copy, its vault of masked values and its documents are deleted after a day without use. A bank would set retention by record type: the audit trail would be kept for as long as its record-keeping duties require. |
| s.25(h) transfers | "not transferred outside Kenya, unless there is proof of adequate data protection safeguards or consent from the data subject" | See *Data leaving Kenya* below. |
| s.41 by design and by default | measures chosen with the processing, including "pseudonymisation and encryption" (s.41(4)(c)) | Placeholders are pseudonymisation. The vault is encrypted with AES-256-GCM per conversation. Row-level security keeps each copy's data apart. The gateway refuses any model call that still carries an unmasked identifier. |
| s.26 rights | access, correction, deletion, objection | The invented bank's privacy notice (`corpus/documents/privacy-notice.md`) explains them. A bank would wire the assistant's requests into its existing rights process; the audit trail shows exactly what was processed for each conversation. |
| s.29 duty to notify | tell people, before collecting, who receives their data "including details of safeguards adopted" | The privacy notice names AI services as service providers, says some may process data outside Kenya, and explains the masking. The chat introduces itself as an AI assistant. A bank would name the providers and the safeguards. |

### The lawful basis (s.30)

| Processing | Basis a bank would rely on |
|---|---|
| Answering a customer's questions and doing what they ask (balances, card blocks, transfers) | performing the contract with the customer (s.30(1)(b)(i)) |
| Reading payslips, IDs and statements for a loan application | steps the customer asked for before entering a contract (s.30(1)(b)(i)) |
| An AI service reading a **photo** of a document | the customer's consent, asked each time on the desk; a PDF goes by the masked text path instead, so consent is never the only way in |
| Passing a fraud report to a person | the contract, and the bank's own legal obligations |
| The audit trail and the evaluation of answers | legitimate interests and record-keeping obligations; a bank would document the balancing test |

## Decisions about people (s.35)

The Act gives a person "a right not to be subject to a decision based solely on automated
processing, including profiling, which produces legal effects concerning or significantly
affects the data subject" (s.35(1)). The General Regulations add that the person must be able to
"obtain human intervention and express their point of view" (reg. 22). The ODPC AI note (§6.6
and §9) treats credit decisions as the leading case.

The platform makes no such decision:

- **A person decides every loan.** The document desk reads the documents and runs the checks;
  a reviewer sees the fields, the evidence highlighted on the page and every check's result, and
  approves or declines. Only an approval pays out (`kaskazi/documents/desk.py`).
- The assistant can work out an indicative eligibility or a quote, always labelled indicative,
  and can never approve or decline anything. The system prompt forbids it to promise anything a
  person would have to decide.
- A low confidence or a failed check sends a document to a person, never to a refusal.
- The customer can ask for a person at any point in a chat.
- Explanations are built in: a product suggestion says which rule fired and why ("Why am I
  seeing this?"), an eligibility result shows the figures it used, and every answer shows its
  sources.

A bank that later let a model decline credit on its own would need one of s.35(2)'s exceptions
and its safeguards, a DPIA, and the bias testing reg. 22 asks for.

## Impact assessment (s.31)

A data protection impact assessment is required "prior to the processing" where processing "is
likely to result in high risk" (s.31(1)), and the report "shall be submitted sixty days prior to
the processing of data" (s.31(5)). The General Regulations (reg. 49) list, among high-risk
processing, automated decision-making, sensitive data, large-scale processing and innovative
technology. A bank deploying this assistant to its customers would need one, with the
threat model (`docs/threat-model.md`) and the system card (`docs/system-card.md`) as inputs.

## Data leaving Kenya (ss.48 to 50)

Both model providers process data outside Kenya. The ODPC transfers note is explicit that
processing in the cloud on servers outside Kenya "constitutes a cross-border data transfer".

- **What leaves:** masked text. Identifiers stay in Kenya, in the vault, but the rest of a
  conversation (what the customer asked, their balances and transactions as the tools returned
  them) is still personal data, and it leaves. A document photo leaves whole.
- **The conditions:** a transfer needs proof of appropriate safeguards given to the Data
  Commissioner, or one of the necessities in s.48(c). Each transfer made on safeguards must be
  documented (General Regulations, reg. 41).
- **Sensitive data:** "The processing of sensitive personal data out of Kenya shall only be
  effected upon obtaining consent of a data subject and on obtaining confirmation of appropriate
  safeguards" (s.49(1)). The Act's definition of sensitive personal data includes property
  details, marital status and family details, which payslips and statements can carry. The desk
  asks for consent before an AI service reads a photo; a bank would also need the safeguards
  confirmed, in the contract and the transfer documentation, as the ODPC transfers note
  requires.
- **What a bank would do:** a data-processing agreement with each provider including the ODPC's
  standard clauses, zero data retention where the provider offers it, the transfer documented,
  and document images read on its own premises or in-country.

## Breaches (s.43)

The bank must "notify the Data Commissioner without delay, within seventy-two hours of becoming
aware of such breach" (s.43(1)(a)), and a processor must tell the bank "within forty-eight hours"
(s.43(3)). `docs/runbook.md` covers what the platform's own records show in a suspected breach
and the order of the first steps. A bank would also have to notify CBK (below).

## The Central Bank of Kenya

### In force today

- **CBK cybersecurity note (2017).** Board oversight of cyber risk, reviewed at least once in 12
  months; a CISO in senior management; third-party and cloud outsourcing managed with due
  diligence, contracts, monitoring and exit plans (§3.3); significant incidents notified to CBK
  "within 24 hours". The model providers are third parties in exactly this sense.
- **CBK/PG/16, outsourcing (2013).** Material outsourcing needs CBK's approval (§4.1.1), and IT
  systems, data processing and document processing are named as material (§4.1.4). Some functions
  can't be outsourced at all, among them the "decision to grant loans" (§4.1.2): the platform
  keeps that decision with a person at the bank. Offshore arrangements must allow CBK and the
  auditors access (§4.7). The draft revision in the September 2026 consultation renames it a
  guideline on third-party service providers and names cloud and software services as material.

### The draft AI guidance note (August 2026, not final)

CBK's survey of AI in banking (data at the end of 2024) found that half the institutions that
responded had adopted AI and that 93% wanted CBK to issue guidance. The draft guidance note in the
September 2026 consultation is that guidance. As drafted, it would ask, among other things:

| The draft | What the platform has, and what a bank would add |
|---|---|
| CBK's approval "prior to deployment of AI systems", with a checklist (§4.7(d)) | The checklist would draw on the threat model, the system card and the evaluation reports, which already exist for each release. |
| The board holds "primary responsibility" (§2.3) | A governance matter for the bank. |
| Agentic AI that can execute transactions is "a high-risk AI system", with authority limits, audit logs and the ability to override or suspend (§4.11.1) | The assistant prepares transactions but can't execute one: only the customer's tap on a confirmation card does (`kaskazi/assistant/actions.py`). Tools are offered by intent, and the audit trail records every call. One setting in the gateway (`KASKAZI_AI_ENABLED`) turns the AI off for the whole site, leaving the quick actions working (`docs/runbook.md`). A bank would still classify the assistant as high-risk, because it prepares transactions. |
| Material decisions "not based solely on unverified outputs" (§4.10); human review of critical decisions, credit denial among them (§4.15.2) | A person decides every loan; every answer is checked before it is shown. |
| Disclosure of AI "at the initial point of interaction", and customers told of each update as a new version (§4.15.1) | The chat opens by introducing itself as an AI assistant, and every page carries the demonstration banner. Every prompt and model setting is versioned and fingerprinted per release, so a bank could tell customers which version answered them. |
| An AI register, and notice to CBK "within 24 hours of any AI incident" (Part V) | The monitoring view and the audit trail give the facts such a notice needs. The register and the reporting would be the bank's. |

## What this platform cannot show

- That a bank's actual contracts, registrations, DPIA or CBK approvals exist. They would be the
  bank's.
- How it would perform on real customers' questions and real documents: every measurement here
  is on sample data (`docs/system-card.md`).
