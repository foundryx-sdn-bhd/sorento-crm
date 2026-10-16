"""S1 - the rule engine and its screen (#1286, D5-D8, D10, D14-D16).

Contract: documentation/plans/products/CONTRACT-product-specs-rule-engine.md.
UAC: documentation/plans/products/product-specs-non-technical-acceptance-criteria.md
(AC-S1.1, AC-S1.2, AC-S1.3, AC-S1.5, AC-S1.16).

`compile_builder` (AC-S1.1, AC-S1.2) is tested at the unit level: build the five kinds
by hand, per contract section 1.1/2, and confirm the compiled fields (section 2.1)
produce the matching behaviour section 2 describes, against real catalogue phrases. This
does not require a running derivation - it pins the compiler's contract directly, which
is what both the Python and the TypeScript compiler must honour identically (the shared
fixture below).

Migration tests (AC-S1.3) run raw SQL against `product_spec_registry` inside a
rolled-back `pg_session`, mirroring `tests/test_migration_450_spec_rules_backfill.py`
(LESSONS: migration tests via upgrade() in pg_session, never sqlite).
"""
from __future__ import annotations

import importlib.util
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from tests._pg_fixture import pg_session, unique_code

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FIXTURE_PATH = (
    _REPO_ROOT
    / "sorento_crm_frontend"
    / "app"
    / "(protected)"
    / "master-data-management"
    / "product-specifications"
    / "lib"
    / "__fixtures__"
    / "rule-builders.json"
)
_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "spec_0002_rules_as_builders.py"
)


def _upper(text_: str) -> str:
    return (text_ or "").upper()


# --------------------------------------------------------------------------- #
# AC-S1.1 - compile_builder, the five kinds, and the shared fixture
# --------------------------------------------------------------------------- #
def test_ac_s1_1_words_kind_compiles():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder(
        {"kind": "words", "look_in": "description", "words": ["SOFT CLOSE", "SOFT CLOSING"], "value": True}
    )
    assert compiled["kind"] == "words"
    assert compiled["scope"] == "description"
    assert compiled["capture"] is None
    assert re.search(compiled["pattern"], "THIS HAS A SOFT CLOSE HINGE")


def test_ac_s1_1_number_kind_compiles():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "look_in": "any", "before": ["OZ"]})
    assert compiled["kind"] == "number"
    assert compiled["scope"] == "any"
    assert compiled["capture"] == 1
    match = re.search(compiled["pattern"], "THIS HOLDS 8OZ")
    assert match and match.group(1) == "8"


def test_ac_s1_1_size_kind_compiles():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "size", "look_in": "description", "pick": 3})
    assert compiled["kind"] == "size"
    assert compiled["pick"] == 3
    assert compiled["pattern"] is not None


def test_ac_s1_1_code_kind_compiles():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder(
        {"kind": "code", "code_match": "ends_with", "texts": ["-GM"], "value": "gunmetal"}
    )
    assert compiled["kind"] == "code"
    assert compiled["scope"] == "code"
    assert compiled["pattern"] is None
    assert compiled["code_match"] == "ends_with"
    assert compiled["texts"] == ["-GM"]


def test_ac_s1_1_product_kind_compiles():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "product", "fact": "length"})
    assert compiled["kind"] == "product"
    assert compiled["scope"] == "product"
    assert compiled["pattern"] is None
    assert compiled["fact"] == "length"


def test_ac_s1_1_every_shipped_rule_compiles_from_its_builder():
    """The rewritten `_rules_from_shipped_tables()` returns builder-only rules,
    205 over 50 keys, no brand - and every one of them compiles. (202 in the appendix
    as first written; fix round 1 added Length rule 4, "the number between LENGTH and
    MM".)"""
    from app.services.product_spec_registry import _rules_from_shipped_tables
    from app.services.product_spec_rules import compile_builder

    shipped = _rules_from_shipped_tables()
    assert "brand" not in shipped
    total = sum(len(rules) for rules in shipped.values())
    assert len(shipped) == 50
    assert total == 205

    for spec_key, rules in shipped.items():
        for rule in rules:
            assert set(rule.keys()) <= {"builder", "_seed"}, (
                f"{spec_key} carries a rule with a non-builder field: {rule}"
            )
            compiled = compile_builder(rule["builder"])
            if compiled.get("pattern"):
                re.compile(compiled["pattern"])


def test_ac_s1_1_shared_fixture_pins_python_and_typescript_to_the_same_compile():
    """The shared fixture both compilers assert against. Generated once the builder
    seed exists (it cannot be hand-typed without drifting from the real compiler)."""
    assert _FIXTURE_PATH.exists(), f"missing shared fixture: {_FIXTURE_PATH}"
    entries = json.loads(_FIXTURE_PATH.read_text())
    assert isinstance(entries, list) and len(entries) > 0

    from app.services.product_spec_rules import compile_builder

    kinds_seen = set()
    for entry in entries:
        assert {"spec_key", "builder", "compiled"} <= set(entry.keys())
        compiled = compile_builder(entry["builder"])
        assert compiled == entry["compiled"], entry["spec_key"]
        kinds_seen.add(entry["builder"].get("kind"))
        if compiled.get("pattern"):
            re.compile(compiled["pattern"])
    assert kinds_seen == {"words", "number", "size", "code", "product"}


