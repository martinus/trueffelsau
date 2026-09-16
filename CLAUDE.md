# Idea Radar — project spec

This file tells an AI agent and a human what this project is, what it must
do, and how to work on it. Keep it short. Update it when a decision changes.

## 1. Purpose

Find product ideas that have a high chance of success, every week, with
little manual work.

The tool collects signals from public sources, classifies them with an LLM,
scores them against a fixed filter, and writes a weekly digest. A human reads
the digest and decides. The tool records the decision and the outcome, so the
filter gets better over time.

## 2. Goals

- Run once a week without manual steps.
- Show the top 10 candidates with the evidence behind each score.
- Show a kill list: candidates that lost their window since last week.
- Keep every score auditable: a human can see the quotes that produced it.
- Keep a history so scores can be recomputed when weights change.

## 3. Non-goals

- Do not build products. This tool finds candidates only.
- Do not predict revenue. Report evidence, not forecasts.
- Do not collect personal data about individuals.
- Do not scrape sites that forbid it. See section 12.

## 4. The filter (acceptance criteria for a candidate)

A candidate scores high when most of these are true. A candidate scores near
zero when point 6 fails.

1. Users face a forced decision or a forced migration, and money is attached.
2. The buyer is a business, a team, or a developer. Consumers count only when
   the value per user is high.
3. The incumbent's moat is code or bad UX. It is not a network, a data set,
   or search rankings.
4. The heavy lifting exists as open source (for example ffmpeg, FreeRDP,
   MuPDF). The product is the front door.
5. The event is fresh. The window closes in weeks, not months.
6. No clone exists yet, or the only clones are stale. One clone with more than
   1,000 stars in the last month closes the window.
7. The idea fits the owner: native code, performance, Linux, C++ or Rust, or
   Austrian rules the owner can learn. UI-heavy consumer apps score down.

Weights live in `config/weights.toml`. Change weights there, not in code.

## 5. Signal types

| Type              | Meaning                                                    |
|-------------------|------------------------------------------------------------|
| `price`           | A price increase or a new paywall                          |
| `forced_account`  | A product now requires a login or a cloud account          |
| `license`         | A license change that restricts use                        |
| `shutdown`        | A product or feature is discontinued                       |
| `acquisition`     | A company is acquired, especially by private equity        |
| `api_lockdown`    | An API is closed, priced, or rate-limited                  |
| `listing`         | A business is for sale on a marketplace                    |
| `pain`            | Repeated complaints with no single event                   |

## 6. Sources and collectors

Each collector writes normalized events to the database. One collector per
source. A collector must be safe to rerun: it must not create duplicates.

| Source                     | Access                          | Notes                                           |
|----------------------------|---------------------------------|-------------------------------------------------|
| Hacker News                | Algolia API (`hn.algolia.com/api`) | Query terms in `config/hn_terms.txt`         |
| Reddit                     | Arctic Shift API                | Subreddits in `config/subreddits.txt`; sleep 1 s between calls |
| Mabya, Projektify          | Direct fetch of listing pages   | German marketplaces; small; fetch weekly        |
| Flippa                     | Apify scraper                   | Price, revenue, profit, multiple, category, age |
| Acquire.com                | Manual CSV export               | Needs login; drop the file in `inbox/`          |
| GitHub                     | REST API                        | Search for existing clones; record stars, age, last commit |
| AlternativeTo              | Fetch of "alternatives to X" pages | Confirms demand                              |
| Pricing pages, changelogs  | Weekly diff of a watchlist      | URLs in `config/watchlist.txt`                  |
| Acquisition news           | Web search, weekly              | Names in `config/pe_firms.txt`                  |
| Austrian forums            | Direct fetch                    | broker-test.at community, r/Austria via Arctic Shift |

Do not add Google Trends unless a stable API exists. `pytrends` is unofficial
and breaks often.

## 7. Data model (SQLite, `data/radar.db`)

- `events`: id, source, source_url, product, fetched_at, event_at, raw_text,
  raw_hash (unique)
- `classifications`: event_id, model, schema_version, json, created_at
- `candidates`: id, product, first_seen, last_seen, status
  (`new`, `watching`, `considered`, `killed`)
- `scores`: candidate_id, week, score, weights_version, evidence_json
- `clones`: candidate_id, repo, stars, created_at, last_commit, checked_at
- `outcomes`: candidate_id, decision, decided_at, note, followup_at,
  followup_result

Never delete rows. Mark them instead.

## 8. Classifier

One call per event. Use a cheap model. Send calls through the Message Batches
API. Use structured outputs. See `https://docs.claude.com`.

Schema version 1 (`schemas/classification_v1.json`):

