"""PV2-RESOLVE: the alias conflict check on the 3 staff save paths, and the alias seed.

CONTRACT-PV2-13oct section 4: "a save creating the same alias under two keys with no
precedence row is refused with the clash named"; seeds only add. No LLM, no network.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.schemas.product import ProductCategoryCreate, ProductCategoryUpdate
from app.schemas.resources import AttachmentTypeUpdate
from app.services.chatbot.pv2 import alias_seed
from app.services.chatbot.pv2 import aliases as A
from app.services.error_handler import AppException
from app.services.product_service import ProductCategoryService
from app.services.resources_service import AttachmentTypeService

from tests.chatbot.pv2 import _resolve_seed as S

MIGRATION = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "pv2_0002_alias_seed.py"


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _precedence(db, kind, alias, winner):
    db.execute(
        text("INSERT INTO chatbot_alias_precedence (kind, alias_norm, winner_key) VALUES (:k, :a, :w)"),
        {"k": kind, "a": alias, "w": winner},
    )
    db.flush()


def _stored(db, code):
    return db.execute(text("SELECT search_synonyms FROM attachment_types WHERE code = :c"), {"c": code}).scalar()


def _message(exc: AppException) -> str:
    return exc.detail["message"]


# --------------------------------------------------------------------------- attachment type
def test_attachment_type_save_refuses_a_word_another_type_holds(db):
    S.attachment_type(db, "technical_specifications", "Technical Specifications", synonyms=["drawing"])
    photos = S.attachment_type(db, "product_photos", "Product Photos", synonyms=["photo"])
    with pytest.raises(AppException) as err:
        AttachmentTypeService(db).update_type(photos.id, AttachmentTypeUpdate(search_synonyms=["photo", "Drawing"]))
    assert err.value.status_code == 409
    assert '"drawing"' in _message(err.value)
    assert "product_photos" in _message(err.value) and "technical_specifications" in _message(err.value)


def test_attachment_type_save_passes_when_precedence_settles_the_word(db):
    S.attachment_type(db, "technical_specifications", "Technical Specifications", synonyms=["drawing"])
    photos = S.attachment_type(db, "product_photos", "Product Photos", synonyms=["photo"])
    _precedence(db, "attachment_type", "drawing", "technical_specifications")
    saved = AttachmentTypeService(db).update_type(
        photos.id, AttachmentTypeUpdate(search_synonyms=["photo", " Drawing ", "drawing"])
    )
    assert _stored(db, "product_photos") == ["photo", "Drawing"]
    assert saved.search_synonyms == ["photo", "Drawing"]


def test_attachment_type_save_without_synonyms_never_checks(db):
    # An existing clash (both already hold "pic") never blocks an unrelated edit.
    S.attachment_type(db, "technical_specifications", "Technical Specifications", synonyms=["pic"])
    photos = S.attachment_type(db, "product_photos", "Product Photos", synonyms=["pic"])
    AttachmentTypeService(db).update_type(photos.id, AttachmentTypeUpdate(description="Pictures"))
    # Re-sending the word it already held is not a NEW clash either.
    AttachmentTypeService(db).update_type(photos.id, AttachmentTypeUpdate(search_synonyms=["pic", "photo"]))
    assert _stored(db, "product_photos") == ["pic", "photo"]


# --------------------------------------------------------------------------- category
def test_category_save_refuses_a_word_another_class_holds(db):
    S.category(db, class_label="Basin", synonyms=["sinki"])
    with pytest.raises(AppException) as err:
        ProductCategoryService(db).create_category(
            ProductCategoryCreate(category_code="KSINK", category_name="Kitchen Sink", search_synonyms=["Sinki"])
        )
    assert err.value.status_code == 409
    assert '"sinki"' in _message(err.value)


def test_category_save_accepts_a_word_its_own_class_already_holds(db):
    first = S.category(db, class_label="Basin", synonyms=["sinki"])
    second = S.category(db, class_label="Basin")
    saved = ProductCategoryService(db).update_category(
        second.id, ProductCategoryUpdate(search_synonyms=["sinki", "lavabo"])
    )
    assert saved.search_synonyms == ["sinki", "lavabo"]
    assert first.search_synonyms == ["sinki"]


# --------------------------------------------------------------------------- spec registry
def _user():
    return {"id": str(uuid.uuid4())}


@pytest.fixture()
def editor(monkeypatch):
    """The route's own field-level grant check, granted: the alias check is what is under test."""
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda *a, **k: True)