# --------------------------------------------------------------------------- #
# AC-S1.2 - matching, the same for every rule, one test per statement
# --------------------------------------------------------------------------- #
def test_ac_s1_2_case_never_matters():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "words", "words": ["SOFT CLOSE"], "value": True})
    assert re.search(compiled["pattern"], _upper("this has a soft close hinge"))


def test_ac_s1_2_words_match_whole_words_only():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "words", "words": ["LED"], "value": True})
    assert not re.search(compiled["pattern"], "THIS BASIN IS SEALED GLASS")
    assert re.search(compiled["pattern"], "THIS HAS LED LIGHTS")


def test_ac_s1_2_hyphen_or_nothing_between_words_of_a_phrase():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "words", "words": ["PULL OUT SHOWER"], "value": True})
    assert re.search(compiled["pattern"], "SRTXX PULL-OUT SHOWER SET")
    assert re.search(compiled["pattern"], "SRTXX PULLOUT SHOWER SET")
    assert re.search(compiled["pattern"], "SRTXX PULL OUT SHOWER SET")


def test_ac_s1_2_hyphen_between_words_soft_close():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "words", "words": ["SOFT CLOSE"], "value": True})
    assert re.search(compiled["pattern"], "SRTXX SOFT-CLOSE HINGE")


def test_ac_s1_2_ellipsis_matches_anything_within_one_sentence():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "words", "words": ["PP ... SEAT"], "value": "pp"})
    assert re.search(compiled["pattern"], "PP SOFT CLOSE SEAT COVER")
    assert not re.search(compiled["pattern"], "PP SOFT CLOSE. THE SEAT COVER IS WHITE")


def test_ac_s1_2_a_number_touched_by_a_letter_or_digit_is_never_read():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "before": ["L"]})
    assert re.search(compiled["pattern"], "SRTKS1008L") is None, "1008 is part of a code, not a length"


def test_ac_s1_2_a_number_touched_across_a_hyphen_is_never_read():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "before": ["L"]})
    assert re.search(compiled["pattern"], "CB F-809L") is None, "809 touches a letter across the hyphen"


def test_ac_s1_2_number_before_reads_hyphen_space_or_nothing():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "before": ["WAY", "WAYS"]})
    for phrase, expected in (("3-WAY", "3"), ("3 WAYS", "3"), ("12 WAY", "12")):
        match = re.search(compiled["pattern"], phrase)
        assert match and match.group(1) == expected, phrase


def test_ac_s1_2_number_before_reads_nothing_between_number_and_word():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "before": ["OZ"]})
    match = re.search(compiled["pattern"], "8OZ CUP")
    assert match and match.group(1) == "8"


def _skip_hit(compiled: dict, haystack: str):
    """Mirrors contract section 2's skip_after / ignore_below loop: try each hit in
    order, skip one the skip pattern rules out or that is below the floor, return the
    first survivor (scaled), or None."""
    haystack = _upper(haystack)
    for match in re.finditer(compiled["pattern"], haystack):
        group = compiled.get("capture")
        raw = match.group(group) if group else match.group(0)
        if compiled.get("skip") and re.search(compiled["skip"], haystack[: match.start()]):
            continue
        if group is None:
            # A Words rule sets its value; there is no number to scale.
            return True
        if raw is not None and compiled.get("min") is not None:
            try:
                if float(raw) < float(compiled["min"]):
                    continue
            except (TypeError, ValueError):
                pass
        if raw is None:
            return True  # a words/present-style hit with no captured number
        value = float(raw)
        if compiled.get("scale"):
            value *= float(compiled["scale"])
        return value
    return None


def test_ac_s1_2_skip_after_a_screw_that_comes_with_it_reads_yes():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "words", "words": ["SCREW"], "skip_after": ["W/O", "WITHOUT"], "value": True})
    assert _skip_hit(compiled, "C/W SCREW") is True


def test_ac_s1_2_skip_after_a_screw_that_is_without_it_reads_nothing():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "words", "words": ["SCREW"], "skip_after": ["W/O", "WITHOUT"], "value": True})
    assert _skip_hit(compiled, "W/O SCREW") is None
    assert _skip_hit(compiled, "WITHOUT SCREW") is None


def test_ac_s1_2_ignore_below_skips_a_small_number_and_reads_the_next():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "before": ["MM"], "ignore_below": 10})
    value = _skip_hit(compiled, "PANEL 8MM THICK, BASE 800MM WIDE")
    assert value == 800.0


def test_ac_s1_2_number_between_reads_the_trap_length():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "after": ["S TRAP", "P TRAP"], "before": ["MM"]})
    for phrase in ("S-TRAP:250MM", "( S- TRAP 250MM )"):
        match = re.search(compiled["pattern"], _upper(phrase))
        assert match, phrase
        assert match.group(1) == "250", phrase


def test_ac_s1_2_written_in_metres_scales_to_millimetres():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "number", "before": ["M"], "written_in": "metres"})
    assert compiled["scale"] == 1000
    match = re.search(compiled["pattern"], "C/W 1.2M HOSE")
    assert match and float(match.group(1)) * compiled["scale"] == 1200.0


