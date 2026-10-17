"""SPEC-REGISTRY-UI Phase 2 red tests: one word, one field (contract C4, UAC AC-2.1 to
AC-2.3).

A NEW word (a value's word, an "other name", or a new value) that normalises equal to
another row's merged word, label or spec_key is refused with 409
`spec_registry_word_on_other_key`, message `"<word>" is already a word for <label>.`, and
nothing in the save is stored. Words the row already held before the save are not judged.

Red until the guard exists: today these saves answer 200 and store the word.

Postgres only (`tests._pg_fixture.blank_session`). Auth-override pattern from
`test_spec_registry_pr2_routes.py`.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session

_MERCHANDISER = "7e95ab26-0b8c-5f5d-9a46-1c5d8e2f7b04"
_MERCH_ROLE = "8fa6bc37-1c9d-5a6e-8b57-2d6e9f3a8c15"
_REGISTRY_ADMIN = "90b7cd48-2dae-5b7f-9c68-3e7fa04b9d26"
_ADMIN_ROLE = "a1c8de59-3ebf-5c80-8d79-4f80b15cae37"
_SORENTO = "00000000-0000-0000-0000-000000000001"

_BASE = "/api/v1/master-data/spec-registry"
_CODE = "spec_registry_word_on_other_key"

_MERCH_SLUGS = ("master_data.products.view", "master_data.products.edit")
_ADMIN_SLUGS = (
    "master_data.spec_registry.view",
    "master_data.spec_registry.edit",
    "master_data.spec_registry.add",
)


def _grant(db: Session, role_id: str, slugs) -> None:
    from app.models.user import UserPermission, UserRolePermission

    for slug in slugs:
        existing = db.query(UserPermission).filter_by(slug=slug).first()
        if existing is None:
            existing = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug, description="")
            db.add(existing)
            db.flush()
        db.add(UserRolePermission(id=str(uuid.uuid4()), role_id=role_id, permission_id=existing.id))


def _seed(db: Session) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    for role_id, slug, name in (
        (_MERCH_ROLE, "zzt_guard_merchandiser", "ZZT Guard Merchandiser"),
        (_ADMIN_ROLE, "zzt_guard_registry_admin", "ZZT Guard Registry Admin"),
    ):
        db.add(
            UserRole(
                id=role_id, slug=slug, name=name, description="", is_protected=False, is_default=False
            )
        )
    db.add(User(id=_MERCHANDISER, email="zzt-guard-merch@test.com", name="Merch", status="ACTIVE"))
    db.add(User(id=_REGISTRY_ADMIN, email="zzt-guard-admin@test.com", name="Admin", status="ACTIVE"))
    db.flush()
    db.add(UserRoleAssignment(user_id=_MERCHANDISER, role_id=_MERCH_ROLE))
    db.add(UserRoleAssignment(user_id=_REGISTRY_ADMIN, role_id=_ADMIN_ROLE))
    _grant(db, _MERCH_ROLE, _MERCH_SLUGS)
    _grant(db, _ADMIN_ROLE, _ADMIN_SLUGS)
    db.commit()


def _key(db: Session, spec_key: str, **kwargs):
    from app.models.product_spec import ProductSpecRegistry

    row = ProductSpecRegistry(
        spec_key=spec_key,
        label=kwargs.pop("label", spec_key.replace("_", " ").title()),
        data_type=kwargs.pop("data_type", "numeric"),
        unit=kwargs.pop("unit", "mm"),
        allowed_values=kwargs.pop("allowed_values", []),
        synonyms=kwargs.pop("synonyms", {}),
        user_synonyms=kwargs.pop("user_synonyms", {}),
        user_values=kwargs.pop("user_values", []),
        applies_when=kwargs.pop("applies_when", {}),
        is_active=kwargs.pop("is_active", True),
        source=kwargs.pop("source", "user"),
        **kwargs,
    )
    db.add(row)
    db.flush()
    return row


def _row(db: Session, spec_key: str):
    from app.models.product_spec import ProductSpecRegistry

    db.expire_all()
    return db.query(ProductSpecRegistry).filter_by(spec_key=spec_key).one()


@pytest.fixture
def api():
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.models.base import set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:
        _seed(db)

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db

        async def _override_scope():
            scope = frozenset({_SORENTO})
            set_company_scope(db, scope)
            return scope

        app.dependency_overrides[apply_company_scope] = _override_scope

        def _as(user_id: str):
            principal = {"id": user_id, "email": f"{user_id}@test.com"}
            app.dependency_overrides[get_current_user] = lambda: principal
            app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

        yield db, _as

        app.dependency_overrides.clear()


def _refused(response, word: str, other_label: str) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == _CODE
    assert body["message"] == f'"{word}" is already a word for {other_label}.'


# --------------------------------------------------------------------------- #
# PATCH user_synonyms
# --------------------------------------------------------------------------- #
def test_a_new_word_that_is_another_fields_own_word_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["trap"]})

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["trap"]}})

    _refused(response, "trap", "Beta pipe")
    assert _row(db, "zzt_a").user_synonyms == {}


def test_the_refusal_names_the_word_the_other_label_and_the_value_it_was_added_under(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["trap"]})

    own = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["trap"]}})
    under_value = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"chrome": ["trap"]}})
    new_value = client.patch(f"{_BASE}/zzt_a", json={"user_values": ["trap"]})
    posted = client.post(f"{_BASE}/zzt_a/values", json={"value": "trap"})

    assert own.json()["details"] == {"word": "trap", "other_label": "Beta pipe", "value": "_self"}
    assert under_value.json()["details"]["value"] == "chrome"
    assert new_value.json()["details"] == {"word": "trap", "other_label": "Beta pipe", "value": None}
    assert posted.json()["details"] == {"word": "trap", "other_label": "Beta pipe", "value": None}


def test_the_comparison_ignores_case_spacing_and_separators(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["trap outlet"]})

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["TRAP-Outlet"]}})

    _refused(response, "TRAP-Outlet", "Beta pipe")
    assert _row(db, "zzt_a").user_synonyms == {}


def test_a_new_word_that_is_a_value_word_of_another_field_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(
        db,
        "zzt_b",
        label="Beta finish",
        data_type="enum",
        unit=None,
        allowed_values=["chrome"],
        synonyms={"chrome": ["shiny"]},
    )

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["shiny"]}})

    _refused(response, "shiny", "Beta finish")


def test_a_new_word_that_is_another_fields_label_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(db, "zzt_b", label="Beta pipe")

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["beta pipe"]}})

    _refused(response, "beta pipe", "Beta pipe")
    assert _row(db, "zzt_a").user_synonyms == {}


def test_a_new_word_that_is_another_fields_key_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(db, "zzt_beta_pipe", label="Something else entirely")

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["zzt beta pipe"]}})

    _refused(response, "zzt beta pipe", "Something else entirely")


def test_a_word_already_on_the_row_re_sent_in_the_same_patch_saves(api):
    """AC-2.2: a shipped overlap keeps saving; only the NEW word is judged."""
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length", synonyms={"_self": ["trap"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["trap"]})

    response = client.patch(
        f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["trap", "fresh word"]}}
    )

    assert response.status_code == 200, response.text
    assert "fresh word" in response.json()["synonyms"]["_self"]


def test_a_user_word_already_stored_re_sent_in_the_same_patch_saves(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length", user_synonyms={"_self": ["trap"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["trap"]})

    response = client.patch(
        f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["trap", "fresh word"]}}
    )

    assert response.status_code == 200, response.text


def test_a_word_suppressed_on_the_other_field_does_not_block(api):
    """AC-2.3."""
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(
        db,
        "zzt_b",
        label="Beta pipe",
        synonyms={"_self": ["trap"]},
        suppressed_synonyms={"_self": ["trap"]},
    )

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["trap"]}})

    assert response.status_code == 200, response.text
    assert "trap" in response.json()["synonyms"]["_self"]


def test_an_inactive_other_field_still_blocks(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["trap"]}, is_active=False)

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["trap"]}})

    _refused(response, "trap", "Beta pipe")


def test_a_word_equal_to_the_rows_own_label_is_not_another_field(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length")
    _key(db, "zzt_b", label="Beta pipe")

    response = client.patch(f"{_BASE}/zzt_a", json={"user_synonyms": {"_self": ["alpha length"]}})

    assert response.status_code == 200, response.text


def test_a_refused_save_stores_nothing_from_the_same_payload(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha length", rank_weight=1.0)
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["trap"]})

    response = client.patch(
        f"{_BASE}/zzt_a",
        json={
            "label": "Renamed alpha",
            "rank_weight": 9.0,
            "parser_hint": "should not land",
            "user_synonyms": {"_self": ["fine word", "trap"]},
        },
    )

    assert response.status_code == 409, response.text
    row = _row(db, "zzt_a")
    assert row.user_synonyms == {}
    assert row.label == "Alpha length"
    assert float(row.rank_weight) == 1.0
    assert row.parser_hint is None
    assert row.label_owned is False


# --------------------------------------------------------------------------- #
# PATCH user_values
# --------------------------------------------------------------------------- #
def test_a_new_value_that_is_another_fields_word_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["shiny"]})

    response = client.patch(f"{_BASE}/zzt_a", json={"user_values": ["shiny"]})

    _refused(response, "shiny", "Beta pipe")
    assert _row(db, "zzt_a").user_values == []


def test_a_new_value_that_is_another_fields_label_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(db, "zzt_b", label="Beta pipe")

    response = client.patch(f"{_BASE}/zzt_a", json={"user_values": ["Beta pipe"]})

    _refused(response, "Beta pipe", "Beta pipe")
    assert _row(db, "zzt_a").user_values == []


def test_a_refused_new_value_leaves_a_same_payload_suppression_unapplied(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome", "gold"], synonyms={"chrome": ["chrome"], "gold": ["gold"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["shiny"]})

    response = client.patch(
        f"{_BASE}/zzt_a", json={"suppressed_values": ["gold"], "user_values": ["shiny"]}
    )

    _refused(response, "shiny", "Beta pipe")
    db.expire_all()
    assert _row(db, "zzt_a").suppressed_values == []
    assert not db.dirty


def test_a_value_already_held_re_sent_with_a_new_one_saves(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(
        db,
        "zzt_a",
        label="Alpha finish",
        data_type="enum",
        unit=None,
        allowed_values=["chrome"],
        synonyms={"chrome": ["chrome"]},
        user_values=["gunmetal"],
    )
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["gunmetal"]})

    response = client.patch(
        f"{_BASE}/zzt_a", json={"user_values": ["gunmetal", "brushed_brass"]}
    )

    assert response.status_code == 200, response.text
    assert "brushed_brass" in response.json()["user_values"]


# --------------------------------------------------------------------------- #
# POST /{spec_key}/values
# --------------------------------------------------------------------------- #
def test_adding_a_value_that_is_another_fields_word_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["shiny"]})

    response = client.post(f"{_BASE}/zzt_a/values", json={"value": "shiny"})

    _refused(response, "shiny", "Beta pipe")
    assert _row(db, "zzt_a").user_values == []


def test_a_merchandiser_adding_a_value_is_held_to_the_same_guard(api):
    db, _as = api
    _as(_MERCHANDISER)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["shiny"]})

    response = client.post(f"{_BASE}/zzt_a/values", json={"value": "Shiny"})

    _refused(response, "Shiny", "Beta pipe")


def test_adding_a_value_that_is_another_fields_label_is_refused(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(db, "zzt_b", label="Beta pipe")

    response = client.post(f"{_BASE}/zzt_a/values", json={"value": "beta pipe"})

    _refused(response, "beta pipe", "Beta pipe")


def test_adding_a_value_that_is_free_still_works(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(db, "zzt_b", label="Beta pipe", synonyms={"_self": ["shiny"]})

    response = client.post(f"{_BASE}/zzt_a/values", json={"value": "gunmetal"})

    assert response.status_code == 200, response.text
    assert "gunmetal" in response.json()["user_values"]


def test_adding_a_value_whose_name_is_suppressed_on_the_other_field_works(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha finish", data_type="enum", unit=None, allowed_values=["chrome"], synonyms={"chrome": ["chrome"]})
    _key(
        db,
        "zzt_b",
        label="Beta pipe",
        synonyms={"_self": ["shiny"]},
        suppressed_synonyms={"_self": ["shiny"]},
    )

    response = client.post(f"{_BASE}/zzt_a/values", json={"value": "shiny"})

    assert response.status_code == 200, response.text
