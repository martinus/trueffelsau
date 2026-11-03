"""Tests for the Hacker News collector.

No test calls the live API (CLAUDE.md section 16). `FakeAlgolia` answers each
(query, page) from a table; `handmade.json` has the shape of a live response
fetched on 2026-11-03, with invented text.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.test_http import FakeResponse
from trueffelsau import db
from trueffelsau.collectors import hn, run_collector
from trueffelsau.collectors.base import Context
from trueffelsau.http import Fetcher
from trueffelsau.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "hn"
NOW = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
FETCHED_AT = "2026-11-03T18:00:00Z"
QUERY = '"price increase"'


def handmade() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "handmade.json").read_text(encoding="utf-8"))
    return data


def page(hits: list[dict[str, Any]], number: int = 0, pages: int = 1) -> dict[str, Any]:
    return {"hits": hits, "nbPages": pages, "page": number, "hitsPerPage": 100}


def story(object_id: int, title: str = "A story") -> dict[str, Any]:
    return {
        "_tags": ["story", f"story_{object_id}"],
        "objectID": str(object_id),
        "created_at_i": 1793606400,
        "title": title,
        "url": None,
        "story_text": None,
    }


@dataclass
class FakeAlgolia:
    """Answers (query, page) with a JSON body, or with an HTTP status."""

    answers: dict[tuple[str, int], object]
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(
        self, url: str, params: Mapping[str, str] | None, headers: Mapping[str, str], timeout: float
    ) -> FakeResponse:
        assert url == hn.SEARCH_URL
        assert params is not None
        self.calls.append(dict(params))
        answer = self.answers[(params["query"], int(params["page"]))]
        if isinstance(answer, int):
            return FakeResponse(status_code=answer, body=b"")
        return FakeResponse(body=json.dumps(answer).encode())


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


def make_context(
    tmp_path: Path,
    send: FakeAlgolia,
    terms: tuple[str, ...] = ("price increase",),
    settings: Settings | None = None,
    clock: Clock | None = None,
) -> Context:
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / hn.TERMS_FILE).write_text("# comment\n" + "\n".join(terms) + "\n", encoding="utf-8")
    connection = db.connect(tmp_path / "radar.db")
    db.migrate(connection)
    chosen = settings if settings is not None else Settings()
    fetcher = Fetcher(chosen, send=send, clock=lambda: 0.0, sleep=lambda seconds: None)
    return Context(connection, fetcher, chosen, config, clock if clock is not None else Clock())


def stored(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM events ORDER BY source_url").fetchall()


# --- parse ------------------------------------------------------------------


def test_a_story_has_title_link_and_text() -> None:
    events = hn.parse(handmade(), FETCHED_AT)
    first = events[0]
    assert first.source == "hn"
    assert first.source_url == "https://news.ycombinator.com/item?id=100"
    assert first.product is None
    assert first.fetched_at == FETCHED_AT
    assert first.event_at == "2026-11-02T08:00:00Z"
    assert first.raw_text == (
        "Acme doubles its price increase again\n"
        "https://example.com/acme-pricing\n"
        "\n"
        "We moved from Acme after the price increase.\n"
        "\n"
        "Self-hosting & ffmpeg did the rest."
    )


def test_a_story_without_link_or_text_is_its_title() -> None:
    events = hn.parse(handmade(), FETCHED_AT)
    assert events[1].raw_text == "Ask HN: What do you use after the price increase?"


def test_a_comment_names_its_story_and_decodes_entities() -> None:
    events = hn.parse(handmade(), FETCHED_AT)
    comment = events[2]
    assert comment.source_url == "https://news.ycombinator.com/item?id=102"
    assert comment.event_at == "2026-11-03T12:30:00Z"
    assert comment.raw_text == (
        "Comment on: Acme doubles its price increase again\n"
        "\n"
        "The price increase hit us too.\n"
        "\n"
        "We pay 'per seat' now: https://example.com/x"
    )


def test_a_comment_without_text_is_skipped() -> None:
    urls = [event.source_url for event in hn.parse(handmade(), FETCHED_AT)]
    assert urls == [hn.ITEM_URL + "100", hn.ITEM_URL + "101", hn.ITEM_URL + "102"]


def test_the_author_is_not_stored() -> None:
    for event in hn.parse(handmade(), FETCHED_AT):
        assert "someuser" not in repr(event)


def test_a_comment_without_story_title_is_its_text() -> None:
    hit = {"_tags": ["comment"], "objectID": "7", "created_at_i": 0, "comment_text": "<p>hi"}
    event = hn.hit_to_event(hit, FETCHED_AT)
    assert event is not None
    assert event.raw_text == "hi"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data.pop("hits"), "no 'hits' list"),
        (lambda data: data["hits"][0].pop("objectID"), "objectID"),
        (lambda data: data["hits"][0].update(objectID="abc"), "objectID"),
        (lambda data: data["hits"][0].pop("created_at_i"), "created_at_i"),
        (lambda data: data["hits"].append("text"), "not an object"),
    ],
)
def test_an_unknown_shape_is_an_error(change: Any, message: str) -> None:
    data = copy.deepcopy(handmade())
    change(data)
    with pytest.raises(ValueError, match=message):
        hn.parse(data, FETCHED_AT)


def test_a_response_that_is_not_an_object_is_an_error() -> None:
    with pytest.raises(ValueError, match="not an object"):
        hn.parse([], FETCHED_AT)


def test_html_paragraphs_and_line_breaks() -> None:
    assert hn.html_to_text("a<br>b<p>  c  <p><p>d &gt; e") == "a\nb\n\nc\n\nd > e"


# --- requests ---------------------------------------------------------------


def test_request_params_ask_for_an_exact_phrase_after_since() -> None:
    since = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    assert hn.request_params("price increase", since, 3) == {
        "query": '"price increase"',
        "tags": "(story,comment)",
        "numericFilters": "created_at_i>1792454400",
        "hitsPerPage": "100",
        "page": "3",
    }


def test_quotes_inside_a_term_are_dropped() -> None:
    assert hn.phrase('say "no"') == '"say no"'


def test_every_page_is_read_until_the_last(tmp_path: Path) -> None:
    send = FakeAlgolia(
        {
            (QUERY, 0): page([story(1)], 0, pages=3),
            (QUERY, 1): page([story(2)], 1, pages=3),
            (QUERY, 2): page([story(3)], 2, pages=3),
        }
    )
    ctx = make_context(tmp_path, send)
    collected = hn.collect(ctx, NOW - timedelta(days=1))
    assert [call["page"] for call in send.calls] == ["0", "1", "2"]
    assert len(collected.events) == 3
    assert collected.error is None


def test_paging_stops_at_the_page_cap(tmp_path: Path) -> None:
    answers: dict[tuple[str, int], object] = {
        (QUERY, number): page([story(number)], number, pages=50) for number in range(50)
    }
    send = FakeAlgolia(answers)
    hn.collect(make_context(tmp_path, send), NOW - timedelta(days=1))
    assert len(send.calls) == hn.MAX_PAGES


def test_an_empty_result_is_one_request(tmp_path: Path) -> None:
    send = FakeAlgolia({(QUERY, 0): page([], 0, pages=0)})
    collected = hn.collect(make_context(tmp_path, send), NOW - timedelta(days=1))
    assert len(send.calls) == 1
    assert collected.events == []


def test_a_failed_term_does_not_stop_the_others(tmp_path: Path) -> None:
    send = FakeAlgolia(
        {
            ('"a"', 0): page([story(1)]),
            ('"b"', 0): 404,
            ('"c"', 0): page([story(3)]),
        }
    )
    collected = hn.collect(make_context(tmp_path, send, terms=("a", "b", "c")), NOW)
    assert [event.source_url for event in collected.events] == [
        hn.ITEM_URL + "1",
        hn.ITEM_URL + "3",
    ]
    assert collected.error is not None
    assert collected.error.startswith("1 of 3 terms failed: 'b': ")
    assert "HTTP 404" in collected.error


def test_a_term_with_a_changed_format_fails_alone(tmp_path: Path) -> None:
    send = FakeAlgolia({('"a"', 0): {"results": []}, ('"b"', 0): page([story(2)])})
    collected = hn.collect(make_context(tmp_path, send, terms=("a", "b")), NOW)
    assert len(collected.events) == 1
    assert collected.error is not None
    assert "'a': response has no 'hits' list" in collected.error


def test_an_empty_terms_file_is_an_error(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, FakeAlgolia({}), terms=())
    with pytest.raises(ValueError, match="no search terms"):
        hn.collect(ctx, NOW)


# --- through run_collector --------------------------------------------------


def test_a_rerun_stores_nothing_new(tmp_path: Path) -> None:
    send = FakeAlgolia({(QUERY, 0): handmade()})
    clock = Clock()
    ctx = make_context(tmp_path, send, settings=Settings(collector_min_hours=0), clock=clock)

    first = run_collector("hn", ctx)
    clock.now += timedelta(hours=1)
    second = run_collector("hn", ctx)

    assert (first.events_new, first.events_seen, first.error) == (3, 0, None)
    assert (second.events_new, second.events_seen, second.error) == (0, 3, None)
    assert len(stored(ctx.connection)) == 3


def test_the_first_run_looks_back_first_run_days(tmp_path: Path) -> None:
    send = FakeAlgolia({(QUERY, 0): page([])})
    ctx = make_context(tmp_path, send, settings=Settings(first_run_days=14))
    run_collector("hn", ctx)
    since = int((NOW - timedelta(days=14)).timestamp())
    assert send.calls[0]["numericFilters"] == f"created_at_i>{since}"


def test_hits_found_by_two_terms_are_stored_once(tmp_path: Path) -> None:
    send = FakeAlgolia({('"a"', 0): page([story(1)]), ('"b"', 0): page([story(1)])})
    report = run_collector("hn", make_context(tmp_path, send, terms=("a", "b")))
    assert (report.events_new, report.events_seen) == (1, 0)


# --- recorded live responses ------------------------------------------------

RECORDED = sorted(FIXTURES.glob("recorded_*.json"))
EVENT_AT = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
#: The tags HN allows in text. A literal "<" in a comment is fine.
HN_TAG = re.compile(r"</?(p|a|i|pre|code)\b")


def test_recorded_fixtures_exist() -> None:
    # Without this, deleting the files would make the tests below pass by
    # running zero times.
    assert RECORDED


@pytest.mark.parametrize("path", RECORDED, ids=lambda path: path.name)
def test_a_recorded_response_gives_complete_events(path: Path) -> None:
    recorded = json.loads(path.read_text(encoding="utf-8"))
    hits = recorded["response"]["hits"]
    events = hn.parse(recorded["response"], FETCHED_AT)

    assert len(events) == len(hits)
    for hit, event in zip(hits, events, strict=True):
        assert event.source == "hn"
        assert event.source_url == hn.ITEM_URL + hit["objectID"]
        assert event.event_at is not None
        assert EVENT_AT.match(event.event_at)
        assert event.event_at == hit["created_at"]
        assert event.raw_text.strip()
        assert not HN_TAG.search(event.raw_text)


@pytest.mark.parametrize("path", RECORDED, ids=lambda path: path.name)
def test_a_recorded_response_has_no_author(path: Path) -> None:
    for hit in json.loads(path.read_text(encoding="utf-8"))["response"]["hits"]:
        assert not {"author", "_highlightResult", "_snippetResult"} & set(hit)
        assert not [tag for tag in hit["_tags"] if tag.startswith("author_")]


# --- scripts/record_fixture.py ----------------------------------------------


def load_recorder() -> Any:
    path = Path(__file__).parent.parent / "scripts" / "record_fixture.py"
    spec = importlib.util.spec_from_file_location("record_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def with_author(hit: dict[str, Any], name: str) -> dict[str, Any]:
    tagged = {**hit, "author": name, "_tags": [*hit["_tags"], f"author_{name}"]}
    tagged["_highlightResult"] = {"author": {"value": name}}
    return tagged


def test_scrub_removes_every_author_name() -> None:
    recorder = load_recorder()
    response = page([with_author(story(1), "alice"), with_author(story(2), "bob")])
    scrubbed = recorder.scrub_hn(response)
    text = json.dumps(scrubbed)
    assert "alice" not in text
    assert "bob" not in text
    assert scrubbed["hits"][0]["_tags"] == ["story", "story_1"]


def test_scrub_keeps_stories_and_comments_up_to_the_limit() -> None:
    recorder = load_recorder()
    comments = [
        {"_tags": ["comment"], "objectID": str(n), "created_at_i": 0, "comment_text": "c"}
        for n in range(10, 20)
    ]
    response = page([story(1), story(2), story(3), *comments])
    ids = [hit["objectID"] for hit in recorder.scrub_hn(response, max_hits=5)["hits"]]
    assert ids == ["1", "2", "10", "11", "12"]


def test_scrub_refuses_a_name_it_did_not_remove() -> None:
    recorder = load_recorder()
    hit = with_author(story(1), "alice")
    hit["_tags"] = ["story", "AUTHOR_x"]
    hit["extra"] = "author_alice"
    with pytest.raises(ValueError, match="alice"):
        recorder.scrub_hn(page([hit]))
