"""Tests for `trueffelsau.events`: the hash and the duplicate-free insert."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trueffelsau import db
from trueffelsau.events import Event, insert_events, parse_utc, utc_text


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = db.connect(tmp_path / "radar.db")
    db.migrate(connection)
    return connection


def event(url: str = "https://example.com/1", text: str = "text", **fields: str) -> Event:
    values = {
        "source": "hn",
        "source_url": url,
        "product": None,
        "fetched_at": "2026-11-03T10:00:00Z",
        "event_at": "2026-11-02T09:00:00Z",
        "raw_text": text,
    }
    values.update(fields)
    return Event(**values)  # type: ignore[arg-type]


def count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM events").fetchone()[0])


def test_insert_returns_the_number_of_new_rows(conn: sqlite3.Connection) -> None:
    assert insert_events(conn, [event("https://a"), event("https://b")]) == 2
    assert count(conn) == 2


def test_a_rerun_adds_nothing(conn: sqlite3.Connection) -> None:
    insert_events(conn, [event("https://a"), event("https://b")])
    assert insert_events(conn, [event("https://a"), event("https://b")]) == 0
    assert count(conn) == 2


def test_the_same_text_fetched_later_is_the_same_event(conn: sqlite3.Connection) -> None:
    insert_events(conn, [event(fetched_at="2026-11-03T10:00:00Z")])
    assert insert_events(conn, [event(fetched_at="2026-11-10T10:00:00Z")]) == 0


def test_changed_text_at_the_same_url_is_a_new_event(conn: sqlite3.Connection) -> None:
    insert_events(conn, [event(text="before edit")])
    assert insert_events(conn, [event(text="after edit")]) == 1


def test_the_stored_row_holds_every_field(conn: sqlite3.Connection) -> None:
    stored = event(product="Acme")
    insert_events(conn, [stored])
    row = conn.execute("SELECT * FROM events").fetchone()
    assert row["source"] == "hn"
    assert row["source_url"] == "https://example.com/1"
    assert row["product"] == "Acme"
    assert row["fetched_at"] == "2026-11-03T10:00:00Z"
    assert row["event_at"] == "2026-11-02T09:00:00Z"
    assert row["raw_text"] == "text"
    assert row["raw_hash"] == stored.raw_hash


def test_a_missing_required_field_is_an_error_not_a_duplicate(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        insert_events(conn, [event(fetched_at=None)])  # type: ignore[arg-type]


def test_field_borders_cannot_collide() -> None:
    # Plain concatenation would give "https://ab" + "c" == "https://a" + "bc".
    assert event("https://ab", "c").raw_hash != event("https://a", "bc").raw_hash


def test_the_source_is_part_of_the_hash() -> None:
    assert event(source="hn").raw_hash != event(source="reddit").raw_hash


def test_utc_text_converts_to_utc() -> None:
    vienna = timezone(timedelta(hours=1))
    assert utc_text(datetime(2026, 11, 3, 11, 0, 5, 999, tzinfo=vienna)) == "2026-11-03T10:00:05Z"


def test_utc_text_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive"):
        utc_text(datetime(2026, 11, 3))


def test_parse_utc_inverts_utc_text() -> None:
    moment = datetime(2026, 11, 3, 10, 0, 5, tzinfo=UTC)
    assert parse_utc(utc_text(moment)) == moment
