"""Collected events and how they are stored.

Every collector turns what it fetched into `Event`s and hands them to
`insert_events`. The `events` table has a unique `raw_hash`, so storing the same
text again is a no-op: a collector can be rerun, or overlap with its last run,
without creating duplicates (CLAUDE.md section 6).

The insert ignores a conflict on `raw_hash` and nothing else. `INSERT OR IGNORE`
would also skip a row that breaks `NOT NULL`, so a collector bug that left out
a required field would lose the event and count it as already stored.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

#: The one format for stored times: ISO-8601, UTC, seconds, a trailing `Z`.
#: Strings in one fixed format sort and compare like the times they name.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_text(moment: datetime) -> str:
    """`moment` as a stored time string. A naive datetime is refused."""
    if moment.tzinfo is None:
        raise ValueError(f"naive datetime {moment!r}: give a time zone")
    return moment.astimezone(UTC).strftime(TIME_FORMAT)


def parse_utc(text: str) -> datetime:
    """The inverse of `utc_text`."""
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=UTC)


@dataclass(frozen=True)
class Event:
    """One collected signal, before classification."""

    source: str
    source_url: str
    product: str | None
    fetched_at: str
    event_at: str | None
    raw_text: str

    @property
    def raw_hash(self) -> str:
        """sha256 over source, URL and text.

        Joined with NUL, which none of the three contains, so moving characters
        across a field border cannot give two different events the same hash.
        `fetched_at` is not in it: the same text fetched twice is one event.
        """
        joined = "\x00".join((self.source, self.source_url, self.raw_text))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def insert_events(connection: sqlite3.Connection, events: Iterable[Event]) -> int:
    """Store the events not stored yet. Returns how many were new.

    Does not commit; the caller decides what else belongs to the transaction.
    """
    new = 0
    for event in events:
        cursor = connection.execute(
            "INSERT INTO events"
            " (source, source_url, product, fetched_at, event_at, raw_text, raw_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (raw_hash) DO NOTHING",
            (
                event.source,
                event.source_url,
                event.product,
                event.fetched_at,
                event.event_at,
                event.raw_text,
                event.raw_hash,
            ),
        )
        new += cursor.rowcount
    return new
