"""QUICK-REPLY-NAMES console pass: the Chatbot Console's own service, parser STUBBED.

The sandbox has no parser API key and no embedding provider, so
`scripts/chatbot_console_check.py` cannot run there. This is its stand-in: every turn
goes through `console_service.run_console_turn`, the function the Chatbot Console
screen's `/console/turn` endpoint calls, with the session variables chained from one
reply to the next exactly as the screen does.

REAL here: the engine, the console service (envelope borrowing, lane switches, the
`quick_replies` the screen renders), the resolver over customers and products seeded
into the blank schema (the three ONG BROTHERS customers of the 6 Oct incident, three
SRT57 codes for a did-you-mean), the MCP presenter.

STUBBED, and labelled so in the PR: the semantic parser (each turn's verdict is the one
the live parser emits for that message, recorded), the access check, the MCP tool call
(the orders list answers empty, the stock tool answers the dealer availability shape as
`tests/chatbot/_r9_engine_console.py` does).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import console_service, turn_runtime
from app.services.chatbot.head import parser as parser_mod
from tests._mc_lookup_seed import customer as seed_customer
from tests.chatbot._r9_engine_console import _present_response, _seed_products, product, stock
from tests.chatbot._turn_helpers import do_window, make_office_access_type
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, stub_access  # noqa: F401 - fixture
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company
from tests.chatbot.test_rearch_s3_roster_from_resolver import _seed_contact
from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, _order_ask

ONG_BROTHERS = (
    "SAMPLE ONG BROTHERS",
    "ONG BROTHERS TILING TRADING SDN BHD",
    "ONG BROTHERS TILING TRADING SAMPLE SDN BHD",
)
DYM_CODES = ["SRT5713", "SRT5732", "SRT57-CR"]


class _Console:
    """The console screen, turn by turn: `run_console_turn` with the state carried."""

    def __init__(self, session_factory, monkeypatch, stub_access) -> None:
        self.session_factory = session_factory
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.state: dict[str, Any] | None = None
        self._next: dict[str, Any] | None = None
        self.run_id = f"ZZT-console-pass-{uuid.uuid4().hex[:8]}"

        _seed_contact(session_factory, phone="+60000000071")
        # V1-DOC-SCOPE: an unlinked contact reads any customer's orders only as staff.
        make_office_access_type(session_factory, CONTACT_ID)
        _link_contact_company(session_factory, company_id=SORENTO)
        self.codes = _seed_products(session_factory, DYM_CODES)
        db = session_factory()
        db.info["company_scope"] = frozenset({SORENTO})
        self.customer_ids = {name: seed_customer(db, company_id=SORENTO, name=name).id for name in ONG_BROTHERS}
        # The console borrows the contact's last real inbound envelope (`_borrow_envelope`).
        db.add(
            ChatbotTurn(
                contact_respond_id=str(CONTACT_ID),
                message_id="ZZT-console-pass-seed",
                ingress="webhook",
                envelope=json.loads(_envelope().model_dump_json()),
                is_test=False,
                status="done",
                stage="sent",
                branch_kind="business_query",
            )
        )
        db.commit()
        stub_access()
        monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)
        present = _present_response()

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
            )

        def fake_parse(config, user_block):
            assert self._next is not None, "a turn ran with no recorded verdict"
            return self._next

        def fake_call_tool(client, name: str, args: dict[str, Any]) -> str:
            self.tool_calls.append((name, dict(args)))
            if name != "crm_inventory_stock_balance_list":
                return json.dumps({"has_result": False, "items": []})
            wanted = args.get("requested_quantities") or {}
            if isinstance(wanted, str):
                wanted = json.loads(wanted)
            entries = [
                {
                    "product_id": pid,
                    "product_code": self.codes[pid],
                    "product_name": self.codes[pid],
                    "needs_quantity": wanted.get(pid) is None,
                    "requested_qty": wanted.get(pid),
                    "branch": "too_big" if wanted.get(pid) is not None else None,
                    "cap_unset": True,
                    "category_name": "ZZT R9",
                    "eta": None,
                    "packing_list": None,
                }
                for pid in args.get("product_ids") or []
                if pid in self.codes
            ]
            payload = {
                "data": [],
                "pagination": {"total": 0, "page": 1, "limit": 50},
                "empty": True,
                "stock_visibility": {"mode": "availability", "warehouse_codes": None, "source": "contact"},
                "stock_availability": entries,
                "last_updated_at": "2026-10-06T09:00:00",
            }
            return present(name, json.dumps(payload))

        from app.services.ai_assistant_service import MCPRuntimeClient

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", fake_parse)
        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
        # The service hardcodes the app's `SessionLocal` as the engine's factory (the same
        # seam `test_console_turn_endpoint.py::stub_console_seams` patches): under the
        # fixture it has to be the scratch schema's, or every read sees an empty database.
        monkeypatch.setattr(console_service, "SessionLocal", session_factory)

    def say(self, text: str, verdict: dict[str, Any]) -> console_service.ConsoleTurnResult:
        self._next = verdict
        mark = len(self.tool_calls)
        result = console_service.run_console_turn(
            self.session_factory(),
            contact_respond_id=str(CONTACT_ID),
            text=text,
            session_vars=self.state,
            run_id=self.run_id,
        )
        self.state = result.session_vars
        self.last_calls = self.tool_calls[mark:]
        row = self.session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        print(
            f"\n[console] {text!r} -> branch={result.branch_kind} status={getattr(row, 'status', None)} "
            f"error={getattr(row, 'error', None)!r}\n  reply={result.reply_text!r}\n  sends={result.send_messages!r}"
            f"\n  quick_replies={result.quick_replies!r}\n  trace={result.trace_summary!r}"
        )
        return result


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):  # noqa: F811 - fixture
    return _Console(session_factory, monkeypatch, stub_access)


def _ong_brothers_ask() -> dict[str, Any]:
    return _order_ask(
        entities=[{"raw": "ong brothers", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
        document=["DO"],
        status="delivered",
        routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries", "team_source": None},
        **do_window(),
    )


def _pick(position: int) -> dict[str, Any]:
    return _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        reference_positions=[position], order_status=None, domain_in_message=False,
    )


def test_console_pass(console: _Console, capsys) -> None:
    rows: list[tuple[str, str, bool, str]] = []

    def check(step: str, expected: str, ok: bool, got: str) -> None:
        rows.append((step, expected, ok, got))

    # 1. The incident conversation: the picker prints its list and sends no buttons.
    r1 = console.say("DO for ong brothers", _ong_brothers_ask())
    text1 = r1.reply_text or ""
    lines = [line for line in text1.splitlines() if line[:2] in ("1.", "2.", "3.")]
    check(
        "1 'DO for ong brothers' -> picker text",
        "Which one do you mean? + 3 numbered lines",
        "Which one do you mean?" in text1 and len(lines) == 3,
        text1,
    )
    check("1 picker quick replies", "[] (no buttons)", r1.quick_replies == [], repr(r1.quick_replies))
    check(
        "1 send_message actions carry no buttons",
        "every send_message quick_replies null",
        all(a.get("quick_replies") is None for a in (r1.actions or []) if a.get("kind") == "send_message"),
        repr([a.get("quick_replies") for a in (r1.actions or []) if a.get("kind") == "send_message"]),
    )
    option2 = lines[1].split(". ", 1)[1] if len(lines) >= 2 else ""
    picked_name = next((name for name in ONG_BROTHERS if option2.startswith(name)), None)

    # 2. "2" picks the second customer.
    r2 = console.say("2", _pick(2))
    order_calls = [a for name, a in console.last_calls if name == ORDERS_LIST]
    check(
        "2 '2' -> orders list fetched for option 2",
        f"customer_ids == [{picked_name}]",
        bool(order_calls) and picked_name is not None and order_calls[-1].get("customer_ids") == [console.customer_ids[picked_name]],
        repr([(name, a.get("customer_ids")) for name, a in console.last_calls]),
    )
    check(
        "2 no re-ask",
        "reply does not ask 'Which one do you mean?'",
        "Which one do you mean?" not in (r2.reply_text or ""),
        r2.reply_text or "",
    )

    # 3. A numbered did-you-mean prints its 1, 2, 3 list and sends no buttons.
    r3 = console.say("check stock srt5764 x 10", stock(product("srt5764", 10)))
    text3 = r3.reply_text or ""
    check(
        "3 'check stock srt5764 x 10' -> numbered did-you-mean",
        "Couldn't find SRT5764. Did you mean: + 3 numbered codes",
        text3.startswith("Couldn't find SRT5764. Did you mean:") and sum(line[:2] in ("1.", "2.", "3.") for line in text3.splitlines()) == 3,
        text3,
    )
    # The live did-you-mean is a `product_pick` roster (`dealer_stock.py`), so its codes
    # print as the numbered list and no button goes out; the "1, 2, 3" set belongs to the
    # n8n-completed `/complete` suggest offer (`answer.py`), which this path never takes.
    check("3 did-you-mean quick replies", "[] (no buttons: product codes)", r3.quick_replies == [], repr(r3.quick_replies))

    # 4. "2" on the did-you-mean picks the second code (a typed number still resolves).
    r4 = console.say("2", _pick(2))
    stock_calls = [a for name, a in console.last_calls if name == "crm_inventory_stock_balance_list"]
    picked_codes = [console.codes.get(pid) for a in stock_calls for pid in (a.get("product_ids") or [])]
    code2 = text3.splitlines()[2].split(". ", 1)[1] if len(text3.splitlines()) >= 3 else ""
    check(
        "4 '2' on the did-you-mean -> stock fetched for the second code",
        f"product_ids == [{code2}]",
        picked_codes == [code2] and code2 != "",
        repr(picked_codes) + " / " + (r4.reply_text or ""),
    )

    width = max(len(r[0]) for r in rows)
    out = ["| step | expected | result |", "| --- | --- | --- |"]
    for step, expected, ok, got in rows:
        out.append(f"| {step.ljust(width)} | {expected} | {'PASS' if ok else 'FAIL: ' + got[:160]!s} |")
    print("\n".join(out))
    failed = [r for r in rows if not r[2]]
    assert not failed, "\n".join(f"{r[0]}: got {r[3]!r}" for r in failed)
