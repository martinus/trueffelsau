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

# --- helpers and fixtures --------------------------------------------------


def write(directory: Path, name: str, sql: str = "CREATE TABLE t (id INTEGER);") -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def table_names(connection: sqlite3.Connection) -> list[str]:
    """Every table in the database, sorted, excluding SQLite's own bookkeeping.

    Lives here rather than in `trueffelsau.db` because nothing that ships calls
    it; it is what these tests assert with.
    """
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [str(row[0]) for row in rows]


@pytest.fixture
def schema(tmp_path: Path) -> Path:
    """An empty directory to put hand-written migrations in."""
    directory = tmp_path / "schema"
    directory.mkdir()
    return directory


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """A database with the real schema applied."""
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
    # Stated rather than defaulted: `lastrowid or 0` would hand back candidate 0
    # and every constraint test below would assert against a row that is not
    # there.
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def add_score(conn: sqlite3.Connection, candidate: int, weights: str = "w1") -> None:
    conn.execute(
        "INSERT INTO scores (candidate_id, week, score, weights_version, evidence_json, "
        "created_at) VALUES (?, '2026-38', 0.5, ?, '{}', '2026-09-16T00:00:00Z')",
        (candidate, weights),
    )


def add_batch(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO batches (id, model, schema_version, submitted_at, status, "
        "request_count) VALUES ('msgbatch_1', 'claude-sonnet-5', 1, 'x', 'in_progress', 10)"
    )


# --- finding and ordering migrations ---------------------------------------


def test_migrations_are_ordered_by_version(tmp_path: Path) -> None:
    write(tmp_path, "002_second.sql")
    write(tmp_path, "001_first.sql")
    write(tmp_path, "003_third.sql")
    found = db.migrations(tmp_path)
    assert [m.version for m in found] == [1, 2, 3]
    assert [m.path.name for m in found] == [
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


def test_a_migration_points_at_its_file(tmp_path: Path) -> None:
    write(tmp_path, "001_first.sql", "CREATE TABLE marker (id INTEGER);")
    found = db.migrations(tmp_path)[0]
    assert found.path.read_text(encoding="utf-8") == "CREATE TABLE marker (id INTEGER);"


@pytest.mark.parametrize("name", ["002-second.sql", "001_first.SQL", "2 second.sql"])
def test_a_misnamed_migration_is_an_error(tmp_path: Path, name: str) -> None:
    # Skipping it silently means a migration that never runs, and the first sign
    # of that is a missing table much later.
    write(tmp_path, name)
    with pytest.raises(ValueError, match=re.escape(name)):
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
    with pytest.raises(ValueError, match=re.escape("already used by 001_also_first.sql")):
        db.migrations(tmp_path)


def test_a_gap_in_the_numbering_is_an_error(tmp_path: Path) -> None:
    # 004 over a database at 002 would leave `user_version` claiming a shape the
    # schema is not in, and every later file would build on the wrong one.
    write(tmp_path, "001_first.sql")
    write(tmp_path, "002_second.sql")
    write(tmp_path, "004_fourth.sql")
    with pytest.raises(ValueError, match="migration 3 is missing"):
        db.migrations(tmp_path)


def test_only_sql_files_are_addressed_to_us(tmp_path: Path) -> None:
    # The directory may pick up a README or, if it is ever made a package, an
    # `__init__.py` and a `__pycache__`. None of those is a migration, and none
    # of them should stop the run.
    write(tmp_path, "001_first.sql")
    (tmp_path / "README.md").write_text("notes", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    assert [m.version for m in db.migrations(tmp_path)] == [1]


def test_the_shipped_schema_is_discoverable() -> None:
    # Reads the package's own directory rather than a fixture: the wheel the
    # systemd unit runs has no repository root, so this is the path that has to
    # keep working after packaging changes.
    found = db.migrations()
    assert [m.version for m in found] == [1]
    assert found[0].path.name == "001_init.sql"
    assert db.schema_dir().is_dir()


# --- connect() -------------------------------------------------------------


def test_connect_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "deeper" / "radar.db"
    db.connect(path)
    assert path.exists()


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_foreign_keys_survive_a_migration(conn: sqlite3.Connection) -> None:
    # `migrate` changes the connection's transaction mode and puts it back. A
    # PRAGMA lost along the way would disable every foreign key in the schema
    # for the rest of the run, silently.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_returns_rows_addressable_by_name(conn: sqlite3.Connection) -> None:
    add_event(conn)
    row = conn.execute("SELECT source, raw_hash FROM events").fetchone()
    assert row["source"] == "hn"
    assert row["raw_hash"] == "h1"


# --- migrate() -------------------------------------------------------------


def test_a_fresh_database_reports_version_zero(tmp_path: Path) -> None:
    assert db.user_version(db.connect(tmp_path / "radar.db")) == 0


def test_migrate_applies_and_records_the_version(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    assert db.migrate(connection) == 1
    assert db.user_version(connection) == 1


def test_migrate_creates_every_table_the_spec_names(conn: sqlite3.Connection) -> None:
    assert table_names(conn) == [
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
    assert table_names(connection).count("events") == 1


def test_migrate_skips_what_is_already_applied(tmp_path: Path, schema: Path) -> None:
    connection = db.connect(tmp_path / "radar.db")
    write(schema, "001_first.sql", "CREATE TABLE one (id INTEGER);")
    db.migrate(connection, schema)

    # Re-running 001 as written would fail with "table one already exists", so a
    # green second call is the evidence it was skipped rather than re-applied.
    write(schema, "002_second.sql", "CREATE TABLE two (id INTEGER);")
    assert db.migrate(connection, schema) == 2
    assert table_names(connection) == ["one", "two"]


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
    assert table_names(connection) == []


def test_a_failure_keeps_the_migrations_that_already_succeeded(
    tmp_path: Path, schema: Path
) -> None:
    # The module promises a partly migrated database finishes where it stopped.
    # Without a commit per migration, all of them share one transaction and the
    # rollback for the last would throw away the ones that worked.
    connection = db.connect(tmp_path / "radar.db")
    write(schema, "001_first.sql", "CREATE TABLE one (id INTEGER);")
    write(schema, "002_second.sql", "CREATE TABLE bad (this is not sql);")
    with pytest.raises(sqlite3.Error):
        db.migrate(connection, schema)

    assert db.user_version(connection) == 1
    assert table_names(connection) == ["one"]


def test_migrate_leaves_the_connection_as_it_found_it(tmp_path: Path) -> None:
    # `migrate` changes the transaction mode to own its commits. Leaving it
    # changed would silently alter every later insert on this connection: the
    # collectors would stop persisting without an explicit commit.
    connection = db.connect(tmp_path / "radar.db")
    before = connection.autocommit
    db.migrate(connection)
    assert connection.autocommit == before


def test_a_final_statement_needs_no_trailing_semicolon(tmp_path: Path, schema: Path) -> None:
    # The transaction is on the connection, not spliced around the text, so the
    # file is run exactly as written.
    connection = db.connect(tmp_path / "radar.db")
    write(schema, "001_first.sql", "CREATE TABLE one (id INTEGER)")
    assert db.migrate(connection, schema) == 1
    assert table_names(connection) == ["one"]


def test_a_database_newer_than_this_code_is_refused(tmp_path: Path, schema: Path) -> None:
    # Otherwise an old checkout reports "nothing to do" against a database it
    # does not understand, then writes rows against a shape it does not know.
    connection = db.connect(tmp_path / "radar.db")
    connection.execute("PRAGMA user_version = 7")
    write(schema, "001_first.sql")
    with pytest.raises(ValueError, match="older than the database"):
        db.migrate(connection, schema)


# --- what the schema itself refuses ----------------------------------------


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


def test_evidence_cannot_point_at_an_event_that_does_not_exist(conn: sqlite3.Connection) -> None:
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
    add_batch(conn)
    with pytest.raises(sqlite3.IntegrityError):
        add_batch(conn)
