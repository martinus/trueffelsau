"""Where trueffelsau keeps the owner's private state, and how it reads its secrets.

The runtime home is deliberately *outside* the checkout. `gra` puts each task in
a throw-away worktree, so a database, a digest archive or a manual CSV export
kept next to the code would be discarded with the worktree that happened to
create it. CLAUDE.md §15 also makes these files private while the code is
public, and a path outside the repository cannot be committed by accident.

Nothing here touches `os.environ` or creates directories. Both are the caller's
job: a resolver that reads process state is one that answers differently
depending on who imported it first, and a resolver that creates what it names
cannot be asked a question without leaving a directory behind.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

#: Set this to put the runtime home somewhere specific. Checked before XDG so a
#: single run can be pointed at a scratch directory without exporting anything
#: that outlives it.
HOME_VAR = "TRUEFFELSAU_HOME"

#: The freedesktop base-directory variable, honoured when `HOME_VAR` is unset.
XDG_VAR = "XDG_DATA_HOME"

#: Appended to whichever data directory wins, so the home is always named after
#: the project rather than being the data directory itself.
PROJECT_DIR = "trueffelsau"

#: Where the XDG spec says the data directory is when `XDG_VAR` is unset.
DEFAULT_DATA_DIR = ".local/share"

#: Names under the home. `env` is deliberately extensionless and flat: systemd's
#: `EnvironmentFile=` reads it directly, so the format it accepts is the format
#: `parse_env` has to accept too.
CONFIG_DIR = "config"
DATA_DIR = "data"
DIGESTS_DIR = "digests"
INBOX_DIR = "inbox"
ENV_FILE = "env"
DB_FILE = "radar.db"

#: Quote characters stripped from a value, matching what systemd accepts.
_QUOTES = "\"'"

#: Starts a comment line. Only at the start of a line: `KEY=a#b` is a value that
#: contains a hash, and systemd reads it that way too.
_COMMENT = "#"


def _from_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def home(env: Mapping[str, str] | None = None) -> Path:
    """The runtime home, as an absolute path.

    `env` defaults to the real environment; pass a mapping to ask the question
    without one. An empty value counts as unset -- `TRUEFFELSAU_HOME=` in a
    shell profile is how a variable gets cleared, and treating it as a request
    for the current directory would put the database somewhere surprising.
    """
    source = _from_env(env)

    explicit = source.get(HOME_VAR, "")
    if explicit:
        return _absolute(Path(explicit))

    xdg = source.get(XDG_VAR, "")
    if xdg:
        return _absolute(Path(xdg) / PROJECT_DIR)

    return _absolute(Path.home() / DEFAULT_DATA_DIR / PROJECT_DIR)


def _absolute(path: Path) -> Path:
    """Expand `~` and anchor a relative path to the current directory.

    Deliberately not `Path.resolve()`: that follows symlinks, and a home reached
    through one should keep the name the owner gave it, not the target's.
    """
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return Path.cwd() / expanded


def config_dir(base: Path) -> Path:
    return base / CONFIG_DIR


def data_dir(base: Path) -> Path:
    return base / DATA_DIR


def digests_dir(base: Path) -> Path:
    return base / DIGESTS_DIR


def inbox_dir(base: Path) -> Path:
    return base / INBOX_DIR


def env_path(base: Path) -> Path:
    return base / ENV_FILE


def db_path(base: Path) -> Path:
    return data_dir(base) / DB_FILE


def parse_env(text: str) -> dict[str, str]:
    """Parse the `KEY=value` lines of an environment file.

    The accepted grammar is the intersection of what systemd's
    `EnvironmentFile=` reads and what is worth writing by hand: blank lines and
    `#` comments are skipped, surrounding quotes are stripped, and the first
    `=` splits the line so a value may itself contain one.

    A line that is neither blank, a comment, nor an assignment raises rather
    than being skipped. The file holds API keys; a typo that silently dropped
    one would surface much later as an authentication error with no obvious
    cause.
    """
    values: dict[str, str] = {}

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(_COMMENT):
            continue

        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError(f"line {number}: expected KEY=value, found {raw!r}")

        name = key.strip()
        if not name:
            raise ValueError(f"line {number}: missing name before '=', found {raw!r}")

        values[name] = _unquote(value.strip())

    return values


def _unquote(value: str) -> str:
    """Strip one pair of matching surrounding quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        return value[1:-1]
    return value


def load_env(path: Path) -> dict[str, str]:
    """`parse_env` over a file, or `{}` when the file does not exist.

    Missing is not an error: every key in the file is optional until the step
    that needs it runs, and `trueffelsau init` has to work before the owner has
    pasted an API key anywhere.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return parse_env(text)
