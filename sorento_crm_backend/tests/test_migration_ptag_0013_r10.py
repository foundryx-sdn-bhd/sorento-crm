"""Migration ``ptag_0013_r10`` (PLAN-price-tag-r10.md, "Migration ptag_0013_r10").

Written test-FIRST (PRINCIPLES.md Phase 2): the revision file does not exist
yet, so the glob in ``_load_migration`` finds nothing and every test in this
module fails at fixture setup with an ``AssertionError`` - the right red for a
migration that has not been written, mirroring
``tests/test_price_tag_data_pin.py::_load_pins_migration``.

Everything the schema half needs is ALREADY missing from the live ORM models
(the coder has not touched them yet either), so ``blank_session()``'s
``create_all`` already builds the PRE-migration shape - no manual column-drop
step is needed the way ``test_migration_324_grn_line_spo_number_raw.py``
needed one.

Covers:

- AC-S1-4  ``price_tag_requests.print_by`` NULL -> ``self``; ``office`` rows kept.
- AC-S2-5  a version-2 prompt is inserted only when version 1 is byte-for-byte
  the pre-r10 fallback; an owner-edited version 1 is left alone.
- AC-S4-1  ``products.price_tag_description`` nullable text.
- AC-S5-1  ``product_combos.image_attachment_id`` FK ``attachments(id)`` SET
  NULL on delete; attachment type ``Combo Image`` (code ``combo_image``)
  seeded, idempotently.
- AC-S6-1  ``price_tag_request_tags.print_excluded`` boolean, NOT NULL, default false.
- AC-S7-11 ``dealer_kit.tag_size_preset`` gains ``sheet_cols``, ``sheet_rows``,
  ``sheet_turn``.
- AC-S8-1  ``price_tag_request_tags.data_updated_at`` / ``data_update_changes``
  / ``data_update_version``.
- Downgrade drops the S1/S4/S6/S8 columns (the attachment TYPE row stays, per
  the plan: "the type row stays if any attachment uses it").
"""
from __future__ import annotations

import glob
import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests._pg_fixture import blank_session, unique_code

# The EXACT text `_ai_extract_price_tag_fallback()` returned before S2 touched
# it (captured 20 Sep 2026, one call, pinned as a literal). A migration must
# never import live application code (LESSONS-LEARNT: `340_scm_committed_reads_
# the_decision`), so the data step - and this test - compare against a frozen
# string, never the live function, which S2 changes in the same lane.
_PRE_R10_FALLBACK = (
    "You are an information-extraction assistant. The user uploads documents "
    "(delivery orders, photos, message screenshots, PDFs). Read every "
    "attachment and return a single JSON object whose top-level keys are the "
    "form field names below. Rules: (1) Omit any field you cannot find or are "
    "not confident about - do not guess. (2) For fields with a `lookup`, "
    "return ONLY one of the listed option `value` strings, exactly. (3) For "
    "`do_number` fields with `multi: true`, return an array of strings. (4) "
    "For `date` fields, use ISO-8601 (YYYY-MM-DD). (5) For `fk_product`, "
    "return the closest-matching product_code from the supplied examples; if "
    "nothing matches, return the raw code as printed in the document. If "
    "multiple distinct product codes apply, return them as a single "
    'comma-separated string (e.g. "TPE-9201, TPE-9203") - never as a JSON '
    "array. (6) For `text`, `textarea`, and `fk_customer` fields, if the "
    "document shows multiple distinct values for the same field, return them "
    "as a single comma-separated string. (7) Never invent values. Never "
    "include explanations or prose. (8) When the form has line items, "
    "`products` is REQUIRED whenever the document shows any product code: "
    "every product code line is one entry, even when no quantity, price or "
    "name is shown. Return an empty `products` array only when no product "
    "code appears anywhere."
)

PROMPT_NAME = "ai_extract_portal_price_tag_request"

# The exact value list `402_attachments_entity_type_allow_supplier_stock_
# list.py` (the most recent widening of `attachments_entity_type_check`
# before this lane) left in the real database - AC-S5-13.
_PRE_R10_ENTITY_TYPES = (
    "product",
    "promotion",
    "complaint",
    "general",
    "complaint_document",
    "order",
    "stock_list",
    "form",
    "inbound_shipment",
    "dealer_kit_asset",
    "project",
    "project_lead",
    "supplier_stock_list",
)


