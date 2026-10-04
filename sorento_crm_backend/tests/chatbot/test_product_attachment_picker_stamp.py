"""Issue #750: the `product_attachment` stamps ("- has Product Photos" / "- no Product
Photos") never render, on any of the three did-you-mean surfaces.

Plan: `documentation/plans/chatbot/PLAN-product-attachment-picker-stamp.md`.
UAC: `documentation/plans/chatbot/product-attachment-picker-stamp-acceptance-criteria.md`.

Owner console turn "photo for srtwc286" (local repro `chatbot.turns`
`bfe8e386-db90-46df-ae6b-35c15d636362`, 8 Sep 2026, `is_test`, contact 900000008) replied

    product_attachment search needs to be more specific. Multiple matches found. Please choose:
    1. SRTWC286-SH-200
    ...
    10. SRTWC286-SH

with no per-line stamp, so picking 1 dead-ends on "no Product Photos matched these" while
picking 4 (SRTWC286-SH) returns the file. The incoming and customer pickers carry the stamp;
this one does not.

**ROOT CAUSE, measured in process against the 7 Sep prod copy (`sorento_ai_automation_0907`),
read-only, with the contact's own company scope stamped on the session
(`engine._scoped_factory`'s own rule).** Nothing upstream of the render skips: the gate is
`require_specific` with 10 uuid-carrying options, `dym_transform` plans `probe_lane: "picker"`
with `probe_needed: True` and `probe_skip_reason: None`, the real probe answers with the
SRTWC286-SH row, and `dym_annotate` returns `ok: True`, `key_mode: "uuid"`,
`dym_available_codes: ["0d0ed752-fd6f-4759-ad8f-0b40e0cbc601"]`. The break is at the render:
`build_suggest_offer` keys each line back by product CODE while the annotator keyed the probe
by product UUID for this domain (`miss_suggest._annotate` sources `meta["probed"]` from
`dym_candidate_uuids` whenever `probe_uuid_keyed` is true, and `_dym_plan` sets that flag for
`product_attachment` on the `d1` and `picker` lanes), so 10 of 10 lines take the
"unprobed renders BARE" branch. `probe_uuid_keyed` and the picker surface landed in the SAME
commit (981860f7e, #674), an ancestor of prod, so the surface has never once stamped for this
domain, anywhere. Second defect behind the first: `DOMAIN_PROBE["product_attachment"].noun` is
`None`, so the render falls back to `attachment_noun()`, the customer's own raw token, and even
a key-mode fix alone would read "- has photo".

**How these tests run.** Every chain is seeded fresh on the blank Postgres schema
(`tests/chatbot/conftest.py::session_factory`), so CI's empty database is enough, and the REAL
resolver, gate, `dym_transform`, `dym_annotate`, `run_miss_lane` and `build_suggest_offer` are
driven: only `MCPRuntimeClient.call_tool` is monkeypatched, and it answers from the SEEDED rows
narrowed by the `product_ids` / `attachment_type_ids` the real `entity_ids_transformer` built,
in the envelope shape the real MCP answered this turn with (measured 8 Sep 2026:
`result_type: product_attachments`, `items[].fields` labelled "Company" / "Product Code" /
"Attachment Type" / "File Name"). No LLM, no network, no n8n: `spec_fallback` and
`understand_phrase` are forced off on the resolver call
(`test_engine_company_scope._real_resolve_entity`).

The has / no split asserted here was verified with SQL against the prod copy first
(`SRTWC286-SH` carries `SRTWC286-SH.jpg` and `SRTWC286_SH_2.jpg` of type Product Photos;
`SRTWC286-SH-200` carries only Certification and Technical Specifications), and each seed
reproduces exactly that split.

AC-3's resolver payload is a LITERAL rather than a real resolve, and that is forced: the D1
surface only fires when the token produced NO `matches` and the resolver's trigram
`alternatives` instead (`entity_resolver.TokenResolution.alternatives`), and the blank schema's
`search_path` excludes `public`, where `pg_trgm` lives, so `similarity()` cannot resolve and no
fuzzy neighbour can be produced here. The payload is built FROM the seeded rows (real uuids,
real codes) and handed to the REAL gate, which classifies it exactly as it classifies a
production one.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.product import Product, ProductAttachment
from app.models.resources import Attachment, AttachmentType
from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import gate as gate_mod
from app.services.chatbot.lanes.business import miss_suggest as miss_mod
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.lanes.business.services import AnswerServices, production_answer_services
from tests.chatbot.test_engine_company_scope import (
    SPACE_ID,
    _real_resolve_entity,
    _seed_company,
    _seed_contact,
    _seed_product,
    _seed_workspace,
)

CONTACT_ID = "ZZT-contact-picker-stamp"
TOKEN = "srtwc286"
HAS_PHOTO_CODE = "SRTWC286-SH"
NO_PHOTO_CODE = "SRTWC286-SH-200"
PHOTO_TYPE_NAME = "Product Photos"
CERT_TYPE_NAME = "Certification"
PROBE_TOOL = "crm_master_product_attachments_list"

# The picker's own numbered-line grammar, the one `build_suggest_offer` matches on.
_LINE_RE = re.compile(r"^\s*[0-9]+\.\s+(.+?)\s*\Z")


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def _seed_attachment_type(
    session_factory: Any,
    type_name: str,
    *,
    description: str | None = None,
    is_certificate: bool = False,
) -> str:
    """An admin attachment-type row. `code` is NULL exactly as production's "Product Photos"
    is (it predates `021_add_attachment_type_code_and_complaint_document.py`).

    `description` defaults to a synthetic ZZT string; pass the real row's wording (tester 38,
    20 Sep 2026 - `test_rearch_r7_live_parity_replay.py`'s `_REAL_ATTACHMENT_TYPE_DESCRIPTIONS`)
    when a test needs a token to reach the type the way it does against production data, since
    the resolver's `_prefix_probe_attachment_type` matches on `description` too.
    """
    db = session_factory()
    attachment_type = AttachmentType(
        type_name=type_name,
        code=None,
        description=description if description is not None else f"{type_name}, seeded by ZZT",
        allowed_extensions="jpg,jpeg,png,webp,gif,pdf",
        max_file_size_mb=10,
        is_certificate=is_certificate,
    )
    db.add(attachment_type)
    db.commit()
    return attachment_type.id


def _seed_file_for(
    session_factory: Any,
    *,
    product_id: str,
    attachment_type_id: str,
    company_id: str,
    filename: str,
) -> str:
    """The PRODUCTION linkage: a `product_attachments` row pointing at an `Attachment` whose
    own `entity_type` is NULL (measured in `test_pass5_item1_photo_attachment_alias.py` - zero
    production rows carry `attachments.entity_type = 'product'`)."""
    db = session_factory()
    attachment = Attachment(
        attachment_type_id=attachment_type_id,
        original_filename=filename,
        stored_filename=f"zzt-{filename}",
        file_path=f"https://example.test/zzt-{filename}",
        entity_type=None,
        entity_id=None,
        company_id=company_id,
    )
    db.add(attachment)
    db.flush()
    db.add(
        ProductAttachment(
            product_id=product_id, attachment_id=attachment.id, company_id=company_id
        )
    )
    db.commit()
    return attachment.id


# --------------------------------------------------------------------------- #
# The turn: parser literal, ctx, and the probe seam
# --------------------------------------------------------------------------- #


def _parser(*, product_raw: str = TOKEN, type_raw: str = "photo", type_code: Any = "photo") -> dict[str, Any]:
    """`chatbot.turns bfe8e386-db90-46df-ae6b-35c15d636362`'s parser output, verbatim in
    shape: the two entities it read from "photo for srtwc286". A literal so no LLM runs."""
    return {
        "entities": [
            {
                "raw": product_raw,
                "hint": "product",
                "canonical_code": None,
                "confident": True,
                "current_message": True,
            },
            {
                "raw": type_raw,
                "hint": "attachment_type",
                "canonical_code": type_code,
                "confident": True,
                "current_message": True,
            },
        ],
        "match_mode": "and",
        "domain_hint": "product_attachment",
        "intent_hint": "check_product_attachment",
        "message_type": "business_query",
        "user_goal": f"trying to get a {type_raw} for {product_raw}",
        "access_levels": [],
        "requested_attributes": [],
        "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
    }


def _ctx(parser: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "parse": {"output": parser},
        "session": {},
        "contact": {"id": CONTACT_ID},
        "text": {"message": {"message": {"text": text, "type": "text"}}},
    }


def _probe_services(
    db: Any,
    monkeypatch: Any,
    *,
    calls: list[tuple[str, dict]],
    with_company: bool = True,
    fail: bool = False,
) -> AnswerServices:
    """The PRODUCTION probe bundle, with only `MCPRuntimeClient.call_tool` replaced.

    `with_company=False` reproduces a presenter row that carried no Company field, which is
    what makes the annotator's (code, company) join ambiguous for a twin code (F1). `fail`
    reproduces a probe that raised, which `run_miss_lane` catches by design.
    """
    from app.services.ai_assistant_service import MCPRuntimeClient

    def _fake_call_tool(_client: Any, tool_name: str, args: dict[str, Any]) -> str:
        calls.append((tool_name, dict(args)))
        if fail:
            raise RuntimeError("ZZT: the MCP probe did not run")
        rows = (
            db.query(
                Product.product_code,
                Company.name,
                AttachmentType.type_name,
                Attachment.original_filename,
                Attachment.attachment_type_id,
            )
            .join(ProductAttachment, ProductAttachment.product_id == Product.id)
            .join(Attachment, Attachment.id == ProductAttachment.attachment_id)
            .join(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
            .join(Company, Company.id == Product.company_id)
            .filter(Product.id.in_(list(args.get("product_ids") or [])))
            .all()
        )
        # The tool narrows by whatever the transformer sent. A certificate-scoped turn sends
        # `certificate_ids` and NO `attachment_type_ids`, so an unconditional type filter
        # would answer an empty page for a question the real tool answers.
        type_ids = list(args.get("attachment_type_ids") or [])
        if type_ids:
            rows = [row for row in rows if str(row[4]) in {str(t) for t in type_ids}]
        items = []
        for code, company_name, type_name, filename, _type_id in rows:
            fields = [{"label": "Product Code", "value": code}]
            if with_company:
                fields.insert(0, {"label": "Company", "value": company_name})
            fields.append({"label": "Attachment Type", "value": type_name})
            fields.append({"label": "File Name", "value": filename})
            items.append({"title": code, "fields": fields})
        return json.dumps(
            {
                "result_type": "product_attachments",
                "intro": "I have attached the file(s) below.",
                "items": items,
                "has_result": len(items) > 0,
            }
        )

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", _fake_call_tool)
    return production_answer_services(db)


def _run_lane(
    db: Any, services: AnswerServices, *, parser: dict[str, Any], text: str, resolved: Any = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """resolve-entity -> disallowed-entity-gate -> not-found-error-message -> the miss lane.

    `resolved` overrides the resolve call for AC-3 only (see the module docstring).
    """
    if resolved is None:
        resolved = _real_resolve_entity(db)(
            resolve_gate.resolve_entity_body(_ctx(parser, text), dry_run=True)
        )
    gate = gate_mod.run_gate({}, parser=parser, resolver=resolved)
    not_found = answer_mod.not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
    offer = miss_mod.run_miss_lane(
        not_found,
        parser=parser,
        resolved=resolved,
        gate=gate,
        services=services,
        contact_id=CONTACT_ID,
        space_id=SPACE_ID,
        execution_id="zzt-picker-stamp",
        dry_run=True,
    )
    return resolved, gate, offer


def _picker_lines(message: Any) -> dict[str, str]:
    """The numbered lines, keyed by the label the line starts with (the stamp stripped)."""
    out: dict[str, str] = {}
    for line in (message or "").split("\n"):
        match = _LINE_RE.match(line)
        if match:
            out[match.group(1).split(" - ")[0].strip()] = line.strip()
    return out


def _seed_contact_in(session_factory: Any, company_ids: list[str]) -> None:
    workspace_id = _seed_workspace(session_factory)
    _seed_contact(
        session_factory,
        contact_id=CONTACT_ID,
        phone="+60000000750",
        workspace_id=workspace_id,
        company_ids=company_ids,
    )


# --------------------------------------------------------------------------- #
# AC-1 / AC-2: the picker stamp and its noun
# --------------------------------------------------------------------------- #


class TestPickerStamp:
    def _seed_owner_turn(self, session_factory: Any, *, type_name: str = PHOTO_TYPE_NAME) -> str:
        """The owner's own split: SRTWC286-SH carries a file of `type_name`,
        SRTWC286-SH-200 does not."""
        company_id = _seed_company(session_factory, name="ZZT Picker Stamp Co")
        _seed_contact_in(session_factory, [company_id])
        has_id = _seed_product(session_factory, company_id=company_id, code=HAS_PHOTO_CODE)
        _seed_product(session_factory, company_id=company_id, code=NO_PHOTO_CODE)
        type_id = _seed_attachment_type(session_factory, type_name)
        _seed_file_for(
            session_factory,
            product_id=has_id,
            attachment_type_id=type_id,
            company_id=company_id,
            filename=f"{HAS_PHOTO_CODE}.jpg",
        )
        return company_id

    def test_ac1_the_picker_stamps_has_or_no_product_photos_per_line(
        self, session_factory, monkeypatch
    ) -> None:
        company_id = self._seed_owner_turn(session_factory)
        db = session_factory()
        # The scope the engine stamps on every session it opens (`_scoped_factory`). Without
        # it the resolver's own probes fail closed and the picker never renders at all.
        set_company_scope(db, frozenset({company_id}))
        calls: list[tuple[str, dict]] = []
        services = _probe_services(db, monkeypatch, calls=calls)

        parser = _parser()
        _resolved, gate, offer = _run_lane(
            db, services, parser=parser, text="photo for srtwc286"
        )

        # Preconditions - each measured healthy on the defect turn, asserted so a failure
        # upstream of the render is never mistaken for the defect under test.
        assert gate.get("require_specific") is True, (
            "the picker did not render, so the surface under test was never reached: "
            f"gate_reason={gate.get('gate_reason')!r}"
        )
        assert calls and calls[0][0] == PROBE_TOOL, calls

        message = offer.get("escalate_message") or ""
        lines = _picker_lines(message)
        assert {HAS_PHOTO_CODE, NO_PHOTO_CODE} <= set(lines), message
        assert lines[HAS_PHOTO_CODE].endswith(f"- has {PHOTO_TYPE_NAME}"), (
            f"{HAS_PHOTO_CODE} carries a {PHOTO_TYPE_NAME} attachment and the probe found it, "
            f"but the picker line reads {lines[HAS_PHOTO_CODE]!r}"
        )
        assert lines[NO_PHOTO_CODE].endswith(f"- no {PHOTO_TYPE_NAME}"), (
            f"{NO_PHOTO_CODE} has no {PHOTO_TYPE_NAME} attachment, so its line must say so "
            f"rather than dead-ending the customer on a pick: {lines[NO_PHOTO_CODE]!r}"
        )
        # The two keys `dym-annotate` carries for this render are CONTROL keys: they are
        # stripped again by `_DYM_CTRL_KEYS`, so the object this node emits is unchanged and
        # `build-suggest-offer`'s own captures stay byte-equal.
        assert "dym_probe_row_keys" not in offer, sorted(offer)
        assert "dym_probe_type_name" not in offer, sorted(offer)

    def test_ac1_numbering_and_order_are_the_gates_own(
        self, session_factory, monkeypatch
    ) -> None:
        """Suffix only: same count, same numbers, same order as `gate_clarification`."""
        company_id = self._seed_owner_turn(session_factory)
        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        services = _probe_services(db, monkeypatch, calls=[])

        _resolved, gate, offer = _run_lane(
            db, services, parser=_parser(), text="photo for srtwc286"
        )

        before = (gate.get("gate_clarification") or "").split("\n")
        after = (offer.get("escalate_message") or "").split("\n")
        assert len(after) == len(before), (before, after)
        for original, rendered in zip(before, after):
            assert rendered.startswith(original), (original, rendered)

    def test_ac2_the_noun_is_the_resolved_type_name_not_the_customers_word(
        self, session_factory, monkeypatch
    ) -> None:
        """"gambar for srtwc286": the parser's raw token is Malay, its `canonical_code` is
        "photo", and the resolved type is "Product Photos" - which is what the line must say."""
        company_id = self._seed_owner_turn(session_factory)
        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        services = _probe_services(db, monkeypatch, calls=[])

        parser = _parser(type_raw="gambar", type_code="photo")
        _resolved, gate, offer = _run_lane(
            db, services, parser=parser, text="gambar for srtwc286"
        )

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        lines = _picker_lines(offer.get("escalate_message"))
        assert lines[HAS_PHOTO_CODE].endswith(f"- has {PHOTO_TYPE_NAME}"), lines[HAS_PHOTO_CODE]
        assert "gambar" not in (offer.get("escalate_message") or ""), offer.get("escalate_message")

    def test_ac2_a_certificate_type_stamps_its_type_name(
        self, session_factory, monkeypatch
    ) -> None:
        """ATTACHMENT-MULTI R5 (tester re-run 2 Oct 2026, finding 3) supersedes #750's
        family word here: a RESOLVED Certification type stamps "Certification", the same
        word the found rows and the "has no" line use. The family word stays for the
        customer's raw word alone and for a certificate-NUMBER scope (TestCertificateScopedTurn)."""
        company_id = self._seed_owner_turn(session_factory, type_name=CERT_TYPE_NAME)
        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        services = _probe_services(db, monkeypatch, calls=[])

        parser = _parser(type_raw="cert", type_code=None)
        _resolved, gate, offer = _run_lane(db, services, parser=parser, text="cert for srtwc286")

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        lines = _picker_lines(offer.get("escalate_message"))
        assert lines[HAS_PHOTO_CODE].endswith(f"- has {CERT_TYPE_NAME}"), lines[HAS_PHOTO_CODE]
        assert lines[NO_PHOTO_CODE].endswith(f"- no {CERT_TYPE_NAME}"), lines[NO_PHOTO_CODE]


