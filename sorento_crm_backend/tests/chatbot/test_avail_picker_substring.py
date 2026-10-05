"""AVAIL-PICKER (owner live trace, 5 Oct 2026): a code-like token that is a SUBSTRING
of product codes names that family on an availability turn.

"wc286 check stock" dead-ended with `Couldn't find: "wc286"` / "But no inventory
matched these." although the resolver had placed the SRTWC286-SH family off its own
substring probe on product_code: `engine._exact_codes_only` kept only an equal or
prefix code. The three seams that read the typed token against the resolved codes now
share one order, exact, then prefix, then substring. The console journeys are
S55-S55e in `test_avail_mode_scenarios.py`; these pin the seams one by one.

Plan: documentation/plans/chatbot/PLAN-avail-picker-substring-05oct.md.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.chatbot import turn_runtime
from app.services.chatbot.engine import _exact_codes_only
from app.services.chatbot.turn import task as turn_task

FAMILY = ["SRTWC286-SH", "SRTWC286-SH-150", "SRTWC286-SH-200"]


def _candidate(code: str) -> dict:
    return {"uuid": f"uuid-{code}", "raw": code, "canonical_code": code, "code": code, "entity_type": "product"}


def _parse(*tokens: str) -> dict:
    return {"entities": [{"raw": t, "hint": "product", "canonical_code": t} for t in tokens]}


def _run(tokens: tuple[str, ...], codes: list[str]):
    """The four resolver answers after the filter. None from the filter means it kept
    every candidate and unplaced nothing, so the inputs stand."""
    candidates = [_candidate(c) for c in codes]
    kinds = {t.casefold(): {"product": len(codes)} for t in tokens}
    out = _exact_codes_only(_parse(*tokens), kinds, {"product": candidates}, list(candidates), {})
    return out if out is not None else (kinds, {"product": candidates}, list(candidates), {})


# ---------------------------------------------------------------- engine._exact_codes_only


def test_a_substring_token_keeps_the_whole_family_it_sits_inside():
    kinds, candidates, compatible, unplaced = _run(("wc286",), FAMILY)
    assert [c["code"] for c in candidates["product"]] == FAMILY
    assert [e["code"] for e in compatible] == FAMILY
    assert unplaced == {}
    assert kinds["wc286"]["product"] == 3


def test_a_prefix_token_still_wins_over_a_wider_substring_reading():
    """Prefix first: "srtwc286-sh-1" is the prefix of one code and a substring of none
    other, so the family is that code alone, as before."""
    _kinds, candidates, _compatible, unplaced = _run(("srtwc286-sh-1",), FAMILY)
    assert [c["code"] for c in candidates["product"]] == ["SRTWC286-SH-150"]
    assert unplaced == {}


def test_an_exact_code_never_brings_its_family_substring_or_not():
    _kinds, candidates, _compatible, unplaced = _run(("SRTWC286-SH",), FAMILY)
    assert [c["code"] for c in candidates["product"]] == ["SRTWC286-SH"]
    assert unplaced == {}


def test_a_code_like_token_inside_no_code_is_still_a_miss():
    _kinds, candidates, compatible, unplaced = _run(("foo99",), FAMILY)
    assert candidates["product"] == []
    assert compatible == []
    assert unplaced == {"foo99": "foo99"}


def test_a_bare_number_is_not_a_product_even_when_the_digits_sit_inside_a_code():
    """"286" is inside every SRTWC286 code and is still not code-like, so it is ignored,
    never a family (owner, 3 Oct 2026: never dump the catalogue for a number)."""
    _kinds, candidates, compatible, unplaced = _run(("286",), FAMILY)
    assert candidates["product"] == []
    assert compatible == []
    assert unplaced == {}


def test_the_substring_family_of_one_token_leaves_another_tokens_exact_code_alone():
    _kinds, candidates, _compatible, unplaced = _run(("SRT5674", "wc286"), ["SRT5674", *FAMILY])
    assert [c["code"] for c in candidates["product"]] == ["SRT5674", *FAMILY]
    assert unplaced == {}


# ---------------------------------------------------------------- turn/task.py::_group


def _rows(*codes: str) -> list[dict]:
    return [{"product_id": f"uuid-{c}", "product_code": c, "needs_quantity": True} for c in codes]


def test_group_reads_a_substring_family_when_no_code_starts_with_the_token():
    rows = _rows("SRT5674", *FAMILY)
    assert [r["product_code"] for r in turn_task._group(rows, "wc286")] == FAMILY


def test_group_keeps_the_prefix_reading_first():
    rows = _rows(*FAMILY)
    assert [r["product_code"] for r in turn_task._group(rows, "srtwc286-sh-2")] == ["SRTWC286-SH-200"]


# ---------------------------------------------------------------- turn_runtime quantity carry


def _resolved(*codes: str) -> list[dict]:
    return [{"uuid": f"uuid-{c}", "code": c, "canonical_code": c} for c in codes]


def _quantities(token: str, qty: int, resolved: list[dict]) -> dict:
    out = {"entities": [{"raw": token, "canonical_code": token, "hint": "product", "quantity": qty}]}
    spec = SimpleNamespace(filters={})
    return turn_runtime._spec_quantities(out, spec, resolved).get("requested_quantities") or {}


def test_a_substring_of_exactly_one_resolved_code_carries_its_quantity():
    assert _quantities("rt5674", 3, _resolved("SRT5674")) == {"uuid-SRT5674": 3}


def test_a_substring_of_several_resolved_codes_is_a_family_and_carries_none():
    assert _quantities("wc286", 5, _resolved(*FAMILY)) == {}


def test_a_prefix_of_exactly_one_code_still_carries_as_before():
    assert _quantities("srtwc287-s", 3, _resolved("SRTWC287-S-150")) == {"uuid-SRTWC287-S-150": 3}
