# Session Handoff — 2026-09-16 15:39

## Resume prompt
Paste this into a fresh session:
> Read `handoff/2026-09-16_1539-trueffelsau-kickoff.md` and continue the work described there. Start with step 1 of "Next steps".

## Goal
Plan and build **trueffelsau**, a research tool that finds product ideas with
a high chance of success, every week, with little manual work. The tool
collects public signals (enshittification events, businesses for sale,
repeated user pain), classifies them with an LLM, scores them against a fixed
filter, and writes a weekly digest for the owner to read.

The name is Austrian dialect for "truffle pig": an animal whose job is to
find something valuable buried in dirt.

## State

- Repo: `https://github.com/martinus/trueffelsau`, branch `main`
- Last commit: `4dbe742 Initial commit` (2026-09-16 17:37 +0200), contains
  only `LICENSE` (GNU AGPL v3.0). No README, no code, no spec in the repo yet.
- **Done**
  - Repository created with AGPL-3.0 license. Verified by cloning and reading
    `LICENSE`.
  - Project spec written as `CLAUDE.md`. It is delivered next to this handoff
    (`../CLAUDE.md`) and is **not yet committed**. It is the source of truth
    for scope, filter, data model, sources, scoring, and agent rules.
  - Name checked against GitHub search: zero repositories named
    `trueffelsau` existed on 2026-09-16.
  - License decision made (see Key context).
- **In progress**
  - Nothing is mid-edit. The planning conversation ended with the spec
    complete and the repo empty.
- **Not started**
  - Everything in the build plan (spec section 14): collectors, database,
    classifier, scoring, digest, tests, cron.
  - README, `.gitignore`, `config/` templates, `schemas/` files.

## Key context

### Who the owner is (relevant facts only)
- Martin, senior software engineer, Austria. C++ performance specialist,
  maintainer of `ankerl::unordered_dense`, `nanobench`, `svector`. Linux user.
  Prefers Python for small tools. Works with Claude Code and keeps agent
  templates in `github.com/martinus/ai`.
- Wants a side project that can eventually become a second income line, but
  has concluded that most "passive" software income is gone. This tool exists
  to find the exceptions systematically instead of by brainstorming.
- Documentation preference: **plain language** (ISO 24495) and Simplified
  Technical English (ASD-STE100). Short sentences, active voice, one
  instruction per sentence. Apply this to README, spec, digests, and prompts.
- Code preference: small functions, plain Python over frameworks, few
  dependencies, tests with recorded fixtures.

### Files that matter
- `CLAUDE.md` (to be committed to repo root): full spec. Read it first.
  Sections: purpose, goals, non-goals, **the filter** (section 4), signal
  types, sources, data model, classifier schema, scoring, digest, outcomes
  loop, legal rules, repo layout, build plan, public/private split, agent rules.

### Decisions and reasons
1. **The filter is the product.** Section 4 of the spec encodes two days of
   analysis. Do not change it without asking the owner. Summary: a candidate
   scores high when users face a forced, money-attached decision; the buyer
   is a business, team, or developer; the incumbent's moat is code or bad UX
   (not network, data, or SEO); the heavy lifting exists as open source; the
   event is fresh; no clone with >1,000 stars appeared in the last month; and
   the idea fits the owner (native, performance, Linux, C++/Rust, or Austrian
   rules he can learn).
2. **Why these signals.** Enshittification events (price hikes, forced
   accounts, license changes, shutdowns, acquisitions, API lockdowns) open
   short windows in which open-source or cheaper replacements win — examples
   the owner knows: Bruno vs Postman, Plausible vs Google Analytics, RustDesk
   vs TeamViewer. Businesses for sale on marketplaces (Mabya, Projektify,
   Flippa) expose validated niches with their economics. Both are proxies for
   "someone already pays for this".
3. **License: AGPL-3.0**, chosen to keep the open-core / hosted-service
   option open. The owner is sole copyright holder. If outside contributors
   appear, decide about a CLA before merging, or relicensing becomes hard.