def test_spec_registry_patch_refuses_a_word_another_value_holds(db, editor):
    from app.api.v1.master_data.spec_registry import SpecKeyUpdate, update_spec_key

    S.registry(db, "trap_type", "Trap", "enum", allowed=["p_trap", "s_trap"],
               synonyms={"p_trap": ["p trap"], "s_trap": ["s trap", "floor waste"]})
    S.registry(db, "finish", "Finish", "enum", allowed=["matte"], synonyms={"matte": ["matte"]})
    with pytest.raises(AppException) as err:
        update_spec_key("finish", SpecKeyUpdate(user_synonyms={"matte": ["Floor  waste"]}), _user(), db)
    assert err.value.status_code == 409
    assert '"floor waste"' in _message(err.value)
    assert "trap_type.s_trap" in _message(err.value) and "finish.matte" in _message(err.value)


def test_spec_registry_patch_passes_when_precedence_settles_the_word(db, editor):
    from app.api.v1.master_data.spec_registry import SpecKeyUpdate, update_spec_key

    S.registry(db, "trap_type", "Trap", "enum", allowed=["s_trap"], synonyms={"s_trap": ["floor waste"]})
    S.registry(db, "finish", "Finish", "enum", allowed=["matte"], synonyms={"matte": ["matte"]})
    _precedence(db, "spec", "floor waste", "trap_type.s_trap")
    out = update_spec_key("finish", SpecKeyUpdate(user_synonyms={"matte": ["floor waste"]}), _user(), db)
    assert out["user_synonyms"] == {"matte": ["floor waste"]}


# --------------------------------------------------------------------------- seed
def _seed_types(db):
    S.attachment_type(db, None, "Technical Specifications")
    S.attachment_type(db, None, "Product Photos")
    db.execute(text("""UPDATE attachment_types SET search_synonyms = '["Gambar"]' WHERE type_name = 'Product Photos'"""))
    S.attachment_type(db, None, "Certificate")
    S.attachment_type(db, "manual_x", "Installation Guide")  # already coded: left alone


def test_alias_seed_fills_codes_adds_aliases_and_is_idempotent(db):
    _seed_types(db)
    first = alias_seed.apply(db)
    assert first["codes"] == 3
    rows = dict(db.execute(text("SELECT type_name, code FROM attachment_types")).all())
    assert rows["Technical Specifications"] == "technical_specifications"
    assert rows["Product Photos"] == "product_photos"
    assert rows["Certificate"] == "certification"
    assert rows["Installation Guide"] == "manual_x"
    photos = _stored(db, "product_photos")
    assert photos[0] == "Gambar" and "gambar" not in photos  # an existing word is kept, never doubled
    assert set(photos) == {"Gambar", "photo", "picture", "图片", "照片"}
    assert {"TD", "CAD", "drawing", "图纸", "lukisan"} <= set(_stored(db, "technical_specifications"))
    assert first["precedence"] == len(alias_seed.PRECEDENCE)

    assert alias_seed.apply(db) == {"codes": 0, "aliases": 0, "precedence": 0}


def test_seed_precedence_settles_every_clash_the_shipped_spec_seed_carries(db):
    from app.services.product_spec_registry import seed_spec_registry

    seed_spec_registry(db)
    db.flush()
    assert [c for c in A.open_clashes(db) if c.kind == "spec"]  # the shipped seed clashes
    alias_seed.apply(db)
    assert [c for c in A.open_clashes(db) if c.kind == "spec"] == []


def test_migration_carries_a_frozen_copy_of_the_seed():
    spec = importlib.util.spec_from_file_location("pv2_0002_alias_seed", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "pv2_0002_alias_seed" and len(module.revision) <= 32
    assert module.down_revision == "pv2_0001_schema"
    assert module.TYPE_CODES == alias_seed.TYPE_CODES
    assert module.TYPE_ALIASES == alias_seed.TYPE_ALIASES
    assert module.PRECEDENCE == alias_seed.PRECEDENCE


def test_migration_imports_no_application_code():
    from tests._migration_imports import app_imports  # type: ignore[attr-defined]

    assert app_imports(MIGRATION) == []
