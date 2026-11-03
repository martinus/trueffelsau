"""Tests for `trueffelsau collect`. Fake collectors replace the real ones, so
nothing here touches the network."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from trueffelsau import bootstrap, cli, collectors
from trueffelsau.collectors.base import Collected, Context
from trueffelsau.events import Event


def found(ctx: Context, since: datetime) -> Collected:
    return Collected([Event("good", "https://example.com/1", None, "t", None, "text")])


def broken(ctx: Context, since: datetime) -> Collected:
    raise RuntimeError("boom")


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRUEFFELSAU_HOME", str(tmp_path))
    bootstrap.init_home(tmp_path)
    monkeypatch.setattr(collectors, "COLLECTORS", {"bad": broken, "good": found})
    return tmp_path


def test_collect_runs_every_source_and_fails_when_one_failed(
    fakes: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["collect"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "bad: 0 new, 0 seen; failed: RuntimeError: boom",
        "good: 1 new, 0 seen",
    ]


def test_collect_one_source(fakes: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["collect", "--source", "good"]) == 0
    assert capsys.readouterr().out == "good: 1 new, 0 seen\n"


def test_a_second_collect_is_skipped(fakes: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["collect", "--source", "good"])
    capsys.readouterr()
    assert cli.main(["collect", "--source", "good"]) == 0
    assert capsys.readouterr().out == "good: skipped, last run 0 h ago\n"


def test_collect_without_init_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRUEFFELSAU_HOME", str(tmp_path / "missing"))
    assert cli.main(["collect"]) == 1
    assert "run `trueffelsau init` first" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()


def test_an_unknown_source_is_a_usage_error(fakes: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["collect", "--source", "nope"])
    assert raised.value.code == 2
