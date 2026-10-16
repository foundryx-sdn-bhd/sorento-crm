"""Fix lane round 2 on PR #1302 (#1286): the reviewer pass at ea0b804b, backend half.

One test (or more) per finding, named by the finding's id: B-1 to B-3 (blocking),
S-1 to S-4 and S-7 to S-10 and S-14 (should fix), N-1 to N-6 (nits). Each was run red
against ea0b804b first, for the reviewer's failing scenario, and then made green.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.error_handler import AppException
from tests._pg_fixture import blank_session, pg_session, unique_code

BASE = "/api/v1/master-data/spec-registry"
BUSY = "Products are still being updated from another change. Try again in a moment."
NO_LETTER = "Rule 1: each word needs more than dots and dashes."
_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _uid() -> str:
    return str(uuid.uuid4())


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(monkeypatch):
    """The API over a blank schema, every grant on, re-reads run on the test's session."""
    import app.services.product_spec_change_listener as listener
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    with blank_session() as db:

        def _inline(codes):
            from app.services.product_spec_derivation import derive_for_code

            for code in codes:
                derive_for_code(db, code)

        monkeypatch.setattr(listener, "_rederive_inline", _inline)
        app.dependency_overrides[get_db] = lambda: db
        user = {"id": _uid(), "email": "round2@example.com"}
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_current_user_or_api_key] = lambda: user
        app.dependency_overrides[apply_company_scope] = lambda: None
        monkeypatch.setattr(
            UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
        )
        monkeypatch.setattr(
            UserPermissionService,
            "get_user_permission_slugs",
            lambda self, uid: {
                "master_data.spec_registry.edit",
                "master_data.spec_registry.view",
                "master_data.spec_registry.add",
                "master_data.products.view",
            },
        )
        cat = ProductCategory(id=_uid(), category_code=unique_code("CAT"), category_name="cat")
        uom = UnitOfMeasure(id=_uid(), uom_code=unique_code("UOM"), uom_name="uom")
        db.add_all([cat, uom])
        db.commit()
        db.info["refs"] = {"cat": cat.id, "uom": uom.id}
        try:
            yield TestClient(app), db
        finally:
            app.dependency_overrides.clear()


def _product(db, description: str, **fields) -> Product:
    refs = db.info["refs"]
    code = unique_code("R2")
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=code,
        description=description,
        category_id=refs["cat"],
        base_uom_id=refs["uom"],
        list_price=Decimal("1.00"),
        **fields,
    )
    db.add(row)
    db.flush()
    from app.services.product_spec_derivation import derive_for_code

    derive_for_code(db, code)
    db.commit()
    return row


def _values(db, product) -> dict:
    db.expire_all()
    spec = db.query(ProductSpecifications).filter_by(product_id=product.id).first()
    return {k: v.get("value") for k, v in ((spec.values if spec else None) or {}).items()}


def _registry(db, spec_key: str) -> ProductSpecRegistry:
    db.expire_all()
    return db.query(ProductSpecRegistry).filter_by(spec_key=spec_key).one()


def _shipped_key_row(db, spec_key: str = "is_rimless") -> ProductSpecRegistry:
    """A seeded key with no rules of its own, which reads with the shipped ones."""
    row = ProductSpecRegistry(
        id=_uid(), spec_key=spec_key, label="Rimless", data_type="boolean", source="seed"
    )
    db.add(row)
    db.commit()
    return row


# --------------------------------------------------------------------------- #
# B-1: an empty rule list means no rule, never "use the shipped rules"
# --------------------------------------------------------------------------- #
def test_b1_removing_the_only_shipped_rule_leaves_the_key_reading_nothing(api):
    from app.services.product_spec_derivation import configured_rules, shipped_rules
    from app.services.product_spec_registry import remove_rule

    client, db = api
    shipped = shipped_rules()["is_rimless"]
    assert len(shipped) == 1, "the scenario needs a key that ships exactly one rule"
    _shipped_key_row(db)
    product = _product(db, "RIMLESS WALL HUNG WC")
    assert _values(db, product).get("is_rimless") is True

    remove_rule(db, "is_rimless", shipped[0]["builder"])

    assert configured_rules(db)["is_rimless"] == []
    assert "is_rimless" not in _values(db, product), "the product stops reading RIMLESS"
    keys = client.get(BASE).json()["keys"]
    listed = next(key for key in keys if key["spec_key"] == "is_rimless")
    assert listed["effective_rules"] == [], "the grid does not show the removed rule again"


