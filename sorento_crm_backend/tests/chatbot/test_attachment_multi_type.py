"""ATTACHMENT-MULTI: several attachment types in one ask, and no snake_case in the reply.

Plan: `documentation/plans/chatbot/PLAN-attachment-multi-type-2oct.md`.
UAC: `documentation/plans/chatbot/attachment-multi-type-2oct-acceptance-criteria.md`.

Owner (2 Oct 2026): a contact cannot get a photo AND a second document type in one ask, and the
attachment picker shows the raw key `product_attachment`. Measured in the cloud sandbox before
any change, on the real resolver, gate, miss lane and composer (only the MCP client stubbed):

- exact product, "photo and technical specifications for SRTWC286-SH": the gate's
  document-class precision (`gate.py` ~1336) kept ONLY Technical Specifications, because
  the parser's "photo" does not equal the type name "Product Photos" - so the fetch would
  never ask for the photo at all;
- ambiguous product, same ask: every picker line was stamped with the LAST type alone, and
  "has" meant "has any typed file" - a photo-only product read "- has Technical Specifications";
- the miss sentence read "But no photo technical specifications matched these", and both
  the picker header and the found bullet printed raw keys (`product_attachment`,
  `attachment_type`).

Type names are the dev database's own (`attachment_types`, read-only crew query 2 Oct 2026):
"Product Photos" and "Technical Specifications" carry no `code`; product codes are the
SRTWC286 family the #750 suite already seeds. Every chain is seeded fresh on the blank schema.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from app.models.base import set_company_scope
from app.services.chatbot.lanes.business import gate as gate_mod
from tests.chatbot import test_product_attachment_picker_stamp as base

PHOTOS = "Product Photos"
SPECS = "Technical Specifications"
SH = "SRTWC286-SH"
SH200 = "SRTWC286-SH-200"
SHP = "SRTWC286-SH-P"

#: A snake_case word as a customer would see it: two or more lowercase runs joined by "_".
_SNAKE_RE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


def _parser(*, product_raw: str = base.TOKEN) -> dict[str, Any]:
    """"photo and technical specifications for <product>": the #750 parser literal with a
    second `attachment_type` entity, in the order the customer named them."""
    parser = base._parser(product_raw=product_raw)
    parser["entities"].append(
        {
            "raw": "technical specifications",
            "hint": "attachment_type",
            "canonical_code": "technical specifications",
            "confident": True,
            "current_message": True,
        }
    )
    parser["user_goal"] = f"trying to get a photo and technical specifications for {product_raw}"
    return parser


def _text(product_raw: str = base.TOKEN) -> str:
    return f"photo and technical specifications for {product_raw}"


def _seed(
    session_factory: Any,
    *,
    files: dict[str, list[str]],
    spec_code: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Products named in `files`, each holding one file per listed type name."""
    company_id = base._seed_company(session_factory, name="ZZT Attachment Multi Co")
    base._seed_contact_in(session_factory, [company_id])
    type_ids = {
        PHOTOS: base._seed_attachment_type(session_factory, PHOTOS),
        SPECS: base._seed_attachment_type(session_factory, SPECS),
    }
    if spec_code is not None:
        db = session_factory()
        from app.models.resources import AttachmentType

        db.query(AttachmentType).filter(AttachmentType.id == type_ids[SPECS]).update({"code": spec_code})
        db.commit()
    for code, types in files.items():
        product_id = base._seed_product(session_factory, company_id=company_id, code=code)
        for type_name in types:
            base._seed_file_for(
                session_factory,
                product_id=product_id,
                attachment_type_id=type_ids[type_name],
                company_id=company_id,
                filename=f"{code}-{type_name.split()[-1].lower()}.pdf",
            )
    return company_id, type_ids


def _lane(
    session_factory: Any,
    monkeypatch: Any,
    company_id: str,
    *,
    product_raw: str = base.TOKEN,
    describe: bool = False,
):
    """The #750 lane. `describe=True` renders each probe row's "Attachment Type" the way the
    real presenter does (`description or type_name`) instead of the type name alone."""
    db = session_factory()
    set_company_scope(db, frozenset({company_id}))
    calls: list[tuple[str, dict]] = []
    services = base._probe_services(db, monkeypatch, calls=calls)
    if describe:
        import json

        from app.models.resources import AttachmentType
        from app.services.ai_assistant_service import MCPRuntimeClient

        stub = MCPRuntimeClient.call_tool
        descriptions = {t.type_name: t.description for t in db.query(AttachmentType).all()}

        def _described(client: Any, tool_name: str, args: dict[str, Any]) -> str:
            envelope = json.loads(stub(client, tool_name, args))
            for item in envelope.get("items") or []:
                for field in item.get("fields") or []:
                    if field.get("label") == "Attachment Type":
                        field["value"] = descriptions.get(field["value"]) or field["value"]
            return json.dumps(envelope)

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", _described)
    resolved, gate, offer = base._run_lane(
        db, services, parser=_parser(product_raw=product_raw), text=_text(product_raw)
    )
    return resolved, gate, offer, calls


