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
from tests.chatbot.conftest import session_factory  # noqa: E402,F401

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


def test_load_gold_accepts_canonical_and_attributes_only(tmp_path):
    rows = pg.load_gold(_write(tmp_path, [_row("a", "x", {"canonical": None, "attributes_only": []})]))
    assert rows[0]["expect"] == {"canonical": None, "attributes_only": []}


def test_canonical_code_case_and_dash_blind():
    v = _v(products=["SRTWT8259-GM"])
    assert pg.grade_parse(v, {"canonical": "SRTWT8259-GM"})[0] is True
    assert pg.grade_parse(v, {"canonical": "srtwt8259gm"})[0] is True
    assert pg.grade_parse(v, {"canonical": "SRTWT8259-GY"})[0] is False
    assert pg.grade_parse(_v(), {"canonical": "SRTWT8259-GM"})[0] is False


def test_canonical_code_must_be_the_product_entitys_not_a_customers():
    v = {"entities": [{"hint": "customer", "canonical_code": "SRTWT8259-GM"}]}
    assert pg.grade_parse(v, {"canonical": "SRTWT8259-GM"})[0] is False


def test_canonical_null_needs_every_product_entity_null():
    assert pg.grade_parse(_v(products=[None]), {"canonical": None})[0] is True
    assert pg.grade_parse(_v(), {"canonical": None})[0] is True
    ok, why = pg.grade_parse(_v(products=[None, "SRTWT8259-GM"]), {"canonical": None})
    assert ok is False and why
    assert pg.grade_parse(_v(products=["SRTWT8259-GM"]), {"canonical": None})[0] is False
    assert pg.grade_parse(_v(products=[""]), {"canonical": None})[0] is True


def test_canonical_null_ignores_non_product_entities():
    v = {"entities": [{"hint": "customer", "canonical_code": "ZZ Demo Trading"}]}
    assert pg.grade_parse(v, {"canonical": None})[0] is True


def test_attributes_only_subset():
    assert pg.grade_parse(_v(attrs=["price"]), {"attributes_only": ["price", "stock"]})[0] is True
    assert pg.grade_parse(_v(attrs=["price", "stock"]), {"attributes_only": ["price", "stock"]})[0] is True
    assert pg.grade_parse(_v(attrs=["Price"]), {"attributes_only": ["price"]})[0] is True
    ok, why = pg.grade_parse(_v(attrs=["price", "discontinued"]), {"attributes_only": ["price"]})
    assert ok is False and why


def test_attributes_only_empty_list_means_no_attribute_at_all():
    assert pg.grade_parse(_v(attrs=["discontinued"]), {"attributes_only": []})[0] is False
    assert pg.grade_parse(_v(), {"attributes_only": []})[0] is True
    assert pg.grade_parse({}, {"attributes_only": []})[0] is True


CODE_PICKS = BACKEND / "scripts" / "chatbot_eval" / "gold" / "parser_code_picks.jsonl"


def test_code_picks_gold_file():
    rows = pg.load_gold(CODE_PICKS)
    ids = [r["id"] for r in rows]
    assert len(rows) >= 16
    assert len(ids) == len(set(ids))
    for prefix in ("cp-typo-", "cp-tie-", "cp-part-", "cp-set-", "cp-amb-", "cp-trans-"):
        assert any(i.startswith(prefix) for i in ids), prefix
    assert sum(i.startswith("cp-typo-") for i in ids) >= 5
    assert sum(i.startswith("cp-tie-") for i in ids) >= 4
    assert sum(i.startswith("cp-part-") for i in ids) >= 3
    assert sum(i.startswith("cp-set-") for i in ids) >= 2
    for r in rows:
        assert "canonical" in r["expect"] and "attributes_only" in r["expect"], r["id"]
        if r["id"].startswith(("cp-tie-", "cp-part-", "cp-amb-")):
            assert r["expect"]["canonical"] is None, r["id"]
        else:
            assert r["expect"]["canonical"], r["id"]


def test_committed_gold_file():
    rows = pg.load_gold(GOLD)
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
    pos = [r for r in rows if r["expect"].get("status") == "outstanding"]
    neg = [r for r in rows if r["expect"].get("status") != "outstanding"]
    assert len(pos) >= 12
    assert len(neg) >= 6
    for r in pos:
        assert r["expect"].get("status") == "outstanding", r["id"]
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


