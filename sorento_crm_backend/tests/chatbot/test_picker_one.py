"""PICKER-ONE (owner, 13 Oct 2026): one customer picker for every entry path.

The owner read three different "which customer?" pickers in one week:

- "Which one do you mean? 1. HANLIM 2. HANLIM TRADING" (no stamp: a carried-subject or a
  typed-names roster, which nothing probed),
- "Which one do you mean? 1. HANLIM - no DO 2. HANLIM TRADING - no DO" (the resolver roster,
  stamped by `turn_runtime.candidates_by_kind`),
- "Which customer do you mean? Please choose: 1. X (MCH, SRT) - has DO" (the gate's own
  picker, its text rewritten by `pickers.annotate_customer`).

Rule: reuse, never parallel paths. Every customer disambiguation renders through
`turn/compose.py::compose_question`, its options built by `turn/narrow.py::_options` and
stamped by ONE helper (`pickers.customer_stamp`), so the same two customers read
byte-identically whichever path asked about them. The company tag stays only when the
options' companies differ. R20 (owner round 7, 13 Sep 2026) stands: an outstanding ask's
roster carries no stamp on any path.

Plan: `documentation/plans/chatbot/PLAN-picker-one-13oct.md`.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import answer_bridge
from app.services.chatbot import copy as copy_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes.business import pickers
from app.services.chatbot.turn import compose as turn_compose
from app.services.chatbot.turn import narrow
from app.services.chatbot.turn import pending as pending_mod
from app.services.chatbot.turn.state import Focus, Profile

UUID_A = "aaaaaaaa-0000-4000-8000-000000000001"
UUID_B = "bbbbbbbb-0000-4000-8000-000000000002"

#: Two customers of one trading word, the owner's own HANLIM shape.
HANLIM = {"uuid": UUID_A, "code": "300-H001", "name": "HANLIM SDN BHD"}
HANLIM_TRADING = {"uuid": UUID_B, "code": "300-H030", "name": "HANLIM TRADING SDN BHD"}

#: HANLIM TRADING has a delivered DO, HANLIM has none.
PROBE = {
    "answers": [
        {
            "title": "DO-1",
            "fields": [
                {"label": "Customer", "value": "HANLIM TRADING SDN BHD"},
                {"label": "Actual Delivery Date", "value": "2026-10-01"},
            ],
        }
    ]
}

EXPECTED_SAME_COMPANY = (
    "Which one do you mean?\n"
    "1. HANLIM SDN BHD - no DO\n"
    "2. HANLIM TRADING SDN BHD - has DO"
)

EXPECTED_TWO_COMPANIES = (
    "Which one do you mean?\n"
    "1. HANLIM SDN BHD (MCH) - no DO\n"
    "2. HANLIM TRADING SDN BHD (SRT) - has DO"
)


def _bases() -> set[str] | None:
    return pickers.customer_bases_with_do(PROBE)


def _resolved(companies: tuple[str, str]) -> dict[str, Any]:
    """The resolver payload both customers came back in (`entity_resolver` shape)."""
    return {
        "resolutions": [
            {
                "token": "hanlim",
                "matches": [
                    {
                        "entity_type": "customer",
                        "canonical_code": row["code"],
                        "uuid": row["uuid"],
                        "company_code": company,
                        "company_name": f"{company} Company",
                        "display": {"customer_name": row["name"]},
                    }
                    for row, company in zip((HANLIM, HANLIM_TRADING), companies)
                ],
            }
        ],
        "unresolved_tokens": [],
    }


# --------------------------------------------------------------------------- #
# The four entry paths, each through the function that path really calls
# --------------------------------------------------------------------------- #


def _gate_offer_text(companies: tuple[str, str], *, bases: set[str] | None) -> str:
    """The gate's own ambiguous-customer picker: `resolve_gate.run`'s `offer` exit,
    answered by `answer_bridge.question_for` (what the engine calls for it). The payload
    is the shape `gate.run_gate`'s customer arm emits (`compatible_entities` rows carry
    the printed line as `title`, company suffix included) after the resolve step has
    measured the DO probe."""
    titles = [
        f"{HANLIM['name']} ({companies[0]})",
        f"{HANLIM_TRADING['name']} ({companies[1]})",
    ]
    payload = {
        "_exit_kind": "offer",
        "gate_clarification": "Which customer do you mean? Please choose:\n"
        + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles)),
        "compatible_entities": [
            {"uuid": row["uuid"], "entity_type": "customer", "code": row["code"], "title": title}
            for row, title in zip((HANLIM, HANLIM_TRADING), titles)
        ],
        "customer_probe_entities": [
            {"uuid": row["uuid"], "entity_type": "customer", "code": row["code"]}
            for row in (HANLIM, HANLIM_TRADING)
        ],
        "picker_families": {
            "HANLIM": [HANLIM["uuid"]],
            "HANLIM TRADING": [HANLIM_TRADING["uuid"]],
        },
        "customer_bases_with_do": sorted(bases) if bases is not None else None,
        "require_specific": True,
        "resolved": _resolved(companies),
        "gate_debug": {"domain": "order"},
    }
    parser = {
        "domain_hint": "order",
        "intent_hint": "check_order",
        "entities": [{"raw": "hanlim", "hint": "customer", "current_message": True, "confident": True}],
    }
    answer = answer_bridge.question_for(
        payload,
        parser=parser,
        ctx={"parse": {"output": parser}, "contact": {"id": "zzt-contact"}, "session": {}},
        canned=copy_mod.fallback_copy(),
        asked_at_turn=1,
    )
    assert answer is not None and answer.question is not None
    assert answer.question.kind == "customer_pick"
    return answer.text


def _resolver_roster_text(companies: tuple[str, str], *, bases: set[str] | None) -> str:
    """The narrower's roster over what the resolver matched for one typed word:
    `turn_runtime.candidates_by_kind` (the stamp) -> `narrow.decide` -> `compose_question`."""
    resolved = _resolved(companies)
    compatible = [
        {
            "entity_type": "customer",
            "canonical_code": row["code"],
            "uuid": row["uuid"],
            "display_name": row["name"],
            "raw": "hanlim",
        }
        for row in (HANLIM, HANLIM_TRADING)
    ]
    grouped = turn_runtime.candidates_by_kind(
        {},
        compatible,
        bases,
        None,
        turn_runtime.company_names_by_uuid(resolved),
        turn_runtime.company_codes_by_uuid(resolved),
    )
    outcome = narrow.decide(
        kind="customer",
        policy_value="must_narrow_one",
        focus=Focus(customers=[{"raw": "hanlim", "hint": "customer", "current_message": True}]),
        profile=Profile(),
        resolved_candidates=grouped["customer"],
        family_grouping="ledger_family",
    )
    assert outcome.ask_kind == "customer_pick", outcome
    return turn_compose.compose_question(pending_mod.ask(outcome.ask_kind, outcome.ask_options)).text


def _focus_roster_text(rows: list[dict[str, Any]], *, bases: set[str] | None) -> str:
    """A roster `narrow.decide` raises off the FOCUS rows (no resolver answer this turn),
    stamped at the engine's ask point by `turn_runtime.stamp_customer_options`."""
    outcome = narrow.decide(
        kind="customer",
        policy_value="must_narrow_one",
        focus=Focus(customers=rows),
        profile=Profile(),
        family_grouping="ledger_family",
    )
    assert outcome.ask_kind == "customer_pick", outcome
    options = turn_runtime.stamp_customer_options(outcome.ask_options, bases)
    return turn_compose.compose_question(pending_mod.ask(outcome.ask_kind, options)).text