def _type_uuids(gate: dict[str, Any]) -> set[str]:
    return {
        str(e.get("uuid"))
        for e in gate.get("compatible_entities") or []
        if e.get("entity_type") == "attachment_type"
    }


# --------------------------------------------------------------------------- #
# R1: both types stay in scope
# --------------------------------------------------------------------------- #


class TestBothTypesStayInScope:
    def test_an_exact_product_keeps_both_asked_types_in_the_gate_scope(
        self, session_factory, monkeypatch
    ) -> None:
        """The defect turn: the document-class precision dropped Product Photos because
        "photo" is not spelt "Product Photos", so the fetch could only ever send the specs."""
        company_id, type_ids = _seed(session_factory, files={SH: [PHOTOS, SPECS]})
        _resolved, gate, _offer, _calls = _lane(session_factory, monkeypatch, company_id, product_raw=SH)

        assert gate.get("gate_passed") is True, gate.get("gate_reason")
        assert _type_uuids(gate) == {type_ids[PHOTOS], type_ids[SPECS]}, (
            "both asked types must reach the fetch: "
            f"gate_reason={gate.get('gate_reason')!r}"
        )

    def test_one_word_that_matched_several_types_is_still_narrowed(self) -> None:
        """The case the precision rule exists for (container-status S1, exec 11661198): ONE
        customer word, "container status list", resolving to three document types, keeps only
        the one it named. Driven from a literal resolver payload: the rule reads nothing but
        the gate's own inputs."""
        types = [
            ("aaaaaaaa-0000-4000-8000-000000000001", "container_status", "Container Status"),
            ("aaaaaaaa-0000-4000-8000-000000000002", None, "Packing List"),
            ("aaaaaaaa-0000-4000-8000-000000000003", None, "Stock_List"),
        ]
        matches = [
            {
                "entity_type": "attachment_type",
                "canonical_code": code or name,
                "uuid": uuid,
                "match_tier": "word",
                "display": {"code": code, "type_name": name},
            }
            for uuid, code, name in types
        ]
        parser = {
            "entities": [
                {
                    "raw": "container status list",
                    "hint": "attachment_type",
                    "canonical_code": "container status",
                    "confident": True,
                    "current_message": True,
                }
            ],
            "match_mode": "or",
            "domain_hint": "resource_attachment",
            "intent_hint": "check_resource_attachment",
            "message_type": "business_query",
        }
        resolver = {
            "tokens": ["container status list"],
            "resolutions": [{"token": "container status list", "resolved": True, "matches": matches}],
        }
        gate = gate_mod.run_gate({}, parser=parser, resolver=resolver)

        assert _type_uuids(gate) == {types[0][0]}, gate.get("gate_reason")

    def test_a_word_that_named_none_of_its_several_types_still_drops_them(self) -> None:
        """Reviewer S1: the parser split "container status list" into two words. "list"
        matched Packing List and Stock_List and named neither, so - as before - both go;
        only a word with ONE match keeps it unnamed."""
        types = {
            "status": ("aaaaaaaa-0000-4000-8000-000000000001", "container_status", "Container Status"),
            "packing": ("aaaaaaaa-0000-4000-8000-000000000002", None, "Packing List"),
            "stock": ("aaaaaaaa-0000-4000-8000-000000000003", None, "Stock_List"),
        }

        def match(key: str) -> dict[str, Any]:
            uuid, code, name = types[key]
            return {
                "entity_type": "attachment_type",
                "canonical_code": code or name,
                "uuid": uuid,
                "match_tier": "word",
                "display": {"code": code, "type_name": name},
            }

        parser = {
            "entities": [
                {"raw": "container status", "hint": "attachment_type", "canonical_code": "container status",
                 "confident": True, "current_message": True},
                {"raw": "list", "hint": "attachment_type", "canonical_code": "list",
                 "confident": True, "current_message": True},
            ],
            "match_mode": "or",
            "domain_hint": "resource_attachment",
            "intent_hint": "check_resource_attachment",
            "message_type": "business_query",
        }
        resolver = {
            "tokens": ["container status", "list"],
            "resolutions": [
                {"token": "container status", "resolved": True, "matches": [match("status")]},
                {"token": "list", "resolved": True, "matches": [match("packing"), match("stock")]},
            ],
        }
        gate = gate_mod.run_gate({}, parser=parser, resolver=resolver)

        assert _type_uuids(gate) == {types["status"][0]}, gate.get("gate_reason")


# --------------------------------------------------------------------------- #
# R2: the picker stamps every asked type, per product
# --------------------------------------------------------------------------- #


