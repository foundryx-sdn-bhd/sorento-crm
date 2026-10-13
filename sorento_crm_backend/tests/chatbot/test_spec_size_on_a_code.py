"""SPEC-PROMO-PARAMS owner hand test round 2 (13 Oct, v101): a size and a spec only RESOLVE products,
for a class ask and for a typed family code alike; the stock reply itself is the existing one.

Golden cases, the owner's four messages over made-up rows shaped like the catalog (a WC's size is
its trap outlet length, `trap_length` in mm; its trap is `trap_type`):
  1. "250mm wc got stock?"            -> the WCs holding 250, never "I need at least one filter".
  2. "srtwc286 200mm got stock?"      -> only the family's 200 variants (passed on v101).
  3. "250mm srtwc286 got stock?"      -> no variant holds 250: "No 250 mm for <family>. Sizes on
                                         file: 150 mm, 200 mm." (approved wording), never all 29.
  4. "p trap 250mm srtwc286 got stock?" -> trap type and size both filter; a variant with no
                                         P-trap on file never lists.
The parser decides what the words mean (the entities below are its output); code honours only
what it emitted. Made-up codes only (ZZTWC*).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn
from tests.chatbot.test_spec_on_category_entity import (  # noqa: F401
    STOCK_TOOL,
    _category,
    _clear_overrides,
    _route_backed_stock,
    _spec_entity,
)
from tests.chatbot import test_outstanding_lane as harness


def _seed_wcs(session_factory, more_200: int = 0) -> dict[str, Any]:
    """One WC family (`family_code`) with S-trap 150 / 200 variants, two P-trap variants (180) and
    one with no trap on file, plus a WC of another family holding 250. One stock row each.
    `more_200`: that many more 200 variants in the family, and one 200 in the other family."""
    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product, stock, warehouse
    from tests.chatbot.test_lane_require import _seed_registry
    from tests.chatbot.test_outstanding_lane import _seed_contact
    from tests.chatbot.test_promo_subject_filter import _class

    _seed_contact(session_factory, variables={})
    _class(session_factory, "Water Closet", ("wc",))
    db = session_factory()
    _seed_registry(db)
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    family = f"ZZTWC{uuid.uuid4().int % 10**6:06d}"
    other = f"ZZTWC{uuid.uuid4().int % 10**6:06d}X"
    rows = {
        f"{family}-SH-150": (family, {"trap_type": "s_trap", "trap_length": 150}),
        f"{family}-SH-200": (family, {"trap_type": "s_trap", "trap_length": 200}),
        f"{family}-SH-NEW-200": (family, {"trap_type": "s_trap", "trap_length": 200}),
        f"{family}-P": (family, {"trap_type": "p_trap", "trap_length": 180}),
        f"{family}-NEW-P": (family, {"trap_type": "p_trap", "trap_length": 180}),
        f"{family}-UF": (family, {}),
        f"{other}-SH-250": (other, {"trap_type": "s_trap", "trap_length": 250}),
    }
    for i in range(more_200):
        rows[f"{family}-SH-200-{i}"] = (family, {"trap_type": "s_trap", "trap_length": 200})
    if more_200:
        rows[f"{other}-SH-200"] = (other, {"trap_type": "s_trap", "trap_length": 200})
    for code, (fam, specs) in rows.items():
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        db.query(Product).filter(Product.id == p.id).update({"family_code": fam})
        values: dict[str, Any] = {"class": {"value": "Water Closet"}}
        for key, value in specs.items():
            values[key] = {"value": value, "unit": "mm"} if isinstance(value, int) else {"value": value}
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values=values))
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    db.commit()
    db.close()
    return {"family": family, "other": other, "codes": list(rows)}


def _code(raw: str) -> dict[str, Any]:
    return {
        "raw": raw.lower(), "hint": "product", "canonical_code": raw, "current_message": True,
        "confident": True, "hint_confident": True, "quantity": None,
    }


def _ask(session_factory, monkeypatch, entities, text) -> tuple[str, list]:
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    qf = _parser_output(domain_hint="inventory", intent_hint="check_stock", entities=entities)
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=text,
        msg_id=f"zzt-soc2-{uuid.uuid4().hex[:6]}", real_resolver=True,
    )
    return (result.reply or {}).get("text") or "", captured


def _listed(reply: str, codes: list[str]) -> set[str]:
    """The codes the reply lists, by whole code (a code is a prefix of its longer siblings)."""
    import re

    return {c for c in codes if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", reply)}


def test_1_a_size_and_a_class_word_list_the_class_holding_the_size(session_factory, monkeypatch, _clear_overrides):
    """RED (v101): "That would search every stock we have - I need at least one filter".
    WC and 250 mm ARE the filters: the WCs holding 250 (on the trap outlet length here)."""
    w = _seed_wcs(session_factory)
    reply, captured = _ask(
        session_factory, monkeypatch, [_spec_entity("250mm", "size", 250), _category("wc", "wc")], "250mm wc got stock?",
    )
    assert "at least one filter" not in reply, reply
    assert _listed(reply, w["codes"]) == {f"{w['other']}-SH-250"}, reply


def test_2_a_family_code_and_a_size_keep_the_variants_holding_it(session_factory, monkeypatch, _clear_overrides):
    """Passed on v101; must stay: "srtwc286 200mm got stock?" lists the 200 variants only."""
    w = _seed_wcs(session_factory)
    f = w["family"]
    reply, _ = _ask(
        session_factory, monkeypatch, [_code(f), _spec_entity("200mm", "size", 200)], f"{f.lower()} 200mm got stock?",
    )
    assert _listed(reply, w["codes"]) == {f"{f}-SH-200", f"{f}-SH-NEW-200"}, reply


def test_3_a_family_code_and_a_size_no_variant_holds_says_the_sizes_on_file(session_factory, monkeypatch, _clear_overrides):
    """RED (v101): every variant listed, the size ignored. Approved wording: "No 250 mm for
    SRTWC286. Sizes on file: 150 mm, 200 mm." (the family's sizes, never another family's)."""
    w = _seed_wcs(session_factory)
    f = w["family"]
    reply, captured = _ask(
        session_factory, monkeypatch, [_spec_entity("250mm", "size", 250), _code(f)], f"250mm {f.lower()} got stock?",
    )
    assert _listed(reply, w["codes"]) == set(), reply
    assert f"No 250 mm for {f}. Sizes on file: 150 mm, 180 mm, 200 mm." in reply, reply
    calls = [a for n, a in captured if n == STOCK_TOOL]
    assert calls and {"key": "size", "op": "eq", "value": 250} in (calls[-1].get("spec_constraints") or []), calls


