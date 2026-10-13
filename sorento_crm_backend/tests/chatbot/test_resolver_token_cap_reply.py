"""RESOLVER-TOKEN-CAP, the reply half: a code the resolver did not look up (cut at its
ceiling, `entity_resolver.PRE_EXTRACTED_TOKEN_CEILING`) is named on the existing
"Not checked: X, Y." line, never dropped in silence and never mislabelled as
"I could not find" (it may well exist).

Seam chain: `resolve_kinds` -> `not_checked_tokens` (the resolver's `truncated_tokens`,
re-keyed to the word the customer typed, exactly as `unplaced_tokens` does for
`unresolved_tokens`) -> `make_tool_runner` -> `envelope_of.not_checked` -> `compose`.

Fake fixture ids only (public repo): every code is ZZT-prefixed.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.plan import FetchSpec
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._turn_helpers import make_office_access_type
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

CODES = [f"ZZTCAP{i:02d}" for i in range(1, 61)]


def _entity(raw: str) -> dict[str, Any]:
    return {"raw": raw, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}


# --------------------------------------------------------------------------- #
# The join: resolver echo -> the word the customer typed
# --------------------------------------------------------------------------- #


def test_not_checked_tokens_are_keyed_like_unplaced_tokens():
    """The resolver echoes the FOLDED token (`resolve_gate._PRODUCT_FOLD` strips the
    hyphen before it is sent); the reply quotes the code as typed."""
    from app.services.chatbot.turn_runtime import not_checked_tokens

    entities = [_entity("ZZTCAP01"), _entity("ZZTCAP51-GY"), _entity("zztcap52")]
    resolved = {
        "resolutions": [{"token": "ZZTCAP01", "matches": [{"uuid": "u1"}]}],
        "unresolved_tokens": [],
        "truncated_tokens": ["ZZTCAP51GY", "ZZTCAP52"],
    }

    assert not_checked_tokens(entities, resolved) == {
        "zztcap51gy": "ZZTCAP51-GY",
        "zztcap52": "zztcap52",
    }


def test_no_truncation_means_nothing_not_checked():
    from app.services.chatbot.turn_runtime import not_checked_tokens

    assert not_checked_tokens([_entity("ZZTCAP01")], {"resolutions": [], "unresolved_tokens": []}) == {}
    assert not_checked_tokens([_entity("ZZTCAP01")], None) == {}


def test_resolve_kinds_carries_the_cut_tokens(monkeypatch):
    """`ResolveOutcome.not_checked`, off the resolver's own answer, beside `unplaced_tokens`."""
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.lanes.business import resolve_gate

    resolved = {
        "resolutions": [{"token": "ZZTCAP01", "matches": [{"uuid": "u1", "entity_type": "product"}]}],
        "unresolved_tokens": [],
        "truncated_tokens": ["ZZTCAP51", "ZZTCAP52"],
    }
    monkeypatch.setattr(
        resolve_gate,
        "run",
        lambda *a, **k: {"resolved": resolved, "gate": {"compatible_entities": []}, "_exit_kind": "continue"},
    )
    ctx = {
        "parse": {
            "output": {
                "domain_hint": "inventory",
                "entities": [_entity("ZZTCAP01"), _entity("ZZTCAP51"), _entity("ZZTCAP52")],
            }
        }
    }

    outcome = turn_runtime.resolve_kinds(
        object(), ctx=ctx, branch_kind="check_stock", space_id=None, dry_run=True, roster_caps={}
    )

    assert outcome.not_checked == {"zztcap51": "ZZTCAP51", "zztcap52": "ZZTCAP52"}
    assert outcome.unplaced_tokens == {}


# --------------------------------------------------------------------------- #
# The envelope and the reply
# --------------------------------------------------------------------------- #


def _spec(not_checked: list[str] | None = None) -> FetchSpec:
    filters = {"not_checked": list(not_checked)} if not_checked else {}
    return FetchSpec(domain="inventory", entities=[], filters=filters, date_window=None)


def test_envelope_carries_the_cut_codes_beside_the_tasks_own():
    from app.services.chatbot.turn_runtime import envelope_of

    env = envelope_of(
        {"fetch": {"has_result": True, "answers": [{"fields": []}], "response": "x"}},
        _spec(["ZZTCAP09"]),
        [],
        not_checked=["ZZTCAP51", "ZZTCAP09", "ZZTCAP52"],
    )

    assert env["not_checked"] == ["ZZTCAP09", "ZZTCAP51", "ZZTCAP52"]


def test_envelope_without_a_cut_is_unchanged():
    from app.services.chatbot.turn_runtime import envelope_of

    env = envelope_of(
        {"fetch": {"has_result": True, "answers": [{"fields": []}], "response": "x"}},
        _spec(),
        [],
    )

    assert env["not_checked"] == []


def _policy() -> Policy:
    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _state() -> State:
    return State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)


def _fig(code: str, qty: int) -> dict[str, Any]:
    return {
        "fields": [
            {"label": "Product Code", "value": code},
            {"label": "Warehouse", "value": "BUKIT RAJA"},
            {"label": "Quantity On Hand", "value": qty},
        ]
    }