class TestPickerStampsEveryType:
    def test_each_line_names_every_asked_type_as_has_or_no(self, session_factory, monkeypatch) -> None:
        company_id, _ = _seed(
            session_factory,
            files={SH: [PHOTOS], SH200: [SPECS], SHP: [PHOTOS, SPECS]},
        )
        _resolved, gate, offer, calls = _lane(session_factory, monkeypatch, company_id)

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        assert calls and calls[0][0] == base.PROBE_TOOL, calls
        lines = base._picker_lines(offer.get("escalate_message"))
        assert lines[SH].endswith(f"- has {PHOTOS}, no {SPECS}"), lines[SH]
        assert lines[SH200].endswith(f"- no {PHOTOS}, has {SPECS}"), lines[SH200]
        assert lines[SHP].endswith(f"- has {PHOTOS}, has {SPECS}"), lines[SHP]

    def test_nothing_on_file_names_both_types_not_only_the_last(self, session_factory, monkeypatch) -> None:
        company_id, _ = _seed(session_factory, files={SH: [], SH200: []})
        _resolved, gate, offer, _calls = _lane(session_factory, monkeypatch, company_id)

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        lines = base._picker_lines(offer.get("escalate_message"))
        for code in (SH, SH200):
            assert lines[code].endswith(f"- no {PHOTOS}, no {SPECS}"), lines[code]

    def test_rows_that_print_the_type_description_still_stamp_has(self, session_factory, monkeypatch) -> None:
        """Reviewer B1: the real presenter prints `description or type_name` in the
        "Attachment Type" field (`sorento_crm_mcp.presenters._att_type`), and dev's types
        carry descriptions. Seeded with dev's own wording, rows rendered presenter-style."""
        from app.models.resources import AttachmentType

        company_id, type_ids = _seed(session_factory, files={SH: [PHOTOS], SH200: [SPECS]})
        db = session_factory()
        for name, description in (
            (PHOTOS, "Product Photos, Photo, Image, Pictures by Marketing"),
            (SPECS, "Technical Specifications / Spec / Drawing by Marketing"),
        ):
            db.query(AttachmentType).filter(AttachmentType.id == type_ids[name]).update({"description": description})
        db.commit()
        _resolved, gate, offer, _calls = _lane(session_factory, monkeypatch, company_id, describe=True)

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        lines = base._picker_lines(offer.get("escalate_message"))
        assert lines[SH].endswith(f"- has {PHOTOS}, no {SPECS}"), lines[SH]
        assert lines[SH200].endswith(f"- no {PHOTOS}, has {SPECS}"), lines[SH200]

    def test_numbering_and_order_stay_the_gates_own(self, session_factory, monkeypatch) -> None:
        company_id, _ = _seed(session_factory, files={SH: [PHOTOS], SH200: [SPECS]})
        _resolved, gate, offer, _calls = _lane(session_factory, monkeypatch, company_id)

        before = (gate.get("gate_clarification") or "").split("\n")
        after = (offer.get("escalate_message") or "").split("\n")
        assert len(after) == len(before), (before, after)
        for original, rendered in zip(before, after):
            assert rendered.startswith(original), (original, rendered)

    def test_the_stamp_noun_is_the_type_name_never_a_slug_code(self, session_factory, monkeypatch) -> None:
        """Twelve dev types carry a slug `code` (`combo_image`, `packing_list`, ...); the
        resolver's `canonical_code` is `code or type_name`, so a product type that gains one
        would otherwise stamp "- has tech_spec" at the customer."""
        company_id, _ = _seed(session_factory, files={SH: [PHOTOS, SPECS], SH200: []}, spec_code="tech_spec")
        _resolved, gate, offer, _calls = _lane(session_factory, monkeypatch, company_id)

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        message = offer.get("escalate_message") or ""
        lines = base._picker_lines(message)
        assert lines[SH].endswith(f"- has {PHOTOS}, has {SPECS}"), lines[SH]
        assert "tech_spec" not in message, message


# --------------------------------------------------------------------------- #
# R4 / R5: the miss sentence and the found bullets
# --------------------------------------------------------------------------- #


class TestMissWording:
    def test_the_miss_sentence_joins_the_asked_types_with_or(self, session_factory, monkeypatch) -> None:
        company_id, _ = _seed(session_factory, files={SH: []})
        _resolved, gate, offer, _calls = _lane(session_factory, monkeypatch, company_id, product_raw=SH)

        message = offer.get("escalate_message") or ""
        assert gate.get("gate_passed") is True, gate.get("gate_reason")
        # Owner ruling 2 Oct 2026 (test list review): the OFFICIAL type names, never the
        # customer's raw words.
        assert f"But no {PHOTOS} or {SPECS} matched these." in message, message

    def test_the_found_bullets_name_both_types_under_a_human_label(
        self, session_factory, monkeypatch
    ) -> None:
        company_id, _ = _seed(session_factory, files={SH: []})
        _resolved, _gate, offer, _calls = _lane(session_factory, monkeypatch, company_id, product_raw=SH)

        message = offer.get("escalate_message") or ""
        # Cloud browser pass 3 Oct: in the order the customer named them ("photo and
        # technical specifications"), not the resolver's.
        assert f"• attachment type: {PHOTOS}, {SPECS}" in message, message