def test_b1_removing_a_persons_only_own_rule_does_not_bring_the_shipped_list_back(api):
    from app.services.product_spec_derivation import configured_rules
    from app.services.product_spec_registry import remove_rule

    _client, db = api
    own = {"kind": "words", "words": ["ZZTRIMFREE"], "value": True}
    row = _shipped_key_row(db)
    row.derivation_rules = [{"builder": own}]
    db.commit()

    remove_rule(db, "is_rimless", own)

    assert configured_rules(db)["is_rimless"] == []


def test_b1_saving_an_empty_rule_list_means_no_rules(api):
    client, db = api
    _shipped_key_row(db)
    product = _product(db, "RIMLESS WALL HUNG WC")

    response = client.patch(f"{BASE}/is_rimless", json={"derivation_rules": []})

    assert response.status_code == 200, response.text
    assert response.json()["effective_rules"] == []
    assert response.json()["products_updated"] == 1
    assert "is_rimless" not in _values(db, product)


def test_b1_a_key_never_given_rules_still_reads_with_the_shipped_ones(api):
    from app.services.product_spec_derivation import configured_rules, shipped_rules

    _client, db = api
    row = _shipped_key_row(db)

    assert _registry(db, row.spec_key).derivation_rules is None
    assert configured_rules(db)["is_rimless"] == shipped_rules()["is_rimless"]


def test_b1_the_migration_turns_every_stored_empty_list_into_the_shipped_marker():
    module = _module(_VERSIONS / "spec_0003_rules_null_brand_pol.py", "zzt_spec_0003")
    with pg_session() as db:
        key = unique_code("emptyrules").lower()
        own = unique_code("ownrules").lower()
        db.execute(text("ALTER TABLE product_spec_registry ALTER COLUMN derivation_rules DROP NOT NULL"))
        for spec_key, rules in ((key, "[]"), (own, '[{"builder": {"kind": "product", "fact": "class"}}]')):
            db.execute(
                text(
                    "INSERT INTO product_spec_registry (id, spec_key, label, data_type, derivation_rules)"
                    " VALUES (:id, :key, :key, 'enum', CAST(:rules AS jsonb))"
                ),
                {"id": _uid(), "key": spec_key, "rules": rules},
            )
        _run(db, module)
        stored = dict(
            db.execute(
                text("SELECT spec_key, derivation_rules FROM product_spec_registry WHERE spec_key IN (:a, :b)"),
                {"a": key, "b": own},
            ).all()
        )
        assert stored[key] is None
        assert stored[own] == [{"builder": {"kind": "product", "fact": "class"}}]
        nullable = db.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name ="
                " 'product_spec_registry' AND column_name = 'derivation_rules'"
                " AND table_schema = current_schema()"
            )
        ).scalar()
        assert nullable == "YES"


def _run(db, module, direction: str = "upgrade") -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        getattr(module, direction)()


# --------------------------------------------------------------------------- #
# B-2: an empty or dash word is refused, and never matches everything
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "builder",
    [
        {"kind": "words", "words": ["..."], "value": "black"},
        {"kind": "words", "words": ["-"], "value": "black"},
        {"kind": "words", "words": ["GOLD", "--"], "value": "black"},
        {"kind": "words", "words": ["GOLD ... -"], "value": "black"},
        {"kind": "words", "words": ["GOLD"], "skip_after": ["..."], "value": "black"},
        {"kind": "number", "before": ["-"]},
        {"kind": "code", "code_match": "contains", "texts": ["-"], "value": "black"},
        {"kind": "code", "code_match": "contains", "texts": [" . "], "value": "black"},
    ],
)
def test_b2_a_word_with_no_letter_or_number_is_refused(builder):
    from app.services.product_spec_rules import validate_rules

    data_type = "numeric" if builder["kind"] == "number" else "enum"
    with pytest.raises(AppException) as excinfo:
        validate_rules(
            [{"builder": builder}], spec_key="finish", data_type=data_type, allowed_values=["black"]
        )
    assert excinfo.value.status_code == 400
    assert excinfo.value.message == NO_LETTER


@pytest.mark.parametrize("words", [["..."], ["-"], ["GOLD", "..."]])
def test_b2_a_stored_empty_word_reads_nothing_on_any_text(words):
    from app.services.product_spec_rules import read_text

    builder = {"kind": "words", "words": words, "value": "black"}
    texts = {"description": "PLAIN CHROME BASIN MIXER, 2 HOLE", "flyer": "", "class_tail": ""}

    assert read_text(builder, texts, "SRT123") is None


