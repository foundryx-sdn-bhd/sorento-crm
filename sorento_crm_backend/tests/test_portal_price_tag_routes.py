"""The portal's price tag routes, through the app rather than the service (D49).

The service-level suite in ``test_price_tag_request.py`` was green while every
write path was dead, because the app mounts ``portal.router`` and
``portal_price_tag.router`` under the same ``/portal`` prefix and Starlette serves
the FIRST route whose path matches. ``POST /submissions/{kind}`` in the generic
portal module matched ``POST /submissions/price_tag_request``, ``price_tag_request``
is in ``SUPPORTED_TYPES`` so the kind check waved it through, and the salesperson
got a 422 about ``body.fields`` - a key belonging to a different form's schema.

So these tests go through ``TestClient``: mounting order is not something a
service call can prove.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402
from tests._pg_fixture import blank_session, unique_code
from tests import _ptag_r9_seed

_BASE = "/api/v1/public/portal/submissions/price_tag_request"
_SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"


def _seed_contact_who_can_see_the_form(db: Session) -> str:
    """A contact whose market segment grants ``price_tag_request``
    (PLAN-portal-forms-market-segment D1: the grant moved off access types)."""
    from app.models.access import RespondContact
    from tests._portal_grant import link_contact_company, link_contact_segment, seed_segment

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=unique_code("contact"),
    )
    db.add(contact)
    db.flush()
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    link_contact_company(db, contact.id)
    return contact.id


def _seed_product(db: Session, *, class_label: str = "Kitchen Sink") -> str:
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    category = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("cat"),
        category_name=unique_code("Category"),
        class_label=class_label,
    )
    brand = Brand(
        id=str(uuid.uuid4()),
        brand_code=unique_code("br"),
        brand_name=unique_code("Brand"),
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("uom"), uom_name="Each")
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=str(uuid.uuid4()),
        product_code=unique_code("prod"),
        product_name=unique_code("Product"),
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=100.00,
    )
    db.add(product)
    db.flush()
    return product.id


@pytest.fixture
def client():
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken

    with blank_session() as db:
        contact_id = _seed_contact_who_can_see_the_form(db)

        def _override_get_db():
            yield db

        def _override_portal_token():
            # Never added to the session: the routes read contact_id off it and
            # nothing persists it.
            return PortalToken(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                space_id="zzt-space",
            )

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_portal_token] = _override_portal_token
        try:
            # The header is what the company-scope resolver reads to give a portal
            # request the incumbent company. Without it every owned-table READ is
            # fail-closed and a request that was just created comes back 404.
            with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as c:
                yield c, db, contact_id
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# The shadowing regression
# ---------------------------------------------------------------------------


class TestTheRouteThatServesTheRequest:
    def test_create_reaches_the_price_tag_route(self, client):
        """The payload the form actually posts, with no ``fields`` key in sight."""
        c, db, _ = client
        product_id = _seed_product(db)

        res = c.post(
            _BASE,
            json={
                "debtor_code": "ZZT-D1",
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "notes": "ZZT",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": product_id,
                        "quantity": 2,
                    }
                ],
            },
        )

        assert res.status_code == 201, res.text
        body = res.json()
        assert body["doc_number"].startswith("PT-")
        assert body["portal_draft_at"] is not None
        assert len(body["lines"]) == 1

    def test_a_draft_needs_neither_a_debtor_nor_a_date(self, client):
        """D48a: the only requirement is that there is something to save."""
        c, db, _ = client
        product_id = _seed_product(db)

        res = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        )

        assert res.status_code == 201, res.text
        body = res.json()
        assert body["debtor_name"] is None
        assert body["needed_by_date"] is None

    def test_the_detail_route_answers_with_the_lines_resolved(self, client):
        """Reopening a draft: the row stores an id, the form needs a code and a name."""
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        res = c.get(f"{_BASE}/{created['id']}")

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["id"] == created["id"]
        assert body["lines"][0]["code"]
        assert body["lines"][0]["name"]
        # The portal form reads this unconditionally.
        assert body["attachments"] == []

    def test_the_detail_route_hides_another_contacts_request(self, client):
        c, db, _ = client
        from app.services.price_tag_request_service import PriceTagRequestService

        other = _seed_contact_who_can_see_the_form(db)
        theirs = PriceTagRequestService.create_request(
            db,
            contact_id=other,
            company_id=_SORENTO_COMPANY_ID,
            data={"debtor_name": "ZZT Theirs"},
        )
        db.flush()

        assert c.get(f"{_BASE}/{theirs.id}").status_code == 404

    def test_update_keeps_the_draft_and_replaces_its_lines(self, client):
        c, db, _ = client
        first = _seed_product(db)
        second = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": first}]},
        ).json()

        res = c.put(
            f"{_BASE}/{created['id']}",
            json={
                "debtor_name": "ZZT Filled In Later",
                "lines": [{"line_type": "product", "product_id": second, "quantity": 4}],
            },
        )

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["id"] == created["id"]
        assert body["debtor_name"] == "ZZT Filled In Later"
        assert len(body["lines"]) == 1
        assert body["lines"][0]["product_id"] == second
        assert body["lines"][0]["quantity"] == 4

    def test_lines_keep_the_order_they_were_posted_in(self, client):
        """The row a refusal names (`line:<index>`) must be the row on screen.

        The form posts the table in order and sends no `sort_order`; a schema
        default of 0 gave every line the same one and the order came back at
        Postgres's discretion.
        """
        c, db, _ = client
        first = _seed_product(db)
        second = _seed_product(db)
        third = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "lines": [
                    {"line_type": "product", "product_id": first},
                    {"line_type": "product", "product_id": second},
                    {"line_type": "product", "product_id": third},
                ]
            },
        ).json()

        body = c.get(f"{_BASE}/{created['id']}").json()

        assert [l["product_id"] for l in body["lines"]] == [first, second, third]
        assert [l["sort_order"] for l in body["lines"]] == [0, 1, 2]

    def test_a_draft_can_be_deleted(self, client):
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        assert c.delete(f"{_BASE}/{created['id']}").status_code == 204
        assert c.get(f"{_BASE}/{created['id']}").status_code == 404


# ---------------------------------------------------------------------------
# R1 (Phase 3 browser finding): D6 auto-split through the REAL portal route,
# not the service directly (test_price_tag_auto_split.py drives
# `PriceTagRequestService.submit_request`, which is not the seam the portal
# create+submit routes use - `create_request` then `portal_submit_price_tag_request`
# flip `portal_draft_at`/`status` without rebuilding lines at all).
# ---------------------------------------------------------------------------


def _combo_with_open_basin(db, cabinet_id: str) -> tuple[str, str]:
    """A combo on `cabinet_id` with ONE open 2-candidate Basin group."""
    from app.models.product_combo import ProductCombo, ProductComboPart

    white = _seed_product(db)
    black = _seed_product(db)
    combo = ProductCombo(id=str(uuid.uuid4()), host_product_id=cabinet_id, name="2 in 1", sort_order=0)
    db.add(combo)
    db.flush()
    for index, candidate_id in enumerate((white, black)):
        db.add(
            ProductComboPart(
                id=str(uuid.uuid4()),
                combo_id=combo.id,
                part_product_id=candidate_id,
                choice_group="Basin",
                sort_order=index,
            )
        )
    db.flush()
    return combo.id, white, black


class TestAutoSplitThroughThePortalRoute:
    def test_create_then_submit_auto_splits_the_open_group(self, client):
        c, db, _ = client
        cabinet_id = _seed_product(db)
        combo_id, white, black = _combo_with_open_basin(db, cabinet_id)

        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "print_by": "office",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": cabinet_id,
                        "combo_id": combo_id,
                        "parts": [{"role": "Basin", "candidates": [white, black]}],
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        request_id = created.json()["id"]

        submitted = c.post(f"{_BASE}/{request_id}/submit")
        assert submitted.status_code == 200, submitted.text

        detail = c.get(f"{_BASE}/{request_id}").json()
        tags = detail["lines"][0]["tags"]
        assert len(tags) == 2, "D6: one tag per Basin candidate, through the route"
        assert all(tag["choices"] for tag in tags), "no tag is left with empty choices"

    def _ui_payload(self, *, sink_id, combo_id, drain_id, tap_a, tap_b, promotion_id):
        """The EXACT shape the real portal UI POSTs (browser pass 2 HAR)."""
        return {
            "debtor_code": "ZZT-CUST01",
            "debtor_name": "ZZT Dealer Customer",
            "needed_by_date": None,
            "notes": None,
            "price_mode": "selling",
            "print_by": "office",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": sink_id,
                    "product_set_id": None,
                    "combo_id": combo_id,
                    "quantity": 1,
                    "included_accessories": None,
                    "remarks": None,
                    "product_class": None,
                    "parts": [
                        {"product_id": drain_id, "role": None, "candidates": []},
                        {
                            "product_id": None,
                            "role": "Kitchen Tap",
                            "candidates": [tap_a, tap_b],
                        },
                    ],
                    "promotion_id": promotion_id,
                    "manual_sell_price": None,
                }
            ],
        }

    def _sink_combo_and_promo(self, db, contact_id):
        """Sink host, a FIXED drain part, an OPEN Kitchen Tap group, a
        promotion covering the sink, and the contact granted `dealer` -
        every ingredient the real browser walk's payload names."""
        from app.models.product_combo import ProductCombo, ProductComboPart

        sink_id = _seed_product(db)
        drain_id = _seed_product(db)
        tap_a = _seed_product(db)
        tap_b = _seed_product(db)
        combo = ProductCombo(
            id=str(uuid.uuid4()), host_product_id=sink_id, name="Sink + Tap", sort_order=0
        )
        db.add(combo)
        db.flush()
        db.add(
            ProductComboPart(
                id=str(uuid.uuid4()),
                combo_id=combo.id,
                part_product_id=drain_id,
                choice_group=None,
                sort_order=0,
            )
        )
        for index, candidate_id in enumerate((tap_a, tap_b)):
            db.add(
                ProductComboPart(
                    id=str(uuid.uuid4()),
                    combo_id=combo.id,
                    part_product_id=candidate_id,
                    choice_group="Kitchen Tap",
                    sort_order=index + 1,
                )
            )
        db.flush()
        _grant_promotion_audience_code(db, contact_id, "dealer")
        promotion_id = _seed_promotion_for(db, sink_id, access_levels=["dealer"])
        return sink_id, combo.id, drain_id, tap_a, tap_b, promotion_id

    def test_create_with_the_ui_payload_shape_then_submit_auto_splits(self, client):
        """T1 (browser pass 2): the real portal UI's exact POST body - Selling
        mode, a line `promotion_id`, `combo_id` set, `product_class: None`,
        a FIXED part with `role: None` ahead of the open group - produced
        ONE tag with empty `choices` in the browser, twice.

        This is unit-for-unit that shape, and it PASSES: 2 tags, both after
        create and after submit. The 4 bisect tests below it (dropping
        `promotion_id`, `role: ""` instead of `null`, dropping the fixed
        part, and going through blank-draft-then-PUT-then-submit - the
        plausible read of "created two requests" if a Save Draft ran first)
        all pass too. No field or sequencing tried here reproduces the
        browser finding - kept as regression coverage for the exact
        contract shape; the coder needs either the real request/product ids
        from the browser session's own database rows, or a repeat capture
        with network-level request/response bodies (not just the outgoing
        POST) to find where the two disagree."""
        c, db, contact_id = client
        sink_id, combo_id, drain_id, tap_a, tap_b, promotion_id = (
            self._sink_combo_and_promo(db, contact_id)
        )

        created = c.post(
            _BASE,
            json=self._ui_payload(
                sink_id=sink_id,
                combo_id=combo_id,
                drain_id=drain_id,
                tap_a=tap_a,
                tap_b=tap_b,
                promotion_id=promotion_id,
            ),
        )
        assert created.status_code == 201, created.text
        request_id = created.json()["id"]

        after_create = c.get(f"{_BASE}/{request_id}").json()
        create_tags = after_create["lines"][0]["tags"]
        assert len(create_tags) == 2, (
            "D6: one tag per Kitchen Tap candidate, right after create",
            create_tags,
        )
        assert all(tag["choices"] for tag in create_tags), "no tag left with empty choices"

        submitted = c.post(f"{_BASE}/{request_id}/submit")
        assert submitted.status_code == 200, submitted.text

        after_submit = c.get(f"{_BASE}/{request_id}").json()
        submit_tags = after_submit["lines"][0]["tags"]
        assert len(submit_tags) == 2, ("still 2 tags after submit", submit_tags)
        assert all(tag["choices"] for tag in submit_tags)

    def test_ui_payload_without_the_line_promotion_id_still_auto_splits(self, client):
        """T1 bisect (1/3): same shape, `promotion_id` dropped."""
        c, db, contact_id = client
        sink_id, combo_id, drain_id, tap_a, tap_b, _promotion_id = (
            self._sink_combo_and_promo(db, contact_id)
        )

        payload = self._ui_payload(
            sink_id=sink_id,
            combo_id=combo_id,
            drain_id=drain_id,
            tap_a=tap_a,
            tap_b=tap_b,
            promotion_id=None,
        )
        created = c.post(_BASE, json=payload)
        assert created.status_code == 201, created.text
        detail = c.get(f"{_BASE}/{created.json()['id']}").json()
        tags = detail["lines"][0]["tags"]
        assert len(tags) == 2, ("without promotion_id", tags)

    def test_ui_payload_with_role_empty_string_instead_of_null_still_auto_splits(
        self, client
    ):
        """T1 bisect (2/3): same shape, the FIXED drain part's `role` sent as
        `""` (what the create/PUT schemas normally carry) instead of `None`
        (what the HAR actually showed)."""
        c, db, contact_id = client
        sink_id, combo_id, drain_id, tap_a, tap_b, promotion_id = (
            self._sink_combo_and_promo(db, contact_id)
        )

        payload = self._ui_payload(
            sink_id=sink_id,
            combo_id=combo_id,
            drain_id=drain_id,
            tap_a=tap_a,
            tap_b=tap_b,
            promotion_id=promotion_id,
        )
        payload["lines"][0]["parts"][0]["role"] = ""
        created = c.post(_BASE, json=payload)
        assert created.status_code == 201, created.text
        detail = c.get(f"{_BASE}/{created.json()['id']}").json()
        tags = detail["lines"][0]["tags"]
        assert len(tags) == 2, ("role '' instead of null", tags)

    def test_ui_payload_without_the_fixed_drain_part_still_auto_splits(self, client):
        """T1 bisect (3/3): same shape, the FIXED drain part dropped entirely
        - only the OPEN Kitchen Tap group remains on `parts`."""
        c, db, contact_id = client
        sink_id, combo_id, _drain_id, tap_a, tap_b, promotion_id = (
            self._sink_combo_and_promo(db, contact_id)
        )

        payload = self._ui_payload(
            sink_id=sink_id,
            combo_id=combo_id,
            drain_id=None,
            tap_a=tap_a,
            tap_b=tap_b,
            promotion_id=promotion_id,
        )
        payload["lines"][0]["parts"] = [
            {"product_id": None, "role": "Kitchen Tap", "candidates": [tap_a, tap_b]}
        ]
        created = c.post(_BASE, json=payload)
        assert created.status_code == 201, created.text
        detail = c.get(f"{_BASE}/{created.json()['id']}").json()
        tags = detail["lines"][0]["tags"]
        assert len(tags) == 2, ("no fixed part at all", tags)

    def test_ui_payload_via_blank_draft_then_put_then_submit_still_auto_splits(
        self, client
    ):
        """T1 bisect (4/4, sequencing not field shape): the real portal form
        calls `createRequest` only when it has no `effectiveId` yet - a prior
        "Save Draft" (or an id already assigned for another reason) routes
        Submit through `updateRequest` (PUT) instead, which is plausibly what
        "created two requests" actually did. Reproduces that two-step shape:
        a bare draft (product only, no combo/parts/promotion) POSTed first,
        then the full UI payload PUT onto it, then submit."""
        c, db, contact_id = client
        sink_id, combo_id, drain_id, tap_a, tap_b, promotion_id = (
            self._sink_combo_and_promo(db, contact_id)
        )

        draft = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": sink_id}]},
        )
        assert draft.status_code == 201, draft.text
        request_id = draft.json()["id"]

        updated = c.put(
            f"{_BASE}/{request_id}",
            json=self._ui_payload(
                sink_id=sink_id,
                combo_id=combo_id,
                drain_id=drain_id,
                tap_a=tap_a,
                tap_b=tap_b,
                promotion_id=promotion_id,
            ),
        )
        assert updated.status_code == 200, updated.text

        submitted = c.post(f"{_BASE}/{request_id}/submit")
        assert submitted.status_code == 200, submitted.text

        detail = c.get(f"{_BASE}/{request_id}").json()
        tags = detail["lines"][0]["tags"]
        assert len(tags) == 2, ("blank draft, then PUT the full shape, then submit", tags)

    def test_put_updating_the_drafts_lines_still_auto_splits(self, client):
        c, db, _ = client
        cabinet_id = _seed_product(db)
        combo_id, white, black = _combo_with_open_basin(db, cabinet_id)

        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": cabinet_id}]},
        ).json()

        updated = c.put(
            f"{_BASE}/{created['id']}",
            json={
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": cabinet_id,
                        "combo_id": combo_id,
                        "parts": [{"role": "Basin", "candidates": [white, black]}],
                    }
                ],
            },
        )
        assert updated.status_code == 200, updated.text

        detail = c.get(f"{_BASE}/{created['id']}").json()
        tags = detail["lines"][0]["tags"]
        assert len(tags) == 2, "D6: one tag per Basin candidate, through PUT"
        assert all(tag["choices"] for tag in tags)


