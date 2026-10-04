"""#1262 fix lane round 6: the owner's hand test of round 5 (27 Sep, console :3092,
parser v40), "DO brand sorneto for cheng huat sentul".

The delivery order list ran (round 5 R1) but the brand never reached it: the header
printed no Brand line and Mocha documents (M2609-0400 M90SS-BL-DIY, M2608-1374 MAB7043,
REP2608-0195 MA0101) sat beside the Sorento ones.

Why: the published prompt's KNOWN BRANDS rule has the parser emit the word the customer
typed as `raw` ("sorneto") and the brand AS LISTED on the Known brands line as
`canonical_code` ("Sorento"). `turn_runtime._brand_hinted_entities_matching_live`
already reads raw OR canonical_code, so the word was kept off the shared resolver (no
"could not find sorneto"), but `lanes.business._resolve_outstanding_brand_ids` matched
the raw word only, found no live row, and so no `brand_ids` and no `Brand:` line.

R1: the brand resolves off raw OR canonical_code (the same rule the resolver gate
uses), the list is filtered by it, the header prints "Brand: Sorento", and inside a
document only that brand's lines are listed. A brand word matching no live brand is
still said back in one line. R2: the owner's exact message replayed under the words the
branch publishes, against the REAL orders route and the REAL presenter.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401 - first app import, resolves the guards cycle

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import Order, OrderLine
from app.models.product import Brand
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.chatbot_parser_prompt import KNOWN_BRANDS_ADDENDUM
from tests._mc_lookup_seed import customer, product, warehouse
from tests.chatbot.conftest import validating_resolve_entity
from tests.test_orders_brand_filter import _seed_superadmin
from tests.chatbot._turn_helpers import DO_WINDOW_DATE, do_window
from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, REPORT, _Replay, _order_ask

OWNER_MESSAGE = "DO brand sorneto for cheng huat sentul"
ORDERS_ROUTE = "/api/v1/order-management/orders"

SORENTO_CODES = ("SRTBV180-DIY", "SRT-R6-SOLO", "SRT-R6-MIXED")
MOCHA_CODES = ("M90SS-BL-DIY", "MAB7043", "MA0101", "MCH-R6-MIXED")


def _v40_do_list(brand_raw: str, brand_canonical: str | None) -> dict[str, Any]:
    """What the published prompt has the parser emit for the owner's message: `raw` is
    the word typed, `canonical_code` the brand as listed (KNOWN BRANDS addendum)."""
    return _order_ask(
        entities=[
            {"raw": brand_raw, "hint": "brand", "canonical_code": brand_canonical,
             "current_message": True, "confident": True},
            {"raw": "cheng huat sentul", "hint": "customer", "canonical_code": None,
             "current_message": True, "confident": True},
        ],
        document=["DO"],
        status=None,
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        **do_window(),
    )


def test_the_published_prompt_has_the_parser_emit_the_listed_brand_as_canonical() -> None:
    """The emission above is the published words' own reading, not a guess."""
    assert '"canonical_code" is the brand name exactly AS LISTED' in KNOWN_BRANDS_ADDENDUM
    assert "never the customer's own" in KNOWN_BRANDS_ADDENDUM


def _seed_documents(session_factory, sorento_brand_id: str) -> tuple[str, dict[str, str]]:
    """Cheng Huat Sentul's delivery orders, Sorento and Mocha products, one mixed."""
    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    mocha = Brand(
        id=str(uuid.uuid4()), brand_name="Mocha", brand_code="MCH", is_active=True, company_id=DEFAULT_COMPANY_ID
    )
    db.add(mocha)
    db.flush()
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="CHENG HUAT HARDWARE (SENTUL)")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    products: dict[str, str] = {}
    for code in SORENTO_CODES + MOCHA_CODES:
        row = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        row.brand_id = sorento_brand_id if code in SORENTO_CODES else mocha.id
        products[code] = row.id
    db.flush()
    documents = {
        "M2609-0400": ["M90SS-BL-DIY"],
        "M2608-1374": ["MAB7043"],
        "REP2608-0195": ["MA0101"],
        "M2608-1180": ["SRTBV180-DIY"],
        "DO-R6-SOLO": ["SRT-R6-SOLO"],
        "DO-R6-MIXED": ["SRT-R6-MIXED", "MCH-R6-MIXED"],
    }
    for number, codes in documents.items():
        order = Order(
            id=str(uuid.uuid4()), order_number=number, customer_id=cust.id, debtor_name=cust.customer_name,
            is_cancelled=False, company_id=DEFAULT_COMPANY_ID,
            actual_delivery_date=DO_WINDOW_DATE,
        )
        db.add(order)
        db.flush()
        for seq, code in enumerate(codes, start=1):
            db.add(
                OrderLine(
                    id=str(uuid.uuid4()), order_id=order.id, product_id=products[code], warehouse_id=wh.id,
                    quantity=seq, line_sequence=seq, company_id=DEFAULT_COMPANY_ID,
                )
            )
    db.commit()
    return cust.id, products