class TestNoSnakeCase:
    def test_the_attachment_picker_carries_no_snake_case_key(self, session_factory, monkeypatch) -> None:
        company_id, _ = _seed(session_factory, files={SH: [PHOTOS], SH200: []})
        _resolved, gate, offer, _calls = _lane(session_factory, monkeypatch, company_id)

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        message = offer.get("escalate_message") or ""
        assert "Please choose:" in message, message
        assert not _SNAKE_RE.findall(message), message

    def test_the_exact_product_miss_carries_no_snake_case_key(self, session_factory, monkeypatch) -> None:
        company_id, _ = _seed(session_factory, files={SH: []})
        _resolved, _gate, offer, _calls = _lane(session_factory, monkeypatch, company_id, product_raw=SH)

        message = offer.get("escalate_message") or ""
        assert not _SNAKE_RE.findall(message), message


# --------------------------------------------------------------------------- #
# R3: a found product missing an asked type says so (owner Q2 (a))
# --------------------------------------------------------------------------- #


def _attachment_envelope(
    product_codes: list[str], rows: list[tuple[str, str]], asked: list[str], *, labels: list[dict] | None = None
) -> dict:
    figures = [
        {
            "fields": [
                {"label": "Product Code", "value": code},
                {"label": "Attachment Type", "value": type_name},
                {"label": "File Name", "value": f"{code}.pdf"},
            ]
        }
        for code, type_name in rows
    ]
    return {
        "domain": "product_attachment",
        "denied": False,
        "entities": [*product_codes, *asked],
        "product_codes": list(product_codes),
        "attachment_types": labels if labels is not None else [{"name": n, "keys": [n]} for n in asked],
        "figures": figures,
        "files": [{"url": f"https://example.test/{c}.pdf", "filename": f"{c}.pdf"} for c, _ in rows],
        "miss": [] if rows else list(product_codes),
        "has_result": bool(rows),
        "tool_has_result": bool(rows),
        "unresolved": [],
        "error": None,
        "lane_text": "I have attached the file(s) below." if rows else "No matching results found.",
    }


def _compose_text(env: dict) -> str:
    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.policy import Policy
    from app.services.chatbot.turn.state import Focus, Profile, State

    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    row = {**_domain_row("product_attachment", narrowing={"product": "must_narrow_one"}), "label": "product attachments"}
    policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)
    return compose([env], state, policy, ctx=None).text


class TestFoundAnswerNamesTheGap:
    def test_a_product_missing_one_asked_type_gets_the_files_it_has_and_one_line(self) -> None:
        text = _compose_text(_attachment_envelope([SH], [(SH, PHOTOS)], [PHOTOS, SPECS]))

        assert "I have attached the file(s) below." in text, text
        assert f"{SH} has no {SPECS}." in text, text
        assert "escalate" not in text.lower(), text

    def test_every_asked_type_on_file_adds_no_line(self) -> None:
        text = _compose_text(_attachment_envelope([SH], [(SH, PHOTOS), (SH, SPECS)], [PHOTOS, SPECS]))

        assert "has no" not in text, text

    def test_a_product_with_no_file_at_all_names_both_types_on_one_line(self) -> None:
        text = _compose_text(
            _attachment_envelope([SH, SH200], [(SH, PHOTOS), (SH, SPECS)], [PHOTOS, SPECS])
        )

        assert f"{SH200} has no {PHOTOS} or {SPECS}." in text, text
        assert f"{SH} has no" not in text, text

    def test_the_gap_line_keeps_the_footer_on_its_own_paragraph(self) -> None:
        """Cloud browser pass 3 Oct: the line landed flush against "_Data last updated"."""
        env = _attachment_envelope([SH], [(SH, PHOTOS)], [PHOTOS, SPECS])
        env["lane_text"] += "\n\n_Data last updated: 03/10/2026 16:24:40_"
        text = _compose_text(env)

        assert f"\n\n{SH} has no {SPECS}.\n\n_Data last updated" in text, repr(text)

    def test_a_type_without_a_label_names_no_gap_at_all(self) -> None:
        """`turn_runtime.attachment_type_labels` could not read a type: no line, never a
        false or slug-named one."""
        text = _compose_text(_attachment_envelope([SH], [(SH, PHOTOS)], [PHOTOS, "tech_spec"], labels=[]))

        assert "has no" not in text and "tech_spec" not in text, text

    def test_the_label_lookup_reads_name_and_description_and_fails_closed(self, session_factory) -> None:
        """`turn_runtime.attachment_type_labels`: name + description per uuid; an unknown
        uuid, a non-uuid id or no type entity at all gives `[]` (no gap line)."""
        from app.models.resources import AttachmentType
        from app.services.chatbot.turn_runtime import attachment_type_labels

        _company_id, type_ids = _seed(session_factory, files={})
        db = session_factory()
        db.query(AttachmentType).filter(AttachmentType.id == type_ids[PHOTOS]).update(
            {"description": "Product Photos, Photo, Image, Pictures by Marketing"}
        )
        db.commit()
        entity = lambda uuid: {"entity_type": "attachment_type", "uuid": uuid}  # noqa: E731

        labels = attachment_type_labels(db, [entity(type_ids[PHOTOS]), {"entity_type": "product", "uuid": "x"}])
        assert labels == [
            {"name": PHOTOS, "keys": [PHOTOS, "Product Photos, Photo, Image, Pictures by Marketing"]}
        ], labels
        assert attachment_type_labels(db, [entity("aaaaaaaa-0000-4000-8000-00000000dead")]) == []
        assert attachment_type_labels(db, [entity("not-a-uuid")]) == []
        assert attachment_type_labels(db, [{"entity_type": "product", "uuid": type_ids[PHOTOS]}]) == []

    def test_a_row_printing_the_types_description_is_not_a_gap(self) -> None:
        """Reviewer B1: the presenter prints `description or type_name`, and dev's real
        Product Photos row describes itself "Product Photos, Photo, Image, Pictures by
        Marketing" - a photo that IS on file must not read "has no Product Photos"."""
        description = "Product Photos, Photo, Image, Pictures by Marketing"
        labels = [{"name": PHOTOS, "keys": [PHOTOS, description]}, {"name": SPECS, "keys": [SPECS]}]
        text = _compose_text(_attachment_envelope([SH], [(SH, description)], [PHOTOS, SPECS], labels=labels))

        assert f"{SH} has no {PHOTOS}" not in text, text
        assert f"{SH} has no {SPECS}." in text, text