# ---------------------------------------------------------------------------
# R2 (Phase 3 security H1 / reviewer S1): `_add_lines` gates promotion
# validation and pricing behind `if product_id:` - a `product_set` line
# (product_id null, product_set_id set) skips that whole block outright, so
# a promotion outside the contact's audience is never refused, and a covering
# one never prices the set as SP.
# ---------------------------------------------------------------------------


def _seed_product_set(db: Session) -> tuple[str, str]:
    """Returns (product_set_id, member_product_id)."""
    from app.models.product_set import ProductSet, ProductSetMember

    member_id = _seed_product(db)
    product_set = ProductSet(
        id=str(uuid.uuid4()), set_code=unique_code("set"), name=unique_code("ZZT Set"),
    )
    db.add(product_set)
    db.flush()
    db.add(
        ProductSetMember(
            id=str(uuid.uuid4()), product_set_id=product_set.id, product_id=member_id,
            quantity=1, contributes_to_price=True, sort_order=0,
        )
    )
    db.flush()
    return product_set.id, member_id


def _seed_promotion_for(db: Session, product_id: str, *, access_levels: list[str]) -> str:
    from decimal import Decimal

    from app.models.marketing import Promotion, PromotionGroup, PromotionProduct

    promotion = Promotion(
        id=str(uuid.uuid4()), description=unique_code("ZZT promo"), is_active=True,
        access_levels=access_levels, company_id=_SORENTO_COMPANY_ID,
    )
    db.add(promotion)
    db.flush()
    group = PromotionGroup(promotion_id=promotion.id, group_name="ZZT group", sort_order=0)
    db.add(group)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()), promotion_id=promotion.id, promotion_group_id=str(group.id),
            product_id=product_id, promo_selling_price=Decimal("80.00"),
            company_id=_SORENTO_COMPANY_ID,
        )
    )
    db.flush()
    return promotion.id


