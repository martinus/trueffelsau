"""The tunable numbers: model, batch size, cost caps, HTTP limits.

They live in `settings.toml` in the runtime home's `config/` directory, next to
the weights. A missing file means every default; a missing key means that
key's default. The defaults are the values in the template that `trueffelsau
init` copies, and a test keeps the two equal, so reading the template tells the
owner what an empty file does.

An unknown key is an error. `max_event_per_run = 50` with the `s` missing would
otherwise be ignored, and the run would spend up to the default instead of the
cap the owner thought they set. The same goes for a wrong type: TOML tells a
string from a number, so `batch_size = "500"` is a mistake worth stopping on.

Also here: `read_list`, the reader for the one-item-per-line files beside it
(search terms, subreddits, firm names, the watchlist).
"""

from __future__ import annotations

import dataclasses
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from trueffelsau import home

#: File name under the home's `config/` directory.
SETTINGS_FILE = "settings.toml"

#: Starts a comment line in a list file. Only at the start of a line, so a
#: watchlist URL with a `#fragment` keeps it.
_COMMENT = "#"


def _default_host_intervals() -> dict[str, float]:
    # GitHub's search API allows about 30 requests a minute with a token.
    return {"api.github.com": 2.0}


@dataclass(frozen=True)
class Settings:
    """Every setting, with its default. See `config/settings.toml` for what each does."""

    model: str = "claude-haiku-5-5"
    input_price_per_mtok: float = 0.05
    output_price_per_mtok: float = 0.25
    batch_size: int = 1000
    max_events_per_run: int = 2000
    max_chars_per_event: int = 6000
    batch_poll_seconds: float = 60.0
    collector_min_hours: float = 20.0
    first_run_days: int = 14
    user_agent: str = "trueffelsau/0.1 (+https://github.com/martinus/trueffelsau)"
    http_timeout_seconds: float = 10.0
    http_max_bytes: int = 2 * 1024 * 1024
    http_min_interval_seconds: float = 1.0
    host_min_interval_seconds: dict[str, float] = field(default_factory=_default_host_intervals)

    def min_interval(self, host: str) -> float:
        """Seconds to wait between two requests to `host`."""
        return self.host_min_interval_seconds.get(host.lower(), self.http_min_interval_seconds)


#: Settings where zero makes no sense: a batch of nothing, a timeout that fires
#: at once, a byte ceiling every response exceeds. Every other number may be 0
#: (no wait, no cost) but never negative.
_POSITIVE = frozenset(
    {"batch_size", "max_chars_per_event", "http_timeout_seconds", "http_max_bytes"}
)


def settings_path(base: Path) -> Path:
    return home.config_dir(base) / SETTINGS_FILE


def from_mapping(data: Mapping[str, object]) -> Settings:
    """Build `Settings` from parsed TOML, checking every key and type."""
    defaults = Settings()
    known = {f.name for f in dataclasses.fields(Settings)}
    values: dict[str, object] = {}

    for key, raw in data.items():
        if key not in known:
            raise ValueError(f"unknown setting {key!r}")
        values[key] = _check(key, raw, getattr(defaults, key))

    # mypy cannot see that the checks above made each value the right type.
    return dataclasses.replace(defaults, **values)  # type: ignore[arg-type]


def _check(key: str, raw: object, default: object) -> object:
    """Return `raw` as the type of `default`, or raise naming the key."""
    # `bool` is a subclass of `int`, so `batch_size = true` would pass an
    # isinstance check for int. It is never what anyone meant.
    if isinstance(raw, bool):
        raise ValueError(f"{key}: expected a number or text, found a boolean")

    if isinstance(default, str):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{key}: expected non-empty text, found {raw!r}")
        return raw

    if isinstance(default, int):
        if not isinstance(raw, int):
            raise ValueError(f"{key}: expected a whole number, found {raw!r}")
        return _check_range(key, raw)

    if isinstance(default, float):
        if not isinstance(raw, int | float):
            raise ValueError(f"{key}: expected a number, found {raw!r}")
        return _check_range(key, float(raw))

    if isinstance(default, dict):
        return _check_table(key, raw)

    raise TypeError(f"{key}: no check for settings of type {type(default).__name__}")


def _check_range[N: (int, float)](key: str, value: N) -> N:
    if value < 0:
        raise ValueError(f"{key}: must not be negative, found {value}")
    if value == 0 and key in _POSITIVE:
        raise ValueError(f"{key}: must be greater than 0")
    return value


def _check_table(key: str, raw: object) -> dict[str, float]:
    """A table of host name to seconds. Host names are compared in lowercase."""
    if not isinstance(raw, dict):
        raise ValueError(f"{key}: expected a table, found {raw!r}")

    table: dict[str, float] = {}
    for host, seconds in raw.items():
        name = f"{key}.{host}"
        if isinstance(seconds, bool) or not isinstance(seconds, int | float):
            raise ValueError(f"{name}: expected a number, found {seconds!r}")
        table[str(host).lower()] = _check_range(name, float(seconds))
    return table


def parse(text: str) -> Settings:
    """`from_mapping` over TOML text."""
    return from_mapping(tomllib.loads(text))


def load(path: Path) -> Settings:
    """`parse` over a file, or the defaults when the file does not exist.

    Missing is not an error for the same reason as in `home.load_env`: the
    commands that need no settings must work before `init` has run.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Settings()
    try:
        return parse(text)
    except (ValueError, tomllib.TOMLDecodeError) as error:
        # The file name is the part the message is missing: the owner edits a
        # file, and an error that does not say which one sends them looking.
        raise ValueError(f"{path}: {error}") from error


def read_list(path: Path) -> list[str]:
    """The items of a one-per-line list file, in file order, without repeats.

    Blank lines and lines starting with `#` are skipped; surrounding space is
    removed. A missing file raises: unlike settings, a list has no useful
    default, and a collector given no search terms would succeed with nothing.
    """
    items: list[str] = []
    seen: set[str] = set()

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(_COMMENT) or line in seen:
            continue
        seen.add(line)
        items.append(line)

    return items