def _load_migration():
    matches = sorted(
        glob.glob(
            str(
                Path(__file__).resolve().parent.parent
                / "alembic"
                / "versions"
                / "*ptag_0013_r10*.py"
            )
        )
    )
    assert matches, "no alembic revision matching '*ptag_0013_r10*.py'"
    spec = importlib.util.spec_from_file_location(
        f"migration_{Path(matches[-1]).stem}", matches[-1]
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pre_migration_shape(db) -> None:
    """Undo what ``blank_session()``'s ``create_all`` already emitted from the
    ORM models - the coder's slice added the r10 columns to the model
    classes, so the blank schema is built at the POST-migration shape and
    the migration's own ``ADD COLUMN`` collides with a column that is
    already there. Mirrors
    ``test_migration_324_grn_line_spo_number_raw.py``'s own
    ``_pre_migration_shape`` (same gap, same fix): drop everything this ONE
    migration adds, `IF EXISTS` so a caller can run it defensively, then let
    ``upgrade()`` add it all back for real.

    ``tag_size_preset`` is unqualified, the same way the migration's own
    ``op.add_column(..., schema=_dealer_kit_schema(conn))`` resolves it -
    the scratch schema's ``dealer_kit`` sibling is already on this
    connection's ``search_path`` (`tests/_pg_fixture.py`), so a literal
    ``dealer_kit.`` prefix here would name the wrong (real) schema instead.
    """
    db.execute(text("ALTER TABLE products DROP COLUMN IF EXISTS price_tag_description"))
    db.execute(text("ALTER TABLE product_combos DROP COLUMN IF EXISTS image_attachment_id"))
    db.execute(text("ALTER TABLE price_tag_request_tags DROP COLUMN IF EXISTS print_excluded"))
    db.execute(text("ALTER TABLE price_tag_request_tags DROP COLUMN IF EXISTS data_updated_at"))
    db.execute(text("ALTER TABLE price_tag_request_tags DROP COLUMN IF EXISTS data_update_changes"))
    db.execute(text("ALTER TABLE price_tag_request_tags DROP COLUMN IF EXISTS data_update_version"))
    db.execute(text("ALTER TABLE tag_size_preset DROP COLUMN IF EXISTS sheet_cols"))
    db.execute(text("ALTER TABLE tag_size_preset DROP COLUMN IF EXISTS sheet_rows"))
    db.execute(text("ALTER TABLE tag_size_preset DROP COLUMN IF EXISTS sheet_turn"))
    # Nothing seeds this row on `create_all` (only the migration itself
    # does), but drop it defensively too - the idempotency test re-runs the
    # seed step and must start from "not there" like every other assertion
    # in this file does.
    db.execute(text("DELETE FROM attachment_types WHERE code = 'combo_image'"))
    # AC-S5-13 (captain's ruling, phase 3 review): `create_all` never builds
    # a CHECK constraint at all (SQLAlchemy models do not declare one), so
    # the scratch schema stayed green while the real DB 500s on
    # `attachments_entity_type_check` - the constraint never got the new
    # `product_combo_image` value the r10 combo image upload writes.
    # Recreated here at the PRE-r10 shape - the value list `402_attachments_
    # entity_type_allow_supplier_stock_list.py` (the most recent widening
    # before this lane) left the real database in.
    db.execute(
        text("ALTER TABLE attachments DROP CONSTRAINT IF EXISTS attachments_entity_type_check")
    )
    db.execute(
        text(
            "ALTER TABLE attachments ADD CONSTRAINT attachments_entity_type_check CHECK ("
            "entity_type IS NULL OR entity_type IN ("
            + ", ".join(f"'{v}'" for v in _PRE_R10_ENTITY_TYPES)
            + "))"
        )
    )


def _run_upgrade(db):
    _pre_migration_shape(db)
    module = _load_migration()
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        module.upgrade()
    return module


def _run_downgrade(db):
    module = _load_migration()
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        module.downgrade()


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Seeding - the FK chains Postgres actually enforces.
# ---------------------------------------------------------------------------


def _product(db) -> str:
    cat, uom, pid = _uid(), _uid(), _uid()
    db.execute(
        text(
            "INSERT INTO product_categories (id, category_code, category_name) "
            "VALUES (:i, :c, 'ZZT r10 category')"
        ),
        {"i": cat, "c": unique_code("C")[:50]},
    )
    db.execute(
        text(
            "INSERT INTO units_of_measure (id, uom_code, uom_name) "
            "VALUES (:i, :c, 'Each')"
        ),
        {"i": uom, "c": unique_code("U")[:20]},
    )
    db.execute(
        text(
            "INSERT INTO products (id, product_code, product_name, category_id, "
            "base_uom_id, list_price, is_active) "
            "VALUES (:i, :c, 'ZZT r10 product', :cat, :uom, 10, true)"
        ),
        {"i": pid, "c": unique_code("P")[:50], "cat": cat, "uom": uom},
    )
    return pid


def _contact(db) -> str:
    cid = _uid()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, phone_number, name) "
            "VALUES (:i, :p, 'ZZT r10 contact')"
        ),
        {"i": cid, "p": f"+60{uuid.uuid4().hex[:9]}"},
    )
    return cid