def _size_pick(pattern: str, pick, text_: str):
    match = re.search(pattern, _upper(text_))
    if not match:
        return None
    labels = [match.group(i) for i in (1, 3, 5, 7)]
    numbers = [match.group(i) for i in (2, 4, 6, 8)]
    if isinstance(pick, str):
        for label, number in zip(labels, numbers):
            if label == pick:
                return float(number)
        return None
    present = [n for n in numbers if n is not None]
    index = pick - 1
    if index >= len(present):
        return None
    return float(present[index])


def test_ac_s1_2_size_pick_reads_the_nth_number_of_a_plain_triple():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "size", "pick": 3})
    assert _size_pick(compiled["pattern"], 3, "1500X750X630MM") == 630.0


def test_ac_s1_2_size_pick_reads_the_labelled_number():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "size", "pick": "W"})
    assert _size_pick(compiled["pattern"], "W", "L750 X W165 X H247MM") == 165.0


def test_ac_s1_2_code_ends_with_matches_the_suffix():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder({"kind": "code", "code_match": "ends_with", "texts": ["-GM"], "value": "gunmetal"})
    code = "SRTWT9605-GM"
    assert code.upper().endswith(tuple(compiled["texts"]))


def test_ac_s1_2_at_end_only_matches_the_trailing_words():
    from app.services.product_spec_rules import compile_builder

    compiled = compile_builder(
        {"kind": "words", "look_in": "name", "words": ["MIRROR CABINET", "VANITY CABINET"], "at_end": True, "value": "bathroom_furniture"}
    )
    assert re.search(compiled["pattern"], "SRTXX STAINLESS MIRROR CABINET")
    assert not re.search(compiled["pattern"], "SRTXX MIRROR CABINET HANDLE")


def test_ac_s1_2_only_when_gates_a_rule_on_another_specs_value():
    """The round/square gate (D5): a size rule for `diameter` only fires when `shape`
    (read earlier in the same derivation) is round or square."""
    from app.services.product_spec_derivation import apply_rules

    rules_by_key = {
        "shape": [{"builder": {"kind": "words", "words": ["ROUND"], "value": "round"}}],
        "diameter": [
            {
                "builder": {
                    "kind": "size",
                    "pick": 1,
                    "only_when": {"spec": "shape", "is": True, "values": ["round", "square"]},
                }
            }
        ],
    }
    round_texts = {"description": "CONCRETE ROUND BASIN (407X120X10MM)", "flyer": "", "class_tail": "", "size_text": ""}
    rect_texts = {"description": "CONCRETE RECTANGULAR BASIN (407X120X10MM)", "flyer": "", "class_tail": "", "size_text": ""}

    round_found = apply_rules(rules_by_key, round_texts, "ZZT-ROUND")
    rect_found = apply_rules(rules_by_key, rect_texts, "ZZT-RECT")

    assert round_found.get("diameter", {}).get("value") == 407, "shape=round must let the diameter rule fire"
    assert rect_found.get("diameter", {}).get("value") is None, "shape != round must gate the diameter rule off"