def _carried_subject_text(*, bases: set[str] | None) -> str:
    """Carried-subject clarify: two customers carried on the focus from an earlier turn,
    named but not settled (no uuid), asked about again."""
    rows = [
        {"raw": row["code"], "canonical_code": row["code"], "name": row["name"], "hint": "customer",
         "current_message": False}
        for row in (HANLIM, HANLIM_TRADING)
    ]
    return _focus_roster_text(rows, bases=bases)


def _typed_names_text(*, bases: set[str] | None) -> str:
    """Typed-names roster: the contact typed both names this message and nothing resolved
    them (`narrow.decide`'s `must_narrow_one` arm over the typed words)."""
    rows = [
        {"raw": row["name"], "hint": "customer", "current_message": True}
        for row in (HANLIM, HANLIM_TRADING)
    ]
    return _focus_roster_text(rows, bases=bases)


# --------------------------------------------------------------------------- #
# Parity
# --------------------------------------------------------------------------- #


class TestOnePickerTextForEveryEntryPath:
    def test_gate_offer_prints_the_compose_question_format(self) -> None:
        assert _gate_offer_text(("SRT", "SRT"), bases=_bases()) == EXPECTED_SAME_COMPANY

    def test_resolver_roster_prints_the_same_bytes(self) -> None:
        assert _resolver_roster_text(("SRT", "SRT"), bases=_bases()) == EXPECTED_SAME_COMPANY

    def test_carried_subject_roster_prints_the_same_bytes(self) -> None:
        assert _carried_subject_text(bases=_bases()) == EXPECTED_SAME_COMPANY

    def test_typed_names_roster_prints_the_same_bytes(self) -> None:
        assert _typed_names_text(bases=_bases()) == EXPECTED_SAME_COMPANY

    def test_all_four_paths_are_byte_identical(self) -> None:
        texts = {
            "gate": _gate_offer_text(("SRT", "SRT"), bases=_bases()),
            "resolver": _resolver_roster_text(("SRT", "SRT"), bases=_bases()),
            "carried": _carried_subject_text(bases=_bases()),
            "typed": _typed_names_text(bases=_bases()),
        }
        assert len(set(texts.values())) == 1, texts


