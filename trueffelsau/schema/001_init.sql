-- The tables CLAUDE.md section 7 names, plus four this build needs to be
-- resumable. Applied by `trueffelsau.db.migrate`, which runs each numbered file
-- once and records how far it got in `PRAGMA user_version`.
--
-- Two rules run through all of them.
--
-- Nothing is ever deleted. A candidate that loses its window is marked
-- `killed`, never removed: section 11 compares what the filter predicted with
-- what actually happened, and a deleted row is a comparison nobody can make
-- again.
--
-- Every derived row carries what produced it. `scores.evidence_json` holds the
-- quotes and the clone data behind a number, `classifications.json` the whole
-- model answer. Section 9 is blunt about it: a score without evidence is a bug.
--
-- Times are ISO-8601 UTC strings. SQLite has no date type, and a string in one
-- fixed format sorts and compares correctly while staying readable in a
-- `sqlite3` shell, which is where these rows get inspected.

-- Raw collected text, one row per signal. Collectors rerun weekly and must not
-- duplicate what they already stored, which is what `raw_hash` is for: it is a
-- digest of source, URL and text, so `INSERT OR IGNORE` makes a rerun cheap and
-- safe. `event_at` is when the event happened and is null when a source does
-- not say; `fetched_at` is when we looked, and always known.
CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    source_url TEXT NOT NULL,
    product    TEXT,
    fetched_at TEXT NOT NULL,
    event_at   TEXT,
    raw_text   TEXT NOT NULL,
    raw_hash   TEXT NOT NULL UNIQUE
);

CREATE INDEX events_source_fetched_at ON events (source, fetched_at);

-- One row per model answer. Section 8: never overwrite an old classification,
-- add a new row -- so there is deliberately no unique constraint here. Two rows
-- for one event under different `schema_version` values are the record of a
-- prompt that changed, and re-reading the old answer is how a change gets
-- judged. `input_tokens` and `output_tokens` are what section 10 reports as the
-- week's cost; they are null for a row whose batch did not report usage.
CREATE TABLE classifications (
    id             INTEGER PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES events (id),
    model          TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    json           TEXT NOT NULL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    created_at     TEXT NOT NULL
);

CREATE INDEX classifications_event_id ON classifications (event_id);

-- A product, not an event. `product` is the normalised name, which is what
-- joins several events about the same company into one candidate, so it is
-- unique. Status moves `new` -> `watching` -> `considered` or `killed` and is
-- constrained here rather than in Python: a typo that wrote `kiled` would
-- otherwise silently drop the candidate out of every status query.
CREATE TABLE candidates (
    id         INTEGER PRIMARY KEY,
    product    TEXT NOT NULL UNIQUE,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('new', 'watching', 'considered', 'killed'))
);

-- Which events a candidate was built from. This is the link the digest walks
-- back down to print the quotes behind a score, so it is what makes section 9's
-- "auditable" true rather than aspirational.
CREATE TABLE candidate_events (
    candidate_id INTEGER NOT NULL REFERENCES candidates (id),
    event_id     INTEGER NOT NULL REFERENCES events (id),
    PRIMARY KEY (candidate_id, event_id)
);

-- One score per candidate per ISO week per weights version. Section 9 says to
-- recompute every score when the weights change and keep the old ones, so
-- `weights_version` is part of the key: a recompute adds rows beside the old
-- numbers instead of replacing them, and the two can be compared afterwards.
CREATE TABLE scores (
    id              INTEGER PRIMARY KEY,
    candidate_id    INTEGER NOT NULL REFERENCES candidates (id),
    week            TEXT NOT NULL,
    score           REAL NOT NULL,
    weights_version TEXT NOT NULL,
    evidence_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (candidate_id, week, weights_version)
);

CREATE INDEX scores_week ON scores (week);

-- What the GitHub search found. `created_at` is the repository's creation date
-- and `last_commit` its last push, both from GitHub; `checked_at` is when we
-- asked. Section 9 turns these into the clone factor, and section 4 point 6
-- into the rule that closes a window.
CREATE TABLE clones (
    id           INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates (id),
    repo         TEXT NOT NULL,
    stars        INTEGER NOT NULL,
    created_at   TEXT,
    last_commit  TEXT,
    checked_at   TEXT NOT NULL
);

CREATE INDEX clones_candidate_id ON clones (candidate_id);

-- The outcomes loop, section 11, and the part that makes the tool improve
-- rather than just report. A decision gets a follow-up date six months out;
-- `followup_result` stays null until that question is answered, which is
-- exactly the query the digest runs to ask it.
CREATE TABLE outcomes (
    id              INTEGER PRIMARY KEY,
    candidate_id    INTEGER NOT NULL REFERENCES candidates (id),
    decision        TEXT NOT NULL CHECK (decision IN ('watching', 'considered', 'killed')),
    decided_at      TEXT NOT NULL,
    note            TEXT,
    followup_at     TEXT,
    followup_result TEXT
);

CREATE INDEX outcomes_followup_at ON outcomes (followup_at);

-- A submitted Message Batches job. Not in section 7, and needed: a batch may
-- take up to 24 hours, so the id has to outlive the process that submitted it
-- or an interrupted run pays for work it can no longer collect.
CREATE TABLE batches (
    id             TEXT PRIMARY KEY,
    model          TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    submitted_at   TEXT NOT NULL,
    ended_at       TEXT,
    status         TEXT NOT NULL,
    request_count  INTEGER NOT NULL
);

-- What each collector did, so the digest can carry a collector-health line.
-- A source that quietly stopped returning rows looks exactly like a quiet week
-- in the digest; this is the table that tells them apart. `error` is null on a
-- clean run and holds the message otherwise.
CREATE TABLE collector_runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    events_new  INTEGER,
    error       TEXT
);

CREATE INDEX collector_runs_source_started_at ON collector_runs (source, started_at);
