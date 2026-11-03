"""Record one live response as a test fixture.

    .venv/bin/python scripts/record_fixture.py hn "price increase"

Fetches the first page for the term with the collector's own parameters,
looking back 14 days, and writes `tests/fixtures/hn/recorded_<term>.json`. The
recorded file pins the real response shape, so a test notices when the parser
and the format drift apart -- though only after the fixture is recorded again.

The fixture is committed to a public repository. Before writing, `scrub_hn`
drops every author name (the `author` field, the `author_<name>` tag, and the
highlight block that repeats the name) and keeps at most 5 hits: enough for
the shape, as little collected text as possible (CLAUDE.md sections 12, 15).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trueffelsau.collectors import hn
from trueffelsau.events import utc_text
from trueffelsau.http import Fetcher
from trueffelsau.settings import Settings

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
MAX_HITS = 5
LOOK_BACK = timedelta(days=14)

#: Hit keys that hold or repeat the author's name.
_AUTHOR_KEYS = ("author", "_highlightResult", "_snippetResult")


def slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")


def scrub_hn(response: dict[str, Any], max_hits: int = MAX_HITS) -> dict[str, Any]:
    """`response` with at most `max_hits` hits and no author names.

    Keeps up to two stories and fills up with comments, so both shapes are in
    the fixture when the response has both. Raises when a name survives.
    """
    hits: list[dict[str, Any]] = response["hits"]
    authors = {hit["author"] for hit in hits if isinstance(hit.get("author"), str)}
    stories = [hit for hit in hits if "comment" not in hit.get("_tags", [])]
    comments = [hit for hit in hits if "comment" in hit.get("_tags", [])]
    picked = (stories[:2] + comments + stories[2:])[:max_hits]

    scrubbed = [_scrub_hit(hit) for hit in picked]
    result = {**response, "hits": scrubbed}

    text = json.dumps(result)
    for author in authors:
        if f"author_{author}" in text or f'"author": "{author}"' in text:
            raise ValueError(f"an author name survived scrubbing: {author!r}")
    return result


def _scrub_hit(hit: dict[str, Any]) -> dict[str, Any]:
    clean = {key: value for key, value in hit.items() if key not in _AUTHOR_KEYS}
    if isinstance(clean.get("_tags"), list):
        clean["_tags"] = [tag for tag in clean["_tags"] if not tag.startswith("author_")]
    return clean


def record_hn(term: str, now: datetime) -> Path:
    params = hn.request_params(term, now - LOOK_BACK, 0)
    page = Fetcher(Settings()).get(hn.SEARCH_URL, params=params, expect="json")
    response = page.json()
    if not isinstance(response, dict) or not response.get("hits"):
        raise SystemExit(f"no hits for {term!r}: a fixture without hits tests nothing")

    fixture = {
        "request": {"url": hn.SEARCH_URL, "params": params},
        "recorded_at": utc_text(now),
        "response": scrub_hn(response),
    }
    path = FIXTURES / "hn" / f"recorded_{slug(term)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a live response as a test fixture.")
    parser.add_argument("source", choices=["hn"])
    parser.add_argument("term")
    args = parser.parse_args(argv)

    path = record_hn(args.term, datetime.now(UTC))
    hits = len(json.loads(path.read_text(encoding="utf-8"))["response"]["hits"])
    print(f"wrote {path} ({hits} hits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
