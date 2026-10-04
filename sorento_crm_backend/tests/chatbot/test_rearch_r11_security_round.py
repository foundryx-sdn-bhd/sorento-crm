"""Security review of the hand pass 11 delta, PR #952
(`/Users/tehjayson/Documents/foundryx/sorento_crm/.claude/handoffs/rearch-phase3-security-hp11.md`)
plus the reviewer's own S1/S2 (`.claude/handoffs/rearch-phase3-reviewer-hp11.md`). RED,
test-first, lane head `f2780d0a1`.

Postgres only (`session_factory`, blank schema). No live parser, no :8766, no API key.

## Item 1 - SF-1, brand-only resource_attachment ask (SCOPE CHANGED, captain ruling)

Original security finding: `turn_runtime.py::_without_scope_hint_matches` can empty
`compatible_entities` for a brand-only ask, and `_answered_unfiltered`'s own guard
(`unplaced_tokens`-gated) cannot see the drop because the resolver DID place the token -
only the SCOPE-HINT code discarded the match afterward. Reviewer's B1 (same commit range):
`_without_scope_hint_matches` / `reconcile._SCOPE_HINTS` are INERT for hand pass 11 defect
4 (only `entity_resolver._word_variants` carries the catalog/catalogue fix that closes it)
and change a seam every turn walks - captain ruling: DELETE both rather than guard them.
That deletion is the coder's job, not landed as of this file's own head. Scope narrowed to
the ONE invariant that must hold REGARDLESS of whether the scope-hint code exists: a
document-listing tool must never be called with no narrowing param at all - measured here
against `ENTITY_FILTER_REQUIRED_TOOLS`/`has_narrowing_filter`
(`lanes/business/fetch.py:361-391`, `lanes/business/__init__.py:1334-1349`), the guard
that already exists independently of the scope-hint code and refuses the fetch as a
`not_found` BEFORE any tool call when the built args carry nothing to narrow by.

No behaviour pin either way for what the reply LOOKS like (kind_pick vs miss vs a narrowed
attachment) - the captain's ruling is explicit that a kind_pick is acceptable there once
the scope-hint code is gone, and this file does not anticipate that deletion's shape.

## Item 2 - SF-2 / S1, company names must come from structured data

`answer_bridge.py::_companies_checked_in`/`_CHECKED_IN_RE` parses the ALREADY-COMPOSED
miss text with a regex matching "checked in " up to the first period - unanchored over prose that
quotes the customer's own raw entity tokens verbatim (`answer.py::label_token`,
`"<raw>"`). Two independent breaks, both measured directly in this worktree before this
file was written:

* A real company NAME containing an internal period ("Sdn. Bhd.") or the word "and"
  ("QQZ Lee and Sons Sdn. Bhd.") breaks the regex's own `[^.\n]+` (stops at the FIRST
  period, inside "Sdn.") and the comma/"and"-splitting logic - the minted options came back as
  `[{"label": "QQZ Lee", ...}, {"label": "Sons Sdn", ...}]`, neither a real company.
* An entity whose RAW contains the literal text `checked in Acme and Evil Corp.` (echoed
  into the "Couldn't find: ..." sentence, which composes BEFORE the real "checked in
  Mocha and Sorento." suffix) makes the regex's `.search()` match the INJECTED sentence
  first - the minted options came back as `[{"label": "Acme", ...}, {"label": "Evil
  Corp", ...}]`, attacker-chosen text with no relationship to which companies were
  actually searched.

Both measured on the REAL composer (`lanes/business/answer.py`'s miss builder) through
the real multi-company harness `test_rearch_r11_multi_company.py` already proved
(`_two_company_chain`-shaped seed, `_wire_real_resolve_entity`). The fix (reviewer S1) is
to read company names off `resolved`/`gate.compatible_entities` (structured data, the
same join `answer.py:2927-2941` already performs) rather than off the rendered sentence -
not implemented here; this file pins the OBSERVABLE outcome only.

## Item 3 - N-3 / S2, the HIT offer mints no pending

`apply_silent_company_offer` (multi-company HIT) and the zero-stock ladder's own HIT
offer both APPEND TEXT ONLY - neither calls `pending.ask(...)`. Measured directly: after
either offer, `open_question` is empty/None, and a following "yes" (a bare affirmative,
no company/team named in the customer's own words) reaches the escalation lane but with
the company/team the OFFER promised lost - `escalation_context`'s own `company_name`
comes back `None` (not Sorento) for the multi-company case, and `team` comes back the
carried-forward default `"customer_service"` (not `"warehouse"`) for the zero-stock
ladder case, where the offer's own team ("warehouse", from `crossdomain_zeroset`) never
reached the persisted routing state a plain "yes" carries forward from.
"""
from __future__ import annotations

