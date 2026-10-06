"""WA-MSG-TRIM (owner, 3 Oct 2026): an out-of-scope escalation sends ONE customer message.

Since 1 Oct 2026 Meta charges every outgoing WhatsApp message, so the lane's old pair
("Please wait for a moment." before the assignment, "routed to the respective
person-in-charge" after it) costs two messages for one handover. The owner approved a
single line: "Routed to your PIC, they will reply shortly."

Plan: `documentation/plans/chatbot/PLAN-wa-msg-trim.md`.
"""
from __future__ import annotations

from tests.chatbot.test_s5_escalation_lane import _assignment_ctx_and_item, _services

ROUTED = "Routed to your PIC, they will reply shortly."


def _sends(actions: list[dict]) -> list[str]:
    return [a["text"] for a in actions if a["kind"] == "send_message"]


def test_live_handover_sends_one_message() -> None:
    from app.services.chatbot.lanes.escalation import run

    ctx, item = _assignment_ctx_and_item()
    services = _services(
        assignee={"assignee_respond_user_id": "respond-usr-7", "assignee_id": "usr-7", "is_already_assigned": False},
    )

    result = run(ctx, item, services=services)

    assert [a["kind"] for a in result["actions"]] == ["assign_conversation", "add_comment", "send_message"]
    assert _sends(result["actions"]) == [ROUTED]


def test_already_assigned_handover_sends_one_message() -> None:
    from app.services.chatbot.lanes.escalation import run

    ctx, item = _assignment_ctx_and_item()
    services = _services(assignee={"is_already_assigned": True})

    result = run(ctx, item, services=services)

    assert [a["kind"] for a in result["actions"]] == ["add_comment", "send_message"]
    assert _sends(result["actions"]) == [ROUTED]


def test_dry_run_preview_shows_the_same_single_message(session_factory) -> None:
    from app.services.chatbot.lanes.escalation import run

    ctx, item = _assignment_ctx_and_item()

    result = run(ctx, item, services=_services(), dry_run=True)

    assert [a["kind"] for a in result["actions"]] == ["assign_conversation", "add_comment", "send_message"]
    assert _sends(result["actions"]) == [ROUTED]
    assert all(a.get("dry_run") is True for a in result["actions"])


def test_the_single_message_reads_in_the_reply_language() -> None:
    """CHAT-LANGUAGE (#1448) translates the lane's copy through the label catalog, so the one
    message the lane now sends must be catalogued too, or ms/zh customers get English."""
    from app.services.chatbot import label_catalog
    from app.services.chatbot.label_catalog import Localizer

    def loc(lang: str) -> Localizer:
        return Localizer(lang, label_catalog.defaults(lang))

    assert loc("ms").reply(ROUTED) == "Telah diserahkan kepada PIC anda, mereka akan membalas tidak lama lagi."
    assert loc("zh").reply(ROUTED) == "已转交给您的负责人（PIC），他们会尽快回复。"
    assert loc("en").reply(ROUTED) == ROUTED
