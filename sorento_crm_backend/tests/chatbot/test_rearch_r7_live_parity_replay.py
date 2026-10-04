"""R7 RED tests - live parity replay, PR #952 chatbot turn engine re-architecture.

Tester 37 (20 Sep 2026). The captain ran ONE live parity pass on the lane stack at coder
head `13e4977a2` (`.claude/handoffs/rearch-parity-live-13e4977a2.log`); 5 replies were not
production's. This file turns each into an engine-level RED pytest by REPLAYING the
parser verdict already recorded for that live turn against the clone DB
`sorento_ai_automation_rearch` (`chatbot.turns`, turn ids named per item below) - no
further OpenAI call is needed, and none was made writing this file (zero live runs, no
`scripts/chatbot_journey.py`, no turns to :8081).

**Test shape**: every test drives `engine.run_turn` (`test_rearch_r5_production_decides.py`'s
own `_run_turn_real` / `_run_turn_with_mcp_call`, real resolver/gate/narrower over seeded
Postgres rows) doubling only the two MCP seams (`FetchServices.mcp_call` and
`AnswerServices.mcp_probe` via `_mcp_probe_for`) - never `answer_bridge.answer_for` called
directly, and no hand-built `payload["_exit_kind"]`, matching every other rearch red-test
file's own convention (a unit test that bypasses `engine.py`'s precedence never catches a
precedence bug).

Postgres only (`session_factory`, blank schema). Every row seeded fresh per test; nothing
borrowed from the clone DB beyond the VERDICT SHAPE (uuids/codes are re-pointed at rows
seeded here, per the brief - CI's DB is empty).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.turn.policy import default_policy
from app.services.chatbot.turn.state import Focus, focus_to_wire
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_outstanding_lane import (
    _capturing_mcp,
    _enable_business_lane,
    _present_response,
    _resolve_services,
    _session_of,
    _wire_business_services,
)
from tests.chatbot.test_product_attachment_picker_stamp import (
    _seed_attachment_type,
    _seed_file_for,
)
from tests.chatbot.test_rearch_r3_bridge_engine import _ambiguous_hanlim_resolve_services
from tests.chatbot.test_rearch_r4_answering_a_miss import (
    _run as _run_answering,
    _seed_bare_contact,
    _write_session_vars,
)
from tests.chatbot.test_rearch_r5_production_decides import (
    _mcp_double,
    _mcp_probe_for,
    _run_turn_real,
    _run_turn_with_mcp_call,
    _seed_contact_and_get,
    _spy_resolve_payload,
)
from tests.chatbot.test_outstanding_lane import _run_turn as _run_turn_fake_resolver
from tests.chatbot.test_outstanding_lane import _seed_contact as _seed_business_contact


# Real `attachment_types` row wording (tester 38, 20 Sep 2026 - captain's own measurement
# of the production data, not invented). `_prefix_probe_attachment_type` matches a token
# against `description` too (entity_resolver.py:2410-2515), so a seed whose description
# reads "<Name>, seeded by ZZT" is reachable only from a token that already IS the type's
# own name - the real rows are reachable from the parser's shorthand words ("certificate",
# "photo", "drawing", ...) because their descriptions actually carry those words. Only
# `description` (and, for Certification, `is_certificate`) differs from production; `code`
# stays NULL, matching the model default (`app/models/resources.py:44`, nullable, no
# default) - the real rows carry no `code` either, exactly as the coder's own measurement
# ("`_seed_attachment_type`'s `code=None` ... is the reason it is a gap for Certification")
# found for a description-less seed.
_REAL_ATTACHMENT_TYPE_DESCRIPTIONS: dict[str, str] = {
    "Certification": "Certification, Cert, Certificate, Watermark Cert, WCM, PPS, Ikram by Purchasing",
    "Product Photos": "Product Photos, Photo, Image, Pictures by Marketing",
    "Technical Specifications": "Technical Specifications / Spec / Drawing by Marketing",
    "Product Videos": "Product Videos by Marketing",
}


def _seed_real_attachment_type(session_factory: Any, type_name: str) -> str:
    """`_seed_attachment_type` with the REAL row's description (and `is_certificate` for
    Certification), so a token that only reaches production through its description
    ("certificate", "drawing", ...) reaches the seeded row here too."""
    return _seed_attachment_type(
        session_factory,
        type_name,
        description=_REAL_ATTACHMENT_TYPE_DESCRIPTIONS[type_name],
        is_certificate=(type_name == "Certification"),
    )


# --------------------------------------------------------------------------- #
# Item 1 - AC-1701/AC-1702 (F6/F7): a miss's noun is the RESOLVED attachment-type
# label, never the raw customer word or a JS-style "null"/"None" literal.
#
# Live turn `bf87f9ef-2ebb-49e8-8b29-bc9edd355eb1` (F7): "But no photo matched
# these." - `answer.py::not_found_error_message`'s `product_attachment` branch
# (line ~3379) builds `attach_noun` off `attach_raws` (THIS turn's own raw
# entity words), never the resolved canonical label - "photo", the customer's
# own word, not "Product Photos".
#
# Live turn `0f732289-fe38-4a3e-bbd5-d472f32fa524` (F6, second step, "1" over
# the roster): "But no null matched these." - `not_found_error_message` reads
# `domain_hint = jsc.get(q, "domain_hint")` (answer.py:2613) off THIS TURN's OWN
# parser verdict only (`q = parser`, the literal argument
# `answer_bridge.answer_for` forwards unchanged - `full_payload`/`parser` are two
# different things, confirmed at answer.py:691-693). The recorded verdict for
# this turn is `message_type: "casual", domain_hint: null, entities: []` (a bare
# reference pick carries no restated domain) - so the `domain_hint ==
# "product_attachment"` branch is never entered, and control falls to the
# generic `elif use_breakdown: build_breakdown_msg(f"{status_label}{jsc.js_string
# (domain_hint)}")` (answer.py:3463-3466), where `jsc.js_string(None)` is the
# literal JS string `"null"` (`jsc.py:47-48`, `String(null)`) - printed as the
# domain word verbatim, even though the SAME turn's own breakdown bullet
# ("attachment_type: Product Photos") proves the resolved label was known via
# the gate/resolved payload the whole time.
# --------------------------------------------------------------------------- #


class TestMissNounIsTheResolvedAttachmentLabel:
    """Captain ruling, 20 Sep 2026: the miss noun is the RESOLVED attachment type label in
    both cases (AC-1702, matching the owner's hand pass 8 production reply), superseding the
    older prompt v11 capture `prod_sample/out-of-scope-438930735...` that printed the
    customer's own word."""

    def test_type_named_this_turn_uses_the_resolved_label_not_the_raw_word(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn `bf87f9ef-2ebb-49e8-8b29-bc9edd355eb1` (F7): product SRTW4002
        genuinely has no file, attachment_type "photo" is named THIS turn. AC-1702's own
        literal: 'But no Product Photos matched these.' - never the raw word "photo"."""
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTF7MISS")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        _seed_real_attachment_type(session_factory, "Product Photos")
        qf = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                {"raw": "photo", "hint": "attachment_type", "canonical_code": "photo",
                 "current_message": True, "confident": True},
                {"raw": code, "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "marketing_product", "suggested_agent": None, "team_source": None},
        )
        result, _captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"photo for {code}",
            msg_id="zzt-r7-f7-miss-named", mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert "But no Product Photos matched these." in reply, (
            f"AC-1702: the miss sentence's noun must be the RESOLVED attachment-type "
            f"label ('Product Photos'), never the customer's raw word ('photo') - live "
            f"turn bf87f9ef-2ebb-49e8-8b29-bc9edd355eb1 printed 'But no photo matched "
            f"these.': {reply!r}"
        )
        assert "no photo matched" not in reply, reply
        assert "null" not in reply and "None" not in reply, reply

    def test_type_carried_on_focus_after_a_pick_uses_the_resolved_label_not_null(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turns `19595aab-62e6-431e-8f28-c60db7b8fb4c` (roster) then
        `0f732289-fe38-4a3e-bbd5-d472f32fa524` ("1" over it) - F6's second step. The
        picked family member has no Product Photos file, so the answering turn is a
        genuine miss; AC-1701/AC-1702 read "Product Photos" there too, never "null"."""
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTF6ROST").replace("-", "")
        has_code, no_code = f"{base}A", f"{base}B"
        has_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=has_code)
        no_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=no_code)
        type_id = _seed_real_attachment_type(session_factory, "Product Photos")
        _seed_file_for(
            session_factory, product_id=has_id, attachment_type_id=type_id,
            company_id=DEFAULT_COMPANY_ID, filename=f"{has_code}.jpg",
        )
        qf1 = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                {"raw": base, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                {"raw": "photo", "hint": "attachment_type", "canonical_code": "photo",
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "marketing_product", "suggested_agent": None, "team_source": None},
        )
        answer_probe = _mcp_probe_for(
            {
                "crm_master_product_attachments_list": [
                    {
                        "product": {"product_code": has_code},
                        "attachment": {"attachment_type": "Product Photos", "original_filename": f"{has_code}.jpg"},
                        "company_name": "Sorento",
                    }
                ]
            }
        )
        result1, _c1 = _run_turn_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"photo for {base}",
            msg_id="zzt-r7-f6-roster-1", mcp_response={"data": []}, answer_mcp_probe=answer_probe,
        )
        reply1 = (result1.reply or {}).get("text") or ""
        assert "Which product do you mean? Please choose:" in reply1, (
            f"test setup sanity: an ambiguous 2-member family must raise gate.py's own "
            f"require-specific roster: {reply1!r}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        no_position = next(
            (o.get("position") for o in options if str(o.get("code") or "").upper() == no_code.upper()),
            None,
        )
        assert no_position is not None, (
            f"test setup sanity: the NO-photo family member must appear in the roster: {options!r}"
        )

        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[no_position],
        )
        result2 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf2, text_body=str(no_position),
            msg_id="zzt-r7-f6-roster-2", mcp_call=_mcp_double(other=lambda *_a, **_k: json.dumps({"data": []}))[0],
            answer_mcp_probe=answer_probe,
        )
        reply2 = (result2.reply or {}).get("text") or ""
        assert "But no Product Photos matched these." in reply2, (
            f"AC-1701/AC-1702: the attachment_type CARRIED on the focus (no entity named "
            f"this turn) must still resolve to its real label in the miss sentence - live "
            f"turn 0f732289-fe38-4a3e-bbd5-d472f32fa524 printed 'But no null matched "
            f"these.': {reply2!r}"
        )
        assert "no null matched" not in reply2, reply2
        assert "null" not in reply2 and "None" not in reply2, reply2