4. **Public code, private judgment.** Code, schemas, and spec are public.
   `config/weights.toml`, `config/watchlist.txt`, `data/`, `digests/`, and
   `inbox/` are private (git-ignored). Reason: the weights encode the owner's
   filter, the collected text has licensing risk if republished, and digests
   contain half-formed judgments about named companies.
5. **Storage: SQLite**, never delete rows, version every schema and every
   classification. Reason: scores must be recomputable when weights change,
   and the outcomes table (section 11) is the long-term asset.
6. **Classifier: cheap model, Message Batches API, structured outputs.**
   Reason: a few hundred to a few thousand independent calls per week; cost
   matters, latency does not. Check `https://docs.claude.com` for current
   model names and batch/structured-output APIs — do not rely on memory.
7. **Every score must carry evidence** (verbatim quotes, clone data). A score
   without evidence is a bug. Reason: false positives are the main failure
   mode; the owner must be able to audit why something ranked high.
8. **Outcomes loop is mandatory** (section 11). Record decisions and check
   back after six months. Without it the weights never improve.

### Source access notes (verified during planning)
- Hacker News: Algolia API, `https://hn.algolia.com/api/v1/search?query=...`,
  no key needed. Query terms belong in `config/hn_terms.txt`.
- Reddit: **Arctic Shift**, `https://arctic-shift.photon-reddit.com/api`,
  unauthenticated. Endpoints `/posts/search`, `/comments/search` (filters:
  `subreddit`, `query`, `after`, `before`, `limit=auto`, `sort`), and
  `/comments/tree?link_id=t3_<id>`. Full-text search works for one subreddit
  per call. It is one person's project: sleep ≥1 s between calls, run at
  most daily. README: `github.com/ArthurHeitmann/arctic_shift`.
- Mabya (`mabya.de`): German marketplace; listing pages are plain HTML and
  small (e.g. `mabya.de/auction/19095`, a sports-tournament SaaS with ~€17k
  revenue). Fetch weekly. Projektify (`projektify.de`) similar, covers Austria.
- Flippa: an Apify scraper exists that returns price, revenue, profit,
  multiple, category, age. Direct scraping of Flippa is not recommended.
- Acquire.com: requires login. Plan: owner exports CSV manually into `inbox/`.
- GitHub: REST search API works unauthenticated at low rate
  (`api.github.com/search/repositories?q=<name>+in:name`). Use a token for
  the clone check to avoid rate limits.
- AlternativeTo: page fetch of "alternatives to X" pages for demand signal.
  Check `robots.txt` first.

### Dead ends — do not repeat
- **Google Trends via `pytrends`**: unofficial, breaks often. Spec says do not
  add unless a stable API exists.
- **Traderoo** (German marketplace): listings are not indexed and need login;
  not worth a collector for now.
- **Reddit official API**: not needed; Arctic Shift covers it without OAuth.
- **Generic "AI SaaS" listings on Flippa** (AI writing tools, YouTube
  automation): these are exactly the layer that AI assistants absorb; the
  filter should score them near zero. Do not treat high revenue there as a
  signal.
- **Ad-supported consumer tools** as a target category: the owner's own data
  (a calculator site earning ~€7–14/month on ~200 search clicks) showed the
  model is dead for solo builders. Score consumer/ad ideas low.

### Commands
- Nothing exists yet. Planned: Python 3.12, `requests`, `anthropic`, stdlib
  `sqlite3` and `tomllib`. Tests must use recorded fixtures, never live APIs.
- Clone: `git clone https://github.com/martinus/trueffelsau`

## Next steps
1. Copy `CLAUDE.md` from the handoff bundle to the repo root. Commit:
   `git add CLAUDE.md && git commit -m "Add project spec"`.
2. Add `README.md` (plain language, under one page: what it is, the truffle-pig
   name, what is public vs private, how to run once code exists) and
   `.gitignore` covering `data/`, `digests/`, `inbox/`, `config/weights.toml`,
   `config/watchlist.txt`, `.env`.