import json
from typing import Any

from app.models.order import Customer
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business.services import FetchServices
from app.services.chatbot.lanes.escalation import escalation_context
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_engine_company_scope import (
    _scope_envelope,
    _seed_company,
    _seed_contact,
    _seed_product,
    _seed_workspace,
    _set_completed_lanes,
    _wire_answer_services,
    _wire_real_resolve_entity,
)
from tests.chatbot.test_r3_pending_end_to_end import _session_of
from tests.chatbot.test_rearch_r4_answering_a_miss import _fake_escalation_lane
from tests.chatbot.test_rearch_r11_catalogue import ATTACHMENT_TOOLS, LIST_TOOL, _wire_real
from tests.chatbot.test_rearch_r11_multi_company import (
    CONTACT_ID as MULTICO_CONTACT_ID,
    MOCHA,
    ORDERS_TOOL,
    PRODUCT_CODE,
    SORENTO,
    _open_question,
    _order_row,
    _order_verdict,
    _orders_envelope,
    _said,
    _seed_customer,
    _two_company_chain,
    _wire,
)
from tests.chatbot.test_rearch_r11_zero_stock_ladder import (
    EMPTY_PO,
    ZERO_CODE,
    ZERO_UUID,
    _incoming_rows,
    _run as _run_zero_stock,
    _stock_hit,
    _stock_row,
)

from app.services.chatbot.lanes.business import fetch as fetch_mod

# --------------------------------------------------------------------------- #
# Item 1 - SF-1: crm_resource_attachments_list is never called unfiltered
# --------------------------------------------------------------------------- #