_MISS_DOMAINS = [d.name for d in default_policy().domains if d.supported]


class TestNoMissReplyEverNamesNullOrNone:
    """General guard, parametrized over every business domain (AC-1701/AC-1702's own
    principle stated generally): a miss composed with no restated domain_hint must never
    fall through to the generic `jsc.js_string(domain_hint)` branch and print the JS/Python
    null literal as the searched noun."""

    @pytest.mark.parametrize("domain", _MISS_DOMAINS)
    def test_a_reference_only_turn_with_no_restated_domain_never_prints_null_or_none(
        self, session_factory, monkeypatch, domain: str
    ) -> None:
        _seed_business_contact(session_factory, variables={})
        _enable_business_lane(session_factory)
        # A bare-word ("1") pick with an OPEN QUESTION seeded directly (never a real
        # first-turn roster - this guard is about the DOMAIN WORD the miss sentence
        # prints, not about how the pending was raised) whose `payload.domain` is
        # deliberately absent, the shape a reference-only casual turn's OWN verdict
        # carries no domain_hint for (mirrors the F6 recorded verdict: `message_type:
        # "casual", domain_hint: null, entities: []`).
        _write_session_vars(
            session_factory,
            {
                "open_question": {
                    "kind": "team_pick", "expects": "yes_no",
                    "options": [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {}}],
                    "team": "customer_service", "asked_at_turn": 1,
                    "payload": {"domain": domain, "escalate_offered": True},
                }
            },
        )
        call, captured = _capturing_mcp({"data": []})
        _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)
        qf = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        )
        result = _run_answering(
            session_factory, monkeypatch, qf=qf, text_body="tell me more",
            msg_id=f"zzt-r7-null-guard-{domain}",
        )
        reply = (result.reply or {}).get("text") or ""
        assert "no null matched" not in reply.lower(), (
            f"[{domain}] a miss reply must never print the JS 'null' literal as its "
            f"searched noun: {reply!r}"
        )
        assert "no none matched" not in reply.lower(), (
            f"[{domain}] a miss reply must never print the Python 'None' literal as its "
            f"searched noun: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Item 2 - AC-1703 (F8 certificate ask): the did-you-mean form for a NON-incoming
# `product_attachment`/certificate ask must be the STAMPED numbered form
# ("Did you mean:\n1. CODE - has/no certificate\n...\nReply with a code to
# continue, ..."), never the inline sentence ("Did you mean X, Y, or Z? Reply
# with a code to continue, ...") the live turn actually printed.
#
# Live turn `55ca1323-8cd0-45dd-8b9f-8600df5db523`: "SRTWT165-FT CERT" ->
# entities: product raw "SRTWT165-FT" (unresolved), attachment_type raw "CERT"
# (canonical_code "certificate") -> `derive_require` (predicate.py:192-193, every
# raw word in `_BARE_CERT_WORDS`) returns the BARE leg `{"certificate": True}`.
# `answer.py::build_suggest_offer`'s D1 arm (line 4157) is NOT `any_uuid` here
# (the near-miss candidates are real product codes, not promotion uuids), so it
# takes the "else: # Code mode" branch (line 4217) - `dym_annotate_on` (line
# 4223) decides stamped-numbered ("Did you mean:\n1. ... - has/no X", line
# 4236-4250) vs the inline sentence (line 4251-4257) actually observed live.
#
# MEASURED root cause (this session, via `_spy_resolve_payload`):
# `gate_debug.domain` reads correctly as `"product_attachment"` (the earlier
# hypothesis that `DOMAIN_PROBE`'s own domain lookup misses is WRONG - noted
# for the coder). The real cause is one step later: `miss_suggest.py:669-678`,
# `_scoping_from(cfg["requires"], gate=g, resolved=r)` (`requires =
# ["attachment_type", "certificate"]`) needs a REAL, uuid-carrying resolver
# match whose own `entity_type` is `"attachment_type"` or `"certificate"` - a
# BARE certificate leg (`derive_require`'s `{"certificate": True}`, no
# attachment_type ENTITY the resolver actually matched to a real
# `AttachmentType` row/uuid) never produces one, so `len(scoping) == 0`,
# `probe_skip_reason = "no_scoping_entity"` (miss_suggest.py:677), the probe
# never runs, and `dym_annotate_on` stays False - "photo" stamps correctly
# elsewhere in this corpus (F6/F7) because it resolves to a real, uuid'd
# "Product Photos" `AttachmentType` match; the bare word "cert"/"certificate"
# structurally never can, on this measured mechanism.
# --------------------------------------------------------------------------- #


class TestCertificateDidYouMeanIsTheStampedForm:
    def test_replaying_the_recorded_certificate_ask_gets_the_stamped_numbered_form(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTCERTFAM").replace("-", "")
        neighbour_nl, neighbour_qt = f"{base}-NL", f"{base}-QT"
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=base)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_nl)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_qt)
        type_id = _seed_real_attachment_type(session_factory, "Certification")

        # Real MCP presenter row: ONLY the base code has a certificate on file - a real
        # tool would never answer a row for a product with no attachment (same
        # convention `TestRequireSpecificRosterCopy` already uses).
        answer_probe = _mcp_probe_for(
            {
                "crm_master_product_attachments_list": [
                    {
                        "product": {"product_code": base},
                        "attachment": {"attachment_type": "Certification", "original_filename": f"{base}.pdf"},
                        "company_name": "Sorento",
                    }
                ]
            }
        )
        # Verdict shape re-pointed at the seeded rows (uuids/codes, per the brief) -
        # otherwise byte-identical to turn 55ca1323-8cd0-45dd-8b9f-8600df5db523's own
        # `raw.derived`: product raw carries a "-FT" suffix no seeded code has, the
        # attachment_type raw is the bare word "CERT".
        qf = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                {"raw": f"{base}-FT", "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True},
                {"raw": "CERT", "hint": "attachment_type", "canonical_code": "certificate",
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        )
        payload_calls = _spy_resolve_payload(monkeypatch)
        result, _captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"{base}-FT CERT",
            msg_id="zzt-r7-f8-cert-dym", mcp_response={"data": []}, answer_mcp_probe=answer_probe,
        )
        reply = (result.reply or {}).get("text") or ""
        payload = payload_calls[-1] if payload_calls else {}
        gate_domain = ((payload.get("gate") or {}).get("gate_debug") or {}).get("domain")

        assert reply.startswith(f'Couldn\'t find "{base}-FT" (product). Did you mean:'), (
            f"AC-1703: a non-incoming did-you-mean with real near-match candidates must "
            f"take the STAMPED NUMBERED form, never the inline sentence live turn "
            f"55ca1323-8cd0-45dd-8b9f-8600df5db523 printed ('Couldn't find \"SRTWT165-FT\" "
            f"(product). Did you mean SRTWT165, SRTWT165-NL, or SRTWT165-QT?'). Measured "
            f"gate_debug.domain={gate_domain!r} (correctly 'product_attachment' - NOT the "
            f"cause). Real cause: miss_suggest.py:669-678's `_scoping_from` finds no "
            f"uuid-carrying 'attachment_type'/'certificate' resolver match for a BARE "
            f"certificate leg, so `probe_skip_reason='no_scoping_entity'` and the probe "
            f"never runs - see this test's module comment: {reply!r}"
        )
        assert "Reply with a code to continue, or would you like me to escalate to " in reply, reply
        # Re-pinned, hand pass 11 owner ruling (21 Sep 2026): a certificate-typed ask
        # routes to purchasing certification, not marketing product - the resolved
        # attachment_type row's own `is_certificate` now overrides the parser's routing
        # on every certificate offer, this did-you-mean's included.
        assert "purchasing certification team" in reply, reply
        assert f"{base} - has certificate" in reply or f"{base} - has Certification" in reply, (
            f"AC-1703: the neighbours must carry a has/no stamp: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Item 3 - AC-1704 (F8 answers): replying "1" (or the typed code) to the
# certificate did-you-mean must continue the ORIGINAL certificate ask for that
# product with a real fetch - never the bare "No matching results found." live
# turn `71a34056-338a-4441-8c02-4bb786d0818f` printed, and the answered roster
# itself must not linger afterwards (contract 36).
#
# Recorded verdict for the "1" turn: `message_type: "casual", reference_target:
# "dym", entities: [], entity_op: "reuse"` - a bare reference pick with NO
# restated domain, exactly `TestAC1704ContinuingTheOriginalAsk`'s own
# `product_attachment` scenario (`test_rearch_r4_answering_a_miss.py:476-508`) -
# reused verbatim here (`_seed_bare_contact`/`_write_session_vars`/`Focus.extra`
# carries `attachment_type` forward the same way), with the roster's OWN 3 real
# SRTWT165-family options instead of that file's synthetic 2-option one.
#
# MEASURED (this session): with the Pending/focus hand-seeded in the ALREADY
# CORRECT shape (`kind="product_pick"`, `payload.domain="product_attachment"`,
# `focus.extra["attachment_type"]` carried), the ANSWERING mechanism itself
# (`turn/apply.py`'s contract-121 pick-continuation path) already runs the real
# fetch scoped to the picked uuid correctly - a GREEN CONTROL, both parametrized
# arms (position and typed code). The live turn's bare "No matching results
# found." is therefore NOT this mechanism failing to answer a well-formed
# roster; it is a downstream CONSEQUENCE of item 2's minting defect (the
# certificate ask never reaches the correctly-shaped stamped `product_pick`
# roster live, so whatever WAS minted for the inline-sentence form the
# customer actually saw was not in the shape this answering mechanism expects).
# Kept in this file as the control that isolates the two defects from each
# other, per the brief's own item 2/3 split.
# --------------------------------------------------------------------------- #


class TestAnsweringTheCertificateDidYouMeanContinuesTheOriginalAsk:
    _OPTIONS = [
        {
            "position": 1, "label": "ZZTCERTANS", "uuid": "33333333-3333-3333-3333-333333333331",
            "uuids": ["33333333-3333-3333-3333-333333333331"], "code": "ZZTCERTANS",
            "entity_type": "product", "payload": {},
        },
        {
            "position": 2, "label": "ZZTCERTANS-NL", "uuid": "33333333-3333-3333-3333-333333333332",
            "uuids": ["33333333-3333-3333-3333-333333333332"], "code": "ZZTCERTANS-NL",
            "entity_type": "product", "payload": {},
        },
        {
            "position": 3, "label": "ZZTCERTANS-QT", "uuid": "33333333-3333-3333-3333-333333333333",
            "uuids": ["33333333-3333-3333-3333-333333333333"], "code": "ZZTCERTANS-QT",
            "entity_type": "product", "payload": {},
        },
    ]

    def _seed(self, session_factory) -> None:
        _seed_bare_contact(session_factory)
        sv: dict[str, Any] = {
            "open_question": {
                "kind": "product_pick", "expects": "pick", "options": self._OPTIONS,
                "team": "marketing_product", "asked_at_turn": 1,
                "payload": {"domain": "product_attachment", "escalate_offered": True},
            },
            "focus": focus_to_wire(
                Focus(
                    extra={
                        "attachment_type": [
                            {
                                "raw": "CERT", "hint": "attachment_type", "canonical_code": "certificate",
                                "current_message": False, "confident": True,
                            }
                        ]
                    }
                )
            ),
        }
        _write_session_vars(session_factory, sv)
        _enable_business_lane(session_factory)

    @pytest.mark.parametrize(
        "answer_text, qf_kwargs",
        [
            pytest.param(
                "1",
                {"message_type": "casual", "intent_hint": None, "domain_hint": None,
                 "entities": [], "reference_positions": [1], "reference_target": "dym", "entity_op": "reuse"},
                id="position",
            ),
            pytest.param(
                "ZZTCERTANS",
                {"message_type": "business_query", "intent_hint": None, "domain_hint": None,
                 "entities": [{"raw": "ZZTCERTANS", "hint": "product", "canonical_code": "ZZTCERTANS",
                               "current_message": True, "confident": True}]},
                id="code",
            ),
        ],
    )
    def test_answering_continues_the_certificate_ask_with_a_real_fetch(
        self, session_factory, monkeypatch, answer_text: str, qf_kwargs: dict[str, Any]
    ) -> None:
        self._seed(session_factory)
        call, captured = _capturing_mcp({"has_result": False, "items": []})
        _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)

        qf = _parser_output(**qf_kwargs)
        result = _run_answering(
            session_factory, monkeypatch, qf=qf, text_body=answer_text,
            msg_id=f"zzt-r7-f8-answer-{qf_kwargs.get('message_type')}",
        )
        reply = (result.reply or {}).get("text") or ""
        assert result.error is None, result.error
        assert reply != "No matching results found.", (
            f"AC-1704: answering the did-you-mean roster must continue the ORIGINAL "
            f"certificate ask with a real fetch - live turn "
            f"71a34056-338a-4441-8c02-4bb786d0818f printed the bare 'No matching results "
            f"found.' with the roster still open: {reply!r} (captured={captured!r})"
        )
        product_ids = {u for _name, args in captured for u in (args.get("product_ids") or [])}
        assert "33333333-3333-3333-3333-333333333331" in product_ids, (
            f"the fetch must be scoped to the PICKED option's own uuid (position 1 / "
            f"typed code both resolve to the same option): captured={captured!r}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "product_pick", (
            f"contract 36: the ANSWERED did-you-mean roster itself must not linger - a "
            f"real zero-row fetch legitimately opening its OWN new escalate offer "
            f"(team_pick) afterwards is correct, not a lingering roster: {open_question!r}"
        )