# --------------------------------------------------------------------------- #
# Q5 (a): every customer-facing snake_case leak the sweep found, one test each
# --------------------------------------------------------------------------- #


def _product(code: str, uuid: str) -> dict[str, Any]:
    return {
        "entity_type": "product",
        "canonical_code": code,
        "uuid": uuid,
        "match_tier": "prefix",
        "company_name": "ZZT Co",
    }


class TestEveryLeakIsHuman:
    def test_the_dropped_filter_line_names_the_kind_in_words(self) -> None:
        """gate.py "Couldn't find: "x" (customer_order)." above a picker."""
        parser = {
            "domain_hint": "incoming",
            "intent_hint": "check_incoming",
            "match_mode": "or",
            "message_type": "business_query",
            "entities": [
                {"raw": "srtwc286", "hint": "product", "confident": True, "current_message": True},
                {"raw": "zz999", "hint": "customer_order", "confident": True, "current_message": True},
            ],
        }
        resolver = {
            "tokens": ["srtwc286", "zz999"],
            "unresolved_tokens": ["zz999"],
            "resolutions": [
                {
                    "token": "srtwc286",
                    "resolved": False,
                    "ambiguous": True,
                    "matches": [
                        _product(SH, "11111111-1111-4111-8111-111111111111"),
                        _product(SH200, "22222222-2222-4222-8222-222222222222"),
                    ],
                },
                {"token": "zz999", "resolved": False, "matches": []},
            ],
        }
        text = gate_mod.run_gate({}, parser=parser, resolver=resolver).get("gate_clarification") or ""

        assert 'Couldn\'t find: "zz999" (customer order).' in text, text
        assert text.split("\n\n", 1)[-1].startswith("Which product do you mean? Please choose:"), text
        assert not _SNAKE_RE.findall(text), text

    def test_the_could_not_find_sentence_names_the_kind_in_words(self) -> None:
        """answer.py `requested`: "Could not find incoming for inbound_shipment C123"."""
        from app.services.chatbot.lanes.business import answer as answer_mod

        out = answer_mod.not_found_error_message(
            {},
            parser={
                "domain_hint": "incoming",
                "routing": {"suggested_team": "purchasing"},
                "entities": [{"raw": "C123", "hint": "inbound_shipment", "confident": True}],
            },
            resolved={
                "tokens": ["C123"],
                "unresolved_tokens": [],
                "by_entity_type": {"inbound_shipment": [{"uuid": "u1", "canonical_code": "C123"}]},
            },
            gate={"gate_passed": True, "gate_reason": "ok"},
        )
        text = out.get("escalate_message") or ""

        assert "Could not find incoming for inbound shipment C123." in text, text
        assert not _SNAKE_RE.findall(text), text

    def test_the_vague_token_clarify_names_kinds_in_words(self) -> None:
        """answer.py "I understood customer_order DO123 ... is that a customer_order, ...?"."""
        from app.services.chatbot.lanes.business import answer as answer_mod

        out = answer_mod.not_found_error_message(
            {},
            parser={
                "domain_hint": "order",
                "routing": {"suggested_team": "customer_service"},
                "entities": [
                    {"raw": "zzq", "hint": "product", "confident": False},
                    {"raw": "DO123", "hint": "customer_order", "confident": True},
                ],
            },
            resolved={"unresolved_tokens": ["zzq"], "resolutions": [{"token": "zzq", "matches": []}]},
            gate={
                "gate_passed": False,
                "gate_reason": "x",
                "gate_debug": {"allowed_lookup": ["customer_order", "inbound_shipment", "product"]},
            },
        )
        text = out.get("escalate_message") or ""

        assert "I understood customer order DO123" in text, text
        assert "is that a customer order, inbound shipment, or product?" in text, text
        assert not _SNAKE_RE.findall(text), text

    def test_the_needs_a_filter_reply_names_the_domain_in_words(self) -> None:
        """answer.py "That would search every resource_attachment we have"."""
        from app.services.chatbot.lanes.business import answer as answer_mod

        out = answer_mod.not_found_error_message(
            {},
            parser={"domain_hint": "resource_attachment", "entities": [], "routing": {"suggested_team": "marketing_product"}},
            resolved={},
            gate={
                "gate_passed": False,
                "gate_reason": "no entities and 'resource_attachment' requires a scoping entity",
                "gate_debug": {"allowed_lookup": ["attachment_type", "attachment"]},
            },
        )
        text = out.get("escalate_message") or ""

        assert "That would search every resource attachment we have" in text, text
        assert not _SNAKE_RE.findall(text), text

    def test_the_did_you_mean_label_falls_back_to_the_kind_in_words(self) -> None:
        """answer.py D1 multi-token: a candidate with no entity_type printed the parser's
        raw hint ("customer_order")."""
        from app.services.chatbot.lanes.business import answer as answer_mod

        parser = {
            "domain_hint": "order",
            "message_type": "business_query",
            "routing": {"suggested_team": "customer_service"},
            "entities": [
                {"raw": "do12x", "hint": "customer_order", "confident": True},
                {"raw": "do34y", "hint": "customer_order", "confident": True},
            ],
        }
        resolved = {
            "tokens": ["do12x", "do34y"],
            "unresolved_tokens": ["do12x", "do34y"],
            "resolutions": [
                {"token": "do12x", "resolved": False, "matches": [], "alternatives": [{"canonical_code": "DO12X1", "uuid": None}]},
                {"token": "do34y", "resolved": False, "matches": [], "alternatives": [{"canonical_code": "DO34Y1", "uuid": None}]},
            ],
        }
        gate = {"gate_passed": True, "gate_reason": "ok"}
        miss = answer_mod.not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = answer_mod.build_suggest_offer(miss, parser=parser, resolved=resolved, gate=gate).get(
            "suggest_response"
        ) or ""

        # MULTI-CODE-DYM Q5 (owner, 4 Oct 2026): each code reads as its single-code reply.
        assert 'Couldn\'t find "do12x" (customer order). Did you mean:' in text, text
        assert not _SNAKE_RE.findall(text), text

    def test_the_kind_pick_option_names_the_kind_in_words(self) -> None:
        """turn/reconcile.py: "water closet (attachment_type)" (owner transcript)."""
        from app.services.chatbot.turn.reconcile import apply_reconciliation

        from tests.chatbot._turn_helpers import entity

        result = apply_reconciliation(
            [entity("water closet", hint="category")],
            {"water closet": {"promotion": 1, "attachment_type": 1}},
        )

        labels = [o.get("label") for o in result.kind_pick_options or []]
        assert "water closet (attachment type)" in labels, labels
        assert all(not _SNAKE_RE.findall(label or "") for label in labels), labels
        # The label is display only; the kind the pick re-types to stays the key.
        assert {o.get("entity_type") for o in result.kind_pick_options} == {"promotion", "attachment_type"}

    def test_the_not_allowed_reply_names_the_team_in_words(self) -> None:
        """canned.py: "Sorry, you are not allowed to access incoming_stock_enquiries"
        (prod samples, 1 turn on dev)."""
        from app.services.chatbot.copy import CannedCopy
        from app.services.chatbot.lanes.canned import access_denied_text

        ctx = {"parse": {"output": {"routing": {"suggested_agent": "incoming_stock_enquiries"}}}}
        text = access_denied_text(None, ctx, CannedCopy(templates={}))

        assert text == "Sorry, you are not allowed to access incoming stock enquiries", text

    def test_the_access_level_ask_names_a_domain_the_label_map_lacked(self) -> None:
        """answer.py DOMAIN_LABELS: `purchase_cost` fell back to the raw key."""
        from app.services.chatbot.lanes.business import answer as answer_mod

        out = answer_mod.access_level_choice_message({"name": []}, parser={"domain_hint": "purchase_cost"})
        text = out.get("escalate_message") or ""

        assert "You have no access levels configured to get last purchase cost." in text, text


