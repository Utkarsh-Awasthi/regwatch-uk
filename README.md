# RegWatch UK

Plain-English alerts when UK rules change for your business.

Small businesses, landlords, sole traders and charities miss rule changes because GOV.UK and legislation.gov.uk change constantly. RegWatch crawls both every night and works out which changes actually change meaning: thresholds, deadlines, duties, penalties, exemptions, where a rule applies. It ranks them against a minimal profile (e.g. *landlord · Plymouth · 2–5 properties*) and sends a weekly digest. Each item says what changed, why you're seeing it, the date in the source, and links every claim to the paragraph it came from.

> Not legal advice. RegWatch describes what public sources say. It never tells users what to do.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m regwatch demo          # offline: demo documents + 3 demo users + digests in data/outbox/
.venv/bin/python -m regwatch nightly       # live: crawl GOV.UK + legislation.gov.uk, diff, summarise
.venv/bin/python -m regwatch serve         # http://localhost:8000
.venv/bin/python -m regwatch digest        # send weekly digests (SMTP, or data/outbox/ without it)
.venv/bin/python -m regwatch evals         # offline evaluation → evals/report.md
.venv/bin/python -m pytest -q              # tests
```

The `demo` output prints each demo user's private `/me/<token>` link. Demo documents are clearly labelled `(demo)`, and their figures are illustrative, not current rules. Configuration is through environment variables; see `.env.example`. Scheduling is in `deploy/crontab`.

## How it works

```
 GOV.UK Search API ─┐                          ┌─ rank.py (profile × change → score, band, reasons)
 GOV.UK Content API ├─ sources/ ─ crawl.py ──┤                                       │
 legislation.gov.uk ┘   (paragraphs)  │       │  summarise.py (extractive | Claude) ── faithfulness.py gate
   Atom feeds + CLML XML               │       │                                       │
                         db.py: documents → versions (hash-deduped) → changes          policy.py (labels, refusals)
                                       │                                               │
                                  diff.py (semantic diff)                      digest.py → email / outbox
                                                                               web.py (sign-up, /me, /changes, /metrics)
```

| Module | What it does |
|---|---|
| `sources/govuk.py` | Incremental discovery via the Search API (`filter_public_timestamp`, guidance & regulation only, operational pages excluded). Full text via the Content API, including multi-part guides, national applicability, taxons and change notes. |
| `sources/legislation.py` | New SIs and Acts from the `/new` Atom feed. CLML XML parsed into provisions (`3(2)(a)`), plus Explanatory Notes, extent and coming-into-force. A watchlist (`watchlist.json`) re-diffs consolidated key Acts. |
| `text.py`, `entities.py` | HTML/XML to citable paragraphs. Extracts money, %, periods, dates, counts ("5 or more tenants"), deontic language (must / must not / exempt / offence) and nations. |
| `diff.py` | **Semantic diff.** Aligns paragraphs (moves are ignored and edits paired fuzzily), compares facts and duties per paragraph, classifies each change (`threshold_or_rate`, `deadline_or_period`, `new_obligation`, `removed_obligation`, `penalty`, `exemption`, `scope`, `negation`, `wording`, `cosmetic`) and scores materiality with a noisy-OR. GOV.UK's major/minor flag caps minor edits. |
| `rank.py` | Transparent relevance: hard geography filter, weighted sector vocabulary with anchor terms, GOV.UK taxon and legislation subject matches, own vs other council, profile attributes, per-user feedback damping. Every score carries human-readable reasons. |
| `summarise.py` | Extractive summariser (deterministic, faithful by construction). The optional Claude summariser uses structured output and server-side refusal fallback. Its output is used only if it passes the faithfulness gate; otherwise the extractive summary is used and the fallback is recorded. |
| `faithfulness.py` | Every claim must cite `new:pN`/`old:pN`. Every number, amount, period and date must appear in the cited text, in the right direction (old→new). Quotations must be verbatim. Content words need lexical support. No advice language. |
| `policy.py` | "Not legal advice" boundary, advice-phrase ban, refusal policy ("we can't tell whether this applies to you") for ambiguous cases and low-confidence bands. Consultations and Bills are labelled as proposals. |
| `digest.py` | Weekly digest per user. Accountants get one digest grouped by client profile (client profiles hold no names or emails). Signed links track opens, clicks and feedback ("this saved me", "not relevant", "summary was wrong"). |
| `users.py`, `metrics.py` | GDPR-minimal profiles, data export, hard erasure. Live metrics: open rate, CTR, user-rated precision by band, "saved me" stories, faithfulness and method mix. |

## Responsible AI

- **Not legal advice.** The disclaimer is shown on every page, in every email, and in the summariser's system prompt.
- **Cite or drop.** Uncited or unverifiable claims fail the gate. Failed LLM summaries fall back to verbatim source text.
- **Refusal policy.** When applicability depends on facts we don't hold or on interpretation ("reasonable", "where appropriate"), or the relevance signal is weak, the item says so and points to appropriate help instead of asserting "this affects you".
- **GDPR-minimal.** We hold an email, coarse sector tags, a nation, an optional council and banded attributes (allow-listed; anything else is dropped). No names, postcodes, addresses or financials. Users can self-serve export (`/me/<token>/export`) and hard deletion.
- **Safe links.** Tracking links are HMAC-signed and redirect only to the change's own source URL, so they can't be used as an open redirect.

## Evaluation

`python -m regwatch evals` writes `evals/report.md`. Latest run:

| Area | Result |
|---|---|
| Change detection (33 labelled pairs) | precision 1.0, recall 1.0, category accuracy 1.0 |
| Relevance (44 labelled profile × change pairs) | shown-at-all P 0.94 / R 1.0; "likely" band P 1.0 / R 1.0 |
| Faithfulness checker | 0% false alarms on faithful summaries; 100% detection of injected wrong amounts, shifted dates, wrong periods, advice, missing/bogus citations, invented claims and altered quotes |
| Policy | advice detector 5/5, 0 false alarms; refusal and proposal labelling verified |

**Caveat.** These fixtures are a small hand-labelled seed set, and the heuristics were tuned against them, so treat these numbers as an upper bound. The next step is to grow the fixtures from real `not_relevant` and `wrong_summary` feedback and from a labelled sample of live crawls. `--llm` adds Claude-summary gate pass rates and an LLM-judge entailment check (needs `ANTHROPIC_API_KEY`).

## Scaling to hundreds of thousands of pages

- **Incremental by design.** Nightly discovery asks only for pages whose `public_timestamp` moved since the stored cursor. Unchanged content is skipped by content hash, and only changed documents are diffed.
- **Versioned storage.** Every distinct version is kept, so any change can be re-diffed or re-summarised when the models improve.
- **Initial backfill.** Page through the Search API by document type, then use the nightly cursor. At the default 5 requests/s, about 300k pages take about 17 hours once. Parallelise per document type if needed.
- **Moving off SQLite.** SQLite (WAL) is fine for a single node. Move `db.py` to Postgres (the schema is portable) and run the crawl as a queue of fetch jobs when a single worker is no longer enough.
- **LLM costs.** These scale with *material changes per week*, not pages. Summaries are computed once per change and shared across all users.

## Roadmap

- Embedding-based paragraph alignment and an NLI model for cross-sentence meaning changes.
- A learned ranker trained on feedback events, with the current transparent scorer as its feature set and fallback.
- Local-authority pages (licensing schemes) as a third source, and an ONS local-authority code list to replace free-text council matching.
- Accountant CSV import of client profiles, and white-label digests.
