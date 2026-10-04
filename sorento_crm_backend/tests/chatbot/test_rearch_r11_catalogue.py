"""Hand pass 11, defect 4 - "cabana catalog" must answer with the Cabana catalogue file,
never a kind_pick. RED, test-first.

Owner's local chain (clone, v39, contact 900000008): "cabana catalog" -> "Which one do you
mean? 1. cabana (promotion) 2. cabana (attachment)" -> "2" -> "Here's what you want:
attachment: CABANA WASH BASIN PROMO 09092026 END USER.pdf (+14 more). But no
resource_attachment matched these. Would you like me to escalate to customer service team?"

Measured (captain's brief): the recorded verdict is CORRECT - entities [{raw "cabana", hint
brand, canonical_code null}, {raw "catalog", hint attachment, canonical_code "catalogue"}],
domain_hint resource_attachment, intent get_resource_attachment, domain_in_message true,
routing purchasing. `turn/reconcile.py::apply_reconciliation` (D2, "two kinds hitting asks")
re-kinded the BRAND word (brand miss on the clone + name hits in promotion and attachment)
into a kind_pick; the pick then resolved "cabana" as 15 promo PDFs by filename and the
"catalog" class entity was dropped, so the fetch missed. Production never asks: a
brand/company word under resource_attachment is a SCOPE, and the class word reaches the
tool as the document class.

**Fixture rebuild (coordinator round 3, 21 Sep 2026): the clone has NO attachment_type
named "catalogue" at all.** The three real catalogue PDFs are plain "Direct Access"
documents (`attachment_types.is_direct_access = true`), company_id the default company:
"@ CABANA CATALOG 2025 (RESEARCHABLE).pdf", "SORENTO CATALOGUE 2024
(SEARCHABLE)_compressed.pdf", "Mocha Catalogue FA 300924.pdf" - plus 51 real
Promotion-type files matching "cabana" by filename (e.g. "CABANA WASH BASIN PROMO
09092026 END USER.pdf", one seeded here) that must NEVER be returned. The earlier
"attachment_type_ids = a resolved catalogue-type uuid" shape this file used before was
therefore unrealistic (no such row exists) - the real production path is
`entity_resolver.resolve_references_intersection`'s own filename-coverage AND
(`_and_probe_attachment(coverage_mode=True)`, opt-in for `domain_hint="resource_attachment"`)
resolving DIRECTLY to the one attachment whose filename covers both the brand word and the
class word - no attachment_type lookup involved at all. `TestBrandWordUnderResourceAttachment
IsAScopeNotAKindPick` (a-d) now runs the REAL resolver against this real-shaped seeded
library (`_wire_real`); `TestGenuinelyAmbiguousWordStillAsks` (the control) is UNCHANGED,
still on the old hand-rolled resolver stub (`_wire`) - it is not about catalogue rows.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.models.access import RespondContact
from app.models.base import set_company_scope
from app.models.company import RespondContactCompany
from app.models.resources import Attachment, AttachmentType
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_engine_company_scope import (
    _real_resolve_entity,
    _seed_workspace,
    _unreachable_access_types,
)
from tests.chatbot.test_r3_pending_end_to_end import _session_of

LIST_TOOL = "crm_resource_attachments_list"
CATALOGUE_TOOL = "crm_resource_attachments_catalogue"
ATTACHMENT_TOOLS = {LIST_TOOL, CATALOGUE_TOOL}

CABANA_FILE = "@ CABANA CATALOG 2025 (RESEARCHABLE).pdf"
# Valid-hex fake uuids (0-9a-f only) - the OLD control test's own stubbed resolver still
# uses these (see `_wire`/`_bundle`/`_name_hits` below, kept for
# `TestGenuinelyAmbiguousWordStillAsks` only, per the coordinator's "control unchanged").
CABANA_FILE_ID = "00000000-0000-0000-0000-0000000000c1"
SORENTO_FILE = "@ SORENTO CATALOG 2025 (RESEARCHABLE).pdf"
SORENTO_FILE_ID = "00000000-0000-0000-0000-0000000000c2"
CATALOGUE_TYPE_UUID = "00000000-0000-0000-0000-0000000000c3"
PROMO_UUID = "00000000-0000-0000-0000-0000000000c4"
PROMO_FILE_UUID = "00000000-0000-0000-0000-0000000000c5"

# The REAL clone shape (a-d): no catalogue attachment_type, two real doc-type rows.
# `CABANA_FILE` above is already the real filename, reused here.
DIRECT_ACCESS_TYPE_NAME = "Direct Access"
PROMO_TYPE_NAME = "Promotion"
SORENTO_CATALOGUE_FILE = "SORENTO CATALOGUE 2024 (SEARCHABLE)_compressed.pdf"
MOCHA_CATALOGUE_FILE = "Mocha Catalogue FA 300924.pdf"
CABANA_PROMO_FILE = "CABANA WASH BASIN PROMO 09092026 END USER.pdf"

ATTACHED_INTRO = "I have attached the file(s) below."
KIND_PICK_QUESTION = "Which one do you mean?"


def _name_hits(brand: str) -> list[dict[str, Any]]:
    """The clone's real resolver shape for a brand word that is NOT a brand row: a name
    hit in `promotion` and a name hit in `attachment` (promo PDFs), no `brand` match."""
    return [
        {
            "uuid": PROMO_UUID, "entity_type": "promotion", "match_tier": "substring",
            "canonical_code": f"{brand.upper()} WASH BASIN PROMO 09092026 END USER",
            "display": {"name": f"{brand.upper()} WASH BASIN PROMO 09092026 END USER"},
        },
        {
            "uuid": PROMO_FILE_UUID, "entity_type": "attachment", "match_tier": "substring",
            "canonical_code": f"{brand.upper()} WASH BASIN PROMO 09092026 END USER.pdf",
            "display": {"original_filename": f"{brand.upper()} WASH BASIN PROMO 09092026 END USER.pdf"},
        },
    ]


def _bundle(brand: str, *, class_token: str = "catalog") -> ResolveGateServices:
    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "tokens": [brand, class_token],
            "resolutions": [
                {"raw": brand, "token": brand, "matches": _name_hits(brand)},
                {
                    "raw": class_token, "token": class_token,
                    "matches": [
                        {
                            "uuid": CATALOGUE_TYPE_UUID, "entity_type": "attachment_type",
                            "canonical_code": "catalogue", "match_field": "code",
                            "match_tier": "exact", "display": {"type_name": "Catalogue"},
                        }
                    ],
                },
            ],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


def _catalogue_envelope(filename: str, file_id: str, company: str) -> dict[str, Any]:
    return {
        "result_type": "resource_attachments",
        "intro": ATTACHED_INTRO,
        "items": [
            {
                "fields": [
                    {"key": "original_filename", "label": "File Name", "value": filename},
                    {"key": "company_name", "label": "Company", "value": company},
                    {"key": "uploaded_at", "label": "Uploaded", "value": "2025-03-01"},
                ]
            }
        ],
        "attachments": [
            {
                "id": file_id, "original_filename": filename, "company_name": company,
                "url": f"https://cdn.example.test/{file_id}.pdf", "content_type": "application/pdf",
            }
        ],
        "has_result": True,
    }


EMPTY = {"result_type": "resource_attachments", "intro": "No matching results found.", "items": [], "has_result": False}


def _wire(session_factory, monkeypatch, brand: str):
    """The clone's rows: a Cabana catalogue for a Cabana-scoped ask, a Sorento one for a
    Sorento-scoped ask - so a brand scope that is honoured returns a DIFFERENT file."""
    from app.models.user import SystemSetting

    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    for row in db.query(SystemSetting).all():
        row.chatbot_completed_lanes = ["business_query"]
    db.commit()

    calls: list[tuple[str, dict[str, Any]]] = []

    def _mcp_call(name: str, args: dict[str, Any]) -> str:
        calls.append((name, dict(args)))
        if name not in ATTACHMENT_TOOLS:
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})
        blob = json.dumps(args).lower()
        if "sorento" in blob:
            return json.dumps(_catalogue_envelope(SORENTO_FILE, SORENTO_FILE_ID, "Sorento"))
        if "cabana" in blob:
            return json.dumps(_catalogue_envelope(CABANA_FILE, CABANA_FILE_ID, "Cabana"))
        return json.dumps(EMPTY)

    bundle = _bundle(brand)
    monkeypatch.setattr(
        engine_mod.business_services, "production_services", lambda db, *, space_id=None: bundle
    )
    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
    )
    return calls


# --------------------------------------------------------------------------- #
# (a)-(d) harness: the REAL resolver against the clone's real catalogue-library
# shape - no catalogue attachment_type, a Direct Access type for the three real
# catalogue PDFs, a Promotion type for the cabana promo PDFs that must not leak.
# --------------------------------------------------------------------------- #


def _seed_attachment_type(session_factory: Any, type_name: str, *, is_direct_access: bool) -> str:
    db = session_factory()
    row = AttachmentType(
        type_name=type_name, code=None, allowed_extensions="pdf",
        is_direct_access=is_direct_access,
    )
    db.add(row)
    db.commit()
    return row.id


def _seed_attachment(
    session_factory: Any, *, type_id: str, filename: str, company_id: str = DEFAULT_COMPANY_ID
) -> str:
    db = session_factory()
    row = Attachment(
        attachment_type_id=type_id, original_filename=filename,
        stored_filename=f"zzt-{filename}", file_path=f"https://example.test/zzt-{filename}",
        company_id=company_id,
    )
    db.add(row)
    db.commit()
    return row.id


def _seed_catalogue_library(session_factory: Any) -> dict[str, str]:
    """The clone's real rows (coordinator, round 3): no attachment_type named
    catalogue at all. Returns {brand-key: attachment uuid}."""
    direct_access_id = _seed_attachment_type(session_factory, DIRECT_ACCESS_TYPE_NAME, is_direct_access=True)
    promo_type_id = _seed_attachment_type(session_factory, PROMO_TYPE_NAME, is_direct_access=False)
    return {
        "cabana": _seed_attachment(session_factory, type_id=direct_access_id, filename=CABANA_FILE),
        "sorento": _seed_attachment(session_factory, type_id=direct_access_id, filename=SORENTO_CATALOGUE_FILE),
        "mocha": _seed_attachment(session_factory, type_id=direct_access_id, filename=MOCHA_CATALOGUE_FILE),
        "cabana_promo": _seed_attachment(session_factory, type_id=promo_type_id, filename=CABANA_PROMO_FILE),
    }


def _scope_seeded_contact_to_default_company(session_factory: Any) -> None:
    """The `seeded` fixture's contact (`tests.chatbot.test_engine.CONTACT_ID`) carries no
    `RespondContactCompany` row, so `engine._session`'s scope stamping
    (`_contact_company_scope` -> `company_scope_resolver.resolve_contact_company_scope`)
    resolves an EMPTY frozenset (fail-closed by design, AC-F3) and blocks every seeded
    attachment below - they all carry `company_id = DEFAULT_COMPANY_ID`. On the real
    clone this contact IS scoped to DEFAULT_COMPANY_ID (Sorento), so this seeds the same
    link the real data has, the same one-line insert
    `tests.chatbot.test_engine_company_scope._seed_contact(..., company_ids=[...])`
    already does for its own from-scratch contacts - reused here rather than duplicated,
    since this contact already exists via the `seeded` fixture's raw-SQL insert."""
    db = session_factory()
    contact = db.query(RespondContact).filter(RespondContact.respond_io_id == str(CONTACT_ID)).one()
    db.add(RespondContactCompany(respond_contact_id=contact.id, company_id=DEFAULT_COMPANY_ID))
    db.commit()