class TestCompanyTagOnlyWhenCompaniesDiffer:
    def test_gate_offer_keeps_the_tag_when_the_companies_differ(self) -> None:
        assert _gate_offer_text(("MCH", "SRT"), bases=_bases()) == EXPECTED_TWO_COMPANIES

    def test_resolver_roster_keeps_the_same_tag(self) -> None:
        assert _resolver_roster_text(("MCH", "SRT"), bases=_bases()) == EXPECTED_TWO_COMPANIES

    def test_one_company_for_every_option_prints_no_tag(self) -> None:
        text = _gate_offer_text(("SRT", "SRT"), bases=_bases())
        assert "(SRT)" not in text, text


    def test_the_same_companies_in_another_order_are_the_same_set(self) -> None:
        """Reviewer S1: "MCH, SRT" and "SRT, MCH" are one company set, so no tag."""
        options = narrow.customer_options(
            [
                {"name": "JYL JUBIN SDN BHD", "uuid": UUID_A, "company_codes": ["MCH", "SRT"]},
                {"name": "YI HONG TILING", "uuid": UUID_B, "company_codes": ["SRT", "MCH"]},
            ]
        )
        assert [o["label"] for o in options] == ["JYL JUBIN SDN BHD", "YI HONG TILING"]


class TestNotMeasuredMeansNoStamp:
    """`None` bases is the probe's NOT MEASURED arm (failed, saturated, or R20's outstanding
    ask that never probes): no stamp on any path, never a confident "no DO"."""

    @pytest.mark.parametrize(
        "render",
        [
            lambda: _gate_offer_text(("SRT", "SRT"), bases=None),
            lambda: _resolver_roster_text(("SRT", "SRT"), bases=None),
            lambda: _carried_subject_text(bases=None),
            lambda: _typed_names_text(bases=None),
        ],
        ids=["gate", "resolver", "carried", "typed"],
    )
    def test_unmeasured_roster_is_bare_and_identical(self, render) -> None:
        assert render() == (
            "Which one do you mean?\n1. HANLIM SDN BHD\n2. HANLIM TRADING SDN BHD"
        )


