# The bank's documents

These are Kaskazi Bank's own documents: product terms, the tariff, guides, policies and staff
procedures. The assistant answers from them and cites them. Kaskazi Bank is invented, and every
document was written for this demonstration. None is taken or adapted from a real bank's text.

## Layout

- `documents/<id>.md`: one document per file.
- `embeddings/`: vectors for each chunk, keyed by the chunk's hash. They are written by
  `kaskazi corpus embed` and committed, so seeding the database and running the retrieval
  evaluation never call the embeddings API.

## Front matter

Every document starts with TOML front matter between `+++` lines:

```toml
+++
id = "debit-card-terms"            # the file name without .md
title = "Debit card terms and conditions"
audience = "customer"              # "customer", or "staff" for internal procedures
topics = ["cards"]                 # accounts, savings, cards, loans, payments, digital, agents,
                                   # complaints, security, privacy, onboarding, service, staff
version = "1.0"
effective = 2026-10-01
owner = "Cards and Payments"       # the team responsible for the document
+++
```

Documents marked `audience = "staff"` are never retrieved for customers. The retrieval layer
filters on it, and an evaluation case checks it.

## Writing rules

- **Figures:** every fee, rate, limit, period and amount comes from
  `kaskazi/corebank/data/bank.toml`, written the same way: `KES 1,000`, `15.5%` a year, `5 working
  days`. If a document needs a figure that file doesn't have, the figure is added there first.
- **Contact details:** no phone numbers, USSD codes, email addresses, web addresses or street
  addresses, apart from town names. Where a real document would give one, it says
  "(not available in this demonstration)".
- **Real organisations:** no real bank, product, card scheme or mobile money brand is named. Use
  generic terms: "mobile money wallet", "card network", "instant transfer to another bank".
- **Style:** plain English, second person, short paragraphs. One `#` title, then `##` and `###`
  sections. Each section is about one thing and can be read on its own, because retrieval
  returns sections, not whole documents. Tables are fine for fees and limits.
