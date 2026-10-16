"""Lane CONTACT-ACTIONS-COPY: "Copy access from contact" copies EVERY per-contact access setting.

Owner, 16 Oct 2026: "when we copy, it doesn't copy things like brands, image access, voice note
access, it doesn't copy all". UAC ``documentation/plans/contacts/contact-actions-copy-16oct-acceptance-criteria.md`` B1-B3.

A source with every access setting set to a non-default value, a target left at defaults: after one
apply every setting equals the source's, and identity fields (name, phone, memory, language,
linked customers) are untouched. Same endpoint and fixtures as ``test_contact_bulk_copy_access.py``.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.access import ContactAttachmentType, RespondContact, SpecVisibilityPolicy
from app.models.media import ContactMediaLimit
from app.models.price_tag import ContactPortalFormOverride
from app.models.product import Brand
from app.models.resources import AttachmentType
from app.services import contact_field_reveal_service

from tests._brand_scope_seed import brand
from tests._pg_fixture import unique_code
from tests.test_contact_bulk_copy_access import (  # noqa: F401 - fixtures + helpers
    AGENTS_EDIT,
    COST,
    D1,
    D2,
    EDIT,
    SORENTO,
    VIEW,
    _access_type,
    _agent,
    _by_id,
    _contact,
    _post,
    _snap,
    _untouched,
    client,
    db,
    perms,
)
from tests.test_contact_copy_fields import (  # noqa: F401 - fixtures + helpers
    STOCK_EDIT,
    _companies,
    _company,
    _grant,
    _policy,
    _segment,
    _segments,
    _stock,
    roles,
)

#: Every chatbot switch on the contact row the copy carries, set to the NON-default value.
SWITCHES = {
    "chatbot_stock_allowed": False,
    "notify_salesman": True,
    "packing_list_allowed": True,
    "chatbot_eta_offset_applied": False,
    "escalation_allowed": False,
    "chatbot_allowed": True,
    "chatbot_skip_period_questions": True,
    "chatbot_files_as_links": True,
}


@pytest.fixture
def full(perms, roles):  # noqa: F811
    perms["granted"] = {VIEW, EDIT, AGENTS_EDIT, STOCK_EDIT}
    return perms


def _media(db, contact, modality, *, allowed, limit=None, clip=None):
    db.add(
        ContactMediaLimit(
            contact_id=contact.id,
            modality=modality,
            is_allowed=allowed,
            monthly_limit=limit,
            max_clip_seconds=clip,
        )
    )
    db.flush()


def _media_of(db, contact_id):
    db.expire_all()
    rows = db.query(ContactMediaLimit).filter(ContactMediaLimit.contact_id == contact_id).all()
    return {r.modality: (r.is_allowed, r.monthly_limit, r.max_clip_seconds) for r in rows}


def _attachment_type(db, tag):
    row = AttachmentType(
        id=str(uuid.uuid4()), type_name=f"ZZT type {unique_code(tag)}", allowed_extensions="pdf"
    )
    db.add(row)
    db.flush()
    return row


def _attachments_of(db, contact_id):
    db.expire_all()
    return sorted(
        str(r.attachment_type_id)
        for r in db.query(ContactAttachmentType).filter(ContactAttachmentType.contact_id == contact_id)
    )


def _forms_of(db, contact_id):
    db.expire_all()
    rows = db.query(ContactPortalFormOverride).filter(ContactPortalFormOverride.contact_id == contact_id)
    return {r.form_type: r.is_enabled for r in rows}


def _spec_of(db, contact_id):
    db.expire_all()
    row = db.query(SpecVisibilityPolicy).filter(SpecVisibilityPolicy.contact_id == contact_id).first()
    return None if row is None else (row.spec_keys, row.excluded_spec_keys)


def _everything(db, contact_id):
    """Every access setting of one contact, read fresh."""
    db.expire_all()
    c = db.get(RespondContact, contact_id)
    return {
        **_snap(db, contact_id),
        "switches": {col: getattr(c, col) for col in SWITCHES},
        "regions": list(c.regions),
        "brands": sorted(c.brand_ids or []),
        "media": _media_of(db, contact_id),
        "stock_policy": _stock(db, contact_id),
        "companies": _companies(db, contact_id),
        "segments": _segments(db, contact_id),
        "attachment_types": _attachments_of(db, contact_id),
        "portal_forms": _forms_of(db, contact_id),
        "spec": _spec_of(db, contact_id),
    }


@pytest.fixture
def rich(db):
    """A source with every access setting set away from its default, and a default target."""
    ta, tb = _access_type(db, "a"), _access_type(db, "b")
    agent = _agent(db, "a")
    b1, b2 = brand(db, "zcab1"), brand(db, "zcab2")
    co = _company(db, "a")
    seg = _segment(db, "a")
    source = _contact(
        db,
        "all-source",
        types=[ta, tb],
        tier="dealer",
        reveals=[COST],
        agents=[(agent, True, D1, D2)],
        language="ms",
    )
    for col, value in SWITCHES.items():
        setattr(source, col, value)
    source.regions = ["west", "east"]
    source.brand_ids = [b1.id, b2.id]
    db.flush()
    _media(db, source, "image", allowed=True, limit=7)
    _media(db, source, "voice", allowed=True, limit=9, clip=45)
    _policy(db, source, "compact", hide_zero=True)
    _grant(db, source, co)
    source.market_segments = [seg]
    for t in (_attachment_type(db, "za1"), _attachment_type(db, "za2")):
        db.add(ContactAttachmentType(contact_id=source.id, attachment_type_id=t.id))
    db.add(ContactPortalFormOverride(contact_id=source.id, form_type="price_tag_request", is_enabled=True))
    db.add(ContactPortalFormOverride(contact_id=source.id, form_type="complaint", is_enabled=False))
    db.add(SpecVisibilityPolicy(contact_id=source.id, spec_keys=None, excluded_spec_keys=["zzt_thickness"]))
    db.flush()
    target = _contact(db, "all-target", language="en", memory="off")
    return source, target


def test_b1_every_access_setting_lands_on_the_target(client, db, full, rich):  # noqa: F811
    source, target = rich
    before_identity = _untouched(db, target.id)
    expected = _everything(db, source.id)

    resp = _post(client, source, [target])
    assert resp.status_code == 200, resp.text
    assert _by_id(resp.json())[target.id]["status"] == "changed"

    got = _everything(db, target.id)
    for key in expected:
        if key == "agent_row_count":
            continue
        assert got[key] == expected[key], key
    # Identity, memory, language and linked customers are never part of the copy.
    assert _untouched(db, target.id) == before_identity


def test_b1_new_facets_are_reported_as_changes_and_listed_in_the_summary(client, db, full, rich):  # noqa: F811
    source, target = rich
    body = _post(client, source, [target], dry_run=True).json()
    facets = {c["facet"] for c in _by_id(body)[target.id]["changes"]}
    assert {
        "brands",
        "image_access",
        "voice_access",
        "chatbot_files_as_links",
        "attachment_types",
        "portal_forms",
        "spec_visibility",
    } <= facets
    labels = {line["label"] for line in body["source"]["summary"]}
    assert {"Brands", "Image access", "Voice note access", "Attachment types", "Portal forms", "Spec visibility"} <= labels
    assert "Media limits" not in body["source"]["not_copied"]
    # A dry run writes nothing.
    assert _media_of(db, target.id) == {}


def test_b2_no_media_row_on_the_source_turns_the_target_off(client, db, full):  # noqa: F811
    source, target = _contact(db, "m-src"), _contact(db, "m-tgt")
    _media(db, target, "image", allowed=True, limit=3)
    resp = _post(client, source, [target])
    assert resp.status_code == 200, resp.text
    change = next(c for c in _by_id(resp.json())[target.id]["changes"] if c["facet"] == "image_access")
    assert change["after"] == "Off"
    assert _media_of(db, target.id)["image"] == (False, None, None)


def test_b2_an_off_row_and_no_row_are_the_same_access(client, db, full):  # noqa: F811
    source, target = _contact(db, "o-src"), _contact(db, "o-tgt")
    _media(db, target, "voice", allowed=False)
    body = _post(client, source, [target], dry_run=True).json()
    assert _by_id(body)[target.id]["status"] == "unchanged"


def test_b2_an_off_row_with_stale_limits_is_still_off(client, db, full):  # noqa: F811
    """The Media Access card keeps the old limits on a row it switches off."""
    source, target = _contact(db, "s-src"), _contact(db, "s-tgt")
    _media(db, source, "voice", allowed=False, limit=7, clip=45)
    body = _post(client, source, [target], dry_run=True).json()
    assert _by_id(body)[target.id]["status"] == "unchanged"


def test_b2_media_is_not_rewritten_when_it_already_matches(client, db, full):  # noqa: F811
    source, target = _contact(db, "u-src"), _contact(db, "u-tgt")
    source.notify_salesman = True
    db.flush()
    resp = _post(client, source, [target])
    assert resp.status_code == 200, resp.text
    assert _by_id(resp.json())[target.id]["status"] == "changed"
    assert _media_of(db, target.id) == {}


def test_b3_an_empty_brand_list_clears_the_target_scope(client, db, full):  # noqa: F811
    source, target = _contact(db, "b-src"), _contact(db, "b-tgt")
    target.brand_ids = [brand(db, "zcab3").id]
    db.flush()
    resp = _post(client, source, [target])
    assert resp.status_code == 200, resp.text
    change = next(c for c in _by_id(resp.json())[target.id]["changes"] if c["facet"] == "brands")
    # Every brand is a widening, never shown as a struck-out list.
    assert change["after"] == "All brands" and change["before"] == "ZZT zcab3"
    assert change["added"] == [] and change["removed"] == []
    db.expire_all()
    assert not db.get(RespondContact, target.id).brand_ids
    # The reveal facet is untouched by brand writes.
    assert contact_field_reveal_service.granted_keys(db, target.id) == contact_field_reveal_service.granted_keys(
        db, source.id
    )


def test_b3_a_brand_outside_the_actor_company_scope_is_not_copied(client, db, full):  # noqa: F811
    """The direct editor refuses a brand id the actor cannot see; the copy leaves the facet out."""
    other = _company(db, "ob")
    hidden = Brand(id=str(uuid.uuid4()), brand_code=unique_code("zob")[:50], brand_name="ZZT hidden", company_id=other.id)
    db.add(hidden)
    db.flush()
    source, target = _contact(db, "h-src"), _contact(db, "h-tgt")
    source.brand_ids = [hidden.id]
    source.notify_salesman = True
    db.flush()
    body = _post(client, source, [target]).json()
    row = _by_id(body)[target.id]
    assert row["status"] == "changed"
    assert all(c["facet"] != "brands" for c in row["changes"])
    assert "Brands" in body["source"]["not_copied"]
    assert all(line["label"] != "Brands" for line in body["source"]["summary"])
    assert "ZZT hidden" not in str(body)
    db.expire_all()
    assert not db.get(RespondContact, target.id).brand_ids


def test_b4_inherited_portal_forms_and_spec_clear_the_target_overrides(client, db, full):  # noqa: F811
    source, target = _contact(db, "i-src"), _contact(db, "i-tgt")
    db.add(ContactPortalFormOverride(contact_id=target.id, form_type="price_tag_request", is_enabled=True))
    db.add(SpecVisibilityPolicy(contact_id=target.id, spec_keys=["zzt_a"], excluded_spec_keys=None))
    db.add(ContactAttachmentType(contact_id=target.id, attachment_type_id=_attachment_type(db, "zi").id))
    db.flush()
    resp = _post(client, source, [target])
    assert resp.status_code == 200, resp.text
    changes = {c["facet"]: c for c in _by_id(resp.json())[target.id]["changes"]}
    assert changes["portal_forms"]["after"] == "Inherited"
    assert changes["spec_visibility"]["after"] == "Inherited"
    assert _forms_of(db, target.id) == {}
    assert _spec_of(db, target.id) is None
    assert _attachments_of(db, target.id) == []


def test_b3_the_differs_filter_ignores_a_brand_the_actor_cannot_copy(db):  # noqa: F811
    """`access_differs_from` must agree with the copy: a hidden source brand is not a difference."""
    from app.services.contact_access_copy_service import contacts_differing_from

    other = _company(db, "of")
    hidden = Brand(id=str(uuid.uuid4()), brand_code=unique_code("zof")[:50], brand_name="ZZT hidden f", company_id=other.id)
    db.add(hidden)
    db.flush()
    source, same, different = _contact(db, "f-src"), _contact(db, "f-same"), _contact(db, "f-diff")
    source.brand_ids = [hidden.id]
    different.notify_salesman = True
    db.flush()
    assert contacts_differing_from(db, source, [same, different]) == [different.id]