def _envelope(rows: dict[str, int], not_checked: list[str] | None = None) -> dict[str, Any]:
    blocks = [
        f"*Product Code:* {c}\n*Warehouse:* BUKIT RAJA\n*Quantity On Hand:* {q}" for c, q in rows.items()
    ]
    return {
        "domain": "inventory",
        "denied": False,
        "entities": list(rows),
        "product_codes": list(rows),
        "figures": [_fig(c, q) for c, q in rows.items()],
        "files": [],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": "Stock availability:\n\n" + "\n\n".join(blocks),
        "not_checked": list(not_checked or []),
    }


def test_nineteen_answered_codes_each_print_and_nothing_is_not_checked():
    rows = {code: (0 if i % 3 == 0 else i) for i, code in enumerate(CODES[:19])}
    text = compose([_envelope(rows)], _state(), _policy(), ctx=None).text

    for code in CODES[:19]:
        assert f"*Product Code:* {code}" in text, text
    assert "Not checked" not in text
    assert "I could not find" not in text


def test_the_cut_codes_are_named_once_on_the_not_checked_line():
    rows = {code: i + 1 for i, code in enumerate(CODES[:50])}
    text = compose([_envelope(rows, CODES[50:])], _state(), _policy(), ctx=None).text

    assert f"Not checked: {', '.join(CODES[50:])}." in text, text
    assert text.count("Not checked") == 1, text
    assert "I could not find" not in text, text


def test_two_sections_name_the_cut_codes_once():
    """A cut code rides every envelope; it is a fact about the message, said once."""
    rows = {code: i + 1 for i, code in enumerate(CODES[:50])}
    first = _envelope(rows, CODES[50:])
    second = {**_envelope(rows, CODES[50:]), "domain": "incoming"}
    incoming = {**_domain_row("incoming", narrowing={"product": "list_all"}), "label": "incoming stock"}
    stock = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    policy = Policy.from_rows(domains=[stock, incoming], kinds=[], tier_order=TIER_ORDER_FIXTURE)

    text = compose([first, second], _state(), policy, ctx=None).text

    assert text.count("Not checked") == 1, text
    assert f"Not checked: {', '.join(CODES[50:])}." in text, text


def test_a_tasks_own_dropped_slot_is_named_once_per_turn():
    """The line the task stamps for a "just proceed" (`turn/task.py`, one `FetchSpec` per
    task, so one section in practice) shares the once-per-turn rule: a name repeated by
    a second section is not said twice."""
    first = _envelope({"ZZTCAP01": 1}, ["ZZTCAP09"])
    second = {**_envelope({"ZZTCAP01": 1}, ["ZZTCAP09"]), "domain": "incoming"}
    incoming = {**_domain_row("incoming", narrowing={"product": "list_all"}), "label": "incoming stock"}
    stock = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    policy = Policy.from_rows(domains=[stock, incoming], kinds=[], tier_order=TIER_ORDER_FIXTURE)

    text = compose([first, second], _state(), policy, ctx=None).text

    assert text.count("Not checked: ZZTCAP09.") == 1, text


# --------------------------------------------------------------------------- #
# End to end: the real engine and the real resolver
# --------------------------------------------------------------------------- #


class TestRealResolverCeiling:
    """60 typed codes through `engine.run_turn` with the real resolver: the first fifty
    are answered, the last ten are named as not checked, none is "could not find"."""

    def test_sixty_codes_name_the_ten_not_checked(self, session_factory, monkeypatch):
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from tests._mc_lookup_seed import product as seed_product
        from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact

        db = session_factory()
        for code in CODES:
            seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        db.commit()
        _seed_contact(session_factory, variables={})
        # V1-DOC-SCOPE: an unlinked contact reads any customer's orders only as staff.
        make_office_access_type(session_factory, CONTACT_ID)

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[_entity(c) for c in CODES]),
            text_body=" ".join(CODES) + " sales order outstanding",
            msg_id="ZZT-cap-60",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": CODES[0]},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        assert captured, "the answered codes must still be fetched"
        assert f"Not checked: {', '.join(CODES[50:])}." in reply, reply
        assert "I could not find" not in reply, reply
        assert "Couldn't find" not in reply, reply

    def test_nineteen_codes_are_all_looked_up(self, session_factory, monkeypatch):
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from tests._mc_lookup_seed import product as seed_product
        from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact

        codes = CODES[:19]
        db = session_factory()
        for code in codes:
            seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        db.commit()
        _seed_contact(session_factory, variables={})
        # V1-DOC-SCOPE: an unlinked contact reads any customer's orders only as staff.
        make_office_access_type(session_factory, CONTACT_ID)

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[_entity(c) for c in codes]),
            text_body=" ".join(codes) + " sales order outstanding",
            msg_id="ZZT-cap-19",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": codes[0]},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        # `fetch.entity_ids_transformer`'s own count of the uuids it put on the call:
        # before the fix this read 8 of 19 (the prod turn's exact shape).
        diagnostics = [args.get("_diagnostics") or {} for _name, args in captured]
        assert diagnostics, "the codes must be fetched"
        assert max(d.get("total_uuids_passed", 0) for d in diagnostics) == 19, diagnostics
        assert "Not checked" not in reply, reply
        assert "I could not find" not in reply, reply