# --------------------------------------------------------------------------- #
# The pick: "1" over a two-type roster answers both types (engine end to end)
# --------------------------------------------------------------------------- #


class TestThePickKeepsBothTypes:
    """`engine.run_turn` twice, real resolver: the roster turn, then a numbered pick of the
    photo-only member. The pick's fetch must ask for BOTH types and the reply must send the
    photo and name the missing specs (R1 + R3 through the whole turn, not just the lane)."""

    @pytest.mark.parametrize(
        ("second", "second_raw", "second_code"),
        # The certification case is the dev parser's own shape (crew trace 2 Oct, turn
        # abfea54d): "certification" came back canonical_code "certificate", which equals no
        # type name, and the photo's "photo" likewise.
        [(SPECS, "technical specifications", "technical specifications"), ("Certification", "certification", "certificate")],
        ids=["specs", "certification"],
    )
    def test_a_numbered_pick_fetches_both_types_and_names_the_gap(
        self, session_factory, monkeypatch, second: str, second_raw: str, second_code: str
    ) -> None:
        """Tester re-run 2 Oct, finding 2: "photo and certification for CB11" then "3"
        (CB110-R) sent the photo WITHOUT "CB110-R has no Certification." - the
        certification case is the one that failed, so both are pinned. Finding 3 rides
        along: the roster stamps the type_name "Certification", never "certificate"."""
        import json

        from app.services.company_scope import DEFAULT_COMPANY_ID
        from tests.chatbot.test_engine import _parser_output
        from tests.chatbot.test_outstanding_lane import _present_response, _session_of
        from tests.chatbot.test_rearch_r5_production_decides import (
            _family_base,
            _mcp_double,
            _mcp_probe_for,
            _run_turn_real,
            _run_turn_with_mcp_call,
            _seed_contact_and_get,
        )
        from tests.chatbot.test_rearch_r7_live_parity_replay import _seed_real_attachment_type

        _seed_contact_and_get(session_factory)
        family = _family_base("ZZTMULTI")
        both_code, photo_code = f"{family}A", f"{family}B"
        both_id = base._seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=both_code)
        photo_id = base._seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=photo_code)
        photos_id = _seed_real_attachment_type(session_factory, PHOTOS)
        specs_id = _seed_real_attachment_type(session_factory, second)
        for product_id, type_id, name in (
            (both_id, photos_id, f"{both_code}.jpg"),
            (both_id, specs_id, f"{both_code}.pdf"),
            (photo_id, photos_id, f"{photo_code}.jpg"),
        ):
            base._seed_file_for(
                session_factory, product_id=product_id, attachment_type_id=type_id,
                company_id=DEFAULT_COMPANY_ID, filename=name,
            )
        # The backend's own row shape: the type is an object, and the presenter prints its
        # description (dev's real wording, `_REAL_ATTACHMENT_TYPE_DESCRIPTIONS`).
        from tests.chatbot.test_rearch_r7_live_parity_replay import _REAL_ATTACHMENT_TYPE_DESCRIPTIONS as descriptions

        rows = {
            both_code: [(PHOTOS, f"{both_code}.jpg"), (second, f"{both_code}.pdf")],
            photo_code: [(PHOTOS, f"{photo_code}.jpg")],
        }

        def _raw_rows(codes: list[str]) -> list[dict[str, Any]]:
            return [
                {
                    "product": {"product_code": code},
                    "attachment": {
                        "attachment_type": {"type_name": type_name, "description": descriptions[type_name]},
                        "original_filename": filename,
                        "file_path": f"https://example.test/{filename}",
                    },
                    "company_name": "Sorento",
                }
                for code in codes
                for type_name, filename in rows[code]
            ]

        probe = _mcp_probe_for({"crm_master_product_attachments_list": _raw_rows([both_code, photo_code])})
        entities = [
            {"raw": family, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            {"raw": "photo", "hint": "attachment_type", "canonical_code": "photo", "current_message": True, "confident": True},
            {
                "raw": second_raw,
                "hint": "attachment_type",
                "canonical_code": second_code,
                "current_message": True,
                "confident": True,
            },
        ]
        qf1 = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=entities,
            routing={"suggested_team": "marketing_product", "suggested_agent": None, "team_source": None},
        )
        result1, _c1 = _run_turn_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"photo and {second_raw} for {family}",
            msg_id="zzt-multi-roster-1", mcp_response={"data": []}, answer_mcp_probe=probe,
        )
        reply1 = (result1.reply or {}).get("text") or ""
        assert "Which product do you mean? Please choose:" in reply1, reply1
        assert f"{photo_code} - has {PHOTOS}, no {second}" in reply1, reply1
        assert "certificate" not in reply1, reply1
        options = (_session_of(session_factory).get("open_question") or {}).get("options") or []
        position = next(
            (o.get("position") for o in options if str(o.get("code") or "").upper() == photo_code.upper()),
            None,
        )
        assert position is not None, options

        calls: list[tuple[str, dict[str, Any]]] = []

        def _answer(name: str, args: dict[str, Any]) -> str:
            calls.append((name, dict(args)))
            if name != "crm_master_product_attachments_list":
                return json.dumps({"data": []})
            return _present_response()(name, json.dumps({"data": _raw_rows([photo_code])}))

        # The dev parser's own read of the bare pick (crew trace 2 Oct, turn 29a88795): the
        # picked product echoed as the turn's entity, `entity_op: reuse`, no document word.
        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None,
            entities=[
                {"raw": photo_code, "hint": "product", "canonical_code": photo_code,
                 "current_message": True, "confident": True},
            ],
            entity_op="reuse",
            reference_positions=[position],
        )
        result2 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf2, text_body=str(position),
            msg_id="zzt-multi-roster-2", mcp_call=_mcp_double(other=_answer)[0], answer_mcp_probe=probe,
        )
        reply2 = (result2.reply or {}).get("text") or ""
        fetches = [args for name, args in calls if name == "crm_master_product_attachments_list"]
        assert fetches, calls
        assert set(map(str, fetches[-1].get("attachment_type_ids") or [])) == {photos_id, specs_id}, fetches
        assert f"{photo_code} has no {second}." in reply2, reply2
        assert "escalate" not in reply2.lower(), reply2