class TestSetLinePromotionGate:
    def test_create_refuses_a_set_lines_promotion_outside_the_audience(self, client):
        c, db, contact_id = client
        set_id, member_id = _seed_product_set(db)
        promotion_id = _seed_promotion_for(db, member_id, access_levels=["some-other-audience"])

        res = c.post(
            _BASE,
            json={
                "price_mode": "selling",
                "lines": [
                    {
                        "line_type": "product_set",
                        "product_set_id": set_id,
                        "promotion_id": promotion_id,
                    }
                ],
            },
        )
        assert res.status_code == 422, res.text
        assert res.json().get("detail") == "line:0", res.text

    def test_a_covered_set_line_in_selling_mode_prices_as_promotion(self, client):
        c, db, contact_id = client
        set_id, member_id = _seed_product_set(db)
        _grant_promotion_audience_code(db, contact_id, "dealer")
        promotion_id = _seed_promotion_for(db, member_id, access_levels=["dealer"])

        res = c.post(
            _BASE,
            json={
                "price_mode": "selling",
                "lines": [
                    {
                        "line_type": "product_set",
                        "product_set_id": set_id,
                        "promotion_id": promotion_id,
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        line = res.json()["lines"][0]
        assert line["sell_price_basis"] == "promotion", line
        assert line["show_promo_price"] is True, line


# ---------------------------------------------------------------------------
# Submit is where completeness is enforced (D48a / D48b)
# ---------------------------------------------------------------------------


class TestSubmitRefusals:
    def test_submit_refuses_an_empty_draft_and_names_every_field(self, client):
        c, _db, _ = client
        # r9 D7: `print_by` is answered so this still tests the COMPLETENESS
        # list - the print guard fires first and would otherwise shadow it.
        created = c.post(
            _BASE, json={"notes": "ZZT nothing else", "print_by": "office"}
        ).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 422, res.text
        body = res.json()
        assert body["code"] == "SUBMIT_INCOMPLETE"
        # needed_by_date is optional (D-P2b) - dropped from what "complete" requires.
        assert body["detail"] == "debtor_name,lines"

    def test_submit_warns_about_an_unpackaged_guarded_line_and_still_submits(self, client):
        """Was a `SET_GUARD_VIOLATION` 422 naming `line:1` (AC-S2-7).

        Through the route rather than the service, because what this case has
        always been about is the ROUTE's answer: the salesperson is told on the
        row, and the row is now a warning they can send anyway.
        """
        c, db, _ = client
        ok_product = _seed_product(db, class_label="Accessories")
        bad_product = _seed_product(db, class_label="Bathroom Furniture")
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "lines": [
                    {"line_type": "product", "product_id": ok_product},
                    {"line_type": "product", "product_id": bad_product},
                ],
            },
        ).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        body = res.json()
        warnings = [line["package_warning"] for line in body["lines"]]
        assert warnings == [None, "No package defined"]

    def test_a_complete_request_submits(self, client):
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        ).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["portal_draft_at"] is None
        assert body["status"] == "new"

    def test_a_request_cannot_be_submitted_twice(self, client):
        """The second submit would fire the form SLA again."""
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        ).json()
        assert c.post(f"{_BASE}/{created['id']}/submit").status_code == 200

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 409
        assert res.json()["code"] == "ALREADY_SUBMITTED"

    def test_a_submitted_request_cannot_be_deleted(self, client):
        """Delete is a draft affordance. A submitted request is marketing's work."""
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        ).json()
        c.post(f"{_BASE}/{created['id']}/submit")

        assert c.delete(f"{_BASE}/{created['id']}").status_code == 409


# ---------------------------------------------------------------------------
# Price mode + line remarks (D5, D6, PLAN-price-tag-r7-request-ux AC-S2-5/7)
#
# `price_mode` replaces the old per-line `show_promo_price` switch as a
# header-level control: `list | selling`, `selling` requires a promotion, and
# on save the service re-derives EVERY line's `show_promo_price` from it
# (`show_promo_price = price_mode == 'selling'`). These tests pin the
# contract; the coder implements the header column, the line column and the
# derivation the same way `PriceTagRequestService.replace_lines` already
# owns writing every other line field.
# ---------------------------------------------------------------------------


