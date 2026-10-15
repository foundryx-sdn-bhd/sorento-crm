"""MIRROR-LINKS: the one-off script that links the mirror / shelf flyer (owner decision 14 Oct).

    venv/bin/python scripts/oneoff/link_mirror_shelf_flyer.py            # dry run
    venv/bin/python scripts/oneoff/link_mirror_shelf_flyer.py --apply    # write, source flyer

Plan: documentation/plans/products/PLAN-mirror-shelf-links-14oct.md (section 5, Loader).
The blank schema has no business products, so `plan()` only sees what each test seeds.
"""
from __future__ import annotations

import io

import pytest

from app.models.base import company_scope, set_company_scope
from app.models.product import Product
from app.models.product_compatibility import ProductCompatibility
from app.services.company_scope import DEFAULT_COMPANY_ID
from scripts.oneoff import link_mirror_shelf_flyer as script

from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session

NOT_ON_DEV = {"SRTMR11417", "SRTSV11417"}


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _seed_rows():
    """A few flyer rows: every row of SRTMR11404-BL, the SRTSV11404-BL row(s), and the rows of
    the two codes that are not on dev (their cabinet exists, the accessory does not)."""
    return [
        r
        for r in script.FLYER_ROWS
        if r[1] in ("SRTMR11404-BL", "SRTSV11404-BL") or r[1] in NOT_ON_DEV
    ]


@pytest.fixture()
def seeded(db):
    rows = _seed_rows()
    codes = {c for _, a, h in rows for c in (a, h) if c not in NOT_ON_DEV}
    made = {code: product(db, company_id=DEFAULT_COMPANY_ID, code=code) for code in sorted(codes)}
    db.commit()
    return rows, made


def _run(db, *argv) -> tuple[int, str]:
    out = io.StringIO()
    code = script.main(list(argv), db=db, out=out)
    return code, out.getvalue()


def _written(db) -> set[tuple[str, str, str, str]]:
    """(kind, accessory code, host code, source) of every link."""
    with company_scope(db, None):
        links = db.query(ProductCompatibility).all()
        codes = {p.id: p.product_code for p in db.query(Product).all()}
    return {
        (l.kind, codes[l.seat_cover_product_id], codes[l.wc_product_id], l.source) for l in links
    }


def _expected(rows):
    return {
        (kind, acc, host, "flyer")
        for kind, acc, host in rows
        if acc not in NOT_ON_DEV and host not in NOT_ON_DEV
    }


# --------------------------------------------------------------------------- data


def test_flyer_data_is_42_mirror_and_shelf_rows():
    assert len(script.FLYER_ROWS) == 42
    assert {kind for kind, _, _ in script.FLYER_ROWS} == {"mirror", "shelf"}
    assert ("mirror", "SRTMR11404-BL", "SRTBF11404") in script.FLYER_ROWS
    assert ("shelf", "SRTSV11404-BL", "SRTBF11404") in script.FLYER_ROWS
    # UAC 6: SRTMR11404-BL fits seven cabinets.
    assert {h for k, a, h in script.FLYER_ROWS if a == "SRTMR11404-BL"} == {
        "SRTBF11404",
        "SRTBF11405",
        "SRTBF11412",
        "SRTBF11413-MBL",
        "SRTBF11501",
        "SRTBF11705",
        "SRTBF11707",
    }
    assert NOT_ON_DEV <= {a for _, a, _ in script.FLYER_ROWS}


# --------------------------------------------------------------------------- plan


def test_plan_links_found_rows_and_reports_missing(db, seeded):
    rows, _ = seeded
    plan = script.plan(db)
    links = {(l["kind"], l["accessory_code"], l["host_code"]) for l in plan["links"]}
    assert links == {(k, a, h) for k, a, h, _ in _expected(rows)}
    assert all(l["already"] is False for l in plan["links"])
    missing_codes = {c for m in plan["missing"] for c in m["not_found"]}
    assert NOT_ON_DEV <= missing_codes


# --------------------------------------------------------------------------- dry run


def test_dry_run_prints_links_and_missing_and_writes_nothing(db, seeded):
    rows, _ = seeded
    code, out = _run(db)
    assert code == 0
    assert "LINK  mirror  SRTMR11404-BL -> SRTBF11404" in out
    assert "LINK  shelf  SRTSV11404-BL -> SRTBF11404" in out
    missing_lines = [l for l in out.splitlines() if l.startswith("MISSING")]
    assert any("SRTMR11417" in l and "not found" in l for l in missing_lines)
    n = len(_expected(rows))
    assert f"DRY RUN: {n} links to write. Run with --apply to write them." in out
    assert _written(db) == set()


# --------------------------------------------------------------------------- apply


def test_apply_writes_flyer_links_with_kind_and_is_idempotent(db, seeded):
    rows, _ = seeded
    code, out = _run(db, "--apply")
    assert code == 0, out
    assert _written(db) == _expected(rows)
    n = len(_expected(rows))
    assert f"{n} links written, 0 already linked." in out

    code, again = _run(db, "--apply")
    assert code == 0
    assert f"0 links written, {n} already linked." in again
    assert "(already linked)" in again
    assert _written(db) == _expected(rows)
    # And a dry run now has nothing left to write.
    _, dry = _run(db)
    assert "DRY RUN: 0 links to write." in dry


def test_reversed_same_kind_link_counts_as_already_and_is_not_written(db, seeded):
    rows, made = seeded
    kind, acc, host = next(r for r in rows if r[1] == "SRTMR11404-BL" and r[2] == "SRTBF11404")
    db.add(
        ProductCompatibility(
            company_id=DEFAULT_COMPANY_ID,
            seat_cover_product_id=made[host].id,
            wc_product_id=made[acc].id,
            kind=kind,
            source="manual",
        )
    )
    db.commit()
    plan = script.plan(db)
    [link] = [l for l in plan["links"] if (l["accessory_code"], l["host_code"]) == (acc, host)]
    assert link["already"] is True
    code, out = _run(db, "--apply")
    assert code == 0, out
    with company_scope(db, None):
        pair = [
            l
            for l in db.query(ProductCompatibility).all()
            if {l.seat_cover_product_id, l.wc_product_id} == {made[acc].id, made[host].id}
            and l.kind == kind
        ]
    assert len(pair) == 1 and pair[0].source == "manual"
