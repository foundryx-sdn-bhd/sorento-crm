"""Code first (fix round 13 on PR #833, folding PR #1351 round 2): a typed code
fragment is a PRODUCT-CODE ask.

Owner, 28 Sep 2026: "the upstream differentiate the forward asking and reverse asking by
either if found by product code -> forward asking, if cannot find product code, fallback
to spec search -> reverse asking". On this branch that is ONE rule,
`references._code_matched`, which replaced the code-shaped-token test
(`_has_exact_product_match`) rather than sitting beside it as a second exemption.

Production case (owner, WhatsApp 28 Sep 14:02): the dealer wrote "7820 stock" and got
"2 taps have stock." over the only two MKT7820SS products with on-hand above zero, while
the catalogue holds seven MKT7820SS products. Owner ruling, verbatim on the PR: a typed
code fragment such as "7820" is a PRODUCT-CODE ask, never a reverse ask. It lists every
product whose code contains the fragment, with the stock figures of each (zero on hand
included, outstanding shown), the full count, and no "N taps have stock" header. Reverse
asking stays for asks that describe products by type, category or specification with no
code typed.

The trace (this file's console, before the fix): the parser reads `check_stock` with one
product entity "7820" -> `predicate.derive_require` maps the INTENT to `{"stock": true}`
-> `resolve_gate._names_a_typed_code` reads "7820" as a DESCRIPTION word (the code shape
wants a leading letter), so the require rides to the resolver -> the resolver's AND probe
matches all seven by product code, but the old `references._has_exact_product_match` also
read "7820" as not code-shaped (it is measurement-shaped: digits only), so the HAS branch ran
`resolve_product_set` over the seven code matches -> the stock leg (on hand > 0) kept two
-> `_emit_spec_matches` re-emitted those two as `spec_search` and
`_strip_word_token_product_matches` dropped the seven code matches -> the gate, the fetch
and the answer saw a two-product counted set.

What is REAL here: the engine, the resolver over a seeded catalogue with seeded stock,
the MCP presenter and `fetch.output_structurer`. What is STUBBED: the parser (the verdict
the live parser gave) and the stock tool's database read (it answers the rows it was
asked for, from the same seeded figures).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod

from tests.chatbot._r9_engine_console import _present_response
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company
from tests.chatbot.test_rearch_s3_roster_from_resolver import _seed_contact

#: The seven MKT7820SS products: (code, on hand, outstanding). Two carry stock (the two
#: production answered with), four sit at zero, one has outstanding only.
FAMILY_7820 = [
    ("MKT7820SS-BL-DIY", 177, 0),
    ("MKT7820SS-DIY", 4, 29),
    ("MKT7820SS-CR-DIY", 0, 0),
    ("MKT7820SS-FGD-DIY", 0, 0),
    ("MKT7820SS-FRG-DIY", 0, 0),
    ("MKT7820SS-GM-DIY", 0, 0),
    ("MKT7820SS", 0, 12),
]
#: A basin class with a gunmetal spec word in the name, for the set-path control.
BASINS = [("ZZTB-GMB-01", "GUNMETAL BASIN ONE", 5), ("ZZTB-GMB-02", "GUNMETAL BASIN TWO", 0)]


def _seed_world(session_factory) -> dict[str, dict[str, Any]]:
    from app.models.inventory import Stock, Warehouse
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    tap = ProductCategory(
        id=str(uuid.uuid4()),
        category_code="ZZTC-7820",
        category_name="ZZT TAP",
        class_label="tap",
        search_synonyms=[],
    )
    basin = ProductCategory(
        id=str(uuid.uuid4()),
        category_code="ZZTC-BASIN",
        category_name="ZZT BASIN",
        class_label="basin",
        search_synonyms=[],
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZTU-7820", uom_name="ZZT uom")
    wh = Warehouse(id=str(uuid.uuid4()), warehouse_code="MOCHA-WH", warehouse_name="MERU")
    db.add_all([tap, basin, uom, wh])
    db.flush()
    world: dict[str, dict[str, Any]] = {}
    rows = [(c, f"ZZT {c}", q, o, tap.id) for c, q, o in FAMILY_7820] + [
        (c, n, q, 0, basin.id) for c, n, q in BASINS
    ]
    for code, name, on_hand, outstanding, category_id in rows:
        pid = str(uuid.uuid4())
        db.add(
            Product(
                id=pid,
                product_code=code,
                product_name=name,
                category_id=category_id,
                base_uom_id=uom.id,
                list_price=1,
            )
        )
        db.flush()
        db.add(
            Stock(
                id=str(uuid.uuid4()),
                product_id=pid,
                warehouse_id=wh.id,
                quantity_on_hand=on_hand,
                quantity_reserved=0,
                quantity_damaged=0,
            )
        )
        world[pid] = {"code": code, "on_hand": on_hand, "outstanding": outstanding}
    db.commit()
    return world


class StockConsole:
    """One dealer, turn by turn, through `engine.run_turn`, with detailed stock rows."""

    def __init__(self, session_factory, monkeypatch, stub_access, *, phone: str) -> None:
        self.session_factory = session_factory
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.traces: list[Any] = []
        self._next: dict[str, Any] | None = None
        _seed_contact(session_factory, phone=phone)
        _link_contact_company(session_factory, company_id=SORENTO)
        self.world = _seed_world(session_factory)
        stub_access(attributes=["inventory.sellable"])
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
            assert self._next is not None, "a turn ran with no stubbed verdict"
            return self._next

        def fake_call_tool(client, name: str, args: dict[str, Any]) -> str:
            self.tool_calls.append((name, args))
            if name != "crm_inventory_stock_balance_list":
                return json.dumps({"answers": []})
            rows = []
            for pid in args.get("product_ids") or []:
                row = self.world.get(pid)
                if row is None:
                    continue
                rows.append(
                    {
                        "product": {"product_code": row["code"], "product_name": row["code"]},
                        "warehouse": {"warehouse_code": "MOCHA-WH", "warehouse_name": "MERU"},
                        "quantity_on_hand": row["on_hand"],
                        "sellable": row["on_hand"],
                        "open_so_qty": row["outstanding"],
                    }
                )
            payload = {
                "data": rows,
                "pagination": {"total": len(rows), "page": 1, "limit": 50},
                "last_updated_at": "2026-09-28T11:45:38",
            }
            return present(name, json.dumps(payload))

        from app.services.ai_assistant_service import MCPRuntimeClient

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", fake_parse)
        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)

    def say(self, message: str, v: dict[str, Any]) -> str:
        self._next = v
        out = engine_mod.run_turn(
            _envelope(
                is_test=True,
                message={
                    "event_type": "message.received",
                    "contact": {"id": CONTACT_ID},
                    "message": {
                        "messageId": f"ZZT-7820-{uuid.uuid4().hex[:10]}",
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
        self.traces.append(getattr(out, "trace", None))
        # The WhatsApp markup ("*Product Code:*") stripped, so a test reads the words.
        return ((out.reply or {}).get("text") or "").replace("*", "").replace("_", "")


def _code_fragment_stock(raw: str = "7820") -> dict[str, Any]:
    """"7820 stock" as the live parser read it: a stock ask naming one product token."""
    return verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=[entity(raw, hint="product")],
        requested_attributes=["stock"],
    )


def _assert_every_product_with_figures(text: str) -> None:
    for code, on_hand, outstanding in FAMILY_7820:
        block = next(
            (b for b in text.split("\n\n") if f"Product Code: {code}\n" in b + "\n"),
            None,
        )
        assert block is not None, f"{code} missing from the reply:\n{text}"
        assert f"Quantity On Hand: {on_hand}" in block, block
        assert f"Outstanding: {outstanding}" in block, block
    assert "have stock" not in text.lower(), text
    assert "has stock" not in text.lower(), text
    assert "Showing" not in text, text


@pytest.mark.parametrize("message", ["7820 stock", "check stock 7820", "7820 got stock?"])
def test_a_code_fragment_stock_ask_lists_every_matching_product_with_figures(
    session_factory, monkeypatch, stub_access, message
):
    console = StockConsole(session_factory, monkeypatch, stub_access, phone="+60900000056")
    text = console.say(message, _code_fragment_stock())

    _assert_every_product_with_figures(text)
    stock_calls = [a for n, a in console.tool_calls if n == "crm_inventory_stock_balance_list"]
    assert stock_calls, console.tool_calls
    asked = {console.world[pid]["code"] for pid in stock_calls[-1].get("product_ids") or []}
    assert asked == {c for c, _q, _o in FAMILY_7820}, asked


def test_a_described_set_with_no_code_still_takes_the_counted_set_path(
    session_factory, monkeypatch, stub_access
):
    """Reverse asking stays for a description: "which gunmetal basin has stock" names a
    class word and no code, so the resolver still counts the set over the stock leg."""
    import app.services.product_predicate_service as pps

    console = StockConsole(session_factory, monkeypatch, stub_access, phone="+60900000057")
    seen: list[dict[str, Any]] = []
    real_set = pps.resolve_product_set

    def spy_set(*args, **kwargs):
        seen.append({"require": kwargs.get("require")})
        return real_set(*args, **kwargs)

    monkeypatch.setattr(pps, "resolve_product_set", spy_set)
    console.say(
        "which gunmetal basin has stock",
        verdict(
            domain_hint="inventory",
            intent_hint="check_stock",
            entities=[entity("basin", hint="product_type")],
            requested_attributes=["stock"],
        ),
    )
    assert seen and seen[0]["require"] == {"stock": True}, seen


def _match(code: str, tier: str) -> dict[str, Any]:
    return {"entity_type": "product", "canonical_code": code, "match_tier": tier}


@pytest.mark.parametrize(
    ("tokens", "intersection", "expected"),
    [
        # The production case: a digits-only fragment the AND probe matched by code.
        (["7820"], [_match("MKT7820SS-DIY", "and")], True),
        # Separators and case do not matter.
        (["7820ss"], [_match("MKT7820SS-DIY", "and")], True),
        # A whole code-shaped token matched in AND mode (AC-1305/R1, same rule).
        (["zztwc286"], [_match("ZZTWC286-SH", "and")], True),
        # A word token carries no digit: a described set, HAS still runs.
        (["basin"], [_match("ZZTB-BASIN-01", "and")], False),
        # A nearest-neighbour guess is not a code match.
        (["7820"], [_match("MKT7820SS-DIY", "trgm")], False),
        (["7820"], [_match("MKT7820SS-DIY", "embedding")], False),
        # A measurement the matched codes do not contain is not a code match.
        (["250mm"], [_match("MKT7820SS-DIY", "and")], False),
    ],
)
def test_code_matched_reads_only_a_digit_token_found_in_a_code(tokens, intersection, expected):
    from app.api.v1.system.references import _code_matched

    assert _code_matched({"intersection": intersection}, tokens) is expected


@pytest.mark.parametrize(
    ("token", "match", "expected"),
    [
        # AC-1305: a code-shaped token that resolved by PREFIX is a code match.
        ("zztwc286", _match("ZZTWC286-SH-150", "prefix"), True),
        # Exact and substring tiers, digits-only included.
        ("mkt7820ss-diy", _match("MKT7820SS-DIY", "exact"), True),
        ("7820", _match("MKT7820SS-GM-DIY", "substring"), True),
        # AC-1305: a WORD that hits a product literally coded SORENTO at the exact tier
        # still describes a set.
        ("sorento", _match("SORENTO", "exact"), False),
        # The spec tier re-emits a counted set's members: never a code match.
        ("7820", _match("MKT7820SS-DIY", "spec_search"), False),
    ],
)
def test_code_matched_reads_each_resolution_by_its_own_token(token, match, expected):
    from app.api.v1.system.references import _code_matched

    result = {"resolutions": [{"token": token, "matches": [match]}]}
    assert _code_matched(result) is expected
