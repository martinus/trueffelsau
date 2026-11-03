"""Hacker News, through the Algolia search API (CLAUDE.md section 6).

One search per term in `hn_terms.txt`, newest first, stories and comments,
created after `since`. Each term is sent as a quoted phrase. Measured on
2026-11-03 over 14 days: unquoted, the 20 terms matched about 6,000 items, and
only 17 of the first 100 for `price increase` contained the phrase; quoted they
matched about 730, all with the phrase. Every unrelated item costs a
classification.

The author of a hit is never read, so it cannot be stored (section 12).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser

from trueffelsau.collectors.base import Collected, Context
from trueffelsau.events import Event, utc_text
from trueffelsau.http import Fetcher, FetchError
from trueffelsau.settings import read_list

SOURCE = "hn"
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://news.ycombinator.com/item?id="
TERMS_FILE = "hn_terms.txt"

#: Algolia's largest page.
HITS_PER_PAGE = 100

#: Pages read per term. Algolia serves at most 1,000 hits for one query, which
#: is 10 pages of 100; a term that reaches it is too broad anyway.
MAX_PAGES = 10


def phrase(term: str) -> str:
    """`term` as an exact-phrase query. Quotes inside the term are dropped."""
    return '"' + term.replace('"', "").strip() + '"'


def request_params(term: str, since: datetime, page: int) -> dict[str, str]:
    """The query parameters for one page of one term."""
    return {
        "query": phrase(term),
        "tags": "(story,comment)",
        "numericFilters": f"created_at_i>{int(since.timestamp())}",
        "hitsPerPage": str(HITS_PER_PAGE),
        "page": str(page),
    }


def collect(ctx: Context, since: datetime) -> Collected:
    """Search every term. A failed term is reported and the others still run."""
    terms = read_list(ctx.config_dir / TERMS_FILE)
    if not terms:
        raise ValueError(f"{TERMS_FILE} has no search terms")

    fetched_at = utc_text(ctx.clock())
    events: list[Event] = []
    failures: list[str] = []
    for term in terms:
        try:
            events.extend(fetch_term(ctx.fetcher, term, since, fetched_at))
        except (FetchError, ValueError) as failure:
            failures.append(f"{term!r}: {failure}")

    if not failures:
        return Collected(events)
    summary = f"{len(failures)} of {len(terms)} terms failed: " + "; ".join(failures)
    return Collected(events, summary)


def fetch_term(fetcher: Fetcher, term: str, since: datetime, fetched_at: str) -> list[Event]:
    """Every page for one term, up to `MAX_PAGES`."""
    events: list[Event] = []
    for page in range(MAX_PAGES):
        params = request_params(term, since, page)
        data = fetcher.get(SEARCH_URL, params=params, expect="json").json()
        events.extend(parse(data, fetched_at))
        if page + 1 >= _page_count(data):
            break
    return events


def parse(data: object, fetched_at: str) -> list[Event]:
    """The events in one response page. Raises `ValueError` on an unknown shape.

    A changed format must fail loudly: parsed into nothing, it would look like
    a quiet week.
    """
    hits = _response(data).get("hits")
    if not isinstance(hits, list):
        raise ValueError("response has no 'hits' list")

    events: list[Event] = []
    for hit in hits:
        if not isinstance(hit, dict):
            raise ValueError(f"hit is not an object: {hit!r}")
        event = hit_to_event(hit, fetched_at)
        if event is not None:
            events.append(event)
    return events


def _response(data: object) -> Mapping[str, object]:
    if not isinstance(data, dict):
        raise ValueError(f"response is not an object: {type(data).__name__}")
    return data


def _page_count(data: object) -> int:
    pages = _response(data).get("nbPages")
    if not isinstance(pages, int) or isinstance(pages, bool):
        raise ValueError(f"response has no page count: nbPages={pages!r}")
    return pages


def hit_to_event(hit: Mapping[str, object], fetched_at: str) -> Event | None:
    """One hit as an event, or `None` when it has no text."""
    object_id = hit.get("objectID")
    if not isinstance(object_id, str) or not object_id.isdigit():
        raise ValueError(f"hit without a numeric objectID: {object_id!r}")
    created = hit.get("created_at_i")
    if not isinstance(created, int) or isinstance(created, bool):
        raise ValueError(f"hit {object_id} without created_at_i: {created!r}")

    tags = hit.get("_tags")
    is_comment = isinstance(tags, list) and "comment" in tags
    text = _comment_text(hit) if is_comment else _story_text(hit)
    if not text:
        return None

    return Event(
        source=SOURCE,
        source_url=ITEM_URL + object_id,
        product=None,
        fetched_at=fetched_at,
        event_at=utc_text(datetime.fromtimestamp(created, UTC)),
        raw_text=text,
    )


def _field(hit: Mapping[str, object], key: str) -> str:
    """A text field, or "" when it is missing or null."""
    value = hit.get(key)
    return value.strip() if isinstance(value, str) else ""


def _story_text(hit: Mapping[str, object]) -> str:
    """Title and link on the first lines, then the story's own text."""
    head = "\n".join(part for part in (_field(hit, "title"), _field(hit, "url")) if part)
    body = html_to_text(_field(hit, "story_text"))
    return "\n\n".join(part for part in (head, body) if part)


def _comment_text(hit: Mapping[str, object]) -> str:
    """The comment, after the title of the story it belongs to.

    The title is context the classifier needs: "they doubled it again" says
    nothing without the story it answers. A comment without text is skipped
    even when the title is known.
    """
    body = html_to_text(_field(hit, "comment_text"))
    if not body:
        return ""
    title = _field(hit, "story_title")
    return f"Comment on: {title}\n\n{body}" if title else body


class _TextExtractor(HTMLParser):
    """Collects the text of HN's small HTML subset. `<p>` starts a paragraph."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def html_to_text(markup: str) -> str:
    """Plain text: tags removed, entities decoded, paragraphs split by a blank line."""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    paragraphs = (part.strip() for part in _PARAGRAPH_BREAK.split("".join(parser.parts)))
    return "\n\n".join(part for part in paragraphs if part)
