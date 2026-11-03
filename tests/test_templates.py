"""Tests for the files that ship inside the package: the classifier schema and
the config templates.

These files have no code, so nothing else would notice them drifting from the
spec. The schema is checked against CLAUDE.md itself, and against the rules the
structured-outputs API enforces: a schema that breaks them is refused with a
400 for the whole batch, a week later, on the owner's machine.
"""

from __future__ import annotations

import dataclasses
import json
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trueffelsau import bootstrap, settings

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "trueffelsau" / "schemas" / "classification_v1.json"
SPEC = ROOT / "CLAUDE.md"

#: Keywords the structured-outputs API refuses (docs, checked 2026-11-03).
#: The SDK can move some of them into descriptions; a raw request cannot.
UNSUPPORTED = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "maxItems",
}

#: The API allows at most this many fields with a union type per request.
MAX_UNIONS = 16


def _schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return data


def _nodes(node: Any) -> Iterator[dict[str, Any]]:
    """Every object in a JSON document, depth first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _nodes(value)


def _spec_section(number: int) -> str:
    text = SPEC.read_text(encoding="utf-8")
    match = re.search(rf"^## {number}\. .*?(?=^## \d+\. )", text, re.MULTILINE | re.DOTALL)
    assert match, f"CLAUDE.md has no section {number}"
    return match.group(0)


def _spec_fields() -> dict[str, str]:
    """The JSON block in CLAUDE.md section 8, as field name -> example value."""
    block = re.search(r"```json\n(.*?)```", _spec_section(8), re.DOTALL)
    assert block, "CLAUDE.md section 8 has no json block"
    fields: dict[str, str] = {}
    for name, value in re.findall(r'"(\w+)":\s*(.+?),?$', block.group(1), re.MULTILINE):
        fields[name] = value
    return fields


def _enum(prop: dict[str, Any]) -> list[str] | None:
    for option in prop.get("anyOf", [prop]):
        if "enum" in option:
            values: list[str] = option["enum"]
            return values
    return None


# --- the schema follows the API's rules ------------------------------------


def test_schema_is_an_object_at_the_top() -> None:
    assert _schema()["type"] == "object"


def test_every_object_closes_its_properties() -> None:
    for node in _nodes(_schema()):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, node.get("description")


def test_every_object_requires_every_property() -> None:
    # An optional field counts against a separate API limit, and a field the
    # model may leave out is one the scorer has to guess about. Unknown is
    # `null`, which is required and explicit.
    for node in _nodes(_schema()):
        if node.get("type") == "object":
            assert sorted(node["required"]) == sorted(node["properties"])


def test_schema_uses_no_unsupported_keyword() -> None:
    for node in _nodes(_schema()):
        assert not UNSUPPORTED & node.keys(), node


def test_min_items_is_at_most_one() -> None:
    for node in _nodes(_schema()):
        assert node.get("minItems", 0) in (0, 1)


def test_schema_stays_under_the_union_limit() -> None:
    unions = sum(
        1 for node in _nodes(_schema()) if "anyOf" in node or isinstance(node.get("type"), list)
    )
    assert unions <= MAX_UNIONS


def test_every_field_has_a_description() -> None:
    # The descriptions are the field definitions the model reads.
    for name, prop in _schema()["properties"].items():
        assert prop.get("description", "").strip(), name


# --- the schema matches CLAUDE.md -------------------------------------------


def test_schema_has_exactly_the_fields_of_spec_section_8() -> None:
    assert sorted(_schema()["properties"]) == sorted(_spec_fields())


def test_schema_enums_match_spec_section_8() -> None:
    properties = _schema()["properties"]
    checked = 0
    for name, example in _spec_fields().items():
        if "|" not in example:
            continue
        expected = [part.strip() for part in example.strip('"').split("|")]
        assert _enum(properties[name]) == expected, name
        checked += 1
    # Five enum fields in the spec. Fewer means the block parser broke.
    assert checked == 5


def test_event_types_match_the_signal_types_of_spec_section_5() -> None:
    signal_types = re.findall(r"^\| `(\w+)`", _spec_section(5), re.MULTILINE)
    assert _enum(_schema()["properties"]["event_type"]) == signal_types


def test_every_spec_field_may_be_unknown() -> None:
    # Section 8: "If a field is unknown, return null." Lists say "none found"
    # with an empty list instead: two ways to say nothing would be two cases
    # for the scorer, and one union fewer per field.
    for name, prop in _schema()["properties"].items():
        if prop.get("type") == "array":
            continue
        assert {"type": "null"} in prop["anyOf"], name


# --- config templates --------------------------------------------------------


def test_every_template_in_the_package_is_copied_by_init() -> None:
    # A template added to the directory but not to TEMPLATES ships and is then
    # never used.
    shipped = {path.name for path in bootstrap.template_dir().iterdir() if path.is_file()}
    assert shipped == set(bootstrap.TEMPLATES)


def test_settings_template_equals_the_defaults() -> None:
    # The template is the documentation of the defaults. If they differ, an
    # owner who deletes a line gets a value the file never showed them.
    template = (bootstrap.template_dir() / "settings.toml").read_text(encoding="utf-8")
    assert settings.parse(template) == settings.Settings()


def test_settings_template_sets_every_setting() -> None:
    template = tomllib.loads((bootstrap.template_dir() / "settings.toml").read_text("utf-8"))
    names = {field.name for field in dataclasses.fields(settings.Settings)}
    assert set(template) == names


def _weights() -> dict[str, Any]:
    return tomllib.loads((bootstrap.template_dir() / "weights.example.toml").read_text("utf-8"))


def test_weights_cover_the_seven_filter_points() -> None:
    weights = _weights()["weights"]
    points = sorted(key for key in weights if key.startswith("p"))
    assert [point[:2] for point in points] == [f"p{n}" for n in range(1, 8)]


def test_weights_sum_to_one() -> None:
    # Then a score is on the same 0-to-1 scale as each point, and two weight
    # versions give comparable numbers.
    weights = _weights()["weights"]
    total = sum(value for key, value in weights.items() if key.startswith("p"))
    assert total == pytest.approx(1.0)


def test_event_type_weights_cover_every_signal_type() -> None:
    event_types = set(_enum(_schema()["properties"]["event_type"]) or [])
    assert set(_weights()["event_type"]) - {"paying_bonus"} == event_types


def test_moat_weights_cover_every_moat() -> None:
    assert set(_weights()["moat"]) == set(_enum(_schema()["properties"]["incumbent_moat"]) or [])


def test_local_rules_weights_cover_every_value() -> None:
    expected = set(_enum(_schema()["properties"]["local_rules"]) or [])
    assert set(_weights()["fit"]["local_rules"]) == expected


def test_buyer_weights_cover_every_buyer_but_consumer() -> None:
    # Consumers go through `consumer_value` (section 4, point 2).
    buyers = set(_enum(_schema()["properties"]["buyer_type"]) or [])
    assert set(_weights()["buyer_type"]) == buyers - {"consumer"}
    values = set(_enum(_schema()["properties"]["value_per_user"]) or [])
    assert set(_weights()["consumer_value"]) == values


@pytest.mark.parametrize("name", ["hn_terms.txt", "subreddits.txt", "pe_firms.txt"])
def test_list_templates_are_not_empty(name: str) -> None:
    assert settings.read_list(bootstrap.template_dir() / name)


def test_subreddits_have_no_prefix() -> None:
    for name in settings.read_list(bootstrap.template_dir() / "subreddits.txt"):
        assert not name.lower().startswith("r/"), name
        assert re.fullmatch(r"\w+", name), name


def test_watchlist_template_holds_only_https_urls() -> None:
    for url in settings.read_list(bootstrap.template_dir() / "watchlist.example.txt"):
        assert url.startswith("https://"), url
