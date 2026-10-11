"""RED tests for the generalised code rule behind product alternatives (CODE-VARIANTS slice,
owner 11 Oct 2026): "set DIY, -N, -PP as alternatives so they appear as alternatives just
like -NEW".

Rule A (suffix markers): Y is the code-rule alternative of X, same company, when removing
exactly ONE whole segment that is one of NEW, DIY, N, PP (split on `-`, case-insensitive,
whitespace around segments trimmed, as -NEW today) gives X's code and X exists.
Rule B (PP for NL): Y is the alternative of X, same company, when replacing exactly one
whole `PP` segment of Y with `NL` gives X's code and X exists (CB6635-NL -> CB6635-PP).

Links are stored like -NEW (source `auto_new`, no new source value), never touch `manual`
links or an `alternatives_manual` product, and are idempotent. Every row is seeded here with
a unique synthetic stem on the Postgres test schema; the stem stays glued to the owner's
real model text (CB1178, SRTSH22512, ...) so the segment shapes are the real ones.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import uuid

import pytest
from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.models.base import set_company_scope
from app.models.product import ProductAlternative
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.product_alternative_service import (
    SOURCE_AUTO_NEW,
    SOURCE_MANUAL,
    alternatives_for,
    backfill_new_alternatives,
    reconcile_new_alternatives,
)

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha
from tests._pg_fixture import blank_session


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
        yield session


def _stem(model: str) -> str:
    return f"ZZSX{uuid.uuid4().hex[:6].upper()}{model}"


def _p(db, code, company_id=DEFAULT_COMPANY_ID):
    return product(db, company_id=company_id, code=code)


def _links(db, product_id):
    rows = db.query(ProductAlternative).filter(ProductAlternative.product_id == product_id).all()
    return sorted((r.alternative_product_id, r.source) for r in rows)


def _alts(db, base):
    return [p.product_code for p in alternatives_for(db, [base.id]).get(base.id, [])]


def _no_links_anywhere(db, *products):
    ids = [p.id for p in products]
    return (
        db.query(ProductAlternative)
        .filter(
            (ProductAlternative.product_id.in_(ids))
            | (ProductAlternative.alternative_product_id.in_(ids))
        )
        .count()
        == 0
    )


# ---------------------------------------------------------------- 1 owner example, both orders
@pytest.mark.parametrize("base_first", [True, False], ids=["base-written-first", "variant-written-first"])
@pytest.mark.parametrize("which", ["variant", "base"])
def test_n_suffix_links_in_either_write_order(db, base_first, which):
    stem = _stem("SRTSH22512")
    if base_first:
        base = _p(db, stem)
        var = _p(db, f"{stem}-N")
    else:
        var = _p(db, f"{stem}-N")
        base = _p(db, stem)
    reconcile_new_alternatives(db, var if which == "variant" else base)
    assert _alts(db, base) == [f"{stem}-N"]
    assert _links(db, base.id) == [(var.id, SOURCE_AUTO_NEW)]
    assert _links(db, var.id) == []


# ---------------------------------------------------------------- 2 DIY, PP, multi-suffix
@pytest.mark.parametrize(
    "base_code, var_code",
    [("CB1178", "CB1178-DIY"), ("CB6640", "CB6640-PP"), ("CB2157SS-NL", "CB2157SS-NL-PP")],
)
@pytest.mark.parametrize("which", ["variant", "base"])
def test_diy_pp_and_multi_suffix_link(db, base_code, var_code, which):
    stem = _stem("")
    base = _p(db, f"{stem}{base_code}")
    var = _p(db, f"{stem}{var_code}")
    reconcile_new_alternatives(db, var if which == "variant" else base)
    assert _links(db, base.id) == [(var.id, SOURCE_AUTO_NEW)]


def test_case_and_spaces_around_segments_are_normalised(db):
    stem = _stem("CB1178")
    base = _p(db, stem)
    var = _p(db, f"{stem} - diy")
    reconcile_new_alternatives(db, var)
    assert _links(db, base.id) == [(var.id, SOURCE_AUTO_NEW)]


# ---------------------------------------------------------------- 3 no base row
def test_diy_without_its_base_row_links_nothing(db):
    stem = _stem("SRT207")
    diy = _p(db, f"{stem}-DIY")
    bl_diy = _p(db, f"{stem}-BL-DIY")
    reconcile_new_alternatives(db, diy)
    reconcile_new_alternatives(db, bl_diy)
    assert _no_links_anywhere(db, diy, bl_diy)


# ---------------------------------------------------------------- 4 not a marker
def test_nl_and_n_followed_by_more_text_are_not_markers(db):
    stem = _stem("SRTSH22512")
    base = _p(db, stem)
    nl = _p(db, f"{stem}-NL")
    n_wepls = _p(db, f"{stem}-N-WEPLS")
    for p in (nl, n_wepls, base):
        reconcile_new_alternatives(db, p)
    assert _links(db, base.id) == []
    assert _links(db, nl.id) == [] and _links(db, n_wepls.id) == []


def test_glued_diy_text_is_not_a_segment(db):
    stem = _stem("CB1178")
    base = _p(db, stem)
    glued = _p(db, f"{stem}DIY")
    reconcile_new_alternatives(db, glued)
    reconcile_new_alternatives(db, base)
    assert _no_links_anywhere(db, base, glued)


# ---------------------------------------------------------------- 5 / 6 / 7 guards
def test_other_company_never_links(db):
    seed_mocha(db)
    stem = _stem("CB1178")
    base = _p(db, stem, DEFAULT_COMPANY_ID)
    diy = _p(db, f"{stem}-DIY", MOCHA_ID)
    reconcile_new_alternatives(db, diy)
    reconcile_new_alternatives(db, base)
    assert _no_links_anywhere(db, base, diy)


def test_manual_base_gets_no_auto_link(db):
    stem = _stem("CB1178")
    base = _p(db, stem)
    base.alternatives_manual = True
    db.flush()
    diy = _p(db, f"{stem}-DIY")
    assert reconcile_new_alternatives(db, diy) == 0
    assert reconcile_new_alternatives(db, base) == 0
    assert _links(db, base.id) == []


def test_manual_links_survive_a_rename(db):
    stem = _stem("CB1178")
    base = _p(db, stem)
    diy = _p(db, f"{stem}-DIY")
    hand = _p(db, _stem("HAND"))
    reconcile_new_alternatives(db, diy)
    db.add(ProductAlternative(product_id=base.id, alternative_product_id=hand.id,
                              source=SOURCE_MANUAL, company_id=DEFAULT_COMPANY_ID))
    db.flush()
    diy.product_code = f"{_stem('ELSE')}-DIY"
    db.flush()
    reconcile_new_alternatives(db, diy)
    assert _links(db, base.id) == [(hand.id, SOURCE_MANUAL)]


def test_rename_drops_the_stale_diy_auto_link(db):
    stem = _stem("CB1178")
    base = _p(db, stem)
    diy = _p(db, f"{stem}-DIY")
    reconcile_new_alternatives(db, diy)
    assert _links(db, base.id) == [(diy.id, SOURCE_AUTO_NEW)]
    diy.product_code = f"{_stem('ELSE')}-DIY"
    db.flush()
    reconcile_new_alternatives(db, diy)
    assert _links(db, base.id) == []


def test_reconcile_is_idempotent_for_the_new_markers(db):
    stem = _stem("CB1178")
    base = _p(db, stem)
    diy = _p(db, f"{stem}-DIY")
    reconcile_new_alternatives(db, diy)
    assert reconcile_new_alternatives(db, diy) == 0
    assert reconcile_new_alternatives(db, base) == 0
    assert _links(db, base.id) == [(diy.id, SOURCE_AUTO_NEW)]


# ---------------------------------------------------------------- 10-12 PP replaces NL
@pytest.mark.parametrize("which", ["nl", "pp"])
def test_pp_is_the_alternative_of_its_nl_twin_without_a_bare_base(db, which):
    stem = _stem("CB6635")
    nl = _p(db, f"{stem}-NL")
    pp = _p(db, f"{stem}-PP")
    reconcile_new_alternatives(db, nl if which == "nl" else pp)
    assert _links(db, nl.id) == [(pp.id, SOURCE_AUTO_NEW)]
    assert _links(db, pp.id) == []
    assert _alts(db, nl) == [f"{stem}-PP"]


def test_pp_nl_twin_written_in_either_order(db):
    stem = _stem("CB6635")
    pp = _p(db, f"{stem}-PP")
    nl = _p(db, f"{stem}-NL")
    reconcile_new_alternatives(db, pp)
    assert _links(db, nl.id) == [(pp.id, SOURCE_AUTO_NEW)]


@pytest.mark.parametrize("which", ["nl", "pp"])
def test_mid_code_pp_nl_twin(db, which):
    stem = _stem("CB6635")
    nl = _p(db, f"{stem}-BL-NL")
    pp = _p(db, f"{stem}-BL-PP")
    reconcile_new_alternatives(db, nl if which == "nl" else pp)
    assert _links(db, nl.id) == [(pp.id, SOURCE_AUTO_NEW)]


def test_nl_without_its_pp_twin_gets_no_link(db):
    stem = _stem("CB6635")
    gy_nl = _p(db, f"{stem}-GY-NL")
    other_pp = _p(db, f"{stem}-PP")  # a PP, but not the GY twin
    reconcile_new_alternatives(db, gy_nl)
    reconcile_new_alternatives(db, other_pp)
    assert _no_links_anywhere(db, gy_nl)


# ---------------------------------------------------------------- 14 no special direction
def test_reverse_looking_pair_is_linked_like_any_other_suffix(db):
    stem = _stem("CB55CR")
    base = _p(db, stem)
    var = _p(db, f"{stem}-N")
    reconcile_new_alternatives(db, base)
    assert _links(db, base.id) == [(var.id, SOURCE_AUTO_NEW)]
    assert _links(db, var.id) == []


# ---------------------------------------------------------------- 8 backfill
def _seed_backfill_world(db):
    s = _stem("BF")
    rows = {
        "new": (_p(db, f"{s}A"), _p(db, f"{s}A-NEW")),
        "diy": (_p(db, f"{s}B"), _p(db, f"{s}B-DIY")),
        "n": (_p(db, f"{s}C"), _p(db, f"{s}C-N")),
        "pp": (_p(db, f"{s}D"), _p(db, f"{s}D-PP")),
        "ppnl": (_p(db, f"{s}E-NL"), _p(db, f"{s}E-PP")),
    }
    return rows


def test_backfill_dry_run_reports_every_marker_and_writes_nothing(db):
    rows = _seed_backfill_world(db)
    report = backfill_new_alternatives(db, apply=False)
    assert report["would_add"] >= 5
    assert report["scanned"] >= 5
    for key, (base, alt) in rows.items():
        assert (base.product_code, alt.product_code) in report["pairs"], key
    assert all(_links(db, base.id) == [] for base, _ in rows.values())


def test_backfill_apply_writes_then_second_apply_adds_nothing(db):
    rows = _seed_backfill_world(db)
    report = backfill_new_alternatives(db, apply=True)
    assert report["added"] >= 5
    for key, (base, alt) in rows.items():
        assert _links(db, base.id) == [(alt.id, SOURCE_AUTO_NEW)], key
    assert backfill_new_alternatives(db, apply=True)["added"] == 0


# ---------------------------------------------------------------- 9 script
def _load_script():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backfill_product_alternatives.py"
    spec = importlib.util.spec_from_file_location("backfill_product_alternatives_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SessionHandle:
    """Hands the script the test's rolled-back session; its close()/commit() are no-ops."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name in ("close", "commit"):
            return lambda *a, **k: None
        return getattr(self._db, name)


