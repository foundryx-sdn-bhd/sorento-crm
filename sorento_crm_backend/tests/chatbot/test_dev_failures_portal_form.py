"""DEV-FAILURES RC-B: a portal link opens the form the customer asked for and the reply names it
(documentation/plans/chatbot/PLAN-dev-failures-12oct.md).

Pure: no database, no LLM. The parser's `portal_form` is given; whether the live parser fills it
is `test_dev_failures_live_parser.py` (opt-in).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.chatbot.head.parser import DECLARED_KEYS, PARSE_OUTPUT_JSON_SCHEMA, TOLERATED_ABSENT
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.chatbot import copy as reply_copy
from app.services.chatbot.lanes.business import fetch
from app.services.portal_service import SUPPORTED_TYPES

ADDENDUM = (
    Path(__file__).resolve().parents[2] / "alembic" / "data"
    / "chatbot_semantic_parser.portal-form.addendum.txt"
)
LINK = "http://crm.example/portal/c/ABC?token=XYZ"


def test_parser_schema_declares_portal_form() -> None:
    assert "portal_form" in PARSE_OUTPUT_JSON_SCHEMA["properties"]
    assert "portal_form" in DECLARED_KEYS
    # Older prompt versions never emit it: absent reads as "no form named".
    assert "portal_form" in TOLERATED_ABSENT


def test_addendum_lists_every_deep_linkable_form() -> None:
    text = ADDENDUM.read_text(encoding="utf-8")
    assert '"portal_form"' in text
    for code in SUPPORTED_TYPES:
        assert code in text


@pytest.mark.parametrize("code", SUPPORTED_TYPES)
def test_named_form_becomes_submission_type(code: str) -> None:
    parse = {"domain_hint": "portal_link", "portal_form": code}
    assert fetch.portal_submission_type(parse) == code


@pytest.mark.parametrize(
    "parse",
    [
        {"domain_hint": "portal_link", "portal_form": None},
        {"domain_hint": "portal_link"},
        # A code the portal cannot deep-link (the price tag request has its own routes) is no form.
        {"domain_hint": "portal_link", "portal_form": "price_tag_request"},
        {"domain_hint": "portal_link", "portal_form": "made_up"},
        # Only a portal link ask carries it.
        {"domain_hint": "forms", "portal_form": "complaint"},
    ],
)
def test_no_form_means_no_submission_type(parse: dict) -> None:
    assert fetch.portal_submission_type(parse) is None


def test_transformer_passes_submission_type_to_the_portal_tool_only() -> None:
    trigger = {
        "tool": "crm_portal_link_get",
        "entities": [],
        "semantic_input": {"contact_id": "c1", "space_id": "s1", "submission_type": "stock_inquiry"},
    }
    assert fetch.entity_ids_transformer(trigger)["submission_type"] == "stock_inquiry"
    other = dict(trigger, tool="crm_stock_get")
    assert "submission_type" not in fetch.entity_ids_transformer(other)


COPY = {
    "complaint": ("Here is your portal link for a complaint.", "Berikut pautan portal anda untuk aduan.", "这是您提交投诉的门户链接。"),
    "stock_inquiry": ("Here is your portal link for a stock inquiry.", "Berikut pautan portal anda untuk pertanyaan stok.", "这是您提交库存查询的门户链接。"),
    "purchase_request": ("Here is your portal link for a purchase request.", "Berikut pautan portal anda untuk permintaan pembelian.", "这是您提交采购申请的门户链接。"),
    "sponsorship_form": ("Here is your portal link for the sponsorship form.", "Berikut pautan portal anda untuk borang tajaan.", "这是您填写赞助表格的门户链接。"),
    None: ("Here is your portal link.", "Berikut pautan portal anda.", "这是您的门户链接。"),
}


@pytest.mark.parametrize("code", list(COPY))
def test_owner_lines_are_editable_copy_in_three_languages(code) -> None:
    name = fetch.PORTAL_LINK_COPY.get(code or "", "portal_link")
    copy = reply_copy.fallback_copy()
    en, ms, zh = COPY[code]
    assert (copy.render_in(name, "en"), copy.render_in(name, "ms"), copy.render_in(name, "zh")) == (en, ms, zh)
    # One registry key per language, so the owner edits each in Settings > AI Prompts.
    for suffix in ("", ".ms", ".zh"):
        assert f"chatbot_reply_{name}{suffix}" in PROMPT_KEYS


def test_reply_prints_the_copy_line_above_the_link() -> None:
    url = f"{LINK}&type=sponsorship_form"
    out = fetch.output_structurer(
        {"portal_url": url},
        {"tool": "crm_portal_link_get", "portal_intro": "Berikut pautan portal anda untuk borang tajaan."},
    )
    text = out.get("response") or ""
    assert text.startswith("Berikut pautan portal anda untuk borang tajaan.")
    assert url in text


def test_reply_without_copy_keeps_todays_line() -> None:
    out = fetch.output_structurer({"portal_url": LINK}, {"tool": "crm_portal_link_get"})
    assert (out.get("response") or "").startswith("Here is your portal link.")


def _run_portal_fetch(parse: dict, localizer) -> tuple[list[dict], dict]:
    """`run_fetch` end to end for a portal_link ask, the MCP call recorded (reviewer blocker 1:
    the wiring between the parse, the tool args and the reply is guarded here, not only the
    pieces)."""
    import json

    from app.services.chatbot.lanes import business
    from app.services.chatbot.lanes.business.services import FetchServices

    calls: list[dict] = []
    url = f"{LINK}&type={parse.get('portal_form') or ''}"

    def mcp_call(name, args):
        calls.append({"name": name, **dict(args)})
        return json.dumps({"portal_url": url})

    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": []},
        "ctx": {"parse": {"output": parse}, "contact": {"id": "c1"}, "localizer": localizer},
    }
    fragment = business.run_fetch(payload, services=FetchServices(mcp_call=mcp_call), dry_run=False)
    return calls, fragment["fetch"]


def test_run_fetch_deep_links_and_names_the_form_in_the_reply_language() -> None:
    from types import SimpleNamespace

    calls, fetched = _run_portal_fetch(
        {"domain_hint": "portal_link", "portal_form": "complaint"}, SimpleNamespace(language="ms")
    )
    assert [c["name"] for c in calls] == ["crm_portal_link_get"]
    assert calls[0]["submission_type"] == "complaint"
    assert fetched["response"].startswith("Berikut pautan portal anda untuk aduan.")


def test_run_fetch_without_a_form_or_localizer_sends_the_bare_english_line() -> None:
    calls, fetched = _run_portal_fetch({"domain_hint": "portal_link", "portal_form": None}, None)
    assert "submission_type" not in calls[0]
    assert fetched["response"].startswith("Here is your portal link.")
