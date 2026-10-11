"""Phase 2 red tests for PROMPT-ABLATION U1-U9.

UAC: documentation/plans/chatbot/prompt-ablation-acceptance-criteria.md
Pure pytest: no DB, no network. The build module is imported lazily so U1 and the
file-only checks run independently of the (not yet written) implementation.
"""

import hashlib
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
V50_PATH = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.v50.txt"
OUT_PATH = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.ablation-20261011.txt"


def _v50() -> str:
    return V50_PATH.read_text(encoding="utf-8")


def _out() -> str:
    return OUT_PATH.read_text(encoding="utf-8")


def _mod():
    from scripts import build_parser_ablation

    return build_parser_ablation


def _cut_ids():
    try:
        return [c[0] for c in _mod().CUTS]
    except Exception:  # module missing: the parametrized tests below fail on the import
        return ["__module_missing__"]


# U5 headers
KEPT_HEADERS = [
    '== PRICE TERMINOLOGY (Sorento-specific, decisive) ==',
    '== INTENT & DOMAIN ==',
    '== DOMAIN IN MESSAGE ==',
    '== DECISIVE DOMAIN TERMS ==',
    '== BARE ENTITY CONTINUATION ==',
    '== REQUESTED ATTRIBUTES ==',
    '== MESSAGE TYPE ==',
    '== ACCESS LEVELS ==',
    '== ENTITY OPERATIONS ==',
    '== ENTITY CONFIDENCE ==',
    '== BROADEN AXIS ==',
    '== ROUTING ==',
    '== DATE FILTER (per-message, never carried over) ==',
    '== COMPANY-NAME REPLY ON AN ESCALATION OFFER ==',
    '== ORDER_STATUS FILTER ==',
    '== DOCUMENT ==',
    '== ASKS (a message that names more than one domain) ==',
    '== OUTPUT (exactly these keys, no others, no comments) ==',
    '== IN AN ORDER OR OUTSTANDING ASK, A SHORT TOKEN IS A LOCATION ==',
    '== SALES_CHANNEL: a NEW nullable output key, "dealer" | "project" | null ==',
    '== THE OPEN QUESTION AND open_question_answer ==',
    '== THE specification ENTITY ==',
    '== KNOWN BRANDS: entities[].hint "brand" ==',
    '== SELF_REFERENCE: the asker means their OWN account ==',
    '== ACCOUNT: which numbered account of a customer the message names ==',
    '== SALES RANKING: order_status "sales_ranking", domain_hint "order" ==',
    '== A WORD BESIDE A CODE IS STILL ONE PRODUCT ENTITY ==',
    '== PROMOTION SUBJECT ==',
    '== MEMORY ==',
    'CURRENT DATE: {{current_date}}',
]

# U6 business lines
KEPT_LINES = [
    'Company codes: Sorento = SRT, Mocha = MCH, Cabana = CBN',
    '"cost" alone is the everyday word for',
    'A dealer IS our customer',
    'only a 4-letter + 7-digit',
    '"202602-S0002"',
    '"Sorento Dealer","Mocha Dealer","Mocha Office","Cabana Dealer","Cabana Office","End User","Sorento Office"',
    'team "customer_service",   agent "order_enquiries"',
    'TRAP SPECS, the most common dealer ask (owner, 6 Oct 2026)',
    '"po_refs": ["..."] or null',
    '"ranking_drill"',
    '"low_stock": {"group_by"',
]

# U7 language teaching gone
GONE_STRINGS = [
    '"eskalasi"',
    '"升级"',
    '"satu", "dua", "lapan"',
    '"yang pertama"',
    '"第一个"',
    '"yi hao"',
    '"ikut pelanggan"',
    '"belum hantar"',
    '"terakhir masuk"',
    '"上次进货"',
    '"laporan jualan"',
    '"jurujual fanny"',
    '"saya punya"',
    '"我的"',
    '"akaun 1"',
    '"户口1"',
    '"paip air"',
    '"tandas"',
    'READ FOR MEANING, NOT SPELLING',
    '"oustangind',
    '"worsr"',
    '"salea"',
    'Encik/En',
    '"lah",',
    '"tomorrow" (= CURRENT DATE + 1 day)',
    '"apa saya tanya tadi"',
    '"传单"',
    '== A DELIVERY WORD PLUS A NAME IS AN ORDER ASK ==',
    '== THE ATTRIBUTE PHRASE, WHOLE ==',
    'before calling the MCP tool',
    '马桶 -> canonical_code "water closet"',
    '面盆 -> canonical_code "wash basin"',
    '小便斗 -> canonical_code "urinal"',
]


def test_u1_v50_unchanged():
    assert hashlib.md5(V50_PATH.read_bytes()).hexdigest() == "4a79359ae71f7d4f1d45fc7dd4153d72"


def test_u2_committed_output_equals_fresh_build():
    m = _mod()
    assert _out() == m.build(_v50())


def test_u3_constants_shape():
    m = _mod()
    assert m.SOURCE == V50_PATH
    assert m.OUTPUT == OUT_PATH
    assert len(m.CUTS) > 0
    ids = [c[0] for c in m.CUTS]
    assert len(ids) == len(set(ids))
    assert "C09" in ids


@pytest.mark.parametrize("cut_id", _cut_ids())
def test_u3_old_occurs_exactly_once_in_v50(cut_id):
    cut = next(c for c in _mod().CUTS if c[0] == cut_id)
    assert _v50().count(cut[1]) == 1


@pytest.mark.parametrize("cut_id", _cut_ids())
def test_u3_missing_old_raises_naming_cut(cut_id):
    m = _mod()
    cut = next(c for c in m.CUTS if c[0] == cut_id)
    broken = _v50().replace(cut[1], "", 1)
    with pytest.raises(ValueError) as exc:
        m.build(broken)
    assert cut_id in str(exc.value)


@pytest.mark.parametrize("cut_id", _cut_ids())
def test_u3_duplicated_old_raises_naming_cut(cut_id):
    m = _mod()
    cut = next(c for c in m.CUTS if c[0] == cut_id)
    doubled = _v50() + "\n" + cut[1]
    with pytest.raises(ValueError) as exc:
        m.build(doubled)
    assert cut_id in str(exc.value)


def test_u4_restore_c09_keeps_original_and_applies_rest():
    m = _mod()
    src = _v50()
    out = m.build(src, restore={"C09"})
    c09_old = next(c[1] for c in m.CUTS if c[0] == "C09")
    assert c09_old in out
    expected = src
    for cid, old, new in m.CUTS:
        if cid != "C09":
            expected = expected.replace(old, new, 1)
    assert out == expected
    assert out != m.build(src)


def test_u4_unknown_restore_id_raises():
    with pytest.raises(ValueError):
        _mod().build(_v50(), restore={"C99999"})


@pytest.mark.parametrize("needle", KEPT_HEADERS)
def test_u5_header_present(needle):
    assert needle in _out()


@pytest.mark.parametrize("needle", KEPT_LINES)
def test_u6_business_line_present(needle):
    assert needle in _out()


@pytest.mark.parametrize("needle", GONE_STRINGS)
def test_u7_language_teaching_absent(needle):
    assert needle not in _out()


def test_u8_entity_confidence_body_at_most_600_chars():
    text = _out()
    header = "== ENTITY CONFIDENCE =="
    start = text.index(header) + len(header)
    m = re.search(r"^(== |━)", text[start:], re.MULTILINE)
    body = text[start : start + m.start()] if m else text[start:]
    assert len(body) <= 600, len(body)


def test_u9_at_least_9000_chars_shorter():
    assert len(_v50()) - len(_out()) >= 9000