class TestPriceModeAndRemarks:
    def test_create_defaults_price_mode_to_list_and_carries_it_on_the_response(
        self, client
    ):
        c, db, _ = client
        product_id = _seed_product(db)

        res = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        )

        assert res.status_code == 201, res.text
        assert res.json()["price_mode"] == "list"

    def test_create_accepts_price_mode_selling(self, client):
        """D1: a promotion is a LINE fact - `promotion_id` at the header is
        `extra="forbid"`-rejected (AC-S6-3), so this attaches it to the
        line, covered (AC-S6-5)."""
        c, db, contact_id = client
        product_id = _seed_product(db)
        # A line-level promotion_id must belong to the contact's audience
        # (the audience check the portal routes now enforce on write, not
        # just on the lookup) - grant the code the default access_levels
        # (["dealer","end_user"]) already carry.
        _grant_promotion_audience_code(db, contact_id)
        promotion_id = _seed_promotion(db)
        _cover_promotion(db, promotion_id, product_id)

        res = c.post(
            _BASE,
            json={
                "price_mode": "selling",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": product_id,
                        "promotion_id": promotion_id,
                    }
                ],
            },
        )

        assert res.status_code == 201, res.text
        assert res.json()["price_mode"] == "selling"
        assert res.json()["lines"][0]["promotion_id"] == promotion_id

    def test_create_rejects_an_unknown_price_mode_with_422(self, client):
        c, db, _ = client
        product_id = _seed_product(db)

        res = c.post(
            _BASE,
            json={
                "price_mode": "bogus",
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        )

        assert res.status_code == 422, res.text

    def test_update_accepts_price_mode(self, client):
        """D1: an update-time promotion is a LINE fact, audience-gated the
        same way create is (AC-S6-5)."""
        c, db, contact_id = client
        product_id = _seed_product(db)
        _grant_promotion_audience_code(db, contact_id)
        promotion_id = _seed_promotion(db)
        _cover_promotion(db, promotion_id, product_id)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()
        assert created["price_mode"] == "list"

        res = c.put(
            f"{_BASE}/{created['id']}",
            json={
                "price_mode": "selling",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": product_id,
                        "promotion_id": promotion_id,
                    }
                ],
            },
        )

        assert res.status_code == 200, res.text
        assert res.json()["price_mode"] == "selling"
        assert res.json()["lines"][0]["promotion_id"] == promotion_id

    def test_update_rejects_an_unknown_price_mode_with_422(self, client):
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        res = c.put(f"{_BASE}/{created['id']}", json={"price_mode": "bogus"})

        assert res.status_code == 422, res.text

    def test_update_rejects_an_explicit_null_price_mode_with_422(self, client):
        """M1: an explicit `null` used to reach `setattr(req, "price_mode",
        None)` - `exclude_unset` only drops an OMITTED field, not one sent
        as null - and 500 on the flush against the NOT NULL column."""
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        res = c.put(f"{_BASE}/{created['id']}", json={"price_mode": None})

        assert res.status_code == 422, res.text

    def test_line_create_accepts_remarks_and_the_response_carries_it(self, client):
        c, db, _ = client
        product_id = _seed_product(db)

        res = c.post(
            _BASE,
            json={
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": product_id,
                        "remarks": "Face out on the top shelf",
                    }
                ]
            },
        )

        assert res.status_code == 201, res.text
        assert res.json()["lines"][0]["remarks"] == "Face out on the top shelf"

    def test_line_update_replaces_remarks(self, client):
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": product_id,
                        "remarks": "Old note",
                    }
                ]
            },
        ).json()

        res = c.put(
            f"{_BASE}/{created['id']}",
            json={
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": product_id,
                        "remarks": "New note",
                    }
                ]
            },
        )

        assert res.status_code == 200, res.text
        assert res.json()["lines"][0]["remarks"] == "New note"

    def test_a_line_with_no_remarks_carries_null_not_a_missing_key(self, client):
        c, db, _ = client
        product_id = _seed_product(db)

        res = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        )

        assert res.status_code == 201, res.text
        body = res.json()["lines"][0]
        assert "remarks" in body
        assert body["remarks"] is None

    def test_submit_with_selling_and_no_promotion_succeeds(self, client):
        """D-P2 owner ruling: the r7 PRICE_MODE_NEEDS_PROMOTION submit guard
        is retired - Selling with no promotion is a valid end state. D3
        (this lane) retires the OLD defect this test used to pin (every
        line printed SP even summed at plain list): a line with no covering
        promotion and no manual price prints LP, `show_promo_price=False`,
        even in Selling mode - AC-S7-5."""
        c, db, _ = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "price_mode": "selling",
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        ).json()
        assert created["price_mode"] == "selling"
        assert created["lines"][0]["promotion_id"] is None

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        from app.models.price_tag import PriceTagRequestLine

        rows = (
            db.query(PriceTagRequestLine)
            .filter(PriceTagRequestLine.request_id == created["id"])
            .all()
        )
        assert len(rows) == 1
        assert all(row.show_promo_price is False for row in rows)

    def test_submit_with_selling_and_a_promotion_succeeds_and_shows_promo_price_on_every_line(
        self, client
    ):
        """D1/D3: each line carries its OWN promotion; `show_promo_price`
        follows AC-S7-5 (Selling AND basis != list) - true here because
        both lines' promotions actually cover them."""
        c, db, contact_id = client
        first = _seed_product(db)
        second = _seed_product(db)
        _grant_promotion_audience_code(db, contact_id)
        promotion_id = _seed_promotion(db)
        _cover_promotion(db, promotion_id, first)
        _cover_promotion(db, promotion_id, second)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "price_mode": "selling",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": first,
                        "promotion_id": promotion_id,
                    },
                    {
                        "line_type": "product",
                        "product_id": second,
                        "promotion_id": promotion_id,
                    },
                ],
            },
        ).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        from app.models.price_tag import PriceTagRequestLine

        rows = (
            db.query(PriceTagRequestLine)
            .filter(PriceTagRequestLine.request_id == created["id"])
            .all()
        )
        assert len(rows) == 2
        assert all(row.show_promo_price is True for row in rows)

    def test_switching_the_header_back_to_list_sets_every_lines_show_promo_price_false(
        self, client
    ):
        """D1: the promotion lives on each line, not the header."""
        c, db, contact_id = client
        first = _seed_product(db)
        second = _seed_product(db)
        _grant_promotion_audience_code(db, contact_id)
        promotion_id = _seed_promotion(db)
        _cover_promotion(db, promotion_id, first)
        _cover_promotion(db, promotion_id, second)
        created = c.post(
            _BASE,
            json={
                "price_mode": "selling",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": first,
                        "promotion_id": promotion_id,
                    },
                    {
                        "line_type": "product",
                        "product_id": second,
                        "promotion_id": promotion_id,
                    },
                ],
            },
        ).json()
        from app.models.price_tag import PriceTagRequestLine

        db.expire_all()
        rows = (
            db.query(PriceTagRequestLine)
            .filter(PriceTagRequestLine.request_id == created["id"])
            .all()
        )
        assert all(row.show_promo_price is True for row in rows)

        res = c.put(
            f"{_BASE}/{created['id']}",
            json={
                "price_mode": "list",
                "lines": [
                    {"line_type": "product", "product_id": first},
                    {"line_type": "product", "product_id": second},
                ],
            },
        )

        assert res.status_code == 200, res.text
        db.expire_all()
        rows = (
            db.query(PriceTagRequestLine)
            .filter(PriceTagRequestLine.request_id == created["id"])
            .all()
        )
        assert len(rows) == 2
        assert all(row.show_promo_price is False for row in rows)


# ---------------------------------------------------------------------------
# The grant gates every route, lookups included
# ---------------------------------------------------------------------------


def _revoke_the_grant(db: Session, contact_id: str) -> None:
    """Hide ``price_tag_request`` for this contact regardless of any segment
    grant (PLAN-portal-forms-market-segment D1) - an ``is_enabled=False``
    override wins over the segment union."""
    from app.models.price_tag import ContactPortalFormOverride

    db.add(
        ContactPortalFormOverride(
            id=str(uuid.uuid4()),
            contact_id=contact_id,
            form_type="price_tag_request",
            is_enabled=False,
        )
    )
    db.flush()