# --------------------------------------------------------------------------- #
# Item 4 - AC-1703 (F8 "Technical drawings sttwc286-SH"): an unrecognised
# attachment-type word paired with an unresolved-but-near-matching product in
# the SAME message. Live turn `0d4abc93-2d53-483c-a56b-9974bf8429e3` answers the
# document-type clarify ("I don't know 'Technical drawings' as a document type.
# Types I know: ..."); the owner's paste says production instead answers the
# PRODUCT did-you-mean.
#
# Measured mechanism: `answer.py::not_found_error_message`'s
# `attachment_types_on_file` branch (line 3233-3252) sets `is_clarification =
# True` whenever the `predicate` carries `attachment_types_on_file` (an
# unrecognised attachment_type word) - REGARDLESS of whether the product token
# ALSO has real near-match candidates. `build_suggest_offer`'s own `is_clar`
# gate (`miss_suggest.py:394,414`, `if not is_clar and not require_spec:`) then
# SKIPS building `d1s` (the product did-you-mean candidates) entirely whenever
# `is_clarification` is True - so the two clarifications structurally cannot
# both reach the customer, and the attachment-type one wins outright by running
# first in `not_found_error_message`'s own if/elif chain.
#
# DIFFERENTIAL NOT ATTEMPTED: `answer.py`'s `not_found_error_message` /
# `build_suggest_offer` are called from exactly two production sites
# (`answer_bridge.py:691`, the bridge under test here, and
# `lanes/business/__init__.py:1892`, `complete_answer`'s own pre-rearch call) -
# but `git show origin/main:sorento_crm_backend/app/services/chatbot/lanes/
# business/answer.py | grep attachment_types_on_file` returns NOTHING: this
# entire branch does not exist on `origin/main`'s copy of the file at all, so it
# was added on THIS lane's own history (not inherited, not shared with a frozen
# main), and calling it a second time via `complete_answer` on this SAME branch
# would exercise the byte-identical function with the byte-identical
# `is_clar`/`d1s` interaction - it cannot show what "production" (main) does,
# only that this branch's OWN two callers agree with each other, which is not
# in question. Falling back to the UAC's own verbatim anchor (AC-1703) and the
# measured mechanism above, cited by file:line, for a coder to verify against
# the owner's actual paste.
# --------------------------------------------------------------------------- #