def test_script_dry_run_is_the_default_prints_pairs_and_writes_nothing(db, monkeypatch, capsys):
    rows = _seed_backfill_world(db)
    script = _load_script()
    monkeypatch.setattr(script, "SessionLocal", lambda: _SessionHandle(db))
    monkeypatch.setattr(sys, "argv", ["backfill_product_alternatives.py"])
    assert script.main() == 0
    out = capsys.readouterr().out
    for key in ("diy", "n", "pp", "ppnl"):
        base, alt = rows[key]
        assert f"{base.product_code} -> {alt.product_code}" in out, key
    assert "would_add" in out
    assert all(_links(db, base.id) == [] for base, _ in rows.values())


# ---------------------------------------------------------------- any position (guards)
@pytest.mark.parametrize("which", ["base", "variant"])
@pytest.mark.parametrize(
    "base_code, var_code",
    [
        ("MWCX7605-S10", "MWCX7605-N-S10"),   # mid-code N (real dev pair shape)
        ("MWCX7606-BL", "MWCX7606-DIY-BL"),   # mid-code DIY
    ],
)
def test_marker_in_the_middle_of_a_code_links(db, base_code, var_code, which):
    stem = _stem("")
    base = _p(db, f"{stem}{base_code}")
    var = _p(db, f"{stem}{var_code}")
    reconcile_new_alternatives(db, base if which == "base" else var)
    assert _links(db, base.id) == [(var.id, SOURCE_AUTO_NEW)]


@pytest.mark.parametrize("which", ["nl", "pp"])
def test_mid_code_nl_gets_its_pp_twin_with_a_trailing_segment(db, which):
    stem = _stem("CB6636")
    nl = _p(db, f"{stem}-NL-BL")
    pp = _p(db, f"{stem}-PP-BL")
    reconcile_new_alternatives(db, nl if which == "nl" else pp)
    assert _links(db, nl.id) == [(pp.id, SOURCE_AUTO_NEW)]