3. Create the layout from spec section 13, with empty `__init__.py` files and
   `config/*.example` templates (`weights.example.toml`, `hn_terms.txt`,
   `subreddits.txt`, `pe_firms.txt`). Commit.
4. Write `schemas/classification_v1.json` exactly as in spec section 8.
5. Build weekend 1 (spec section 14): HN collector, Arctic Shift collector,
   SQLite schema from section 7, batch classifier, plain digest. Definition
   of done: one `digests/YYYY-WW.md` exists and every candidate in it links
   to the quotes that produced its score.
6. Record fixtures for both collectors and write tests before adding any
   further source.
7. Then weekend 2 (marketplaces, GitHub clone check) and weekend 3 (scoring
   with evidence, kill list, cron), per the spec.

## Reflection

### 1. What in the delivered work am I least confident is correct?
The scoring section of the spec (section 9). The weights, the freshness
decay (0 at 60 days) and the clone thresholds (1,000 stars, 30 days) are
judgment calls made without data. They are stated as defaults so the tool
can run, not as validated numbers. Check them against the first three
digests: if the top 10 is dominated by one signal type, the weights are
wrong. Also unverified: whether Arctic Shift's `before`/`after` paging
behaves exactly as described in its README under `limit=auto`; test with a
small subreddit first.

### 2. What assumptions did I make that I never stated explicitly?
- That the owner will actually maintain the outcomes table by hand for
  months. If not, the tool stays a feed and never becomes a filter.
- That HN and Reddit contain the events early enough. Enterprise-tool
  changes (licensing, PE acquisitions) often surface first in trade press
  or vendor forums; if that's true, the "acquisition news via web search"
  collector matters more than the spec suggests.
- That the marketplaces' HTML stays stable enough for weekly fetches. If
  not, the collectors need fixtures updated every few months.
- That the owner's employer contract permits a side business built on this
  output. This was flagged in conversation but never confirmed.

### 3. What is the biggest thing the user may not realize about the broader situation?
The tool can only raise candidate quality; it cannot raise the chance of
success, which is decided by distribution and execution. The pattern the
tool hunts (open-source or cheaper replacement after an enshittification
event) rewards shipping within roughly three weeks of the event. If the
owner is not willing to drop other work and ship fast when the digest shows
a strong candidate, most of the tool's value is lost. A second point: the
best candidates may not be "build" ideas at all but "buy" ideas (small
businesses for sale that the owner could run cheaper); the spec treats
listings as one source among many, and they may deserve more weight.

### 4. If this work breaks in 3 months, what's the most likely reason?
Source drift. Arctic Shift is a volunteer service with no uptime guarantee;
Mabya, Projektify and AlternativeTo can change HTML at any time; the Apify
Flippa scraper can be withdrawn. Second most likely: the Anthropic model
name or Batches API details in the classifier go stale. Mitigation is in
the spec (fixtures, version the schema, check docs) but nothing enforces it.
Add a "collector health" line to the digest early.

### 5. Were there any tools, scripts, or hooks that would have reduced my churn this session if they had existed when we started?
A GitHub name-availability script (search API loop) was written ad hoc to
check candidate names; keep it as `scripts/check_name.py`, it is useful for
every future project. A generic "fetch and diff a URL weekly" script would
have made the pricing-page watchlist collector trivial; it is worth building
first because three collectors (pricing pages, changelogs, AlternativeTo)
reduce to it.

### 6. What could the user have done differently to make this session smoother?
The constraints (passive, scalable, no second domain, no support burden)
came out one at a time over two days, each one invalidating the previous
round of ideas. Stating all constraints up front — and which are
negotiable — would have shortened the path considerably. That is exactly
what the spec's filter now does for the next agent: it is the constraint
list written down once.

### 7. If I could add one unrequested, industry-leading feature, what would it be?
A **window timer per candidate**: for each fresh event, the tool watches
GitHub daily and estimates how many days remain before a clone closes the
window, based on how fast repos on the same topic gained stars after
comparable past events. Nobody offers "time left to ship" as a signal; it
would turn the digest from a list into a decision with a deadline, which is
what this pattern actually requires.