class TestCertificateScopedTurn:
    """A turn scoped by a resolved CERTIFICATE, not by an attachment type.

    `DOMAIN_PROBE["product_attachment"].requires` is `["attachment_type", "certificate"]`, and
    a `certificate` entity's `canonical_code` is the certificate NUMBER
    (`entity_resolver.py` ~1701, `canonical_code=row.certificate_number`), so naming the
    scoping entity's code verbatim would stamp "- has MS1234-5" at the customer. The family
    word is what the line must say.

    The resolver payload is a literal for the same reason AC-3's is (see the module
    docstring): the certificates chain is not what this test is about, and the entity shape is
    `_probe_certificate`'s own. Everything downstream of it - gate, transform, probe seam,
    annotator, composer - is real.
    """

    CERT_NUMBER = "MS1234-5"
    # A syntactically real uuid so `_scoping_from`'s `_is_uuid` accepts it and the transformer
    # sends `certificate_ids`; nothing dereferences it, since the probe seam is the stub.
    CERT_UUID = "3f2b1c66-9c1a-4a3e-8f21-0a5f6b7c8d90"

    def _resolver_payload(self, products: list[tuple[str, str]]) -> dict[str, Any]:
        return {
            "tokens": [TOKEN, self.CERT_NUMBER],
            "resolutions": [
                {
                    "token": TOKEN,
                    "resolved": False,
                    "ambiguous": True,
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": code,
                            "uuid": uuid,
                            "match_field": "product_code",
                            "match_tier": "prefix",
                            "company_name": "ZZT Cert Co",
                            "display": {"product_code": code},
                        }
                        for code, uuid in products
                    ],
                    "alternatives": [],
                },
                {
                    "token": self.CERT_NUMBER,
                    "resolved": True,
                    "ambiguous": False,
                    "matches": [
                        {
                            "entity_type": "certificate",
                            "canonical_code": self.CERT_NUMBER,
                            "uuid": self.CERT_UUID,
                            "match_field": "certificate_number",
                            "match_tier": "exact",
                            "display": {"certificate_number": self.CERT_NUMBER},
                        }
                    ],
                    "alternatives": [],
                },
            ],
            "unresolved_tokens": [],
        }

    def test_ac2_a_certificate_scoped_turn_stamps_the_family_word_not_the_number(
        self, session_factory, monkeypatch
    ) -> None:
        company_id = _seed_company(session_factory, name="ZZT Cert Co")
        _seed_contact_in(session_factory, [company_id])
        has_id = _seed_product(session_factory, company_id=company_id, code=HAS_PHOTO_CODE)
        no_id = _seed_product(session_factory, company_id=company_id, code=NO_PHOTO_CODE)
        type_id = _seed_attachment_type(session_factory, CERT_TYPE_NAME)
        _seed_file_for(
            session_factory,
            product_id=has_id,
            attachment_type_id=type_id,
            company_id=company_id,
            filename=f"{HAS_PHOTO_CODE}-cert.pdf",
        )

        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        calls: list[tuple[str, dict]] = []
        services = _probe_services(db, monkeypatch, calls=calls)

        parser = _parser(type_raw=self.CERT_NUMBER, type_code=None)
        _resolved, gate, offer = _run_lane(
            db,
            services,
            parser=parser,
            text=f"{self.CERT_NUMBER} for srtwc286",
            resolved=self._resolver_payload([(HAS_PHOTO_CODE, has_id), (NO_PHOTO_CODE, no_id)]),
        )

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        assert calls and self.CERT_UUID in (calls[0][1].get("certificate_ids") or []), calls
        message = offer.get("escalate_message") or ""
        lines = _picker_lines(message)
        assert lines[HAS_PHOTO_CODE].endswith("- has certificate"), lines[HAS_PHOTO_CODE]
        assert lines[NO_PHOTO_CODE].endswith("- no certificate"), lines[NO_PHOTO_CODE]
        assert self.CERT_NUMBER not in message, message


