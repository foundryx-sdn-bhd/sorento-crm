"""The turn engine resolves products with zero company scope (defect verified 6 Sep
2026, this lane's own brief).

`app/services/chatbot/lanes/business/services.py`'s `_resolve_entity(db)` calls the
resolver ROUTE function `resolve_reference_post` IN PROCESS, with the `db` that
`app/services/chatbot/engine.py`'s `_session(session_factory)` opened. Nothing under
`app/services/chatbot/` ever calls `set_company_scope` on that session, so
`get_company_scope(db)` reads `UNSET` for the whole turn, `build_company_predicate`
compiles that to `false()` for every `CompanyScopedMixin` model (`Product` included),
and the resolver's own ORM probes (`_probe_product`, `_and_probe_product`) return zero
rows regardless of which company the contact actually belongs to. Every business turn
that asks about a real product therefore answers "Couldn't find: <code> (product)".

This file drives the bug through `engine.run_turn`, on the REAL production
`resolve_entity` seam - not the always-empty / always-a-fixed-uuid stand-ins
`test_s6c_engine_paths.py` and friends use, which is exactly why no existing test
caught this. `access_types` and `probe` stay stubbed: `business_query` enters
`resolve_gate.run` at `entry="resolve"` (`ENTRY_BY_BRANCH_KIND`), never
`"access_check"`, so neither seam should ever be called on this branch kind, and a
call is a wiring drift, not a passing turn. `business.run_fetch` is stubbed to a
canned success (only reached on the `continue` exit, i.e. only when a product DID
resolve): the resolve step is what this file grades, and a real RAG/MCP tool
selection would grade something else on top of it. `spec_fallback` is forced off on
every real resolve call - the seam's own production body hardcodes
`spec_fallback: True`, which reaches an LLM
(`product_spec_understanding.derive_search_inputs`, gated on `understand_phrase`) the
moment the normal probes miss, which is exactly the "unknown contact" arm below. No
test in this file may touch an LLM, the network, or n8n.

A gotcha found while writing this file, worth stating so nobody "fixes" these tests
into passing for the wrong reason: `get_company_scope(db)` does NOT read `UNSET` in
THIS test process the way it does in production. `tests/conftest.py` installs a
suite-wide `after_begin` listener (`_default_company_scope_for_tests`) that
`session.info.setdefault("company_scope", SORENTO)` on every brand-new session, so
legacy tests that never think about company scope still see Sorento's data. Every
company seeded below is deliberately a FRESH id, never Sorento's, so the observed
failure ("Couldn't find") is still the real defect - nothing overwrites that harness
default with the CONTACT's own company - it is just that the wrong value in play here
is "the test harness's Sorento default" rather than production's own "UNSET",
and the fix must overwrite that default on every session regardless of which
literal value it started as.

Postgres only (`tests/_pg_fixture.py` via `tests/chatbot/conftest.py`'s
`session_factory`), every chain seeded fresh. No em or en dashes.

Retired 16 Sep 2026 (AC-1592, the SAME ruling `test_complete_turn.py`'s own header
already documents for the identical reason - "complete_turn requires status
delegated; run_turn now completes in-process"): `TestTheTailSessionCarriesTheContactsScope`'s
one test forced `_set_completed_lanes(..., [])` to make `engine.run_turn` leave a turn
`"delegated"` so it could grade `complete_turn`'s own session's company scope. Measured
directly this session: `CRM_COMPLETED_BRANCH_KINDS` (`contracts.py`) now covers all 13
`BRANCH_KINDS` (S7's own completion, already reached) - `chatbot_completed_lanes` no
longer gates completion at all, so no branch kind `run_turn` can route to ever leaves a
turn delegated, and this test's whole setup is unconstructable under the current engine.
`complete_turn`/`/complete` is NOT retired as a whole (still reachable from
`app/api/v1/external/chat.py`, still guards a non-delegated turn with a 409) - only "a
genuinely still-delegated turn's tail session" has no construction left via `run_turn`,
same finding `test_complete_turn.py` already made independently. The property this test
protected (the tail's session carries the CONTACT's own company scope, not the test
harness's Sorento default) is not orphaned: `TestSessionSeamCarriesTheContactsScope` and
`TestScopeIsTheContactsCompanyOnly` above prove the SAME scope-stamping seam
(`engine._session`) inside `run_turn` itself, which is the only place a real turn's
session is opened today.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from app.models.access import RespondContact
from app.models.base import get_company_scope
from app.models.company import Company, RespondContactCompany
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.respond_workspace import RespondWorkspace
from app.models.user import SystemSetting
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import Envelope
from app.services.chatbot.lanes.business.services import AnswerServices, ResolveGateServices
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401

# D5's own hardcoded n8n default, reused here as the workspace's `space_id` rather
# than a made-up one: `stub_access()` (imported above) points `engine_mod.default_space_id`
# at this SAME literal, so a fix that keeps using that call site and a fix that reads
# the workspace row directly (`RespondWorkspaceService(db).get_default()`) agree on
# which workspace the contact belongs to either way.
SPACE_ID = "364817"


# --------------------------------------------------------------------------- #
# Seeding helpers - every chain seeded fresh, nothing borrowed from the shared DB.
# --------------------------------------------------------------------------- #


def _seed_workspace(session_factory: Any) -> str:
    db = session_factory()
    workspace = RespondWorkspace(
        space_id=SPACE_ID,
        name="ZZT scope test workspace",
        api_key_ciphertext="ZZT-cipher",
        is_default=True,
    )
    db.add(workspace)
    db.commit()
    return workspace.id


def _seed_company(session_factory: Any, *, name: str) -> str:
    db = session_factory()
    company = Company(name=name, code=unique_code(name.replace(" ", ""))[:50])
    db.add(company)
    db.commit()
    return company.id


def _seed_product(session_factory: Any, *, company_id: str, code: str) -> str:
    db = session_factory()
    category = ProductCategory(
        category_code=unique_code("CAT")[:50],
        category_name="ZZT scope category",
        company_id=company_id,
    )
    uom = UnitOfMeasure(uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company_id)
    db.add_all([category, uom])
    db.flush()
    product = Product(
        product_code=code,
        product_name=f"ZZT scope product {code}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=10,
        is_active=True,
        company_id=company_id,
    )
    db.add(product)
    db.commit()
    return product.id


def _seed_contact(
    session_factory: Any,
    *,
    contact_id: str,
    phone: str,
    workspace_id: str,
    company_ids: list[str] = (),
) -> str:
    db = session_factory()
    contact = RespondContact(
        respond_io_id=contact_id,
        phone_number=phone,
        workspace_id=workspace_id,
        session_vars={"variables": {}},
        # AI-ALLOWED-GATE: a new contact is blocked by default; this one must answer.
        chatbot_allowed=True,
    )
    db.add(contact)
    db.flush()
    for company_id in company_ids:
        db.add(RespondContactCompany(respond_contact_id=contact.id, company_id=company_id))
    db.commit()
    return contact.id


def _set_completed_lanes(session_factory: Any, system_settings_row: Any, lanes: list[str]) -> None:
    db = session_factory()
    row = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
    row.chatbot_completed_lanes = lanes
    db.commit()


def _scope_envelope(contact_id: str, *, message_id: str, text: str) -> Envelope:
    payload = {
        "contact": {
            "id": contact_id,
            "firstName": "ZZT",
            "custom_fields": [{"name": "is_human_intervened", "value": "false"}],
        },
        "message": {
            "event_type": "message.received",
            "contact": {"id": contact_id},
            "message": {
                "messageId": message_id,
                "contactId": contact_id,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": text},
            },
        },
    }
    return Envelope(**payload)


def _entity_for(code: str) -> dict[str, Any]:
    return {
        "raw": code,
        "hint": "product",
        "canonical_code": None,
        "current_message": True,
        "confident": True,
    }


def _spy_resolve_kinds(monkeypatch: Any) -> list[list[dict[str, Any]]]:
    """AC-1592 port: the old `_looked_up_resolved` read `record["raw"]["resolve_gate"]
    ["resolved"]` off the "looked_up" trace stage - that raw shape is now
    `{"envelopes": [...]}` (contract 129, `engine.py`'s own `turn_trace.record("looked_up"
    ...)` call), and an envelope's own `entities` field is display STRINGS
    (`turn_runtime.envelope_of`'s `codes`), not the resolver's rich per-candidate dicts -
    company scope cannot be read back off it at all.

    The resolver's real per-candidate rows (`uuid`, `entity_type`, ...) still exist, one
    layer up: `turn_runtime.resolve_kinds`'s own return (`compatible_entities`, its 2nd
    tuple element), called once by `engine.run_turn` with the REAL resolver wired in via
    `_wire_real_resolve_entity`. Spied here (call-through, not a stub - the real resolve
    still runs) so a test can grade exactly what the resolver itself matched, the same
    property `_looked_up_resolved` used to prove a different way. Returns the list of
    calls' `compatible_entities`, in call order (one call per turn in every test here).

    `company_id` itself is NOT in this shape (grep-confirmed: neither `resolve_gate.py`
    nor `turn_runtime.candidates_by_kind` carries it) - the scope property is instead
    proven by UUID identity, which is exactly as strong a guarantee here: two
    same-CODED products in different companies still have different uuids, so "the
    other company's product's uuid never appears" is the same assertion the old
    `company_id`-keyed check made, just keyed on the field the current resolver
    actually returns.
    """
    from app.services.chatbot import turn_runtime as turn_runtime_mod

    real_resolve_kinds = turn_runtime_mod.resolve_kinds
    calls: list[list[dict[str, Any]]] = []

    def _spy(db: Any, **kwargs: Any) -> Any:
        result = real_resolve_kinds(db, **kwargs)
        calls.append(result.compatible_entities)
        return result

    monkeypatch.setattr(engine_mod.turn_runtime, "resolve_kinds", _spy)
    return calls


# --------------------------------------------------------------------------- #
# Engine wiring - the real resolve seam, the entry-gate/picker seams stubbed unreachable,
# and a canned fetch success so a resolved product reaches "done" without the RAG/MCP
# tool-selection machinery this file is not about.
# --------------------------------------------------------------------------- #


def _unreachable_access_types(**_kwargs: Any) -> Any:
    raise AssertionError(
        "business_query enters resolve_gate at entry='resolve' (ENTRY_BY_BRANCH_KIND), "
        "never 'access_check' - access_types must not run on this branch kind"
    )


def _unreachable_probe(**_kwargs: Any) -> Any:
    raise AssertionError(
        "a single resolved (or genuinely absent) product needs no picker probe on the "
        "master_products domain"
    )


def _real_resolve_entity(db: Any):
    """The SAME production binding `business_services._resolve_entity(db)` builds,
    minus the hardcoded `spec_fallback: True` - see the module docstring for why."""
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings

    def call(body: dict[str, Any]) -> dict[str, Any]:
        safe_body = {**body, "spec_fallback": False, "understand_phrase": False}
        principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
        return resolve_reference_post(
            ResolveReferenceRequest(**safe_body), current_user=principal, db=db
        )

    return call


def _wire_real_resolve_entity(monkeypatch: Any) -> None:
    def _bundle(db: Any, *, space_id: str | None = None) -> ResolveGateServices:
        return ResolveGateServices(
            access_types=_unreachable_access_types,
            resolve_entity=_real_resolve_entity(db),
            probe=_unreachable_probe,
        )

    monkeypatch.setattr(engine_mod.business_services, "production_services", _bundle)


def _no_probe_answer_services() -> AnswerServices:
    def _mcp_probe(name: str, args: dict) -> Any:
        return {"answers": [], "has_result": False}

    def _family_fetch(query: str) -> Any:
        return {"data": []}

    return AnswerServices(mcp_probe=_mcp_probe, family_fetch=_family_fetch)


def _wire_answer_services(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        engine_mod.business_services,
        "answer_services_for",
        lambda session_factory: _no_probe_answer_services(),
    )


def _wire_answered_fetch(monkeypatch: Any) -> None:
    """`business.run_fetch` only runs on the `continue` exit (gate passed, a product
    resolved). A canned success here isolates "did resolve find the right company's
    product" from tool selection, which this file is not about."""

    def _run_fetch(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        fetch = {
            "answers": [{"note": "in stock"}],
            "response": "Yes, we have that in stock.",
            "has_result": True,
            "_fetch_arm": "result",
        }
        return {
            "kind": "result",
            "_fetch_arm": "result",
            "delegate": "business_query",
            "delegate_payload": {**payload, "fetch": fetch},
            "fetch": fetch,
        }

    monkeypatch.setattr(engine_mod.business, "run_fetch", _run_fetch)


def _wire_business_query_turn(session_factory: Any, system_settings_row: Any, monkeypatch: Any) -> None:
    set_chatbot_switches(session_factory, business_lane=True)
    _set_completed_lanes(session_factory, system_settings_row, ["business_query"])
    _wire_real_resolve_entity(monkeypatch)
    _wire_answer_services(monkeypatch)
    _wire_answered_fetch(monkeypatch)


# --------------------------------------------------------------------------- #
# 1. The session seam carries the contact's scope - a product in the contact's own
#    company resolves and the turn answers. RED today: nothing overwrites the test
#    harness's Sorento default with the contact's own (fresh, non-Sorento) company.
# --------------------------------------------------------------------------- #


class TestSessionSeamCarriesTheContactsScope:
    def test_a_contacts_own_company_product_resolves_and_the_turn_answers(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        company_id = _seed_company(session_factory, name="ZZT Scope Co Own")
        code = "ZZTSCOPEOWN1"
        product_id = _seed_product(session_factory, company_id=company_id, code=code)
        workspace_id = _seed_workspace(session_factory)
        contact_id = "ZZT-contact-scope-own"
        _seed_contact(
            session_factory,
            contact_id=contact_id,
            phone="+60000000101",
            workspace_id=workspace_id,
            company_ids=[company_id],
        )

        _wire_business_query_turn(session_factory, system_settings_row, monkeypatch)
        stub_parser(_parser_output(domain_hint="master_products", entities=[_entity_for(code)]))
        stub_access()
        resolve_calls = _spy_resolve_kinds(monkeypatch)

        result = engine_mod.run_turn(
            _scope_envelope(
                contact_id, message_id="ZZT-msg-scope-own", text=f"do you have {code} in stock"
            ),
            session_factory=session_factory,
        )

        assert result.status == "done", result.error
        assert not result.reply["text"].startswith("Couldn't find"), result.reply["text"]

        assert resolve_calls, "the resolver was never called this turn"
        product_matches = resolve_calls[-1]
        assert any(m.get("uuid") == product_id for m in product_matches), (
            "resolve step never reported the seeded product as a match "
            f"(company scope blocked it): {product_matches}"
        )


# --------------------------------------------------------------------------- #
# 2. The scope is the CONTACT's company, never every company - a same-coded product
#    in a company the contact does NOT belong to must not surface. This must stay
#    meaningful after the fix: it fails if the fix sets scope = None (all companies).
# --------------------------------------------------------------------------- #


class TestScopeIsTheContactsCompanyOnly:
    def test_the_other_companys_same_coded_product_never_surfaces(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        company_a = _seed_company(session_factory, name="ZZT Scope Co A")
        company_b = _seed_company(session_factory, name="ZZT Scope Co B")
        code = "ZZTSCOPEBOTH1"
        product_a = _seed_product(session_factory, company_id=company_a, code=code)
        product_b = _seed_product(session_factory, company_id=company_b, code=code)
        workspace_id = _seed_workspace(session_factory)
        contact_id = "ZZT-contact-scope-a-only"
        _seed_contact(
            session_factory,
            contact_id=contact_id,
            phone="+60000000102",
            workspace_id=workspace_id,
            company_ids=[company_a],
        )

        _wire_business_query_turn(session_factory, system_settings_row, monkeypatch)
        stub_parser(_parser_output(domain_hint="master_products", entities=[_entity_for(code)]))
        stub_access()
        resolve_calls = _spy_resolve_kinds(monkeypatch)

        result = engine_mod.run_turn(
            _scope_envelope(
                contact_id, message_id="ZZT-msg-scope-a-only", text=f"do you have {code} in stock"
            ),
            session_factory=session_factory,
        )

        assert result.status == "done", result.error
        assert resolve_calls, "the resolver was never called this turn"
        product_matches = resolve_calls[-1]
        matched_uuids = {m.get("uuid") for m in product_matches}

        assert product_b not in matched_uuids, (
            "the OTHER company's same-coded product surfaced - scope leaked, or the "
            f"fix widened to all companies: {product_matches}"
        )
        # AC-1592 port: `company_id` is not in this shape (see `_spy_resolve_kinds`'s own
        # docstring) - uuid identity carries the same guarantee, since the two seeded
        # products share a CODE but never a uuid.
        assert matched_uuids == {product_a}, (
            f"expected only the contact's own company's product, got: {product_matches}"
        )


# --------------------------------------------------------------------------- #
# 3. A contact with no company membership fails closed: no exception, and the
#    product never resolves - exactly the "Couldn't find" reply the defect reports,
#    which is also today's (buggy) behaviour by coincidence, since nothing overwrites
#    the harness's Sorento default with anything for an unknown contact either. The
#    guard is against a fix that widens an unknown contact to "all companies".
# --------------------------------------------------------------------------- #


class TestUnknownContactFailsClosed:
    def test_a_contact_with_no_company_membership_never_resolves_the_product(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        company_name = "ZZT Scope Co Orphan"
        company_id = _seed_company(session_factory, name=company_name)
        code = "ZZTSCOPEORPHAN1"
        _seed_product(session_factory, company_id=company_id, code=code)
        workspace_id = _seed_workspace(session_factory)
        contact_id = "ZZT-contact-scope-orphan"
        # No respond_contact_companies row for this contact at all - the AC-F3 shape
        # (contact matched, zero memberships), never the "no contact identity" shape.
        _seed_contact(
            session_factory,
            contact_id=contact_id,
            phone="+60000000103",
            workspace_id=workspace_id,
            company_ids=[],
        )

        _wire_business_query_turn(session_factory, system_settings_row, monkeypatch)
        stub_parser(_parser_output(domain_hint="master_products", entities=[_entity_for(code)]))
        stub_access()
        resolve_calls = _spy_resolve_kinds(monkeypatch)

        result = engine_mod.run_turn(
            _scope_envelope(
                contact_id, message_id="ZZT-msg-scope-orphan", text=f"do you have {code} in stock"
            ),
            session_factory=session_factory,
        )

        assert result.status == "done", result.error

        # 20 Sep 2026 (R-d, hand pass 7 - flagged for the security-reviewer, not just
        # the tester): this test used to prove "no product data reaches an orphan
        # contact" by checking that the COMPOSER discards a tool's raw response text -
        # that mechanism is retired on purpose (the owner's hand pass 7 ruling is the
        # opposite: reply copy IS production copy, `turn/compose.py` now reuses the
        # fetch lane's own `lane_text` verbatim). Measured directly (a temporary spy on
        # `engine_mod.business.run_fetch`): `run_fetch` DOES run for this turn and its
        # canned "Yes, we have that in stock." text DOES reach the reply - `master_
        # products`' own `product` entity kind narrows `optional_filter` (no override
        # in `chatbot_domains.narrowing`, so the entity-kind default applies), so an
        # unresolved product does not block the domain's own (company-scoped) generic
        # fetch. That is a DIFFERENT, legitimate property (a catalogue domain still
        # answers when the named product does not resolve) and not what this test
        # polices. The real invariant - a contact with no company membership gets no
        # REAL row for the product it asked about - lives one layer down, at entity
        # resolution, and is proven directly against the resolver's own output below,
        # never inferred from the composed text.
        assert resolve_calls, "the resolver was never called this turn"
        product_matches = resolve_calls[-1]
        assert product_matches == [], (
            f"an orphan contact (no company membership) still saw a product: {product_matches}"
        )

        # And the reply carries no TRACE of the orphan's own (never-resolved) product or
        # company - the seeded product's own name and its company's own name are both
        # values a REAL fetch of this exact row would have to carry; their absence is
        # the actual "no product data reached the reply" property, independent of
        # whatever generic boilerplate the (stubbed) catalogue fetch also says.
        text = result.reply["text"]
        lowered = text.lower()
        assert "could not find" in lowered or "couldn't find" in lowered, text
        assert code in text, f"the unresolved code must still be named honestly: {text!r}"
        assert f"ZZT scope product {code}" not in text, text
        assert company_name not in text, text


