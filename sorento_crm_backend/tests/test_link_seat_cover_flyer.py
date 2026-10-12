"""WC-SEAT-COMPAT: the one-off script that links the seat cover flyer (owner decision 12 Oct).

    venv/bin/python scripts/oneoff/link_seat_cover_flyer.py                  # dry run
    venv/bin/python scripts/oneoff/link_seat_cover_flyer.py --apply          # write, source flyer
    venv/bin/python scripts/oneoff/link_seat_cover_flyer.py --apply --retire-spec

Links ONLY the SKUs a printed row names unambiguously; every other SKU of a printed model is
printed as UNRESOLVED with its reason and never linked. Idempotent.
Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
"""
from __future__ import annotations

import io
from collections import Counter

import pytest

from app.models.audit import AuditLog
from app.models.base import set_company_scope
from app.models.product_compatibility import ProductCompatibility
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.product_compatibility_service import covers_for
from scripts.oneoff import link_seat_cover_flyer as script

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha
from tests._pg_fixture import blank_session

CATALOGUE = [
    "SRTSCBD331-UF",
    "SRTSCBD823-MBL",
    "SRTSCBD824-BL",
    "SRTSCBD333-UF",
    "SRTSCBD333A-UF",
    "SRTSCBD326",
    "SRTSCBD327",
    "SRTSCBD407",
    "SRTSCBD841-BL",
    # SRTWC8383: plain row 3, colour rows 24 (MBL), 25 (BL), 28 (GY), 29 (RG).
    "SRTWC8383-RL-UF",
    "SRTWC8383-SH-UF-150-NEW",
    "SRTWC8383-RL-MBL",
    "SRTWC8383-RL-BL",
    "SRTWC8383-RL-MK",  # a finish the flyer does not print
    "SRTWC8383-RL-FT",  # a code part nobody has said is a plain variant marker
    "SRTWC8383-RL-MBL-BL",  # fits -MBL and -BL equally
    "SRTWC8383-SC",  # a seat cover, never a WC
    "SRTWC8122",
    "SRTWC8066-RL",
    "SRTWC8066-S-BL",
    "SRTWC8066-S",  # S is printed only with a colour
    "SRTWC202-S-PINK",  # row 22 prints SRTWC202 with the note "Pink"
    "SRTWC203-S-PINK",  # row 23 prints SRTWC203 as Blue
    "SRTWC8088-RL-UF",  # its cover SRTSCBD330-UF is not in the catalogue
]


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture()
def catalogue(db):
    rows = {code: product(db, company_id=DEFAULT_COMPANY_ID, code=code) for code in CATALOGUE}
    db.commit()
    return rows


def _run(db, *argv) -> tuple[int, str]:
    out = io.StringIO()
    code = script.main(list(argv), db=db, out=out)
    return code, out.getvalue()


def _pairs(db) -> set[tuple[str, str]]:
    from app.models.base import company_scope
    from app.models.product import Product

    with company_scope(db, None):
        rows = db.query(ProductCompatibility).all()
        codes = {p.id: p.product_code for p in db.query(Product).all()}
    return {(codes[r.wc_product_id], codes[r.seat_cover_product_id]) for r in rows}


# --------------------------------------------------------------------------- data


def test_flyer_data_is_the_49_printed_pairs():
    pairs = script.flyer_pairs()
    assert len(script.FLYER_ROWS) == 35
    assert len(pairs) == 49
    assert len({cover for _, _, cover in pairs}) == 36
    assert len({wc for _, wc, _ in pairs}) == 48
    assert {(wc, cover) for row, wc, cover in pairs if row == 6} == {
        ("SRTWC8122", "SRTSCBD333-UF"),
        ("SRTWC8122", "SRTSCBD333A-UF"),
    }
    per_wc = Counter(wc for _, wc, _ in pairs)
    assert {wc for wc, n in per_wc.items() if n > 1} == {"SRTWC8122"}
    wcs = {wc for _, wc, _ in pairs}
    for smart in ("SRTWC185", "SRTWC187", "SRTWC189", "SRTWC189-G2", "SRTWC191", "SRTWC191-G2", "SRTWC195"):
        assert smart not in wcs


# --------------------------------------------------------------------------- dry run


EXPECTED_LINKS = {
    ("SRTWC8383-RL-UF", "SRTSCBD331-UF"),
    ("SRTWC8383-SH-UF-150-NEW", "SRTSCBD331-UF"),
    ("SRTWC8383-RL-MBL", "SRTSCBD823-MBL"),
    ("SRTWC8383-RL-BL", "SRTSCBD824-BL"),
    ("SRTWC8122", "SRTSCBD333-UF"),
    ("SRTWC8122", "SRTSCBD333A-UF"),
    ("SRTWC8066-RL", "SRTSCBD407"),
    ("SRTWC8066-S-BL", "SRTSCBD841-BL"),
    ("SRTWC202-S-PINK", "SRTSCBD326"),
}