def test_run_live_smoke_two_rows_by_repeats(session_factory, monkeypatch):
    """run_live must reach the parser module (app.services.chatbot.head.parser), grade each emission and report."""
    from app.services import llm_provider
    from app.services.chatbot.head import parser as parser_mod

    class _Keyed:
        api_key = "sk-synthetic-not-a-real-key-WXYZ"

    def fake_chat(self, messages, **kw):
        return llm_provider.ChatResult(content="{}", prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
                                       cached_tokens=800)

    verdict = {"status": "ok", "domain_hint": "product", "intent_hint": "lookup", "requested_attributes": [],
               "entities": [{"type": "product", "value": "ABC-1"}]}

    def fake_parse(config, user_block):
        llm_provider.OpenAIProvider.chat(_Keyed(), [{"role": "user", "content": "x"}], model="gpt-5.4-mini")
        return dict(verdict)

    config = parser_mod.ParserConfig(system_prompt="x", prompt_version=1, provider="openai", model="gpt-5.4-mini",
                                     api_key="k")
    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", fake_chat)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(parser_mod, "resolve_config", lambda db, **kw: config)

    rows = [
        {"id": "pass-row", "message": "abc-1 price", "expect": {"status": "ok"}},
        {"id": "fail-row", "message": "abc-1 stock", "expect": {"status": "needs_clarification"}},
    ]
    seen: list[dict] = []
    out = pg.run_live(rows, session_factory=session_factory, version_id="1", repeats=2, max_usd=5.0,
                      require_key_last4="WXYZ", on_row=seen.append)

    assert len(out) == 4
    assert [(r["id"], r["repeat"]) for r in out] == [("pass-row", 0), ("pass-row", 1), ("fail-row", 0), ("fail-row", 1)]
    for r in out:
        for key in ("id", "repeat", "passed", "reasons", "usd", "status", "domain_hint", "intent_hint",
                    "requested_attributes", "entities"):
            assert key in r
        assert r["status"] == "ok" and r["usd"] > 0
    assert all(r["passed"] for r in out[:2]) and all(r["reasons"] == [] for r in out[:2])
    for r, case in zip(out, [rows[0], rows[0], rows[1], rows[1]]):
        assert (r["passed"], r["reasons"]) == pg.grade_parse(verdict, case["expect"])
    assert not any(r["passed"] for r in out[2:])
    assert seen == out


OTHER_COMPANY = "00000000-0000-0000-0000-000000000002"


def test_run_live_registers_the_scope_listener_and_scopes_the_candidate_block(session_factory, monkeypatch):
    """The runner scopes its session to the default company, so it must install the scope filter itself:
    in a fresh process nothing else does, and the candidate block then lists other companies' codes."""
    import uuid

    from app.models.company import Company
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from app.services import company_scope, llm_provider
    from app.services.chatbot.head import parser as parser_mod
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._pg_fixture import unique_code

    db = session_factory()
    for cid, label in ((DEFAULT_COMPANY_ID, "DEF"), (OTHER_COMPANY, "OTH")):
        if db.get(Company, cid) is None:
            db.add(Company(id=cid, name=f"ZZT {label}", code=unique_code(label)[:20]))
    db.flush()
    held = {}
    for cid, code in ((DEFAULT_COMPANY_ID, "ZZQPL7-MINE"), (OTHER_COMPANY, "ZZQPL7-THEIRS")):
        cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat",
                              company_id=cid)
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=cid)
        db.add_all([cat, uom])
        db.flush()
        db.add(Product(id=str(uuid.uuid4()), product_code=code, product_name=f"Name of {code}", category_id=cat.id,
                       base_uom_id=uom.id, list_price=1, company_id=cid, is_searchable=True))
        held[cid] = code
    db.commit()

    class _Keyed:
        api_key = "sk-synthetic-not-a-real-key-WXYZ"

    events: list[str] = []
    blocks: list[str] = []
    real_register = company_scope.register_company_scope_listeners

    def spy_register():
        events.append("register")
        return real_register()

    def fake_chat(self, messages, **kw):
        return llm_provider.ChatResult(content="{}", prompt_tokens=10, completion_tokens=1, total_tokens=11,
                                       cached_tokens=0)

    def fake_parse(config, user_block):
        events.append("parse")
        blocks.append(user_block)
        llm_provider.OpenAIProvider.chat(_Keyed(), [{"role": "user", "content": "x"}], model="gpt-5.4-mini")
        return {"status": "ok", "domain_hint": "product", "entities": [], "requested_attributes": []}

    config = parser_mod.ParserConfig(system_prompt="x", prompt_version=1, provider="openai", model="gpt-5.4-mini",
                                     api_key="k", closed_lists=True, code_lookup=True)
    monkeypatch.setattr(company_scope, "register_company_scope_listeners", spy_register)
    monkeypatch.setattr(llm_provider.OpenAIProvider, "chat", fake_chat)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(parser_mod, "resolve_config", lambda db, **kw: config)

    out = pg.run_live([{"id": "scope", "message": "zzqpl7 price", "expect": {}}], session_factory=session_factory,
                      version_id="1", repeats=1, max_usd=5.0, require_key_last4="WXYZ")

    assert out and out[0]["reasons"] == [], out
    # The red assertion: the runner itself installs the listener, before it builds the user turn.
    assert "register" in events, "run_live never calls register_company_scope_listeners"
    assert events.index("register") < events.index("parse")
    # End to end (can only fail in a process where nothing registered the listener yet).
    assert blocks and held[DEFAULT_COMPANY_ID] in blocks[0]
    assert held[OTHER_COMPANY] not in blocks[0]
