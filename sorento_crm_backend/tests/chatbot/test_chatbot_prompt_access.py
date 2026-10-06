"""ACCESS-MODEL S7: the pure prompt renderer `prompt_access.render_for_access` and `tag_index`
(AC-AM-13, AC-AM-14).

Tag grammar under test:
  block   `{{#only TAG}}` alone on a line ... `{{/only}}` alone on a line
  inline  `{{#only TAG}}span{{/only}}`
TAG is a domain name (granted iff in `access.domains`) or a field/report key (granted iff in
`access.attributes`); comma-separated tags grant if ANY is granted. Untagged text always stays.

Red-first: `app/services/chatbot/prompt_access.py` does not exist yet.
"""
from __future__ import annotations

import logging
import re

import pytest

FIXTURE = """Intro line, always.
{{#only inventory}}
STOCK block
stock body line
{{/only}}
Middle line, untagged.
{{#only purchase_cost}}
COST block
cost body line
{{/only}}
{{#only sales_orders.outstanding}}
OUTSTANDING block
outstanding body line
{{/only}}
{{#only order,sales}}
ORDER OR SALES block
order body line
{{/only}}
{{#only inventory}}
STOCK second block
second stock body
{{/only}}
{{#only incoming}}
INCOMING block
incoming body line
{{/only}}
Price is RM10 {{#only purchase_cost}}(cost RM7){{/only}} and {{#only inventory}}(stock 5){{/only}} today.
Tail line, always.
"""
KNOWN = frozenset(
    {"inventory", "purchase_cost", "sales_orders.outstanding", "order", "sales", "incoming"}
)
_MARKER_LINE = re.compile(r"^\{\{(?:#only [^}]*|/only)\}\}\n", re.M)
_INLINE = re.compile(r"\{\{#only [^}]*\}\}|\{\{/only\}\}")


def _access(domains=(), attributes=()):
    from app.services.chatbot.access_tree import EffectiveAccess

    return EffectiveAccess(
        resolved=True,
        domains=frozenset(domains),
        attributes=tuple(sorted(attributes)),
        sees_all_customers=False,
        roles=(),
    )


def _render(text, access, known=KNOWN):
    from app.services.chatbot.prompt_access import render_for_access

    return render_for_access(text, access, known_tags=known)


def test_untagged_text_survives_a_contact_with_no_access():
    out = _render(FIXTURE, _access())
    assert out == (
        "Intro line, always.\nMiddle line, untagged.\n"
        "Price is RM10  and  today.\nTail line, always.\n"
    )


def test_a_granted_domain_keeps_its_block_and_drops_the_marker_lines_with_no_blank_left():
    out = _render(FIXTURE, _access(domains={"inventory"}))
    assert out == (
        "Intro line, always.\nSTOCK block\nstock body line\nMiddle line, untagged.\n"
        "STOCK second block\nsecond stock body\n"
        "Price is RM10  and (stock 5) today.\nTail line, always.\n"
    )
    assert "{{" not in out and "\n\n" not in out


def test_a_field_or_report_key_is_granted_by_attributes_not_domains():
    out = _render(FIXTURE, _access(attributes={"sales_orders.outstanding"}))
    assert "OUTSTANDING block" in out
    assert "outstanding body line" in out
    only_domain = _render(FIXTURE, _access(domains={"sales_orders.outstanding"}))
    assert "OUTSTANDING block" not in only_domain, "a key is read from attributes only"


def test_comma_separated_tags_grant_if_any_is_granted():
    assert "ORDER OR SALES block" in _render(FIXTURE, _access(domains={"sales"}))
    assert "ORDER OR SALES block" in _render(FIXTURE, _access(domains={"order"}))
    assert "ORDER OR SALES block" not in _render(FIXTURE, _access(domains={"incoming"}))


def test_inline_span_is_kept_or_removed_by_its_tag():
    granted = _render(FIXTURE, _access(attributes={"purchase_cost"}))
    assert "Price is RM10 (cost RM7) and  today." in granted
    denied = _render(FIXTURE, _access())
    assert "cost RM7" not in denied