# --------------------------------------------------------------------------- #
# AC-3: the did-you-mean (D1) surfaces
# --------------------------------------------------------------------------- #


class TestD1Surfaces:
    def _resolver_payload(
        self, *, token: str, products: list[tuple[str, str]], type_uuid: str
    ) -> dict[str, Any]:
        """`resolve_references`' own OR-mode shape for a token that produced NO matches and
        trigram `alternatives` instead - the only shape that reaches D1 on this domain,
        because any product MATCH makes the gate require a specific pick."""
        return {
            "tokens": [token, "photo"],
            "resolutions": [
                {
                    "token": token,
                    "resolved": False,
                    "ambiguous": False,
                    "matches": [],
                    "alternatives": [
                        {
                            "entity_type": "product",
                            "canonical_code": code,
                            "uuid": uuid,
                            "match_field": "product_code",
                            "match_tier": "trigram",
                            "similarity": 0.62,
                            "company_name": "ZZT D1 Co",
                            "display": {"product_code": code},
                        }
                        for code, uuid in products
                    ],
                },
                {
                    "token": "photo",
                    "resolved": True,
                    "ambiguous": False,
                    "matches": [
                        {
                            "entity_type": "attachment_type",
                            "canonical_code": PHOTO_TYPE_NAME,
                            "uuid": type_uuid,
                            "match_field": "type_name",
                            "match_tier": "substring",
                            "display": {"type_name": PHOTO_TYPE_NAME},
                        }
                    ],
                    "alternatives": [],
                },
            ],
            "unresolved_tokens": [token],
        }

    def test_ac3_the_did_you_mean_offer_stamps_the_same_suffix(
        self, session_factory, monkeypatch
    ) -> None:
        company_id = _seed_company(session_factory, name="ZZT D1 Co")
        _seed_contact_in(session_factory, [company_id])
        has_id = _seed_product(session_factory, company_id=company_id, code=HAS_PHOTO_CODE)
        no_id = _seed_product(session_factory, company_id=company_id, code=NO_PHOTO_CODE)
        type_id = _seed_attachment_type(session_factory, PHOTO_TYPE_NAME)
        _seed_file_for(
            session_factory,
            product_id=has_id,
            attachment_type_id=type_id,
            company_id=company_id,
            filename=f"{HAS_PHOTO_CODE}.jpg",
        )

        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        calls: list[tuple[str, dict]] = []
        services = _probe_services(db, monkeypatch, calls=calls)

        parser = _parser(product_raw="srtwc2869")
        resolved = self._resolver_payload(
            token="srtwc2869",
            products=[(HAS_PHOTO_CODE, has_id), (NO_PHOTO_CODE, no_id)],
            type_uuid=type_id,
        )
        _resolved, gate, offer = _run_lane(
            db, services, parser=parser, text="photo for srtwc2869", resolved=resolved
        )

        assert gate.get("require_specific") is not True, (
            "this AC is about the D1 surface, so the gate must NOT have rendered a picker: "
            f"{gate.get('gate_clarification')!r}"
        )
        assert calls and calls[0][0] == PROBE_TOOL, calls
        response = offer.get("suggest_response") or ""
        assert f"{HAS_PHOTO_CODE} - has {PHOTO_TYPE_NAME}" in response, response
        assert f"{NO_PHOTO_CODE} - no {PHOTO_TYPE_NAME}" in response, response