class TestUnknownAttachmentTypeAndUnplacedProductInOneAsk:
    def test_the_product_did_you_mean_wins_over_the_document_type_clarify(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTTDRAW").replace("-", "")
        neighbour_p, neighbour_pp = f"{base}-P", f"{base}-PP"
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=base)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_p)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_pp)
        # The clone's own 4 real types (Certification, Product Photos, Product Videos,
        # Technical Specifications) - "Technical drawings" is genuinely NOT one of them,
        # matching the live turn exactly.
        for type_name in ("Certification", "Product Photos", "Product Videos", "Technical Specifications"):
            _seed_real_attachment_type(session_factory, type_name)

        # Verdict re-pointed at the seeded family, otherwise the recorded shape verbatim:
        # a MISTYPED product raw ("sttwc286-SH" -> here `{base}SH` with a typo'd case
        # shape is unnecessary - the live defect is about ORDER OF EVALUATION, not typo
        # distance) alongside an unrecognised attachment_type raw.
        qf = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                {"raw": f"s{base}", "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True},
                {"raw": "Technical drawings", "hint": "attachment_type", "canonical_code": "technical drawing",
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        )
        result, _captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"Technical drawings s{base}",
            msg_id="zzt-r7-f8-tech-drawings", mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Did you mean:" in reply or "Did you mean " in reply, (
            f"AC-1703: a mistyped product token with real near-match candidates must "
            f"answer the PRODUCT did-you-mean even when the SAME message also names an "
            f"unrecognised attachment-type word - live turn "
            f"0d4abc93-2d53-483c-a56b-9974bf8429e3 answered the document-type clarify "
            f"instead ('I don't know '\''Technical drawings'\'' as a document type. Types "
            f"I know: ...'). Measured cause: answer.py:3233-3252's "
            f"`attachment_types_on_file` branch sets `is_clarification=True`, which "
            f"miss_suggest.py:394,414's own `is_clar` gate then uses to skip building the "
            f"product did-you-mean candidates entirely - see this test's module comment "
            f"for why a differential against `complete_answer` was not feasible: {reply!r}"
        )
        assert "Reply with a code to continue" in reply, reply


