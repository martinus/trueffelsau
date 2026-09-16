"""Tests for `trueffelsau.db` and the schema it applies.

The constraint tests are the ones worth keeping. A missing `UNIQUE` on
`events.raw_hash` does not fail: the weekly rerun quietly doubles every event,
and the first symptom is a digest counting the same signal twice.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from trueffelsau import db

# --- finding and ordering migrations ---------------------------------------


def write(directory: Path, name: str, sql: str = "CREATE TABLE t (id INTEGER);") -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_migrations_are_ordered_by_version(tmp_path: Path) -> None:
    write(tmp_path, "002_second.sql")
    write(tmp_path, "001_first.sql")
    write(tmp_path, "003_third.sql")
    assert [m.version for m in db.migrations(tmp_path)] == [1, 2, 3]
    assert [m.name for m in db.migrations(tmp_path)] == [
        "001_first.sql",
        "002_second.sql",
        "003_third.sql",
    ]


def test_migrations_sort_numerically_not_as_text(tmp_path: Path) -> None:
    # Unpadded on purpose. "10_step.sql" sorts before "1_step.sql" by name, so a
    # run that trusted the directory listing would try to apply 10 first. Zero
    # padding hides that, which is exactly why this test does not use it: the
    # number in the name is what the order means, not the string.
    for version in range(1, 11):
        write(tmp_path, f"{version}_step.sql")
    assert [m.version for m in db.migrations(tmp_path)] == list(range(1, 11))


def test_migrations_carry_the_file_contents(tmp_path: Path) -> None:
    write(tmp_path, "001_first.sql", "CREATE TABLE marker (id INTEGER);")
    assert db.migrations(tmp_path)[0].sql == "CREATE TABLE marker (id INTEGER);"


def test_a_misnamed_file_is_an_error(tmp_path: Path) -> None:
    # Skipping it silently means a migration that never runs, and the first sign
    # of that is a missing table much later.
    write(tmp_path, "001_first.sql")
    write(tmp_path, "002-second.sql")
    with pytest.raises(ValueError, match=re.escape("002-second.sql")):
        db.migrations(tmp_path)


def test_an_uppercase_extension_is_an_error(tmp_path: Path) -> None:
    write(tmp_path, "001_first.SQL")
    with pytest.raises(ValueError, match=re.escape("001_first.SQL")):
        db.migrations(tmp_path)


def test_version_zero_is_an_error(tmp_path: Path) -> None:
    # A fresh database reports `user_version` 0, so 0 has to mean "nothing
    # applied". A migration numbered 0 would never be applied at all.
    write(tmp_path, "000_zeroth.sql")
    with pytest.raises(ValueError, match="must start at 1"):
        db.migrations(tmp_path)


def test_a_repeated_version_is_an_error(tmp_path: Path) -> None:
    write(tmp_path, "001_first.sql")
    write(tmp_path, "001_also_first.sql")
    with pytest.raises(ValueError, match="already used"):
        db.migrations(tmp_path)


def test_a_gap_in_the_numbering_is_an_error(tmp_path: Path) -> None:
    # 004 over a database at 002 would leave `user_version` claiming a shape the
    # schema is not in, and every later file would build on the wrong one.
    write(tmp_path, "001_first.sql")
    write(tmp_path, "002_second.sql")
    write(tmp_path, "004_fourth.sql")
    with pytest.raises(ValueError, match="migration 3 is missing"):
        db.migrations(tmp_path)


def test_dunder_files_and_directories_are_not_migrations(tmp_path: Path) -> None:
    write(tmp_path, "001_first.sql")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    assert [m.version for m in db.migrations(tmp_path)] == [1]


def test_the_shipped_schema_is_discoverable() -> None:
    # Reads the package's own directory rather than a fixture: the wheel the
    # systemd unit runs has no repository root, so this is the path that has to
    # keep working after packaging changes.
    found = db.migrations()
    assert [m.version for m in found] == [1]
    assert found[0].name == "001_init.sql"
    assert db.schema_dir().is_dir()


# --- connect() -------------------------------------------------------------


def test_connect_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "deeper" / "radar.db"
    db.connect(path)
    assert path.exists()


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_returns_rows_addressable_by_name(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    db.migrate(connection)
    connection.execute(
        "INSERT INTO events (source, source_url, fetched_at, raw_text, raw_hash) "
        "VALUES ('hn', 'https://example.test/1', '2026-09-16T00:00:00Z', 'text', 'h1')"
    )
    row = connection.execute("SELECT source, raw_hash FROM events").fetchone()
    assert row["source"] == "hn"
    assert row["raw_hash"] == "h1"


# --- migrate() -------------------------------------------------------------


def test_a_fresh_database_reports_version_zero(tmp_path: Path) -> None:
    assert db.user_version(db.connect(tmp_path / "radar.db")) == 0


def test_migrate_applies_and_records_the_version(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    assert db.migrate(connection) == 1
    assert db.user_version(connection) == 1


def test_migrate_creates_every_table_the_spec_names(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    db.migrate(connection)
    assert db.table_names(connection) == [
        "batches",
        "candidate_events",
        "candidates",
        "classifications",
        "clones",
        "collector_runs",
        "events",
        "outcomes",
        "scores",
    ]


def test_migrate_runs_twice_without_complaint(tmp_path: Path) -> None:
    # `trueffelsau init` is safe to rerun, and the weekly job calls this every
    # time rather than checking first.
    connection = db.connect(tmp_path / "radar.db")
    db.migrate(connection)
    assert db.migrate(connection) == 1
    assert db.table_names(connection).count("events") == 1


def test_migrate_skips_what_is_already_applied(tmp_path: Path, schema: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    write(schema, "001_first.sql", "CREATE TABLE one (id INTEGER);")
    db.migrate(connection, schema)

    # Re-running 001 as written would fail with "table one already exists", so a
    # green second call is the evidence it was skipped rather than re-applied.
    write(schema, "002_second.sql", "CREATE TABLE two (id INTEGER);")
    assert db.migrate(connection, schema) == 2
    assert db.table_names(connection) == ["one", "two"]


def test_a_failed_migration_leaves_no_trace(tmp_path: Path, schema: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    write(
        schema,
        "001_first.sql",
        "CREATE TABLE good (id INTEGER);\nCREATE TABLE bad (this is not sql);",
    )
    with pytest.raises(sqlite3.Error):
        db.migrate(connection, schema)

    assert db.user_version(connection) == 0
    assert db.table_names(connection) == []


@pytest.fixture
def schema(tmp_path: Path) -> Path:
    directory = tmp_path / "schema"
    directory.mkdir()
    return directory


# --- what the schema itself refuses ----------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = db.connect(tmp_path / "radar.db")
    db.migrate(connection)
    return connection


def add_event(conn: sqlite3.Connection, raw_hash: str = "h1") -> None:
    conn.execute(
        "INSERT INTO events (source, source_url, fetched_at, raw_text, raw_hash) "
        "VALUES ('hn', 'https://example.test/1', '2026-09-16T00:00:00Z', 'text', ?)",
        (raw_hash,),
    )


def add_candidate(conn: sqlite3.Connection, product: str = "acme") -> int:
    cursor = conn.execute(
        "INSERT INTO candidates (product, first_seen, last_seen, status) "
        "VALUES (?, '2026-09-16', '2026-09-16', 'new')",
        (product,),
    )
    return int(cursor.lastrowid or 0)


def test_the_same_signal_cannot_be_stored_twice(conn: sqlite3.Connection) -> None:
    # This is what makes a collector safe to rerun (section 6).
    add_event(conn, "h1")
    with pytest.raises(sqlite3.IntegrityError):
        add_event(conn, "h1")


def test_two_different_signals_are_both_kept(conn: sqlite3.Connection) -> None:
    add_event(conn, "h1")
    add_event(conn, "h2")
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_a_candidate_status_outside_the_spec_is_refused(conn: sqlite3.Connection) -> None:
    # `kiled` would otherwise drop the candidate out of every status query
    # without failing anything.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidates (product, first_seen, last_seen, status) "
            "VALUES ('acme', '2026-09-16', '2026-09-16', 'kiled')"
        )


@pytest.mark.parametrize("status", ["new", "watching", "considered", "killed"])
def test_every_status_the_spec_names_is_accepted(conn: sqlite3.Connection, status: str) -> None:
    conn.execute(
        "INSERT INTO candidates (product, first_seen, last_seen, status) VALUES (?, 'x', 'x', ?)",
        (status, status),
    )


def test_one_product_is_one_candidate(conn: sqlite3.Connection) -> None:
    add_candidate(conn, "acme")
    with pytest.raises(sqlite3.IntegrityError):
        add_candidate(conn, "acme")


def test_evidence_cannot_point_at_an_event_that_does_not_exist(
    conn: sqlite3.Connection,
) -> None:
    # Without this the digest can print a candidate whose quotes are unreachable,
    # which section 9 calls a bug.
    candidate = add_candidate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate_events (candidate_id, event_id) VALUES (?, 999)",
            (candidate,),
        )


def test_an_event_is_linked_to_a_candidate_only_once(conn: sqlite3.Connection) -> None:
    candidate = add_candidate(conn)
    add_event(conn)
    event = conn.execute("SELECT id FROM events").fetchone()["id"]
    conn.execute(
        "INSERT INTO candidate_events (candidate_id, event_id) VALUES (?, ?)",
        (candidate, event),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate_events (candidate_id, event_id) VALUES (?, ?)",
            (candidate, event),
        )


def add_score(conn: sqlite3.Connection, candidate: int, weights: str = "w1") -> None:
    conn.execute(
        "INSERT INTO scores (candidate_id, week, score, weights_version, evidence_json, "
        "created_at) VALUES (?, '2026-38', 0.5, ?, '{}', '2026-09-16T00:00:00Z')",
        (candidate, weights),
    )


def test_a_week_is_scored_once_per_weights_version(conn: sqlite3.Connection) -> None:
    candidate = add_candidate(conn)
    add_score(conn, candidate, "w1")
    with pytest.raises(sqlite3.IntegrityError):
        add_score(conn, candidate, "w1")


def test_new_weights_score_the_same_week_again(conn: sqlite3.Connection) -> None:
    # Section 9: recompute when the weights change, and keep the old numbers so
    # the two can be compared.
    candidate = add_candidate(conn)
    add_score(conn, candidate, "w1")
    add_score(conn, candidate, "w2")
    assert conn.execute("SELECT count(*) FROM scores").fetchone()[0] == 2


def test_an_outcome_records_only_a_real_decision(conn: sqlite3.Connection) -> None:
    candidate = add_candidate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO outcomes (candidate_id, decision, decided_at) VALUES (?, 'maybe', 'x')",
            (candidate,),
        )


def test_an_outcome_starts_without_its_answer(conn: sqlite3.Connection) -> None:
    # The follow-up question is asked six months later; until then the column is
    # null, and that is the query the digest runs to find it.
    candidate = add_candidate(conn)
    conn.execute(
        "INSERT INTO outcomes (candidate_id, decision, decided_at, followup_at) "
        "VALUES (?, 'considered', '2026-09-16', '2027-03-16')",
        (candidate,),
    )
    row = conn.execute("SELECT followup_result FROM outcomes").fetchone()
    assert row["followup_result"] is None


def test_a_batch_id_is_stored_once(conn: sqlite3.Connection) -> None:
    # The id outlives the process that submitted it; a duplicate would mean two
    # rows disagreeing about one job's status.
    for _ in range(2):
        try:
            conn.execute(
                "INSERT INTO batches (id, model, schema_version, submitted_at, status, "
                "request_count) VALUES ('msgbatch_1', 'claude-sonnet-5', 1, 'x', 'in_progress', 10)"
            )
        except sqlite3.IntegrityError:
            return
    pytest.fail("a repeated batch id was accepted")
