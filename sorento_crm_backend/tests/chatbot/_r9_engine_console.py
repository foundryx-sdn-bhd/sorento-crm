"""An owner console conversation through `engine.run_turn`, for PR #1247 round 9.

Not itself a test file (no `test_` prefix - pytest never collects it).

What is REAL here: the engine, the resolver (the owner's SRTWC286 family and the other
codes of the 26 Sep sessions are seeded into the blank schema, so "check stock
STWC2867" misses and the real trigram did-you-mean offers SRTWC286-SH and
SRTWC286-SH-P, exactly as the owner saw it), the MCP presenter
(`sorento_crm_mcp.presenters.present_response`) and `fetch.output_structurer`.

What is STUBBED: the parser (each turn's verdict is the one the live parser SHOULD
emit for that message, the round 9 contract) and the stock tool's database read. The
stock tool answers the way `StockService._apply_stock_visibility` answers a dealer
whose category has no X set: every product with no quantity `needs_quantity`, every
quantity `too_big` ("🚫 the quantity is more than what I can confirm here, please refer to
your salesman.") - the sentence the owner saw on every answered row.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from app.services.chatbot import engine as engine_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.head import parser as parser_mod

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company
from tests.chatbot.test_rearch_s3_roster_from_resolver import _seed_contact

TOO_BIG = "🚫 the quantity is more than what I can confirm here. Please refer to your salesman."

#: The family the owner's "check stock srtwc286" placed (26 Sep, every session).
OWNER_FAMILY = [
    "SRTWC286-SH",
    "SRTWC286-SH-150",
    "SRTWC286-SH-200",
    "SRTWC286-SH-NEW",
    "SRTWC286-SH-NEW-150",
    "SRTWC286-SH-NEW-200",
    "SRTWC286-SH-NEW-P",
    "SRTWC286-SH-P",
    "SRTWC286-SH-PP",
    "SRTWC286-SH-UF",
]
#: Row 9 of the 10:11Z session names a second family; ELP3754 is the hand test F1
#: one-candidate did-you-mean ("ELP3753 10" -> "Did you mean ELP3754?").
OTHER_CODES = ["SRTWC287-S-150", "ELP3754"]


def _present_response():
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    from sorento_crm_mcp.presenters import present_response

    return present_response


def _seed_products(session_factory, codes: list[str]) -> dict[str, str]:
    """The catalogue rows, `{uuid: code}`."""
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    cat = ProductCategory(
        id=str(uuid.uuid4()),
        category_code="ZZTC-r9",
        category_name="ZZT R9",
        class_label="wc286",
        search_synonyms=[],
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZTU-r9", uom_name="ZZT uom")
    db.add_all([cat, uom])
    db.flush()
    by_uuid: dict[str, str] = {}
    for code in codes:
        pid = str(uuid.uuid4())
        by_uuid[pid] = code
        db.add(
            Product(
                id=pid,
                product_code=code,
                product_name=f"ZZT {code}",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=1,
            )
        )
    db.commit()
    return by_uuid


class EngineConsole:
    """One dealer on the :3087 console, turn by turn, through `engine.run_turn`."""

    def __init__(self, session_factory, monkeypatch, stub_access, *, phone: str) -> None:
        self.session_factory = session_factory
        self.transcript: list[str] = []
        self.blocks: list[str] = []
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        #: Every `turn_apply` Plan's trace, in call order (issue #1352: the judgement a
        #: turn made - its decision and the rules that fired - read off the real apply).
        self.traces: list[Any] = []
        #: The LAST apply trace of each turn, one per `say` (a turn can run apply twice).
        self.turn_traces: list[Any] = []
        self._next: dict[str, Any] | None = None
        #: Issue #1352: set to a number and the stock tool answers in the `compact`
        #: shape (a staff contact who may see quantities: "Total: <n>"), so a stock
        #: turn ANSWERS rather than asking the dealer's "How many units?". None keeps
        #: the dealer availability shape every earlier test pins.
        self.stock_on_hand: int | None = None
        self._turns = 0
        #: `session_patch` of the last turn, sent back as the next turn's
        #: `previous_conversation_state` - the console's own carry.
        self.state: dict[str, Any] | None = None
        _seed_contact(session_factory, phone=phone)
        _link_contact_company(session_factory, company_id=SORENTO)
        self.codes = _seed_products(session_factory, OWNER_FAMILY + OTHER_CODES)
        self.uuid_of = {code: pid for pid, code in self.codes.items()}
        stub_access()
        monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)
        present = _present_response()

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub",
                prompt_version=1,
                provider="openai",
                model="gpt-test",
                api_key="sk-test",
            )

        def fake_parse(config, user_block):
            self.blocks.append(user_block)
            assert self._next is not None, "a turn ran with no stubbed verdict"
            return self._next

        def fake_call_tool(client, name: str, args: dict[str, Any]) -> str:
            self.tool_calls.append((name, args))
            if name != "crm_inventory_stock_balance_list":
                return json.dumps({"answers": []})
            if self.stock_on_hand is not None:
                summary = [
                    {
                        "product_id": pid,
                        "product_code": self.codes[pid],
                        "total_on_hand": self.stock_on_hand,
                        "locations": [{"warehouse_code": "BRW", "quantity_on_hand": self.stock_on_hand}],
                    }
                    for pid in args.get("product_ids") or []
                    if pid in self.codes
                ]
                return present(
                    name,
                    json.dumps(
                        {
                            "data": [],
                            "pagination": {"total": len(summary), "page": 1, "limit": 50},
                            "stock_visibility": {"mode": "compact", "warehouse_codes": None, "source": "contact"},
                            "stock_summary": summary,
                            "last_updated_at": "2026-09-29T01:00:00",
                        }
                    ),
                )
            wanted = args.get("requested_quantities") or {}
            if isinstance(wanted, str):
                # The route's query param: a JSON object, as `turn_runtime` sends it.
                wanted = json.loads(wanted)
            entries = []
            for pid in args.get("product_ids") or []:
                code = self.codes.get(pid)
                if code is None:
                    continue
                qty = wanted.get(pid)
                entries.append(
                    {
                        "product_id": pid,
                        "product_code": code,
                        "product_name": code,
                        "needs_quantity": qty is None,
                        "requested_qty": qty,
                        "branch": "too_big" if qty is not None else None,
                        "cap_unset": True,
                        "category_name": "ZZT R9",
                        "eta": None,
                        "packing_list": None,
                    }
                )
            payload = {
                "data": [],
                "pagination": {"total": 0, "page": 1, "limit": 50},
                "empty": True,
                "stock_visibility": {"mode": "availability", "warehouse_codes": None, "source": "contact"},
                "stock_availability": entries,
                "last_updated_at": "2026-09-26T14:00:00",
            }
            return present(name, json.dumps(payload))

        from app.services.ai_assistant_service import MCPRuntimeClient

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", fake_parse)
        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)

        real_apply = engine_mod.turn_apply

        def traced_apply(*args: Any, **kwargs: Any):
            state_out, plan = real_apply(*args, **kwargs)
            self.traces.append(plan.trace)
            return state_out, plan

        monkeypatch.setattr(engine_mod, "turn_apply", traced_apply)

    def say(self, message: str, v: dict[str, Any]) -> str:
        self._turns += 1
        self._next = v
        out = engine_mod.run_turn(
            _envelope(
                is_test=True,
                previous_conversation_state=self.state,
                message={
                    "event_type": "message.received",
                    "contact": {"id": CONTACT_ID},
                    "message": {
                        "messageId": f"ZZT-r9-{uuid.uuid4().hex[:10]}",
                        "contactId": CONTACT_ID,
                        "channelId": "whatsapp",
                        "traffic": "incoming",
                        "message": {"type": "text", "text": message},
                    },
                },
            ),
            session_factory=self.session_factory,
        )
        assert out.error is None, out.error
        self.state = out.session_patch
        text = (out.reply or {}).get("text") or ""
        self.turn_traces.append(self.traces[-1] if self.traces else None)
        self.transcript += [message, f"-> {text}"]
        return text

    @property
    def last_block(self) -> str:
        return self.blocks[-1]

    @property
    def last_trace(self) -> Any:
        return self.traces[-1]

    @property
    def stored_question(self) -> dict[str, Any] | None:
        """The open question the last turn stored for the next one (`tail.py`)."""
        state = self.state if isinstance(self.state, dict) else {}
        question = state.get("open_question")
        return question if isinstance(question, dict) else None


def stock(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """A stock ask as the parser reads it."""
    return verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=list(entities),
        **overrides,
    )


def product(raw: str, quantity: Any = None) -> dict[str, Any]:
    return entity(raw, hint="product", quantity=quantity)


def answer(mode: str | None, *, picked=(), items=(), qty_for_all=None) -> dict[str, Any]:
    """The round 9 `open_question_answer` object."""
    return {
        "mode": mode,
        "picked": list(picked),
        "items": [
            {"position": p, "code": c, "qty": q} for (p, c, q) in items
        ],
        "qty_for_all": qty_for_all,
    }


def reply(**overrides: Any) -> dict[str, Any]:
    """A follow-up message the parser read against the open question."""
    base = {"domain_hint": "inventory", "intent_hint": "check_stock", "entities": []}
    base.update(overrides)
    return verdict(**base)


def numbered(head: str, codes: list[str]) -> str:
    return "\n".join([head, *[f"{i}. {code}" for i, code in enumerate(codes, 1)]])
