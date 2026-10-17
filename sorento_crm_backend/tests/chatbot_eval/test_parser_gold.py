"""Red tests for scripts/chatbot_eval/parser_gold.py (CHAT-CODE-LOOKUP R2-1, UAC U7.1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scripts.chatbot_eval import parser_gold as pg  # noqa: E402

GOLD = BACKEND / "scripts" / "chatbot_eval" / "gold" / "parser_when_deliver.jsonl"


def _write(tmp_path, lines):
    p = tmp_path / "g.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _row(i="a", m="hello", expect=None):
    return json.dumps({"id": i, "message": m, "expect": expect if expect is not None else {}})


def test_load_gold_reads_rows(tmp_path):
    rows = pg.load_gold(_write(tmp_path, [_row("a"), _row("b", "x", {"status": "outstanding"})]))
    assert [r["id"] for r in rows] == ["a", "b"]
    assert rows[1]["expect"] == {"status": "outstanding"}


@pytest.mark.parametrize(
    "lines",
    [
        [_row("a"), _row("a")],
        [_row("a", "   ")],
        [_row("a", "hi", {"bogus": 1})],
        ["not json"],
    ],
)
def test_load_gold_rejects_bad_input(tmp_path, lines):
    with pytest.raises(ValueError):
        pg.load_gold(_write(tmp_path, lines))


def _v(status=None, domain=None, products=(), customer=False, attrs=()):
    ents = [{"hint": "product", "canonical_code": c} for c in products]
    if customer:
        ents.append({"hint": "customer", "canonical_code": None, "text": "ZZ Demo Trading"})
    return {
        "status": status,
        "domain_hint": domain,
        "entities": ents,
        "requested_attributes": list(attrs),
    }


def test_empty_expect_passes():
    assert pg.grade_parse(_v(), {}) == (True, [])


def test_status_exact():
    assert pg.grade_parse(_v("outstanding"), {"status": "outstanding"})[0] is True
    ok, why = pg.grade_parse(_v("delivered"), {"status": "outstanding"})
    assert ok is False and why


def test_status_null_and_missing_fail_exact():
    assert pg.grade_parse(_v(None), {"status": "outstanding"})[0] is False
    assert pg.grade_parse({"domain_hint": "order"}, {"status": "outstanding"})[0] is False


def test_not_status():
    assert pg.grade_parse(_v("outstanding"), {"not_status": ["outstanding"]})[0] is False
    assert pg.grade_parse(_v("delivered"), {"not_status": ["outstanding"]})[0] is True
    assert pg.grade_parse(_v(None), {"not_status": ["outstanding"]})[0] is True
    assert pg.grade_parse({}, {"not_status": ["outstanding"]})[0] is True


def test_not_status_with_none_forbids_null():
    assert pg.grade_parse(_v(None), {"not_status": [None]})[0] is False
    assert pg.grade_parse({}, {"not_status": [None]})[0] is False
    assert pg.grade_parse(_v("outstanding"), {"not_status": [None]})[0] is True


def test_domain_and_intent_hint():
    assert pg.grade_parse(_v(domain="order"), {"domain_hint": "order"})[0] is True
    assert pg.grade_parse(_v(domain="incoming"), {"domain_hint": "order"})[0] is False
    v = _v()
    v["intent_hint"] = "FILTER"
    assert pg.grade_parse(v, {"intent_hint": "FILTER"})[0] is True
    assert pg.grade_parse(v, {"intent_hint": "LOOKUP"})[0] is False
    assert pg.grade_parse(_v(), {"intent_hint": "FILTER"})[0] is False


def test_product_match_case_and_dash_blind():
    v = _v(products=["SRTKT1636SS-GM"])
    assert pg.grade_parse(v, {"product": "SRTKT1636SS-GM"})[0] is True
    assert pg.grade_parse(v, {"product": "srtkt1636ssgm"})[0] is True
    assert pg.grade_parse(v, {"product": "SRT-KT1636-SS-GM"})[0] is True
    assert pg.grade_parse(v, {"product": "SRTKT1636SS-BK"})[0] is False
    assert pg.grade_parse(_v(), {"product": "SRTKT1636SS-GM"})[0] is False


def test_product_only_counts_product_entities():
    v = {"entities": [{"hint": "customer", "canonical_code": "SRTWB8001"}]}
    assert pg.grade_parse(v, {"product": "SRTWB8001"})[0] is False


def test_product_any_of_several_entities():
    v = _v(products=["SRTWB8001", "SRTWB8002"])
    assert pg.grade_parse(v, {"product": "SRTWB8002"})[0] is True


def test_no_attribute():
    assert pg.grade_parse(_v(attrs=["delivery"]), {"no_attribute": "delivery"})[0] is False
    assert pg.grade_parse(_v(attrs=["price"]), {"no_attribute": "delivery"})[0] is True
    assert pg.grade_parse(_v(), {"no_attribute": "delivery"})[0] is True
    assert pg.grade_parse({}, {"no_attribute": "delivery"})[0] is True


def test_customer_present_absent():
    assert pg.grade_parse(_v(customer=True), {"customer": True})[0] is True
    assert pg.grade_parse(_v(), {"customer": True})[0] is False
    assert pg.grade_parse(_v(), {"customer": False})[0] is True
    assert pg.grade_parse(_v(customer=True), {"customer": False})[0] is False


def test_all_reasons_reported():
    ok, why = pg.grade_parse(_v("x", "inventory"), {"status": "outstanding", "domain_hint": "order"})
    assert ok is False and len(why) == 2


def test_committed_gold_file():
    rows = pg.load_gold(GOLD)
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
    pos = [r for r in rows if r["expect"].get("status") == "outstanding"]
    neg = [r for r in rows if r["expect"].get("status") != "outstanding"]
    assert len(pos) >= 12
    assert len(neg) >= 6
    for r in pos:
        assert r["expect"].get("no_attribute") == "delivery", r["id"]
        assert r["expect"].get("domain_hint") == "order", r["id"]
        assert r["expect"].get("product"), r["id"]
    for r in rows:
        assert "SRTKT73SS-BL when deliver" not in r["message"]


_BASE = ["live", "--gold", str(GOLD), "--out", "o.jsonl", "--max-usd", "1",
         "--require-key-last4", "abcd", "--prompt-version-id", "1", "--repeats", "1"]


def _without(flag):
    out, skip = [], False
    for a in _BASE:
        if skip:
            skip = False
            continue
        if a == flag:
            skip = True
            continue
        out.append(a)
    return out


@pytest.mark.parametrize("flag", ["--max-usd", "--require-key-last4", "--prompt-version-id"])
def test_live_refusal_prints_refused(flag, capsys):
    rc = pg.main(_without(flag))
    cap = capsys.readouterr()
    assert rc == 2
    assert "refused" in (cap.out + cap.err)


@pytest.mark.parametrize("n", ["0", "-1"])
def test_live_repeats_must_be_positive(n, capsys):
    argv = list(_BASE)
    argv[argv.index("--repeats") + 1] = n
    rc = pg.main(argv)
    assert rc != 0


def test_summarise():
    rows = [
        {"id": "a", "repeat": 0, "passed": True, "reasons": [], "usd": 0.01},
        {"id": "a", "repeat": 1, "passed": False, "reasons": ["status"], "usd": 0.01},
        {"id": "a", "repeat": 2, "passed": True, "reasons": [], "usd": 0.01},
        {"id": "b", "repeat": 0, "passed": False, "reasons": ["x"], "usd": 0.0},
    ]
    s = pg.summarise(rows)
    assert s["a"] == {"n": 3, "passed": 2}
    assert s["b"] == {"n": 1, "passed": 0}
    assert pg.summarise([]) == {}