# --------------------------------------------------------------------------- #
# AC-S1.3 - the conversion migration
# --------------------------------------------------------------------------- #
def _migration_module():
    spec = importlib.util.spec_from_file_location("zzt_migration_spec_0002", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(db, direction: str = "upgrade") -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _migration_module()
    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        getattr(module, direction)()


def _own_rules(db, spec_key: str, rules: list, *, data_type: str = "enum") -> None:
    existing = db.execute(
        text("SELECT id FROM product_spec_registry WHERE spec_key = :key"), {"key": spec_key}
    ).first()
    payload = {"key": spec_key, "rules": json.dumps(rules)}
    if existing is None:
        db.execute(
            text(
                "INSERT INTO product_spec_registry (id, spec_key, label, data_type, derivation_rules)"
                " VALUES (:id, :key, :key, :dtype, CAST(:rules AS jsonb))"
            ),
            {**payload, "id": str(uuid.uuid4()), "dtype": data_type},
        )
    else:
        db.execute(
            text("UPDATE product_spec_registry SET derivation_rules = CAST(:rules AS jsonb) WHERE spec_key = :key"),
            payload,
        )


def _stored_rules(db, spec_key: str) -> list:
    return db.execute(
        text("SELECT derivation_rules FROM product_spec_registry WHERE spec_key = :key"),
        {"key": spec_key},
    ).scalar()


def test_ac_s1_3_a_human_contains_rule_converts_to_a_builder_only_words_rule():
    with pg_session() as db:
        key = unique_code("finlike")
        _own_rules(db, key, [{"match": "contains", "pattern": "GUNMETAL", "value": "gunmetal"}])

        _run_migration(db)

        stored = _stored_rules(db, key)
        assert all(set(rule.keys()) <= {"builder", "_seed"} for rule in stored)
        assert stored[0]["builder"]["kind"] == "words"
        assert "GUNMETAL" in stored[0]["builder"]["words"]


def test_ac_s1_3_an_old_builder_shaped_rule_converts_to_the_new_builder_shape():
    with pg_session() as db:
        key = unique_code("capoz")
        _own_rules(
            db,
            key,
            [{"match": "regex", "pattern": r"(?<![A-Z0-9X])(\d+(?:\.\d+)?)\s*OZ\b", "capture": 1,
              "builder": {"kind": "number_before", "word": "OZ"}}],
            data_type="numeric",
        )

        _run_migration(db)

        stored = _stored_rules(db, key)
        assert set(stored[0].keys()) <= {"builder", "_seed"}
        assert stored[0]["builder"]["kind"] == "number"
        assert "OZ" in stored[0]["builder"].get("before", [])


def test_ac_s1_3_a_code_suffix_rule_converts_to_the_code_kind():
    with pg_session() as db:
        key = unique_code("finish2")
        _own_rules(db, key, [{"match": "code_suffix", "pattern": "GM", "value": "gunmetal"}])

        _run_migration(db)

        stored = _stored_rules(db, key)
        assert stored[0]["builder"]["kind"] == "code"
        assert stored[0]["builder"]["code_match"] == "ends_with"


def test_ac_s1_3_a_column_rule_with_a_shape_gate_converts_with_only_when():
    with pg_session() as db:
        key = unique_code("dimlen")
        _own_rules(
            db,
            key,
            [
                {
                    "match": "from_field",
                    "pattern": "column:dimensions_length",
                    "unless": {"shape": ["round", "square"]},
                }
            ],
            data_type="numeric",
        )

        _run_migration(db)

        stored = _stored_rules(db, key)
        builder = stored[0]["builder"]
        assert builder["kind"] == "product"
        assert builder["fact"] == "length"
        assert builder.get("only_when") == {"spec": "shape", "is": False, "values": ["round", "square"]}


def test_ac_s1_3_a_name_head_rule_converts_to_the_product_name_fact():
    with pg_session() as db:
        key = unique_code("classy")
        _own_rules(db, key, [{"match": "name_head", "pattern": "class_tail"}])

        _run_migration(db)

        stored = _stored_rules(db, key)
        assert stored[0]["builder"] == {"kind": "product", "fact": "name"}


def test_ac_s1_3_neighbouring_rules_with_the_same_answer_are_folded():
    with pg_session() as db:
        key = unique_code("mount")
        _own_rules(
            db,
            key,
            [
                {"match": "contains", "pattern": "WALL HUNG", "value": "wall_hung"},
                {"match": "contains", "pattern": "WALL MOUNTED", "value": "wall_hung"},
                {"match": "contains", "pattern": "WALL MOUNT", "value": "wall_hung"},
            ],
        )

        _run_migration(db)

        stored = _stored_rules(db, key)
        assert len(stored) == 1, "three neighbours with the same answer must fold into one rule"
        assert set(stored[0]["builder"]["words"]) == {"WALL HUNG", "WALL MOUNTED", "WALL MOUNT"}


def test_ac_s1_3_an_unconvertible_hand_typed_regex_stops_the_migration():
    with pg_session() as db:
        key = unique_code("weird")
        _own_rules(db, key, [{"match": "regex", "pattern": r"\bFOO\s+\d", "value": True}])

        with pytest.raises(Exception) as excinfo:
            _run_migration(db)
        assert "FOO" in str(excinfo.value) or key in str(excinfo.value)


def test_ac_s1_3_a_second_run_over_already_converted_rules_is_a_no_op():
    with pg_session() as db:
        key = unique_code("idempot")
        _own_rules(db, key, [{"match": "contains", "pattern": "CHROME", "value": "chrome"}])

        _run_migration(db)
        first = _stored_rules(db, key)
        _run_migration(db)
        second = _stored_rules(db, key)
        assert first == second


# --------------------------------------------------------------------------- #
# AC-S1.5 - save validation, plain messages naming the missing part
# --------------------------------------------------------------------------- #
def _expect_400(rules, *, spec_key="finish", data_type="enum", allowed_values=None):
    from app.services.error_handler import AppException
    from app.services.product_spec_rules import validate_rules

    with pytest.raises(AppException) as excinfo:
        validate_rules(rules, spec_key=spec_key, data_type=data_type, allowed_values=allowed_values or ["black"])
    assert excinfo.value.status_code == 400
    return excinfo.value.message


def test_ac_s1_5_a_rule_with_no_builder_is_refused():
    message = _expect_400([{}])
    assert "no parts" in message.lower()


def test_ac_s1_5_a_rule_with_an_unknown_kind_is_refused():
    message = _expect_400([{"builder": {"kind": "regex"}}])
    assert "kind" in message.lower()


def test_ac_s1_5_words_with_no_words_is_refused():
    message = _expect_400([{"builder": {"kind": "words", "value": "black"}}])
    assert "word" in message.lower()


def test_ac_s1_5_words_with_no_value_is_refused():
    message = _expect_400([{"builder": {"kind": "words", "words": ["MATT BLACK"]}}])
    assert "value" in message.lower()


def test_ac_s1_5_number_with_no_before_and_no_after_is_refused():
    message = _expect_400([{"builder": {"kind": "number"}}], data_type="numeric")
    assert "number" in message.lower()


def test_ac_s1_5_size_with_a_bad_pick_is_refused():
    message = _expect_400([{"builder": {"kind": "size", "pick": "Q"}}], data_type="numeric")
    assert "size" in message.lower() or "number" in message.lower()


def test_ac_s1_5_code_with_a_bad_code_match_is_refused():
    message = _expect_400([{"builder": {"kind": "code", "code_match": "middle", "texts": ["-GM"], "value": "black"}}])
    assert "code" in message.lower()


def test_ac_s1_5_code_with_no_texts_is_refused():
    message = _expect_400([{"builder": {"kind": "code", "code_match": "ends_with", "value": "black"}}])
    assert "code" in message.lower()


def test_ac_s1_5_product_with_a_bad_fact_is_refused():
    message = _expect_400([{"builder": {"kind": "product", "fact": "colour"}}])
    assert "fact" in message.lower() or "product" in message.lower()


def test_ac_s1_5_only_when_without_a_spec_or_values_is_refused():
    message = _expect_400(
        [{"builder": {"kind": "words", "words": ["MATT BLACK"], "value": "black", "only_when": {}}}]
    )
    assert "only when" in message.lower()


def test_ac_s1_5_only_when_naming_brand_is_refused():
    message = _expect_400(
        [
            {
                "builder": {
                    "kind": "words",
                    "words": ["MATT BLACK"],
                    "value": "black",
                    "only_when": {"spec": "brand", "is": True, "values": ["SORENTO"]},
                }
            }
        ]
    )
    # The brand sentence, not "pick the specification" (review N-1).
    from app.services.product_spec_rules import BRAND_IS_NOT_A_SPEC

    assert message == BRAND_IS_NOT_A_SPEC


def test_ac_s1_5_no_message_carries_a_snake_case_word():
    message = _expect_400([{}])
    assert not re.search(r"[a-z]_[a-z]", message), message


# --------------------------------------------------------------------------- #
# AC-S1.16 - saving a rule re-reads exactly the products it changes
# --------------------------------------------------------------------------- #
@pytest.fixture
def registry_client(monkeypatch):
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService
    from fastapi.testclient import TestClient
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        import app.services.product_spec_change_listener as listener

        def _inline_in_this_session(codes):
            from app.services.product_spec_derivation import derive_for_code

            for code in codes:
                derive_for_code(db, code)

        monkeypatch.setattr(listener, "_rederive_inline", _inline_in_this_session)

        app.dependency_overrides[get_db] = lambda: db
        user = {"id": str(uuid.uuid4()), "email": "ruleengine@example.com"}
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
                "master_data.products.view",
                "master_data.products.edit",
                "master_data.spec_registry.view",
                "master_data.spec_registry.edit",
                "master_data.spec_registry.add",
            },
        )
        try:
            yield TestClient(app), db
        finally:
            app.dependency_overrides.clear()


