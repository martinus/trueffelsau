"""Tests for `run_collector`: skip, look-back, run rows, and fail-soft.

The collector here is a fake that records the `since` it was given and returns
what the test tells it to, so these tests are about the shared rules only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trueffelsau import db
from trueffelsau.collectors import OVERLAP, RunReport, run_collector
from trueffelsau.collectors.base import Collect, Collected, Context
from trueffelsau.events import Event
from trueffelsau.http import Fetcher
from trueffelsau.settings import Settings

NOW = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)


def event(number: int) -> Event:
    return Event("fake", f"https://example.com/{number}", None, "2026-11-03T18:00:00Z", None, "t")


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@dataclass
class FakeCollector:
    """Returns the next scripted result, or raises it."""

    results: list[Collected | Exception]
    since: list[datetime] = field(default_factory=list)

    def __call__(self, ctx: Context, since: datetime) -> Collected:
        self.since.append(since)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@dataclass
class Harness:
    ctx: Context
    clock: Clock
    collector: FakeCollector

    def run(self) -> RunReport:
        collectors: dict[str, Collect] = {"fake": self.collector}
        return run_collector("fake", self.ctx, collectors)

    def runs(self) -> list[sqlite3.Row]:
        return self.ctx.connection.execute("SELECT * FROM collector_runs ORDER BY id").fetchall()

    def event_count(self) -> int:
        return int(self.ctx.connection.execute("SELECT count(*) FROM events").fetchone()[0])


def harness(tmp_path: Path, *results: Collected | Exception) -> Harness:
    connection = db.connect(tmp_path / "radar.db")
    db.migrate(connection)
    settings = Settings(collector_min_hours=20, first_run_days=14)
    clock = Clock()
    fetcher = Fetcher(settings, send=lambda *args: pytest.fail("no network in these tests"))
    ctx = Context(connection, fetcher, settings, tmp_path, clock)
    return Harness(ctx, clock, FakeCollector(list(results)))


def test_a_run_stores_events_and_a_complete_run_row(tmp_path: Path) -> None:
    h = harness(tmp_path, Collected([event(1), event(2)]))
    h.clock.now = NOW

    report = h.run()

    assert report == run_report(new=2)
    [row] = h.runs()
    assert row["source"] == "fake"
    assert row["started_at"] == "2026-11-03T18:00:00Z"
    assert row["finished_at"] == "2026-11-03T18:00:00Z"
    assert row["events_new"] == 2
    assert row["error"] is None
    assert h.event_count() == 2


def test_the_first_run_looks_back_first_run_days(tmp_path: Path) -> None:
    h = harness(tmp_path, Collected([]))
    h.run()
    assert h.collector.since == [NOW - timedelta(days=14)]


def test_a_recent_clean_run_skips_the_source(tmp_path: Path) -> None:
    h = harness(tmp_path, Collected([event(1)]), Collected([event(2)]))
    h.run()
    h.clock.now = NOW + timedelta(hours=19)

    report = h.run()

    assert report == run_report(skipped=19.0)
    assert len(h.runs()) == 1
    assert len(h.collector.since) == 1


def test_a_later_run_looks_back_to_the_last_clean_start(tmp_path: Path) -> None:
    h = harness(tmp_path, Collected([]), Collected([]))
    h.run()
    h.clock.now = NOW + timedelta(hours=20)
    h.run()
    assert h.collector.since[1] == NOW - OVERLAP


def test_a_rerun_counts_known_events_as_seen(tmp_path: Path) -> None:
    h = harness(tmp_path, Collected([event(1)]), Collected([event(1), event(2)]))
    h.run()
    h.clock.now = NOW + timedelta(hours=20)
    assert h.run() == run_report(new=1, seen=1)
    assert h.event_count() == 2


def test_a_partial_failure_keeps_events_and_does_not_count_as_clean(tmp_path: Path) -> None:
    h = harness(
        tmp_path,
        Collected([]),
        Collected([event(1)], error="1 of 2 terms failed"),
        Collected([]),
    )
    h.run()
    h.clock.now = NOW + timedelta(hours=20)
    assert h.run() == run_report(new=1, error="1 of 2 terms failed")
    assert h.event_count() == 1
    assert h.runs()[1]["error"] == "1 of 2 terms failed"

    # One hour later: not skipped, and it looks back past the failed run.
    h.clock.now = NOW + timedelta(hours=21)
    assert h.run() == run_report()
    assert h.collector.since[2] == NOW - OVERLAP


def test_an_exception_is_recorded_and_stores_nothing(tmp_path: Path) -> None:
    h = harness(tmp_path, RuntimeError("boom"), Collected([]))

    report = h.run()

    assert report == run_report(error="RuntimeError: boom")
    [row] = h.runs()
    assert row["error"] == "RuntimeError: boom"
    assert row["finished_at"] is not None
    assert row["events_new"] == 0
    # Not a clean run, so the next one goes ahead at once.
    assert h.run() == run_report()


def test_events_from_a_failed_insert_are_rolled_back(tmp_path: Path) -> None:
    # fetched_at is not in the hash but is NOT NULL, so the insert fails after
    # the first event went in.
    broken = Event("fake", "https://example.com/x", None, None, None, "t")  # type: ignore[arg-type]
    h = harness(tmp_path, Collected([event(1), broken]))

    report = h.run()

    assert report.error is not None
    assert "IntegrityError" in report.error
    assert h.event_count() == 0


def test_an_unfinished_run_does_not_count_as_clean(tmp_path: Path) -> None:
    h = harness(tmp_path, Collected([]))
    h.ctx.connection.execute(
        "INSERT INTO collector_runs (source, started_at) VALUES ('fake', ?)",
        ("2026-11-03T17:00:00Z",),
    )
    h.ctx.connection.commit()
    assert h.run() == run_report()


def test_other_sources_do_not_count(tmp_path: Path) -> None:
    h = harness(tmp_path, Collected([]))
    h.ctx.connection.execute(
        "INSERT INTO collector_runs (source, started_at, finished_at, events_new)"
        " VALUES ('other', ?, ?, 0)",
        ("2026-11-03T17:00:00Z", "2026-11-03T17:01:00Z"),
    )
    h.ctx.connection.commit()
    assert h.run() == run_report()


def test_an_unknown_source_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        run_collector("nope", harness(tmp_path).ctx)


def run_report(
    new: int = 0, seen: int = 0, error: str | None = None, skipped: float | None = None
) -> RunReport:
    return RunReport("fake", new, seen, error, skipped)
