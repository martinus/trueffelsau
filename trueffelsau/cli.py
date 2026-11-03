"""The `trueffelsau` command. One subcommand per pipeline step.

Each subcommand is a small function that takes the parsed arguments and returns
the exit code. `main` takes `argv` so tests can call it without a subprocess.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from trueffelsau import __version__, bootstrap, collectors, db, home, settings
from trueffelsau.collectors.base import Context
from trueffelsau.http import Fetcher

Command = Callable[[argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trueffelsau",
        description="Find product ideas with a high chance of success, weekly.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    init = commands.add_parser(
        "init",
        help="create the runtime home, copy config templates, create the database",
        description=(
            "Create the runtime home (TRUEFFELSAU_HOME, default "
            "~/.local/share/trueffelsau). Existing files are never overwritten."
        ),
    )
    init.set_defaults(run=run_init)

    collect = commands.add_parser(
        "collect",
        help="fetch new events from the sources",
        description=(
            "Fetch new events from every source, or from one. A source that ran "
            "without errors less than collector_min_hours ago is skipped. Exits "
            "with 1 when a source failed."
        ),
    )
    collect.add_argument(
        "--source", choices=sorted(collectors.COLLECTORS), help="run only this source"
    )
    collect.set_defaults(run=run_collect)

    return parser


def run_init(args: argparse.Namespace) -> int:
    report = bootstrap.init_home(home.home())

    print(f"home: {report.base}")
    for path in report.created:
        print(f"created: {_relative(path, report.base)}")
    for path in report.kept:
        print(f"kept:    {_relative(path, report.base)}")
    database = _relative(home.db_path(report.base), report.base)
    print(f"database: {database}, version {report.db_version}")
    return 0


def run_collect(args: argparse.Namespace) -> int:
    base = home.home()
    database = home.db_path(base)
    # `db.connect` would create an empty file, and every source would then fail
    # on its missing term list. One clear line is better.
    if not database.exists():
        print(f"no database at {database}: run `trueffelsau init` first", file=sys.stderr)
        return 1

    loaded = settings.load(settings.settings_path(base))
    connection = db.connect(database)
    try:
        db.migrate(connection)
        ctx = Context(
            connection=connection,
            fetcher=Fetcher(loaded),
            settings=loaded,
            config_dir=home.config_dir(base),
            clock=lambda: datetime.now(UTC),
        )
        names = [args.source] if args.source else list(collectors.COLLECTORS)
        failed = False
        for name in names:
            report = collectors.run_collector(name, ctx)
            print(format_report(report))
            failed = failed or report.error is not None
    finally:
        connection.close()
    return 1 if failed else 0


def format_report(report: collectors.RunReport) -> str:
    """One line per source: counts, a skip, or counts and the failure."""
    if report.skipped_after_hours is not None:
        return f"{report.source}: skipped, last run {report.skipped_after_hours:.0f} h ago"
    line = f"{report.source}: {report.events_new} new, {report.events_seen} seen"
    if report.error is not None:
        line += f"; failed: {report.error}"
    return line


def _relative(path: Path, base: Path) -> str:
    return str(path.relative_to(base))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run: Command = args.run
    return run(args)