def test_b2_a_bracket_still_says_something():
    """ "The number between ( and MM" is a real rule: only dots and dashes say nothing."""
    from app.services.product_spec_rules import read_text, validate_rules

    rules = validate_rules(
        [{"builder": {"kind": "number", "after": ["("], "before": ["MM"]}}], data_type="numeric"
    )
    texts = {"description": "MARBLE TOP BASIN (800MM)", "flyer": "", "class_tail": ""}
    assert read_text(rules[0]["builder"], texts, "")[0] == 800


def test_b2_a_stored_dash_code_text_reads_nothing():
    from app.services.product_spec_rules import read_text

    builder = {"kind": "code", "code_match": "contains", "texts": ["-"], "value": "black"}
    assert read_text(builder, {"description": "", "flyer": "", "class_tail": ""}, "SRT-123") is None


# --------------------------------------------------------------------------- #
# B-3: Try it is bounded in text and rule size, and its matching is linear
# --------------------------------------------------------------------------- #
def test_b3_twenty_gapped_phrases_over_a_long_text_read_fast():
    """The reviewer's measure: 1.8 s at 4,900 characters, quadratic after that."""
    from app.services.product_spec_rules import read_text

    builder = {"kind": "words", "words": [f"A ... B{i}" for i in range(20)], "value": "black"}
    texts = {"description": ("A " * 4900)[:4900], "flyer": "", "class_tail": ""}
    started = time.perf_counter()
    assert read_text(builder, texts, "") is None
    assert time.perf_counter() - started < 0.5


def test_b3_nineteen_gapped_skip_phrases_over_a_long_text_read_fast():
    from app.services.product_spec_rules import read_text

    builder = {
        "kind": "words",
        "words": ["C"],
        "skip_after": [f"A ... B{i}" for i in range(19)],
        "value": "black",
    }

    def best_of_three(size: int) -> float:
        texts = {"description": ("A B18 C " * 1300)[:size], "flyer": "", "class_tail": ""}
        runs = []
        for _ in range(3):
            started = time.perf_counter()
            read_text(builder, texts, "")
            runs.append(time.perf_counter() - started)
        return min(runs)

    # Smoke bound on a small input: 0.027 s locally, so 0.5 s only fails on a real regression.
    smoke = best_of_three(490)
    assert smoke < 0.5, smoke

    short, long_ = best_of_three(4900), best_of_three(9800)
    # The algorithmic property, independent of runner speed. Linear: twice the text, about
    # twice the time (measured 1.8x to 2.1x). Quadratic would be 4x, cubic (the old code,
    # 0.3 s at 4,400) 8x.
    assert long_ / short < 3.0, (short, long_)
    # Absolute ceiling with CI headroom: 9,800 characters measured 0.68 s to 0.84 s locally
    # (cloud lane, 27 Sep 2026) and 2.30 s on the shared CI runner, so 5.0 s is over five
    # times the local number. The ratio above is what proves linearity; this only catches a
    # constant-factor blow-up.
    assert long_ < 5.0, long_


def test_b3_a_rule_takes_one_gapped_phrase():
    from app.services.product_spec_rules import validate_rules

    with pytest.raises(AppException) as excinfo:
        validate_rules(
            [{"builder": {"kind": "words", "words": ["A ... B", "C ... D"], "value": "black"}}],
            data_type="enum",
            allowed_values=["black"],
        )
    assert excinfo.value.message == "Rule 1: use ... in one phrase only."


def test_b3_try_is_a_plain_function_so_its_matching_runs_off_the_event_loop():
    from app.api.v1.master_data.spec_registry import try_spec_key

    assert not inspect.iscoroutinefunction(try_spec_key)


def test_b3_try_refuses_a_long_text_and_too_many_rules(api):
    from app.services.product_spec_rules import MAX_RULES_PER_TRY, MAX_TRY_TEXT

    client, db = api
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a"], source="user",
    )
    db.add(row)
    db.commit()
    rule = {"builder": {"kind": "words", "words": ["A"], "value": "a"}}

    long_text = client.post(
        f"{BASE}/{row.spec_key}/try", json={"text": "A " * MAX_TRY_TEXT, "rules": [rule]}
    )
    many_rules = client.post(
        f"{BASE}/{row.spec_key}/try",
        json={"text": "A", "rules": [rule] * (MAX_RULES_PER_TRY + 1)},
    )
    fine = client.post(f"{BASE}/{row.spec_key}/try", json={"text": "A", "rules": [rule]})

    assert long_text.status_code == 422, long_text.text
    assert many_rules.status_code == 422, many_rules.text
    assert fine.status_code == 200, fine.text


