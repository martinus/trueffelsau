"""`trueffelsau init`: create the runtime home and fill it with templates.

The templates ship inside the package, in `trueffelsau/config/`, for the same
reason the migrations do: the weekly run uses an installed wheel, which has no
repository checkout beside it to copy from.

`init` never overwrites. Once copied, `weights.toml` and `watchlist.txt` are
the owner's judgment and `env` holds their API keys; a second `init` that
replaced any of them with a template would destroy exactly the files that
cannot be recreated. So every file is created with an exclusive open, and a
file that already exists is reported as kept, whatever it contains.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from trueffelsau import db, home

#: Template file name in the package -> file name in the home's `config/`.
#: The `.example` files are renamed on the way: the template is public, the
#: copy is private, and the private name is what `.gitignore` lists.
TEMPLATES: dict[str, str] = {
    "settings.toml": "settings.toml",
    "weights.example.toml": "weights.toml",
    "watchlist.example.txt": "watchlist.txt",
    "hn_terms.txt": "hn_terms.txt",
    "subreddits.txt": "subreddits.txt",
    "pe_firms.txt": "pe_firms.txt",
    "CHANGELOG.md": "CHANGELOG.md",
}

#: The first content of a new `env` file. Every key is commented out, so the
#: file parses before the owner has pasted anything into it.
ENV_TEMPLATE = """\
# API keys for trueffelsau. One KEY=value line per key.
# systemd reads this file too (EnvironmentFile=), so keep that format.
# This file is private. Keep its mode at 600.

# Needed for `trueffelsau classify`. Create a key in the Claude Console.
#ANTHROPIC_API_KEY=

# Optional. Raises the GitHub rate limit for the clone check.
#GITHUB_TOKEN=

# Optional. Needed only for the Flippa collector.
#APIFY_TOKEN=
"""

#: Only the owner may read the home and the key file: the home holds collected
#: text and judgments about named companies (CLAUDE.md section 15).
_PRIVATE_DIR = 0o700
_PRIVATE_FILE = 0o600


@dataclass
class InitReport:
    """What `init_home` did, for the CLI to print."""

    base: Path
    created: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    db_version: int = 0


def template_dir() -> Path:
    return Path(__file__).parent / "config"


def init_home(base: Path) -> InitReport:
    """Create the home layout, copy missing templates, migrate the database.

    Safe to run again: it only adds what is missing, and the migration skips
    what the database has already seen.
    """
    report = InitReport(base=base)

    base.mkdir(mode=_PRIVATE_DIR, parents=True, exist_ok=True)
    for directory in (
        home.config_dir(base),
        home.data_dir(base),
        home.digests_dir(base),
        home.inbox_dir(base),
    ):
        directory.mkdir(mode=_PRIVATE_DIR, exist_ok=True)

    source = template_dir()
    for template, target in TEMPLATES.items():
        text = (source / template).read_text(encoding="utf-8")
        _create(home.config_dir(base) / target, text, 0o644, report)

    _create(home.env_path(base), ENV_TEMPLATE, _PRIVATE_FILE, report)

    connection = db.connect(home.db_path(base))
    try:
        report.db_version = db.migrate(connection)
    finally:
        connection.close()

    return report


def _create(path: Path, text: str, mode: int, report: InitReport) -> None:
    """Write `text` to a new file at `path`, or record that the file exists.

    `O_EXCL` makes "does it exist" and "create it" one step. Checking first and
    writing second leaves a moment in which another `init` creates the file
    and this one overwrites it.
    """
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        report.kept.append(path)
        return

    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    report.created.append(path)