def _price_tag_request(db, contact_id: str, product_id: str, *, print_by) -> str:
    rid = _uid()
    db.execute(
        text(
            "INSERT INTO price_tag_requests (id, contact_id, debtor_name, "
            "status, print_by, doc_number) "
            "VALUES (:i, :c, 'ZZT Dealer', 'new', :pb, :doc)"
        ),
        {"i": rid, "c": contact_id, "pb": print_by, "doc": unique_code("PT")[:20]},
    )
    line_id = _uid()
    db.execute(
        text(
            "INSERT INTO price_tag_request_lines (id, request_id, line_type, "
            "product_id, sort_order, quantity) "
            "VALUES (:i, :r, 'product', :p, 0, 1)"
        ),
        {"i": line_id, "r": rid, "p": product_id},
    )
    tag_id = _uid()
    db.execute(
        text(
            "INSERT INTO price_tag_request_tags (id, line_id, sort_order, quantity) "
            "VALUES (:i, :l, 0, 1)"
        ),
        {"i": tag_id, "l": line_id},
    )
    return rid, tag_id


def _combo(db, host_product_id: str) -> str:
    combo_id = _uid()
    db.execute(
        text(
            "INSERT INTO product_combos (id, host_product_id, name, sort_order) "
            "VALUES (:i, :h, :n, 0)"
        ),
        {"i": combo_id, "h": host_product_id, "n": unique_code("combo")[:50]},
    )
    return combo_id


def _attachment(db) -> str:
    aid = _uid()
    name = unique_code("zztimg")
    db.execute(
        text(
            "INSERT INTO attachments (id, original_filename, stored_filename, "
            "file_path, mime_type, storage_provider, is_deleted) "
            "VALUES (:i, :f, :f, :p, 'image/jpeg', 's3', false)"
        ),
        {"i": aid, "f": f"{name}.jpg", "p": f"products/{name}.jpg"},
    )
    return aid


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


# ---------------------------------------------------------------------------
# AC-S1-4
# ---------------------------------------------------------------------------


def test_null_print_by_backfills_to_self_office_rows_untouched(db):
    product = _product(db)
    contact = _contact(db)
    null_request, _ = _price_tag_request(db, contact, product, print_by=None)
    office_request, _ = _price_tag_request(db, contact, product, print_by="office")
    db.commit()

    _run_upgrade(db)

    rows = db.execute(
        text("SELECT id, print_by FROM price_tag_requests WHERE id IN (:a, :b)"),
        {"a": null_request, "b": office_request},
    ).mappings().all()
    # `id` comes back as a native `UUID` from the driver, not the plain str
    # `_uid()` handed the INSERT - stringify the key or the lookup below
    # KeyErrors on a type mismatch that has nothing to do with the backfill.
    by_id = {str(row["id"]): row["print_by"] for row in rows}
    assert by_id[null_request] == "self"
    assert by_id[office_request] == "office"


# ---------------------------------------------------------------------------
# AC-S2-5
# ---------------------------------------------------------------------------


