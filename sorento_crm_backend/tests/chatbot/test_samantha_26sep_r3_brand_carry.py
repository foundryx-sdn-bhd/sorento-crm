"""#1262 fix lane round 3 (reviewer pass round 2 at b219a730): the live brand is a
CARRIED axis, read from one source.

B1-r2: round 2 fixed the first turn only. The brand was settled onto focus only from a
scope question's stored filters, so a brand typed with an order ask never reached focus
and the next turn carried the customer but lost the brand, in silence:

* probe A: "delivery orders brand Sorento for Cheng Huat Sentul", then "what about
  August" - turn 2 called `crm_order_management_orders_list` with no `brand_ids` and
  the Brand line vanished;
* probe B: "outstanding brand Sorento dealer Cheng Huat Sentul", "3", then "delivered
  in August" - turn 3 called `crm_outstanding_report` with no `brand_ids`.

N7: a brand typed by its code ("srt") printed `Brand: srt`, the typed word, not the
live row's name.

S5: a live brand plus an unlisted brand word in one order ask ("Sorento and XYZ")
filtered by Sorento and never said "XYZ" back.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, stub_access  # noqa: F401
from tests.chatbot.test_samantha_26sep_s9_brand_resolve import (
    _seed_brand,
    _seed_contact_scoped_to_sorento,
)

CHENG_HUAT_UUID = "44444444-4444-4444-4444-444444444444"


def _brand(raw: str) -> dict[str, Any]:
    return {"raw": raw, "hint": "brand", "canonical_code": None, "current_message": True, "confident": True}


def _cheng_huat() -> dict[str, Any]:
    return {
        "raw": "Cheng Huat Sentul",
        "hint": "customer",
        "canonical_code": None,
        "current_message": True,
        "confident": True,
    }


class _Chat:
    """Real `engine.run_turn` turns in one conversation; parser, access, resolver and MCP
    faked. `say(text, qf)` runs one turn and returns the reply text."""

    def __init__(
        self,
        session_factory,
        monkeypatch,
        *,
        attributes: list[str],
        tool_body: dict[str, Any],
        report_unresolved: bool = True,
    ):
        from app.models.user import SystemSetting
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.head import parser as parser_mod
        from app.services.chatbot.lanes.business.services import AnswerServices

        self.session_factory = session_factory
        self.monkeypatch = monkeypatch
        self.engine = engine_mod
        self.parser = parser_mod
        _seed_contact_scoped_to_sorento(session_factory)
        self.brand_id = _seed_brand(session_factory, name="Sorento", code="SRT")
        set_chatbot_switches(session_factory, business_lane=True)
        db = session_factory()
        row = db.query(SystemSetting).first()
        if row is None:
            row = SystemSetting()
            db.add(row)
        row.chatbot_completed_lanes = ["business_query"]
        db.commit()

        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, *, agent_code, contact_id, space_id: {
                "allowed": True,
                "decision": "allow",
                "agent_name": "General",
                "attributes": attributes,
                "all_attributes_allowed": None,
            },
        )

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)

        self.asked: list[str] = []

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            tokens = list(body.get("tokens") or [])
            self.asked.extend(tokens)
            resolutions = [
                {
                    "token": token,
                    "resolved": True,
                    "matches": [
                        {
                            "uuid": CHENG_HUAT_UUID,
                            "entity_type": "customer",
                            "canonical_code": "Cheng Huat Sentul",
                            "match_tier": "exact",
                        }
                    ],
                }
                for token in tokens
                if token == "Cheng Huat Sentul"
            ]
            # `report_unresolved=False` is the reviewer's S5 probe shape: the resolver
            # says nothing at all about a word it could not place.
            unresolved = [t for t in tokens if t != "Cheng Huat Sentul"] if report_unresolved else []
            return {"tokens": tokens, "resolutions": resolutions, "unresolved_tokens": unresolved}

        resolve_services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(resolve_entity),
            probe=lambda **_: None,
        )
        self.captured: list[tuple[str, dict[str, Any]]] = []

        def mcp_call(name: str, args: dict) -> str:
            self.captured.append((name, dict(args)))
            return json.dumps(tool_body)

        monkeypatch.setattr(
            engine_mod.business_services, "production_services", lambda db, *, space_id=None: resolve_services
        )
        monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call))
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: AnswerServices(
                mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
            ),
        )
        self._turn = 0

    def say(self, text_body: str, qf: dict[str, Any]) -> str:
        self._turn += 1
        self.monkeypatch.setattr(self.parser, "parse", lambda config, user_block: qf)
        envelope = _envelope()
        envelope.message["message"]["messageId"] = f"ZZT-r3-brand-carry-{self._turn}"
        envelope.message["message"]["message"]["text"] = text_body
        result = self.engine.run_turn(envelope, session_factory=self.session_factory)
        return (result.reply or {}).get("text") or ""

    def calls_since(self, mark: int) -> list[tuple[str, dict[str, Any]]]:
        return self.captured[mark:]


_MISS = {"has_result": False, "items": []}
_HIT_ROW = {
    "order_number": "DO-ZZT-1",
    "customer_name": "Cheng Huat Sentul",
    "actual_delivery_date": "2026-08-01",
}
_HIT = {"has_result": True, "items": [_HIT_ROW], "data": [_HIT_ROW]}


def _delivered_orders_qf(entities: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
    return _parser_output(
        domain_hint="order", intent_hint="check_order", order_status="delivered", entities=entities,
        **{**do_window(), **extra},
    )


def _august_qf() -> dict[str, Any]:
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status="delivered",
        entities=[],
        date_mode="range",
        date_filter_start="2026-08-01",
        date_filter_end="2026-08-31",
    )


class TestB1r2OrderFollowUpKeepsTheBrand:
    """Probe A: the orders list follow-up."""

    def _run(self, session_factory, monkeypatch, tool_body):
        chat = _Chat(session_factory, monkeypatch, attributes=[], tool_body=tool_body)
        first = chat.say(
            "delivery orders brand Sorento for Cheng Huat Sentul",
            _delivered_orders_qf([_brand("Sorento"), _cheng_huat()]),
        )
        mark = len(chat.captured)
        second = chat.say("what about August", _august_qf())
        return chat, first, second, chat.calls_since(mark)

    def _assert_follow_up_call(self, chat, calls) -> None:
        order_calls = [(n, a) for n, a in calls if n in fetch_mod.ORDER_TOOLS]
        assert order_calls, f"the follow-up must call an orders tool: {calls}"
        _, args = order_calls[0]
        assert args.get("customer_ids") == [CHENG_HUAT_UUID], args
        assert args.get("date_filter_start") == "2026-08-01" or "2026-08" in json.dumps(args), args
        assert args.get("brand_ids") == [chat.brand_id], (
            f"the brand the conversation is about must ride the follow-up like the "
            f"customer does, never be dropped in silence: {args}"
        )

    def test_miss_follow_up_sends_the_brand_and_names_it(self, session_factory, monkeypatch) -> None:
        chat, first, second, calls = self._run(session_factory, monkeypatch, _MISS)
        assert "Brand: Sorento" in first, first
        self._assert_follow_up_call(chat, calls)
        assert "Brand: Sorento" in second, second

    def test_hit_follow_up_sends_the_brand_and_names_it(self, session_factory, monkeypatch) -> None:
        chat, first, second, calls = self._run(session_factory, monkeypatch, _HIT)
        assert "Brand: Sorento" in first, first
        self._assert_follow_up_call(chat, calls)
        assert "Brand: Sorento" in second, second


class TestB1r2OutstandingFollowUpKeepsTheBrand:
    """Probe B: the outstanding report, answered, then a follow-up."""

    def test_third_turn_report_still_carries_the_brand(self, session_factory, monkeypatch) -> None:
        chat = _Chat(
            session_factory, monkeypatch, attributes=["sales_orders.outstanding"], tool_body=_MISS
        )
        chat.say(
            "outstanding brand Sorento dealer Cheng Huat Sentul",
            _parser_output(
                domain_hint="order", intent_hint="check_order", order_status="outstanding",
                entities=[_brand("Sorento"), _cheng_huat()],
            ),
        )
        mark = len(chat.captured)
        chat.say(
            "3",
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[3],
            ),
        )
        answered = chat.calls_since(mark)
        assert answered and answered[-1][0] == "crm_outstanding_report", answered
        assert answered[-1][1].get("brand_ids") == [chat.brand_id], answered

        mark = len(chat.captured)
        third = chat.say(
            "delivered in August",
            _parser_output(
                domain_hint="order", intent_hint="check_order", order_status="outstanding",
                entities=[], date_mode="range",
                date_filter_start="2026-08-01", date_filter_end="2026-08-31",
            ),
        )
        calls = chat.calls_since(mark)
        report_calls = [(n, a) for n, a in calls if n == "crm_outstanding_report"]
        assert report_calls, f"the follow-up must re-run the report: {calls} / {third!r}"
        _, args = report_calls[-1]
        assert args.get("customer_ids") == [CHENG_HUAT_UUID], args
        assert args.get("brand_ids") == [chat.brand_id], (
            f"the third turn must keep the brand the report was asked about: {args}"
        )


class TestN7BrandTypedByCodeIsNamedOffTheLiveRow:
    def test_code_typed_brand_prints_the_brand_name(self, session_factory, monkeypatch) -> None:
        chat = _Chat(session_factory, monkeypatch, attributes=[], tool_body=_MISS)
        reply = chat.say(
            "delivery orders srt for Cheng Huat Sentul",
            _delivered_orders_qf([_brand("srt"), _cheng_huat()]),
        )
        order_calls = [(n, a) for n, a in chat.captured if n in fetch_mod.ORDER_TOOLS]
        assert order_calls and order_calls[0][1].get("brand_ids") == [chat.brand_id], chat.captured
        assert "Brand: Sorento" in reply, reply
        assert "Brand: srt" not in reply, reply


class TestS5UnlistedBrandBesideALiveOneIsSaidBack:
    def _run(self, session_factory, monkeypatch, tool_body):
        chat = _Chat(
            session_factory, monkeypatch, attributes=[], tool_body=tool_body, report_unresolved=False
        )
        reply = chat.say(
            "delivery orders Sorento and XYZ for Cheng Huat Sentul",
            _delivered_orders_qf([_brand("Sorento"), _brand("XYZ"), _cheng_huat()]),
        )
        return chat, reply

    def test_miss_names_the_unlisted_word(self, session_factory, monkeypatch) -> None:
        chat, reply = self._run(session_factory, monkeypatch, _MISS)
        order_calls = [(n, a) for n, a in chat.captured if n in fetch_mod.ORDER_TOOLS]
        assert order_calls and order_calls[0][1].get("brand_ids") == [chat.brand_id], chat.captured
        assert "XYZ" in reply, (
            f"an unlisted brand word beside a live one must be said back, never "
            f"silently filtered away: {reply!r}"
        )

    def test_hit_names_the_unlisted_word(self, session_factory, monkeypatch) -> None:
        chat, reply = self._run(session_factory, monkeypatch, _HIT)
        assert "XYZ" in reply, (
            f"an unlisted brand word beside a live one must be said back, never "
            f"silently filtered away: {reply!r}"
        )


class TestS6TypedUnlistedBrandEndsTheCarry:
    """Fix lane round 4, S6 (reviewer pass round 3 at 21d77994, probe P2): plan ruling 13
    says the brand carries until a later order turn types another brand. A typed brand
    that is not on the live list is that turn: it ends the carried brand, and is said
    back, never answered with the carried brand's rows."""

    def _run(self, session_factory, monkeypatch, tool_body):
        chat = _Chat(session_factory, monkeypatch, attributes=[], tool_body=tool_body)
        first = chat.say(
            "delivery orders brand Sorento for Cheng Huat Sentul",
            _delivered_orders_qf([_brand("Sorento"), _cheng_huat()]),
        )
        mark = len(chat.captured)
        second = chat.say(
            "delivery orders brand XYZ for Cheng Huat Sentul",
            _delivered_orders_qf([_brand("XYZ"), _cheng_huat()]),
        )
        return chat, first, second, chat.calls_since(mark)

    def _assert_no_carried_brand(self, chat, first, second, calls) -> None:
        assert "Brand: Sorento" in first, first
        order_calls = [(n, a) for n, a in calls if n in fetch_mod.ORDER_TOOLS]
        assert order_calls, f"the second turn must call an orders tool: {calls}"
        _, args = order_calls[0]
        assert args.get("customer_ids") == [CHENG_HUAT_UUID], args
        assert not args.get("brand_ids"), (
            f"a typed brand that is not on the list ends the carried brand; it must "
            f"never filter by the brand the earlier turn typed: {args}"
        )
        assert "Brand: Sorento" not in second, second
        assert "XYZ" in second, f"the unlisted brand must be said back: {second!r}"

    def test_miss_drops_the_carried_brand_and_names_xyz(self, session_factory, monkeypatch) -> None:
        self._assert_no_carried_brand(*self._run(session_factory, monkeypatch, _MISS))

    def test_hit_drops_the_carried_brand_and_names_xyz(self, session_factory, monkeypatch) -> None:
        self._assert_no_carried_brand(*self._run(session_factory, monkeypatch, _HIT))

    def test_the_next_bare_turn_does_not_bring_the_old_brand_back(
        self, session_factory, monkeypatch
    ) -> None:
        chat, _first, _second, _calls = self._run(session_factory, monkeypatch, _MISS)
        mark = len(chat.captured)
        third = chat.say("what about August", _august_qf())
        order_calls = [(n, a) for n, a in chat.calls_since(mark) if n in fetch_mod.ORDER_TOOLS]
        assert order_calls, f"the follow-up must call an orders tool: {chat.captured}"
        assert not order_calls[0][1].get("brand_ids"), order_calls[0][1]
        assert "Brand: Sorento" not in third, third


class TestN11BrandLookupFailureIsLogged:
    """Fix lane round 4, N11: a real error reading the live brands must not drop the
    carried brand in silence; the swallowed failure is logged at warning."""

    def test_a_brand_lookup_error_logs_a_warning(self, monkeypatch, caplog) -> None:
        import logging

        from app.services.chatbot import turn_runtime

        def boom(db):
            raise RuntimeError("brands table unreachable")

        monkeypatch.setattr(turn_runtime, "active_brands", boom)
        with caplog.at_level(logging.WARNING, logger=turn_runtime.logger.name):
            ids, names = turn_runtime.order_brand_filter(object(), {"entities": [_brand("Sorento")]}, None)
        assert (ids, names) == ([], [])
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings and "brand" in warnings[0].getMessage().lower(), caplog.records
