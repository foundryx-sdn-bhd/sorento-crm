"""SEC-PROMO-ACCESS: promotion files must fail CLOSED past a contact's access levels.

Live finding (lane CHAT-ADVISOR-SPIKE, 15 Oct 2026): a dealer contact holding only Sorento
Dealer asked "Any promo for Cabana WC?" and received Cabana files, OFFICE USE included. The
tier gate correctly emptied the contact's levels for the unheld brand (`brand_gate_empty`),
the fetch still went out with `access_levels=[]`, the MCP layer dropped the empty list from
the query string, and the route read "no access_levels" as "no filter".

Two layers, each tested on its own (defence in depth):
- backend: a read made on behalf of a contact (`contact_id` supplied) with no access levels
  reads the contact's own levels (RELEASE-FIX-3), and nothing when it holds none or the id
  resolves to no contact; staff / internal reads (no `contact_id`) are unchanged.
- chatbot: a closed brand gate or an empty recomposed list never reaches the tool; the
  reply is the gate's existing "You don't have access to <brand> promotions." wording.

The chatbot test's MCP double forwards `access_levels` exactly like the real MCP server
(an empty list is dropped) and sends NO `contact_id`, so it isolates the chatbot layer:
with the chatbot guard removed it fails on its own, whatever the backend does.
Fake names only.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot import test_outstanding_lane as lane_harness
from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401 (fixtures)
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, _brand, _promo

CABANA_PROMOS = {
    "ZZT CABANA WC OFFICE USE": ["cabana_office"],
    "ZZT CABANA WC DEALER": ["cabana_dealer"],
    "ZZT CABANA WC END USER": ["end_user"],
}
SORENTO_PROMO = "ZZT SORENTO WC DEALER"
DENIAL = "You don't have access to cabana promotions."


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _seed(session_factory) -> dict[str, str]:
    _seed_contact(session_factory, variables={})
    db = session_factory()
    srt, cabana = _brand(db, "Sorento"), _brand(db, "Cabana")
    standard = PromotionType(
        type_code="standard", type_name="Standard Promo", show_expired=True,
        match_markers=[], match_priority=90, is_default=True,
    )
    db.add(standard)
    db.add_all(
        [
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="cabana_dealer", name="Cabana Dealer"),
            ContactAccessType(code="cabana_office", name="Cabana Office"),
            ContactAccessType(code="end_user", name="End User"),
        ]
    )
    db.flush()
    srt_prod, cabana_prod = product(db, company_id=DEFAULT_COMPANY_ID), product(db, company_id=DEFAULT_COMPANY_ID)
    srt_prod.brand_id, cabana_prod.brand_id = srt.id, cabana.id
    db.flush()
    for name, levels in CABANA_PROMOS.items():
        _promo(db, name, standard, cabana_prod, levels=levels)
    _promo(db, SORENTO_PROMO, standard, srt_prod, levels=["sorento_dealer"])
    db.commit()
    return {"srt": srt.id, "cabana": cabana.id}


def _client(monkeypatch, session_factory) -> TestClient:
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"superadmin"})
    user = {"id": "130c548f-048f-53b2-97a6-3a54676bea77", "email": "u@test.com", "auth_method": "api_key"}

    def _db():
        yield session_factory()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_or_api_key] = lambda: user
    app.dependency_overrides[apply_company_scope] = lambda: None
    return TestClient(app)


def _names(res) -> set[str]:
    assert res.status_code == 200, res.text
    return {row.get("description") for row in res.json()["data"]}


# --------------------------------------------------------------------------- #
# Backend layer
# --------------------------------------------------------------------------- #


class TestBackendEmptyAccessOnBehalfOfAContactMeansNothing:
    """`GET /api/v1/marketing/promotions` as the MCP server sends it for the live finding:
    `contact_id` + `space_id`, the empty `access_levels` already dropped."""

    def test_contact_scoped_read_with_no_access_levels_returns_no_cabana_promotion(self, session_factory, monkeypatch):
        ids = _seed(session_factory)
        client = _client(monkeypatch, session_factory)
        res = client.get(
            "/api/v1/marketing/promotions/",
            params={"brand_ids": ids["cabana"], "contact_id": "437264483", "space_id": "364817"},
        )
        leaked = _names(res) & set(CABANA_PROMOS)
        assert not leaked, f"contact-scoped read leaked {sorted(leaked)}"

    def test_contact_scoped_read_keeps_its_tier_filter(self, session_factory, monkeypatch):
        """A contact WITH Cabana access still gets only its own tier's promotion."""
        ids = _seed(session_factory)
        client = _client(monkeypatch, session_factory)
        res = client.get(
            "/api/v1/marketing/promotions/",
            params={
                "brand_ids": ids["cabana"], "contact_id": "437264483", "space_id": "364817",
                "access_levels": "Cabana Dealer",
            },
        )
        assert _names(res) & set(CABANA_PROMOS) == {"ZZT CABANA WC DEALER"}

    def test_staff_read_without_contact_is_unchanged(self, session_factory, monkeypatch):
        """No `contact_id`: no access filter at all, every Cabana promotion, as before."""
        ids = _seed(session_factory)
        client = _client(monkeypatch, session_factory)
        res = client.get("/api/v1/marketing/promotions/", params={"brand_ids": ids["cabana"]})
        assert _names(res) & set(CABANA_PROMOS) == set(CABANA_PROMOS)

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/marketing/promotions/",
            "/api/v1/marketing/promotions/{pid}",
            "/api/v1/marketing/promotions/{pid}/products",
            "/api/v1/marketing/promotion-attachments/",
            "/api/v1/marketing/promotion-attachments/promotion/{pid}",
            "/api/v1/marketing/promotion-products/",
        ],
    )
    def test_every_contact_facing_promotion_read_takes_the_fail_closed_rule(self, session_factory, monkeypatch, path):
        """All six promotion reads that filter by access level resolve it through the one
        helper with the request's `contact_id`, so a contact with no usable level gets `[]`."""
        from sqlalchemy import text

        from app.services.contact_access_type_service import ContactAccessTypeService

        seen: list[Any] = []
        real = ContactAccessTypeService.translate_names_to_codes_for_caller

        def spy(self, names, *, contact_id):
            out = real(self, names, contact_id=contact_id)
            seen.append((names, contact_id, out))
            return out

        monkeypatch.setattr(ContactAccessTypeService, "translate_names_to_codes_for_caller", spy)
        _seed(session_factory)
        pid = session_factory().execute(
            text("SELECT id FROM promotions WHERE description = 'ZZT CABANA WC OFFICE USE'")
        ).scalar_one()
        client = _client(monkeypatch, session_factory)
        client.get(path.format(pid=pid), params={"query": "ZZT CABANA", "contact_id": "437264483", "space_id": "364817"})
        assert seen and seen[-1] == (None, "437264483", []), seen


