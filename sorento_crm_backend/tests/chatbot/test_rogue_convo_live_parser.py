"""ROGUE-CONVO-REVIEW class F: the LIVE parser reads a description with a code inside it,
and a bare count with no code beside it (owner, 6 Oct 2026, dealer conversation).

Two prompt-only rules, graded against the real provider, each shape N times (default 5,
`ROGUE_CONVO_RUNS`), on two texts:

  * BASE: the text the addenda are applied to (`_rogue_convo_prompt.base_text`, the
    exported dev text when `ROGUE_CONVO_PROMPT_FILE` names one, else the shipped
    `SEMANTIC_PARSER_PROMPT`), expected RED on the target shapes (xfail, not strict);
  * PATCHED: the same text with both addenda (`_rogue_convo_prompt.patched_text`),
    expected GREEN on the target shapes AND on the control shapes.

The control shapes pin behaviour that already works and must not move: the turn-6
sentence the model already splits perfectly, a bare family code ("8066"), a short code
("Srts07"), a code beside a count ("Srtsc07 50 unit"), and a bare number answering an open
stock question ("50").

Each run prints one JSON line; each shape prints one summary line
`ROGUE-CONVO-EVAL shape=<id> prompt=<base|patched> pass=<k>/<n>` for the plan's table.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker; model
`ROGUE_CONVO_LIVE_MODEL`, default `gpt-5.4-mini`, the production parser model; key
`OPENAI_API_KEY`, or a placeholder where an egress proxy injects it). Same pattern as
`test_order_broaden_live_parser.py` and `test_low_stock_live_parser.py`.

All codes below are fake fixture codes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from tests.chatbot import _rogue_convo_prompt as rc

pytestmark = pytest.mark.live_external

TODAY = "Tuesday, 06 October 2026"
RUNS = int(os.environ.get("ROGUE_CONVO_RUNS", "5"))
SEED = Path(__file__).parent / "fixtures" / "prompt_blocks_seed.txt"

#: The policy blocks seed carries no Specification lines (the registry is a DB read in
#: production). Two lines in the production format (`chatbot_parser_prompt` block
#: builder) so the model has a trap key to bind "S-TRAP250MM" to.
SPEC_LINES = (
    'Specification trap_length ("Trap length"): a number in mm. Words: trap, s-trap, p-trap, trap distance.\n'
    'Specification trap_type ("Trap type"): choices S trap = s_trap (s-trap, s trap); P trap = p_trap (p-trap, p trap).\n'
)

BRANDS = [{"brand_name": "Sorento", "brand_code": "SRT"}, {"brand_name": "Mocha", "brand_code": "MCH"}]

#: What the focus carries after a photo named a product and a count (turn 8 -> turn 9).
FOCUS_AFTER_PHOTO = SimpleNamespace(
    domains=["inventory"],
    customers=[],
    products=[{"raw": "PSJLSRTSC07", "name": "PSJLSRTSC07", "canonical_code": "PSJLSRTSC07"}],
    document=[],
    status=None,
    date_window=None,
    tasks=[],
)

OPEN_STOCK_QUESTION = {
    "kind": "quantities",
    "items": [{"position": 1, "code": "SRTSC07", "qty": None}],
    "owed": [1],
}


def _system_text(label: str) -> str:
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    body = rc.base_text() if label == "base" else rc.patched_text()
    blocks = SEED.read_text(encoding="utf-8") + SPEC_LINES
    text = f"{body.rstrip()}\n\n{BLOCKS_BEGIN}\n{blocks}{BLOCKS_END}\n"
    return text.replace("{{current_date}}", TODAY)


def _config(label: str):
    from app.services.chatbot.head import parser

    return parser.ParserConfig(
        system_prompt=_system_text(label),
        prompt_version=0,
        provider="openai",
        model=os.environ.get("ROGUE_CONVO_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )


def _parse(label: str, message: str, *, previous: str = "(none)", focus: Any = None,
           open_question: dict | None = None) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    block = parser.build_user_block(
        previous_response=previous,
        latest_user_message=message,
        pending_kind=None,
        focus=focus,
        brands=BRANDS,
        open_question=open_question,
    )
    out = dict(parser.parse(_config(label), block))
    print(json.dumps({
        "prompt": label, "message": message, "domain_hint": out.get("domain_hint"),
        "intent_hint": out.get("intent_hint"), "entity_op": out.get("entity_op"),
        "entities": out.get("entities"), "demand_qty": out.get("demand_qty"),
        "requested_attributes": out.get("requested_attributes"),
        "open_question_answer": out.get("open_question_answer"),
    }, ensure_ascii=False))
    return out


def _entities(out: dict[str, Any], hint: str) -> list[dict[str, Any]]:
    return [e for e in (out.get("entities") or []) if isinstance(e, dict) and e.get("hint") == hint]


def _fold(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum()).lower()


def _eval(label: str, shape: str, run: Callable[[str], dict[str, Any]], check: Callable[[dict[str, Any]], None]) -> None:
    """Run the shape RUNS times; print the pass count; fail with the first failing verdict."""
    failures: list[str] = []
    for _ in range(RUNS):
        out = run(label)
        try:
            check(out)
        except AssertionError as exc:  # noqa: PERF203 - one verdict per run is the point
            failures.append(str(exc))
    passed = RUNS - len(failures)
    print(f"ROGUE-CONVO-EVAL shape={shape} prompt={label} pass={passed}/{RUNS}")
    assert not failures, f"{shape} on {label}: {passed}/{RUNS} passed; first failure: {failures[0]}"


BASE = pytest.param("base", marks=pytest.mark.xfail(reason="the base text has no rule for this shape", strict=False))
PATCHED = pytest.param("patched")


# --------------------------------------------------------------------------- #
# F(i) targets: a description with a code inside it
# --------------------------------------------------------------------------- #

DESCRIPTION_WITH_CODE = "SORENTO ONE PIECE WASHDOWN TOILET (S-TRAP250MM)-SRTWC8354-SH"


def _check_code_is_the_product(out: dict[str, Any], code: str) -> None:
    products = _entities(out, "product")
    assert products, f"no product entity: {out.get('entities')}"
    raws = [str(e.get("raw") or "") for e in products]
    assert any(_fold(r) == _fold(code) for r in raws), f"product raw is not the code alone: {raws}"
    assert all(" " not in r.strip() for r in raws), f"a product raw still carries words: {raws}"
    assert all("sorento" not in r.lower() for r in raws), f"a product raw still carries the brand: {raws}"
    brands = _entities(out, "brand")
    assert brands and _fold(brands[0].get("raw")) == "sorento", f"brand not its own entity: {out.get('entities')}"


@pytest.mark.parametrize("label", [BASE, PATCHED])
def test_f1_description_with_code_inside_makes_the_code_the_product(label: str) -> None:
    def check(out: dict[str, Any]) -> None:
        _check_code_is_the_product(out, "SRTWC8354-SH")
        assert _entities(out, "category") or _entities(out, "specification"), (
            f"the words became neither category nor specification: {out.get('entities')}"
        )

    _eval(label, "F1-description-with-code", lambda l: _parse(l, DESCRIPTION_WITH_CODE), check)


@pytest.mark.parametrize("label", [BASE, PATCHED])
def test_f1_price_of_a_description_with_code_inside(label: str) -> None:
    def check(out: dict[str, Any]) -> None:
        _check_code_is_the_product(out, "SRTWC8354-SH")
        assert out.get("domain_hint") == "master_products", out.get("domain_hint")
        attrs = [str(a).lower() for a in (out.get("requested_attributes") or [])]
        assert any("price" in a for a in attrs), attrs

    _eval(label, "F1-price-description-with-code", lambda l: _parse(l, f"price {DESCRIPTION_WITH_CODE}"), check)


@pytest.mark.parametrize("label", [BASE, PATCHED])
def test_f1_photo_text_brand_glued_to_code_is_two_entities(label: str) -> None:
    # The media lane's rendered text: the code line with its count, then a stray brand
    # word the vision step read (the "null" caption line is class G, not reproduced here).
    message = "SORENTO PSJLSRTSC07 x50\nSORENTO"

    def check(out: dict[str, Any]) -> None:
        _check_code_is_the_product(out, "PSJLSRTSC07")
        product = next(e for e in _entities(out, "product") if _fold(e.get("raw")) == "psjlsrtsc07")
        assert product.get("quantity") == 50, product

    _eval(label, "F1-photo-brand-glued-to-code", lambda l: _parse(l, message), check)


# --------------------------------------------------------------------------- #
# F(ii) targets: a bare count with no code beside it
# --------------------------------------------------------------------------- #

def _check_bare_count(out: dict[str, Any], qty: int) -> None:
    products = _entities(out, "product")
    assert not products, f"a count became a product: {products}"
    assert not any(_fold(e.get("raw")) in ("x", "unit", "units", "pcs") for e in out.get("entities") or []), out.get("entities")
    answer = out.get("open_question_answer") or {}
    answered = answer.get("qty_for_all") == qty or any(
        isinstance(i, dict) and i.get("qty") == qty for i in (answer.get("items") or [])
    )
    assert out.get("demand_qty") == qty or answered, (
        f"the count reached neither demand_qty nor the open answer: demand_qty={out.get('demand_qty')} answer={answer}"
    )


@pytest.mark.parametrize("label", [BASE, PATCHED])
def test_f2_x_50_unit_after_a_photo_is_a_quantity_not_a_product(label: str) -> None:
    previous = "Couldn't find PSJLSRTSC07. Please refer to your salesman."
    _eval(
        label, "F2-X-50-unit",
        lambda l: _parse(l, "X 50 unit", previous=previous, focus=FOCUS_AFTER_PHOTO),
        lambda out: _check_bare_count(out, 50),
    )


@pytest.mark.parametrize("label", [BASE, PATCHED])
def test_f2_x46_pcs_is_a_quantity_not_a_product(label: str) -> None:
    _eval(
        label, "F2-x46-pcs",
        lambda l: _parse(l, "x46 pcs", focus=FOCUS_AFTER_PHOTO),
        lambda out: _check_bare_count(out, 46),
    )


# --------------------------------------------------------------------------- #
# Controls: already good, must stay good on the PATCHED text (graded on both)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("label", ["base", "patched"])
def test_control_turn6_sentence_still_splits_brand_code_and_spec(label: str) -> None:
    message = "SORENTO S-TRAP 250MM ONE PIECE WC-SRTWC8318-RL-250"
    _eval(label, "C-turn6-sentence", lambda l: _parse(l, message),
          lambda out: _check_code_is_the_product(out, "SRTWC8318-RL-250"))


@pytest.mark.parametrize("label", ["base", "patched"])
def test_control_bare_family_code_8066_stays_a_product_token(label: str) -> None:
    def check(out: dict[str, Any]) -> None:
        products = _entities(out, "product")
        assert products and any(_fold(e.get("raw")) == "8066" for e in products), out.get("entities")
        assert out.get("domain_hint") == "inventory", out.get("domain_hint")

    _eval(label, "C-8066-stock", lambda l: _parse(l, "8066 stock ?"), check)


@pytest.mark.parametrize("label", ["base", "patched"])
def test_control_short_code_srts07_stays_a_product_token(label: str) -> None:
    def check(out: dict[str, Any]) -> None:
        products = _entities(out, "product")
        assert products and any(_fold(e.get("raw")) == "srts07" for e in products), out.get("entities")

    _eval(label, "C-Srts07", lambda l: _parse(l, "Srts07", focus=FOCUS_AFTER_PHOTO), check)


@pytest.mark.parametrize("label", ["base", "patched"])
def test_control_code_beside_a_count_keeps_quantity_on_the_entity(label: str) -> None:
    def check(out: dict[str, Any]) -> None:
        products = _entities(out, "product")
        match = [e for e in products if _fold(e.get("raw")) == "srtsc07"]
        assert match, out.get("entities")
        assert match[0].get("quantity") == 50, match[0]
        assert not out.get("demand_qty"), out.get("demand_qty")

    _eval(label, "C-Srtsc07-50-unit", lambda l: _parse(l, "Srtsc07 50 unit"), check)


@pytest.mark.parametrize("label", ["base", "patched"])
def test_control_bare_number_answers_the_open_stock_question(label: str) -> None:
    def check(out: dict[str, Any]) -> None:
        assert not _entities(out, "product"), out.get("entities")
        _check_bare_count(out, 50)

    _eval(
        label, "C-50-fill",
        lambda l: _parse(l, "50", previous="How many units of SRTSC07?", open_question=OPEN_STOCK_QUESTION),
        check,
    )