class TestOneStampHelper:
    def test_customer_stamp_reads_the_family_base(self) -> None:
        bases = _bases()
        assert pickers.customer_stamp("HANLIM TRADING SDN BHD [A/C II]", bases) == "has DO"
        assert pickers.customer_stamp("HANLIM SDN BHD", bases) == "no DO"
        assert pickers.customer_stamp("HANLIM SDN BHD", None) is None

    def test_stamp_customer_options_keeps_a_stamp_already_measured(self) -> None:
        options = [{"position": 1, "label": "X", "name": "X", "stamp": "has DO"}]
        assert turn_runtime.stamp_customer_options(options, set())[0]["stamp"] == "has DO"

    def test_gate_picker_records_the_measurement_as_data(self) -> None:
        """The gate's customer arm keeps what its probe measured as DATA on the item the
        bridge reads, so the turn neither probes twice nor reads its own prose back."""
        from app.services.chatbot.lanes.business import resolve_gate

        assert not hasattr(pickers, "annotate_customer"), (
            "annotate_customer rewrote the rendered picker text; the one picker path "
            "stamps options instead"
        )
        assert hasattr(resolve_gate, "measure_customer_picker")
        gate = {"compatible_entities": [], "customer_probe_entities": [{"uuid": UUID_A}]}
        out = resolve_gate.measure_customer_picker(dict(gate), probe=PROBE, parser={})
        assert out["customer_bases_with_do"] == sorted(_bases())
        out = resolve_gate.measure_customer_picker(dict(gate), probe=None, parser={})
        assert out["customer_bases_with_do"] is None


# --------------------------------------------------------------------------- #
# The engine's ask point, end to end (reviewer B1): a typed-names roster - two customer
# words the resolver placed nowhere - is stamped by the same helper on a real turn, and
# an outstanding ask (R20) is neither probed nor stamped.
# --------------------------------------------------------------------------- #


def _typed_names_turn(
    session_factory, monkeypatch, *, order_status: str | None, office: bool = True, attributes: list[str] | None = None
):
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from tests.chatbot.conftest import validating_resolve_entity
    from tests.chatbot._turn_helpers import make_office_access_type
    from tests.chatbot.test_engine import CONTACT_ID, _parser_output
    from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

    probes: list[Any] = []

    def probe(*, tool, contact_id, entities, semantic_input, user_prompt):
        probes.append(entities)
        return {
            "items": [
                {
                    "title": "DO-1",
                    "fields": [
                        {"label": "Customer", "value": "HANLIM TRADING SDN BHD"},
                        {"label": "Actual Delivery Date", "value": "2026-10-01"},
                    ],
                }
            ],
            "has_result": True,
        }

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(
            lambda body: {"tokens": [], "resolutions": [], "unresolved_tokens": []}
        ),
        probe=probe,
    )
    _seed_contact(session_factory, variables={})
    if office:
        # V1-DOC-SCOPE slice 3: plays an office role, unlinked != customer.
        make_office_access_type(session_factory, CONTACT_ID)
    result, _captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=_parser_output(
            domain_hint="order",
            intent_hint="check_order",
            order_status=order_status,
            entities=[
                {"raw": "hanlim trading", "hint": "customer", "canonical_code": None,
                 "current_message": True, "confident": True},
                {"raw": "hanlim hardware", "hint": "customer", "canonical_code": None,
                 "current_message": True, "confident": True},
            ],
        ),
        text_body="DO for hanlim trading and hanlim hardware",
        msg_id=f"zzt-picker-one-typed-{order_status}",
        resolve_services=services,
        **({"attributes": attributes} if attributes is not None else {}),
    )
    return (result.reply or {}).get("text") or "", probes


class TestAskPointStampsATypedNamesRoster:
    def test_the_typed_names_roster_is_stamped_on_a_real_turn(self, session_factory, monkeypatch) -> None:
        reply, probes = _typed_names_turn(session_factory, monkeypatch, order_status=None)
        assert reply == "Which one do you mean?\n1. hanlim trading - has DO\n2. hanlim hardware - no DO"
        assert len(probes) == 1, probes

    def test_an_outstanding_typed_names_roster_is_neither_probed_nor_stamped(
        self, session_factory, monkeypatch
    ) -> None:
        reply, probes = _typed_names_turn(session_factory, monkeypatch, order_status="outstanding")
        assert reply == "Which one do you mean?\n1. hanlim trading\n2. hanlim hardware"
        assert probes == []
