"""PV2-RESOLVE aliases + precedence (CONTRACT-PV2-13oct section 4, Aliases). No LLM, no network."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.services.chatbot.pv2 import aliases as A
from app.services.error_handler import AppException

from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def precedence(db, kind, alias, winner):
    db.execute(
        text("INSERT INTO chatbot_alias_precedence (id, kind, alias_norm, winner_key) VALUES (:i, :k, :a, :w)"),
        {"i": str(uuid.uuid4()), "k": kind, "a": alias, "w": winner},
    )
    db.flush()


def two_types(db, tech=("td", "drawing"), photos=("photo",)):
    S.attachment_type(db, "technical_specifications", "Technical Specifications", synonyms=tech)
    S.attachment_type(db, "product_photos", "Product Photos", synonyms=photos)


# --- pure helpers (13: find_clashes / winners) ---------------------------------
def test_norm_casefolds_and_collapses_whitespace():
    assert A.norm("  Foo   BAR ") == "foo bar"


def test_find_clashes_table():
    amap = {"a": ["td", "x"], "b": ["TD", "y"], "c": ["z"]}
    assert [(c.alias, c.keys) for c in A.find_clashes("attachment_type", amap, {})] == [("td", ("a", "b"))]
    assert A.find_clashes("attachment_type", amap, {"td": "a"}) == []
    # a precedence row naming a key that does not own the word settles nothing
    assert len(A.find_clashes("attachment_type", amap, {"td": "c"})) == 1


def test_winners_prints_shared_word_only_under_winner_and_drops_unsettled():
    amap = {"a": ["td", "x", "u"], "b": ["td", "y", "u"]}
    out = A.winners(amap, {"td": "b"})
    assert out == {"a": ["x"], "b": ["td", "y"]}


# --- check_conflicts ----------------------------------------------------------
def test_check_conflicts_refuses_new_alias_under_another_key(db):
    two_types(db)
    clashes = A.check_conflicts(db, "attachment_type", "product_photos", ["TD"])
    assert len(clashes) == 1
    assert clashes[0].alias == "td"
    assert set(clashes[0].keys) == {"technical_specifications", "product_photos"}


def test_check_conflicts_passes_a_fresh_word(db):
    two_types(db)
    assert A.check_conflicts(db, "attachment_type", "product_photos", ["picture"]) == []


def test_check_conflicts_passes_when_precedence_names_a_winner_among_the_keys(db):
    two_types(db)
    precedence(db, "attachment_type", "td", "technical_specifications")
    assert A.check_conflicts(db, "attachment_type", "product_photos", ["td"]) == []


def test_check_conflicts_ignores_words_already_in_previous(db):
    two_types(db, tech=("td",), photos=("td",))  # an existing, unsettled clash
    assert A.check_conflicts(db, "attachment_type", "product_photos", ["td", "picture"], previous=["TD"]) == []


def test_check_conflicts_same_key_is_not_a_clash(db):
    two_types(db)
    assert A.check_conflicts(db, "attachment_type", "technical_specifications", ["td"]) == []


def test_check_conflicts_product_class_kind_keys_by_class_slug(db):
    # The index's PRODUCT CLASSES key: slug of the class label (pv2/index.py product_class_key).
    S.category(db, class_label="Water Closet", synonyms=["wc"])
    S.category(db, class_label="Basin", synonyms=["sink"])
    clashes = A.check_conflicts(db, "product_class", "basin", ["WC"])
    assert len(clashes) == 1 and set(clashes[0].keys) == {"water_closet", "basin"}


def test_product_class_words_of_an_unsearchable_category_are_not_aliases(db):
    S.category(db, class_label="Water Closet", synonyms=["wc"], searchable=False)
    assert A.check_conflicts(db, "product_class", "basin", ["wc"]) == []


def test_check_conflicts_spec_attribute_kind_self_words_and_inactive_rows_ignored(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm", synonyms={"_self": ["panjang"]})
    S.registry(db, "dim_old", "Old", "numeric", unit="mm", synonyms={"_self": ["lama"]}, active=False)
    live = A.check_conflicts(db, "spec_attribute", "dim_height", ["panjang"])
    assert len(live) == 1 and set(live[0].keys) == {"dim_length", "dim_height"}
    assert A.check_conflicts(db, "spec_attribute", "dim_height", ["lama"]) == []


def test_enum_value_words_are_not_index_aliases(db):
    # The index prints an attribute's own words only, so a value word never clashes.
    S.registry(db, "trap_type", "Trap Type", "enum", allowed=["p_trap"], synonyms={"p_trap": ["p-bend"]})
    assert A.check_conflicts(db, "spec_attribute", "outlet", ["p-bend"]) == []


def test_refuse_conflicts_raises_409_naming_the_clash(db):
    two_types(db)
    with pytest.raises(AppException) as exc:
        A.refuse_conflicts(db, "attachment_type", "product_photos", ["td"])
    assert exc.value.status_code == 409
    assert "td" in str(exc.value.message).casefold()
    A.refuse_conflicts(db, "attachment_type", "product_photos", ["picture"])  # no raise


# --- open_clashes / index_aliases ---------------------------------------------
def test_open_clashes_lists_unsettled_and_not_settled(db):
    S.category(db, class_label="Basin", synonyms=["sinki"])
    S.category(db, class_label="Kitchen Sink", synonyms=["sinki"])
    mine = [c for c in A.open_clashes(db) if c.kind == "product_class"]
    assert [(c.alias, set(c.keys)) for c in mine] == [("sinki", {"basin", "kitchen_sink"})]
    precedence(db, "product_class", "sinki", "kitchen_sink")
    assert [c for c in A.open_clashes(db) if c.kind == "product_class"] == []


def test_attachment_type_aliases_stay_in_data_and_never_block_a_publish(db):
    two_types(db, tech=("cad", "td"), photos=("cad",))
    assert [c for c in A.open_clashes(db) if c.kind == "attachment_type"] == []
    # A staff save still refuses the shared word, so the data stays clean.
    assert A.check_conflicts(db, "attachment_type", "product_photos", ["td"])


def test_index_aliases_prints_shared_word_only_under_winner_and_drops_unsettled(db):
    two_types(db, tech=("td", "cad", "drawing"), photos=("td", "cad", "picture"))
    precedence(db, "attachment_type", "td", "technical_specifications")
    out = A.index_aliases(db, "attachment_type")
    assert sorted(out["technical_specifications"]) == ["drawing", "td"]  # cad unsettled: dropped everywhere
    assert out["product_photos"] == ["picture"]