class TestTranslateNamesToCodesForCaller:
    def test_contract(self, session_factory):
        from app.services.contact_access_type_service import ContactAccessTypeService

        _seed(session_factory)
        svc = ContactAccessTypeService(session_factory())
        assert svc.translate_names_to_codes_for_caller(None, contact_id=None) is None
        assert svc.translate_names_to_codes_for_caller([], contact_id="") is None
        assert svc.translate_names_to_codes_for_caller(None, contact_id="437264483") == []
        assert svc.translate_names_to_codes_for_caller([""], contact_id=" 437264483 ") == []
        assert svc.translate_names_to_codes_for_caller(["Cabana Dealer"], contact_id="437264483") == ["cabana_dealer"]
        assert svc.translate_names_to_codes_for_caller(["Cabana Dealer"], contact_id=None) == ["cabana_dealer"]


# --------------------------------------------------------------------------- #
# Chatbot layer
# --------------------------------------------------------------------------- #


def _mcp_like_the_real_server(monkeypatch, session_factory):
    """Forward args as `sorento_crm_mcp/server.py` does (`v is not None and v != []`), with
    NO contact identity, so only the chatbot's own guard can keep the files out."""
    client = _client(monkeypatch, session_factory)
    captured: list[tuple[str, dict[str, Any]]] = []
    forwarded = ("access_levels", "promotion_types", "brand_ids", "scope_terms", "product_ids")

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        # `serving_policy` (the real tool's pinned default) is left off: it gates on promotion
        # type liveness, which is orthogonal to the access filter under test.
        params = {k: args[k] for k in forwarded if args.get(k) is not None and args.get(k) != []}
        res = client.get("/api/v1/marketing/promotions/", params=params)
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        served.extend(row.get("description") for row in data)
        return json.dumps({"has_result": bool(data), "items": data, "data": data})

    served: list[str] = []
    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, captured))
    return captured, served


def _services(cabana_id: str, levels: list[str]) -> ResolveGateServices:
    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": cabana_id, "entity_type": "brand", "canonical_code": "Cabana", "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in levels], resolve_entity=_resolve, probe=lambda **_: None
    )