def test_4_a_family_code_a_trap_and_a_size_filter_on_both(session_factory, monkeypatch, _clear_overrides):
    """RED (v101): -NEW-P, -P and -UF listed, the size ignored and UF (no P-trap on file) in. The
    trap type and the size both filter; no P-trap variant holds 250, so the P-trap sizes are said."""
    w = _seed_wcs(session_factory)
    f = w["family"]
    reply, _ = _ask(
        session_factory, monkeypatch,
        [_spec_entity("p trap", "trap_type", "p_trap"), _spec_entity("250mm", "size", 250), _code(f)],
        f"p trap 250mm {f.lower()} got stock?",
    )
    assert _listed(reply, w["codes"]) == set(), reply
    assert f"No 250 mm for {f}. Sizes on file: 180 mm." in reply, reply


def test_4b_a_family_code_a_trap_and_a_size_it_holds_list_only_those(session_factory, monkeypatch, _clear_overrides):
    """The same rule when the size is on file: the P-trap 180 variants only, never -UF."""
    w = _seed_wcs(session_factory)
    f = w["family"]
    reply, _ = _ask(
        session_factory, monkeypatch,
        [_spec_entity("p trap", "trap_type", "p_trap"), _spec_entity("180mm", "size", 180), _code(f)],
        f"p trap 180mm {f.lower()} got stock?",
    )
    assert _listed(reply, w["codes"]) == {f"{f}-P", f"{f}-NEW-P"}, reply


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("ms", "Tiada 250 mm untuk {f}. Saiz yang ada: 150 mm, 180 mm, 200 mm."),
        ("zh", "{f} 没有 250 mm。现有尺寸：150 mm, 180 mm, 200 mm。"),
    ],
)
def test_3_in_malay_and_chinese(session_factory, monkeypatch, _clear_overrides, lang, expected):
    """The miss sentence for a family code, as the reply localiser renders it in the customer's
    language (the turn's English reply is test 3)."""
    w = _seed_wcs(session_factory)
    f = w["family"]
    from app.services.chatbot.label_catalog import Localizer, defaults

    english = f"No 250 mm for {f}. Sizes on file: 150 mm, 180 mm, 200 mm."
    assert Localizer(lang, defaults(lang)).reply(english) == expected.format(f=f)


def test_1_ms_and_zh_shapes_list_the_same_wcs(session_factory, monkeypatch, _clear_overrides):
    """"ada wc 250mm?" / "250mm 马桶有货吗？": the parser's class word in the customer's language with
    its English canonical, the size its own entity. The same WCs as case 1."""
    w = _seed_wcs(session_factory)
    for raw, text in (("wc", "ada wc 250mm?"), ("马桶", "250mm 马桶有货吗？")):
        reply, _ = _ask(
            session_factory, monkeypatch, [_category(raw, "wc"), _spec_entity("250mm", "size", 250)], text,
        )
        assert "at least one filter" not in reply, (text, reply)
        assert _listed(reply, w["codes"]) == {f"{w['other']}-SH-250"}, (text, reply)


def test_2b_page_two_of_a_family_and_a_size_keeps_the_family(session_factory, monkeypatch, _clear_overrides):
    """Reviewer S2: page 1 carries the code's ids (`spec_keeps_ids`); "more" names no code, so page
    2 must take them from the carried arguments: the rest of the family's 200s, never the other
    family's 200."""
    w = _seed_wcs(session_factory, more_200=5)
    f = w["family"]
    first, captured = _ask(
        session_factory, monkeypatch, [_code(f), _spec_entity("200mm", "size", 200)], f"{f.lower()} 200mm got stock?",
    )
    first_listed = _listed(first, w["codes"])
    assert len(first_listed) == 5 and "Reply to see more" in first, first
    captured.clear()
    more = _parser_output(domain_hint="inventory", intent_hint="check_stock", entities=[], continuation=True)
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=more, text_body="more",
        msg_id=f"zzt-soc2-{uuid.uuid4().hex[:6]}", real_resolver=True,
    )
    second = (result.reply or {}).get("text") or ""
    calls = [a for n, a in captured if n == STOCK_TOOL]
    assert calls and calls[-1].get("page") == 2 and calls[-1].get("product_ids"), calls
    second_listed = _listed(second, w["codes"])
    family_200 = {c for c in w["codes"] if c.startswith(f"{f}-SH") and ("-200" in c)}
    assert first_listed | second_listed == family_200 and not first_listed & second_listed, (first, second)
    assert f"{w['other']}-SH-200" not in second_listed, second
