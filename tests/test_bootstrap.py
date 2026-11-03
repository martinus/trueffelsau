"""Tests for `trueffelsau init`, through `bootstrap.init_home` and the CLI.

The never-overwrite tests matter most. A second `init` that replaced the
weights or the key file with a template would delete the two files the owner
cannot get back from anywhere.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from trueffelsau import bootstrap, cli, db, home

# --- layout --------------------------------------------------------------------


def test_init_creates_every_directory(tmp_path: Path) -> None:
    base = tmp_path / "home"
    bootstrap.init_home(base)
    for directory in ("config", "data", "digests", "inbox"):
        assert (base / directory).is_dir(), directory


def test_init_copies_every_template_under_its_private_name(tmp_path: Path) -> None:
    bootstrap.init_home(tmp_path)
    for template, target in bootstrap.TEMPLATES.items():
        source = (bootstrap.template_dir() / template).read_text(encoding="utf-8")
        assert (tmp_path / "config" / target).read_text(encoding="utf-8") == source


def test_init_renames_the_private_templates(tmp_path: Path) -> None:
    # `.gitignore` lists these names; a copy under the `.example` name would
    # be neither ignored nor read.
    bootstrap.init_home(tmp_path)
    assert (tmp_path / "config" / "weights.toml").is_file()
    assert (tmp_path / "config" / "watchlist.txt").is_file()
    assert not (tmp_path / "config" / "weights.example.toml").exists()


def test_init_creates_an_env_file_that_parses_empty(tmp_path: Path) -> None:
    bootstrap.init_home(tmp_path)
    assert home.load_env(home.env_path(tmp_path)) == {}


def test_env_file_is_readable_by_the_owner_only(tmp_path: Path) -> None:
    bootstrap.init_home(tmp_path)
    mode = stat.S_IMODE(home.env_path(tmp_path).stat().st_mode)
    assert mode & 0o077 == 0


def test_home_directory_is_private(tmp_path: Path) -> None:
    base = tmp_path / "home"
    bootstrap.init_home(base)
    assert stat.S_IMODE(base.stat().st_mode) & 0o077 == 0


def test_init_migrates_the_database(tmp_path: Path) -> None:
    report = bootstrap.init_home(tmp_path)
    connection = db.connect(home.db_path(tmp_path))
    try:
        assert db.user_version(connection) == report.db_version
        assert report.db_version == db.migrations()[-1].version
    finally:
        connection.close()


# --- never overwrite --------------------------------------------------------------


def test_init_keeps_an_edited_weights_file(tmp_path: Path) -> None:
    bootstrap.init_home(tmp_path)
    weights = tmp_path / "config" / "weights.toml"
    weights.write_text("# my judgment\n", encoding="utf-8")

    report = bootstrap.init_home(tmp_path)

    assert weights.read_text(encoding="utf-8") == "# my judgment\n"
    assert weights in report.kept


def test_init_keeps_an_existing_env_file(tmp_path: Path) -> None:
    bootstrap.init_home(tmp_path)
    env = home.env_path(tmp_path)
    env.write_text("ANTHROPIC_API_KEY=secret\n", encoding="utf-8")

    bootstrap.init_home(tmp_path)

    assert home.load_env(env) == {"ANTHROPIC_API_KEY": "secret"}


def test_second_init_creates_nothing(tmp_path: Path) -> None:
    first = bootstrap.init_home(tmp_path)
    second = bootstrap.init_home(tmp_path)
    assert second.created == []
    assert sorted(second.kept) == sorted(first.created)


def test_init_adds_a_template_that_is_missing(tmp_path: Path) -> None:
    bootstrap.init_home(tmp_path)
    (tmp_path / "config" / "pe_firms.txt").unlink()

    report = bootstrap.init_home(tmp_path)

    assert report.created == [tmp_path / "config" / "pe_firms.txt"]


# --- CLI -------------------------------------------------------------------------


def test_cli_init_uses_the_home_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRUEFFELSAU_HOME", str(tmp_path / "home"))

    assert cli.main(["init"]) == 0

    assert (tmp_path / "home" / "data" / "radar.db").is_file()
    out = capsys.readouterr().out
    assert f"home: {tmp_path / 'home'}" in out
    assert "created: config/weights.toml" in out


def test_cli_init_reports_kept_files_on_a_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRUEFFELSAU_HOME", str(tmp_path))
    cli.main(["init"])
    capsys.readouterr()

    cli.main(["init"])

    out = capsys.readouterr().out
    assert "created:" not in out
    assert "kept:    config/weights.toml" in out


def test_cli_without_a_command_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2