def _seed_prompt_version(db, *, version: int, template: str) -> str:
    version_id = _uid()
    db.execute(
        text(
            "INSERT INTO ai_prompt_versions (id, name, version, type, template, variables) "
            "VALUES (:i, :n, :v, 'text', :t, '[]'::jsonb)"
        ),
        {"i": version_id, "n": PROMPT_NAME, "v": version, "t": template},
    )
    return version_id


def _seed_production_label(db, version_id: str) -> None:
    db.execute(
        text(
            "INSERT INTO ai_prompt_labels (id, name, label, version_id) "
            "VALUES (:i, :n, 'production', :v)"
        ),
        {"i": _uid(), "n": PROMPT_NAME, "v": version_id},
    )


def _production_template(db) -> str | None:
    return db.execute(
        text(
            "SELECT v.template FROM ai_prompt_labels l "
            "JOIN ai_prompt_versions v ON v.id = l.version_id "
            "WHERE l.name = :n AND l.label = 'production'"
        ),
        {"n": PROMPT_NAME},
    ).scalar()


def test_a_stock_untouched_v1_prompt_moves_production_to_a_new_rule_9_version(db):
    """AC-S2-5 (amended): a version-2 row nobody's `production` label points
    at never reaches a live extract call - the fix has to move the LABEL,
    not just insert a row."""
    v1_id = _seed_prompt_version(db, version=1, template=_PRE_R10_FALLBACK)
    _seed_production_label(db, v1_id)
    db.commit()

    _run_upgrade(db)

    versions = db.execute(
        text("SELECT version, template FROM ai_prompt_versions WHERE name = :n ORDER BY version"),
        {"n": PROMPT_NAME},
    ).mappings().all()
    assert [row["version"] for row in versions] == [1, 2], versions
    assert versions[1]["template"] != versions[0]["template"]

    production_text = _production_template(db)
    assert production_text is not None
    assert "(9)" in production_text
    assert "quantity" in production_text.lower()


def test_an_owner_edited_v1_prompt_is_left_alone(db):
    v1_id = _seed_prompt_version(db, version=1, template="The owner's own edited prompt text.")
    _seed_production_label(db, v1_id)
    db.commit()

    _run_upgrade(db)

    versions = db.execute(
        text("SELECT version FROM ai_prompt_versions WHERE name = :n"),
        {"n": PROMPT_NAME},
    ).mappings().all()
    assert [row["version"] for row in versions] == [1], (
        "an owner edit must never be silently superseded"
    )
    assert _production_template(db) == "The owner's own edited prompt text."


def test_a_pre_existing_v2_does_not_collide_and_production_ends_on_the_rule_9_text(db):
    """AC-S2-5 (amended): the migration must never hardcode `version = 2` -
    an owner (or an earlier bump like `272_ideate_intent_parser_prompt.py`'s
    own mechanism) may already have published one for an unrelated reason,
    and a literal insert of `version = 2` collides on
    `uq_ai_prompt_versions_name_version`. `production` still names the
    pre-r10 text (nobody has re-published since), so the migration must
    still publish the rule-9 fallback - at whatever version number is next
    free - and move the label onto it.
    """
    v1_id = _seed_prompt_version(db, version=1, template=_PRE_R10_FALLBACK)
    # An unrelated v2 an owner saved by hand, carrying neither the pre-r10
    # text nor rule (9) - `production` still points at v1.
    _seed_prompt_version(db, version=2, template="An owner's own unrelated v2 edit.")
    _seed_production_label(db, v1_id)
    db.commit()

    _run_upgrade(db)

    production_text = _production_template(db)
    assert production_text is not None
    assert "(9)" in production_text
    assert "quantity" in production_text.lower()


def test_no_stored_prompt_at_all_inserts_nothing(db):
    """A tenant that never diverged from the seed has no v1 row to compare
    against - the migration must not invent one out of nothing."""
    db.commit()

    _run_upgrade(db)

    count = db.execute(
        text("SELECT count(*) FROM ai_prompt_versions WHERE name = :n"), {"n": PROMPT_NAME}
    ).scalar()
    assert count == 0


# ---------------------------------------------------------------------------
# AC-S4-1
# ---------------------------------------------------------------------------