def _point_seeded_contact_at_default_workspace(session_factory: Any) -> None:
    """Round 3 follow-up (coder measurement, 21 Sep 2026): the company-scope fix above
    is not enough on its own. `field_access.resolve_contact_id(db, contact_id,
    space_id)` JOINs through `RespondWorkspace` whenever `space_id` is truthy;
    `stub_access()` hardcodes `default_space_id` to "364817", the `seeded` fixture's
    contact carries `workspace_id = None`, and this private test DB has NO
    `RespondWorkspace` row at all - so the contact resolves to `None` and the scope
    stays `frozenset()` regardless of the `RespondContactCompany` link, exactly the
    same fail-closed symptom one layer deeper. Seeds ONE workspace at that same
    space_id (`_seed_workspace`, already hardcoded to "364817") and points the
    existing contact row at it - reuses `test_engine_company_scope.py::_seed_workspace`
    rather than a second workspace-seeding helper."""
    workspace_id = _seed_workspace(session_factory)
    db = session_factory()
    contact = db.query(RespondContact).filter(RespondContact.respond_io_id == str(CONTACT_ID)).one()
    contact.workspace_id = workspace_id
    db.commit()


def _wire_real(session_factory, monkeypatch) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, str]]:
    """The REAL resolver (`entity_resolver.resolve_references_intersection`'s
    filename-coverage AND, `_and_probe_attachment(coverage_mode=True)`, opt-in for
    `domain_hint="resource_attachment"`) against the real-shaped seeded library above.
    The MCP tool itself stays stubbed (no live server), but its stub ANSWERS FROM the
    seeded rows filtered by whatever real args the transformer built - not a canned
    fixed-uuid envelope - so a wrong id the resolver sends shows up as a wrong/empty
    reply rather than being silently absorbed by the stub."""
    from app.models.user import SystemSetting

    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    for row in db.query(SystemSetting).all():
        row.chatbot_completed_lanes = ["business_query"]
    db.commit()

    _scope_seeded_contact_to_default_company(session_factory)
    _point_seeded_contact_at_default_workspace(session_factory)
    ids = _seed_catalogue_library(session_factory)

    def _bundle(db: Any, *, space_id: str | None = None) -> ResolveGateServices:
        return ResolveGateServices(
            access_types=_unreachable_access_types,
            resolve_entity=_real_resolve_entity(db),
            probe=lambda **_: None,
        )

    monkeypatch.setattr(engine_mod.business_services, "production_services", _bundle)

    calls: list[tuple[str, dict[str, Any]]] = []

    def _mcp_call(name: str, args: dict[str, Any]) -> str:
        calls.append((name, dict(args)))
        if name not in ATTACHMENT_TOOLS:
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})
        db2 = session_factory()
        # Coder 40's measurement, round 4: `Attachment` is `CompanyScopedMixin` and every
        # seeded row carries `DEFAULT_COMPANY_ID` - a fresh session opened here with no
        # scope stamped reads UNSCOPED (0 rows), falls through to EMPTY, and the composer
        # correctly reports a miss for what should have been a hit. The tool call itself
        # was always right (`attachment_ids` = the one catalogue uuid); only this stub's
        # own read needed the scope a real request-scoped session already carries.
        set_company_scope(db2, frozenset({DEFAULT_COMPANY_ID}))
        q = (
            db2.query(
                Attachment.id, Attachment.original_filename, AttachmentType.type_name
            )
            .outerjoin(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
            .filter(Attachment.is_deleted.is_(False))
        )
        attachment_ids = [str(u) for u in (args.get("attachment_ids") or [])]
        type_ids = [str(u) for u in (args.get("attachment_type_ids") or [])]
        type_code = args.get("attachment_type_code")
        type_codes = list(args.get("attachment_type_codes") or [])
        if attachment_ids:
            q = q.filter(Attachment.id.in_(attachment_ids))
        elif type_ids:
            q = q.filter(Attachment.attachment_type_id.in_(type_ids))
        elif type_code or type_codes:
            names = ([type_code] if type_code else []) + type_codes
            q = q.filter(AttachmentType.type_name.in_(names))
        else:
            # crm_resource_attachments_list's own contract: named no document at all
            # -> the tool returns nothing, by design.
            return json.dumps(EMPTY)
        rows = q.all()
        if not rows:
            return json.dumps(EMPTY)
        items = [
            {
                "fields": [
                    {"key": "original_filename", "label": "File Name", "value": filename},
                    {"key": "company_name", "label": "Company", "value": "Sorento"},
                    {"key": "uploaded_at", "label": "Uploaded", "value": "2025-03-01"},
                ]
            }
            for _aid, filename, _type_name in rows
        ]
        attachments = [
            {
                "id": str(aid), "original_filename": filename, "company_name": "Sorento",
                "url": f"https://cdn.example.test/{aid}.pdf", "content_type": "application/pdf",
            }
            for aid, filename, _type_name in rows
        ]
        return json.dumps(
            {
                "result_type": "resource_attachments", "intro": ATTACHED_INTRO,
                "items": items, "attachments": attachments, "has_result": True,
            }
        )

    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
    )
    return calls, ids


