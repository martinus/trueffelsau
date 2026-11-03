"""The `trueffelsau` command. One subcommand per pipeline step.

Each subcommand is a small function that takes the parsed arguments and returns
the exit code. `main` takes `argv` so tests can call it without a subprocess.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

from trueffelsau import __version__, bootstrap, home

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


def _relative(path: Path, base: Path) -> str:
    return str(path.relative_to(base))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run: Command = args.run
    return run(args)