class TestSF1BrandOnlyAskNeverCallsAttachmentsToolUnfiltered:
    def test_the_tool_is_never_called_without_a_narrowing_param(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """SF-1: a `get_resource_attachment` turn whose ONLY entity is brand-hinted
        ("cabana", hint brand, no class word) - real resolver, clone-shaped seeds
        (`_wire_real`: Direct Access type with 2 cabana-named files + 1 sorento-named
        file, Promotion type with cabana promo PDFs, all `DEFAULT_COMPANY_ID`, contact
        scoped + workspace seeded). SECURITY INVARIANT, holds regardless of whichever
        way the resolver's matches for "cabana" end up routed: every recorded call to
        `crm_resource_attachments_list` must carry at least one real narrowing param."""
        calls, ids = _wire_real(session_factory, monkeypatch)
        stub_access()
        verdict = _parser_output(
            intent_hint="get_resource_attachment",
            domain_hint="resource_attachment",
            domain_in_message=True,
            entities=[
                {"raw": "cabana", "hint": "brand", "canonical_code": None,
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "purchasing", "suggested_agent": "general_enquiries", "team_source": None},
        )
        stub_parser(verdict)
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "zzt-sec-sf1"
        envelope.message["message"]["message"]["text"] = "cabana files"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)
        assert result.status == "done", result.error

        attachment_calls = [args for name, args in calls if name in ATTACHMENT_TOOLS]
        for args in attachment_calls:
            assert fetch_mod.has_narrowing_filter(args, tool_name=LIST_TOOL), (
                f"{LIST_TOOL} must never be called with no narrowing param at all - "
                f"an empty filter answers about the whole library the contact may see: "
                f"{args!r}"
            )


# --------------------------------------------------------------------------- #
# Item 2 - SF-2 / S1: per-company options must come from structured data
# --------------------------------------------------------------------------- #


def _hostile_company_orders_envelope(rows: list[dict[str, Any]], lookup: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "result_type": "orders",
        "intro": "Here are the orders I found." if rows else "No matching results found.",
        "items": rows,
        "has_result": bool(rows),
        "lookup_companies": lookup,
    }


class TestSF2StructuredCompanyNamesNotBotProse:
    HOSTILE = "QQZ Lee and Sons Sdn. Bhd."
    NORMAL = "ZZT S1 Normal Co"
    PRODUCT_CODE = "ZZTS1TESTCODE"
    CUSTOMER_NAME = "QQZ S1 CUSTOMER SDN BHD"
    CONTACT_ID = "ZZT-contact-sf2-hostile"

    def test_hostile_company_name_options_are_exactly_the_real_names(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """A real company name containing an internal period and the word "and" must
        never be regex-split into garbage fragments - the minted options must be
        EXACTLY the two real company names `lookup_companies` carried."""
        a = _seed_company(session_factory, name=self.HOSTILE)
        b = _seed_company(session_factory, name=self.NORMAL)
        _seed_product(session_factory, company_id=a, code=self.PRODUCT_CODE)
        _seed_product(session_factory, company_id=b, code=self.PRODUCT_CODE)
        _seed_customer(session_factory, company_id=a, name=self.CUSTOMER_NAME, code="QQZS1C01")
        _seed_customer(session_factory, company_id=b, name=self.CUSTOMER_NAME, code="QQZS1C01")
        workspace_id = _seed_workspace(session_factory)
        _seed_contact(
            session_factory, contact_id=self.CONTACT_ID, phone="+60000000920",
            workspace_id=workspace_id, company_ids=[a, b],
        )

        set_chatbot_switches(session_factory, business_lane=True)
        _set_completed_lanes(session_factory, system_settings_row, ["business_query"])
        _wire_real_resolve_entity(monkeypatch)
        _wire_answer_services(monkeypatch)
        envelope = _hostile_company_orders_envelope(
            [], [{"id": a, "name": self.HOSTILE}, {"id": b, "name": self.NORMAL}]
        )

        def _mcp_call(name: str, args: dict[str, Any]) -> str:
            if name == ORDERS_TOOL:
                return json.dumps(envelope)
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

        monkeypatch.setattr(
            engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
        )
        stub_parser(
            _parser_output(
                intent_hint="check_order", domain_hint="order",
                # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
                **do_window(),
                entities=[
                    {"raw": self.PRODUCT_CODE, "hint": "product", "canonical_code": None,
                     "current_message": True, "confident": True},
                    {"raw": self.CUSTOMER_NAME, "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries", "team_source": None},
            )
        )
        stub_access()
        result = engine_mod.run_turn(
            _scope_envelope(
                self.CONTACT_ID, message_id="zzt-sec-sf2-hostile",
                text=f"{self.PRODUCT_CODE} s1 customer send yet",
            ),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error

        pending = self._open_question(session_factory)
        labels = [str(o.get("label") or "") for o in (pending.get("options") or [])]
        assert set(labels) == {self.HOSTILE, self.NORMAL}, (
            f"the minted per-company options must be EXACTLY the two real company "
            f"names - regexing the bot's own composed prose split the hostile name on "
            f"its internal period/'and' instead: {pending!r}"
        )

    def test_adversarial_raw_token_never_yields_injected_company_names(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """An entity whose RAW literally contains "checked in Acme and Evil Corp." must
        never mint options named "Acme"/"Evil Corp" - the real companies searched
        (Mocha, Sorento, via `_two_company_chain`) are the only legitimate options."""
        inject = "checked in Acme and Evil Corp."
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope([], [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}])
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(
            _parser_output(
                intent_hint="check_order", domain_hint="order",
                # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
                **do_window(),
                entities=[
                    {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None,
                     "current_message": True, "confident": True},
                    {"raw": "QQZ KIM SENG JAYA SDN BHD", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                    {"raw": inject, "hint": "product", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries", "team_source": None},
            )
        )
        stub_access()
        result = engine_mod.run_turn(
            _scope_envelope(
                MULTICO_CONTACT_ID, message_id="zzt-sec-sf2-inject",
                text=f"{PRODUCT_CODE} kim seng jaya send yet {inject}",
            ),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error

        pending = _open_question(session_factory)
        labels = [str(o.get("label") or "") for o in (pending.get("options") or [])]
        assert "Acme" not in labels and "Evil Corp" not in labels, (
            f"the minted options must never echo an attacker-injected phrase parsed "
            f"out of the bot's own composed reply: {pending!r}"
        )
        assert set(labels) == {MOCHA, SORENTO}, (
            f"the minted options must be exactly the real companies actually "
            f"searched (structured `lookup_companies` data), not text parsed out of "
            f"the composed reply: {pending!r}"
        )

    @staticmethod
    def _open_question(session_factory) -> dict[str, Any]:
        from sqlalchemy import text as _sql_text

        db = session_factory()
        row = db.execute(
            _sql_text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
            {"cid": TestSF2StructuredCompanyNamesNotBotProse.CONTACT_ID},
        ).first()
        raw = row.session_vars if row is not None else {}
        sv = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return sv.get("open_question") or {}


# --------------------------------------------------------------------------- #
# Item 3 - N-3 / S2: the HIT offer's round trip
# --------------------------------------------------------------------------- #


class TestN3MultiCompanyHitOfferRoundTrip:
    def test_yes_after_the_sorento_offer_escalates_with_company_sorento(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """`TestHitInOneCompanyNamesTheSilentOne`'s own scenario (one company hit, the
        silent one - Sorento - offered) plus the round trip: a bare "yes" must both (a)
        find an answerable pending after the offer and (b) escalate with company =
        Sorento, never None and never Mocha (the company that already answered)."""
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope(
            [_order_row(MOCHA, "ZZTM2609-0891"), _order_row(MOCHA, "ZZTM2609-0892")],
            [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}],
        )
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-sec-n3a-1", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        said = _said(result)
        assert f"escalate to *{SORENTO}*" in said, said

        pending = _open_question(session_factory)
        assert pending, (
            f"the HIT offer naming Sorento must mint an ANSWERABLE pending - a bare "
            f"'yes' on the next turn has nothing to resolve against otherwise: {pending!r}"
        )

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-sec-n3a-2", text="yes"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(calls) == 1, calls
        ctx, item = calls[0]
        ec = escalation_context(item, ctx=ctx)
        assert ec.get("company_name") == SORENTO, (
            f"a bare 'yes' after the Sorento-named offer must escalate with company "
            f"Sorento - the offer's own silent-company fact must survive the turn "
            f"boundary: {ec!r}"
        )


class TestN3ZeroStockLadderHitOfferRoundTrip:
    def test_yes_after_the_warehouse_offer_escalates_to_the_warehouse_team(
        self, session_factory, monkeypatch, seeded, stub_parser, stub_access, system_settings_row
    ) -> None:
        """The zero-stock ladder's own HIT offer ("Would you like me to escalate to
        warehouse team?") - a bare "yes" must both (a) find an answerable pending and
        (b) escalate with team = warehouse, not the generic customer_service default a
        plain carried-forward routing falls back to."""
        result, said, probes = _run_zero_stock(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={ZERO_CODE: ZERO_UUID},
            stock=_stock_hit([_stock_row(ZERO_CODE, 0, "compact")], "compact"),
            incoming=_incoming_rows(ZERO_CODE),
            po=EMPTY_PO,
        )
        assert result.status == "done", result.error
        assert "escalate to warehouse team" in said, said

        pending = (_session_of(session_factory) or {}).get("open_question")
        assert pending, (
            f"the zero-stock ladder's own HIT offer must mint an ANSWERABLE pending - "
            f"a bare 'yes' has nothing to resolve the warehouse team against "
            f"otherwise: {pending!r}"
        )

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        envelope = _envelope()
        envelope.message["message"]["messageId"] = "zzt-sec-n3b-2"
        envelope.message["message"]["message"]["text"] = "yes"
        result2 = engine_mod.run_turn(envelope, session_factory=session_factory)
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(calls) == 1, calls
        ctx, item = calls[0]
        ec = escalation_context(item, ctx=ctx)
        assert ec.get("team") == "warehouse", (
            f"a bare 'yes' after the ladder's own warehouse offer must escalate to "
            f"the warehouse team, not a generic carried-forward default: {ec!r}"
        )