# --------------------------------------------------------------------------- #
# AC-4 / AC-5: cross-company twins
# --------------------------------------------------------------------------- #


class TestTwinCodes:
    COMPANY_A = "ZZT Twin Co Alpha"
    COMPANY_B = "ZZT Twin Co Beta"

    def _seed_twins(self, session_factory: Any, *, photo_on_200: bool = False) -> tuple[str, str, str]:
        """Two companies each own SRTWC286-SH; company A also owns SRTWC286-SH-200, which is
        what keeps the gate from collapsing the twins (`mc-prefix-collapse` fires only when
        every candidate shares ONE code). A's twin carries the photo; B's does not."""
        company_a = _seed_company(session_factory, name=self.COMPANY_A)
        company_b = _seed_company(session_factory, name=self.COMPANY_B)
        _seed_contact_in(session_factory, [company_a, company_b])
        twin_a = _seed_product(session_factory, company_id=company_a, code=HAS_PHOTO_CODE)
        _seed_product(session_factory, company_id=company_b, code=HAS_PHOTO_CODE)
        other = _seed_product(session_factory, company_id=company_a, code=NO_PHOTO_CODE)
        type_id = _seed_attachment_type(session_factory, PHOTO_TYPE_NAME)
        _seed_file_for(
            session_factory,
            product_id=twin_a,
            attachment_type_id=type_id,
            company_id=company_a,
            filename=f"{HAS_PHOTO_CODE}-A.jpg",
        )
        if photo_on_200:
            _seed_file_for(
                session_factory,
                product_id=other,
                attachment_type_id=type_id,
                company_id=company_a,
                filename=f"{NO_PHOTO_CODE}.jpg",
            )
        return company_a, company_b, type_id

    def test_ac4_a_company_suffixed_line_is_stamped_per_company(
        self, session_factory, monkeypatch
    ) -> None:
        company_a, company_b, _type_id = self._seed_twins(session_factory)
        db = session_factory()
        set_company_scope(db, frozenset({company_a, company_b}))
        services = _probe_services(db, monkeypatch, calls=[], with_company=True)

        _resolved, gate, offer = _run_lane(
            db, services, parser=_parser(), text="photo for srtwc286"
        )

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        lines = _picker_lines(offer.get("escalate_message"))
        label_a = f"{HAS_PHOTO_CODE} ({self.COMPANY_A})"
        label_b = f"{HAS_PHOTO_CODE} ({self.COMPANY_B})"
        assert {label_a, label_b} <= set(lines), (
            "the gate no longer company-suffixes duplicated codes, so this AC is testing "
            f"something else: {offer.get('escalate_message')!r}"
        )
        assert lines[label_a].endswith(f"- has {PHOTO_TYPE_NAME}"), lines[label_a]
        assert lines[label_b].endswith(f"- no {PHOTO_TYPE_NAME}"), lines[label_b]

    def test_ac5_an_unattributable_twin_renders_bare_while_its_siblings_stamp(
        self, session_factory, monkeypatch
    ) -> None:
        """No Company field on the probe rows: the annotator cannot attribute the twin code
        to one uuid (F1), so both twin lines stay BARE rather than promising a file that
        only one company owns. The unambiguous sibling is still stamped."""
        company_a, company_b, _type_id = self._seed_twins(session_factory, photo_on_200=True)
        db = session_factory()
        set_company_scope(db, frozenset({company_a, company_b}))
        services = _probe_services(db, monkeypatch, calls=[], with_company=False)

        _resolved, gate, offer = _run_lane(
            db, services, parser=_parser(), text="photo for srtwc286"
        )

        assert gate.get("require_specific") is True, gate.get("gate_reason")
        lines = _picker_lines(offer.get("escalate_message"))
        label_a = f"{HAS_PHOTO_CODE} ({self.COMPANY_A})"
        label_b = f"{HAS_PHOTO_CODE} ({self.COMPANY_B})"
        assert lines[label_a].endswith(label_a), lines[label_a]
        assert lines[label_b].endswith(label_b), lines[label_b]
        assert lines[NO_PHOTO_CODE].endswith(f"- has {PHOTO_TYPE_NAME}"), lines[NO_PHOTO_CODE]