def test_products_gain_a_nullable_price_tag_description_column(db):
    product = _product(db)
    db.commit()

    _run_upgrade(db)

    value = db.execute(
        text("SELECT price_tag_description FROM products WHERE id = :i"), {"i": product}
    ).scalar()
    assert value is None

    db.execute(
        text("UPDATE products SET price_tag_description = 'ZZT tag text' WHERE id = :i"),
        {"i": product},
    )
    stored = db.execute(
        text("SELECT price_tag_description FROM products WHERE id = :i"), {"i": product}
    ).scalar()
    assert stored == "ZZT tag text"


def test_downgrade_drops_price_tag_description(db):
    _run_upgrade(db)
    _run_downgrade(db)

    exists = db.execute(
        text(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'products' "
            "AND column_name = 'price_tag_description' AND table_schema = current_schema()"
        )
    ).scalar()
    assert exists is None


# ---------------------------------------------------------------------------
# AC-S5-1
# ---------------------------------------------------------------------------


def test_product_combos_gain_image_attachment_id_set_null_on_delete(db):
    host = _product(db)
    combo_id = _combo(db, host)
    attachment_id = _attachment(db)
    db.commit()

    _run_upgrade(db)

    db.execute(
        text("UPDATE product_combos SET image_attachment_id = :a WHERE id = :c"),
        {"a": attachment_id, "c": combo_id},
    )
    db.execute(text("DELETE FROM attachments WHERE id = :a"), {"a": attachment_id})

    remaining = db.execute(
        text("SELECT image_attachment_id FROM product_combos WHERE id = :c"), {"c": combo_id}
    ).scalar()
    assert remaining is None, "the FK must be ON DELETE SET NULL, not a hard block"


def test_combo_image_attachment_type_is_seeded_idempotently(db):
    _run_upgrade(db)

    rows = db.execute(
        text("SELECT type_name, allowed_extensions FROM attachment_types WHERE code = 'combo_image'")
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["type_name"] == "Combo Image"

    # Re-running the seed step must not create a second row.
    module = _load_migration()
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        if hasattr(module, "_seed_combo_image_type"):
            module._seed_combo_image_type()
        else:
            module.upgrade()

    count = db.execute(
        text("SELECT count(*) FROM attachment_types WHERE code = 'combo_image'")
    ).scalar()
    assert count == 1


def test_downgrade_drops_image_attachment_id_but_keeps_the_type_row(db):
    _run_upgrade(db)
    _run_downgrade(db)

    column_gone = db.execute(
        text(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'product_combos' "
            "AND column_name = 'image_attachment_id' AND table_schema = current_schema()"
        )
    ).scalar()
    assert column_gone is None

    type_still_there = db.execute(
        text("SELECT 1 FROM attachment_types WHERE code = 'combo_image'")
    ).scalar()
    assert type_still_there is not None, (
        "the plan says the type row stays even after a downgrade, since an "
        "attachment may still reference it"
    )


# ---------------------------------------------------------------------------
# AC-S6-1
# ---------------------------------------------------------------------------


def test_print_excluded_defaults_false_not_null(db):
    product = _product(db)
    contact = _contact(db)
    _request_id, tag_id = _price_tag_request(db, contact, product, print_by="self")
    db.commit()

    _run_upgrade(db)

    value = db.execute(
        text("SELECT print_excluded FROM price_tag_request_tags WHERE id = :i"), {"i": tag_id}
    ).scalar()
    assert value is False

    not_null = db.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns WHERE table_name = "
            "'price_tag_request_tags' AND column_name = 'print_excluded' "
            "AND table_schema = current_schema()"
        )
    ).scalar()
    assert not_null == "NO"


# ---------------------------------------------------------------------------
# AC-S7-11
# ---------------------------------------------------------------------------


