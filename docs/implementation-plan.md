# trueffelsau — implementation plan

## Context

The repo holds `LICENSE`, `CLAUDE.md` (the spec) and one handoff document. No
code exists. The spec fixes scope, filter, data model, sources, scoring and
digest. The handoff lists the build order and dead ends. This plan turns both
into concrete milestones and fixes the technology stack so the tool runs on
Martin's laptop with no extra infrastructure.

Stack decisions (answered 2026-09-16):

| Decision | Choice | Reason |
|---|---|---|
| Environment | `python3 -m venv .venv` + `pip install -e '.[dev]'`, hatchling `pyproject.toml` | Same as `tupferl`/`woswoar`; no new tool. `uv` is not installed. |
| Python | `requires-python = ">=3.12"`; runs on the installed 3.14.7 | Spec says 3.12; the laptop has only 3.14. Use nothing newer than 3.12 syntax. |
| Scheduler | systemd user timer, `Persistent=true` | Machine may be off on Monday; timer catches up. Journal gives logs. Already in use on this machine. |
| Package name | `trueffelsau` (not `radar`) | Matches repo and CLI name. Spec §13 gets updated. |
| Classifier model | `claude-sonnet-5` via Message Batches + structured outputs | ~$2.50 per 1,000 events at batch price. Configurable in `settings.toml`. |
| HTTP | `requests` | Per spec. Already a system package; simple; easy to fake in tests. |
| HTML parsing | stdlib `html.parser` | Marketplace pages are small and plain. Add a parser library only if a page defeats it. |
| Runtime home | `~/.local/share/trueffelsau/` (override with `TRUEFFELSAU_HOME`) holding `config/`, `data/`, `digests/`, `inbox/`, `venv/` | `gra` puts each task in a throw-away worktree; private state must live outside the worktree. Spec §13/§15 get updated. |
| Secrets | `$TRUEFFELSAU_HOME/env` (`KEY=value` lines): `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `APIFY_TOKEN` | Loaded by systemd `EnvironmentFile=` and by the CLI (10-line parser, no `python-dotenv`). |
| Dependencies | runtime: `anthropic>=1,<2`, `requests>=2.32`. dev: `pytest`, `ruff>=0.16`, `mypy` | Spec: add a dependency only when it removes real work. No pydantic, no jsonschema (the API guarantees schema-valid output). |
| CLI | `argparse` subcommands, entry point `trueffelsau = "trueffelsau.cli:main"` | Plain Python over frameworks. |

Verified facts the plan relies on:

- `anthropic` 1.6.0 on PyPI; 1.x needs Python ≥3.10. Batches (`client.messages.batches.create/retrieve/results`) are GA. Structured outputs (`output_config={"format": {"type": "json_schema", "schema": ...}}`) are GA, work inside Batches, and are supported on Sonnet 5 and Haiku 4.5. Nullable fields must be `{"anyOf": [{"type": "string"}, {"type": "null"}]}`; every object needs `additionalProperties: false` and a full `required` list. Structured outputs and Batches are Claude API only (fine here).
- `crond` and systemd user timers both exist locally; timers chosen.
- No Anthropic key in the environment yet. The Max subscription does not cover the API (help center: a paid subscription "doesn't include access to the Claude API or Console"). The owner creates a Console account, prepays ~$10, and puts `ANTHROPIC_API_KEY` in `$TRUEFFELSAU_HOME/env`. Expected spend: ~$2.50 per 1,000 classified events with Sonnet 5 at batch prices; `settings.max_events_per_run` caps it. Decided 2026-09-16: API key, not a `claude -p` backend.

## Repo layout (final)

```
pyproject.toml
README.md                        plain language, under one page
.gitignore                       .venv/, data/, digests/, inbox/, config/weights.toml, config/watchlist.txt, env, .env
CLAUDE.md                        spec; §13 and §15 updated for package name and runtime home
config/                          public term lists and templates, copied to $TRUEFFELSAU_HOME/config by `init`
  settings.toml                  model id, batch size, sleep intervals, max events per run
  weights.example.toml           full weights file with all mapping tables
  hn_terms.txt  subreddits.txt  pe_firms.txt  watchlist.example.txt
  CHANGELOG.md                   weight changes and reasons (spec §11)
