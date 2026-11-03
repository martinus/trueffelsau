"""Tests for `trueffelsau.settings`.

The strictness tests matter most. A setting that is read wrong does not fail;
it runs with a different number, and the first sign is a bill or an empty
digest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trueffelsau import settings as s

# --- parse(): values -------------------------------------------------------


def test_empty_text_gives_the_defaults() -> None:
    assert s.parse("") == s.Settings()


def test_a_set_key_overrides_only_that_key() -> None:
    result = s.parse("batch_size = 50\n")
    assert result.batch_size == 50
    assert result.max_events_per_run == s.Settings().max_events_per_run


def test_a_whole_number_is_accepted_for_a_float_setting() -> None:
    # TOML writes `60` and `60.0` differently; nobody means different things.
    result = s.parse("batch_poll_seconds = 30\n")
    assert result.batch_poll_seconds == 30.0
    assert isinstance(result.batch_poll_seconds, float)


def test_zero_is_allowed_where_it_means_no_wait() -> None:
    assert s.parse("http_min_interval_seconds = 0\n").http_min_interval_seconds == 0.0


def test_host_table_replaces_the_default_table() -> None:
    result = s.parse('[host_min_interval_seconds]\n"example.com" = 3\n')
    assert result.host_min_interval_seconds == {"example.com": 3.0}


# --- parse(): what it refuses ----------------------------------------------


def test_unknown_key_is_an_error() -> None:
    with pytest.raises(ValueError, match="max_event_per_run"):
        s.parse("max_event_per_run = 50\n")


def test_text_for_a_number_is_an_error() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        s.parse('batch_size = "500"\n')


def test_a_fraction_for_a_whole_number_is_an_error() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        s.parse("batch_size = 2.5\n")


def test_a_boolean_for_a_number_is_an_error() -> None:
    # `bool` is an `int` in Python, so this is the case a naive check lets through.
    with pytest.raises(ValueError, match="boolean"):
        s.parse("batch_size = true\n")


def test_a_number_for_text_is_an_error() -> None:
    with pytest.raises(ValueError, match="model"):
        s.parse("model = 5\n")


def test_empty_text_for_the_model_is_an_error() -> None:
    with pytest.raises(ValueError, match="model"):
        s.parse('model = "  "\n')


def test_a_negative_number_is_an_error() -> None:
    with pytest.raises(ValueError, match="negative"):
        s.parse("max_events_per_run = -1\n")


@pytest.mark.parametrize(
    "key", ["batch_size", "max_chars_per_event", "http_timeout_seconds", "http_max_bytes"]
)
def test_zero_is_an_error_where_it_makes_no_sense(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        s.parse(f"{key} = 0\n")


def test_a_host_table_with_text_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"host_min_interval_seconds\.example\.com"):
        s.parse('[host_min_interval_seconds]\n"example.com" = "slow"\n')


def test_a_host_value_that_is_not_a_table_is_an_error() -> None:
    with pytest.raises(ValueError, match="table"):
        s.parse("host_min_interval_seconds = 2\n")


# --- min_interval() ---------------------------------------------------------


def test_min_interval_uses_the_host_value() -> None:
    assert s.Settings().min_interval("api.github.com") == 2.0


def test_min_interval_falls_back_to_the_general_value() -> None:
    assert s.Settings().min_interval("hn.algolia.com") == s.Settings().http_min_interval_seconds


def test_min_interval_ignores_host_case() -> None:
    # Host names are case-insensitive; URLs in the wild are not consistent.
    configured = s.parse('[host_min_interval_seconds]\n"Example.COM" = 5\n')
    assert configured.min_interval("EXAMPLE.com") == 5.0


# --- load() ------------------------------------------------------------------


def test_missing_file_gives_the_defaults(tmp_path: Path) -> None:
    assert s.load(tmp_path / "settings.toml") == s.Settings()


def test_load_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('model = "claude-haiku-5-5"\n', encoding="utf-8")
    assert s.load(path).model == "claude-haiku-5-5"


def test_load_names_the_file_in_an_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("batch_sise = 5\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"settings\.toml"):
        s.load(path)


def test_load_names_the_file_in_a_toml_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("batch_size = \n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"settings\.toml"):
        s.load(path)


def test_settings_path_is_in_the_config_directory(tmp_path: Path) -> None:
    assert s.settings_path(tmp_path) == tmp_path / "config" / "settings.toml"


# --- read_list() ---------------------------------------------------------------


def test_read_list_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "list.txt"
    path.write_text("# heading\n\nfirst\n  second  \n# more\n", encoding="utf-8")
    assert s.read_list(path) == ["first", "second"]


def test_read_list_keeps_a_hash_inside_a_line(tmp_path: Path) -> None:
    path = tmp_path / "list.txt"
    path.write_text("https://example.com/changelog#2026\n", encoding="utf-8")
    assert s.read_list(path) == ["https://example.com/changelog#2026"]


def test_read_list_drops_repeats_and_keeps_order(tmp_path: Path) -> None:
    # A repeated term would cost a second API call for the same results.
    path = tmp_path / "list.txt"
    path.write_text("b\na\nb\n", encoding="utf-8")
    assert s.read_list(path) == ["b", "a"]


def test_read_list_of_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        s.read_list(tmp_path / "missing.txt")