EXPECTED_UNRESOLVED = {
    ("SRTWC8383-RL-MK", "finish_not_printed"),
    ("SRTWC8383-RL-FT", "unknown_segment"),
    ("SRTWC8383-RL-MBL-BL", "ambiguous"),
    ("SRTWC8066-S", "sub_model_not_printed"),
    ("SRTWC203-S-PINK", "finish_not_printed"),
}


def test_plan_links_only_unambiguous_skus(db, catalogue):
    plan = script.plan(db)
    assert {(link["wc_code"], link["cover_code"]) for link in plan["links"]} == EXPECTED_LINKS
    assert {(u["wc_code"], u["reason"]) for u in plan["unresolved"]} == EXPECTED_UNRESOLVED
    # The colour row maps through the printed code: 8383-RL-MBL is row 24, never row 3.
    [mbl] = [link for link in plan["links"] if link["wc_code"] == "SRTWC8383-RL-MBL"]
    assert (mbl["flyer_row"], mbl["printed_code"]) == (24, "SRTWC8383-MBL")
    # -SC SKUs are seat covers: neither linked nor reported as a WC.
    every = {link["wc_code"] for link in plan["links"]} | {u["wc_code"] for u in plan["unresolved"]}
    assert "SRTWC8383-SC" not in every
    # A SKU whose printed cover is missing is reported, never linked.
    assert {
        (m["wc_code"], m["reason"]) for m in plan["missing"] if m["wc_code"] == "SRTWC8088-RL-UF"
    } == {("SRTWC8088-RL-UF", "cover_missing")}


def test_dry_run_prints_the_pairs_and_unresolved_and_writes_nothing(db, catalogue):
    code, out = _run(db)
    assert code == 0
    assert "DRY RUN" in out
    assert "LINK  row 24  SRTWC8383-MBL -> SRTSCBD823-MBL  SKU SRTWC8383-RL-MBL" in out
    assert "LINK  row 6  SRTWC8122 -> SRTSCBD333A-UF  SKU SRTWC8122" in out
    assert "UNRESOLVED  SRTWC8383-RL-MK  finish_not_printed" in out
    assert "UNRESOLVED  SRTWC8066-S  sub_model_not_printed" in out
    assert "MISSING  row 2  SRTWC8088 -> SRTSCBD330-UF  SKU SRTWC8088-RL-UF  cover_missing" in out
    assert "SRTWC8383-SC" not in out
    assert "9 links to write" in out
    assert _pairs(db) == set()


# --------------------------------------------------------------------------- apply


def test_apply_writes_flyer_links_and_is_idempotent(db, catalogue):
    code, out = _run(db, "--apply")
    assert code == 0
    assert _pairs(db) == EXPECTED_LINKS
    links = db.query(ProductCompatibility).all()
    assert {link.source for link in links} == {"flyer"}
    assert "9 links written" in out

    code, again = _run(db, "--apply")
    assert code == 0
    assert "0 links written, 9 already linked" in again
    assert db.query(ProductCompatibility).count() == 9

    # No inheritance: a sibling the flyer does not name has no cover.
    unresolved = catalogue["SRTWC8383-RL-MK"]
    assert covers_for(db, [unresolved.id]) == {}


def test_apply_keeps_a_manual_link_and_never_duplicates_it(db, catalogue):
    wc, cover = catalogue["SRTWC8383-RL-UF"], catalogue["SRTSCBD331-UF"]
    db.add(
        ProductCompatibility(
            company_id=wc.company_id,
            seat_cover_product_id=cover.id,
            wc_product_id=wc.id,
            source="manual",
        )
    )
    db.commit()
    _run(db, "--apply")
    [row] = db.query(ProductCompatibility).filter(ProductCompatibility.wc_product_id == wc.id).all()
    assert row.source == "manual"


def test_apply_links_each_company_inside_itself(db, catalogue):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    mocha_wc = product(db, company_id=MOCHA_ID, code="SRTWC8383-RL-UF")
    mocha_cover = product(db, company_id=MOCHA_ID, code="SRTSCBD331-UF")
    db.commit()
    _run(db, "--apply")
    from app.models.base import company_scope

    with company_scope(db, None):
        [link] = (
            db.query(ProductCompatibility)
            .filter(ProductCompatibility.wc_product_id == mocha_wc.id)
            .all()
        )
    assert link.seat_cover_product_id == mocha_cover.id
    assert str(link.company_id) == MOCHA_ID