class TestTheGenericPortalDoesNotServeThisKind:
    """Two different questions were being answered by one tuple.

    ``SUPPORTED_TYPES`` says which kinds the GENERIC portal machinery serves -
    the submission CRUD, the neighbours, the revision policy rows. A price tag
    request is served by none of them: it has its own routes, its own service
    and no revision policy at all. Adding it to that tuple made the generic
    listing answer ``200 []`` instead of refusing, offered a revision-config row
    that configures nothing, and left the router include ORDER load-bearing -
    the only thing keeping the generic handler off the real price tag writes.

    Being GRANTABLE on an access type is the other question, and it has its own
    tuple now.
    """

    def test_the_generic_listing_refuses_the_kind(self, client):
        c, _db, _contact_id = client

        res = c.get(
            "/api/v1/public/portal/submissions",
            params={"type": "price_tag_request"},
        )

        assert res.status_code == 400, res.text
        assert "price_tag_request" in res.text

    # Review round 3: the neighbours route now DOES serve price_tag_request
    # (its own dedicated dispatch, not the generic SUPPORTED_TYPES machinery
    # this class is otherwise about) - the refusal this test pinned is
    # retired. Coverage moved to
    # test_portal_price_tag_revise.py::TestNeighboursRouteForPriceTagRequest
    # (`test_neighbours_for_the_owner` - 200 with prev/next/position/total;
    # `test_neighbours_other_contact_404` - 404 for a foreign token), which
    # already exercises both the happy path and the ownership gate, so
    # nothing here duplicates it.

    # The pair of tests that used to sit here ("still grantable on an access
    # type" / "unknown kind still refused by the grant schema") asserted the
    # OTHER question this class's own docstring names - and PLAN-portal-forms-
    # market-segment D1 answers it "no": access types carry no portal-form
    # grant at all any more, `ContactAccessTypeUpdate` has no such field
    # (see `test_access_type_portal_forms_removed.py` for that contract).
    # The grantable-kind question they meant to guard now belongs to
    # `MarketSegmentUpdate` (`test_market_segment_portal_forms.py`).


class TestTheListTheSalespersonReads:
    def test_a_row_carries_the_line_count_the_card_prints(self, client):
        """The portal card prints "N lines" and N was ``undefined``.

        ``PriceTagRequestListItem`` never declared ``line_count``, so the field
        was dropped on the way out and the card rendered nothing where the count
        belongs.
        """
        c, db, _contact_id = client
        product_id = _seed_product(db)
        c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        )

        rows = c.get(_BASE).json()["items"]

        assert len(rows) == 1
        assert rows[0]["line_count"] == 1


class TestTheGrantGatesEveryRoute:
    def test_the_debtor_lookup_refuses_a_contact_without_the_grant(self, client):
        """A revoked contact cannot enumerate their agent's debtor book.

        This was the one route of the ten that never called ``_assert_visible``,
        so the customer names, the customer codes and who buys from whom stayed
        readable with the form itself switched off.
        """
        c, db, contact_id = client
        _revoke_the_grant(db, contact_id)

        res = c.get("/api/v1/public/portal/lookups/debtors-for-agent")

        assert res.status_code == 403, res.text
        assert res.json()["code"] == "FORM_TYPE_NOT_VISIBLE"

    def test_the_item_lookup_beside_it_refuses_the_same_way(self, client):
        """The control: its sibling lookup was already gated."""
        c, db, contact_id = client
        _revoke_the_grant(db, contact_id)

        res = c.get("/api/v1/public/portal/lookups/price-tag-items")

        assert res.status_code == 403, res.text
        assert res.json()["code"] == "FORM_TYPE_NOT_VISIBLE"

    def test_the_debtor_lookup_still_answers_a_granted_contact(self, client):
        """And the gate does not cost the contact who IS granted the form."""
        c, _db, _contact_id = client

        res = c.get("/api/v1/public/portal/lookups/debtors-for-agent")

        assert res.status_code == 200, res.text
        assert isinstance(res.json(), list)


# ---------------------------------------------------------------------------
# Download the latest completed tag sheet PDF (PLAN-price-tag-feedback-r2 S2)
# ---------------------------------------------------------------------------


def _seed_completed_export(
    db: Session,
    request_id: str,
    *,
    filename: str = "ZZT-tags-v1.pdf",
    offset_seconds: int = 0,
) -> str:
    """A READY ``user_downloads`` row for ``request_id``, storage key returned.

    Directly seeded rather than driven through ``request_tag_sheet_export`` +
    the RQ render task: the render leg needs a live worker and a rendered
    catalogue page, neither of which this suite stands up. What the route under
    test reads is the finished row - status, kind, source pointer, storage key -
    so seeding that row directly proves the same contract.

    ``created_at`` is set explicitly rather than left to the column's
    ``server_default=func.now()``: within one transaction Postgres's `now()` is
    the transaction start time, constant for every statement in it, so two rows
    seeded back to back in the same test transaction would tie on it. A real
    export never has this problem (each export is its own request/transaction);
    only stacking two of them inside one test does.
    """
    from app.models.download import DownloadStatus, UserDownload
    from app.services.dealer_kit.tag_sheet_export_service import KIND

    key = f"zzt/{uuid.uuid4()}.pdf"
    download = UserDownload(
        id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),  # the marketing staffer who exported it
        kind=KIND,
        source_entity_type="price_tag_request",
        source_entity_id=request_id,
        status=DownloadStatus.READY.value,
        filename=filename,
        storage_provider="s3",
        storage_key=key,
        created_at=datetime.utcnow() + timedelta(seconds=offset_seconds),
    )
    db.add(download)
    db.flush()
    return key