# --------------------------------------------------------------------------- #
# Owner ruling 2 Oct 2026 (hand test "photo and cert for strwc286"): a product that
# is not found and has no did-you-mean candidate is named alone, offer kept
# --------------------------------------------------------------------------- #


class TestUnfoundProductWithNoCandidates:
    def _lane_with_no_candidates(self, session_factory, monkeypatch, *, two_types: bool):
        """The dev trace's own shape (turn 641, 2 Oct): the product token matched nothing
        and the resolver had no alternatives for it (best trigram 0.25 < the 0.30 floor);
        the document words resolved. A literal payload, like #750's AC-3, because the blank
        schema has no pg_trgm."""
        import copy

        company_id = base._seed_company(session_factory, name="ZZT Unfound Co")
        base._seed_contact_in(session_factory, [company_id])
        base._seed_product(session_factory, company_id=company_id, code=SH)
        photos = base._seed_attachment_type(session_factory, PHOTOS)
        cert = base._seed_attachment_type(session_factory, "Certification", is_certificate=True)
        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        services = base._probe_services(db, monkeypatch, calls=[])
        parser = base._parser(product_raw="strwc286")
        resolved = base.TestD1Surfaces()._resolver_payload(token="strwc286", products=[], type_uuid=photos)
        if two_types:
            parser["entities"].append(
                {"raw": "cert", "hint": "attachment_type", "canonical_code": "certificate",
                 "confident": True, "current_message": True}
            )
            extra = copy.deepcopy(resolved["resolutions"][1])
            extra["token"] = "cert"
            extra["matches"][0].update(
                {"canonical_code": "Certification", "uuid": cert, "display": {"type_name": "Certification"}}
            )
            resolved["resolutions"].append(extra)
            resolved["tokens"].append("cert")
        _resolved, gate, offer = base._run_lane(
            db, services, parser=parser, text="photo and cert for strwc286", resolved=resolved
        )
        return gate, offer

    def test_two_types_reply_names_only_the_unfound_product_and_keeps_the_offer(
        self, session_factory, monkeypatch
    ) -> None:
        gate, offer = self._lane_with_no_candidates(session_factory, monkeypatch, two_types=True)

        message = offer.get("escalate_message") or ""
        assert gate.get("gate_passed") is False, gate.get("gate_reason")
        assert offer.get("suggest_offer") is not True, offer.get("suggest_response")
        assert message == (
            'Couldn\'t find "strwc286" (product). '
            "Would you like me to escalate to marketing product team?"
        ), message

    def test_an_unplaced_product_row_on_the_gate_still_counts_as_unfound(
        self, session_factory, monkeypatch
    ) -> None:
        """Cloud browser pass 3 Oct on d193f450: through the real engine the gate's
        `compatible_entities` also carries the unplaced token as a uuid-less product row
        (`{"raw": "strwc286", "uuid": null, "entity_type": "product"}`), and the guard read
        that as a resolved product, so the full breakdown still printed. A product with no
        uuid is not resolved."""
        from app.services.chatbot.lanes.business import answer as answer_mod

        gate, _offer = self._lane_with_no_candidates(session_factory, monkeypatch, two_types=True)
        engine_gate = {
            **gate,
            "compatible_entities": [
                *(gate.get("compatible_entities") or []),
                {"raw": "strwc286", "code": None, "uuid": None, "entity_type": "product"},
            ],
        }
        parser = base._parser(product_raw="strwc286")
        parser["entities"].append(
            {"raw": "cert", "hint": "attachment_type", "canonical_code": "certificate",
             "confident": True, "current_message": True}
        )
        resolved = {"unresolved_tokens": ["strwc286"], "resolutions": [
            {"token": "strwc286", "resolved": False, "matches": [], "alternatives": []},
        ]}
        message = answer_mod.not_found_error_message(
            {}, parser=parser, resolved=resolved, gate=engine_gate
        ).get("escalate_message") or ""

        assert message == (
            'Couldn\'t find "strwc286" (product). '
            "Would you like me to escalate to marketing product team?"
        ), message

    def test_one_type_reply_is_the_same_shape(self, session_factory, monkeypatch) -> None:
        _gate, offer = self._lane_with_no_candidates(session_factory, monkeypatch, two_types=False)

        message = offer.get("escalate_message") or ""
        assert message.startswith('Couldn\'t find "strwc286" (product).'), message
        assert "matched these" not in message and "Here's what you want" not in message, message
        assert "Would you like me to escalate to" in message, message
