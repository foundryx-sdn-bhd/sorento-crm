"""CHAT-CODE-LOOKUP slice A, U1.1: under the gate a code word with no exact hit lists its
near spellings from similarity 0.30 up (today's floor is 0.5). Code only shortlists.
Synthetic codes shaped like the TRAIN typos (a letter swap, a swapped prefix); each fixture
asserts its own similarity band so it cannot drift."""
from __future__ import annotations

import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.chatbot.head import candidates
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from tests._pg_fixture import blank_session, unique_code

OTHER_COMPANY = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.company import Company

        session.add(Company(id=OTHER_COMPANY, name="Other", code=unique_code("OTH")[:20]))
        session.flush()
        yield session


def _products(db, *codes: str, company_id: str = DEFAULT_COMPANY_ID, searchable: bool = True) -> None:
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat", company_id=company_id,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=company_id)
    db.add_all([cat, uom])
    db.flush()
    for code in codes:
        db.add(Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name of {code}", category_id=cat.id,
            base_uom_id=uom.id, list_price=1, company_id=company_id, is_searchable=searchable,
        ))
    db.flush()


def _sim(typed: str, code: str) -> float:
    return candidates.similarity(candidates.trigrams(typed), candidates.trigrams(code))


def _band(typed: str, *codes: str) -> None:
    for code in codes:
        assert 0.30 <= _sim(typed, code) < candidates.NEAR_CODE, (typed, code, _sim(typed, code))


def _lines(db, message: str, **kw) -> list[str]:
    return candidates.candidate_lines(candidates.shortlist(db, message, **kw))


def _entry_line(lines: list[str], word: str) -> str | None:
    prefix = f'- "{word}": '
    return next((line for line in lines if line.startswith(prefix)), None)


def test_a_letter_swap_lists_the_meant_code_and_its_decoy_as_near_lines(db):
    _band("zzcm66631", "ZZMC66631", "ZZFAL66631")
    _products(db, "ZZMC66631", "ZZFAL66631")

    line = _entry_line(_lines(db, "Check stock ZZCM66631", code_lookup=True), "zzcm66631")

    assert line is not None
    assert "near code ZZMC66631 (Name of ZZMC66631)" in line
    assert "near code ZZFAL66631 (Name of ZZFAL66631)" in line
    assert line.index("ZZMC66631") < line.index("ZZFAL66631")  # 0.43 before 0.40


def test_a_swapped_prefix_lists_the_meant_code_even_when_a_decoy_scores_higher(db):
    _band("zhs2072", "ZSH2072", "ZHS2039")
    assert _sim("zhs2072", "ZHS2039") > _sim("zhs2072", "ZSH2072")
    _products(db, "ZSH2072", "ZHS2039")

    line = _entry_line(_lines(db, "stock zhs2072 x 72 units", code_lookup=True), "zhs2072")

    assert line is not None and "near code ZSH2072" in line and "near code ZHS2039" in line
    assert line.index("ZHS2039") < line.index("ZSH2072")  # best similarity first; code never picks


def test_gate_off_lists_neither_as_today(db):
    _products(db, "ZZMC66631", "ZZFAL66631")

    assert _lines(db, "Check stock ZZCM66631") == []


def test_a_word_with_an_exact_code_gains_no_near_lines_below_the_floor(db):
    _band("zzex5001", "ZZXE5001")
    _products(db, "ZZEX5001", "ZZXE5001")

    on = _lines(db, "stock zzex5001", code_lookup=True)

    assert on == _lines(db, "stock zzex5001")
    assert "ZZXE5001" not in "\n".join(on)


def test_an_exact_code_ignores_case_spaces_and_dashes(db):
    _band("zzex5001", "ZZXE5001")
    _products(db, "ZZ-EX5001", "ZZXE5001")

    on = _lines(db, "stock zzex5001", code_lookup=True)

    assert on == _lines(db, "stock zzex5001")
    assert "ZZXE5001" not in "\n".join(on)


def test_at_most_five_near_lines_best_first(db):
    near_best = [f"ZZN71{n}9" for n in range(1, 6)]  # 0.455 each
    near_low = ["ZZN7208", "ZZN7308", "ZZN7408"]  # 0.333 each
    _band("zzn7109", *near_best, *near_low)
    _products(db, *near_best, *near_low)

    line = _entry_line(_lines(db, "stock zzn7109", code_lookup=True), "zzn7109")

    assert line is not None and line.count("near code ") == candidates.CAP_PER_WORD == 5
    assert all(code in line for code in near_best)
    assert not any(code in line for code in near_low)


def test_a_near_code_of_another_company_or_not_chat_searchable_is_not_listed(db):
    _band("zzcm66631", "ZZMC66631", "ZZFAL66631")
    _products(db, "ZZMC66631", company_id=OTHER_COMPANY)
    _products(db, "ZZFAL66631", searchable=False)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    assert _lines(db, "stock zzcm66631", code_lookup=True) == []


def test_listed_codes_are_the_folded_codes_of_the_rendered_lines_only(db):
    _band("zzcm66631", "ZZMC66631", "ZZFAL66631")
    _products(db, "ZZMC66631", "ZZ-FAL66631")

    entries = candidates.shortlist(db, "stock zzcm66631", code_lookup=True)

    assert candidates.listed_codes(entries) == frozenset({"zzmc66631", "zzfal66631"})
    assert candidates.listed_codes([]) == frozenset()


def test_listed_codes_leaves_out_lines_cut_by_the_caps(db):
    codes = [f"ZZN71{n}9" for n in range(1, 6)] + ["ZZN7208"]
    _products(db, *codes)

    listed = candidates.listed_codes(candidates.shortlist(db, "stock zzn7109", code_lookup=True))

    assert len(listed) == 5 and "zzn7208" not in listed