def test_b3_read_specs_from_a_text_refuses_a_long_paste():
    from pydantic import ValidationError

    from app.api.v1.master_data.product_specifications import SpecExtractRequest
    from app.services.product_spec_extract import MAX_TEXT_LENGTH

    with pytest.raises(ValidationError):
        SpecExtractRequest(text="A" * (MAX_TEXT_LENGTH + 1))
    assert SpecExtractRequest(text="A" * MAX_TEXT_LENGTH).text


# --------------------------------------------------------------------------- #
# S-1: the evidence script and the migrations report what goes with the brand row
# --------------------------------------------------------------------------- #
def test_s1_the_evidence_script_reports_the_brand_rows_tuning_and_gates():
    from scripts import product_spec_lane_evidence as script

    sql = "\n".join(query for _title, query in script._SECTIONS)
    for column in ("rank_weight", "value_weights", "is_active"):
        assert column in sql
    assert "only_when" in sql and "applies_when" in sql and "unless" in sql
    assert "spec_visibility_policies" in sql
    migration = _module(_VERSIONS / "spec_0001_drop_brand_spec.py", "zzt_spec_0001_s1")
    assert set(script.HAND_SET_SOURCES) == set(migration._AUTHORED)


def test_s1_the_s0_migration_logs_the_brand_rows_tuning(caplog):
    migration = _module(_VERSIONS / "spec_0001_drop_brand_spec.py", "zzt_spec_0001_log")
    with pg_session() as db:
        db.execute(text("DELETE FROM product_spec_registry WHERE spec_key = 'brand'"))
        db.execute(
            text(
                "INSERT INTO product_spec_registry (id, spec_key, label, data_type, rank_weight,"
                " value_weights, is_active) VALUES (:id, 'brand', 'Brand', 'enum', 7.5,"
                " CAST('{\"SORENTO\": 8.0}' AS jsonb), true)"
            ),
            {"id": _uid()},
        )
        with caplog.at_level(logging.WARNING):
            migration._delete_registry_row(db.connection())
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "rank_weight" in logged and "7.5" in logged
    assert "is_active" in logged
    assert "SORENTO" in logged


def test_s1_the_s1_migration_warns_about_a_gate_on_brand(caplog):
    migration = _module(_VERSIONS / "spec_0002_rules_as_builders.py", "zzt_spec_0002_s1")
    rule = {"match": "contains", "pattern": "GOLD", "value": "gold", "applies_when": {"brand": ["SORENTO"]}}
    with caplog.at_level(logging.WARNING):
        migration.convert_rules("finish", [rule])
    assert any(
        "brand" in record.getMessage() and "never" in record.getMessage() for record in caplog.records
    )


# --------------------------------------------------------------------------- #
# S-2: OTHERS never binds, whatever its flag says
# --------------------------------------------------------------------------- #
def test_s2_a_searchable_others_brand_still_never_binds_the_word_others():
    from app.models.base import company_scope
    from app.services.product_spec_search import resolve_terms_to_specs
    from app.services.product_spec_understanding import _brand_vocabulary

    with blank_session() as db:
        with company_scope(db, None):
            db.add_all(
                [
                    Brand(id=_uid(), brand_code="ZZT-SRT", brand_name="SORENTO"),
                    # A new company's OTHERS from ingest: searchable by default.
                    Brand(id=_uid(), brand_code="ZZT-OTH", brand_name="OTHERS", is_searchable=True),
                ]
            )
            db.flush()
            resolved = resolve_terms_to_specs(db, ["any", "others", "colour"])
            vocabulary = _brand_vocabulary(db)

    assert not any(entry["key"] == "brand" for entry in resolved)
    assert "OTHERS" not in vocabulary.allowed_values