# --------------------------------------------------------------------------- retire


@pytest.fixture()
def seat_cover_spec(db, catalogue):
    from app.services.product_spec_write import apply_spec_values

    registry = db.query(ProductSpecRegistry).filter_by(spec_key="seat_cover").one_or_none()
    if registry is None:
        registry = ProductSpecRegistry(
            spec_key="seat_cover",
            label="Seat Cover",
            data_type="enum",
            allowed_values=["SRTSCBD331-UF"],
            source="user",
            is_active=True,
            derivation_rules=[{"match": "contains", "pattern": "SRTWC8383", "value": "SRTSCBD331-UF"}],
        )
        db.add(registry)
        db.flush()
    apply_spec_values(
        db,
        "SRTWC8383-RL-UF",
        [{"spec_key": "seat_cover", "value": "SRTSCBD331-UF", "source": "human"}],
        commit=False,
    )
    db.commit()
    return registry


def _holding(db) -> int:
    return (
        db.query(ProductSpecifications)
        .filter(ProductSpecifications.values.has_key("seat_cover"))  # noqa: W601
        .count()
    )


def test_retire_needs_apply(db, seat_cover_spec):
    code, out = _run(db, "--retire-spec")
    assert code == 2
    assert "--apply" in out
    assert _holding(db) == 1


def test_apply_then_retire_removes_the_values_and_deactivates_the_key(db, seat_cover_spec):
    code, out = _run(db, "--apply", "--retire-spec")
    assert code == 0, out
    db.refresh(seat_cover_spec)
    assert seat_cover_spec.is_active is False
    assert seat_cover_spec.derivation_rules == []
    assert _holding(db) == 0
    assert "1 Seat Cover spec value removed" in out
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event == "seat_cover_spec.retire", AuditLog.entity_id == seat_cover_spec.id)
        .all()
    )
    assert audit and "SRTWC8383-RL-UF" in str(audit[-1].old_values)
    # Idempotent: a second run has nothing left to retire.
    code, again = _run(db, "--apply", "--retire-spec")
    assert code == 0
    assert "0 Seat Cover spec values removed" in again


def test_retire_refused_while_a_company_holding_a_value_has_no_flyer_link(db, seat_cover_spec):
    from app.services.product_spec_write import apply_spec_values

    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    # Mocha holds a value on a SKU whose cover Mocha does not carry: nothing can replace it.
    product(db, company_id=MOCHA_ID, code="SRTWC8383-RL-UF")
    apply_spec_values(
        db,
        "SRTWC8383-RL-UF",
        [{"spec_key": "seat_cover", "value": "SRTSCBD331-UF", "source": "human"}],
        commit=False,
    )
    db.commit()
    code, out = _run(db, "--apply", "--retire-spec")
    assert code == 2
    assert "Mocha" in out or MOCHA_ID in out
    db.refresh(seat_cover_spec)
    assert seat_cover_spec.is_active is True
    assert _holding(db) == 2


def test_a_sku_grouped_under_a_printed_model_by_hand_is_unresolved_and_never_linked(db, catalogue):
    sku = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC8383X-RL")
    sku.family_code, sku.family_code_manual = "SRTWC8383", True
    db.commit()
    db.refresh(sku)
    assert sku.family_code == "SRTWC8383"
    plan = script.plan(db)
    assert ("SRTWC8383X-RL", "not_printed") in {(u["wc_code"], u["reason"]) for u in plan["unresolved"]}
    _run(db, "--apply")
    assert covers_for(db, [sku.id]) == {}


def test_a_pair_linked_the_other_way_round_is_not_linked_again(db, catalogue):
    wc, cover = catalogue["SRTWC8383-RL-UF"], catalogue["SRTSCBD331-UF"]
    db.add(
        ProductCompatibility(
            company_id=wc.company_id,
            seat_cover_product_id=wc.id,
            wc_product_id=cover.id,
            source="manual",
        )
    )
    db.commit()
    _run(db, "--apply")
    assert (
        db.query(ProductCompatibility)
        .filter(ProductCompatibility.seat_cover_product_id == cover.id)
        .filter(ProductCompatibility.wc_product_id == wc.id)
        .count()
        == 0
    )


def test_dry_run_marks_an_inactive_sku(db, catalogue):
    sku = catalogue["SRTWC8383-RL-BL"]
    sku.is_active = False
    db.commit()
    _, out = _run(db)
    assert "SKU SRTWC8383-RL-BL  (inactive SKU)" in out
