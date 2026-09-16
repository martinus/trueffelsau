"""Tests for `trueffelsau.home`.

Each test names one decision the module makes. The precedence tests matter most:
picking the wrong directory does not fail, it writes the database somewhere the
next run will not look, and the symptom is an empty digest rather than an error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trueffelsau import home as h

# --- home(): which directory wins ------------------------------------------


def test_explicit_home_variable_wins() -> None:
    assert h.home({"TRUEFFELSAU_HOME": "/srv/tsau"}) == Path("/srv/tsau")


def test_explicit_home_beats_xdg() -> None:
    env = {"TRUEFFELSAU_HOME": "/srv/tsau", "XDG_DATA_HOME": "/xdg"}
    assert h.home(env) == Path("/srv/tsau")


def test_xdg_is_used_when_home_variable_is_unset() -> None:
    assert h.home({"XDG_DATA_HOME": "/xdg"}) == Path("/xdg/trueffelsau")


def test_empty_home_variable_counts_as_unset() -> None:
    # `TRUEFFELSAU_HOME=` is how a shell profile clears a variable. Reading it
    # as a path would resolve to the current directory.
    env = {"TRUEFFELSAU_HOME": "", "XDG_DATA_HOME": "/xdg"}
    assert h.home(env) == Path("/xdg/trueffelsau")


def test_empty_xdg_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/someone")
    assert h.home({"XDG_DATA_HOME": ""}) == Path("/home/someone/.local/share/trueffelsau")


def test_falls_back_to_xdg_default_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/someone")
    assert h.home({}) == Path("/home/someone/.local/share/trueffelsau")


def test_reads_the_real_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUEFFELSAU_HOME", "/srv/from-environ")
    assert h.home() == Path("/srv/from-environ")


# --- home(): the path is always absolute -----------------------------------


def test_relative_home_is_anchored_to_the_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert h.home({"TRUEFFELSAU_HOME": "scratch"}) == tmp_path / "scratch"


def test_tilde_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/someone")
    assert h.home({"TRUEFFELSAU_HOME": "~/tsau"}) == Path("/home/someone/tsau")


def test_tilde_is_expanded_in_xdg_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/someone")
    assert h.home({"XDG_DATA_HOME": "~/share"}) == Path("/home/someone/share/trueffelsau")


def test_symlinked_home_keeps_the_name_it_was_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Not `Path.resolve()`: the owner's spelling is the one that goes into logs
    # and the systemd unit, so a symlinked home must not silently become its
    # target.
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    assert h.home({"TRUEFFELSAU_HOME": str(link)}) == link


# --- the names under the home ----------------------------------------------


def test_directory_layout() -> None:
    base = Path("/srv/tsau")
    assert h.config_dir(base) == Path("/srv/tsau/config")
    assert h.data_dir(base) == Path("/srv/tsau/data")
    assert h.digests_dir(base) == Path("/srv/tsau/digests")
    assert h.inbox_dir(base) == Path("/srv/tsau/inbox")


def test_env_file_is_flat_and_extensionless() -> None:
    # systemd's `EnvironmentFile=` points straight at this path.
    assert h.env_path(Path("/srv/tsau")) == Path("/srv/tsau/env")


def test_database_lives_under_the_data_directory() -> None:
    # CLAUDE.md §7 names it `data/radar.db`.
    assert h.db_path(Path("/srv/tsau")) == Path("/srv/tsau/data/radar.db")


# --- parse_env() -----------------------------------------------------------


def test_parses_assignments() -> None:
    assert h.parse_env("ANTHROPIC_API_KEY=sk-ant-123\nGITHUB_TOKEN=ghp-456\n") == {
        "ANTHROPIC_API_KEY": "sk-ant-123",
        "GITHUB_TOKEN": "ghp-456",
    }


def test_skips_blank_lines_and_comments() -> None:
    text = "\n# a comment\n   \n\t# indented comment\nA=1\n"
    assert h.parse_env(text) == {"A": "1"}


def test_hash_inside_a_value_is_not_a_comment() -> None:
    assert h.parse_env("A=one#two\n") == {"A": "one#two"}


def test_strips_surrounding_double_quotes() -> None:
    assert h.parse_env('A="spaced value"\n') == {"A": "spaced value"}


def test_strips_surrounding_single_quotes() -> None:
    assert h.parse_env("A='spaced value'\n") == {"A": "spaced value"}


def test_empty_quoted_value_becomes_empty_string() -> None:
    assert h.parse_env('A=""\n') == {"A": ""}


def test_mismatched_quotes_are_kept() -> None:
    assert h.parse_env("A=\"unbalanced'\n") == {"A": "\"unbalanced'"}


def test_inner_quotes_are_kept() -> None:
    assert h.parse_env('A=say "hi"\n') == {"A": 'say "hi"'}


def test_only_the_first_equals_splits_the_line() -> None:
    assert h.parse_env("A=key=value\n") == {"A": "key=value"}


def test_whitespace_around_name_and_value_is_dropped() -> None:
    assert h.parse_env("  A  =  1  \n") == {"A": "1"}


def test_a_later_assignment_wins() -> None:
    assert h.parse_env("A=1\nA=2\n") == {"A": "2"}


def test_value_may_be_empty() -> None:
    assert h.parse_env("A=\n") == {"A": ""}


def test_a_line_without_equals_is_an_error() -> None:
    # The file holds API keys. A dropped line would surface much later as an
    # authentication failure with nothing pointing back here.
    with pytest.raises(ValueError, match="line 2"):
        h.parse_env("A=1\nJUST_A_NAME\n")


def test_a_line_with_no_name_is_an_error() -> None:
    with pytest.raises(ValueError, match="line 1"):
        h.parse_env("=orphan\n")


def test_empty_text_parses_to_nothing() -> None:
    assert h.parse_env("") == {}


# --- load_env() ------------------------------------------------------------


def test_load_env_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text("A=1\n", encoding="utf-8")
    assert h.load_env(path) == {"A": "1"}


def test_load_env_returns_nothing_when_the_file_is_missing(tmp_path: Path) -> None:
    # `trueffelsau init` has to work before an API key has been pasted anywhere.
    assert h.load_env(tmp_path / "absent") == {}


def test_load_env_propagates_a_malformed_line(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text("nonsense\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        h.load_env(path)


def test_load_env_refuses_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    # `UnicodeDecodeError` is a `ValueError`, so an `except ValueError` here
    # would turn a corrupted file into an empty one: the run would then proceed
    # with no API key and fail later with nothing pointing back at the file.
    path = tmp_path / "env"
    path.write_bytes(b"A=\xff\xfe\n")
    with pytest.raises(UnicodeDecodeError):
        h.load_env(path)