def _recorded_verdict(brand: str, class_word: str = "catalog") -> dict[str, Any]:
    """The owner's recorded verdict, verbatim in every field that matters."""
    return _parser_output(
        intent_hint="get_resource_attachment",
        domain_hint="resource_attachment",
        domain_in_message=True,
        entities=[
            {"raw": brand, "hint": "brand", "canonical_code": None, "current_message": True, "confident": True},
            {"raw": class_word, "hint": "attachment", "canonical_code": "catalogue", "current_message": True, "confident": True},
        ],
        routing={"suggested_team": "purchasing", "suggested_agent": "general_enquiries", "team_source": None},
    )


def _turn(session_factory, stub_parser, verdict: dict[str, Any], *, text: str, msg_id: str):
    stub_parser(verdict)
    envelope = _envelope()
    envelope.message["message"]["messageId"] = msg_id
    envelope.message["message"]["message"]["text"] = text
    return engine_mod.run_turn(envelope, session_factory=session_factory)


def _said(result) -> str:
    return "\n".join(
        [((result.reply or {}).get("text") or "")]
        + [a.get("text") or "" for a in (result.actions or []) if isinstance(a, dict)]
    )


def _sent_files(result) -> list[str]:
    names: list[str] = []
    for a in result.actions or []:
        if isinstance(a, dict) and a.get("kind") == "send_attachments":
            for f in a.get("attachments_src") or a.get("attachments") or []:
                if isinstance(f, dict):
                    names.append(str(f.get("original_filename") or f.get("filename") or f.get("name") or ""))
    return names