class _RouteReplay(_Replay):
    """The round 5 replay (real `engine.run_turn`, one contact, session carried), with
    the orders list answered by the REAL `GET /order-management/orders` over seeded rows
    and rendered by the REAL presenter, so the lines the owner reads are the lines the
    route returns for the args the chatbot sent."""

    def __init__(self, session_factory, monkeypatch):
        super().__init__(session_factory, monkeypatch)
        from app.services.chatbot import engine as engine_mod
        from tests.chatbot.test_outstanding_lane import _present_response

        self.customer_id, _products = _seed_documents(session_factory, self.brand_id)
        customer_id = self.customer_id

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            tokens = list(body.get("tokens") or [])
            hits = [t for t in tokens if t.casefold() == "cheng huat sentul"]
            return {
                "tokens": tokens,
                "resolutions": [
                    {"token": t, "resolved": True, "matches": [
                        {"uuid": customer_id, "entity_type": "customer", "canonical_code": t, "match_tier": "exact"}
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
            scope = frozenset({DEFAULT_COMPANY_ID})
            set_company_scope(route_db, scope)
            return scope

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
        app.dependency_overrides[apply_company_scope] = _override_scope
        client = TestClient(app)
        present = _present_response()
        prior = self._mcp_call_of(engine_mod)

        def mcp_call(name: str, args: dict) -> str:
            if name != ORDERS_LIST:
                return prior(name, args)
            self.captured.append((name, dict(args)))
            params = {k: v for k, v in args.items() if v is not None and k not in ("contact_id", "space_id")}
            resp = client.get(ORDERS_ROUTE, params=params)
            assert resp.status_code == 200, resp.text
            return present(name, resp.text)

        from app.services.chatbot.lanes.business.services import FetchServices

        monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call))

    @staticmethod
    def _mcp_call_of(engine_mod):
        return engine_mod.business_services.fetch_services(None).mcp_call


@pytest.fixture
def route_replay(session_factory, monkeypatch):
    chat = _RouteReplay(session_factory, monkeypatch)
    try:
        yield chat
    finally:
        app.dependency_overrides.clear()


def _listed_codes(reply: str) -> set[str]:
    return {code for code in SORENTO_CODES + MOCHA_CODES if code in reply}


def test_owner_message_filters_the_do_list_by_the_misspelt_brand(route_replay) -> None:
    """R1 + R2: the owner's exact message under the published words."""
    chat = route_replay
    reply, calls, _projected = chat.turn(OWNER_MESSAGE, _v40_do_list("sorneto", "Sorento"))
    where = f"calls={calls} reply={reply!r}"
    names = [c[0] for c in calls]
    assert REPORT not in names, where
    order_calls = [a for n, a in calls if n == ORDERS_LIST]
    assert order_calls, where
    assert order_calls[-1].get("brand_ids") == [chat.brand_id], where
    assert order_calls[-1].get("customer_ids") == [chat.customer_id], where
    assert "Brand: Sorento" in reply, where
    listed = _listed_codes(reply)
    assert listed, f"the Sorento documents must be listed: {where}"
    assert listed <= set(SORENTO_CODES), f"only Sorento lines may be listed, got {sorted(listed)}: {where}"
    for number in ("M2609-0400", "M2608-1374", "REP2608-0195"):
        assert number not in reply, f"{number} carries no Sorento line: {where}"
    for number in ("M2608-1180", "DO-R6-SOLO", "DO-R6-MIXED"):
        assert number in reply, f"{number} carries a Sorento line: {where}"


def test_the_brand_rides_the_next_turn_after_the_misspelt_ask(route_replay) -> None:
    """The resolved brand is carried like any other brand (round 3 B1-r2)."""
    from tests.chatbot.test_samantha_26sep_r3_brand_carry import _august_qf

    chat = route_replay
    chat.turn(OWNER_MESSAGE, _v40_do_list("sorneto", "Sorento"))
    reply, calls, _ = chat.turn("what about August", _august_qf())
    order_calls = [a for n, a in calls if n == ORDERS_LIST]
    assert order_calls and order_calls[-1].get("brand_ids") == [chat.brand_id], (calls, reply)
    assert "Brand: Sorento" in reply, reply


def test_an_unknown_brand_word_is_said_back_in_one_line(route_replay) -> None:
    """A brand word matching no live brand, raw or canonical: no brand filter, no Brand
    line, and the word is said back the way the other lists say it."""
    chat = route_replay
    reply, calls, _ = chat.turn("DO brand zorbix for cheng huat sentul", _v40_do_list("zorbix", None))
    order_calls = [a for n, a in calls if n == ORDERS_LIST]
    assert order_calls and not order_calls[-1].get("brand_ids"), (calls, reply)
    assert "Brand:" not in reply, reply
    notes = [line for line in reply.splitlines() if "zorbix" in line.lower()]
    assert len(notes) == 1, f"the unknown brand word is said back in exactly one line: {reply!r}"


# --------------------------------------------------------------------------- #
# The seams.
# --------------------------------------------------------------------------- #


def test_resolver_reads_the_listed_brand_off_canonical_code(session_factory) -> None:
    from app.services.chatbot.lanes.business import _resolve_outstanding_brand_ids
    from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _seed_brand

    brand_id = _seed_brand(session_factory, name="Sorento", code="SRT")
    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    semantic: dict[str, Any] = {}
    _resolve_outstanding_brand_ids(_v40_do_list("sorneto", "Sorento"), semantic, db=db)
    assert semantic.get("outstanding_brand_ids") == [brand_id]

    unknown: dict[str, Any] = {}
    _resolve_outstanding_brand_ids(_v40_do_list("zorbix", "Zorbix"), unknown, db=db)
    assert not unknown.get("outstanding_brand_ids")


def test_order_brand_filter_names_the_live_row_for_the_misspelt_word(session_factory) -> None:
    from app.services.chatbot import turn_runtime
    from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _seed_brand

    brand_id = _seed_brand(session_factory, name="Sorento", code="SRT")
    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    ids, names = turn_runtime.order_brand_filter(db, _v40_do_list("sorneto", "Sorento"), None)
    assert (ids, names) == ([brand_id], ["Sorento"])


def test_orders_route_lists_only_the_brands_lines(session_factory) -> None:
    """The route: a brand filter keeps a mixed document but drops its other-brand lines."""
    from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _seed_brand

    brand_id = _seed_brand(session_factory, name="Sorento", code="SRT")
    customer_id, _ = _seed_documents(session_factory, brand_id)
    route_db = session_factory()
    principal = _seed_superadmin(route_db)

    def _override_db():
        yield route_db

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(route_db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        client = TestClient(app)
        body = client.get(ORDERS_ROUTE, params={"brand_ids": [brand_id], "customer_ids": [customer_id]}).json()
        unfiltered = client.get(ORDERS_ROUTE, params={"customer_ids": [customer_id]}).json()
    finally:
        app.dependency_overrides.clear()
    by_number = {row["order_number"]: row for row in body["data"]}
    assert set(by_number) == {"M2608-1180", "DO-R6-SOLO", "DO-R6-MIXED"}, sorted(by_number)
    mixed_codes = [line["product"]["product_code"] for line in by_number["DO-R6-MIXED"]["lines"]]
    assert mixed_codes == ["SRT-R6-MIXED"], mixed_codes
    # No brand asked: the document keeps every line, byte for byte as before.
    full = {row["order_number"]: row for row in unfiltered["data"]}
    assert len(full) == 6
    assert sorted(line["product"]["product_code"] for line in full["DO-R6-MIXED"]["lines"]) == [
        "MCH-R6-MIXED", "SRT-R6-MIXED",
    ]
    assert json.dumps(body)  # serialisable
