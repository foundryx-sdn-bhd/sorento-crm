"""REPORT-ENGINE hand-test finding (owner, 4 Oct 2026): a brand name shared across companies is ONE brand.

On dev the `brands` table holds a SORENTO row per company (unique key `(company_id, brand_name)`),
so "top salesman for sorento water closet this year" answered `'sorento' matches several brands:
SORENTO.` The rule: a word is ambiguous only when its hits carry DIFFERENT brand names (compared
case-insensitively and trimmed); the same name across companies is one brand, the union of its ids.
The route then restricts to the contact's own companies.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text as _sql

from app.models.product import Brand
from tests._mc_lookup_seed import seed_mocha
from tests.chatbot.test_report_ask_lane import (  # noqa: F401 - fixtures + helpers
    THIS_YEAR,
    _e,
    _rank,
    _seed_more,
    console,
)
from tests.chatbot.test_engine import CONTACT_ID


def _brand_in_company(console, *, name: str, code: str, company_id: str) -> str:
    db = console.session_factory()
    try:
        row = Brand(id=str(uuid.uuid4()), brand_code=code, brand_name=name, company_id=company_id)
        db.add(row)
        db.commit()
        return str(row.id)
    finally:
        db.close()


def _grant_company(console, company_id: str) -> None:
    """The harness contact may see `company_id` too (a staff contact of both companies, as on dev)."""
    db = console.session_factory()
    try:
        db.execute(
            _sql(
                "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                "SELECT gen_random_uuid(), id, :company FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"company": company_id, "cid": str(CONTACT_ID)},
        )
        db.commit()
    finally:
        db.close()


def _two_companies_one_name(console, name: str = "ZZT SORENTO") -> list[str]:
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = console.session_factory()
    try:
        mocha = seed_mocha(db)
        db.commit()
        mocha_id = str(mocha.id)
    finally:
        db.close()
    _grant_company(console, mocha_id)
    return [
        _brand_in_company(console, name=name, code="ZZTSA1", company_id=DEFAULT_COMPANY_ID),
        _brand_in_company(console, name=name, code="ZZTSA2", company_id=mocha_id),
    ]


def test_the_same_brand_name_in_two_companies_is_one_brand_with_both_ids(console) -> None:
    ids = _two_companies_one_name(console)
    text, calls = console.say(_rank(_e("zzt sorento", "brand"), **THIS_YEAR), "top 3 salesman for zzt sorento this year")
    assert "matches several" not in text, text
    (args,) = calls
    assert sorted(args.get("brand_ids") or []) == sorted(ids), (args.get("brand_ids"), ids)


def test_the_comparison_ignores_case_and_surrounding_spaces(console) -> None:
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = console.session_factory()
    try:
        mocha = seed_mocha(db)
        db.commit()
        mocha_id = str(mocha.id)
    finally:
        db.close()
    _grant_company(console, mocha_id)
    ids = [
        _brand_in_company(console, name="ZZT Sorento", code="ZZTSA1", company_id=DEFAULT_COMPANY_ID),
        _brand_in_company(console, name="zzt sorento ", code="ZZTSA2", company_id=mocha_id),
    ]
    text, calls = console.say(_rank(_e("zzt sorento", "brand"), **THIS_YEAR), "top 3 salesman for zzt sorento this year")
    assert "matches several" not in text, text
    (args,) = calls
    assert sorted(args.get("brand_ids") or []) == sorted(ids), (args.get("brand_ids"), ids)


def test_different_brand_names_for_one_word_still_say_several_and_list_both(console) -> None:
    _seed_more(console)
    text, calls = console.say(_rank(_e("ZZTALPHA ZZTBETA", "brand")), "top 3 salesman for ZZTALPHA ZZTBETA")
    assert calls == []
    assert text.strip() == "'ZZTALPHA ZZTBETA' matches several brands: ZZTALPHA, ZZTBETA. Ask again naming one.", text


def test_a_shared_name_next_to_a_different_name_is_still_several_and_lists_each_name_once(console) -> None:
    from app.services.company_scope import DEFAULT_COMPANY_ID

    _seed_more(console)  # ZZTALPHA and ZZTBETA in the default company
    db = console.session_factory()
    try:
        mocha = seed_mocha(db)
        db.commit()
        mocha_id = str(mocha.id)
    finally:
        db.close()
    _grant_company(console, mocha_id)
    _brand_in_company(console, name="ZZTALPHA", code="ZZTAL2", company_id=mocha_id)
    text, calls = console.say(_rank(_e("ZZTALPHA ZZTBETA", "brand")), "top 3 salesman for ZZTALPHA ZZTBETA")
    assert calls == []
    assert text.strip() == "'ZZTALPHA ZZTBETA' matches several brands: ZZTALPHA, ZZTBETA. Ask again naming one.", text
