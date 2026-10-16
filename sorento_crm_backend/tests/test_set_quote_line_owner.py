"""SET-QUOTE-LINE owner hand test round: S4 sheet import writes SET rows (C2-C4) and the
Complete set rule on every set change (A2b). Written red before the fixes.

Import helpers come from `test_series_import_sets_families.py`, line helpers from
`test_set_quote_line.py`.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.company import Company
from app.models.projects import ProjectSeriesSet

from ._pg_fixture import blank_session
from .test_series_import_sets_families import (  # noqa: F401
    _apply,
    _on_series,
    _price,
    _product,
    _row,
    _set,
    _uid,
    _world,
)
from .test_set_quote_line import _env, _product as _line_product, _save, _set as _line_set


def _set_rows(w) -> dict:
    """{set_id: (selling_price, max_discount_pct)} for the series."""
    rows = w.db.query(ProjectSeriesSet).filter(ProjectSeriesSet.series_id == w.series.id).all()
    return {r.product_set_id: (r.selling_price, r.max_discount_pct) for r in rows}


def _two_part_set(w, code):
    a, b = _product(w, f"{code}-PED"), _product(w, f"{code}-CIS")
    return _set(w, code, [a, b]), a, b


# ------------------------------------------------------------------ C2


def test_c2_a_set_code_with_numbers_writes_one_series_set_row_and_unpriced_part_rows():
    with _world() as w:
        ps, a, b = _two_part_set(w, "ZOWN1SET")

        _apply(w, [_row("ZOWN1SET", "900", "8")])

        assert _set_rows(w) == {ps.id: (Decimal("900"), Decimal("8"))}
        assert _on_series(w) == {a.product_code, b.product_code}
        assert _price(w, a) == (None, None)
        assert _price(w, b) == (None, None)


def test_c2_a_code_only_row_writes_the_set_row_with_no_numbers():
    with _world() as w:
        ps, _a, _b = _two_part_set(w, "ZOWN2SET")

        _apply(w, ["ZOWN2SET"])

        assert _set_rows(w) == {ps.id: (None, None)}


def test_c2_a_code_only_row_never_blanks_numbers_already_on_the_set_row():
    with _world() as w:
        ps, _a, _b = _two_part_set(w, "ZOWN3SET")
        _apply(w, [_row("ZOWN3SET", "900", "8")])

        _apply(w, ["ZOWN3SET"])

        assert _set_rows(w) == {ps.id: (Decimal("900"), Decimal("8"))}


def test_c2_reimporting_with_a_new_price_updates_the_set_row():
    with _world() as w:
        ps, _a, _b = _two_part_set(w, "ZOWN4SET")
        _apply(w, [_row("ZOWN4SET", "900", "8")])

        _apply(w, [_row("ZOWN4SET", "950", "5")])

        assert _set_rows(w) == {ps.id: (Decimal("950"), Decimal("5"))}


def test_c2_another_companys_set_is_never_written():
    with _world() as w:
        other = Company(id=_uid(), name=f"zzt-other-{_uid()[:6]}", code=f"ZO{_uid()[:6]}")
        w.db.add(other)
        w.db.flush()
        fp = _product(w, "ZOWN5-X", company_id=other.id)
        _set(w, "ZOWN5SET", [fp], company_id=other.id)

        _apply(w, [_row("ZOWN5SET", "900", "8"), _row("ZOWN5", "900", "8")])

        assert _set_rows(w) == {}


# ------------------------------------------------------------------ C3


def test_c3_a_family_code_writes_a_row_for_every_active_set_in_the_family():
    with _world() as w:
        s1, _a, _b = _two_part_set(w, "ZOWN6-RL")
        s2, _c, _d = _two_part_set(w, "ZOWN6-SH")
        inactive = _set(w, "ZOWN6-OLD", [_product(w, "ZOWN6-OLDP")], active=False)
        other = _two_part_set(w, "ZOWN66-RL")[0]  # continuation, not family

        _apply(w, [_row("ZOWN6", "700", "6")])

        rows = _set_rows(w)
        assert rows == {s1.id: (Decimal("700"), Decimal("6")), s2.id: (Decimal("700"), Decimal("6"))}
        assert inactive.id not in rows and other.id not in rows


def test_c3_replace_removes_set_rows_the_sheet_does_not_resolve_and_append_keeps_them():
    with _world() as w:
        keep, _a, _b = _two_part_set(w, "ZOWN7KEEP")
        drop, _c, _d = _two_part_set(w, "ZOWN7DROP")
        _apply(w, [_row("ZOWN7KEEP", "100", "1"), _row("ZOWN7DROP", "200", "2")])
        assert set(_set_rows(w)) == {keep.id, drop.id}

        _apply(w, [_row("ZOWN7KEEP", "110", "1")], mode="append")
        assert set(_set_rows(w)) == {keep.id, drop.id}

        _apply(w, [_row("ZOWN7KEEP", "120", "1")], mode="replace")
        assert _set_rows(w) == {keep.id: (Decimal("120"), Decimal("1"))}


# ------------------------------------------------------------------ C4


def test_c4_the_resolved_entry_names_the_sets_for_a_set_code_and_for_a_family():
    with _world() as w:
        _two_part_set(w, "ZOWN8SET")
        _two_part_set(w, "ZOWN9-RL")
        _two_part_set(w, "ZOWN9-SH")

        report = _apply(w, [_row("ZOWN8SET", "1", "1"), _row("ZOWN9", "1", "1")])

        by_code = {entry["code"]: entry for entry in report["resolved"]}
        assert by_code["ZOWN8SET"].get("sets") == ["ZOWN8SET"]
        assert sorted(by_code["ZOWN9"].get("sets", [])) == ["ZOWN9-RL", "ZOWN9-SH"]


# ------------------------------------------------------------------ A2b


def _two_sets(db, env):
    first = _line_set(db, env["company_id"], env["category"], env["uom"])
    second = _line_set(db, env["company_id"], env["category"], env["uom"])
    return first, second


def _parts_text(product_set) -> str:
    return " + ".join(
        m.product.product_code
        if Decimal(m.quantity) == 1
        else f"{Decimal(m.quantity).normalize():f} x {m.product.product_code}"
        for m in sorted(product_set.members, key=lambda m: m.sort_order or 0)
    )[:100]


def test_a2b_changing_the_set_replaces_a_non_blank_complete_set_with_the_new_sets_parts():
    with blank_session() as db:
        env = _env(db)
        first, second = _two_sets(db, env)
        line = _save(db, env, {"product_set_id": first.id, "unit_price": "900.00", "quantity": "1"})
        assert line.complete_set == _parts_text(first)

        line = _save(db, env, {"product_set_id": second.id}, line=line)

        assert line.complete_set == _parts_text(second)


def test_a2b_moving_a_line_from_a_set_to_a_product_clears_complete_set():
    with blank_session() as db:
        env = _env(db)
        first, _second = _two_sets(db, env)
        product = _line_product(db, env["category"], env["uom"], f"ZZT-A2B-{_uid()[:5]}")
        line = _save(db, env, {"product_set_id": first.id, "unit_price": "900.00", "quantity": "1"})
        assert line.complete_set

        line = _save(db, env, {"product_set_id": None, "product_id": product.id}, line=line)

        assert line.complete_set is None


def test_a2b_moving_a_line_from_a_set_to_no_product_clears_complete_set():
    with blank_session() as db:
        env = _env(db)
        first, _second = _two_sets(db, env)
        line = _save(db, env, {"product_set_id": first.id, "unit_price": "900.00", "quantity": "1"})

        line = _save(
            db, env, {"product_set_id": None, "description_snapshot": "Bespoke vanity"}, line=line
        )

        assert line.complete_set is None


def test_a2b_a_plain_product_lines_complete_set_is_left_as_stored():
    with blank_session() as db:
        env = _env(db)
        product = _line_product(db, env["category"], env["uom"], f"ZZT-A2B-{_uid()[:5]}")
        line = _save(
            db,
            env,
            {
                "product_id": product.id,
                "unit_price": "10.00",
                "quantity": "1",
                "complete_set": "KEEP ME",
            },
        )

        line = _save(db, env, {"quantity": "2"}, line=line)

        assert line.complete_set == "KEEP ME"


def test_a2b_a_set_change_overrides_a_complete_set_carried_in_the_same_write():
    with blank_session() as db:
        env = _env(db)
        first, second = _two_sets(db, env)
        line = _save(db, env, {"product_set_id": first.id, "unit_price": "900.00", "quantity": "1"})

        line = _save(
            db,
            env,
            {"product_set_id": second.id, "complete_set": _parts_text(first)},
            line=line,
        )

        assert line.complete_set == _parts_text(second)


def test_a2b_a_set_change_with_a_null_complete_set_in_the_body_still_stores_the_new_parts():
    with blank_session() as db:
        env = _env(db)
        first, second = _two_sets(db, env)
        line = _save(db, env, {"product_set_id": first.id, "unit_price": "900.00", "quantity": "1"})

        line = _save(db, env, {"product_set_id": second.id, "complete_set": None}, line=line)

        assert line.complete_set == _parts_text(second)


def _family_world(w):
    rl, _a, _b = _two_part_set(w, "ZOWNA-RL")
    sh, _c, _d = _two_part_set(w, "ZOWNA-SH")
    return rl, sh


def test_s4_the_set_code_row_beats_a_family_row_reaching_the_same_set_set_row_first():
    with _world() as w:
        rl, sh = _family_world(w)

        _apply(w, [_row("ZOWNA-RL", "1050", "6"), _row("ZOWNA", "900", "5")])

        assert _set_rows(w) == {
            rl.id: (Decimal("1050"), Decimal("6")),
            sh.id: (Decimal("900"), Decimal("5")),
        }


def test_s4_the_set_code_row_beats_a_family_row_reaching_the_same_set_family_row_first():
    with _world() as w:
        rl, sh = _family_world(w)

        _apply(w, [_row("ZOWNA", "900", "5"), _row("ZOWNA-RL", "1050", "6")])

        assert _set_rows(w) == {
            rl.id: (Decimal("1050"), Decimal("6")),
            sh.id: (Decimal("900"), Decimal("5")),
        }