# --------------------------------------------------------------------------- #
# Item 5 - AC-1708 / reviewer S7 companion: after a multi-domain ambiguous
# customer pick is answered, BOTH named domains must be fetched for the picked
# customer (the pending keeps every domain) - the captain's ruling that an
# `offer`/`access_ask` resolver exit is answered by the bridge regardless of how
# many domains the plan names, while hit/miss COMPOSITION for a multi-domain
# plan stays on `turn/compose.py`.
#
# Builds on (does not edit) `test_rearch_r6_review_round.py::
# TestMultiDomainAmbiguousCustomerUsesGatesOwnHeader`, which already pins the
# HEADER half red (S7). This was meant to be the missing SECOND half: once "1"
# is answered, both the "order" and "incoming" fetches for HANLIM TRADING SDN
# BHD must run.
#
# MEASURED (this session): it already does. GREEN CONTROL, not a red - see the
# assertion's own comment below for the fixture-double nuance (why the second
# section's own rendered TEXT does not literally say "incoming"). The pending
# genuinely keeps both domains and the bridge/compose path fetches both after
# the pick; the "missing half" this item set out to find does not reproduce.
# --------------------------------------------------------------------------- #


class TestMultiDomainPickFetchesEveryNamedDomain:
    def test_answering_the_multi_domain_customer_pick_fetches_both_domains(
        self, session_factory, monkeypatch
    ) -> None:
        def spy_probe(*, tool: str, contact_id: Any, entities: Any, semantic_input: dict, user_prompt: str) -> Any:
            return {"items": [], "has_result": False}

        _seed_business_contact(session_factory, variables={})
        result1, _c1 = _run_turn_fake_resolver(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint=None, intent_hint=None,
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
                **do_window(),
                asks=[{"domain": "order"}, {"domain": "incoming"}],
            ),
            text_body="orders and incoming for hanlim",
            msg_id="zzt-r7-multi-domain-pick-1",
            attributes=["sales_orders.outstanding"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        reply1 = (result1.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        # Tolerant of the SEPARATE, already-red S7 header defect
        # (`TestMultiDomainAmbiguousCustomerUsesGatesOwnHeader`, `test_rearch_r6_review_
        # round.py`) - this test is about what gets FETCHED after the pick, not which
        # header text asks the question, so the sanity check only needs a genuine picker
        # to have fired, whichever header it used.
        assert open_question.get("kind") in ("customer_pick", "customer"), (
            f"test setup sanity: the ambiguous customer must raise a real picker: "
            f"reply={reply1!r} open_question={open_question!r}"
        )
        domains_asked = open_question.get("payload", {}).get("domains") or [
            open_question.get("payload", {}).get("domain")
        ]

        calls2: list[str] = []

        def _mcp_call(name: str, args: dict[str, Any]) -> Any:
            calls2.append(name)
            if name == "crm_order_management_orders_list":
                return _present_response()(
                    name,
                    json.dumps(
                        {"data": [
                            {"order_number": "ZZT-HANLIM-ORD-1", "debtor_name": "HANLIM TRADING SDN BHD",
                             "order_date": "2026-01-15", "order_status": None,
                             "lines": [{"product": {"product_code": "ZZTHANLIM"}, "quantity": 1}]}
                        ]}
                    ),
                )
            if name == "crm_incoming_stock_list":
                return json.dumps({"has_result": True, "items": [{"title": "ZZTHANLIM incoming"}]})
            return json.dumps({"has_result": False, "items": []})

        # `test_outstanding_lane._run_turn`'s OWN internal call to `_wire_business_
        # services` (its last wiring step, using `_capturing_mcp(mcp_response=None)`
        # by default) would clobber any `fetch_services` patch set BEFORE calling it -
        # `_run_turn` has no parameter for a per-tool-name differentiated double, so
        # this turn is driven with `_wire_business_services` + `engine_mod.run_turn`
        # directly (the same wiring `_run_turn` itself does, minus the parser/access
        # stub duplication this file's other tests do not need for a SECOND turn on
        # an already-stubbed contact).
        from app.services.chatbot.head import parser as parser_mod
        from tests.chatbot.test_engine import _envelope

        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[1],
        )
        _enable_business_lane(session_factory)
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, *, agent_code, contact_id, space_id: {
                "allowed": True, "decision": "allow", "agent_name": "General",
                "attributes": ["sales_orders.outstanding"], "all_attributes_allowed": None,
            },
        )
        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf2)
        _wire_business_services(
            monkeypatch, resolve_services=_ambiguous_hanlim_resolve_services(spy_probe), mcp_call=_mcp_call,
        )
        envelope2 = _envelope()
        envelope2.message["message"]["messageId"] = "zzt-r7-multi-domain-pick-2"
        envelope2.message["message"]["message"]["text"] = "1"
        result2 = engine_mod.run_turn(envelope2, session_factory=session_factory)
        reply2 = (result2.reply or {}).get("text") or ""
        # GREEN CONTROL (measured this session): both tool calls DO fire and both
        # sections DO compose - the "missing half" this item set out to red does not
        # reproduce on the current engine. `crm_order_management_orders_list` renders
        # its own real "Here are the orders I found." intro (the seeded row's own
        # data appears); the second, generic "Here are the results." section is this
        # test's OWN thin double for `crm_incoming_stock_list` (a bare dict, never
        # routed through a domain-specific composer) rendering the library's shared
        # fallback intro - not evidence the domain was dropped, only that this
        # double did not feed it a shape a nicer composer recognises. Kept as the
        # control that isolates "does the SECOND domain fetch at all" (yes) from
        # "is its OWN produced text pretty" (a fixture-double question, not this
        # item's).
        assert "crm_order_management_orders_list" in calls2 and "crm_incoming_stock_list" in calls2, (
            f"AC-1708 companion: once the ambiguous customer is picked, BOTH named "
            f"domains (order, incoming) must be fetched for that customer - the pending "
            f"must keep every domain the original ask named: calls={calls2!r} "
            f"reply={reply2!r} open_question after the pick={domains_asked!r}"
        )
        assert "Here are the orders I found." in reply2, reply2


