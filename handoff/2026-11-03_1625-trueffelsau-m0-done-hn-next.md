# Session Handoff — 2026-11-03 16:25

## Resume prompt
Paste this into a fresh session in your local checkout:
> Read `handoff/2026-11-03_1625-trueffelsau-m0-done-hn-next.md` and continue
> the work described there. Start with step 1 of "Next steps".

## Goal
Build **trueffelsau**: a tool that finds product ideas with a high chance of
success, every week, with little manual work. `CLAUDE.md` is the spec.
`docs/implementation-plan.md` turns it into milestones M0 to M3.

This session finished M0 and M1 step 6. The next step is M1 step 7, the
Hacker News collector. The owner moved to a local session because the cloud
session could not reach the HN and Reddit APIs.

## State

- Repo: `https://github.com/martinus/trueffelsau`, branch `main`.
- Last commit: `382e6d2 Add http.py: one fetcher with the courtesy and safety
  rules (#4)`. CI is green on Python 3.12 and 3.14.
- 200 tests pass. `ruff check`, `ruff format --check` and
  `mypy trueffelsau tests` are clean.
- No open pull requests. Merged branches are deleted automatically.
- **Done**
  - M0, complete (PR #3): `trueffelsau/schemas/classification_v1.json`,
    `trueffelsau/config/` templates, `settings.py`, `bootstrap.py`,
    `cli.py` with `trueffelsau init`, spec §7 and §13 updated.
  - M1 step 6 (PR #4): `trueffelsau/http.py`, one `Fetcher` for all
    requests.
  - The owner ran `trueffelsau init` on his PC. His runtime home is
    `/home/martinus/.local/share/trueffelsau`, at database version 1. All
    files were created; none are edited yet. `env` has no API key yet.
  - `docs/implementation-plan.md` has a new section "Ideas taken from
    isas1/daily-team" and a model decision (see below).
- **In progress**
  - Nothing is mid-edit. The HN collector was designed (see "Design for the
    HN collector") but no file was written.
- **Not started**
  - M1 steps 7 to 12: HN collector, Reddit collector, classifier, candidates
    and minimal score, model check, `run` command.
  - M2 and M3.

## Key context

### Files that matter
- `CLAUDE.md`: the spec. Read it first. §4 (the filter) and §6 (the
  sources) must not change without asking the owner (§16).
- `docs/implementation-plan.md`: milestones, the stack decisions, the
  daily-team ideas table, and the risks. Steps are numbered 1 to 20.
- `trueffelsau/http.py`: every collector must fetch through
  `Fetcher.get(url, params=..., expect="json"|"html"|"feed"|"any")`. It
  pauses per host, caps bytes while reading, checks the content type,
  follows same-host HTTPS redirects only, and retries 408/429/5xx. Tests
  inject `send`, `clock` and `sleep`.
- `trueffelsau/settings.py`: `Settings` dataclass. A new setting needs two
  edits: the field with its default, and the same line in
  `trueffelsau/config/settings.toml`. A test fails when the template and
  the defaults differ.
- `trueffelsau/bootstrap.py`: `TEMPLATES` maps package templates to home
  file names. A test fails when a template file is not in this map.
- `trueffelsau/schema/001_init.sql`: tables, including `collector_runs`
  (source, started_at, finished_at, events_new, error) and `events` with
  `raw_hash TEXT NOT NULL UNIQUE`.
- `tests/test_templates.py`: reads the field list and enums from the JSON
  block in `CLAUDE.md` §8 and the signal types from the §5 table. If you
  change either, change the schema too.

### Decisions and reasons
1. **Classifier model: `claude-haiku-5-5`** (was `claude-sonnet-5`, now a
   legacy model). Batch prices per MTok: Haiku 5.5 $0.05 / $0.25,
   Sonnet 5.5 $1 / $5 (docs checked 2026-11-03). The docs name
   classification and extraction as Haiku 5.5's use. It is not proven for
   the judgment fields, so plan step 11 compares Haiku 5.5 and Sonnet 5.5
   on the first 50 real events. Rule: if they disagree on more than 1 event
   in 10 in `event_type`, `buyer_type`, `value_per_user`, `incumbent_moat`
   or `heavy_lifting_available`, read the disagreements; switch to Sonnet
   5.5 when Sonnet is right in most of them.
2. **Templates, schemas and migrations live inside the package.** The weekly
   run uses an installed wheel, which has no repository root. Verified by
   building a wheel and running `init` from a clean virtualenv.
3. **The two list fields in the classifier schema are plain arrays**, empty
   when nothing is found, not nullable. One way to say "nothing", and two
   fewer union fields (the API allows 16; the schema uses 10).
4. **Structured outputs rules** (docs, 2026-11-03): no `minimum`, `maximum`,
   `maxItems` or string length limits; `additionalProperties: false`; every
   field required. The model may change the capitalization of an enum
   value, so ingest must compare enums case-insensitively and store them in
   lowercase.
5. **Ideas from isas1/daily-team** (a daily web-tool generator that reads
   HN, Reddit and GitHub Trending): only its source handling fits. The
   table in the plan lists each idea and where it goes. Still to build:
   skip a source with a successful run in the last `collector_min_hours`
   (20); raise "markup may have changed" when an HTML parser finds zero
   items; drop a classifier quote that is not a substring of `raw_text`.
   GitHub Trending was not added (needs the owner's approval, §16).
   `r/SideProject` was added to `subreddits.txt`.
6. **`init` never overwrites.** Files are created with `O_EXCL`. The home is
   mode 700, `env` is mode 600.

### Design for the HN collector (worked out, not written)
- `trueffelsau/events.py`: `Event` dataclass (source, source_url, product,
  fetched_at, event_at, raw_text). `raw_hash` = sha256 of source,
  source_url and raw_text joined with `\x00`, so field borders cannot
  collide. `insert_events(connection, events) -> int` with
  `INSERT OR IGNORE`, returns the number of new rows.
- `trueffelsau/collectors/__init__.py`: a registry of name to collect
  function, and `run_collector(name, ctx)`. It skips a source whose last
  run without error started less than `collector_min_hours` ago. It writes
  a `collector_runs` row at the start and completes it at the end. An
  exception becomes the `error` text, and the other sources still run.
  `ctx` carries the connection, the fetcher, the settings, the home config
  directory and `now`, so tests control time.
- `trueffelsau/collectors/hn.py`:
  - One query per term in `hn_terms.txt`:
    `https://hn.algolia.com/api/v1/search_by_date` with `query`,
    `tags=(story,comment)`, `numericFilters=created_at_i>SINCE`,
    `hitsPerPage=100`, `page=N`. Stop at the last page or at a page cap.
  - `source_url = https://news.ycombinator.com/item?id=<objectID>`.
  - `event_at` from `created_at`, as an ISO-8601 UTC string ending in `Z`.
  - `raw_text`: for a story, the title, the link URL and `story_text`; for
    a comment, "Comment on: <story_title>" and `comment_text`. Strip HTML
    with stdlib `html.parser`; turn `<p>` into blank lines; decode
    entities. Skip hits with no text.
  - Never store `author` (§12).
  - One failed term does not stop the others. The run then records an
    error such as "2 of 21 terms failed: ...", so the next run is not
    skipped.
  - `SINCE` = start of the last run without error; on the first run, now
    minus a new setting `first_run_days` (suggested 14).
- CLI: `trueffelsau collect [--source NAME]`. One line per source: new and
  seen counts, "skipped, last run N h ago", or "failed: reason". Exit code
  1 when any source failed.
- `scripts/record_fixture.py hn "<term>"`: fetches live through `Fetcher`
  with the collector's own parameters. Writes
  `tests/fixtures/hn/recorded_<slug>.json` as
  `{"request": ..., "recorded_at": ..., "response": ...}`. Removes `author`
  and `_highlightResult` (which repeats the author) from every hit, and
  keeps at most 5 hits.
- Tests: logic tests (dedupe on rerun, since filter, HTML stripping, URL,
  paging, skip-if-recent, fail-soft) on a small hand-made fixture. One
  parametrized test runs `parse` on every `recorded_*.json` and checks that
  each gives events with all required fields.

### Not verified yet
- The exact HN Algolia response shape. Fetch one live response before you
  write the hand-made fixture. Expected keys per hit: `objectID`,
  `created_at`, `created_at_i`, `title`, `url`, `story_text`,
  `comment_text`, `story_id`, `story_title`, `parent_id`, `_tags`; top
  level `hits`, `nbHits`, `page`, `nbPages`, `hitsPerPage`.
- How Algolia matches a multi-word term. If "price increase" matches the
  two words anywhere in a text, most hits are noise, and noise costs
  classification money (`max_events_per_run` caps it at 2000). Check
  whether a quoted phrase gives exact matches.
- Arctic Shift paging with `limit=auto` (from the first handoff).

### Open question for the owner
- **Fixtures in a public repository.** §15 says "Do not publish collected
  data", and §16 asks for recorded fixtures. A recorded HN response is
  collected public text. The plan above strips usernames and keeps 5 hits.
  Ask the owner whether that is acceptable, or whether recorded fixtures
  should stay out of git (for example in `tests/fixtures/` with a
  `.gitignore` entry and a skip when they are missing).

### Dead ends — do not repeat
- In the cloud session: the shell could not reach `hn.algolia.com` or
  `arctic-shift.photon-reddit.com` (proxy refused). This is why the owner
  moved to a local session.
- A force push was refused in the cloud session. To bring a stacked branch
  up to date, merge `main` into it instead of rebasing.
- `pytest | tail -1` hides a failing exit code, and a push in the same
  command line went out with a broken `pyproject.toml` (a merge had
  duplicated `[project.scripts]`). Use `set -o pipefail`, or run the checks
  and the push as separate steps.
- The cloud clone was shallow with a fetch refspec for `main` only. A local
  full clone does not have this problem.
- The first daily-team page fetched was a cached version from 2026-10-03.
  The repo had changed a lot by 2026-11-03. Clone a repo to read its
  current state.

### Commands
```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/ruff format --check . \
  && .venv/bin/mypy trueffelsau tests && .venv/bin/pytest
TRUEFFELSAU_HOME=/tmp/tsau .venv/bin/trueffelsau init   # scratch home
```
Working practice so far: one pull request per step, squash merge, the PR
title ends in `(#N)`. Each PR so far listed defects that were injected one
at a time to show that the new tests catch them.

## Next steps
1. Fetch one live HN Algolia response and confirm the shape and the
   multi-word matching (see "Not verified yet").
2. Ask the owner the fixture question (see "Open question").
3. Write `trueffelsau/events.py` and `collectors/__init__.py`
   (`run_collector`) with tests.
4. Write `collectors/hn.py` with the hand-made fixture tests.
5. Write `scripts/record_fixture.py`, record one fixture, and add the
   parametrized test over recorded fixtures.
6. Add `trueffelsau collect` to the CLI. Run it against a scratch home, then
   rerun it: the second run must add 0 events (or be skipped).
7. Open a PR. Then continue with M1 step 8, the Reddit collector, in the
   same way.
8. Before M1 step 9 (classifier): the owner needs an API key from the Claude
   Console in `~/.local/share/trueffelsau/env`.

## Reflection

### 1. What in the delivered work am I least confident is correct?
The default weights and the owner-fit keyword table in
`weights.example.toml`. They are copied from the plan, which says they are
judgment calls without data. The tests check that they are complete and
sum to 1, not that they are right. Second: the cost per 1,000 events
($0.15 for Haiku 5.5) is an estimate from assumed token counts, without
thinking tokens.

### 2. What assumptions did I make that I never stated explicitly?
That `collector_min_hours = 20` is a fair reading of §12 ("do not run more
than once a day"). That HN comments are worth collecting at all; they are
most of the volume and most of the noise. That 5 hits per recorded fixture
is enough to catch a format change.

### 3. What is the biggest thing the user may not realize about the broader situation?
The first real cost is not money but noise. With 21 terms, comments
included, and loose word matching, one week can fill the 2,000-event cap
with unrelated text, and the digest then ranks noise. Check the hit
counts per term on the first collection, before the classifier exists.

### 4. If this work breaks in 3 months, what's the most likely reason?
A source changes its response format. The recorded fixtures pin today's
format, so tests will not notice a change on the live side. The
`collector_runs` health line in the digest is the only guard; build it in
M1, not later.

### 5. Were there any tools, scripts, or hooks that would have reduced my churn this session if they had existed when we started?
A script that runs the defect-injection check from a list of
(file, old, new, name) entries. I wrote it twice, ad hoc. Keeping it as
`scripts/inject_defects.py` would make the check cheap for every PR.

### 6. What could the user have done differently to make this session smoother?
Starting locally, or allowing the two API hosts in the cloud session's
network settings, would have let the HN collector be finished in this
session.

### 7. If I could add one unrequested, industry-leading feature, what would it be?
A per-term yield report in `trueffelsau status`: for each search term, how
many events it found and how many of those reached a candidate with a
score above 0.5. Terms that never produce a candidate cost API calls and
classification money; this would show which ones to delete.