def test_ac_s1_16_saving_a_words_rule_reports_and_applies_products_updated(registry_client):
    from decimal import Decimal

    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from app.models.product_spec import ProductSpecRegistry, ProductSpecifications

    client, db = registry_client
    key = unique_code("markerkey")
    cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("CAT"), category_name="cat")
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM"), uom_name="uom")
    row = ProductSpecRegistry(
        id=str(uuid.uuid4()),
        spec_key=key,
        label="Marker key",
        data_type="enum",
        allowed_values=["a"],
        source="user",
    )
    db.add_all([cat, uom, row])
    db.flush()

    matching_codes = []
    for i in range(2):
        code = unique_code(f"MATCH{i}")
        product = Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=code,
            description="THIS HAS ZZTMARKERWORD INSIDE IT",
            category_id=cat.id,
            base_uom_id=uom.id,
            list_price=Decimal("1.00"),
        )
        db.add(product)
        db.flush()
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=product.id))
        matching_codes.append(code)

    other_code = unique_code("OTHER")
    other = Product(
        id=str(uuid.uuid4()),
        product_code=other_code,
        product_name=other_code,
        description="NOTHING RELEVANT HERE",
        category_id=cat.id,
        base_uom_id=uom.id,
        list_price=Decimal("1.00"),
    )
    db.add(other)
    db.flush()
    db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=other.id))
    db.commit()

    response = client.patch(
        f"/api/v1/master-data/spec-registry/{key}",
        json={
            "derivation_rules": [
                {"builder": {"kind": "words", "words": ["ZZTMARKERWORD"], "value": "a"}}
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["products_updated"] == 2, body

    for code in matching_codes:
        spec = (
            db.query(ProductSpecifications)
            .join(Product, Product.id == ProductSpecifications.product_id)
            .filter(Product.product_code == code)
            .first()
        )
        assert (spec.values or {}).get(key, {}).get("value") == "a"


# --------------------------------------------------------------------------- #
# AC-S1.4 - golden parity: the old matcher and the new engine over real phrases
#
# Added by the coder (#1286, S1). Every product in the existing golden sample
# (`tests/fixtures/spec_derivation_golden_sample.json`, 2,000 real catalogue codes) is
# read twice: with the frozen pre-lane shipped rules (`tests/fixtures/legacy_shipped_rules.json`)
# through a frozen copy of the OLD matcher kept below, and with the new shipped builder
# rules through the new engine. Every difference must fall in one of the four groups plan
# D5 names; anything else is printed and fails the test.
# --------------------------------------------------------------------------- #
_GOLDEN_SAMPLE = Path(__file__).resolve().parent / "fixtures" / "spec_derivation_golden_sample.json"
_LEGACY_RULES = Path(__file__).resolve().parent / "fixtures" / "legacy_shipped_rules.json"

# Frozen from `product_spec_derivation` as it stood before this lane (origin/main 232182ae5).
_OLD_TRAP_LENGTH_RE = re.compile(r"[SP]\s*-?\s*TRAP\s*[:,]?\s*(\d+(?:\.\d+)?)\s*MM")
_OLD_RECORD_KINDS = {"from_field", "name_head"}
_OLD_DEFAULT_SCOPE_BY_KEY = {"class": "class_tail"}


def _old_number(raw):
    value = float(raw)
    return int(value) if value.is_integer() else value


def _old_rule_matches(rule, texts, code, default_scope="any"):
    kind = str(rule.get("match") or "contains").lower()
    pattern = str(rule.get("pattern") or "")
    if not pattern:
        return None
    scope = str(rule.get("source") or default_scope).lower()
    if kind == "code_suffix":
        if "-" not in code:
            return None
        if code.rsplit("-", 1)[1] != pattern.upper():
            return None
        return rule.get("value"), f"-{pattern.upper()}", "code"
    if kind in {"code_contains", "code_starts_with"}:
        needle = pattern.upper()
        hit = code.startswith(needle) if kind == "code_starts_with" else needle in code
        return (rule.get("value"), needle, "code") if hit else None
    names = [scope] if scope in texts else ["description", "flyer"]
    for name in names:
        haystack = texts.get(name, "")
        if not haystack:
            continue
        if kind == "contains":
            if re.search(rf"(?<![A-Z]){re.escape(pattern.upper())}(?![A-Z])", haystack):
                return rule.get("value"), pattern.upper(), name
        elif kind == "ends_with":
            token = pattern.upper()
            if haystack == token or haystack.endswith(" " + token):
                return rule.get("value"), token, name
        elif kind == "present":
            match = re.search(rf"(?<![A-Z]){pattern}(?![A-Z])", haystack)
            if match:
                return rule.get("value", True), match.group(0), name
        elif kind == "regex":
            match = re.search(pattern, haystack)
            if match:
                group = int(rule.get("capture") or 0)
                raw = match.group(group) if group else match.group(0)
                if group and raw is None:
                    continue
                value = _old_number(raw) if group else rule.get("value", True)
                scale = rule.get("scale")
                if scale and isinstance(value, (int, float)):
                    value = value * float(scale)
                    if float(value).is_integer():
                        value = int(value)
                return value, match.group(0), name
    return None


def _old_record_read(rule, product, category, spec_key):
    from app.services.product_spec_derivation import _class_from_description

    if product is None:
        return None
    kind = str(rule.get("match") or "").lower()
    pattern = str(rule.get("pattern") or "")
    if kind == "name_head":
        named = _class_from_description(product.description or "", (product.product_code or "").upper())
        return (named[0], named[1], "field") if named else None
    if pattern == "category":
        label = getattr(category, "class_label", None) if category is not None else None
        return (label, getattr(category, "category_code", "") or "", "category") if label else None
    if pattern.startswith("column:"):
        raw = getattr(product, pattern.split(":", 1)[1], None)
        return None if raw is None else (_old_number(str(raw)), f"{spec_key}={raw}", "field")
    return None


def _old_gate_passes(rule, held):
    for field, wanted in (("applies_when", True), ("unless", False)):
        for gate_key, permitted in (rule.get(field) or {}).items():
            value = held.get(gate_key)
            allowed = {str(v).strip().lower() for v in (permitted or [])}
            hit = value is not None and str(value).strip().lower() in allowed
            if hit is not wanted:
                return False
    return True


def _old_apply_rules(rules_by_key, texts, code, *, product, category, max_values):
    """The old first-match loop, values and cap flags only (the parity compares values)."""
    from app.services.product_spec_derivation import MULTI_VALUE_KEYS

    gates = set()
    for rules in rules_by_key.values():
        for rule in rules:
            gates |= set(rule.get("applies_when") or {}) | set(rule.get("unless") or {})
    ordered = [k for k in rules_by_key if k in gates] + [k for k in rules_by_key if k not in gates]
    values: dict = {}
    origins: dict = {}
    for key in ordered:
        collected, origin, value = [], None, None
        for rule in rules_by_key[key]:
            if not _old_gate_passes(rule, values):
                continue
            kind = str(rule.get("match") or "contains").lower()
            is_column = kind == "from_field" and str(rule.get("pattern") or "").startswith("column:")
            if kind in _OLD_RECORD_KINDS:
                hit = _old_record_read(rule, product, category, key)
            else:
                hit = _old_rule_matches(rule, texts, code, _OLD_DEFAULT_SCOPE_BY_KEY.get(key, "any"))
            if hit is None or hit[0] is None:
                continue
            cap = max_values.get(key)
            if cap is not None and not is_column and isinstance(hit[0], (int, float)) \
                    and not isinstance(hit[0], bool) and hit[0] > cap:
                break
            if key in MULTI_VALUE_KEYS:
                if collected and hit[2] != origin:
                    break
                if hit[0] not in collected:
                    origin = origin or hit[2]
                    collected.append(hit[0])
                continue
            value, origin = hit[0], hit[2]
            break
        if collected:
            value = collected[0] if len(collected) == 1 else collected
        if value is not None:
            values[key] = value
            origins[key] = origin
    return values, origins


class _ParityRow:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def _parity_inputs(entry):
    from decimal import Decimal

    def dec(raw):
        return None if raw is None else Decimal(raw)

    product = _ParityRow(
        product_code=entry["code"],
        description=entry["description"],
        dimensions_length=dec(entry["dimensions_length"]),
        dimensions_width=dec(entry["dimensions_width"]),
        dimensions_height=dec(entry["dimensions_height"]),
    )
    category = (
        _ParityRow(category_code=entry["category_code"], class_label=entry["class_label"])
        if entry["category_code"]
        else None
    )
    return product, category


# The four groups plan D5 names, and nothing else: the three differences the first
# parity run found outside them (hose length on bathtubs, "LENGTH-200MM", a model number
# read as bowls) were fixed in the shipped rules (fix round 1). Each takes one
# difference and says whether it is that group's.
def _group_two_digit_counts(diff):
    return diff["key"] in {"way_count", "spray_functions"} and isinstance(diff["after"], int) \
        and diff["after"] >= 10


def _group_power_stands_alone(diff):
    """D5 group 2: the old reading took its digits out of a code ("SRTKS1008L 1HP" read
    as 1008). Only a lost power reading whose number, in the description, is glued to a
    letter or digit before it (directly or across a hyphen) and never stands on its own
    before HP belongs here (review S-14: this used to accept any lost reading)."""
    if not (diff["key"] == "power_hp" and diff["after"] is None and diff["before"] is not None):
        return False
    before = diff["before"]
    number = re.escape(str(int(before)) if float(before).is_integer() else str(before))
    hp = r"[\s\-]*HP(?![A-Z])"
    glued = re.search(rf"(?:[A-Z0-9]|[A-Z0-9]-){number}{hp}", diff["description"])
    alone = re.search(rf"(?<![A-Z0-9.])(?<![A-Z0-9]-){number}{hp}", diff["description"])
    return bool(glued) and not alone


def _group_phrase_gap(diff):
    """D5 group 3, "OVER FLOW" also matches "OVER-FLOW", and every other phrase with it:
    the new reading's words differ from the rule's own only by a hyphen, a doubled space
    or no space between them (D5: "a space, a hyphen or nothing all count")."""
    if diff["before"] is not None or not diff["evidence"]:
        return False
    evidence = diff["evidence"]
    squashed = re.sub(r"[\s\-]+", "", evidence)
    return evidence not in diff["words"] and squashed in {re.sub(r"[\s\-]+", "", w) for w in diff["words"]}


def _group_flyer_labelled_size(diff):
    # The flyer is not a derivation input, so this group only shows in proposals.
    return False


def _fix_no_hose_on_a_tub(diff):
    """Fix round 1, and a correction of the OLD engine too: "BRAVAT 1.5M ... BATHTUB"
    read as a 1500 mm hose before this lane. A hose length is never read on a product
    whose class is Bathtub, Jacuzzi or Bathtub and Jacuzzi."""
    return (
        diff["key"] == "hose_length"
        and diff["after"] is None
        and re.search(r"BATHTUB|BATH TUB|JACUZZI", diff["description"]) is not None
    )


def _pr_a_has_massage_is_a_new_key(diff):
    """Chat fix PR-A S3: `has_massage` is a key the legacy rules never had. It appears, true,
    where a tub's catalogue text names massage, whirlpool, jacuzzi or air bubble; nothing else."""
    return diff["key"] == "has_massage" and diff["before"] is None and diff["after"] is True


_D5_GROUPS = (
    ("D5 1: two-digit ways and spray functions", _group_two_digit_counts),
    ("D5 2: power needs a number standing on its own", _group_power_stands_alone),
    ("D5 3: a hyphen, a double space or nothing between a phrase's words", _group_phrase_gap),
    ("D5 4: the flyer's labelled L, W, H", _group_flyer_labelled_size),
    ("Fix round 1: no hose length on a bathtub or jacuzzi (the old value was wrong)", _fix_no_hose_on_a_tub),
    ("Chat fix PR-A S3: has_massage, a new yes/no key", _pr_a_has_massage_is_a_new_key),
)


def _parity_differences():
    from app.services.product_spec_derivation import (
        _Derivation,
        _apply_scope,
        apply_rules,
        class_text,
        shipped_rules,
    )
    from app.services.product_spec_registry import shipped_max_values, shipped_scopes

    sample = json.loads(_GOLDEN_SAMPLE.read_text())["products"]
    legacy = {k: v for k, v in json.loads(_LEGACY_RULES.read_text()).items() if k != "brand"}
    new_rules = shipped_rules()
    scopes, caps = shipped_scopes(), shipped_max_values()

    def scoped(values: dict, origins: dict) -> dict:
        # The same scope `derive()` applies, class-from-category included (it never gates).
        out = _Derivation()
        for key, value in values.items():
            out.set(key, value, "", source="category" if origins.get(key) == "category" else "derived")
        _apply_scope(out, scopes)
        return {key: entry["value"] for key, entry in out.values.items()}

    differences = []
    for entry in sample:
        product, category = _parity_inputs(entry)
        code = (product.product_code or "").upper()
        description = (product.description or "").upper()
        class_tail = class_text(product.description or "", code)
        old_texts = {
            "description": description,
            "flyer": "",
            "class_tail": class_tail,
            "size_text": _OLD_TRAP_LENGTH_RE.sub(" ", description),
        }
        new_texts = {"description": description, "flyer": "", "class_tail": class_tail}
        before = scoped(
            *_old_apply_rules(legacy, old_texts, code, product=product, category=category, max_values=caps)
        )
        fired = apply_rules(new_rules, new_texts, code, product=product, category=category, max_values=caps)
        after = scoped(
            {k: r["value"] for k, r in fired.items() if r["value"] is not None},
            {k: r["origin"] for k, r in fired.items()},
        )
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                differences.append(
                    {
                        "code": entry["code"],
                        "key": key,
                        "before": before.get(key),
                        "after": after.get(key),
                        "evidence": (fired.get(key) or {}).get("evidence") or "",
                        "words": [
                            word
                            for rule in new_rules.get(key) or []
                            for word in rule["builder"].get("words") or []
                        ],
                        "description": description,
                    }
                )
    return differences


def test_s14_the_power_group_takes_only_a_number_glued_to_a_code():
    """Review S-14: the group used to accept ANY lost power reading."""
    def diff(description, before=1):
        return {"key": "power_hp", "before": before, "after": None, "description": description}

    assert _group_power_stands_alone(diff("SRTKS1-HP KITCHEN SINK"))
    assert _group_power_stands_alone(diff("WASTE DISPOSER SRT-1HP"))
    assert not _group_power_stands_alone(diff("WASTE DISPOSER 1 HP"))
    assert not _group_power_stands_alone(diff("WASTE DISPOSER 1HP, SRTKS1HP"))
    assert not _group_power_stands_alone({**diff("SRTKS1HP"), "after": 1})


def test_ac_s1_4_golden_parity_differences_fall_only_in_the_named_groups():
    grouped: dict[str, list] = {name: [] for name, _ in _D5_GROUPS}
    unexplained = []
    for diff in _parity_differences():
        row = (diff["code"], diff["key"], diff["before"], diff["after"], diff["evidence"])
        for name, belongs in _D5_GROUPS:
            if belongs(diff):
                grouped[name].append(row)
                break
        else:
            unexplained.append(row + (diff["description"],))

    for name, rows in grouped.items():
        print(f"{name}: {len(rows)}")
        for row in rows:
            print("   ", row)
    for row in unexplained:
        print("UNEXPLAINED", row)
    assert not unexplained, f"{len(unexplained)} differences in no named group (printed above)"


# --------------------------------------------------------------------------- #
# Fix round 1 (#1286): the three parity differences outside plan D5 are defects in the
# shipped rules, fixed there. Each pinned with the real golden-sample phrases.
# --------------------------------------------------------------------------- #
def _golden_entry(code: str) -> dict:
    sample = json.loads(_GOLDEN_SAMPLE.read_text())["products"]
    return next(entry for entry in sample if entry["code"] == code)


def _derive_golden(code: str):
    from app.services.product_spec_derivation import derive, shipped_rules
    from app.services.product_spec_registry import shipped_max_values, shipped_scopes

    product, category = _parity_inputs(_golden_entry(code))
    return derive(
        product,
        category,
        rules_by_key=shipped_rules(),
        scopes_by_key=shipped_scopes(),
        max_values=shipped_max_values(),
    )


@pytest.mark.parametrize("code", ["BRBTB25801W", "BRJC25828W-2", "BRJCB25730W25-2"])
def test_fix1_a_bathtubs_length_in_metres_is_not_a_hose_length(code):
    """"BRAVAT 1.75M DECK MOUNTED SIMPLE BATHTUB": 1.75 m is the tub, not a hose."""
    result = _derive_golden(code)
    assert "hose_length" not in result.values, result.values.get("hose_length")


def test_fix1_a_hand_bidet_still_reads_its_hose_length():
    from app.services.product_spec_derivation import apply_rules, shipped_rules

    texts = {"description": "SORENTO HAND BIDET C/W 1.2M HOSE", "flyer": "", "class_tail": "HAND BIDET"}
    found = apply_rules(shipped_rules(), texts, "ZZT-BIDET")
    assert found["hose_length"]["value"] == 1200


@pytest.mark.parametrize(
    "code,length",
    [("BRD314CP-2", 200), ("BRD314CP-4", 70), ("BRD314CP-4-ENG", 54), ("BRD323BTC-ENG", 400)],
)
def test_fix1_a_length_written_as_length_hyphen_mm_is_read(code, length):
    """"BRAVAT SHOWER ARM CHROME (LENGTH-200MM)" states its length in words."""
    result = _derive_golden(code)
    assert result.values["dim_length"]["value"] == length


def test_fix1_the_length_after_the_word_length_is_rule_4_of_length():
    from app.services.product_spec_derivation import shipped_rules

    builder = shipped_rules()["dim_length"][3]["builder"]
    assert builder == {
        "kind": "number",
        "look_in": "description",
        "after": ["LENGTH"],
        "before": ["MM"],
        "only_when": {"spec": "shape", "is": False, "values": ["round", "square"]},
    }


def test_fix1_a_model_number_before_bowl_is_flagged_not_stored():
    """"MOCHA GLASS BASIN (650x420MM) 6086 BOWL ONLY": 6086 is a model number."""
    result = _derive_golden("MGB6086B")
    assert "bowl_count" not in result.values
    assert any(
        flag["spec_key"] == "bowl_count" and flag["reason"] == "implausible_dimension"
        for flag in result.exceptions
    )


def test_fix1_bowl_count_ships_a_cap_of_nine():
    from app.services.product_spec_registry import shipped_max_values

    assert shipped_max_values()["bowl_count"] == 9