# --------------------------------------------------------------------------- #
# Item 6 - AC-1703 (F8 "Incoming srtwt7202-new"). Live turn
# `5cde645f-7e01-489b-833c-fe41876daa9f` answers "I don't know 'new' as a
# product type." The brief's own premise is that "the parser split 'new' off as
# a product_type word" - MEASURED FALSE: the turn's own recorded `raw.derived`/
# `raw.parser_raw` (`chatbot.turns.trace`, `understood` stage) carries exactly
# ONE entity, `{"raw": "srtwt7202-new", "hint": "product", ...}` - no separate
# `product_type` entity at all. The parser already got this right.
#
# The split happens DOWNSTREAM, in `resolve_gate.py`'s `require`/`predicate_words`
# derivation (`resolve_gate.py:703-725`): `derive_require` (`predicate.py:180-181`,
# `_BARE_LEG_BY_INTENT["check_incoming"] -> {"incoming": True}`) fires
# UNCONDITIONALLY for every `check_incoming` turn, including a bare product-code
# lookup with no attribute/class-word ask at all - there is no gate on "does
# this turn actually describe a SET" before the predicate/described-set pipeline
# engages. `derive_predicate_words` (`predicate.py:232-235`) then adds the leg
# word itself ("incoming") to `predicate_words`, and the SERVER-SIDE described-
# set matcher (reached through the same `resolve_entity_body` the real resolver
# call posts, confirmed live via this turn's own `looked_up` envelope:
# `unrecognized_terms` ends up containing "new") treats the token's own trailing
# hyphen-suffix as a candidate class/type word once the leg has engaged - a
# genuine ENGINE-level defect, not a parser-prompt one, replaying the ALREADY-
# CORRECT recorded verdict below.
#
# This directly contradicts the brief's instruction to treat this item as "no
# engine red" - see the handoff for detail. Written and left RED, matching what
# was actually measured.
# --------------------------------------------------------------------------- #


