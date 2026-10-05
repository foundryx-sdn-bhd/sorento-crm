"""The escalation lane writes NO chat history (coder's own test, S5 decision 5 Sep 2026).

`sub-add-comment-respond` does two things when it runs: the respond.io comment AND a CRM
chat-history POST. It keeps doing both when the caller executes the `add_comment` action, so
a write here would DOUBLE the comment - one row from this lane, one from the sub - and the
second would arrive minutes later with a different author.

Asserted by ROW COUNT on the real (blank) Postgres schema rather than by grepping the
source: a count catches an import added three layers down, where a grep of this one module
would not. The source check is kept as a second, cheaper net for the obvious case.

Nothing here reaches an LLM, n8n or respond.io.
"""
from __future__ import annotations

import inspect

from sqlalchemy import text

from tests.chatbot.test_s5_escalation_lane import _ctx, _item, _services


def _chat_history_rows(db) -> int:
    return db.execute(text("SELECT count(*) FROM chat_histories")).scalar() or 0


def test_the_assignment_arm_writes_no_chat_history(session_factory) -> None:
    """The full assignment path - assignee, SLA, comment, both sends - and not one row."""
    from app.services.chatbot.lanes.escalation import run

    db = session_factory()
    before = _chat_history_rows(db)

    ctx = _ctx(routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries"})
    item = _item(
        brand_code=None, company_id=None, company_name=None, routing_source="none", team="customer_service"
    )
    result = run(ctx, item, services=_services())

    assert [a["kind"] for a in result["actions"]] == [
        "assign_conversation",
        "add_comment",
        "send_message",
    ]
    assert _chat_history_rows(session_factory()) == before, (
        "the escalation lane wrote chat history; `sub-add-comment-respond` already does "
        "that when the caller executes the add_comment action, so this would double it"
    )


def test_the_comment_action_carries_the_respond_user_id_and_no_mention_markup() -> None:
    """The executor turns `mention_user_ids` into the sub's `user_id`; the sub prefixes the
    `{{@user.<id>}}` markup itself, so the text must not carry any."""
    from app.services.chatbot.lanes.escalation import run

    ctx = _ctx(routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries"})
    item = _item(
        brand_code=None, company_id=None, company_name=None, routing_source="none", team="customer_service"
    )
    services = _services(
        assignee={"assignee_id": "usr-9", "assignee_respond_user_id": "respond-usr-9"}
    )

    comment = next(a for a in run(ctx, item, services=services)["actions"] if a["kind"] == "add_comment")

    assert comment["mention_user_ids"] == ["respond-usr-9"]
    assert "{{@" not in comment["text"] and "@user." not in comment["text"]


def test_the_lane_module_does_not_reach_for_chat_history() -> None:
    """The cheap net, for the case the row count would only catch after someone ran it.

    Docstrings are stripped before the check. The module's own docstring EXPLAINS that it
    must not write chat history and names this test file, so a raw-text grep would fail on
    the very comment that documents the rule.
    """
    import ast

    import app.services.chatbot.lanes.escalation as escalation_mod

    tree = ast.parse(inspect.getsource(escalation_mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    for banned in ("chat_histories", "ChatHistory", "chat_history"):
        assert banned not in code, f"the escalation lane reaches for {banned!r}"
