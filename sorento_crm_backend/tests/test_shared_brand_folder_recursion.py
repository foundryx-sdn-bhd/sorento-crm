"""Group C - Folders: recursion and the ancestor invariant.

`documentation/plans/multi-company/shared-brand-attachments-acceptance-criteria.md`
AC-C1..C7. Postgres only via `tests/_pg_fixture.py::blank_session`, own seeded
`ZZT-` chain per test.

AC-C1..C6 exercise `AttachmentCompanyService.apply()` directly. AC-C7 needs the
HTTP `GET .../attachments/drive` endpoint (route-level serialization).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.base import set_company_scope
from app.models.product import ProductAttachment
from app.models.resources import Attachment, AttachmentDirectory
from app.services.attachment_company_service import AttachmentCompanyService

from tests import _shared_brand_seed as seed
from tests._pg_fixture import blank_session

SORENTO = seed.SORENTO_ID
MOCHA = seed.MOCHA_ID


@pytest.fixture
def db():
    with blank_session() as session:
        seed.seed_mocha(session)
        set_company_scope(session, frozenset({SORENTO, MOCHA}))
        yield session


def _tree(db, *, root_company, mid_company, leaf_company):
    t = seed.att_type(db)
    root = seed.folder(db, company_id=root_company, name="ZZT Root")
    mid = seed.folder(db, company_id=mid_company, name="ZZT Mid", parent_id=root.id)
    leaf = seed.folder(db, company_id=leaf_company, name="ZZT Leaf", parent_id=mid.id)
    root_file = seed.attachment(db, company_id=root_company, type_id=t.id, directory_id=root.id, filename="ZZT-root.pdf")
    mid_file = seed.attachment(db, company_id=mid_company, type_id=t.id, directory_id=mid.id, filename="ZZT-mid.pdf")
    leaf_file = seed.attachment(db, company_id=leaf_company, type_id=t.id, directory_id=leaf.id, filename="ZZT-leaf.pdf")
    return root, mid, leaf, root_file, mid_file, leaf_file, t


def _twin_for(db, *, code: str):
    cat_s = seed.category(db, company_id=SORENTO)
    uom_s = seed.uom(db, company_id=SORENTO)
    cat_m = seed.category(db, company_id=MOCHA)
    uom_m = seed.uom(db, company_id=MOCHA)
    return seed.twin_products(
        db, code=code, sorento_cat=cat_s.id, sorento_uom=uom_s.id, mocha_cat=cat_m.id, mocha_uom=uom_m.id
    )


# --------------------------------------------------------------------------- #
# AC-C1 + AC-C2 - sharing `mid` shares its subtree AND pulls `root` up;
# a sibling file directly in root is untouched (ancestor pull is folders only)
# --------------------------------------------------------------------------- #


def test_ac_c1_and_c2_sharing_mid_shares_subtree_and_pulls_root_folder_only(db):
    root, mid, leaf, root_file, mid_file, leaf_file, t = _tree(
        db, root_company=SORENTO, mid_company=SORENTO, leaf_company=SORENTO
    )
    sibling_x = seed.attachment(db, company_id=SORENTO, type_id=t.id, directory_id=root.id, filename="ZZT-x.pdf")

    mid_product, mid_twin = _twin_for(db, code="ZZT-C1-MID")
    leaf_product, leaf_twin = _twin_for(db, code="ZZT-C1-LEAF")
    seed.product_attachment(db, company_id=SORENTO, product_id=mid_product.id, attachment_id=mid_file.id)
    seed.product_attachment(db, company_id=SORENTO, product_id=leaf_product.id, attachment_id=leaf_file.id)
    db.commit()

    result = AttachmentCompanyService(db).apply(directory_ids=[mid.id], company_id=None)

    assert result["updated_directories"] == 3, "root + mid + leaf"
    db.expire_all()

    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == root.id).first().company_id is None
    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == mid.id).first().company_id is None
    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == leaf.id).first().company_id is None

    assert db.query(Attachment).filter(Attachment.id == mid_file.id).first().company_id is None
    assert db.query(Attachment).filter(Attachment.id == leaf_file.id).first().company_id is None

    # AC-C2: the sibling file in root, outside the mid subtree, keeps its company.
    assert db.query(Attachment).filter(Attachment.id == sibling_x.id).first().company_id == SORENTO
    # root's OWN company changed (ancestor pull), but root is a FOLDER, not a file -
    # the pull never touches root_file, which was never selected nor a descendant of mid.
    assert db.query(Attachment).filter(Attachment.id == root_file.id).first().company_id == SORENTO

    # The twin linker ran for every collected FILE (mid_file, leaf_file).
    assert (
        db.query(ProductAttachment)
        .filter(ProductAttachment.attachment_id == mid_file.id, ProductAttachment.product_id == mid_twin.id)
        .first()
        is not None
    )
    assert (
        db.query(ProductAttachment)
        .filter(ProductAttachment.attachment_id == leaf_file.id, ProductAttachment.product_id == leaf_twin.id)
        .first()
        is not None
    )


# --------------------------------------------------------------------------- #
# AC-C3 - owning pushes down only: setting a shared `mid` to S owns mid + leaf
# (+ their files), `root` stays NULL
# --------------------------------------------------------------------------- #


def test_ac_c3_owning_mid_pushes_down_but_root_stays_shared(db):
    root, mid, leaf, root_file, mid_file, leaf_file, t = _tree(
        db, root_company=None, mid_company=None, leaf_company=None
    )
    db.commit()

    result = AttachmentCompanyService(db).apply(directory_ids=[mid.id], company_id=SORENTO)

    assert result["updated_directories"] == 2, "mid + leaf only, root is untouched"
    db.expire_all()

    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == mid.id).first().company_id == SORENTO
    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == leaf.id).first().company_id == SORENTO
    assert db.query(Attachment).filter(Attachment.id == mid_file.id).first().company_id == SORENTO
    assert db.query(Attachment).filter(Attachment.id == leaf_file.id).first().company_id == SORENTO

    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == root.id).first().company_id is None, (
        "owning must never pull an ancestor along - only sharing does that"
    )


# --------------------------------------------------------------------------- #
# AC-C4 - owning a FILE in a shared leaf leaves the leaf shared
# --------------------------------------------------------------------------- #


def test_ac_c4_owning_a_file_leaves_its_shared_folder_alone(db):
    root, mid, leaf, root_file, mid_file, leaf_file, t = _tree(
        db, root_company=None, mid_company=None, leaf_company=None
    )
    db.commit()

    result = AttachmentCompanyService(db).apply(attachment_ids=[leaf_file.id], company_id=SORENTO)

    assert result["updated_directories"] == 0
    assert result["updated_attachments"] == 1
    db.expire_all()
    assert db.query(Attachment).filter(Attachment.id == leaf_file.id).first().company_id == SORENTO
    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == leaf.id).first().company_id is None


# --------------------------------------------------------------------------- #
# AC-C5 - a mixed selection (directories + attachments) applies in ONE
# transaction, counted separately
# --------------------------------------------------------------------------- #


def test_ac_c5_mixed_selection_counts_separately_in_one_call(db):
    t = seed.att_type(db)
    folder_a = seed.folder(db, company_id=SORENTO, name="ZZT Folder A")
    folder_a_file = seed.attachment(db, company_id=SORENTO, type_id=t.id, directory_id=folder_a.id)
    standalone_file = seed.attachment(db, company_id=SORENTO, type_id=t.id, directory_id=None)
    db.commit()

    result = AttachmentCompanyService(db).apply(
        directory_ids=[folder_a.id], attachment_ids=[standalone_file.id], company_id=None
    )

    assert result["updated_directories"] == 1
    # folder_a_file (descendant of folder_a) + standalone_file (explicit) = 2.
    assert result["updated_attachments"] == 2

    db.expire_all()
    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == folder_a.id).first().company_id is None
    assert db.query(Attachment).filter(Attachment.id == folder_a_file.id).first().company_id is None
    assert db.query(Attachment).filter(Attachment.id == standalone_file.id).first().company_id is None


# --------------------------------------------------------------------------- #
# AC-C6 - a soft-deleted subfolder is not touched by the recursion
# --------------------------------------------------------------------------- #


def test_ac_c6_soft_deleted_subfolder_is_untouched(db):
    t = seed.att_type(db)
    root = seed.folder(db, company_id=SORENTO, name="ZZT Root")
    trashed = seed.folder(db, company_id=SORENTO, name="ZZT Trashed", parent_id=root.id, is_deleted=True)
    trashed_file = seed.attachment(db, company_id=SORENTO, type_id=t.id, directory_id=trashed.id)
    db.commit()

    result = AttachmentCompanyService(db).apply(directory_ids=[root.id], company_id=None)

    assert result["updated_directories"] == 1, "root only - the deleted subfolder must not be collected"
    db.expire_all()
    assert db.query(AttachmentDirectory).filter(AttachmentDirectory.id == trashed.id).first().company_id == SORENTO
    assert db.query(Attachment).filter(Attachment.id == trashed_file.id).first().company_id == SORENTO


# --------------------------------------------------------------------------- #
# AC-C7 - GET .../attachments/drive under Mocha scope, after AC-C1's share,
# returns root/mid/leaf + their files at their real positions; `x` does not
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(db, monkeypatch):
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    def _override_get_db():
        yield db

    def _override_current_user():
        return {"id": "773b536d-c675-5a29-b44c-37f956462ba0"}

    # LIST-API-PERMS: the drive route carries a permission gate now; this principal has no
    # users row, so answer the role lookup as admin (the gate has its own tests).
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_current_user_or_api_key] = _override_current_user
    app.dependency_overrides[apply_company_scope] = lambda: set_company_scope(
        db, frozenset({MOCHA})
    )
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_ac_c7_drive_under_mocha_scope_shows_the_shared_subtree_not_the_sibling(client, db):
    set_company_scope(db, frozenset({SORENTO, MOCHA}))
    root, mid, leaf, root_file, mid_file, leaf_file, t = _tree(
        db, root_company=SORENTO, mid_company=SORENTO, leaf_company=SORENTO
    )
    sibling_x = seed.attachment(db, company_id=SORENTO, type_id=t.id, directory_id=root.id, filename="ZZT-x.pdf")
    db.commit()

    AttachmentCompanyService(db).apply(directory_ids=[mid.id], company_id=None)
    db.commit()

    r = client.get(
        "/api/v1/resource-management/attachments/drive",
        params={"directory_id": root.id, "recursive": True, "limit": 50},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    names = {row.get("name") or row.get("original_filename") for row in body["data"]}

    assert "ZZT Mid" in names
    assert "ZZT Leaf" in names
    assert "ZZT-mid.pdf" in names
    assert "ZZT-leaf.pdf" in names
    assert "ZZT-x.pdf" not in names, "the Sorento-only sibling must not leak into Mocha scope"

    # R14 / AC-E3, through response_model: both folder rows are shared by this
    # scenario (mid was shared directly, root pulled up as its ancestor, leaf
    # pulled down as its descendant) - every folder row must read company_id
    # AND company_name as null/null, not silently drop the fields.
    folder_rows = {row["id"]: row for row in body["data"] if row.get("kind") == "folder"}
    assert set(folder_rows) == {mid.id, leaf.id}
    for row in folder_rows.values():
        assert row["company_id"] is None
        assert row["company_name"] is None