class TestTheDownloadRoute:
    def test_the_detail_route_says_whether_a_completed_export_exists(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()
        assert created["has_completed_export"] is False

        _seed_completed_export(db, created["id"])

        body = c.get(f"{_BASE}/{created['id']}").json()
        assert body["has_completed_export"] is True

    def test_the_owner_streams_the_latest_completed_export(self, client, monkeypatch):
        c, db, _contact_id = client
        from app.api.v1.public import portal_price_tag
        from tests._fake_storage import FakeStorage

        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        storage = FakeStorage()
        monkeypatch.setattr(portal_price_tag, "get_backend", lambda provider: storage)
        key = _seed_completed_export(db, created["id"], filename="ZZT-tags-v1.pdf")
        storage.objects[key] = (b"%PDF-1.4 zzt bytes", "application/pdf")

        res = c.get(f"{_BASE}/{created['id']}/download")

        assert res.status_code == 200, res.text
        assert res.content == b"%PDF-1.4 zzt bytes"
        assert "ZZT-tags-v1.pdf" in res.headers["content-disposition"]

    def test_a_second_later_export_wins_over_the_first(self, client, monkeypatch):
        """"Latest completed" - a re-export after a proof revision must win."""
        c, db, _contact_id = client
        from app.api.v1.public import portal_price_tag
        from tests._fake_storage import FakeStorage

        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        storage = FakeStorage()
        monkeypatch.setattr(portal_price_tag, "get_backend", lambda provider: storage)
        first_key = _seed_completed_export(
            db, created["id"], filename="v1.pdf", offset_seconds=0
        )
        storage.objects[first_key] = (b"v1 bytes", "application/pdf")
        second_key = _seed_completed_export(
            db, created["id"], filename="v2.pdf", offset_seconds=5
        )
        storage.objects[second_key] = (b"v2 bytes", "application/pdf")

        res = c.get(f"{_BASE}/{created['id']}/download")

        assert res.status_code == 200, res.text
        assert res.content == b"v2 bytes"

    def test_no_completed_export_refuses_with_404(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        res = c.get(f"{_BASE}/{created['id']}/download")

        assert res.status_code == 404, res.text

    def test_a_foreign_token_gets_404_not_found_no_existence_oracle(self, client):
        """Ownership fails BEFORE the export lookup runs, so a foreign token
        gets ``_require_own_request``'s message ("Price tag request not
        found.") rather than anything about the export - it never learns one
        exists. That message differs from the "no export yet" 404 an owner
        gets (asserted below); what neither one leaks is whether the OTHER
        fact is true.
        """
        c, db, _contact_id = client
        from app.services.price_tag_request_service import PriceTagRequestService

        other = _seed_contact_who_can_see_the_form(db)
        theirs = PriceTagRequestService.create_request(
            db,
            contact_id=other,
            company_id=_SORENTO_COMPANY_ID,
            data={"debtor_name": "ZZT Theirs"},
        )
        db.flush()
        _seed_completed_export(db, theirs.id)

        res = c.get(f"{_BASE}/{theirs.id}/download")

        assert res.status_code == 404, res.text
        assert res.json()["message"] == "Price tag request not found."

    def test_visibility_revoked_refuses_before_looking_for_an_export(self, client):
        c, db, contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()
        _seed_completed_export(db, created["id"])
        _revoke_the_grant(db, contact_id)

        res = c.get(f"{_BASE}/{created['id']}/download")

        assert res.status_code == 403, res.text
        assert res.json()["code"] == "FORM_TYPE_NOT_VISIBLE"

    def test_a_storage_outage_answers_502_not_a_relabeled_404(self, client, monkeypatch):
        """Mirrors ``portal_download_attachment``: a bucket that refuses is a
        502 the caller can retry, not a 404 that reads like the file was
        deleted."""
        c, db, _contact_id = client
        from app.api.v1.public import portal_price_tag
        from tests._fake_storage import FakeStorage

        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        storage = FakeStorage()
        storage.downloading_fails = True
        monkeypatch.setattr(portal_price_tag, "get_backend", lambda provider: storage)
        _seed_completed_export(db, created["id"])

        res = c.get(f"{_BASE}/{created['id']}/download")

        assert res.status_code == 502, res.text


# ---------------------------------------------------------------------------
# A malformed id must 404, never 500 (uuid_path_param gap)
# ---------------------------------------------------------------------------


class TestMalformedRequestId:
    """A non-UUID ``{request_id}`` used to reach the service layer, whose
    ``get_request`` would either raise a raw DB error or simply find nothing
    the hard way. Either path is a client-visible 500 for what is, from the
    caller's side, a guaranteed-missing row - the same thing a well-formed but
    absent id already answers with a clean 404."""

    _BAD_ID = "not-a-uuid"

    def test_the_detail_route_404s_on_a_malformed_id(self, client):
        c, _db, _contact_id = client

        res = c.get(f"{_BASE}/{self._BAD_ID}")

        assert res.status_code == 404, res.text

    def test_the_download_route_404s_on_a_malformed_id(self, client):
        c, _db, _contact_id = client

        res = c.get(f"{_BASE}/{self._BAD_ID}/download")

        assert res.status_code == 404, res.text

    def test_the_update_route_404s_on_a_malformed_id(self, client):
        c, _db, _contact_id = client

        res = c.put(f"{_BASE}/{self._BAD_ID}", json={"debtor_name": "ZZT"})

        assert res.status_code == 404, res.text

    def test_the_submit_route_404s_on_a_malformed_id(self, client):
        c, _db, _contact_id = client

        res = c.post(f"{_BASE}/{self._BAD_ID}/submit")

        assert res.status_code == 404, res.text
# The promotions lookup (S4, #477)
# ---------------------------------------------------------------------------


def _seed_promotion(
    db: Session,
    *,
    description: str = "ZZT Promo",
    is_active: bool = True,
    start_date: date | None = None,
    end_date: date | None = None,
    access_levels: list[str] | None = None,
) -> str:
    from app.models.marketing import Promotion

    kwargs = {}
    if access_levels is not None:
        kwargs["access_levels"] = access_levels
    promo = Promotion(
        id=str(uuid.uuid4()),
        description=description,
        is_active=is_active,
        start_date=start_date,
        end_date=end_date,
        **kwargs,
    )
    db.add(promo)
    db.flush()
    return promo.id


def _cover_promotion(db: Session, promotion_id: str, product_id: str) -> None:
    """D1/AC-S6-5: a line's promotion is accepted only if it has a
    ``PromotionProduct`` row for a product on that line - the request-level
    header is gone, and per-line validation checks coverage strictly."""
    from decimal import Decimal

    from app.models.marketing import PromotionGroup, PromotionProduct

    group = PromotionGroup(promotion_id=promotion_id, group_name="ZZT group", sort_order=0)
    db.add(group)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()),
            promotion_id=promotion_id,
            promotion_group_id=str(group.id),
            product_id=product_id,
            promo_selling_price=Decimal("80.00"),
        )
    )
    db.flush()


def _grant_promotion_audience_code(db: Session, contact_id: str, code: str = "dealer") -> None:
    """Adds ONE more access code to the contact seeded by ``client``.

    The audience gate on this lookup reads a DIFFERENT code than the
    form-visibility grant ``_seed_contact_who_can_see_the_form`` already set
    up - a contact who may see the form is not automatically an audience a
    promotion is priced for, so a test that asserts a promotion IS returned
    needs its own grant of a code that matches ``Promotion.access_levels``.
    """
    from app.models.access import ContactAccessType, respond_contact_access_types

    if db.query(ContactAccessType).filter(ContactAccessType.code == code).first() is None:
        db.add(ContactAccessType(code=code, name=code))
        db.flush()
    db.execute(
        respond_contact_access_types.insert().values(
            contact_id=contact_id, access_type_code=code
        )
    )
    db.flush()


# D4 (PLAN-price-tag-line-promo-combo-subject.md): `GET /lookups/promotions`
# is retired with the header Promotion select - `lookup_promotions` is gone,
# replaced by the per-line `POST /lookups/line-pricing` route, covered in
# `tests/test_price_tag_line_pricing.py`. `TestThePromotionsLookup` (391 lines)
# tested the retired route and is removed rather than rewritten.
def _seed_two_company_contact(db: Session):
    """A contact granted the form, mapped to Sorento AND a second company, with
    a REAL, persisted ``PortalToken`` row.

    The company-scope resolver (``app.services.company_scope_resolver``) reads
    ``PortalToken`` straight off the database by its ``token`` column - it does
    not go through ``get_portal_token``'s dependency override the way the route's
    own auth does - so a test that wants a genuine multi-company scope has to
    persist the row rather than override the dependency the way ``client`` does.

    Returns ``(contact_id, access_type_code, second_company_id, token_value)``.
    """
    from datetime import datetime, timedelta as _timedelta

    from app.models.access import (
        ContactAccessType,
        RespondContact,
        respond_contact_access_types,
    )
    from app.models.company import Company, RespondContactCompany
    from app.models.portal import PortalToken
    from tests._portal_grant import link_contact_segment, seed_segment

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=unique_code("contact"),
    )
    db.add(contact)
    # PLAN-portal-forms-market-segment D1: the access type no longer carries
    # any portal-form grant - kept here only for its CODE, which a caller uses
    # as a product's `access_levels` entry (a different mechanism entirely).
    # The price_tag_request grant itself now comes from a market segment.
    access_type = ContactAccessType(
        code=unique_code("at"),
        name=unique_code("Access Type"),
    )
    db.add(access_type)
    second_company = Company(
        id=str(uuid.uuid4()),
        name=unique_code("Second Co"),
        code=unique_code("co")[:20],
    )
    db.add(second_company)
    db.flush()
    db.execute(
        respond_contact_access_types.insert().values(
            contact_id=contact.id, access_type_code=access_type.code
        )
    )
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    db.add_all(
        [
            RespondContactCompany(
                id=str(uuid.uuid4()),
                respond_contact_id=contact.id,
                company_id=_SORENTO_COMPANY_ID,
            ),
            RespondContactCompany(
                id=str(uuid.uuid4()),
                respond_contact_id=contact.id,
                company_id=second_company.id,
            ),
        ]
    )
    token_value = f"zzt-token-{uuid.uuid4().hex}"
    db.add(
        PortalToken(
            id=str(uuid.uuid4()),
            token=token_value,
            contact_id=contact.id,
            space_id="zzt-space",
            expires_at=datetime.utcnow() + _timedelta(days=1),
        )
    )
    db.flush()
    return contact.id, access_type.code, second_company.id, token_value


