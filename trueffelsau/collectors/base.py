"""What a collector gets and what it gives back.

Separate from the package `__init__` so a collector module can import these
without importing the registry that imports it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trueffelsau.events import Event
from trueffelsau.http import Fetcher
from trueffelsau.settings import Settings


@dataclass(frozen=True)
class Context:
    """Everything a collector may use. Tests pass a fake fetcher and clock."""

    connection: sqlite3.Connection
    fetcher: Fetcher
    settings: Settings
    config_dir: Path
    clock: Callable[[], datetime]


@dataclass(frozen=True)
class Collected:
    """A collector's result.

    `error` is set when part of the work failed but the rest is worth keeping,
    for example one search term out of twenty. The run is then recorded as
    failed, so the next one is not skipped and looks back far enough to fetch
    what this one missed.
    """

    events: list[Event]
    error: str | None = None


#: A collector: fetch everything that appeared after `since` (an aware datetime).
Collect = Callable[[Context, datetime], Collected]