schemas/classification_v1.json   spec §8 as real JSON Schema
schemas/db/001_init.sql          spec §7 tables; later files add columns, never drop
trueffelsau/
  __init__.py  __main__.py  cli.py
  home.py                        resolves TRUEFFELSAU_HOME, loads env file, paths
  settings.py                    reads settings.toml (tomllib)
  db.py                          connect, migrate (PRAGMA user_version), insert helpers
  http.py                        Session with User-Agent, timeout, retry on 429/5xx, per-host min interval
  models.py                      dataclasses: Event, Classification, Candidate, Score, Clone
  collectors/
    __init__.py                  registry: name -> collect(ctx) function
    hn.py  reddit.py  mabya.py  projektify.py  flippa.py  acquire_csv.py  alternativeto.py  watchlist.py  pe_news.py
  classify/
    prompt.py                    system prompt text (frozen, cached)
    batch.py                     build requests, submit, poll, ingest results
  score/
    weights.py                   load and validate weights.toml
    scoring.py                   seven points -> score with evidence
    clones.py                    GitHub search and clone factor
  digest/
    writer.py                    digests/YYYY-WW.md
scripts/
  install.sh                     creates $TRUEFFELSAU_HOME/venv, pip installs the checkout, installs the timer
  record_fixture.py              fetch one live response and save it under tests/fixtures/
  check_name.py                  GitHub name-availability check (from handoff §5)
systemd/
  trueffelsau.service  trueffelsau.timer