def _seed_product_in_company(
    db: Session, company_id: str, *, code: str, class_label: str = "Kitchen Sink"
) -> str:
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    category = ProductCategory(
        id=str(uuid.uuid4()),
        company_id=company_id,
        category_code=unique_code("cat"),
        category_name=unique_code("Category"),
        class_label=class_label,
    )
    brand = Brand(
        id=str(uuid.uuid4()),
        company_id=company_id,
        brand_code=unique_code("br"),
        brand_name=unique_code("Brand"),
    )
    uom = UnitOfMeasure(
        id=str(uuid.uuid4()),
        company_id=company_id,
        uom_code=unique_code("uom"),
        uom_name="Each",
    )
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=str(uuid.uuid4()),
        company_id=company_id,
        product_code=code,
        product_name=unique_code("Product"),
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=100.00,
    )
    db.add(product)
    db.flush()
    return product.id


@contextmanager
def _multi_company_client(db: Session, token_value: str):
    """A TestClient authenticated with a REAL, persisted portal token, so the
    router-level company-scope dependency (``apply_company_scope``) resolves the
    same multi-company membership the route's own auth sees."""
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken

    token_row = db.query(PortalToken).filter(PortalToken.token == token_value).one()

    def _override_get_db():
        yield db

    def _override_portal_token():
        return token_row

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_portal_token] = _override_portal_token
    try:
        with TestClient(app, headers={"X-Portal-Token": token_value}) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


class TestPickerCompanyScope:
    """A contact shared between two companies must see exactly ONE row per
    duplicated code, and it must be the row belonging to the SAME company
    ``_resolve_company`` will stamp the request with (#485).
    """

    def test_item_lookup_returns_the_request_companys_row_only(self):
        from app.api.v1.public.portal_price_tag import _resolve_company
        from app.models.portal import PortalToken

        with blank_session() as db:
            _contact_id, _access_code, second_company_id, token_value = (
                _seed_two_company_contact(db)
            )
            token_row = db.query(PortalToken).filter(PortalToken.token == token_value).one()
            expected_company_id = _resolve_company(db, token_row)
            other_company_id = (
                second_company_id
                if expected_company_id != second_company_id
                else _SORENTO_COMPANY_ID
            )

            shared_code = unique_code("shared")
            product_a_id = _seed_product_in_company(db, expected_company_id, code=shared_code)
            product_b_id = _seed_product_in_company(db, other_company_id, code=shared_code)

            with _multi_company_client(db, token_value) as c:
                res = c.get(
                    "/api/v1/public/portal/lookups/price-tag-items",
                    params={"q": shared_code},
                )

        assert res.status_code == 200, res.text
        rows = [r for r in res.json() if r["code"] == shared_code]
        assert len(rows) == 1, rows
        assert rows[0]["id"] == product_a_id
        assert rows[0]["id"] != product_b_id

    # D4: the `GET /lookups/promotions` company-scope guard is retired with
    # the route itself - no replacement needed, `line-pricing` scopes by the
    # ordinary company predicate the same as every other owned query.


# ---------------------------------------------------------------------------
# R6 (Phase 3 review) - `manual_sell_price` has no bound at all: -5 and 0 are
# valid `Decimal`s pydantic accepts as-is, and an absurd `1E+400` is a valid
# arbitrary-precision `Decimal` too. 175.50 (the plan's own S6-4 example
# figure) stays accepted throughout the existing suite.
# ---------------------------------------------------------------------------


class TestManualPriceBoundsOnCreateAndUpdate:
    def test_create_rejects_out_of_bounds_manual_price(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)

        for bad in (-5, 0, "1E+400"):
            res = c.post(
                _BASE,
                json={
                    "price_mode": "selling",
                    "lines": [
                        {
                            "line_type": "product",
                            "product_id": product_id,
                            "manual_sell_price": bad,
                        }
                    ],
                },
            )
            assert res.status_code == 422, (bad, res.text)

    def test_update_rejects_out_of_bounds_manual_price(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "price_mode": "selling",
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        ).json()

        for bad in (-5, 0, "1E+400"):
            res = c.put(
                f"{_BASE}/{created['id']}",
                json={
                    "price_mode": "selling",
                    "lines": [
                        {
                            "line_type": "product",
                            "product_id": product_id,
                            "manual_sell_price": bad,
                        }
                    ],
                },
            )
            assert res.status_code == 422, (bad, res.text)


# ---------------------------------------------------------------------------
# R4b/R5 (Phase 3 review) - the fail-closed promotion gate on CREATE, and the
# same gate under a MULTI-company scope on REVISE.
# ---------------------------------------------------------------------------