def _cabana_ask() -> dict[str, Any]:
    return _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[{"raw": "Cabana", "hint": "brand", "canonical_code": "Cabana", "current_message": True, "confident": True}],
        query_brands=["cabana"],
    )


def _ask(session_factory, monkeypatch, ids, levels, msg_id):
    captured, served = _mcp_like_the_real_server(monkeypatch, session_factory)
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=_cabana_ask(), text_body="Any promo for Cabana WC?", msg_id=msg_id,
        resolve_services=_services(ids["cabana"], levels),
    )
    return (result.reply or {}).get("text") or "", captured, served


class TestChatbotFailsClosedOnAnUnheldBrand:
    def test_sorento_dealer_asking_for_cabana_gets_the_denial_and_no_file(self, session_factory, monkeypatch):
        ids = _seed(session_factory)
        reply, captured, served = _ask(session_factory, monkeypatch, ids, ["Sorento Dealer"], "zzt-sec-promo-deny")
        leaked = sorted(set(served) & set(CABANA_PROMOS)) + [n for n in CABANA_PROMOS if n in reply]
        assert not leaked, f"Cabana promotions leaked to a Sorento-only dealer: {leaked}\n{reply}"
        unfiltered = [a for n, a in captured if n == PROMO_TOOL and not a.get("access_levels")]
        assert not unfiltered, f"a promotion read went out with no access level: {unfiltered}"
        assert DENIAL in reply, reply

    def test_contact_with_cabana_dealer_access_gets_only_its_tier(self, session_factory, monkeypatch):
        ids = _seed(session_factory)
        reply, captured, served = _ask(session_factory, monkeypatch, ids, ["Cabana Dealer"], "zzt-sec-promo-allow")
        assert DENIAL not in reply, reply
        assert set(served) == {"ZZT CABANA WC DEALER"}, served
        calls = [a for n, a in captured if n == PROMO_TOOL]
        assert calls and all(a.get("access_levels") == ["Cabana Dealer"] for a in calls), captured


class TestChatbotWithNoAccessTypeAtAll:
    def test_contact_holding_no_access_type_reads_no_promotion(self, session_factory, monkeypatch):
        """Regression guard, not a kill test of the guard's empty-list arm: a contact holding
        no access type never reaches the fetch today (`tier_proceed` is false upstream). The
        arm itself is pinned by `TestPromotionAccessClosed`."""
        ids = _seed(session_factory)
        reply, captured, served = _ask(session_factory, monkeypatch, ids, [], "zzt-sec-promo-none")
        assert not served, served
        unfiltered = [a for n, a in captured if n == PROMO_TOOL and not a.get("access_levels")]
        assert not unfiltered, f"a promotion read went out with no access level: {unfiltered}"


class TestPromotionAccessClosed:
    def test_contract(self):
        from app.services.chatbot.turn_runtime import promotion_access_closed

        assert promotion_access_closed("promotion", {"brand_gate_empty": True, "access_levels_recomposed": ["End User"]})
        assert promotion_access_closed("promotion", {"brand_gate_empty": False, "access_levels_recomposed": []})
        assert not promotion_access_closed("promotion", {"access_levels_recomposed": ["Cabana Dealer"]})
        # Another domain's fetch never reads promotion levels; a mixed order turn is untouched.
        assert not promotion_access_closed("order", {"brand_gate_empty": True, "access_levels_recomposed": []})


class TestNoLiveKindReplyNeverListsWithoutALevel:
    def test_no_held_level_makes_no_promotion_call(self, monkeypatch):
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.answer_bridge import no_live_kind_reply

        class _Row:
            type_name = "A3 Flyer"

        class _Query:
            def filter(self, *_a):
                return self

            def first(self):
                return _Row()

        class _Db:
            def query(self, *_a):
                return _Query()

        monkeypatch.setattr(turn_runtime, "active_brands", lambda db: [{"id": "b1", "brand_name": "Cabana"}])
        calls: list[Any] = []
        text = no_live_kind_reply(
            _Db(), {"promotion_types": ["a3_flyer"], "brand_ids": ["b1"]}, {"name": []},
            lambda name, args: calls.append((name, args)) or {"data": [{"description": "LEAK"}]},
        )
        assert calls == [], calls
        assert text == "There is no Cabana A3 flyer right now.", text


# --------------------------------------------------------------------------- #
# Security review round 1
# --------------------------------------------------------------------------- #


