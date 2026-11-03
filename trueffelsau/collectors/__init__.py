"""The collectors, one per source, and `run_collector`, which runs one.

`run_collector` holds the rules every source shares, so a collector module only
fetches and parses:

- A source with a clean run (finished, no error) that started less than
  `collector_min_hours` ago is skipped. CLAUDE.md section 12: at most one run a
  day against services other people run.
- Each run gets a `collector_runs` row when it starts, completed when it ends.
  A run that never completes (the process was killed) has no `finished_at` and
  does not count as clean.
- A collector looks back to the start of the last clean run, with an hour of
  overlap for items the search index added late. The first run looks back
  `first_run_days`. Overlap is free: `insert_events` ignores what it has.
- An exception becomes the run's `error` and the caller goes on with the next
  source.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from trueffelsau.collectors import hn
from trueffelsau.collectors.base import Collect, Context
from trueffelsau.events import insert_events, parse_utc, utc_text

#: Name to collector. The name is the `source` column of every row it writes.
COLLECTORS: dict[str, Collect] = {
    hn.SOURCE: hn.collect,
}

#: How far before the last clean start the next run looks back.
OVERLAP = timedelta(hours=1)


@dataclass(frozen=True)
class RunReport:
    """What one `run_collector` call did, for the CLI to print."""

    source: str
    events_new: int = 0
    events_seen: int = 0
    error: str | None = None
    #: Hours since the last clean run, set only when this run was skipped.
    skipped_after_hours: float | None = None


def run_collector(
    name: str, ctx: Context, collectors: dict[str, Collect] | None = None
) -> RunReport:
    """Run the collector `name` unless it ran recently. Never raises for a
    collector's failure; an unknown name raises `KeyError`."""
    collect = (COLLECTORS if collectors is None else collectors)[name]
    connection = ctx.connection
    now = ctx.clock()

    last = _last_clean_start(connection, name)
    if last is not None:
        since_last = now - parse_utc(last)
        if since_last < timedelta(hours=ctx.settings.collector_min_hours):
            return RunReport(name, skipped_after_hours=since_last / timedelta(hours=1))
        since = parse_utc(last) - OVERLAP
    else:
        since = now - timedelta(days=ctx.settings.first_run_days)

    cursor = connection.execute(
        "INSERT INTO collector_runs (source, started_at) VALUES (?, ?)", (name, utc_text(now))
    )
    run_id = cursor.lastrowid
    connection.commit()

    try:
        collected = collect(ctx, since)
        # Two search terms can find the same item. Counted once, so "seen"
        # means "stored by an earlier run".
        events = list({event.raw_hash: event for event in collected.events}.values())
        new = insert_events(connection, events)
    except Exception as error:
        connection.rollback()
        message = f"{type(error).__name__}: {error}"
        _finish(ctx, run_id, 0, message)
        return RunReport(name, error=message)

    _finish(ctx, run_id, new, collected.error)
    return RunReport(name, new, len(events) - new, collected.error)


def _last_clean_start(connection: sqlite3.Connection, source: str) -> str | None:
    row = connection.execute(
        "SELECT max(started_at) FROM collector_runs"
        " WHERE source = ? AND finished_at IS NOT NULL AND error IS NULL",
        (source,),
    ).fetchone()
    return None if row[0] is None else str(row[0])


def _finish(ctx: Context, run_id: int | None, events_new: int, error: str | None) -> None:
    """Complete the run row. Commits, together with any inserted events."""
    ctx.connection.execute(
        "UPDATE collector_runs SET finished_at = ?, events_new = ?, error = ? WHERE id = ?",
        (utc_text(ctx.clock()), events_new, error, run_id),
    )
    ctx.connection.commit()