# --------------------------------------------------------------------------- #
# S-3: a stored `brand` in a visibility policy is migrated away
# --------------------------------------------------------------------------- #
def test_s3_the_migration_takes_brand_out_of_every_visibility_policy(caplog):
    module = _module(_VERSIONS / "spec_0003_rules_null_brand_pol.py", "zzt_spec_0003_s3")
    with pg_session() as db:
        policies = {}
        for column, keys in (("spec_keys", "ARRAY['brand','finish']"), ("excluded_spec_keys", "ARRAY['brand']")):
            segment = unique_code("SEG")
            db.execute(
                text(
                    "INSERT INTO market_segments (id, code, name, is_active, sort_order)"
                    " VALUES (:id, :code, :code, true, 0)"
                ),
                {"id": _uid(), "code": segment},
            )
            policies[column] = (_uid(), segment)
            db.execute(
                text(
                    f"INSERT INTO spec_visibility_policies (id, segment_code, {column})"
                    f" VALUES (:id, :seg, {keys})"
                ),
                {"id": policies[column][0], "seg": segment},
            )
        with caplog.at_level(logging.WARNING):
            _run(db, module)
        shown = db.execute(
            text("SELECT spec_keys FROM spec_visibility_policies WHERE id = :id"),
            {"id": policies["spec_keys"][0]},
        ).scalar()
        hidden = db.execute(
            text("SELECT excluded_spec_keys FROM spec_visibility_policies WHERE id = :id"),
            {"id": policies["excluded_spec_keys"][0]},
        ).scalar()
    assert shown == ["finish"]
    assert hidden == []
    logged = " ".join(record.getMessage() for record in caplog.records)
    for _policy_id, segment in policies.values():
        assert segment in logged


# --------------------------------------------------------------------------- #
# S-4: "brand" is a word search knows
# --------------------------------------------------------------------------- #
def test_s4_the_word_brand_is_in_the_search_vocabulary():
    from app.services.product_spec_search import _search_vocabulary

    with blank_session() as db:
        words = _search_vocabulary(db, registry_rows=[], brands=[])
    assert {"brand", "brands"} <= set(words)


# --------------------------------------------------------------------------- #
# S-7: a failure after the commit never turns a landed save into a 500
# --------------------------------------------------------------------------- #
def test_s7_a_reread_that_fails_after_the_save_committed_still_answers_the_save(api, monkeypatch):
    from app.services import product_spec_rederive

    client, db = api
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a"], source="user",
        derivation_rules=[{"builder": {"kind": "words", "words": ["OLD"], "value": "a"}}],
    )
    db.add(row)
    db.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(product_spec_rederive, "reread_after_save", _boom)
    response = client.patch(
        f"{BASE}/{row.spec_key}",
        json={"derivation_rules": [{"builder": {"kind": "words", "words": ["NEW"], "value": "a"}}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["products_updated"] == 0
    assert _registry(db, row.spec_key).derivation_rules[0]["builder"]["words"] == ["NEW"]


def test_s7_a_remove_whose_reread_fails_is_still_a_committed_remove(api, monkeypatch):
    from app.services import product_spec_rederive
    from app.services.product_spec_registry import remove_rule

    _client, db = api
    rule = {"kind": "words", "words": ["OLD"], "value": "a"}
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a"], source="user", derivation_rules=[{"builder": rule}],
    )
    db.add(row)
    db.commit()
    monkeypatch.setattr(
        product_spec_rederive, "reread_after_save", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )

    result = remove_rule(db, row.spec_key, rule)

    assert result["products_updated"] == 0
    assert _registry(db, row.spec_key).derivation_rules == []


# --------------------------------------------------------------------------- #
# S-8: one catalogue read at a time across every process, and a remove waits its turn
# --------------------------------------------------------------------------- #
def test_s8_a_save_is_refused_while_another_process_holds_the_catalogue_read(api):
    from app.database import engine
    from app.services.product_spec_preview import catalogue_read_lock_key

    client, db = api
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a"], source="user",
    )
    db.add(row)
    db.commit()
    key = catalogue_read_lock_key(db)
    with engine.connect() as other_process:
        assert other_process.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
        try:
            response = client.patch(
                f"{BASE}/{row.spec_key}",
                json={"derivation_rules": [{"builder": {"kind": "words", "words": ["NEW"], "value": "a"}}]},
            )
        finally:
            other_process.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})

    assert response.status_code == 409, response.text
    assert response.json()["message"] == BUSY


def test_s8_a_deferred_remove_waits_for_a_running_read_instead_of_being_voided(api, monkeypatch):
    from app.services import product_spec_preview, product_spec_registry

    _client, db = api
    rule = {"kind": "words", "words": ["OLD"], "value": "a"}
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a"], source="user", derivation_rules=[{"builder": rule}],
    )
    db.add(row)
    db.commit()
    monkeypatch.setattr(product_spec_registry, "REMOVE_WAIT_SECONDS", 10)
    with product_spec_preview._RUNNING_LOCK:
        product_spec_preview._RUNNING_JOB_ID = "zzt-other-read"

    def _finish():
        with product_spec_preview._RUNNING_LOCK:
            product_spec_preview._RUNNING_JOB_ID = None

    timer = threading.Timer(0.5, _finish)
    timer.start()
    try:
        product_spec_registry.remove_rule(db, row.spec_key, rule)
    finally:
        timer.cancel()
        _finish()

    assert _registry(db, row.spec_key).derivation_rules == []