```json
{
  "product": "string",
  "category": "string",
  "event_type": "price | forced_account | license | shutdown | acquisition | api_lockdown | listing | pain",
  "buyer_type": "consumer | prosumer | developer | smb | enterprise",
  "value_per_user": "low | medium | high",
  "incumbent_moat": "network | data | seo | code | none",
  "heavy_lifting_available": true,
  "heavy_lifting_names": ["string"],
  "local_rules": "none | AT | DE | DACH | other",
  "evidence_of_paying": ["verbatim quote"],
  "quote": "one verbatim quote that best shows the pain",
  "confidence": 0.0
}
```

Rules for the prompt:

- Answer only from the given text. Do not use outside knowledge to fill fields.
- If a field is unknown, return `null`. Do not guess.
- Quotes must be verbatim and short (under 30 words).

Store the full JSON. Never overwrite an old classification; add a new row.

## 9. Scoring

Score = weighted sum of the filter points in section 4, each mapped to 0–1.

- Freshness: 1.0 at day 0, 0.0 at day 60, linear.
- Clones: 1.0 if none; 0.5 if only stale clones (no commit in 12 months);
  0.0 if a clone has more than 1,000 stars and a commit in the last 30 days.
- All other points come from the classification, mapped by tables in
  `config/weights.toml`.

Every score row stores the evidence: the quotes and the clone data that
produced it. A score without evidence is a bug.

Recompute all scores when `weights.toml` changes. Keep old scores.

## 10. Digest

Write `digests/YYYY-WW.md` every Monday. Contents:

1. Top 10 candidates. For each: score, event type, buyer type, days since
   event, clones found, two evidence quotes, one line "what you would build".
2. Kill list: candidates that scored in the top 20 last week and lost their
   window (a clone appeared, or the event was reversed).
3. New listings: businesses for sale that passed the moat test.
4. Stats: events collected, classified, cost in tokens.

Keep the digest under 2 pages. Link to the database for details.

## 11. Outcomes loop

This is the part that makes the tool better. Do not skip it.

- When the owner marks a candidate `considered`, record the decision and a
  follow-up date six months later.
- On the follow-up date, the digest asks: did someone ship it? Did it earn?
  Record the answer in `outcomes`.
- Once a quarter, compare outcomes with scores. Adjust weights. Record the
  change and the reason in `config/CHANGELOG.md`.

## 12. Legal and courtesy rules

- Respect `robots.txt` and terms of service. If a site forbids scraping, use
  its API or skip it.
- Arctic Shift and HN Algolia are third-party services. Sleep between calls.
  Do not run more than once a day.
- Store only public text. Do not store usernames of individuals.
- Do not publish collected data. See section 15.

## 13. Repo layout

The repository holds the public half: code, schemas, and this spec.

```
trueffelsau/         the package, named after the repository
  collectors/        one module per source
  classify/          prompt, schema, batch client
  score/             scoring and weights
  digest/            digest writer
  home.py            resolves the runtime home, reads the env file
config/              term lists and templates, public ones only
schemas/             JSON schemas, versioned
tests/
CLAUDE.md            this file
```

The private half lives outside the checkout, in the runtime home:

```
$TRUEFFELSAU_HOME/   default ~/.local/share/trueffelsau
  config/            weights.toml, watchlist.txt, CHANGELOG.md
  data/              radar.db
  digests/           weekly output
  inbox/             manual exports
  env                API keys, read by systemd and by the CLI
```

The home is outside the checkout on purpose. Each task runs in a throw-away git
worktree, so a database or a digest archive kept next to the code is discarded
with the worktree that happened to create it. A path outside the repository
also cannot be committed by accident.

Language: Python 3.12 or newer. Dependencies: `requests`, `sqlite3` (stdlib),
`anthropic`, `tomllib` (stdlib). Add a dependency only when it removes real
work.

## 14. Build plan

- Weekend 1: HN and Reddit collectors, database, classifier, plain digest.
- Weekend 2: marketplace collectors, GitHub clone check.
- Weekend 3: scoring with evidence, kill list, cron job.
- After that: only fix what the digests show is wrong.

Definition of done for weekend 1: one digest exists, and every candidate in
it links to the quotes that produced its score.

## 15. What is public and what is private

- Code, schemas, and this spec: public (AGPL-3.0, see `LICENSE`).
- Everything under `$TRUEFFELSAU_HOME` is private. The weights and the
  watchlist hold the owner's judgment, `data/` and `digests/` hold collected
  text and half-formed judgments about named companies, and `env` holds API
  keys. None of it is in the repository.

## 16. Rules for the agent

- Read this file before any change.
- Do not change the filter in section 4 without asking.
- Do not add a source that is not in section 6 without asking.
- Write tests for collectors with recorded fixtures. Do not call live APIs in
  tests.
- Keep functions small. Prefer plain Python over frameworks.
- When a classification looks wrong, add the case to
  `tests/fixtures/classify/` before changing the prompt.
- Report token cost in every digest.
