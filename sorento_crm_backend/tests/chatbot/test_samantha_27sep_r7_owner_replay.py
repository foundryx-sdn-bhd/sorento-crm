"""#1262 fix lane round 7: the owner's hand test of round 6 (27 Sep, 22:49 to 22:51 MYT,
console :3092, parser v40), replayed turn by turn.

The transcript, in order (the first try of turn 1 failed in the parser and left no state):

1. "DO brand sorneto for cheng huat sentul" -> Brand: MOCHA, "no order matched", an
   escalate offer and the routing picker.
2. "DO brand sorento for cheng huat sentul" -> Brand: SORENTO, the list.
3. "DO brand sorneto for cheng huat sentul" -> Brand: SORENTO this time.
4. "how aobut mocha" -> out_of_scope, escalated.
5. "DO brand mocha for cheng huat sentul" -> Brand: MOCHA, no order found, although this
   customer has Mocha company documents (M2609-0400, M2609-0113, M2608-1374, M2608-1346,
   M2608-1329, REP2608-0195).
6. "DO for cheng huat sentul" -> still Brand: MOCHA.
7. "all brand" and 8. "clear the brand" -> Brand: MOCHA again, the customer line
   exploded into every account.

Why (line numbers at 1d19726a):

* Turn 1 vs 3: the brand id came from the parser's `canonical_code`
  (`lanes/business/__init__.py:970`, the raw word OR `_brand_canonical_words`). The
  published KNOWN BRANDS words tell the model to write the brand AS LISTED, so for a
  word on no list ("sorneto") the model guesses, and a guess is not the same twice: one
  run wrote "MOCHA", the next "SORENTO". The typed word itself was only ever matched
  exactly (`:971-975`), so it never decided anything.
* Turn 4: the parser read "how aobut mocha" as a request for help with no domain, and
  `turn/apply.py:_lane` sends `request_for_help` to the escalation lane
  (`route.py:18`, "out_of_scope"). Nothing reads the message against the open list.
* Turn 5: the orders route filters by `Product.brand_id IN (...)` only
  (`order_service.py:158`); Mocha company products carry no brand row, so the Mocha
  company's documents never match a Mocha brand filter.
* Turn 6: `order_brand_filter` (`turn_runtime.py:470-472`) hands the focus's carried
  brand to `_resolve_outstanding_brand_ids`, which rides it on every turn that typed no
  brand word (`lanes/business/__init__.py:979-997`), a fresh ask included.
* Turns 7 and 8: no reader knows the clear words; the carried brand rode on, and the
  customer line printed each carried ledger's name (the turn typed no customer word).
* The escalate offer: an empty order list is a plain miss, and the miss composer offers
  a person on every miss.

This file types the owner's exact messages in order, with the parser emitting what v40
emitted for them, against the REAL orders route and the REAL presenter over seeded
Sorento and Mocha documents.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: F401 - first app import, resolves the guards cycle

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import Order, OrderLine
from app.models.product import Brand
from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from tests._mc_lookup_seed import MOCHA_ID, customer, product, seed_mocha, warehouse
from tests.chatbot.conftest import validating_resolve_entity
from tests.chatbot._turn_helpers import DO_WINDOW_DATE, do_window
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, REPORT, _order_ask, _Replay
from tests.test_orders_brand_filter import _seed_superadmin

ORDERS_ROUTE = "/api/v1/order-management/orders"
BOTH = frozenset({DEFAULT_COMPANY_ID, MOCHA_ID})

#: The customer's documents: number -> (company, [(product code, brand)]). Brand None is
#: a product with no brand row (every Mocha company product).
DOCUMENTS: dict[str, tuple[str, list[tuple[str, str | None]]]] = {
    "M2609-0400": (MOCHA_ID, [("M90SS-BL-DIY", None)]),
    "M2609-0113": (MOCHA_ID, [("MAB7755-BL", None)]),
    "M2608-1374": (MOCHA_ID, [("MAB7043", None)]),
    "REP2608-0195": (MOCHA_ID, [("MA0101", None)]),
    "202609-2571": (DEFAULT_COMPANY_ID, [("SRTBV180-DIY", "sorento")]),
    "202609-R7MB": (DEFAULT_COMPANY_ID, [("MLM3202", "mocha")]),
    "202609-R7MX": (DEFAULT_COMPANY_ID, [("SRT-R7-MIXED", "sorento"), ("MCH-R7-MIXED", "mocha")]),
}
MOCHA_DOCS = {"M2609-0400", "M2609-0113", "M2608-1374", "REP2608-0195", "202609-R7MB", "202609-R7MX"}
SORENTO_DOCS = {"202609-2571", "202609-R7MX"}
ALL_DOCS = set(DOCUMENTS)
SORENTO_CODES = {"SRTBV180-DIY", "SRT-R7-MIXED"}
MOCHA_CODES = {"M90SS-BL-DIY", "MAB7755-BL", "MAB7043", "MA0101", "MLM3202", "MCH-R7-MIXED"}

ESCALATE_WORDS = ("speak to", "a person", "someone from", "which team", "our team", "escalat")


def _brand(raw: str, canonical: str | None) -> dict[str, Any]:
    return {"raw": raw, "hint": "brand", "canonical_code": canonical, "current_message": True, "confident": True}


def _customer(raw: str = "cheng huat sentul") -> dict[str, Any]:
    return {"raw": raw, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}


def _do_ask(brand: dict[str, Any] | None) -> dict[str, Any]:
    entities = ([brand] if brand else []) + [_customer()]
    # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
    return _order_ask(entities=entities, document=["DO"], status=None, **do_window())


def _help_request(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """What v40 read "how aobut mocha" as: a request for help with no domain."""
    return _parser_output(
        message_type="request_for_help", intent_hint=None, domain_hint=None, entities=entities,
        domain_in_message=False, user_goal="asking about mocha",
    )


def _broaden(**extra: Any) -> dict[str, Any]:
    """"all brand" / "clear the brand": an order read with nothing of its own."""
    return _parser_output(
        domain_hint="order", intent_hint="check_order", entities=[], domain_in_message=False,
        scope_intent="broaden", **{**do_window(), **extra},
    )


class _OwnerChat(_Replay):
    """Real `engine.run_turn`, one contact scoped to Sorento AND Mocha, one session; the
    orders list answered by the REAL route over the seeded documents and rendered by the
    REAL presenter."""

    def __init__(self, session_factory, monkeypatch):
        super().__init__(session_factory, monkeypatch)
        from app.services.chatbot import engine as engine_mod
        from tests.chatbot.test_outstanding_lane import _present_response

        self._seed(session_factory)
        ledgers = [self.sorento_ledger, self.mocha_ledger]

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            tokens = list(body.get("tokens") or [])
            hits = [t for t in tokens if t.casefold() == "cheng huat sentul"]
            return {
                "tokens": tokens,
                "resolutions": [
                    {"token": t, "resolved": True, "matches": [
                        {"uuid": lid, "entity_type": "customer", "canonical_code": t, "match_tier": "exact"}
                        for lid in ledgers
                    ]}
                    for t in hits
                ],
                "unresolved_tokens": [t for t in tokens if t not in hits],
            }

        services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(resolve_entity),
            probe=lambda **_: None,
        )
        monkeypatch.setattr(
            engine_mod.business_services, "production_services", lambda db, *, space_id=None: services
        )

        route_db = session_factory()
        principal = _seed_superadmin(route_db)

        def _override_db():
            yield route_db

        async def _override_scope():
            set_company_scope(route_db, BOTH)
            return BOTH

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
        app.dependency_overrides[apply_company_scope] = _override_scope
        client = TestClient(app)
        present = _present_response()

        def mcp_call(name: str, args: dict) -> str:
            self.captured.append((name, dict(args)))
            if name != ORDERS_LIST:
                return '{"has_result": false, "items": []}'
            params = {k: v for k, v in args.items() if v is not None and k not in ("contact_id", "space_id")}
            resp = client.get(ORDERS_ROUTE, params=params)
            assert resp.status_code == 200, resp.text
            self.route_rows.append(resp.json().get("data") or [])
            return present(name, resp.text)

        self.route_rows: list[list[dict[str, Any]]] = []
        monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call))

    def _seed(self, session_factory) -> None:
        db = session_factory()
        set_company_scope(db, BOTH)
        seed_mocha(db)
        db.execute(
            text(
                "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                "SELECT gen_random_uuid(), id, :company_id FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"cid": str(CONTACT_ID), "company_id": MOCHA_ID},
        )
        mocha = Brand(
            id=str(uuid.uuid4()), brand_name="Mocha", brand_code="MCH", is_active=True, company_id=DEFAULT_COMPANY_ID
        )
        db.add(mocha)
        db.flush()
        self.mocha_brand_id = mocha.id
        brand_of = {"sorento": self.brand_id, "mocha": mocha.id, None: None}
        sorento_cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="CHENG HUAT HARDWARE (SENTUL) SDN BHD - [A/C I]")
        mocha_cust = customer(db, company_id=MOCHA_ID, name="CHENG HUAT HARDWARE (SENTUL) SDN BHD - [IBORN]")
        self.sorento_ledger, self.mocha_ledger = sorento_cust.id, mocha_cust.id
        warehouses = {c: warehouse(db, company_id=c).id for c in (DEFAULT_COMPANY_ID, MOCHA_ID)}
        for number, (company_id, lines) in DOCUMENTS.items():
            cust = mocha_cust if company_id == MOCHA_ID else sorento_cust
            order = Order(
                id=str(uuid.uuid4()), order_number=number, customer_id=cust.id, debtor_name=cust.customer_name,
                is_cancelled=False, company_id=company_id,
                actual_delivery_date=DO_WINDOW_DATE,
            )
            db.add(order)
            db.flush()
            for seq, (code, brand) in enumerate(lines, start=1):
                row = product(db, company_id=company_id, code=code)
                row.brand_id = brand_of[brand]
                db.flush()
                db.add(
                    OrderLine(
                        id=str(uuid.uuid4()), order_id=order.id, product_id=row.id,
                        warehouse_id=warehouses[company_id], quantity=seq, line_sequence=seq, company_id=company_id,
                    )
                )
        db.commit()

    def kind(self) -> str | None:
        """The branch kind the last turn closed with, read off its turn row."""
        from app.models.chatbot_turn import ChatbotTurn

        db = self.session_factory()
        row = db.query(ChatbotTurn).filter(ChatbotTurn.message_id == f"ZZT-r3-brand-carry-{self._turn}").first()
        return row.branch_kind if row is not None else None


@pytest.fixture
def owner_chat(session_factory, monkeypatch):
    chat = _OwnerChat(session_factory, monkeypatch)
    try:
        yield chat
    finally:
        app.dependency_overrides.clear()


def _numbers(reply: str) -> set[str]:
    return {n for n in DOCUMENTS if n in reply}


def _codes(reply: str) -> set[str]:
    return {c for c in SORENTO_CODES | MOCHA_CODES if c in reply}


def _header(reply: str, label: str) -> str | None:
    for line in reply.splitlines():
        if line.strip().startswith(f"{label}:"):
            return line.strip()
    return None


#: The owner's messages in order: (message, v40 emission, expected brand or None for all
#: brands, the documents the list must hold).
OWNER_TURNS: list[tuple[str, dict[str, Any], str | None, set[str]]] = [
    # v40 guessed "Mocha" as the listed brand for the misspelt word on this run.
    ("DO brand sorneto for cheng huat sentul", _do_ask(_brand("sorneto", "Mocha")), "Sorento", SORENTO_DOCS),
    ("DO brand sorento for cheng huat sentul", _do_ask(_brand("sorento", "Sorento")), "Sorento", SORENTO_DOCS),
    # ... and "Sorento" on this one.
    ("DO brand sorneto for cheng huat sentul", _do_ask(_brand("sorneto", "Sorento")), "Sorento", SORENTO_DOCS),
    ("how aobut mocha", _help_request([_brand("mocha", "Mocha")]), "Mocha", MOCHA_DOCS),
    ("DO brand mocha for cheng huat sentul", _do_ask(_brand("mocha", "Mocha")), "Mocha", MOCHA_DOCS),
    ("DO for cheng huat sentul", _do_ask(None), None, ALL_DOCS),
    ("how aobut mocha", _help_request([]), "Mocha", MOCHA_DOCS),
    ("all brand", _broaden(), None, ALL_DOCS),
    ("how aobut mocha", _help_request([_brand("mocha", "Mocha")]), "Mocha", MOCHA_DOCS),
    ("clear the brand", _broaden(), None, ALL_DOCS),
]


def test_owner_transcript_replayed_in_order(owner_chat) -> None:
    chat = owner_chat
    for n, (message, qf, brand, docs) in enumerate(OWNER_TURNS, start=1):
        reply, calls, _ = chat.turn(message, qf)
        kind = chat.kind()
        where = f"turn {n} {message!r}: kind={kind} calls={calls} reply={reply!r}"
        order_calls = [a for name, a in calls if name == ORDERS_LIST]
        assert kind == "business_query", where
        assert REPORT not in [c[0] for c in calls], where
        assert order_calls, f"the delivery order list must run: {where}"
        args = order_calls[-1]
        want_ids = {"Sorento": [chat.brand_id], "Mocha": [chat.mocha_brand_id], None: None}[brand]
        assert (args.get("brand_ids") or None) == want_ids, where
        if brand:
            assert _header(reply, "Brand") == f"Brand: {brand}", where
        else:
            assert _header(reply, "Brand") is None, where
        assert _header(reply, "Customer") == "Customer: CHENG HUAT HARDWARE (SENTUL) SDN BHD - [A/C I], CHENG HUAT HARDWARE (SENTUL) SDN BHD - [IBORN]", where
        assert _numbers(reply) == docs, where
        listed = _codes(reply)
        if brand == "Sorento":
            assert listed <= SORENTO_CODES, where
        if brand == "Mocha":
            assert listed <= MOCHA_CODES, where
        low = reply.casefold()
        assert not any(w in low for w in ESCALATE_WORDS), f"no escalate offer inside a list: {where}"

        if brand is not None:
            assert "find" not in low, f"a brand that resolved is never said back as not found: {where}"


# --------------------------------------------------------------------------- #
# R1: the brand word decides, the same way every time.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("canonical", ["Mocha", "Sorento", None, "MCH"])
def test_the_misspelt_word_gives_sorento_whatever_the_parser_guessed(owner_chat, canonical) -> None:
    chat = owner_chat
    for _ in range(2):
        reply, calls, _ = chat.turn(OWNER_TURNS[0][0], _do_ask(_brand("sorneto", canonical)))
        args = [a for n, a in calls if n == ORDERS_LIST][-1]
        assert args.get("brand_ids") == [chat.brand_id], (canonical, calls)
        assert _header(reply, "Brand") == "Brand: Sorento", reply


def test_brand_rows_for_word_is_deterministic_and_nearest() -> None:
    from app.services.chatbot.turn_runtime import brand_rows_for_word

    brands = [
        {"id": "m", "brand_name": "MOCHA", "brand_code": "MCH"},
        {"id": "s", "brand_name": "SORENTO", "brand_code": "SRT"},
    ]

    def ids(word: str) -> list[str]:
        return [r["id"] for r in brand_rows_for_word(brands, word)]

    for word, want in [
        ("sorento", ["s"]), ("SORENTO", ["s"]), ("srt", ["s"]), ("sorneto", ["s"]), ("sorenot", ["s"]),
        ("sorrento", ["s"]), ("mocha", ["m"]), ("mcoha", ["m"]), ("mch", ["m"]), ("sorento brand", ["s"]),
        ("zorbix", []), ("xyz", []), ("", []), ("brand", []), ("mca", []),
    ]:
        assert [ids(word) for _ in range(3)] == [want] * 3, word


def test_an_unknown_brand_word_is_one_line_and_no_filter(owner_chat) -> None:
    chat = owner_chat
    reply, calls, _ = chat.turn("DO brand zorbix for cheng huat sentul", _do_ask(_brand("zorbix", "Mocha")))
    args = [a for n, a in calls if n == ORDERS_LIST][-1]
    assert not args.get("brand_ids"), calls
    assert _header(reply, "Brand") is None, reply
    assert len([line for line in reply.splitlines() if "zorbix" in line.lower()]) == 1, reply
    assert _numbers(reply) == ALL_DOCS, reply


# --------------------------------------------------------------------------- #
# R3 and R5: a brand word alone switches, the clear words clear, the list re-runs.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("message", ["how aobut mocha", "mocha?", "mocha", "what about mocha brand", "and mocha only"])
def test_a_brand_word_alone_switches_the_open_list(owner_chat, message) -> None:
    chat = owner_chat
    chat.turn(OWNER_TURNS[1][0], OWNER_TURNS[1][1])
    for qf in (_help_request([]), _help_request([_brand("mocha", "Mocha")]), _parser_output(message_type="casual", domain_hint=None, entities=[])):
        reply, calls, _ = chat.turn(message, qf)
        assert chat.kind() == "business_query", reply
        args = [a for n, a in calls if n == ORDERS_LIST][-1]
        assert args.get("brand_ids") == [chat.mocha_brand_id], calls
        assert _header(reply, "Brand") == "Brand: Mocha", reply
        assert _numbers(reply) == MOCHA_DOCS, reply
    reply, calls, _ = chat.turn("sorento", _help_request([]))
    assert [a for n, a in calls if n == ORDERS_LIST][-1].get("brand_ids") == [chat.brand_id], calls
    assert _numbers(reply) == SORENTO_DOCS, reply


@pytest.mark.parametrize(
    "message", ["all brand", "all brands", "any brand", "clear the brand", "remove the brand", "no brand"]
)
def test_the_clear_words_clear_the_brand_and_rerun(owner_chat, message) -> None:
    chat = owner_chat
    chat.turn(OWNER_TURNS[4][0], OWNER_TURNS[4][1])
    for qf in (_broaden(), _help_request([]), _parser_output(message_type="casual", domain_hint=None, entities=[])):
        reply, calls, _ = chat.turn(message, qf)
        assert chat.kind() == "business_query", reply
        args = [a for n, a in calls if n == ORDERS_LIST][-1]
        assert not args.get("brand_ids"), calls
        assert args.get("customer_ids") == [chat.sorento_ledger, chat.mocha_ledger], calls
        assert _header(reply, "Brand") is None, reply
        assert _header(reply, "Customer") == "Customer: CHENG HUAT HARDWARE (SENTUL) SDN BHD - [A/C I], CHENG HUAT HARDWARE (SENTUL) SDN BHD - [IBORN]", reply
        assert _numbers(reply) == ALL_DOCS, reply


def test_a_brand_word_with_no_open_list_is_not_rewritten(session_factory) -> None:
    from app.services.chatbot.order_list import order_list_verdict
    from app.services.chatbot.turn.state import Focus, State

    qf = _help_request([])
    out, state, rule = order_list_verdict(session_factory(), qf, State(focus=Focus(domains=["inventory"])), "mocha")
    assert rule is None and out is qf and state.focus.domains == ["inventory"]


# --------------------------------------------------------------------------- #
# R4: the brand carries on a bare continuation only.
# --------------------------------------------------------------------------- #


def _august() -> dict[str, Any]:
    return _parser_output(
        domain_hint="order", intent_hint="check_order", entities=[], domain_in_message=False,
        date_mode="range", date_filter_start="2026-08-01", date_filter_end="2026-08-31",
    )


def test_a_fresh_ask_with_no_brand_is_all_brands(owner_chat) -> None:
    chat = owner_chat
    chat.turn(OWNER_TURNS[4][0], OWNER_TURNS[4][1])
    reply, calls, _ = chat.turn("DO for cheng huat sentul", _do_ask(None))
    assert not [a for n, a in calls if n == ORDERS_LIST][-1].get("brand_ids"), calls
    assert _header(reply, "Brand") is None, reply
    # ... and the brand does not come back on the continuation after it.
    reply, calls, _ = chat.turn("what about August", _august())
    assert not [a for n, a in calls if n == ORDERS_LIST][-1].get("brand_ids"), calls


def test_a_bare_continuation_keeps_the_brand(owner_chat) -> None:
    chat = owner_chat
    chat.turn(OWNER_TURNS[4][0], OWNER_TURNS[4][1])
    reply, calls, _ = chat.turn("what about August", _august())
    assert [a for n, a in calls if n == ORDERS_LIST][-1].get("brand_ids") == [chat.mocha_brand_id], calls
    assert _header(reply, "Brand") == "Brand: Mocha", reply
    reply, calls, _ = chat.turn(
        "and cheng huat sentul?",
        _parser_output(domain_hint="order", intent_hint="check_order", entities=[_customer()], domain_in_message=False),
    )
    assert [a for n, a in calls if n == ORDERS_LIST][-1].get("brand_ids") == [chat.mocha_brand_id], calls


# --------------------------------------------------------------------------- #
# R6: no escalation inside a list; an empty result is one line.
# --------------------------------------------------------------------------- #


def _year(year: int) -> dict[str, Any]:
    # January of `year`: a whole year is over the 31-day cap and would be refused, not listed
    # (DO-ASK-SIMPLIFY, owner 4 Oct 2026); the test needs a range with no documents in it.
    return _parser_output(
        domain_hint="order", intent_hint="check_order", entities=[], domain_in_message=False,
        date_mode="range", date_filter_start=f"{year}-01-01", date_filter_end=f"{year}-01-31",
    )


def test_an_empty_list_is_one_line_and_keeps_the_conversation(owner_chat) -> None:
    from app.services.chatbot.order_list import EMPTY_LIST_LINE

    chat = owner_chat
    chat.turn(OWNER_TURNS[4][0], OWNER_TURNS[4][1])
    reply, calls, _ = chat.turn("what about 2019", _year(2019))
    assert [a for n, a in calls if n == ORDERS_LIST], calls
    assert chat.kind() == "business_query", reply
    assert _numbers(reply) == set(), reply
    low = reply.casefold()
    assert not any(w in low for w in ESCALATE_WORDS), reply
    assert reply.strip().splitlines()[-1] == EMPTY_LIST_LINE, reply
    assert _header(reply, "Brand") == "Brand: Mocha", reply
    assert _open_question(chat) in (None, {}), "no escalate offer or routing picker is left open"
    # The conversation stays: the next brand word re-runs the list.
    reply, calls, _ = chat.turn("sorento", _help_request([]))
    assert chat.kind() == "business_query", reply
    assert [a for n, a in calls if n == ORDERS_LIST][-1].get("brand_ids") == [chat.brand_id], calls


def _open_question(chat) -> dict[str, Any] | None:
    from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _open_question_of

    q = _open_question_of(chat.session_factory)
    return q if q.get("kind") in ("team_pick", "member_offer", "company_pick") else None


def test_a_request_for_a_person_still_escalates(owner_chat) -> None:
    chat = owner_chat
    chat.turn(OWNER_TURNS[1][0], OWNER_TURNS[1][1])
    chat.turn("can i talk to someone", _help_request([]))
    assert chat.kind() == "out_of_scope"


# --------------------------------------------------------------------------- #
# R2: the Mocha brand is every Mocha company item plus every Mocha-brand product.
# --------------------------------------------------------------------------- #


def test_outstanding_report_brand_mocha_counts_mocha_company_documents(owner_chat) -> None:
    from app.services.outstanding_report_service import outstanding_report

    chat = owner_chat
    db = chat.session_factory()
    set_company_scope(db, BOTH)
    ledgers = [chat.sorento_ledger, chat.mocha_ledger]
    mocha = outstanding_report(db, scope="do", customer_ids=ledgers, brand_ids=[chat.mocha_brand_id])
    sorento = outstanding_report(db, scope="do", customer_ids=ledgers, brand_ids=[chat.brand_id])
    every = outstanding_report(db, scope="do", customer_ids=ledgers)
    assert {r["do_number"] for r in mocha["do_rows"]} == MOCHA_DOCS
    assert {r["do_number"] for r in sorento["do_rows"]} == SORENTO_DOCS
    assert {r["do_number"] for r in every["do_rows"]} == ALL_DOCS


def test_company_scope_is_untouched_by_the_brand_rule(owner_chat) -> None:
    """A session scoped to Sorento alone never reads a Mocha company product, brand
    Mocha or not: the brand rule widens the brand, never the scope."""
    from app.models.product import Product
    from app.services.order_service import narrow_product_ids_by_brand

    chat = owner_chat
    db = chat.session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    stmt = narrow_product_ids_by_brand(db, None, [chat.mocha_brand_id])
    codes = {c for (c,) in db.query(Product.product_code).filter(Product.id.in_(stmt)).all()}
    assert codes == {"MLM3202", "MCH-R7-MIXED"}, codes
    set_company_scope(db, BOTH)
    stmt = narrow_product_ids_by_brand(db, None, [chat.mocha_brand_id])
    codes = {c for (c,) in db.query(Product.product_code).filter(Product.id.in_(stmt)).all()}
    assert codes == MOCHA_CODES, codes
    stmt = narrow_product_ids_by_brand(db, None, [chat.brand_id])
    codes = {c for (c,) in db.query(Product.product_code).filter(Product.id.in_(stmt)).all()}
    assert codes == SORENTO_CODES, codes