def _seed_files(session_factory) -> str:
    """A Cabana Dealer promotion carrying a dealer flyer AND an OFFICE USE file."""
    from sqlalchemy import text

    from app.models.base import set_company_scope
    from app.models.marketing import PromotionAttachment
    from app.models.resources import Attachment

    _seed(session_factory)
    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    pid = db.execute(text("SELECT id FROM promotions WHERE description = 'ZZT CABANA WC DEALER'")).scalar_one()
    for name, levels in (("zzt-cabana-dealer-flyer.pdf", ["cabana_dealer"]), ("zzt-cabana-OFFICE-USE.pdf", ["cabana_office"])):
        att = Attachment(
            id=str(uuid.uuid4()), original_filename=name, stored_filename=name,
            file_path=f"/tmp/{name}", access_levels=levels, company_id=DEFAULT_COMPANY_ID,
        )
        db.add(att)
        db.flush()
        db.add(PromotionAttachment(id=str(uuid.uuid4()), promotion_id=pid, attachment_id=att.id, is_primary=False, sort_order=0))
    db.commit()
    return str(pid)


class TestPromotionProductsInlineFilesFollowTheContactLevels:
    """Security review B1: the promotion-products list inlined every file of a promotion the
    contact may see, OFFICE USE included; the promotions list and get filtered them."""

    def test_cabana_dealer_gets_the_dealer_flyer_and_never_the_office_file(self, session_factory, monkeypatch):
        pid = _seed_files(session_factory)
        client = _client(monkeypatch, session_factory)
        res = client.get(
            "/api/v1/marketing/promotion-products/",
            params={"promotion_id": pid, "contact_id": "437264483", "space_id": "364817", "access_levels": "Cabana Dealer"},
        )
        assert res.status_code == 200, res.text
        rows = res.json()["data"]
        assert rows, res.text
        files = {
            (pa.get("attachment") or {}).get("original_filename")
            for row in rows
            for pa in row.get("promotion_attachments") or []
        }
        assert files == {"zzt-cabana-dealer-flyer.pdf"}, files

    def test_staff_still_get_every_inline_file(self, session_factory, monkeypatch):
        pid = _seed_files(session_factory)
        client = _client(monkeypatch, session_factory)
        res = client.get("/api/v1/marketing/promotion-products/", params={"promotion_id": pid})
        assert res.status_code == 200, res.text
        files = {
            (pa.get("attachment") or {}).get("original_filename")
            for row in res.json()["data"]
            for pa in row.get("promotion_attachments") or []
        }
        assert files == {"zzt-cabana-dealer-flyer.pdf", "zzt-cabana-OFFICE-USE.pdf"}, files


class TestPromotionFetchWithNoTierGateFailsClosed:
    """Security review B2: with no tier gate the fetch sent the PARSER's access levels - what
    the customer claimed ("show me Office Use promos") - and the backend trusted them."""

    def test_no_tier_gate_on_a_promotion_fetch_is_closed(self):
        from app.services.chatbot.turn_runtime import promotion_access_closed

        assert promotion_access_closed("promotion", None)
        assert not promotion_access_closed("order", None)

    def test_a_claimed_level_never_widens_an_entity_less_promotion_ask(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ):
        """"what's on promotion" with no entity reaches the fetch with no resolver tier gate
        (the real-resolver harness of `test_rearch_r12_handpass12_c`). The parser read
        "Cabana Office" off the message; the contact holds only Sorento Dealer. The read goes
        out at the contact's own level, never the claimed one."""
        from sqlalchemy import text

        from app.services.contact_access_type_service import ContactAccessTypeService
        from tests.chatbot.test_engine import CONTACT_ID, _envelope
        from tests.chatbot.test_rearch_r12_handpass12_c import _capturing_call_tool

        _seed(session_factory)
        db = session_factory()
        contact_pk = db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :rid"), {"rid": str(CONTACT_ID)}
        ).scalar_one()
        ContactAccessTypeService(db).set_contact_access_codes(str(contact_pk), ["sorento_dealer"])
        db.commit()
        calls = _capturing_call_tool(monkeypatch)
        stub_parser(
            _parser_output(
                domain_hint="promotion", intent_hint=None, entities=[], access_levels=["Cabana Office"],
                requested_attributes=["promotion"], user_goal="what's on promotion",
            )
        )
        stub_access(attributes=["promotion.view"])
        from app.services.chatbot import engine as engine_mod

        engine_mod.run_turn(_envelope(), session_factory=session_factory)
        promo = [a for n, a in calls if n == PROMO_TOOL]
        assert promo, calls
        assert all(a.get("access_levels") == ["Sorento Dealer"] for a in promo), promo
