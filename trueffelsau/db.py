"""The SQLite database, and the migrations that build it.

Schema changes arrive as numbered files in `trueffelsau/schema/`. `migrate`
applies the ones the database has not seen and records how far it got in
`PRAGMA user_version`, so running it twice is not an error and a partly migrated
database finishes where it stopped.

Migrations only ever add. CLAUDE.md section 7 forbids deleting rows, and the
same reasoning covers columns and tables: the outcomes loop compares what the
filter said months ago with what happened, and a dropped column is a comparison
nobody can make again. A later file may add a table, add a column, or backfill
one -- never drop or rewrite.

A migration file carries no transaction control of its own, and no statement
that cannot run inside a transaction (`VACUUM`, `PRAGMA journal_mode`). `migrate`
runs each file in one, so such a file fails when it is applied, with SQLite's own
message. This stays a documented convention rather than a check because the
check cannot be written honestly: refusing any file containing `BEGIN` would also
refuse `CREATE TRIGGER ... BEGIN ... END`, which is legitimate SQL.

The one case that is not merely loud is a file containing a bare `COMMIT`: its
own statements commit, `user_version` commits with them, and the migration then
raises anyway. The database ends up correct and the caller is told it failed.
Measured, not assumed -- and it is why the convention is stated here rather than
left implicit.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

#: Migration file names: a number, an underscore, a lowercase name. The number is
#: the `user_version` the file brings the database to, so it starts at 1 -- a
#: fresh SQLite database reports 0, which has to mean "nothing applied yet".
MIGRATION_PATTERN = re.compile(r"^(\d+)_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    """One numbered schema file. Its contents are read when it is applied."""

    version: int
    path: Path


def schema_dir() -> Path:
    """The directory holding the migration files.

    Inside the package rather than beside it: the weekly run is a systemd unit
    pointed at an installed wheel, which has no repository root to read from.
    """
    return Path(__file__).parent / "schema"


def migrations(directory: Path | None = None) -> list[Migration]:
    """Every migration, lowest version first.

    A file whose name does not parse is an error rather than something to skip.
    `002_add_scores.SQL` or `2-add-scores.sql` would otherwise be passed over in
    silence, and the first anyone would know of it is a missing table.
    """
    source = schema_dir() if directory is None else directory
    found: list[Migration] = []

    for path in source.iterdir():
        # The rule is "every .sql file here is a migration"; anything else is
        # not addressed to us. A misnamed migration still ends in `.sql`, so it
        # reaches the pattern below and is refused rather than passed over --
        # which is the case worth being strict about. Skipping by name instead
        # would be `__pycache__` with the label filed off. Compared
        # case-insensitively so `001_init.SQL` reaches the pattern and is
        # refused, rather than being quietly passed over as not-a-migration.
        if path.suffix.lower() != ".sql":
            continue

        match = MIGRATION_PATTERN.match(path.name)
        if match is None:
            raise ValueError(f"{path.name}: not a migration, expected NNN_name.sql")

        version = int(match.group(1))
        if version < 1:
            raise ValueError(f"{path.name}: version must start at 1, found {version}")

        found.append(Migration(version=version, path=path))

    # The name is in the sort key only to break ties, so a duplicate blames the
    # same file every time instead of depending on directory order.
    found.sort(key=lambda migration: (migration.version, migration.path.name))

    for expected, migration in enumerate(found, start=1):
        # Sorted, so a version below its position is a repeat and one above it
        # is a hole. A hole matters most: applying 004 to a database at 002
        # would leave `user_version` claiming a shape the schema is not in, and
        # every later file would build on the wrong one. Refusing version 0
        # above is what keeps `found[expected - 2]` in range here.
        if migration.version < expected:
            raise ValueError(
                f"{migration.path.name}: version {migration.version} "
                f"already used by {found[expected - 2].path.name}"
            )
        if migration.version > expected:
            raise ValueError(
                f"migration {expected} is missing, found {migration.path.name} instead"
            )

    return found


def connect(path: Path) -> sqlite3.Connection:
    """Open the database, creating the file and its parent if needed.

    Foreign keys are off by default in SQLite and are turned on per connection,
    never once in the file. Without this, `candidate_events` would happily point
    at events that do not exist and the digest would print a candidate with no
    quotes behind it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def user_version(connection: sqlite3.Connection) -> int:
    """How many migrations this database has had applied."""
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def migrate(connection: sqlite3.Connection, directory: Path | None = None) -> int:
    """Apply every migration the database has not seen. Returns the new version.

    Each file applies whole or not at all, together with its `user_version`
    bump. A half-applied schema recorded under a number that describes neither
    shape is the one failure running again cannot repair.
    """
    available = migrations(directory)
    current = user_version(connection)

    if available and current > available[-1].version:
        raise ValueError(
            f"database is at version {current} but the newest migration is "
            f"{available[-1].version}: this code is older than the database"
        )

    # `autocommit = False` is what puts the transaction on the connection rather
    # than in the SQL. Splicing `BEGIN` and `COMMIT` around the file also works,
    # but it means editing someone else's SQL to get a property the connection
    # can provide -- and it breaks on a file whose last statement has no
    # trailing semicolon, because the appended PRAGMA glues onto it.
    previous_mode = connection.autocommit
    connection.autocommit = False
    try:
        for migration in available:
            if migration.version <= current:
                continue

            try:
                connection.executescript(migration.path.read_text(encoding="utf-8"))
                # Commits with the DDL above, so the recorded version and the
                # shape it describes can never disagree. A PRAGMA takes no
                # parameter binding; the value is an int from a matched pattern.
                connection.execute(f"PRAGMA user_version = {migration.version}")
                connection.commit()
            except Exception:
                # Without this the caller that catches the error goes on reading
                # half a schema as though it were whole. `suppress` because a
                # file that committed on its own leaves nothing open, and the
                # rollback would then raise a second error over the first one --
                # which is the error actually worth seeing.
                with suppress(sqlite3.Error):
                    connection.rollback()
                raise

            current = migration.version
    finally:
        connection.autocommit = previous_mode

    return current
