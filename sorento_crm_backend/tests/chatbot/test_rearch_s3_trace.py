"""S3 - trace carries `apply`, `memory`, `prompt_text` (AC-1549, PLAN-chatbot-turn-
rearch.md "Turn order" G, "Testing seams").

Unlike every other S3 file in this lane, `app.services.chatbot.trace_detail.
compose_trace_detail` ALREADY EXISTS (chatbot-growth-r1, Slice D1) - so this file is
RED for an ASSERTION reason, not a missing-module one: measured today,
`compose_trace_detail`'s return dict is `{stages, parse, decay, open_question, focus,
tool, crossdomain, reveals, session}` (`app/services/chatbot/trace_detail.py:242-252`)
- no `apply`, `memory` or `prompt_text` key. `ChatbotTurnDetailResponse.trace_detail`
is a loose `dict[str, Any]` (already commented at its own definition as deliberately
loose so a NEW `TurnTrace.add` kind is not silently dropped by a strict model - see
`app/schemas/chatbot_turn.py:46-58`), so the API-level test below is a genuine
end-to-end check of the endpoint, not merely a fixture-shape assertion the
`response_model` would drop.

Test pattern (hand-built trace records, real endpoint) copied from
`test_turn_detail_api.py::TestComposedFromHandBuiltTrace` - reuses its `_seed_turn` /
`_trace_record` / `client` / `db` / `BASE` / `VIEW` / `_permissions` fixtures directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.services.chatbot import trace_detail as trace_detail_mod
from app.models.chatbot_turn import ChatbotTurn

from tests.chatbot.test_turns_admin_api import (
    _GRANTS,
    _permissions,  # noqa: F401
    _seed_turn,
    _trace_record,
    BASE,
    client,  # noqa: F401
    db,  # noqa: F401
    VIEW,
)


def _contact(label: str = "") -> str:
    return f"ZZT-contact-trace-{label}-{uuid.uuid4().hex[:8]}"


def _apply_record() -> dict:
    return {
        "kind": "apply",
        "at": datetime.now(timezone.utc).isoformat(),
        "verdict": {"domain_hint": "inventory"},
        "state_diff": {"focus.products": ["SRTWC287"]},
        "narrowing": ["inventory.product:list_all"],
        "reconciled": [],
        "plan": {"domains": ["inventory"], "denied": []},
    }


def _memory_record() -> dict:
    return {
        "kind": "memory",
        "at": datetime.now(timezone.utc).isoformat(),
        "focus": {"before": {}, "after": {"products": ["SRTWC287"]}, "writer": "apply"},
        "profile": {"before": {"tier": None}, "after": {"tier": None}, "writer": "contact"},
        "episodes": {"before": [], "after": [], "writer": "tail"},
    }


def _prompt_text_record(text: str = "ZZT rendered user block") -> dict:
    return {"kind": "prompt_text", "at": datetime.now(timezone.utc).isoformat(), "text": text}


class TestComposeTraceDetailUnit:
    def test_apply_key_present_with_named_fields(self) -> None:
        row = ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=_contact("apply"),
            message_id="ZZT-trace-apply-1",
            ingress="console",
            trace=[_trace_record("received"), _apply_record()],
        )

        detail = trace_detail_mod.compose_trace_detail(row)

        assert "apply" in detail, detail.keys()
        apply_entry = detail["apply"]
        for field in ("verdict", "state_diff", "narrowing", "reconciled", "plan"):
            assert field in apply_entry, f"apply record missing {field!r}: {apply_entry!r}"

    def test_memory_key_present_with_three_shelves_and_writer(self) -> None:
        row = ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=_contact("memory"),
            message_id="ZZT-trace-memory-1",
            ingress="console",
            trace=[_trace_record("received"), _memory_record()],
        )

        detail = trace_detail_mod.compose_trace_detail(row)

        assert "memory" in detail, detail.keys()
        memory_entry = detail["memory"]
        for shelf in ("focus", "profile", "episodes"):
            assert shelf in memory_entry, f"memory record missing shelf {shelf!r}: {memory_entry!r}"
            assert "writer" in memory_entry[shelf], memory_entry[shelf]

    def test_customer_scope_events_are_listed_with_reason_and_ids(self) -> None:
        """CHATBOT-SELFREF-SCOPE R4: every `customer_scope` event, in order, with the
        fields the panel prints; a `False` rides through, an absent field is left out."""
        at = datetime.now(timezone.utc).isoformat()
        row = ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=_contact("scope"),
            message_id="ZZT-trace-scope-1",
            ingress="console",
            trace=[
                _trace_record("received"),
                {
                    "kind": "customer_scope", "at": at, "refused": "customer_not_permitted",
                    "reason": "typed_word_matched_only_other_customers", "typed": ["water tap"],
                    "dropped": ["f1"], "self_reference": True,
                },
                {"kind": "customer_scope", "at": at, "decision": "scoped_to_links", "ids": ["a"], "self_reference": False},
            ],
        )

        detail = trace_detail_mod.compose_trace_detail(row)

        assert detail["customer_scope"] == [
            {
                "at": at, "refused": "customer_not_permitted",
                "reason": "typed_word_matched_only_other_customers", "dropped": ["f1"],
                "typed": ["water tap"], "self_reference": True,
            },
            {"at": at, "decision": "scoped_to_links", "ids": ["a"], "self_reference": False},
        ]

    def test_prompt_text_key_present_as_string_capped_at_64kb(self) -> None:
        huge = "x" * 100_000
        row = ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=_contact("prompt"),
            message_id="ZZT-trace-prompt-1",
            ingress="console",
            trace=[_trace_record("received"), _prompt_text_record(huge)],
        )

        detail = trace_detail_mod.compose_trace_detail(row)

        assert "prompt_text" in detail, detail.keys()
        assert isinstance(detail["prompt_text"], str), detail["prompt_text"]
        assert len(detail["prompt_text"]) <= 65536, (
            f"prompt_text must be capped at 64 KB, got {len(detail['prompt_text'])} chars"
        )


class TestTurnDetailApiExposesAllThree:
    def test_get_turn_returns_apply_memory_prompt_text(self, client, db) -> None:
        _GRANTS.clear()
        _GRANTS.add(VIEW)
        contact = _contact("api")
        turn = _seed_turn(
            db,
            contact_respond_id=contact,
            status="done",
            branch_kind="business_query",
            trace=[
                _trace_record("received"),
                _apply_record(),
                _memory_record(),
                _prompt_text_record(),
            ],
        )

        resp = client.get(f"{BASE}/{turn.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        trace_detail = body.get("trace_detail") or {}

        assert "apply" in trace_detail, trace_detail.keys()
        assert "memory" in trace_detail, trace_detail.keys()
        assert "prompt_text" in trace_detail, trace_detail.keys()