def test_full_access_equals_the_input_with_the_markers_removed_byte_for_byte():
    full = _access(
        domains={"inventory", "purchase_cost", "order", "sales", "incoming"},
        attributes={"sales_orders.outstanding", "purchase_cost"},
    )
    expected = _INLINE.sub("", _MARKER_LINE.sub("", FIXTURE))
    assert _render(FIXTURE, full) == expected
    assert expected.count("block") == 6


def test_nested_inner_is_kept_only_when_outer_and_inner_are_granted():
    text = "a\n{{#only inventory}}\nouter\n{{#only purchase_cost}}\ninner\n{{/only}}\nouter tail\n{{/only}}\nz\n"
    known = frozenset({"inventory", "purchase_cost"})
    both = _render(text, _access(domains={"inventory", "purchase_cost"}), known)
    assert both == "a\nouter\ninner\nouter tail\nz\n"
    outer_only = _render(text, _access(domains={"inventory"}), known)
    assert outer_only == "a\nouter\nouter tail\nz\n"
    inner_only = _render(text, _access(domains={"purchase_cost"}), known)
    assert inner_only == "a\nz\n"
    neither = _render(text, _access(), known)
    assert neither == "a\nz\n"


def test_an_unknown_tag_fails_closed_drops_the_content_and_the_markers_and_warns(caplog):
    text = "a\n{{#only mystery}}\nsecret body\n{{/only}}\nz\n"
    with caplog.at_level(logging.WARNING):
        out = _render(text, _access(), KNOWN)
    assert out == "a\nz\n"
    assert any("mystery" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def test_an_unknown_inline_tag_fails_closed():
    out = _render("a {{#only mystery}}secret{{/only}} z\n", _access(), KNOWN)
    assert "secret" not in out and "{{" not in out


def test_a_missing_close_marker_fails_closed_drops_the_guarded_content_and_warns(caplog):
    text = "a\n{{#only inventory}}\nsecret stuff\nmore secret\n"
    with caplog.at_level(logging.WARNING):
        out = _render(text, _access(), KNOWN)
    assert "secret" not in out
    assert "{{" not in out
    assert out.startswith("a\n")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_an_inline_span_that_runs_over_a_line_break_fails_closed_and_warns(caplog):
    text = "a\nPrice {{#only purchase_cost}}secret cost\nmore secret{{/only}} end\nz\n"
    with caplog.at_level(logging.WARNING):
        out = _render(text, _access(), KNOWN)
    assert "secret" not in out
    assert "{{" not in out
    assert out.startswith("a\n") and out.rstrip("\n").endswith("z")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_stray_close_marker_is_dropped_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        out = _render("a\n{{/only}}\nb\n", _access(), KNOWN)
    assert out == "a\nb\n"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_tag_index_maps_each_tag_to_the_first_line_of_every_block_it_guards():
    from app.services.chatbot.prompt_access import tag_index

    assert tag_index(FIXTURE) == {
        "inventory": ["STOCK block", "STOCK second block"],
        "purchase_cost": ["COST block"],
        "sales_orders.outstanding": ["OUTSTANDING block"],
        "order": ["ORDER OR SALES block"],
        "sales": ["ORDER OR SALES block"],
        "incoming": ["INCOMING block"],
    }


@pytest.mark.parametrize("granted", [(), ("b",), ("a",), ("a", "b")])
def test_nested_inline_spans_fail_closed_unless_the_outer_is_granted(granted):
    text = "pre {{#only a}}x{{#only b}}y{{/only}}z{{/only}} post\n"
    out = _render(text, _access(domains=granted), frozenset({"a", "b"}))
    assert "{{" not in out
    if "a" not in granted:
        assert "x" not in out and "y" not in out and "z" not in out
        assert out.startswith("pre") and out.rstrip("\n").endswith("post")
    elif "b" not in granted:
        assert "y" not in out