# --------------------------------------------------------------------------- #
# S-9: the fingerprint moves only when the work it stands for is done; one catch-up
# --------------------------------------------------------------------------- #
@contextmanager
def _session_factory_over(db):
    @contextmanager
    def factory():
        yield db

    yield factory


def test_s9_a_queued_reread_stores_the_fingerprint_only_when_it_finishes(api, monkeypatch):
    from app.services import product_spec_change_listener as listener
    from app.services import product_spec_rederive

    client, db = api
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a"], source="user",
    )
    db.add(row)
    db.commit()
    for _ in range(3):
        _product(db, "ZZTNEWWORD BASIN")
    product_spec_rederive._store_fingerprint(db, product_spec_rederive.rules_fingerprint(db))
    before = product_spec_rederive._stored_fingerprint(db)
    monkeypatch.setattr(listener, "INLINE_REDERIVE_LIMIT", 1)
    queued: list = []
    monkeypatch.setattr(
        product_spec_rederive, "enqueue_job", lambda func, *a, **k: queued.append((func, a, k)) or "job"
    )

    response = client.patch(
        f"{BASE}/{row.spec_key}",
        json={"derivation_rules": [{"builder": {"kind": "words", "words": ["ZZTNEWWORD"], "value": "a"}}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["products_updated"] == 3
    assert product_spec_rederive._stored_fingerprint(db) == before, "not stored while the codes wait"
    assert len(queued) == 1
    func, args, kwargs = queued[0]
    import app.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: _Borrowed(db))
    monkeypatch.setattr("app.tasks.product_spec_tasks.SessionLocal", lambda: _Borrowed(db))
    func(*args, **{k: v for k, v in kwargs.items() if k not in {"queue_name", "job_id"}})
    assert product_spec_rederive._stored_fingerprint(db) == product_spec_rederive.rules_fingerprint(db)


class _Borrowed:
    """A context manager lending the test's session without closing it."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        return False


def test_s9_two_workers_starting_on_the_same_rules_queue_one_catch_up(monkeypatch):
    import app.database as database
    from app.services import product_spec_rederive

    with blank_session() as db:
        monkeypatch.setattr(database, "SessionLocal", lambda: _Borrowed(db))
        queued: list = []
        monkeypatch.setattr(
            product_spec_rederive, "enqueue_job", lambda func, *a, **k: queued.append(k) or "job"
        )
        seen: set[str] = set()
        monkeypatch.setattr(product_spec_rederive, "_already_queued", lambda job_id: job_id in seen)

        assert product_spec_rederive.catch_up_on_worker_start() is True
        seen.add(queued[0]["job_id"])
        assert product_spec_rederive.catch_up_on_worker_start() is False

    assert len(queued) == 1
    assert queued[0]["job_id"].startswith("spec-catch-up-")


# --------------------------------------------------------------------------- #
# S-10: removing a choice re-reads the products that held it
# --------------------------------------------------------------------------- #
def test_s10_removing_a_shipped_choice_rereads_the_products_holding_it(api):
    from app.services.product_spec_registry import remove_value

    _client, db = api
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a", "b"], source="seed",
        derivation_rules=[{"builder": {"kind": "words", "words": ["ZZTAWORD"], "value": "a"}}],
    )
    db.add(row)
    db.commit()
    product = _product(db, "ZZTAWORD BASIN")
    assert _values(db, product)[row.spec_key] == "a"

    result = remove_value(db, row.spec_key, "a")

    assert result.get("products_updated") == 1
    assert row.spec_key not in _values(db, product)


def test_s10_saving_suppressed_values_rereads_the_products_holding_them(api):
    client, db = api
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a", "b"], synonyms={"a": ["a"], "b": ["b"]}, source="seed",
        derivation_rules=[{"builder": {"kind": "words", "words": ["ZZTAWORD"], "value": "a"}}],
    )
    db.add(row)
    db.commit()
    product = _product(db, "ZZTAWORD BASIN")

    response = client.patch(f"{BASE}/{row.spec_key}", json={"suppressed_values": ["a"]})

    assert response.status_code == 200, response.text
    assert response.json()["products_updated"] == 1
    assert row.spec_key not in _values(db, product)


# --------------------------------------------------------------------------- #
# S-14: the unpinned repairs
# --------------------------------------------------------------------------- #
def test_s14_brand_is_refused_as_a_rules_own_specification():
    from app.services.product_spec_rules import BRAND_IS_NOT_A_SPEC, validate_rules

    with pytest.raises(AppException) as excinfo:
        validate_rules(
            [{"builder": {"kind": "words", "words": ["SORENTO"], "value": "SORENTO"}}],
            spec_key="brand",
            data_type="enum",
        )
    assert excinfo.value.message == BRAND_IS_NOT_A_SPEC


def test_s14_brand_is_refused_as_a_new_specification(api):
    from app.services.product_spec_rules import BRAND_IS_NOT_A_SPEC

    client, db = api
    response = client.post(BASE, json={"spec_key": "brand", "label": "Brand", "data_type": "enum"})

    assert response.status_code == 400, response.text
    assert response.json()["message"] == BRAND_IS_NOT_A_SPEC
    assert db.query(ProductSpecRegistry).filter_by(spec_key="brand").first() is None


@pytest.mark.parametrize("gate_key", ["brand", "Brand", " BRAND "])
def test_s14_n3_a_scope_on_brand_is_refused_in_any_case(api, gate_key):
    from app.services.product_spec_rules import BRAND_IS_NOT_A_SPEC

    client, db = api
    row = ProductSpecRegistry(
        id=_uid(), spec_key=f"zzt_{_uid()[:8]}", label="ZZT", data_type="enum",
        allowed_values=["a"], source="user",
    )
    db.add(row)
    db.commit()

    response = client.patch(f"{BASE}/{row.spec_key}", json={"applies_when": {gate_key: ["SORENTO"]}})

    assert response.status_code == 400, response.text
    assert response.json()["message"] == BRAND_IS_NOT_A_SPEC


def test_s14_a_spec_write_naming_brand_is_refused():
    from app.services.product_spec_rules import BRAND_IS_NOT_A_SPEC
    from app.services.product_spec_write import _prepare

    with pytest.raises(AppException) as excinfo:
        _prepare({"spec_key": "Brand", "value": "SORENTO"}, None)
    assert excinfo.value.message == BRAND_IS_NOT_A_SPEC


def test_s14_a_product_brand_change_is_a_reread_trigger(monkeypatch):
    import app.services.product_spec_change_listener as listener

    with blank_session() as db:
        listener.register_product_spec_listeners()
        pending: list[set[str]] = []
        monkeypatch.setattr(listener, "_rederive_inline", lambda codes: pending.append(set(codes)))
        cat = ProductCategory(id=_uid(), category_code=unique_code("CAT"), category_name="cat")
        uom = UnitOfMeasure(id=_uid(), uom_code=unique_code("UOM"), uom_name="uom")
        first = Brand(id=_uid(), brand_code=unique_code("B1"), brand_name="ZZT ONE")
        second = Brand(id=_uid(), brand_code=unique_code("B2"), brand_name="ZZT TWO")
        db.add_all([cat, uom, first, second])
        db.flush()
        code = unique_code("BRANDMOVE")
        product = Product(
            id=_uid(), product_code=code, product_name=code, description="BASIN",
            category_id=cat.id, base_uom_id=uom.id, list_price=Decimal("1.00"), brand_id=first.id,
        )
        db.add(product)
        db.commit()
        pending.clear()

        product.brand_id = second.id
        db.commit()

    assert any(code in batch for batch in pending), "a re-brand must re-render the sentence"


def test_s14_brand_get_and_put_carry_is_searchable(api, monkeypatch):
    client, db = api
    brand = Brand(id=_uid(), brand_code=unique_code("BR"), brand_name="ZZT SEARCHABLE")
    db.add(brand)
    db.commit()
    import app.api.v1.master_data.brands as brands_route

    monkeypatch.setattr(brands_route, "_assert_company_readable", lambda *a, **k: None, raising=False)

    got = client.get(f"/api/v1/master-data/brands/{brand.id}")
    assert got.status_code == 200, got.text
    assert got.json()["is_searchable"] is True

    put = client.put(f"/api/v1/master-data/brands/{brand.id}", json={"is_searchable": False})
    assert put.status_code == 200, put.text
    assert put.json()["is_searchable"] is False
    db.expire_all()
    assert db.query(Brand).filter_by(id=brand.id).one().is_searchable is False


def test_s14_the_migrations_frozen_converter_writes_exactly_the_shipped_rules():
    from app.services.product_spec_derivation import shipped_rules

    migration = _module(_VERSIONS / "spec_0002_rules_as_builders.py", "zzt_spec_0002_s14")
    legacy = json.loads((Path(__file__).parent / "fixtures" / "legacy_shipped_rules.json").read_text())
    shipped = shipped_rules()
    for spec_key, rules in legacy.items():
        if spec_key == "brand":
            continue
        converted = migration._corrected(spec_key, migration.convert_rules(spec_key, rules))
        assert [r["builder"] for r in converted] == [
            r["builder"] for r in shipped.get(spec_key) or []
        ], spec_key
    # has_massage (#1720) is a shipped rule seeded by cfs_0001, after spec_0002, so the
    # legacy fixture spec_0002 converted never held it.
    assert "has_massage" not in legacy
    assert set(shipped) - {"has_massage"} <= set(legacy)


# --------------------------------------------------------------------------- #
# Nits
# --------------------------------------------------------------------------- #
def test_n1_only_when_on_brand_is_refused_with_the_brand_sentence():
    from app.services.product_spec_rules import BRAND_IS_NOT_A_SPEC, validate_rules

    with pytest.raises(AppException) as excinfo:
        validate_rules(
            [
                {
                    "builder": {
                        "kind": "words", "words": ["GOLD"], "value": "gold",
                        "only_when": {"spec": "Brand", "is": True, "values": ["SORENTO"]},
                    }
                }
            ],
            spec_key="finish",
            data_type="enum",
            allowed_values=["gold"],
        )
    assert excinfo.value.message == BRAND_IS_NOT_A_SPEC


def test_n2_a_skipped_stored_rule_warns_once_not_once_per_product(caplog):
    from app.services.product_spec_rules import read_text

    builder = {"kind": "words", "words": [f"ZZT{_uid()[:6]} ... A ... B"], "value": "x"}
    texts = {"description": "A B", "flyer": "", "class_tail": ""}
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            read_text(builder, texts, "")
    assert len([r for r in caplog.records if "..." in r.getMessage()]) == 1


def test_n4_a_preview_refused_by_a_running_save_says_products_are_being_updated():
    from app.services import product_spec_preview

    with blank_session() as db:
        token = product_spec_preview.begin_catalogue_read(db)
        try:
            with pytest.raises(AppException) as excinfo:
                product_spec_preview.start("finish", [], db)
        finally:
            product_spec_preview.end_catalogue_read(token)
    assert excinfo.value.status_code == 409
    assert excinfo.value.message == BUSY


def test_n5_from_field_choices_is_gone():
    from app.services import product_spec_registry

    assert not hasattr(product_spec_registry, "from_field_choices")


def test_n6_the_brand_vocabulary_skips_inactive_brands_and_keeps_the_most_used():
    from app.models.base import company_scope
    from app.services import product_spec_understanding as understanding

    with blank_session() as db:
        with company_scope(db, None):
            cat = ProductCategory(id=_uid(), category_code=unique_code("CAT"), category_name="cat")
            uom = UnitOfMeasure(id=_uid(), uom_code=unique_code("UOM"), uom_name="uom")
            retired = Brand(id=_uid(), brand_code="ZZT-OLD", brand_name="AAA RETIRED", is_active=False)
            busy = Brand(id=_uid(), brand_code="ZZT-BUSY", brand_name="ZZZ BUSY")
            quiet = [
                Brand(id=_uid(), brand_code=f"ZZT-Q{i}", brand_name=f"BBB QUIET {i:02d}")
                for i in range(understanding._OPEN_VOCABULARY_LIMIT)
            ]
            db.add_all([cat, uom, retired, busy, *quiet])
            db.flush()
            for _ in range(3):
                code = unique_code("BUSY")
                db.add(
                    Product(
                        id=_uid(), product_code=code, product_name=code, description="BASIN",
                        category_id=cat.id, base_uom_id=uom.id, list_price=Decimal("1.00"),
                        brand_id=busy.id,
                    )
                )
            db.flush()
            described, _index, _open = understanding._vocabulary(db)

    brand = next(entry for entry in described if entry["spec_key"] == "brand")
    assert "AAA RETIRED" not in brand["allowed_values"]
    assert "ZZZ BUSY" in brand["allowed_values"], "the cap keeps the brands products carry"