# --------------------------------------------------------------------------- #
# 4. Unit: every session the engine opens carries the CONTACT's own scope.
#
# The literal brief for this test was "assert none is UNSET" - not exercisable here:
# `tests/conftest.py`'s `after_begin` listener defaults every brand-new session's
# scope to a fixed Sorento frozenset before any production code runs (see the module
# docstring), so `scope is not UNSET` would pass today by accident of that harness
# convenience, not because the engine did anything. The property that IS exercisable,
# and is what the brief is actually after, is that the engine OVERWRITES that default
# with the CONTACT's real company on every session it opens - so the company below is
# a fresh id, deliberately never Sorento's, and the assertion is an exact match, not a
# not-UNSET check.
# --------------------------------------------------------------------------- #


class TestEverySessionTheEngineOpensCarriesTheContactsScope:
    def test_every_session_carries_the_contacts_own_company_not_the_test_harness_default(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        company_id = _seed_company(session_factory, name="ZZT Scope Co SessionSeam")
        workspace_id = _seed_workspace(session_factory)
        contact_id = "ZZT-contact-scope-session-seam"
        _seed_contact(
            session_factory,
            contact_id=contact_id,
            phone="+60000000104",
            workspace_id=workspace_id,
            company_ids=[company_id],
        )

        seen: list[Any] = []
        original_session = engine_mod._session

        @contextmanager
        def _recording_session(factory):
            with original_session(factory) as db:
                yield db
                # Read AFTER the `with` body ran (whatever the fix does to `db` while
                # it was open), not at open time - a fix that stamps the scope as its
                # first act inside the block would otherwise read as still-defaulted
                # here.
                seen.append(get_company_scope(db))

        monkeypatch.setattr(engine_mod, "_session", _recording_session)

        stub_parser(_parser_output(domain_hint="forms", entities=[], user_goal="checking a form"))
        stub_access()

        engine_mod.run_turn(
            _scope_envelope(
                contact_id, message_id="ZZT-msg-scope-session-seam", text="checking a form"
            ),
            session_factory=session_factory,
        )

        assert seen, "the engine never opened a session through `_session` - seam drifted"
        assert all(scope == frozenset({company_id}) for scope in seen), (
            "a session the engine opened did not carry the contact's own company "
            f"scope ({company_id!r}); the test harness's own Sorento default is "
            f"indistinguishable from a fix that never ran: {seen}"
        )


# --------------------------------------------------------------------------- #
# 5. RETIRED (AC-1592, 16 Sep 2026) - see the module docstring's own "Retired" note.
#    `TestTheTailSessionCarriesTheContactsScope` and its `_tail_fragments` helper used
#    to force a "delegated" turn to grade `complete_turn`'s tail session scope; no
#    branch kind leaves a turn delegated under the current engine, so the setup is
#    unconstructable. The property is covered by tests 1-2 above instead.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 6. The escalation lane opens its OWN session (its writes are a unit of work of their
#    own and must not ride the turn's routing transaction). That session came off
#    `SessionLocal` directly, outside the engine's factory, so it carried no scope - and
#    escalation is the lane that READS `Team` / `AgentTeam` to draw a round-robin
#    assignee, both `CompanyScopedMixin`, so the whole lane fails closed on a real turn.
# --------------------------------------------------------------------------- #


def _escalation_parser_output() -> dict[str, Any]:
    return _parser_output(
        message_type="request_for_help",
        intent_hint=None,
        domain_hint=None,
        scope_intent=None,
        user_goal="wants a human",
        entities=[],
        routing={
            "suggested_team": "customer_service",
            "suggested_agent": "general_enquiries",
            "team_source": "inferred",
        },
        escalation={"is_escalation_confirmation": True, "company_pick": None},
    )


def _stub_escalation_seams() -> Any:
    """The four seams, canned. The SESSION is what this test grades, not the assignment."""

    class _Seams:
        resolve_and_gate = staticmethod(
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("resolve_and_gate is never called on the live graph")
            )
        )
        next_assignee = staticmethod(
            lambda body: {
                "assignee_id": "usr-pic-1",
                "assignee_email": "pic@sorento.example",
                "assignee_name": "PIC One",
                "assignee_respond_user_id": "respond-usr-1",
                "team_set_code": "CS",
                "brand_code": None,
                "company_id": None,
                "is_already_assigned": False,
            }
        )
        sla_create = staticmethod(
            lambda body: {
                "id": "sla-row-1",
                "initiated_at": "2026-09-05T04:00:00+00:00",
                "due_at": "2026-09-05T08:00:00+00:00",
                "due_at_resolution": "2026-09-06T04:00:00+00:00",
            }
        )
        team_members = staticmethod(lambda *a, **k: [])

    return _Seams()


