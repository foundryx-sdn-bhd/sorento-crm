"""Security guards for price tag combos (S1 to S4).

Written from the security review's five findings plus the code review's two, on
the tester's branch, against the CONTRACT rather than against an implementation
that is on the lane. Each test names the thing that goes wrong when the guard is
missing, because that is what stops it being deleted later as "defensive".

The five security findings:

* **B1** neither `price_tag_request_tags` nor `price_tag_request_lines` is
  company-scoped on its own - both hang off the request, which carries the
  partition - so a tag route that queried only those two had nothing for the
  scope predicate to attach to, and PATCH / split / DELETE all worked across
  companies. DELETE went further and rewrote the other company's saved design.
* **B2** `parts` and `combo_id` arrive from the PORTAL. `candidates` is JSONB,
  which stores any string at all, and is read back into `Product.id.in_(...)`
  where a non-UUID is a Postgres error rather than an empty result; a real id
  from another company would be stored and then resolved onto the tag.
* **S1** the "Sold with" mirror joins through combos to hosts, which is a second
  table to leak a foreign company's product code through.
* **S2** a non-UUID path id on the portal combos lookup reaches Postgres as a
  comparison it refuses - a 500 where a 404 belongs, and a 500 tells an
  anonymous caller more than a 404 does.
* **S3** `choices` was a free `{anything: anything}` write: a role the line never
  opened, or a product from another company, would be stored and resolved, which
  is how a cabinet prints a basin nobody offered.

And the two review blockers: split must resolve the request ONCE (B1 of the code
review), and the class-labels route is gated on the settings slug its only
consumer holds (S6).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.models.base import company_scope, set_company_scope
from app.models.company import Company
from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.models.user import User, UserStatus
from app.services.company_scope_resolver import apply_company_scope
from app.services.price_tag_request_service import PriceTagRequestService
from app.services.user_service import UserPermissionService

from app.models.price_tag import (  # noqa: E402
    PriceTagRequest,
    PriceTagRequestLine,
    PriceTagRequestLinePart,
    PriceTagRequestTag,
)
from app.models.product_combo import ProductCombo, ProductComboPart  # noqa: E402

from tests._pg_fixture import blank_session, unique_code

SORENTO = "00000000-0000-0000-0000-000000000001"
MOCHA = "00000000-0000-0000-0000-000000000002"

_BASE = "/api/v1/dealer-kit/price-tag-requests"
_PORTAL = "/api/v1/public/portal/submissions/price_tag_request"
_LOOKUPS = "/api/v1/public/portal/lookups"
_SOLD_WITH = "/api/v1/master-data/products/{product_id}/sold-with"
_CLASS_LABELS = "/api/v1/master-data/product-categories/class-labels"

PROCESS = "dealer_kit.price_tag_requests.process"
VIEW = "dealer_kit.price_tag_requests.view"
PRODUCTS_VIEW = "master_data.products.view"
CATEGORIES_VIEW = "master_data.product_categories.view"
SETTINGS_VIEW = "user_management.settings.view"


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seeding - CI's database is empty, so every FK target is created here
# ---------------------------------------------------------------------------


def _mocha(db) -> str:
    if db.query(Company).filter(Company.id == MOCHA).first() is None:
        db.add(
            Company(id=MOCHA, name="ZZT Mocha", code=unique_code("MCH")[:20], is_active=True)
        )
        db.flush()
    return MOCHA


def _product(db, stem: str, *, company_id: str = SORENTO, class_label=None, list_price="100.00"):
    code = unique_code(stem)
    category = ProductCategory(
        id=_uid(),
        category_code=code[:50],
        category_name=f"ZZT cat {code}",
        class_label=class_label,
    )
    brand = Brand(id=_uid(), brand_code=code[:50], brand_name=f"ZZT {code}")
    uom = UnitOfMeasure(id=_uid(), uom_code=code[:20], uom_name="Each")
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=_uid(),
        company_id=company_id,
        product_code=code,
        product_name=f"ZZT {stem}",
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=Decimal(list_price),
        is_active=True,
    )
    db.add(product)
    db.flush()
    return product


def _contact(db):
    from app.models.access import RespondContact
    from tests._portal_grant import link_contact_company, link_contact_segment, seed_segment

    contact = RespondContact(
        id=_uid(), phone_number=f"+60{uuid.uuid4().hex[:9]}", name=unique_code("contact")
    )
    db.add(contact)
    db.flush()
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    link_contact_company(db, contact.id)
    return contact


def _combo(db, host, name, parts):
    combo = ProductCombo(id=_uid(), host_product_id=host.id, name=name, sort_order=0)
    db.add(combo)
    db.flush()
    for index, (product, group) in enumerate(parts):
        db.add(
            ProductComboPart(
                id=_uid(),
                combo_id=combo.id,
                part_product_id=product.id,
                choice_group=group,
                sort_order=index,
            )
        )
    db.flush()
    return combo


def _request_with_open_tag(db, *, company_id: str, candidates: int = 2):
    """A submitted request whose one line leaves a Basin group open."""
    contact = _contact(db)
    cabinet = _product(db, "SRTBF11834", company_id=company_id, class_label="Bathroom Furniture")
    mirror = _product(db, "SRTMR502", company_id=company_id, list_price="75.00")
    basins = [
        _product(db, f"SRTBS90{i}", company_id=company_id, list_price="257.00")
        for i in range(candidates)
    ]
    combo = _combo(db, cabinet, "3 in 1", [(mirror, None)] + [(b, "Basin") for b in basins])

    request = PriceTagRequestService.submit_request(
        db,
        contact_id=contact.id,
        company_id=company_id,
        data={
            "debtor_name": "ZZT Dealer",
            # r9 D7/AC-S3-1: a submitted request has answered Printing, and the
            # revision path re-validates it.
            "print_by": "office",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": cabinet.id,
                    "combo_id": combo.id,
                    "quantity": 1,
                    "parts": [
                        {"product_id": mirror.id},
                        {"role": "Basin", "candidates": [b.id for b in basins]},
                    ],
                }
            ],
        },
    )
    db.commit()
    return request, basins


def _revision_config(db):
    """Revisions are OFF until the tenant enables them per entity type.

    `PortalRevisionService.revise` refuses outright without this row, so the two
    quantity cases below would fail on the config rather than on what they are
    about. Seeded exactly as tests/test_portal_price_tag_revise.py does.
    """
    from app.models.portal import PortalRevisionConfig

    existing = (
        db.query(PortalRevisionConfig)
        .filter(PortalRevisionConfig.source_entity_type == "price_tag_request")
        .first()
    )
    if existing is not None:
        return existing
    row = PortalRevisionConfig(
        id=_uid(),
        source_entity_type="price_tag_request",
        is_enabled=True,
        max_revisions=None,
        allowed_statuses=["new", "changes_requested"],
        restart_stage_code=None,
    )
    db.add(row)
    db.commit()
    return row


def _tags_of(db, request) -> list[PriceTagRequestTag]:
    return (
        db.query(PriceTagRequestTag)
        .join(PriceTagRequestLine, PriceTagRequestLine.id == PriceTagRequestTag.line_id)
        .filter(PriceTagRequestLine.request_id == request.id)
        .order_by(PriceTagRequestTag.sort_order)
        .all()
    )


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def _crm_client(db, monkeypatch, *, allow: set[str], company_id: str) -> TestClient:
    user = User(
        id=_uid(),
        email=f"{unique_code('sec')}@zzt.test",
        name="ZZT Security Caller",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    principal = {"id": str(user.id), "email": user.email}

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        scope = frozenset({company_id})
        set_company_scope(_db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in allow,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    # The either-grant dependency reads the whole slug set rather than asking per slug.
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: set(allow)
    )
    return TestClient(app)


def _portal_client(db, contact_id: str) -> TestClient:
    from app.api.v1.public.portal import get_portal_token
    from app.models.portal import PortalToken

    def _override_db():
        yield db

    def _override_token():
        return PortalToken(id=_uid(), contact_id=contact_id, space_id="zzt-space")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_portal_token] = _override_token
    return TestClient(app, headers={"X-Portal-Token": "zzt-token"})


# --------------------------------------------------------------------------- B1


def test_cross_company_tag_routes_404_and_change_nothing(db, monkeypatch):
    """A Sorento user cannot PATCH or DELETE Mocha's tags, or PATCH Mocha's
    line's price - and 404, not 403.

    404 rather than 403 on purpose: never confirm another company's id is
    real. The byte-for-byte check afterwards is the point of the test - the
    first version of these routes answered an error AND had already
    written. D6 (PLAN-price-tag-line-promo-combo-subject.md) retires
    `POST .../tags/{tag_id}/split` outright - that assertion moved to
    `test_price_tag_auto_split.py::test_split_route_gone_and_choices_rejected`,
    which proves the route answers 404/405 for EVERY caller, not just a
    cross-company one, so it is no longer this test's concern. D5/S11 adds
    `PATCH .../lines/{line_id}` in its place - the new line-level route this
    test now covers instead.
    """
    _mocha(db)
    request, basins = _request_with_open_tag(db, company_id=MOCHA, candidates=2)
    tag = _tags_of(db, request)[0]
    tag.marketing_price_override = Decimal("1234.00")
    tag.marketing_override_reason = "ZZT Mocha decision"
    db.commit()

    before = {
        "quantity": tag.quantity,
        "choices": dict(tag.choices or {}),
        "override": tag.marketing_price_override,
        "reason": tag.marketing_override_reason,
        "tag_count": len(_tags_of(db, request)),
        "line_promotion_id": request.lines[0].promotion_id,
    }

    client = _crm_client(db, monkeypatch, allow={VIEW, PROCESS}, company_id=SORENTO)

    patched = client.patch(
        f"{_BASE}/{request.id}/tags/{tag.id}",
        json={"quantity": 99, "marketing_price_override": "1.00"},
    )
    assert patched.status_code == 404, patched.text

    line_patched = client.patch(
        f"{_BASE}/{request.id}/lines/{request.lines[0].id}",
        json={"manual_sell_price": 1.00},
    )
    assert line_patched.status_code == 404, line_patched.text

    deleted = client.delete(f"{_BASE}/{request.id}/tags/{tag.id}")
    assert deleted.status_code == 404, deleted.text

    db.expire_all()
    after = _tags_of(db, request)
    assert len(after) == before["tag_count"]
    fresh = after[0]
    assert fresh.quantity == before["quantity"]
    assert dict(fresh.choices or {}) == before["choices"]
    assert fresh.marketing_price_override == before["override"]
    assert fresh.marketing_override_reason == before["reason"]
    db.refresh(request.lines[0])
    assert request.lines[0].promotion_id == before["line_promotion_id"]


def test_cross_company_delete_leaves_the_saved_design_untouched(db, monkeypatch):
    """DELETE rewrites the request's draft document - across companies it rewrote THEIRS.

    The 404 alone is not the assertion: the document has to come back byte for
    byte, because a doc silently stripped of a placement reads as a design
    nobody ever drew.
    """
    import json

    from app.models.dealer_kit import Page

    _mocha(db)
    request, _basins = _request_with_open_tag(db, company_id=MOCHA)
    tag = _tags_of(db, request)[0]

    doc = {
        "kind": "tag_sheet",
        "imposition": {
            "preset": "auto",
            "page_width_mm": 210,
            "page_height_mm": 297,
            "bleed_mm": 3,
            "gap_mm": 2,
        },
        "sheets": [
            {
                "id": "sheet-1",
                "tags": [
                    {
                        "id": f"{tag.id}-c0",
                        "template_id": "tpl-1",
                        "request_tag_id": tag.id,
                        "x_mm": 5,
                        "y_mm": 5,
                        "width_mm": 95,
                        "height_mm": 44.5,
                        "layers": [],
                    }
                ],
            }
        ],
    }
    page = Page(
        id=_uid(),
        name="ZZT Mocha sheet",
        slug=unique_code("mocha-sheet"),
        kind="tag_sheet",
        request_id=request.id,
        company_id=MOCHA,
        draft_doc=doc,
    )
    db.add(page)
    db.commit()
    before = json.dumps(page.draft_doc, sort_keys=True)

    client = _crm_client(db, monkeypatch, allow={VIEW, PROCESS}, company_id=SORENTO)
    assert client.delete(f"{_BASE}/{request.id}/tags/{tag.id}").status_code == 404

    db.expire_all()
    # The request left the SORENTO scope on this session, and `page` is
    # company-owned - so an unscoped read-back finds nothing and `.one()` raises
    # before the comparison it exists for. Read Mocha's row as Mocha. That is the
    # harness, not the guard: what is under test is the document's CONTENT, and
    # the 404 above is what proves the caller could not reach it.
    with company_scope(db, frozenset({MOCHA})):
        fresh = db.query(Page).filter(Page.id == page.id).one()
        after = json.dumps(fresh.draft_doc, sort_keys=True)
    assert after == before


# --------------------------------------------------------------------------- B2


def test_portal_parts_refuse_a_non_uuid_candidate(db):
    """`candidates` is JSONB, so a junk string stores happily and explodes on read.

    `Product.id.in_(...)` with a non-UUID is a Postgres ERROR, not an empty
    result, so the row would take down every later read of that request. The
    refusal names the row, because the form puts the message on it.
    """
    contact = _contact(db)
    cabinet = _product(db, "SRTBF11834", class_label="Bathroom Furniture")
    # PLAN-price-tag-ai-extract-resolver.md D5: a line's `parts` are only
    # even considered once the product carries at least one `ProductCombo` -
    # without one, that guard would refuse this line before the non-uuid
    # candidate this test is actually about is ever reached.
    _combo(db, cabinet, "combo", [(_product(db, "SRTMR-FIXED"), None)])
    client = _portal_client(db, contact.id)

    response = client.post(
        _PORTAL,
        json={
            "debtor_name": "ZZT Dealer",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": cabinet.id,
                    "parts": [{"role": "basin", "candidates": ["not-a-uuid"]}],
                }
            ],
        },
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "INVALID_PART"
    assert body["detail"] == "line:0", "the form puts the message on the row"
    assert db.query(PriceTagRequestLinePart).count() == 0, "nothing may be written"


def test_portal_parts_refuse_an_unknown_and_a_foreign_candidate(db):
    """A well-formed uuid is not enough: it has to be a product THIS caller can see.

    The two cases are one query - another company's product simply is not
    returned by a scoped read - but they are asserted separately because they
    fail for different reasons and a future refactor could fix one and not the
    other.
    """
    _mocha(db)
    contact = _contact(db)
    cabinet = _product(db, "SRTBF11834", class_label="Bathroom Furniture")
    # PLAN-price-tag-ai-extract-resolver.md D5: a combo has to exist before
    # `parts` is even considered, or this line would be refused for that
    # reason before the unknown/foreign candidate this test is about.
    _combo(db, cabinet, "combo", [(_product(db, "SRTMR-FIXED"), None)])
    theirs = _product(db, "MOCHA-BASIN", company_id=MOCHA)
    client = _portal_client(db, contact.id)

    def _submit(candidate_id: str):
        return client.post(
            _PORTAL,
            json={
                "debtor_name": "ZZT Dealer",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": cabinet.id,
                        "parts": [{"role": "Basin", "candidates": [candidate_id]}],
                    }
                ],
            },
        )

    unknown = _submit(_uid())
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["code"] == "INVALID_PART"

    foreign = _submit(theirs.id)
    assert foreign.status_code == 422, foreign.text
    assert foreign.json()["code"] == "INVALID_PART"

    assert db.query(PriceTagRequestLinePart).count() == 0


def test_a_combo_from_another_host_is_stored_as_null(db):
    """A combo id that is not this product's is dropped, not obeyed and not refused.

    Obeying it would price and print somebody else's package on this cabinet.
    Refusing it would break warn-and-allow, which the whole slice is built on -
    so it lands as "No package chosen" on the row instead.
    """
    contact = _contact(db)
    cabinet = _product(db, "SRTBF11834", class_label="Bathroom Furniture")
    other_host = _product(db, "SRTBF99999", class_label="Bathroom Furniture")
    mirror = _product(db, "SRTMR502")
    # The cabinet gets a combo of its OWN, so the warning that lands afterwards
    # is "No package chosen" - which can only be true if the foreign combo_id was
    # nulled. Without it the host would warn "No package defined" whatever was
    # sent (D2's first branch), and the test would pass without proving anything
    # about the foreign id.
    _combo(db, cabinet, "3 in 1", [(mirror, None)])
    foreign_combo = _combo(db, other_host, "Somebody else's 3 in 1", [(mirror, None)])
    client = _portal_client(db, contact.id)

    created = client.post(
        _PORTAL,
        json={
            "debtor_name": "ZZT Dealer",
            # r9 D7/AC-S3-1: Printing has no default and submit refuses without
            # it, so a request that is meant to reach `submitted` answers it.
            "print_by": "office",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": cabinet.id,
                    "combo_id": foreign_combo.id,
                    "parts": [],
                }
            ],
        },
    )
    assert created.status_code == 201, created.text

    submitted = client.post(f"{_PORTAL}/{created.json()['id']}/submit")
    assert submitted.status_code == 200, submitted.text

    db.expire_all()
    line = (
        db.query(PriceTagRequestLine)
        .filter(PriceTagRequestLine.request_id == created.json()["id"])
        .one()
    )
    assert line.combo_id is None
    assert line.package_warning == "No package chosen"


def test_every_read_still_answers_after_a_valid_parts_submit(db, monkeypatch):
    """The four readers of a line with parts all answer 200, not 500.

    The point of B2: a bad part id is a Postgres error on the way OUT, so the
    damage shows up in the portal detail, the CRM detail, resolve-prices and the
    print payload rather than at the write. A valid submit has to leave all four
    working.

    D6: `_request_with_open_tag`'s default (2 Basin candidates) auto-splits
    into 2 tags at submit, not the 1 this test used to expect - the line
    itself still carries the same parts either way, which is what this test
    is actually about.
    """
    request, _basins = _request_with_open_tag(db, company_id=SORENTO)
    contact_id = request.contact_id

    portal = _portal_client(db, contact_id)
    detail = portal.get(f"{_PORTAL}/{request.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["lines"][0]["parts"], "the portal read view shows the parts"
    app.dependency_overrides.clear()

    crm = _crm_client(db, monkeypatch, allow={VIEW, PROCESS}, company_id=SORENTO)

    crm_detail = crm.get(f"{_BASE}/{request.id}")
    assert crm_detail.status_code == 200, crm_detail.text

    resolved = crm.post(f"{_BASE}/{request.id}/resolve-prices", json=None)
    assert resolved.status_code == 200, resolved.text
    assert len(resolved.json()) == 2, "D6: one row per auto-split tag"

    from app.services.dealer_kit import tag_data_service

    rows = tag_data_service.resolve_request_line_data(db, request)
    assert len(rows) == 2
    assert all(row["parts"] for row in rows), "the print payload resolves the parts"


def test_a_package_warning_never_names_a_uuid(db):
    """The warning is read by a salesperson, so it names CODES - and only codes.

    A part whose product falls out of scope must not degrade into "Missing:
    <uuid>": that is both unreadable and an id leak onto a screen the UAC says
    carries no ids at all (AC-X-2).
    """
    import re

    contact = _contact(db)
    cabinet = _product(db, "SRTBF11834", class_label="Bathroom Furniture")
    mirror = _product(db, "SRTMR502")
    basin = _product(db, "SRTBS900")
    combo = _combo(db, cabinet, "3 in 1", [(mirror, None), (basin, "Basin")])

    request = PriceTagRequestService.submit_request(
        db,
        contact_id=contact.id,
        company_id=SORENTO,
        data={
            "debtor_name": "ZZT Dealer",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": cabinet.id,
                    "combo_id": combo.id,
                    "parts": [],
                }
            ],
        },
    )
    db.flush()

    warning = request.lines[0].package_warning or ""
    assert warning, "a combo with nothing answered must warn"
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        warning,
        re.IGNORECASE,
    ), f"the warning leaked an id: {warning}"
    assert mirror.product_code in warning or "Basin" in warning


# --------------------------------------------------------------------------- S1


def test_sold_with_never_names_another_companys_host(db, monkeypatch):
    """The mirror joins combos to hosts, which is a second path to a foreign code.

    The part is shared by both companies' catalogues here, so the only thing
    keeping Mocha's cabinet off a Sorento reader's screen is the scope on the
    host lookup.
    """
    _mocha(db)
    basin = _product(db, "SRTBS900", company_id=SORENTO)
    mine = _product(db, "SRTBF11834", company_id=SORENTO)
    theirs = _product(db, "MOCHA-CAB", company_id=MOCHA)
    _combo(db, mine, "3 in 1", [(basin, "Basin")])
    _combo(db, theirs, "Mocha 2 in 1", [(basin, "Basin")])

    client = _crm_client(db, monkeypatch, allow={PRODUCTS_VIEW}, company_id=SORENTO)
    response = client.get(_SOLD_WITH.format(product_id=basin.id))

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    codes = {row["host_code"] for row in rows}
    assert mine.product_code in codes
    assert theirs.product_code not in codes, "a foreign host leaked through the mirror"
    assert theirs.id not in {row["host_product_id"] for row in rows}
    assert theirs.product_code not in response.text


# --------------------------------------------------------------------------- S2


def test_portal_combos_lookup_404s_on_a_non_uuid(db):
    """A junk path id is a 404, never a 500.

    Postgres refuses `uuid = 'abc'` outright, so the comparison raises rather
    than returning nothing - and a 500 to an unauthenticated-ish caller says
    more about the stack than a 404 does.
    """
    contact = _contact(db)
    client = _portal_client(db, contact.id)

    response = client.get(f"{_LOOKUPS}/product-combos/abc")
    assert response.status_code == 404, response.text

    # A well-formed id that simply is not there answers the same way.
    missing = client.get(f"{_LOOKUPS}/product-combos/{_uid()}")
    assert missing.status_code == 404, missing.text


def test_portal_combos_lookup_404s_on_another_companys_product(db):
    """Scope is what makes the 404 above meaningful for a REAL id."""
    _mocha(db)
    contact = _contact(db)
    theirs = _product(db, "MOCHA-CAB", company_id=MOCHA)
    client = _portal_client(db, contact.id)

    response = client.get(f"{_LOOKUPS}/product-combos/{theirs.id}")
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- S3


# D6 (PLAN-price-tag-line-promo-combo-subject.md, owner ruling): Split and
# Pick one are retired outright - a line's open choice group auto-splits into
# one tag per candidate at submit (`_add_line_tags`), so there is no
# "one tag, still open" state left for either action to reach.
# `test_pick_one_refuses_a_role_the_line_never_opened`,
# `test_pick_one_refuses_a_product_outside_the_candidates` and
# `test_pick_one_accepts_a_real_candidate_and_stores_it` all exercised
# `choices` on `PATCH .../tags/{tag_id}`, a field this revision refuses
# outright; `test_split_resolves_the_request_once` (review B1) benchmarked
# the now-gone `POST .../tags/{tag_id}/split` route. The replacement
# coverage: `test_price_tag_auto_split.py::test_split_route_gone_and_choices_rejected`
# proves the route is gone and `choices` is refused, and
# `test_price_tag_auto_split.py::test_one_open_group_makes_n_tags`/
# `test_two_open_groups_cartesian` prove the auto-split shape (N candidates
# -> N tags, one resolve for the whole line) these tests used to pin by hand.


# --------------------------------------------------------------------------- review S6


def test_class_labels_gated_on_settings_view(db, monkeypatch):
    """The class-labels picker is gated on the slug its only consumer holds.

    Its one reader is the System Settings guarded-classes multi-select. Gated on
    the CATEGORY slug instead, a settings admin with no category permission
    would open that page to an empty picker and have no way to tell it apart
    from "nothing is configured" - and a category reader with no settings
    access would be handed a vocabulary that is not theirs.
    """
    _product(db, "SRTBF11834", class_label="Bathroom Furniture")

    denied = _crm_client(db, monkeypatch, allow={CATEGORIES_VIEW}, company_id=SORENTO)
    refused = denied.get(_CLASS_LABELS)
    assert refused.status_code == 403, refused.text
    app.dependency_overrides.clear()

    allowed = _crm_client(db, monkeypatch, allow={SETTINGS_VIEW}, company_id=SORENTO)
    response = allowed.get(_CLASS_LABELS)
    assert response.status_code == 200, response.text
    assert "Bathroom Furniture" in str(response.json())


def test_ac_s3_3_class_labels_readable_by_a_spec_or_product_reader(db, monkeypatch):
    """#1286 AC-S3.3: the Product Specifications list counts Product class's choices
    from this list, so a spec or product reader without the settings grant gets it
    too. A category-only reader is still refused (the test above)."""
    _product(db, "SRTBF11834", class_label="Bathroom Furniture")

    for grant in ("master_data.spec_registry.view", PRODUCTS_VIEW):
        client = _crm_client(db, monkeypatch, allow={grant}, company_id=SORENTO)
        response = client.get(_CLASS_LABELS)
        assert response.status_code == 200, (grant, response.text)
        assert "Bathroom Furniture" in str(response.json())
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- review B1 (quantity)


def test_revise_carries_the_quantity_onto_a_single_tag(db):
    """A quantity change on the line follows onto its one tag.

    The tag's quantity is seeded from the line's at submit; a salesperson
    revising 1 to 9 has changed how many tags they want, and a tag left at 1
    prints one. `candidates=1`: D6 auto-splits a real open group into one
    tag PER candidate, so a single-candidate group is what still leaves the
    line with exactly one tag to carry the new quantity onto.
    """
    from app.models.portal import PortalToken
    from app.services.portal_revision_service import PortalRevisionService

    _revision_config(db)
    request, _basins = _request_with_open_tag(db, company_id=SORENTO, candidates=1)
    line = request.lines[0]
    product_id = line.product_id
    assert _tags_of(db, request)[0].quantity == 1

    token = PortalToken(id=_uid(), contact_id=request.contact_id, space_id="zzt-space")
    PortalRevisionService(db).revise(
        token,
        "price_tag_request",
        str(request.id),
        {"products": [{"product_id": product_id, "quantity": 9}]},
        "Reason",
        request.revision_no or 0,
    )
    db.expire_all()

    fresh = PriceTagRequestService.get_request(db, str(request.id))
    fresh_line = fresh.lines[0]
    assert fresh_line.quantity == 9
    assert len(fresh_line.tags) == 1
    assert fresh_line.tags[0].quantity == 9


def test_revise_leaves_a_split_lines_per_tag_quantities_alone(db):
    """Marketing's own numbers survive a revision of the line they hang off.

    Once a line has more than one tag, each carries a quantity marketing set
    for THAT candidate. Overwriting them from the line's single number would
    undo that work on every remark the salesperson fixes. D6: the two tags
    already exist (auto-split off the one open Basin group at submit) - this
    sets marketing's own quantities on the EXISTING pair rather than
    building a second, duplicate pair by hand.
    """
    from app.models.portal import PortalToken
    from app.services.portal_revision_service import PortalRevisionService

    _revision_config(db)
    request, basins = _request_with_open_tag(db, company_id=SORENTO)
    line = request.lines[0]
    product_id = line.product_id

    tags = _tags_of(db, request)
    assert len(tags) == 2, "D6: auto-split off the one open Basin group"
    tags[0].quantity = 2
    tags[1].quantity = 3
    db.commit()

    token = PortalToken(id=_uid(), contact_id=request.contact_id, space_id="zzt-space")
    PortalRevisionService(db).revise(
        token,
        "price_tag_request",
        str(request.id),
        {"products": [{"product_id": product_id, "quantity": 7}]},
        "Reason",
        request.revision_no or 0,
    )
    db.expire_all()

    fresh = PriceTagRequestService.get_request(db, str(request.id))
    fresh_line = fresh.lines[0]
    tags = sorted(fresh_line.tags, key=lambda t: t.sort_order or 0)
    assert len(tags) == 2, "the split survives the revision"
    assert [t.quantity for t in tags] == [2, 3], "per-tag quantities are marketing's"