# --------------------------------------------------------------------------- #
# AC-6: fail open
# --------------------------------------------------------------------------- #


class TestProbeFailureRendersTheBarePicker:
    def test_ac6_a_probe_that_raised_leaves_the_picker_byte_identical(
        self, session_factory, monkeypatch
    ) -> None:
        company_id = _seed_company(session_factory, name="ZZT Fail Open Co")
        _seed_contact_in(session_factory, [company_id])
        has_id = _seed_product(session_factory, company_id=company_id, code=HAS_PHOTO_CODE)
        _seed_product(session_factory, company_id=company_id, code=NO_PHOTO_CODE)
        type_id = _seed_attachment_type(session_factory, PHOTO_TYPE_NAME)
        _seed_file_for(
            session_factory,
            product_id=has_id,
            attachment_type_id=type_id,
            company_id=company_id,
            filename=f"{HAS_PHOTO_CODE}.jpg",
        )

        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        calls: list[tuple[str, dict]] = []
        services = _probe_services(db, monkeypatch, calls=calls, fail=True)

        _resolved, gate, offer = _run_lane(
            db, services, parser=_parser(), text="photo for srtwc286"
        )

        assert calls, "the probe seam was never reached, so nothing failed open"
        assert offer.get("escalate_message") == gate.get("gate_clarification"), (
            "a probe that raised must leave the picker exactly as the gate rendered it: "
            f"{offer.get('escalate_message')!r}"
        )