tests/
  conftest.py                    tmp home dir, tmp sqlite, fake HTTP session
  fixtures/<source>/*.json|html  recorded responses
  fixtures/classify/*.json       event text + expected classification (spec §16)
  test_*.py
.github/workflows/ci.yml         ruff, mypy, pytest on 3.12 and 3.14
```

## Data model (SQL, `schemas/db/001_init.sql`)

Spec §7 tables plus three additions needed to make the pipeline resumable and
auditable (spec §7 gets these lines):

- `batches(id TEXT PRIMARY KEY, model, schema_version, submitted_at, ended_at, status, request_count)` — a batch can take up to 24 h; the run must survive a restart.
- `classifications` gains `input_tokens INTEGER, output_tokens INTEGER` — digest §10.4 must report token cost.
- `candidate_events(candidate_id, event_id, PRIMARY KEY(candidate_id, event_id))` — links the events whose quotes form the evidence of a score.

Rules: `events.raw_hash = sha256(source || source_url || raw_text)` with `UNIQUE`; collectors use `INSERT OR IGNORE`, so reruns create no duplicates. No `DELETE` anywhere; `candidates.status` and `outcomes` mark instead. `PRAGMA user_version` tracks migrations; `db.migrate()` applies `schemas/db/NNN_*.sql` in order.

Candidate identity: `candidates.product` is the normalised classifier `product` (lowercase, ASCII, collapsed whitespace). Two events with the same normalised product join the same candidate.

## CLI

```
trueffelsau init                      create home dir, copy config templates, create db
trueffelsau collect [--source NAME]   run collectors, insert events
trueffelsau classify [--no-wait]      submit unclassified events as a batch; wait and ingest unless --no-wait
trueffelsau ingest                    ingest results of ended batches (used after --no-wait)
trueffelsau clones                    GitHub clone check for candidates with events in the last 60 days
trueffelsau score [--recompute]       score candidates for the current ISO week; --recompute rescoring all weeks with current weights
trueffelsau digest [--week YYYY-WW]   write the digest
trueffelsau run                       collect -> classify -> clones -> score -> digest; each step idempotent
trueffelsau decide ID considered|killed|watching --note TEXT   records outcome, followup_at = +6 months
trueffelsau followup ID --result TEXT
trueffelsau status                    pending batches, last run per collector, event counts
```

`run` is safe to rerun after an interruption: collectors dedupe by hash, `classify` skips events that already have a classification for `(model, schema_version)`, `ingest` skips batches already marked ended, `score` overwrites nothing (a second score row for the same week gets a new `weights_version` or is skipped when identical).

## Classifier (`trueffelsau/classify/`)

- `schemas/classification_v1.json`: spec §8 fields; enums for `event_type`, `buyer_type`, `value_per_user`, `incumbent_moat`, `local_rules`; every field nullable via `anyOf`; `additionalProperties: false`; all fields `required`.
- System prompt (frozen text in `prompt.py`, marked `cache_control: {"type": "ephemeral"}`): the three rules from spec §8 (answer only from the text, `null` when unknown, verbatim quotes under 30 words) plus the field definitions. User message: `source`, `source_url`, `event_at`, and `raw_text` (truncated at `settings.max_chars_per_event`, default 6000, with a note when truncated — not silently).
- Request per event:
  `Request(custom_id=f"event-{id}", params=MessageCreateParamsNonStreaming(model=settings.model, max_tokens=1024, system=[...cached...], messages=[...], output_config={"format": {"type": "json_schema", "schema": schema}}))`
- Submit in chunks of `settings.batch_size` (default 1000). Store batch id in `batches`. Poll `client.messages.batches.retrieve(id).processing_status` every 60 s until `"ended"` (or return at once with `--no-wait`). Iterate `client.messages.batches.results(id)` keyed by `custom_id`; for `succeeded` parse the first text block with `json.loads`, store the full JSON, `usage.input_tokens`, `usage.output_tokens`; for `errored`/`expired` log and leave the event unclassified so the next run retries; check `stop_reason` (`max_tokens`, `refusal`) before parsing.
- Cost cap: `settings.max_events_per_run` (default 2000) bounds a week's spend; `status` shows how many were left over.
- Tests: fake `client` object whose `batches.results()` yields recorded result objects from `tests/fixtures/classify/batch_results.jsonl`; assert rows in `classifications`. Prompt regression cases live in `tests/fixtures/classify/*.json` (input text + expected fields); a test checks the prompt still contains each rule string.

## Scoring (`trueffelsau/score/`)

Seven points, each mapped to 0–1, weights and mapping tables in `weights.toml`:

| Point | Source | Mapping (in `weights.toml`) |
|---|---|---|
| p1 forced decision + money | `event_type`, `evidence_of_paying` | `[event_type]` table (e.g. `price=1.0, shutdown=1.0, api_lockdown=0.8, license=0.8, forced_account=0.9, listing=0.7, acquisition=0.6, pain=0.3`); `+0.2` capped at 1 when `evidence_of_paying` is non-empty |
| p2 buyer | `buyer_type`, `value_per_user` | `[buyer_type]` table; `consumer` uses `[consumer_value]` table by `value_per_user` |
| p3 moat | `incumbent_moat` | `[moat]` table: `code=1, none=1, seo=0.2, data=0.1, network=0` |
| p4 heavy lifting | `heavy_lifting_available` | `true=1, false=0.2, null=0.5` |
| p5 freshness | `event_at` (fallback `fetched_at`) | linear 1.0 at day 0 → 0.0 at day 60 |
| p6 clones | `clones` table | `1.0` none; `0.5` only stale (no commit in 12 months); `0.0` if a repo has >1,000 stars and a commit in 30 days |
| p7 owner fit | `category`, `heavy_lifting_names`, `local_rules` | `[fit.keywords]` table (substring match on category/names, e.g. `video`, `pdf`, `remote desktop`, `cli`, `linux`, `performance` → 1.0; `mobile app`, `social`, `marketplace` → 0.2; default 0.5) plus `[fit.local_rules]` (`AT=1.0, DACH=0.8, DE=0.6, other=0.4, none=0.5`); take the max |

`score = Σ weight_i · p_i  ×  (gate_when_cloned if p6 == 0 else 1)` with `gate_when_cloned = 0.1` in `weights.toml`. `weights_version = sha256(weights.toml)[:12]`. `evidence_json` stores: per-point value and the input that produced it, the event ids, the `quote` and `evidence_of_paying` strings, and the clone rows. A score with an empty evidence list raises — it is a bug per spec §9.

`null` classification fields map to 0.5 (unknown), never to 0 or 1; the value and the fact that it was a default are in the evidence.

Point 7's keyword table is the weakest mapping (no classifier field for owner fit). Revisit after three digests; a schema v2 with an `owner_fit` field needs the owner's approval (spec §16).

## Clone check (`score/clones.py`)

For each candidate with an event in the last 60 days: `GET https://api.github.com/search/repositories?q=<product>+in:name,description&sort=stars&order=desc&per_page=10` with `Authorization: Bearer $GITHUB_TOKEN` when set. Store top 10 as `clones` rows (`repo, stars, created_at, last_commit = pushed_at, checked_at`). Sleep `settings.github_sleep` (default 2 s). Recorded fixture for tests.

## Digest (`digest/writer.py`)

`$TRUEFFELSAU_HOME/digests/YYYY-WW.md`, ISO week. Sections exactly as spec §10:

1. Top 10: score, event type, buyer type, days since event, clones found (`n repos, max stars`), two quotes, "what you would build" (= `product` + `heavy_lifting_names`, one line, no LLM call).
2. Kill list: candidates in last week's top 20 whose p6 is now 0 or whose status was set to `killed` this week.
3. New listings: events with `event_type = listing` this week and `p3 ≥ 0.5`.
4. Stats: events collected per source, events classified, input/output tokens and cost (`tokens × price from settings.toml`), collector health (last success time per source, errors).
5. Follow-ups due: `outcomes` with `followup_at ≤ today` and no `followup_result`, each with the `trueffelsau followup` command to run.

Under 2 pages: cap quotes at 30 words, list at most 10/10/10 items.

## Collectors (`trueffelsau/collectors/`)

Common shape so tests never hit the network:

```python
def fetch(http, cfg, since) -> list[dict]   # raw JSON/HTML pages
def parse(raw, source_url_fn) -> list[Event] # pure; tested with fixtures
def collect(ctx) -> CollectResult           # fetch + parse + insert; records health
```

| Collector | Milestone | Access | Notes |
|---|---|---|---|
| `hn` | M1 | `https://hn.algolia.com/api/v1/search_by_date?query=<term>&tags=(story,comment)&numericFilters=created_at_i><since>&hitsPerPage=100` | One call per term in `hn_terms.txt`; `source_url = https://news.ycombinator.com/item?id=<objectID>`; `raw_text = title + story_text/comment_text` (HTML stripped). Sleep 1 s. |
| `reddit` | M1 | Arctic Shift `/api/posts/search?subreddit=<s>&query=<term>&after=<iso>&limit=100&sort=asc` | subreddits × terms calls, sleep ≥1 s; `raw_text = title + selftext`; store no usernames. Test paging on one small subreddit first (handoff reflection 1). |
| `mabya`, `projektify` | M2 | listing index → listing pages, stdlib `html.parser` | `event_type` hint `listing`; check `robots.txt` before first fetch and record the result in the collector docstring. |
| `acquire_csv` | M2 | CSV from `inbox/` | Column mapping in `settings.toml`. |
| `flippa` | M2 (optional) | Apify actor via REST, needs `APIFY_TOKEN` | Skipped with a status line when the token is unset. |
| `alternativeto` | M2 (optional) | page fetch | Only if `robots.txt` allows; otherwise skipped, noted in README. |
| `watchlist` | M3 | fetch URL, diff against last stored body | Generic "fetch and diff a URL" — also serves changelogs. |
| `pe_news` | M3 | Claude `web_search_20260209` server tool on `claude-sonnet-5`, one call per firm in `pe_firms.txt` | Keeps the stack to one external API. Results become `acquisition` events with the found URL as `source_url`. |

Each collector records `(source, started_at, finished_at, events_new, error)` in a `collector_runs` table (fourth data-model addition; feeds "collector health" in the digest per handoff reflection 4).

## Milestones

### M0 — scaffolding (small, one commit each)

1. `pyproject.toml` (hatchling, dynamic version from `trueffelsau/__init__.py`, deps as above, `[project.scripts]`, ruff/mypy config), `.gitignore`, `README.md`, empty package tree.
2. `schemas/classification_v1.json`, `config/` templates, `schemas/db/001_init.sql`.
3. `home.py`, `settings.py`, `db.py` with `migrate()`; `trueffelsau init`; tests for migrate (idempotent, `user_version` advances) and env-file parsing.
4. `.github/workflows/ci.yml`.
5. Update `CLAUDE.md` §7 (four table additions), §13 (package name, runtime home), §15 (private files live in `$TRUEFFELSAU_HOME`).

### M1 — weekend 1: first digest

6. `http.py` with retry and min-interval; test with a fake adapter.
7. `collectors/hn.py` + recorded fixture (`scripts/record_fixture.py hn "postman pricing"`), tests: parse, dedupe on rerun, since-filter.
8. `collectors/reddit.py` + fixture, tests as above; paging test.
9. `classify/prompt.py`, `classify/batch.py`, `batches` table; tests with fake client and `batch_results.jsonl` fixture; `classify --no-wait` and `ingest`.
10. Candidate creation from classifications (`candidate_events`), minimal `score` with p1–p5 and p7 (p6 = 1.0 until M2), `digest` sections 1 and 4.
11. `run` command. Definition of done: `trueffelsau run` on the laptop produces one `digests/YYYY-WW.md` and every top-10 entry shows the quotes and event ids behind its score.

### M2 — weekend 2: marketplaces and clones

12. `score/clones.py` + fixture; p6 wired in; `clones` command.
13. `mabya.py`, `projektify.py`, `acquire_csv.py` with fixtures; digest section 3.
14. `flippa.py` and `alternativeto.py` only if access checks pass; otherwise document why not.

### M3 — weekend 3: scoring loop, kill list, timer

15. `score --recompute`, `weights_version`, `config/CHANGELOG.md` convention.
16. Kill list (digest section 2) from last week's top 20 vs current p6/status.
17. `decide`, `followup`, digest section 5 (outcomes loop, spec §11).
18. `watchlist.py`, `pe_news.py`.
19. `scripts/install.sh` + `systemd/trueffelsau.{service,timer}` (`OnCalendar=Mon 06:00`, `Persistent=true`, `EnvironmentFile=%h/.local/share/trueffelsau/env`, `ExecStart=%h/.local/share/trueffelsau/venv/bin/trueffelsau run`). README section "Install on your machine".

### After M3

Only fix what digests show (spec §14). Candidates for later: window timer (handoff reflection 7), schema v2 with `owner_fit`.

## Verification

Per change (working agreements): focused test after the first edit, regression test per behaviour, one semantic mutation per regression test, then `ruff check . && ruff format --check . && mypy trueffelsau && pytest`.

End to end after M1:

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
export TRUEFFELSAU_HOME=/tmp/tsau && .venv/bin/trueffelsau init
# put ANTHROPIC_API_KEY=... into /tmp/tsau/env
.venv/bin/trueffelsau collect --source hn      # check: events table grows, rerun adds 0
.venv/bin/trueffelsau classify                 # check: batch row, classifications rows with token counts
.venv/bin/trueffelsau score && .venv/bin/trueffelsau digest
cat /tmp/tsau/digests/$(date +%G-%V).md        # each top-10 entry lists quotes and event ids
sqlite3 /tmp/tsau/data/radar.db 'select candidate_id, score, evidence_json from scores limit 3'
```

After M3: `scripts/install.sh`, then `systemctl --user list-timers trueffelsau.timer` and `systemctl --user start trueffelsau.service && journalctl --user -u trueffelsau -n 50`.

## Risks and open points

- Spec §7/§13/§15 need the edits listed in M0 step 5; §16 forbids changing the filter (§4) and adding sources, and this plan does neither.
- Point 7 (owner fit) mapping is a heuristic; expect to tune it after the first digests.
- Arctic Shift paging and uptime are unverified; the reddit collector must fail soft (record error, continue with other sources).
- Marketplace HTML can change; fixtures pin the parsed shape so a break shows up as a test failure, not a silent empty digest.
- No Anthropic key exists on the machine yet; M1 step 11 needs one.