def test_tag_size_preset_gains_sheet_columns(db):
    preset_id = _uid()
    db.execute(
        text(
            "INSERT INTO tag_size_preset (id, company_id, name, width_mm, height_mm) "
            "VALUES (:i, :cid, :n, 66.7, 31.9)"
        ),
        {"i": preset_id, "cid": "00000000-0000-0000-0000-000000000001", "n": unique_code("size")},
    )
    db.commit()

    _run_upgrade(db)

    row = db.execute(
        text(
            "SELECT sheet_cols, sheet_rows, sheet_turn FROM tag_size_preset WHERE id = :i"
        ),
        {"i": preset_id},
    ).mappings().first()
    assert row is not None
    assert row["sheet_cols"] is None
    assert row["sheet_rows"] is None
    assert row["sheet_turn"] is False

    db.execute(
        text(
            "UPDATE tag_size_preset SET sheet_cols = 3, sheet_rows = 9, sheet_turn = true "
            "WHERE id = :i"
        ),
        {"i": preset_id},
    )
    updated = db.execute(
        text("SELECT sheet_cols, sheet_rows, sheet_turn FROM tag_size_preset WHERE id = :i"),
        {"i": preset_id},
    ).mappings().first()
    assert (updated["sheet_cols"], updated["sheet_rows"], updated["sheet_turn"]) == (3, 9, True)


# ---------------------------------------------------------------------------
# AC-S8-1
# ---------------------------------------------------------------------------


def test_tags_gain_the_three_data_update_columns(db):
    product = _product(db)
    contact = _contact(db)
    _request_id, tag_id = _price_tag_request(db, contact, product, print_by="self")
    db.commit()

    _run_upgrade(db)

    row = db.execute(
        text(
            "SELECT data_updated_at, data_update_changes, data_update_version "
            "FROM price_tag_request_tags WHERE id = :i"
        ),
        {"i": tag_id},
    ).mappings().first()
    assert row["data_updated_at"] is None
    assert row["data_update_changes"] is None
    assert row["data_update_version"] is None


def test_downgrade_drops_the_s6_and_s8_tag_columns(db):
    _run_upgrade(db)
    _run_downgrade(db)

    for column in ("print_excluded", "data_updated_at", "data_update_changes", "data_update_version"):
        exists = db.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_name = "
                "'price_tag_request_tags' AND column_name = :c AND table_schema = current_schema()"
            ),
            {"c": column},
        ).scalar()
        assert exists is None, f"{column} should be gone after downgrade"


# ---------------------------------------------------------------------------
# AC-S5-13 (captain's ruling, phase 3 review): the browser run 500s on the
# real DB because `attachments_entity_type_check` never gained
# `product_combo_image` - the scratch schema has no such constraint at all
# (`create_all` never builds one), which is why pytest stayed green while
# the live upload failed.
# ---------------------------------------------------------------------------


def test_an_attachment_with_entity_type_product_combo_image_inserts_after_upgrade(db):
    _run_upgrade(db)

    aid = _uid()
    name = unique_code("zztcombo")
    db.execute(
        text(
            "INSERT INTO attachments (id, original_filename, stored_filename, "
            "file_path, mime_type, storage_provider, is_deleted, entity_type) "
            "VALUES (:i, :f, :f, :p, 'image/jpeg', 's3', false, 'product_combo_image')"
        ),
        {"i": aid, "f": f"{name}.jpg", "p": f"combos/{name}.jpg"},
    )
    stored = db.execute(
        text("SELECT entity_type FROM attachments WHERE id = :i"), {"i": aid}
    ).scalar()
    assert stored == "product_combo_image"


def test_downgrade_restores_the_402_entity_type_list(db):
    _run_upgrade(db)
    _run_downgrade(db)

    # The 402 list still works...
    aid = _uid()
    name = unique_code("zztstock")
    db.execute(
        text(
            "INSERT INTO attachments (id, original_filename, stored_filename, "
            "file_path, mime_type, storage_provider, is_deleted, entity_type) "
            "VALUES (:i, :f, :f, :p, 'image/jpeg', 's3', false, 'supplier_stock_list')"
        ),
        {"i": aid, "f": f"{name}.jpg", "p": f"stock/{name}.jpg"},
    )
    stored = db.execute(
        text("SELECT entity_type FROM attachments WHERE id = :i"), {"i": aid}
    ).scalar()
    assert stored == "supplier_stock_list"

    # ...but `product_combo_image` (the value THIS migration adds) must be
    # gone again, or the downgrade did not actually restore the 402 shape.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO attachments (id, original_filename, stored_filename, "
                "file_path, mime_type, storage_provider, is_deleted, entity_type) "
                "VALUES (:i, :f, :f, :p, 'image/jpeg', 's3', false, 'product_combo_image')"
            ),
            {"i": _uid(), "f": "x.jpg", "p": "x.jpg"},
        )

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