class TestCreateFailsClosedWithNoAudience:
    def test_a_line_promotion_id_is_refused_for_a_contact_with_no_access_codes(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        promotion_id = _seed_promotion_for(db, product_id, access_levels=["dealer"])

        res = c.post(
            _BASE,
            json={
                "price_mode": "selling",
                "lines": [
                    {
                        "line_type": "product",
                        "product_id": product_id,
                        "promotion_id": promotion_id,
                    }
                ],
            },
        )
        assert res.status_code == 422, res.text
        assert res.json().get("detail") == "line:0", res.text


class TestReviseCrossCompanyPromotionGate:
    """R5: a contact shared between company A and company B must not be able
    to price a request STAMPED company A with a promotion that only belongs
    to company B - the same multi-company sharing `TestPickerCompanyScope`
    (#485) had to pin for the item picker. Currently GREEN - the company
    predicate `_covering_promotions` already runs under is the REQUEST's own
    (single) company, never the contact's multi-company scope, so this locks
    that in as a regression guard rather than exposing a new gap."""

    def test_revise_refuses_a_line_promotion_from_the_other_company(self):
        from decimal import Decimal

        from app.models.marketing import Promotion, PromotionGroup, PromotionProduct
        from app.models.portal import PortalRevisionConfig, PortalToken
        from app.models.user import SystemSetting
        from app.services.price_tag_request_service import PriceTagRequestService

        with blank_session() as db:
            db.add(
                SystemSetting(
                    id=str(uuid.uuid4()), portal_revisions_enabled=True, portal_max_revisions=2
                )
            )
            db.add(
                PortalRevisionConfig(
                    id=str(uuid.uuid4()),
                    source_entity_type="price_tag_request",
                    is_enabled=True,
                    max_revisions=None,
                    allowed_statuses=["new", "changes_requested"],
                    restart_stage_code=None,
                )
            )
            db.commit()
            contact_id, access_code, second_company_id, token_value = (
                _seed_two_company_contact(db)
            )
            token_row = db.query(PortalToken).filter(PortalToken.token == token_value).one()
            from app.api.v1.public.portal_price_tag import _resolve_company

            request_company_id = _resolve_company(db, token_row)
            other_company_id = (
                second_company_id
                if request_company_id != second_company_id
                else _SORENTO_COMPANY_ID
            )

            product_id = _seed_product_in_company(
                db, request_company_id, code=unique_code("R5prod")
            )
            other_product_id = _seed_product_in_company(
                db, other_company_id, code=unique_code("R5other")
            )

            # A promotion that belongs to the OTHER company, covering the
            # OTHER company's own product - never company A's line's product.
            promotion = Promotion(
                id=str(uuid.uuid4()),
                description=unique_code("ZZT other-co promo"),
                is_active=True,
                access_levels=[access_code, "dealer", "end_user"],
                company_id=other_company_id,
            )
            db.add(promotion)
            db.flush()
            group = PromotionGroup(
                promotion_id=promotion.id, group_name="ZZT group", sort_order=0
            )
            db.add(group)
            db.flush()
            db.add(
                PromotionProduct(
                    id=str(uuid.uuid4()),
                    promotion_id=promotion.id,
                    promotion_group_id=str(group.id),
                    product_id=other_product_id,
                    promo_selling_price=Decimal("1.00"),
                    company_id=other_company_id,
                )
            )
            db.flush()

            request = PriceTagRequestService.create_request(
                db,
                contact_id=contact_id,
                company_id=request_company_id,
                data={
                    "debtor_name": "ZZT Dealer",
                    "price_mode": "selling",
                    "lines": [{"line_type": "product", "product_id": product_id}],
                },
            )
            # Matches `test_portal_price_tag_revise.py`'s `_seed_request`: a
            # revisable request has answered who prints (r9 D7) and sits in an
            # `allowed_statuses` status.
            request.status = "new"
            request.print_by = "office"
            request.portal_draft_at = None
            db.commit()

            with _multi_company_client(db, token_value) as c:
                res = c.post(
                    f"/api/v1/public/portal/submissions/price_tag_request/{request.id}/revise",
                    json={
                        "reason": "Trying the other company's promotion",
                        "expected_revision_no": 0,
                        "fields": {},
                        "products": [
                            {
                                "product_id": product_id,
                                "quantity": 1,
                                "promotion_id": promotion.id,
                            }
                        ],
                    },
                )

        assert res.status_code == 422, res.text
        body = res.json()
        assert body.get("detail") == "line:0", body
        assert body.get("code") == "PROMOTION_NOT_AVAILABLE", body


@pytest.fixture(autouse=True)
def no_respond(monkeypatch):
    """S8: no test run reaches api.respond.io. See `_ptag_r9_seed.block_respond`.

    Every transition here goes through the real notifier, which sends over the
    network unless something stops it - the run log used to carry a live
    ``Window check: Respond.io list_messages failed`` per transition.
    """
    return _ptag_r9_seed.block_respond(monkeypatch)


# ---------------------------------------------------------------------------
# S9 (PLAN-price-tag-r10.md "Portal Download PDF"): the portal can ask for a
# fresh export instead of sitting on a dead button.
#
# Contract (plan): `POST /public/portal/submissions/price_tag_request/{id}/
# export` (contact-authenticated, queues `request_tag_sheet_export` as the
# request's assignee); detail response gains `latest_export_status` in
# `ready | pending | failed | null`.
# ---------------------------------------------------------------------------


def _approve(db, request_id: str, *, assigned_to_id: str | None = None) -> None:
    from app.models.price_tag import PriceTagRequest

    row = db.query(PriceTagRequest).filter(PriceTagRequest.id == request_id).one()
    row.status = "approved"
    if assigned_to_id:
        row.assigned_to_id = assigned_to_id
    db.commit()


class TestAcS91LatestExportStatus:
    def test_no_export_at_all_is_null(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()

        body = c.get(f"{_BASE}/{created['id']}").json()

        assert "latest_export_status" in body, (
            "response_model silently drops an undeclared field - assert it by name"
        )
        assert body["latest_export_status"] is None

    def test_a_ready_export_reports_ready(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()
        _seed_completed_export(db, created["id"])

        body = c.get(f"{_BASE}/{created['id']}").json()
        assert body["latest_export_status"] == "ready"

    def test_a_failed_export_reports_failed(self, client):
        from app.models.download import DownloadStatus, UserDownload
        from app.services.dealer_kit.tag_sheet_export_service import KIND

        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()
        db.add(
            UserDownload(
                id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                kind=KIND,
                source_entity_type="price_tag_request",
                source_entity_id=created["id"],
                status=DownloadStatus.FAILED.value,
                filename="failed.pdf",
                storage_provider="s3",
                storage_key=f"zzt/{uuid.uuid4()}.pdf",
            )
        )
        db.commit()

        body = c.get(f"{_BASE}/{created['id']}").json()
        assert body["latest_export_status"] == "failed"

    def test_a_pending_export_reports_pending(self, client):
        from app.models.download import DownloadStatus, UserDownload
        from app.services.dealer_kit.tag_sheet_export_service import KIND

        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()
        db.add(
            UserDownload(
                id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                kind=KIND,
                source_entity_type="price_tag_request",
                source_entity_id=created["id"],
                status=DownloadStatus.PENDING.value,
                filename="pending.pdf",
                storage_provider="s3",
                storage_key=f"zzt/{uuid.uuid4()}.pdf",
            )
        )
        db.commit()

        body = c.get(f"{_BASE}/{created['id']}").json()
        assert body["latest_export_status"] == "pending"


class TestAcS92PortalExportRoute:
    def test_approved_with_a_saved_version_queues_an_export_202(self, client, monkeypatch):
        from tests import _ptag_r9_seed as seed
        from app.models.price_tag import PriceTagRequest

        c, db, contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()
        request = db.query(PriceTagRequest).filter_by(id=created["id"]).one()
        seed.attach_design(db, request)
        _approve(db, created["id"])

        queued: list = []
        monkeypatch.setattr(
            "app.services.queue_service.enqueue_job",
            lambda *a, **k: queued.append((a, k)),
        )

        response = c.post(f"{_BASE}/{created['id']}/export")

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["download_id"]

    def test_proof_ready_refuses_with_409(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()

        response = c.post(f"{_BASE}/{created['id']}/export")

        assert response.status_code == 409, response.text

    def test_captains_list_item_5_approved_with_no_saved_version_is_409(self, client):
        """`request_tag_sheet_export`'s own NO_VERSION guard: approved status
        alone is not enough - a request that was approved before any design
        was ever saved has no page/version to render, so the queue refuses
        with 409 rather than enqueueing a render of nothing."""
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()
        _approve(db, created["id"])

        response = c.post(f"{_BASE}/{created['id']}/export")

        assert response.status_code == 409, response.text

    def test_ac_s9_7_a_second_export_while_one_is_pending_answers_202_with_the_same_download_id(
        self, client, monkeypatch
    ):
        """AC-S9-7: a double-click (or the poll racing a slow click) must not
        queue a second render of the same request - the same `download_id`
        comes back, and `enqueue_job` is called exactly once."""
        from tests import _ptag_r9_seed as seed
        from app.models.download import UserDownload
        from app.models.price_tag import PriceTagRequest

        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()
        request = db.query(PriceTagRequest).filter_by(id=created["id"]).one()
        seed.attach_design(db, request)
        _approve(db, created["id"])

        queued: list = []
        monkeypatch.setattr(
            "app.services.queue_service.enqueue_job",
            lambda *a, **k: queued.append((a, k)),
        )

        first = c.post(f"{_BASE}/{created['id']}/export")
        assert first.status_code == 202, first.text
        first_id = first.json()["download_id"]

        second = c.post(f"{_BASE}/{created['id']}/export")
        assert second.status_code == 202, second.text
        assert second.json()["download_id"] == first_id, (
            "a second call while the first is still pending must answer the "
            "SAME download, not queue a duplicate render"
        )

        assert len(queued) == 1, "enqueue_job must not fire twice for one pending export"
        rows = (
            db.query(UserDownload)
            .filter(UserDownload.source_entity_id == created["id"])
            .count()
        )
        assert rows == 1, "no second user_downloads row for the same request"

    def test_ac_s9_7_revoked_visibility_refuses_the_export_route_too(self, client):
        from tests import _ptag_r9_seed as seed
        from app.models.price_tag import PriceTagRequest

        c, db, contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE, json={"lines": [{"line_type": "product", "product_id": product_id}]}
        ).json()
        request = db.query(PriceTagRequest).filter_by(id=created["id"]).one()
        seed.attach_design(db, request)
        _approve(db, created["id"])
        _revoke_the_grant(db, contact_id)

        response = c.post(f"{_BASE}/{created['id']}/export")

        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORM_TYPE_NOT_VISIBLE"

    def test_another_contacts_request_404s(self, client):
        c, db, _contact_id = client
        from app.services.price_tag_request_service import PriceTagRequestService

        other = _seed_contact_who_can_see_the_form(db)
        theirs = PriceTagRequestService.create_request(
            db,
            contact_id=other,
            company_id=_SORENTO_COMPANY_ID,
            data={"debtor_name": "ZZT Theirs"},
        )
        db.flush()
        _approve(db, theirs.id)

        response = c.post(f"{_BASE}/{theirs.id}/export")

        assert response.status_code == 404, response.text
