"""SPEC-REGISTRY-UI Phase 2 red tests: the chatbot meaning (`parser_hint`), the owned
label (`label_owned`), the `also_on` note and the `Means:` text in `{{specs}}`.

Contract: `documentation/plans/chatbot/PLAN-spec-registry-ui-17oct.md`, section "Backend
contract", C1 (PATCH + serialise), C2 (seed), C3 (`specification_lines` + prompt var).
UAC: AC-1.1 to AC-1.5, AC-3.1, AC-3.2.

Red until `product_spec_registry.parser_hint` / `label_owned` exist on the model, the
PATCH schema accepts `parser_hint`, `_serialise` returns the new keys and
`specification_lines` prints `Means:`.

Postgres only (`tests._pg_fixture.blank_session`). Every row is seeded here. Auth-override
pattern from `test_spec_registry_pr2_routes.py`.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session

_MERCHANDISER = "3a51c7e2-6d48-5b19-9c02-7e1f4a8b3d60"
_MERCH_ROLE = "4b62d8f3-7e59-5c2a-8d13-8f2a5b9c4e71"
_REGISTRY_ADMIN = "5c73e904-8f6a-5d3b-9e24-9a3b6c0d5f82"
_ADMIN_ROLE = "6d84fa15-9a7b-5e4c-8f35-0b4c7d1e6a93"
_SORENTO = "00000000-0000-0000-0000-000000000001"

_BASE = "/api/v1/master-data/spec-registry"

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
        (_MERCH_ROLE, "zzt_hint_merchandiser", "ZZT Hint Merchandiser"),
        (_ADMIN_ROLE, "zzt_hint_registry_admin", "ZZT Hint Registry Admin"),
    ):
        db.add(
            UserRole(
                id=role_id, slug=slug, name=name, description="", is_protected=False, is_default=False
            )
        )
    db.add(User(id=_MERCHANDISER, email="zzt-hint-merch@test.com", name="Merch", status="ACTIVE"))
    db.add(User(id=_REGISTRY_ADMIN, email="zzt-hint-admin@test.com", name="Admin", status="ACTIVE"))
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
        data_type=kwargs.pop("data_type", "text"),
        unit=kwargs.pop("unit", None),
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


def _list_keys(client, _as) -> list[dict]:
    """The list read runs on `master_data.products.view` (the product page's grant), so
    read it as the merchandiser, then hand the registry admin back."""
    _as(_MERCHANDISER)
    try:
        response = client.get(_BASE)
        assert response.status_code == 200, response.text
        return response.json()["keys"]
    finally:
        _as(_REGISTRY_ADMIN)


@pytest.fixture(autouse=True)
def _fresh_prompt_cache():
    from app.services import chatbot_prompt_vars

    chatbot_prompt_vars.clear_cache()
    yield
    chatbot_prompt_vars.clear_cache()


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


# --------------------------------------------------------------------------- #
# C1 - parser_hint on PATCH (AC-1.2)
# --------------------------------------------------------------------------- #
def test_hint_is_stored_trimmed_on_one_line(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap outlet length", data_type="numeric", unit="mm")

    response = client.patch(
        f"{_BASE}/zzt_trap_length",
        json={"parser_hint": "  the length of\n the   outlet\tpipe  "},
    )

    assert response.status_code == 200, response.text
    assert response.json()["parser_hint"] == "the length of the outlet pipe"
    assert _row(db, "zzt_trap_length").parser_hint == "the length of the outlet pipe"


def test_hint_of_exactly_200_characters_saves(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")

    # Padding spaces are cleaned away BEFORE the count.
    hint = "x" * 200
    response = client.patch(f"{_BASE}/zzt_trap_length", json={"parser_hint": f"   {hint}   "})

    assert response.status_code == 200, response.text
    assert response.json()["parser_hint"] == hint


def test_hint_of_201_characters_is_refused_422_and_not_stored(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")

    response = client.patch(f"{_BASE}/zzt_trap_length", json={"parser_hint": "x" * 201})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "spec_registry_hint_too_long"
    assert _row(db, "zzt_trap_length").parser_hint is None


def test_control_characters_are_removed_from_the_hint(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")

    response = client.patch(
        f"{_BASE}/zzt_trap_length", json={"parser_hint": "outlet\u0000 pipe\u0007 length"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["parser_hint"] == "outlet pipe length"
    assert _row(db, "zzt_trap_length").parser_hint == "outlet pipe length"


def test_an_absurdly_long_hint_is_a_422_not_a_500(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")

    response = client.patch(f"{_BASE}/zzt_trap_length", json={"parser_hint": "x" * 5000})

    assert response.status_code == 422, response.text


def test_blank_hint_is_stored_as_null(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")
    assert (
        client.patch(f"{_BASE}/zzt_trap_length", json={"parser_hint": "something"}).status_code == 200
    )

    response = client.patch(f"{_BASE}/zzt_trap_length", json={"parser_hint": "  \n  "})

    assert response.status_code == 200, response.text
    assert response.json()["parser_hint"] is None
    assert _row(db, "zzt_trap_length").parser_hint is None


def test_products_edit_only_caller_cannot_set_a_hint(api):
    db, _as = api
    _as(_MERCHANDISER)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")

    response = client.patch(f"{_BASE}/zzt_trap_length", json={"parser_hint": "sneaky"})

    assert response.status_code == 403, response.text
    assert _row(db, "zzt_trap_length").parser_hint is None


def test_unit_is_still_not_editable(api):
    """`unit` stays out of `SpecKeyUpdate` (AC-4): a sent unit changes nothing."""
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")

    response = client.patch(
        f"{_BASE}/zzt_trap_length", json={"unit": "cm", "parser_hint": "keeps unit"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["parser_hint"] == "keeps unit"
    assert _row(db, "zzt_trap_length").unit == "mm"


# --------------------------------------------------------------------------- #
# C1 - label_owned, and the new keys on GET and PATCH
# --------------------------------------------------------------------------- #
def test_a_changed_label_marks_the_row_owned(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm", source="seed")

    response = client.patch(f"{_BASE}/zzt_trap_length", json={"label": "Trap outlet length"})

    assert response.status_code == 200, response.text
    assert response.json()["label"] == "Trap outlet length"
    assert response.json()["label_owned"] is True
    assert _row(db, "zzt_trap_length").label_owned is True


def test_resending_the_same_label_does_not_mark_the_row_owned(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm", source="seed")

    response = client.patch(
        f"{_BASE}/zzt_trap_length", json={"label": "Trap length", "rank_weight": 2.0}
    )

    assert response.status_code == 200, response.text
    assert response.json()["label_owned"] is False
    assert _row(db, "zzt_trap_length").label_owned is False


def test_patch_and_get_carry_hint_owned_and_also_on_defaults(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")

    patched = client.patch(f"{_BASE}/zzt_trap_length", json={"rank_weight": 2.0}).json()
    listed = {k["spec_key"]: k for k in _list_keys(client, _as)}["zzt_trap_length"]

    for body in (patched, listed):
        assert body["parser_hint"] is None
        assert body["label_owned"] is False
        assert body["also_on"] == {}


def test_get_carries_a_saved_hint(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap length", data_type="numeric", unit="mm")
    client.patch(f"{_BASE}/zzt_trap_length", json={"parser_hint": "outlet pipe length"})

    listed = {k["spec_key"]: k for k in _list_keys(client, _as)}["zzt_trap_length"]

    assert listed["parser_hint"] == "outlet pipe length"


def test_also_on_names_the_other_field_for_a_word_label_and_key_overlap(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    # A is the row under test. Its words: "length" (same as B's label), "trap" (a word of
    # C), "zzt other key" (normalises equal to D's spec_key) and "only mine" (no overlap).
    _key(
        db,
        "zzt_a",
        label="Trap length",
        data_type="numeric",
        unit="mm",
        synonyms={"_self": ["trap", "length", "zzt other key", "only mine"]},
    )
    _key(db, "zzt_b", label="Length", data_type="numeric", unit="mm")
    _key(db, "zzt_c", label="Pipe size", data_type="numeric", unit="mm", synonyms={"_self": ["Trap"]})
    _key(db, "zzt_other_key", label="Other thing", data_type="numeric", unit="mm")

    listed = {k["spec_key"]: k for k in _list_keys(client, _as)}["zzt_a"]

    assert listed["also_on"] == {
        "trap": ["Pipe size"],
        "length": ["Length"],
        "zzt other key": ["Other thing"],
    }
    assert "only mine" not in listed["also_on"]


def test_also_on_covers_value_words_and_comes_back_on_patch(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(
        db,
        "zzt_finish",
        label="Finish",
        data_type="enum",
        allowed_values=["chrome"],
        synonyms={"chrome": ["shiny"]},
    )
    _key(db, "zzt_look", label="Look", data_type="enum", allowed_values=["bright"], synonyms={"bright": ["shiny"]})

    patched = client.patch(f"{_BASE}/zzt_finish", json={"rank_weight": 3.0}).json()

    assert patched["also_on"] == {"shiny": ["Look"]}


def test_a_suppressed_word_on_the_other_field_is_not_in_also_on(api):
    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_a", label="Alpha", data_type="numeric", unit="mm", synonyms={"_self": ["shared"]})
    _key(
        db,
        "zzt_b",
        label="Beta",
        data_type="numeric",
        unit="mm",
        synonyms={"_self": ["shared"]},
        suppressed_synonyms={"_self": ["shared"]},
    )

    listed = {k["spec_key"]: k for k in _list_keys(client, _as)}["zzt_a"]

    assert listed["also_on"] == {}


# --------------------------------------------------------------------------- #
# C2 - seed_spec_registry
# --------------------------------------------------------------------------- #
def _seed_entry():
    from app.services.product_spec_registry import SPEC_REGISTRY_SEED

    return SPEC_REGISTRY_SEED[0]


def test_seed_keeps_an_owned_label_but_still_repairs_other_columns(api):
    from app.services.product_spec_registry import seed_spec_registry

    db, _ = api
    entry = _seed_entry()
    row = _key(
        db,
        entry["spec_key"],
        label="Owner's own name",
        data_type=entry["data_type"],
        synonyms={"zzt_drift": ["zzt drifted word"]},
        source="seed",
        label_owned=True,
    )
    db.commit()

    seed_spec_registry(db, commit=True)
    db.refresh(row)

    assert row.label == "Owner's own name"
    assert row.synonyms == entry.get("synonyms", {})


def test_seed_repairs_an_unowned_label(api):
    from app.services.product_spec_registry import seed_spec_registry

    db, _ = api
    entry = _seed_entry()
    row = _key(
        db,
        entry["spec_key"],
        label="Drifted label",
        data_type=entry["data_type"],
        source="seed",
        label_owned=False,
    )
    db.commit()

    seed_spec_registry(db, commit=True)
    db.refresh(row)

    assert row.label == entry["label"]


def test_seed_never_writes_parser_hint(api):
    from app.models.product_spec import ProductSpecRegistry
    from app.services.product_spec_registry import SPEC_REGISTRY_SEED, seed_spec_registry

    db, _ = api
    entry = _seed_entry()
    row = _key(
        db,
        entry["spec_key"],
        label="Drifted label",
        data_type=entry["data_type"],
        source="seed",
        parser_hint="the owner's own meaning",
    )
    db.commit()

    seed_spec_registry(db, commit=True)
    db.refresh(row)

    assert row.parser_hint == "the owner's own meaning"
    # Rows the seed CREATED carry no hint either.
    created = (
        db.query(ProductSpecRegistry)
        .filter(ProductSpecRegistry.spec_key != entry["spec_key"])
        .all()
    )
    assert len(created) == len(SPEC_REGISTRY_SEED) - 1
    assert all(r.parser_hint is None for r in created)


# --------------------------------------------------------------------------- #
# C3 - specification_lines prints `Means:`
# --------------------------------------------------------------------------- #
def _line(db: Session, spec_key: str) -> str:
    from app.services.chatbot_parser_prompt import specification_lines

    return next(l for l in specification_lines(db) if l.startswith(f"Specification {spec_key} ("))


def test_numeric_line_carries_means_after_the_type_phrase():
    with blank_session() as db:
        _key(
            db,
            "zzt_trap_length",
            label="Trap outlet length",
            data_type="numeric",
            unit="mm",
            synonyms={"_self": ["trap"]},
            parser_hint="the length of the outlet pipe",
        )
        assert _line(db, "zzt_trap_length") == (
            'Specification zzt_trap_length ("Trap outlet length"): a number in mm. '
            "Means: the length of the outlet pipe. Words: trap."
        )


def test_boolean_line_carries_means_before_words():
    with blank_session() as db:
        _key(
            db,
            "zzt_has_trap",
            label="Has trap",
            data_type="boolean",
            synonyms={"true": ["with trap"]},
            parser_hint="whether a trap is included",
        )
        assert _line(db, "zzt_has_trap") == (
            'Specification zzt_has_trap ("Has trap"): yes or no. '
            "Means: whether a trap is included. Words: with trap."
        )


def test_enum_line_carries_means_after_the_choices():
    with blank_session() as db:
        _key(
            db,
            "zzt_finish",
            label="Finish",
            data_type="enum",
            allowed_values=["chrome", "brushed_brass"],
            parser_hint="the surface colour",
        )
        line = _line(db, "zzt_finish")
        assert line.startswith('Specification zzt_finish ("Finish"): choices ')
        assert line.endswith(". Means: the surface colour.")
        assert line.count("Means:") == 1


def test_a_trailing_period_in_the_hint_is_not_doubled():
    with blank_session() as db:
        _key(
            db,
            "zzt_trap_length",
            label="Trap length",
            data_type="numeric",
            unit="mm",
            parser_hint="outlet pipe length.",
        )
        assert _line(db, "zzt_trap_length").endswith("a number in mm. Means: outlet pipe length.")


def test_quotes_in_the_hint_render_as_apostrophes():
    with blank_session() as db:
        _key(
            db,
            "zzt_trap_length",
            label="Trap length",
            data_type="numeric",
            unit="mm",
            parser_hint='the "S" trap length',
        )
        line = _line(db, "zzt_trap_length")
        assert "Means: the 'S' trap length." in line
        assert '"S"' not in line


@pytest.mark.parametrize("hint", [None, "", "   "])
def test_a_blank_hint_prints_nothing(hint):
    with blank_session() as db:
        _key(
            db,
            "zzt_trap_length",
            label="Trap length",
            data_type="numeric",
            unit="mm",
            synonyms={"_self": ["trap"]},
            parser_hint=hint,
        )
        line = _line(db, "zzt_trap_length")
        assert "Means" not in line
        assert line == 'Specification zzt_trap_length ("Trap length"): a number in mm. Words: trap.'


# --------------------------------------------------------------------------- #
# C3 / AC-1.4 - the `specs` prompt var shows a committed PATCH, no publish
# --------------------------------------------------------------------------- #
def test_specs_prompt_var_shows_the_new_hint_after_a_committed_patch(api):
    from app.services import chatbot_prompt_vars

    db, _as = api
    _as(_REGISTRY_ADMIN)
    client = TestClient(app)
    _key(db, "zzt_trap_length", label="Trap outlet length", data_type="numeric", unit="mm")
    db.commit()

    before = chatbot_prompt_vars.render_value(db, "specs")  # fills the cache
    assert "zzt_trap_length" in before
    assert "Means" not in before

    response = client.patch(
        f"{_BASE}/zzt_trap_length", json={"parser_hint": "length of the outlet pipe"}
    )
    assert response.status_code == 200, response.text

    after = chatbot_prompt_vars.render_value(db, "specs")
    assert "Means: length of the outlet pipe." in after