class TestIncomingHyphenSuffixNeverReadsAsAnUnknownProductType:
    """Reviewer MB-3 (re-check round, 20 Sep 2026): `resolve_gate._token_of`
    (`resolve_gate.py:587-602`) folds a token's hyphens ONLY when the entity's OWN
    `hint == "product"` - the resolver never reads it, the PARSER's hint word decides.
    The captain's live re-check saw the parser emit `inbound_shipment` for this exact
    ask on one of two runs (`Couldn't find: "srtwt7202-new" (inbound shipment)`), so
    `hint="product"` alone (this class's original, still-passing parametrization) does
    not guard AC-1703 for the shape live traffic actually produces. Re-parametrized
    over BOTH hints per the re-check brief; `allowed_lookup` is `['product',
    'inbound_shipment', 'category', 'brand']` in both (measured, reviewer), so the
    guard's own `allowed` filter is not what differs - only the token folding is."""

    @pytest.mark.parametrize("hint", ["product", "inbound_shipment"])
    def test_replaying_the_recorded_single_entity_verdict_still_answers_the_product_dym(
        self, session_factory, monkeypatch, hint: str
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTINCNEW").replace("-", "")
        neighbour_bl, neighbour_gm = f"{base}-BL", f"{base}-GM"
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_bl)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_gm)

        # Byte-identical to turn 5cde645f-7e01-489b-833c-fe41876daa9f's own recorded
        # `raw.derived` shape, re-pointed at the seeded family: ONE product entity,
        # raw carrying a "-new" suffix no seeded code has, no product_type entity.
        # `hint` is the ONE input MB-3 measured as deciding whether `_token_of` folds
        # the hyphen suffix before the fuzzy scan ever sees it.
        qf = _parser_output(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[
                {"raw": f"{base}-new", "hint": hint, "canonical_code": None,
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
        )
        result, _captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"Incoming {base}-new",
            msg_id=f"zzt-r7-incoming-new-dym-{hint}", mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert "as a product type" not in reply, (
            f"[hint={hint}] the parser's own verdict already names ONE clean product "
            f"entity ('{base}-new', no separate product_type word) - the engine must "
            f"not re-derive a bogus 'new' product-type word downstream of it, "
            f"whichever hint word the parser guessed for the SAME token. Live turn "
            f"5cde645f-7e01-489b-833c-fe41876daa9f answered 'I don't know '\''new'\'' as "
            f"a product type.' from this exact recorded (already-correct) verdict: "
            f"{reply!r}"
        )
        assert "Did you mean" in reply, (
            f"[hint={hint}] AC-1703: a real near-match family must be offered as the "
            f"did-you-mean, same as every other F8 case, regardless of the parser's "
            f"own hint word for the unplaced token: {reply!r}"
        )
