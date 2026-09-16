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
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

#: Migration file names: a zero-padded number, an underscore, a name. The number
#: is the `user_version` the file brings the database to, so it starts at 1 --
#: a fresh SQLite database reports 0, which must mean "nothing applied yet".
MIGRATION_PATTERN = re.compile(r"^(\d+)_[a-z0-9_]+\.sql$")

#: Where the files live, inside the package rather than beside it. A wheel
#: installed into a virtualenv has no repository root to read from, and the
#: weekly run is a systemd unit pointed at exactly such an install.
SCHEMA_PACKAGE = "trueffelsau"
SCHEMA_DIR = "schema"


@dataclass(frozen=True)
class Migration:
    """One numbered schema file."""

    version: int
    name: str
    sql: str


def schema_dir() -> Path:
    """The directory holding the migration files."""
    return Path(str(files(SCHEMA_PACKAGE))) / SCHEMA_DIR


def migrations(directory: Path | None = None) -> list[Migration]:
    """Every migration, lowest version first.

    A file whose name does not parse is an error rather than something to skip.
    `002_add_scores.SQL` or `2_add_scores.sql` would otherwise be ignored in
    silence, and the first anyone would know of it is a missing table.
    """
    source = schema_dir() if directory is None else directory
    found: dict[int, Migration] = {}

    for path in sorted(source.iterdir()):
        if path.name.startswith("__") or path.is_dir():
            continue

        match = MIGRATION_PATTERN.match(path.name)
        if match is None:
            raise ValueError(f"{path.name}: not a migration, expected NNN_name.sql")

        version = int(match.group(1))
        if version < 1:
            raise ValueError(f"{path.name}: version must start at 1, found {version}")

        previous = found.get(version)
        if previous is not None:
            raise ValueError(f"{path.name}: version {version} already used by {previous.name}")

        found[version] = Migration(
            version=version,
            name=path.name,
            sql=path.read_text(encoding="utf-8"),
        )

    ordered = [found[version] for version in sorted(found)]
    _check_contiguous(ordered)
    return ordered


def _check_contiguous(ordered: list[Migration]) -> None:
    """Refuse a gap in the numbering.

    A missing 003 means a file was lost or never committed. Applying 004 over a
    database at 002 would leave `user_version` claiming a state the schema is
    not in, and every later migration would be applied to the wrong shape.
    """
    for expected, migration in enumerate(ordered, start=1):
        if migration.version != expected:
            raise ValueError(f"migration {expected} is missing, found {migration.name} instead")


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
    current = user_version(connection)

    for migration in migrations(directory):
        if migration.version <= current:
            continue

        # `executescript` does no implicit transaction control of its own and
        # commits any pending one before it starts, so `with connection:` would
        # not wrap this -- the BEGIN and COMMIT have to be in the script. SQLite
        # keeps DDL and `user_version` inside a transaction, so both roll back
        # together. The version is an int from a matched pattern; a PRAGMA takes
        # no parameter binding.
        try:
            connection.executescript(
                f"BEGIN;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;"
            )
        except Exception:
            # The script stopped before its COMMIT, so the transaction is still
            # open and everything it did so far is still visible on this
            # connection. Without this rollback a caller that catches the error
            # goes on reading half a schema as though it were whole.
            connection.rollback()
            raise

        current = migration.version

    return current


def table_names(connection: sqlite3.Connection) -> list[str]:
    """Every table in the database, sorted. Excludes SQLite's own bookkeeping."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [str(row[0]) for row in rows]