class TestBrandWordUnderResourceAttachmentIsAScopeNotAKindPick:
    @pytest.mark.parametrize("text", ["may i hv cabana catalogue", "cabana catalog"])
    def test_no_kind_pick_is_minted(
        self, text, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """(a) a brand-hinted entity under a resource_attachment plan is never re-kinded."""
        _wire_real(session_factory, monkeypatch)
        stub_access()
        result = _turn(session_factory, stub_parser, _recorded_verdict("cabana"), text=text, msg_id=f"ZZT-r11-cat-{len(text)}")
        assert result.status == "done", result.error
        said = _said(result)
        assert KIND_PICK_QUESTION not in said, said
        # The kind_pick's own NUMBERED option lines (D2's exact old failure shape,
        # module docstring), not the generic miss reply's own "(hint)" echo - a
        # genuine "Couldn't find: X (brand), Y (attachment)." miss ALSO contains the
        # bare substring "(attachment)", which is not a kind_pick and must not fail
        # this check.
        assert "1. cabana (promotion)" not in said and "2. cabana (attachment)" not in said, said
        assert (_session_of(session_factory).get("open_question") or {}).get("kind") != "kind_pick", (
            _session_of(session_factory).get("open_question")
        )

    def test_the_class_entity_reaches_the_tool_as_the_document_class(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """(b) the resolver's own filename-coverage AND (`_and_probe_attachment
        (coverage_mode=True)`) narrows DIRECTLY to the Cabana catalogue attachment -
        `attachment_ids` carries that uuid and ONLY that uuid; the same-brand
        Promotion-type file (covers "cabana" but not "catalogue") must never reach
        the tool call at all."""
        calls, ids = _wire_real(session_factory, monkeypatch)
        stub_access()
        _turn(session_factory, stub_parser, _recorded_verdict("cabana"), text="cabana catalog", msg_id="ZZT-r11-cat-tool")
        attachment_calls = [args for name, args in calls if name in ATTACHMENT_TOOLS]
        assert attachment_calls, f"the resource attachment tool must run, no miss without a fetch: {calls}"
        args = attachment_calls[0]
        attachment_ids = [str(u) for u in (args.get("attachment_ids") or [])]
        assert attachment_ids == [str(ids["cabana"])], (
            "attachment_ids must carry the Cabana catalogue uuid ONLY, never the "
            "same-brand promo file's uuid", args, ids,
        )

    def test_the_reply_is_the_attachment_form_with_the_cabana_catalogue(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """(c) production's attachment reply, the Cabana catalogue in `send_attachments`."""
        _wire_real(session_factory, monkeypatch)
        stub_access()
        result = _turn(session_factory, stub_parser, _recorded_verdict("cabana"), text="cabana catalog", msg_id="ZZT-r11-cat-reply")
        said = _said(result)
        assert ATTACHED_INTRO in said, said
        assert "no resource_attachment matched" not in said.lower(), said
        assert "escalate" not in said.lower(), said
        assert CABANA_FILE in _sent_files(result), (result.actions,)

    def test_sorento_and_cabana_catalogues_are_different_files(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """(d) the brand scope is honoured: two brands, two files - ONE seeded library
        serves both asks, since the resolver itself scopes by whichever brand word the
        message names."""
        _wire_real(session_factory, monkeypatch)
        stub_access()
        cabana = _turn(session_factory, stub_parser, _recorded_verdict("cabana", "catalogue"), text="cabana catalogue", msg_id="ZZT-r11-cat-d1")
        sorento = _turn(session_factory, stub_parser, _recorded_verdict("sorento", "catalogue"), text="sorento catalogue", msg_id="ZZT-r11-cat-d2")
        cabana_files, sorento_files = _sent_files(cabana), _sent_files(sorento)
        assert CABANA_FILE in cabana_files, (cabana.actions,)
        assert SORENTO_CATALOGUE_FILE in sorento_files, (sorento.actions,)
        assert set(cabana_files).isdisjoint(sorento_files), (cabana_files, sorento_files)


class TestGenuinelyAmbiguousWordStillAsks:
    def test_control_two_real_kinds_under_a_non_scope_hint_mint_the_kind_pick(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """(e) CONTROL, green today: D2 kept - "cabana" typed as a PRODUCT under a stock ask,
        with a promotion hit and an attachment hit and no product, still asks."""
        _wire(session_factory, monkeypatch, "cabana")
        stub_access()
        result = _turn(
            session_factory, stub_parser,
            _parser_output(
                intent_hint="check_stock", domain_hint="inventory",
                entities=[{"raw": "cabana", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
            ),
            text="cabana stock", msg_id="ZZT-r11-cat-control",
        )
        assert result.status == "done", result.error
        assert KIND_PICK_QUESTION in _said(result), _said(result)
        assert (_session_of(session_factory).get("open_question") or {}).get("kind") == "kind_pick"