class TestEscalationLaneSessionCarriesTheContactsScope:
    def test_the_lanes_own_session_carries_the_contacts_own_company(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        from app.services.chatbot.lanes import escalation as escalation_mod

        company_id = _seed_company(session_factory, name="ZZT Scope Co Escalation")
        workspace_id = _seed_workspace(session_factory)
        contact_id = "ZZT-contact-scope-escalation"
        _seed_contact(
            session_factory,
            contact_id=contact_id,
            phone="+60000000106",
            workspace_id=workspace_id,
            company_ids=[company_id],
        )

        set_chatbot_switches(session_factory, business_lane=False)
        _set_completed_lanes(session_factory, system_settings_row, ["out_of_scope"])
        stub_parser(_escalation_parser_output())
        stub_access()

        # `production_services` is where the lane's own session becomes visible: it is
        # handed the session `production_session()` opened, which is the seam under test.
        # The four seams themselves are canned - the assignment is not what this grades.
        seen: list[Any] = []

        def _recording_production_services(db: Any) -> Any:
            seen.append(get_company_scope(db))
            return _stub_escalation_seams()

        monkeypatch.setattr(
            escalation_mod, "production_services", _recording_production_services
        )

        result = engine_mod.run_turn(
            _scope_envelope(
                contact_id, message_id="ZZT-msg-scope-escalation", text="I want to talk to a person"
            ),
            session_factory=session_factory,
        )

        assert result.status != "failed", result.error
        assert seen, (
            "the escalation lane never reached its production session - it either took the "
            "clarify arm or the lane did not run at all"
        )
        assert all(scope == frozenset({company_id}) for scope in seen), (
            "the escalation lane's own session did not carry the contact's own company "
            f"scope ({company_id!r}); it reads `Team` / `AgentTeam` to draw an assignee, so "
            f"an unscoped session draws from nothing: {seen}"
        )
