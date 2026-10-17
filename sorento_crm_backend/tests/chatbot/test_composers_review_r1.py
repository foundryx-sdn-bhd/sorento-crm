"""CHAT-COMPOSER review fix round 1: red tests for the review findings.

B1  gate OFF: ms/zh rendering of every reply that also prints on main is byte-identical to main
    (English sentence kept), and none of the lane's new catalogue keys is seeded or used.
B2  Q1 in ms/zh and with the promotion section first: the question goes, and the task goes with it
    (never a question without its task).
S3  the R3 drop keys on the miss STATING the absence, not on the text mentioning the code.
S4  `current_message` null counts as the current message (Q1 keeps the question and the task).
N5  a real UUID-shaped token never reaches composer output.
L1  the customer found bullet names only the groups of the customers the gate-off path named.
L2  a UUID product code never appears in the new "No <type> on record for <code>" lines.
N7  the miss noun survives the `status_label` prefix.

Gate-off literals for B1 were captured by running the same calls in the base worktree
(main 20c191d86), never edited there.
"""
from __future__ import annotations

import re

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services import ledger_family as LF
from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.lanes.business import answer as A
from app.services.chatbot.turn import compose as turn_compose
from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_composers_stock_xd import _set_gate

UUID = "123e4567-e89b-12d3-a456-426614174000"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _loc(lang, table=None):
    return Localizer(lang, table if table is not None else defaults(lang))


# --------------------------------------------------------------------------- #
# B1: gate OFF is main, byte for byte
# --------------------------------------------------------------------------- #

DESCRIBED = "Here's what you want: taps\n• finish: gunmetal\n\nBut no stock matched these."
DESCRIBED_OFFER = DESCRIBED + " Would you like me to escalate to sales team?"
MS_OFFER = "Adakah anda mahu saya rujuk kepada pasukan sales?"
ZH_OFFER = "需要我转交给 sales 团队吗？"

#: sentence -> (key text, stored-row template). Every NEW lane key's English sentence.
NEW_KEY_SENTENCES = [
    "But no stock matched these.",
    "But no product matched these.",
    "But no SPO line matched these.",
    "No Product Photos on record for X1.",
    "No weight on record for X1.",
    "No list price on record for X1.",
    "No dimensions on record for X1.",
]
NEW_KEYS = [
    "But no stock matched these.",
    "But no product matched these.",
    "But no SPO line matched these.",
    "No {what} on record for {product}.",
    "No {noun} on record for {product}.",
    "No list price on record for {product}.",
    "No dimensions on record for {product}.",
]


def test_b1_described_members_reply_is_main_gate_off():
    """`described_members_reply` -> `what_you_want_reply(leg="stock")` prints on main."""
    reply = A.what_you_want_reply("taps", ["• finish: gunmetal"], leg="stock", team="sales")
    assert reply == DESCRIBED_OFFER


@pytest.mark.parametrize("lang,offer", [("ms", MS_OFFER), ("zh", ZH_OFFER)])
def test_b1_gate_off_ms_zh_keeps_the_english_sentence(monkeypatch, lang, offer):
    _set_gate(monkeypatch, False)
    loc = _loc(lang)
    assert loc.reply(DESCRIBED) == DESCRIBED
    assert loc.reply(DESCRIBED_OFFER) == f"{DESCRIBED} {offer}"


@pytest.mark.parametrize("lang", ["ms", "zh"])
@pytest.mark.parametrize("sentence", NEW_KEY_SENTENCES)
def test_b1_gate_off_no_new_key_is_used(monkeypatch, lang, sentence):
    _set_gate(monkeypatch, False)
    assert _loc(lang).reply(sentence) == sentence


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_b1_gate_off_defaults_hold_no_new_key(monkeypatch, lang):
    _set_gate(monkeypatch, False)
    table = defaults(lang)
    assert [k for k in NEW_KEYS if k in table] == []
    _set_gate(monkeypatch, True)
    assert [k for k in NEW_KEYS if k not in defaults(lang)] == []


@pytest.mark.parametrize("lang", ["ms", "zh"])
@pytest.mark.parametrize("sentence", NEW_KEY_SENTENCES)
def test_b1_gate_off_a_stored_row_for_a_new_key_is_ignored(monkeypatch, lang, sentence):
    """A translation-memory row for a lane key must not make a gate-off reply differ from main."""
    _set_gate(monkeypatch, False)
    stored = {k: "STORED {what} {product} {noun}".strip() for k in NEW_KEYS}
    table = {**defaults(lang), **stored}
    assert _loc(lang, table).reply(sentence) == sentence


GATE_ON_MS = {
    "But no stock matched these.": "Tiada stok yang sepadan.",
    "But no product matched these.": "Tiada produk yang sepadan.",
    "But no SPO line matched these.": "Tiada baris SPO yang sepadan.",
    "No Product Photos on record for X1.": "Tiada Product Photos dalam rekod untuk X1.",
    "No weight on record for X1.": "Tiada weight direkodkan untuk X1.",
    "No list price on record for X1.": "Tiada harga senarai direkodkan untuk X1.",
    "No dimensions on record for X1.": "Tiada dimensi direkodkan untuk X1.",
}


@pytest.mark.parametrize("sentence,expected", list(GATE_ON_MS.items()))
def test_b1_gate_on_translates_as_today(monkeypatch, sentence, expected):
    _set_gate(monkeypatch, True)
    assert _loc("ms").reply(sentence) == expected


# --------------------------------------------------------------------------- #
# B2 / S4: Q1 in ms / zh, promotion first, null current_message
# --------------------------------------------------------------------------- #

BARE = {
    "en": "How many units do you need?",
    "ms": "Berapa unit yang anda perlukan?",
    "zh": "您需要多少件？",
}
PROMO = {
    "en": "I have attached the file(s) below.\n\n1. *Promotion:* CABANA",
    "ms": "Saya telah melampirkan fail di bawah.\n\n1. *Promosi:* CABANA",
    "zh": "我已附上文件。\n\n1. *促销:* CABANA",
}


class _Spec:
    def __init__(self, domain):
        self.domain = domain
        self.entities = [{"uuid": "x"}]
        self.filters = {}


class _Plan:
    def __init__(self, *domains):
        self.fetch = [_Spec(d) for d in domains]


def _q1(text, *, current=False, domains=("inventory", "promotion")):
    from app.services.chatbot.engine import _stock_ask_reply

    entity = ht.asked("SRTKS7547")
    entity["current_message"] = current
    state = ht.state(availability_only=True)
    out = _stock_ask_reply(
        turn_compose.Answer(text=text),
        state,
        ht.envelopes(ht.row("SRTKS7547")),
        _Plan(*domains),
        verdict(domain_hint="promotion", entities=[entity], domain_in_message=False),
        turn_no=5,
    )
    opened = [t for t in state.focus.tasks if t.kind == "stock_qty" and t.opened_at_turn == 5]
    return out, opened


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_b2_localized_question_is_removed_and_the_task_dropped(monkeypatch, lang):
    _set_gate(monkeypatch, True)
    out, opened = _q1(f"{BARE[lang]}\n\n{PROMO[lang]}")
    assert BARE[lang] not in out.text
    assert out.text == PROMO[lang]
    assert opened == []


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_b2_promotion_first_the_question_is_removed_and_the_task_dropped(monkeypatch, lang):
    _set_gate(monkeypatch, True)
    out, opened = _q1(f"{PROMO[lang]}\n\n{BARE[lang]}")
    assert BARE[lang] not in out.text
    assert out.text == PROMO[lang]
    assert opened == []


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_b2_never_a_question_without_its_task(monkeypatch, lang):
    """Whatever the layout, a question still in the text means the task is still open."""
    _set_gate(monkeypatch, True)
    unknown = "Sila nyatakan kuantiti anda?"
    for text in (f"{BARE[lang]}\n\n{PROMO[lang]}", f"{PROMO[lang]}\n\n{BARE[lang]}", f"{unknown}\n\n{PROMO[lang]}"):
        out, opened = _q1(text)
        question_left = BARE[lang] in out.text or unknown in out.text
        assert not question_left or opened, (text, out.text)


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_s4_null_current_message_counts_as_current(monkeypatch, lang):
    """The engine convention is `is not False` = current: Q1 keeps the question and the task."""
    _set_gate(monkeypatch, True)
    text = f"{BARE[lang]}\n\n{PROMO[lang]}"
    out, opened = _q1(text, current=None)
    assert out.text == text
    assert len(opened) == 1


# --------------------------------------------------------------------------- #
# S3: the R3 drop needs a miss that STATES the absence
# --------------------------------------------------------------------------- #


def _xd(code="SRT5734"):
    item = {"fields": [{"key": "product_code", "label": "Product Code", "value": code}, {"key": "t", "label": "Total", "value": "60"}]}
    zs = {
        "active": True,
        "origin_domain": "incoming",
        "team": "warehouse",
        "missing": [{"code": code, "_n": code, "uuid": f"u-{code}"}],
    }
    return A.crossdomain_render({"items": [item], "has_result": True}, zeroset=zs, validator={})


def _fold(text, **kw):
    from app.services.chatbot.answer_bridge import _apply_crossdomain_render

    return _apply_crossdomain_render(text, {"render": _xd()}, **kw)


def test_s3_picker_naming_the_code_keeps_the_absence_note(monkeypatch):
    _set_gate(monkeypatch, True)
    text = "Which product do you mean? Please choose:\n1. SRT5734-WH\n2. SRT5734-BL"
    assert "No incoming for SRT5734." in _fold(text)


def test_s3_found_line_naming_the_code_keeps_the_absence_note(monkeypatch):
    _set_gate(monkeypatch, True)
    text = "Here's what you want:\n• product: SRT5734-WH\n\nPROMO CABANA is not available to you."
    assert "No incoming for SRT5734." in _fold(text)


def test_s3_a_miss_that_states_the_absence_still_drops_the_note(monkeypatch):
    _set_gate(monkeypatch, True)
    text = "Here's what you want:\n• product: SRT5734\n\nBut no incoming matched these."
    assert "No incoming for SRT5734." not in _fold(text)


# --------------------------------------------------------------------------- #
# N5: a real UUID never reaches composer output
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("gate", [False, True])
def test_n5_uuid_token_never_appears_in_composer_output(monkeypatch, gate):
    from app.services.chatbot.composers import misses, stock

    _set_gate(monkeypatch, gate)
    outs = [
        stock.block(1, {"title": UUID, "fields": []}),
        stock.block(1, {"title": "name", "fields": [{"label": "Product Code", "value": UUID}]}),
        stock.dealer_block({"title": UUID, "flags": {}}),
    ]
    if gate:
        outs += [stock.miss_line([UUID]), misses.not_found([UUID, "X"]).text]
    for t in outs:
        assert not UUID_RE.search(t), t


# --------------------------------------------------------------------------- #
# L1: the customer found bullet names only what the gate-off path named
# --------------------------------------------------------------------------- #

HANLIM = "HANLIM TRADING SDN BHD"
JUBIN = "JUBIN BMS SDN BHD"
LEDGERS = [
    (HANLIM + " [A/C I]", HANLIM),
    (HANLIM + " [A/C II]", HANLIM),
    ("JUBIN BMS (1990) SDN BHD", JUBIN),
    ("JUBIN BMS (1990) SDN BHD [A/C I]", JUBIN),
]


def _customer_miss():
    rows = [{"uuid": f"u{i}", "entity_type": "customer", "display_name": n, "code": f"300-X{i}"} for i, (n, _g) in enumerate(LEDGERS)]
    parser = {"domain_hint": "order", "entities": [], "routing": {}, "access_levels": []}
    resolved = {"by_entity_type": {"customer": ["x"]}, "resolutions": [], "unresolved_tokens": ["ZZXX"], "tokens": []}
    gate = {"gate_passed": True, "compatible_entities": rows}
    with LF.customer_groups(dict(LEDGERS)):
        return A.not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)["escalate_message"]


def test_l1_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert "\n• customer: HANLIM TRADING SDN BHD [A/C I] (+3 more)\n" in _customer_miss()


def test_l1_bullet_names_only_the_named_customers_group_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _customer_miss()
    assert "\n• customer: HANLIM TRADING SDN BHD\n" in text, text
    bullet = next(ln for ln in text.splitlines() if ln.startswith("• customer:"))
    assert JUBIN not in bullet and "(+" not in bullet


# --------------------------------------------------------------------------- #
# L2: a UUID product code never appears in the new "on record" lines
# --------------------------------------------------------------------------- #


def _else_arm(code_stored):
    parser = {
        "domain_hint": "product_attachment",
        "intent_hint": "check_product_attachment",
        "message_type": "business_query",
        "entities": [
            {"raw": "SRT207DIY", "hint": "product", "current_message": True, "confident": True},
            {"raw": "Product Photos", "hint": "attachment_type", "current_message": True, "confident": True},
        ],
        "routing": {"suggested_team": "marketing_product"},
    }
    resolved = {
        "resolutions": [
            {"token": "srt207diy", "matches": [{"entity_type": "product", "canonical_code": code_stored, "uuid": "p0", "match_tier": "spec_search"}]}
        ],
        "unresolved_tokens": ["srt207diy"],
        "tokens": ["srt207diy"],
        "intersection": [],
    }
    gate = {
        "gate_passed": True,
        "compatible_entities": [{"uuid": "p0", "entity_type": "product", "code": code_stored}],
        "gate_debug": {"domain": "product_attachment"},
    }
    return A.not_found_error_message(
        {"resolved": resolved, "gate": gate}, parser=parser, resolved=resolved, gate=gate, product_token_missed=True
    )["escalate_message"]


def _breakdown(code):
    parser = {
        "domain_hint": "product_attachment",
        "intent_hint": "check_product_attachment",
        "message_type": "business_query",
        "entities": [
            {"raw": "SRT207DIY", "hint": "product", "current_message": True, "confident": True},
            {"raw": "Product Photos", "hint": "attachment_type", "current_message": True, "confident": True},
        ],
        "routing": {"suggested_team": "marketing_product"},
    }
    resolved = {
        "resolutions": [
            {"token": "srt207diy", "matches": [{"entity_type": "product", "canonical_code": code, "uuid": "p0"}]},
            {
                "token": "product photos",
                "matches": [
                    {"entity_type": "attachment_type", "canonical_code": "Product Photos", "uuid": "t0", "display": {"type_name": "Product Photos"}}
                ],
            },
        ],
        "unresolved_tokens": [],
        "tokens": ["srt207diy"],
        "intersection": [],
        "by_entity_type": {"product": [code], "attachment_type": ["Product Photos"]},
    }
    gate = {
        "gate_passed": True,
        "compatible_entities": [
            {"uuid": "p0", "entity_type": "product", "code": code},
            {"uuid": "t0", "entity_type": "attachment_type", "code": "Product Photos"},
        ],
        "gate_debug": {"domain": "product_attachment"},
    }
    return A.not_found_error_message({"resolved": resolved, "gate": gate}, parser=parser, resolved=resolved, gate=gate)[
        "escalate_message"
    ]


@pytest.mark.parametrize("fn", [_else_arm, _breakdown], ids=["else_arm", "breakdown"])
def test_l2_uuid_product_code_never_in_the_on_record_line(monkeypatch, fn):
    _set_gate(monkeypatch, True)
    out = fn(UUID)
    assert not UUID_RE.search(out), out


# --------------------------------------------------------------------------- #
# N7: the miss noun with a status_label prefix
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["outstanding", "delivered"])
def test_n7_status_label_prefix_names_no_internal_domain(monkeypatch, status):
    _set_gate(monkeypatch, True)
    rows = [
        {"entity_type": "product", "uuid": f"u{i}", "canonical_code": f"SRTWC18{i}-BL", "code": f"SRTWC18{i}-BL", "display": {"description": "d"}}
        for i in range(3)
    ]
    parser = {
        "domain_hint": "order",
        "order_status": status,
        "entities": [{"raw": "toilet", "hint": "product", "confident": False, "current_message": True}],
        "routing": {"suggested_team": "customer_service"},
        "access_levels": [],
    }
    resolved = {
        "by_entity_type": {"product": ["SRTWC180-BL"]},
        "resolutions": [{"token": "toilet", "matches": rows}],
        "unresolved_tokens": [],
        "tokens": [],
    }
    out = A.not_found_error_message({}, parser=parser, resolved=resolved, gate={"gate_passed": True, "compatible_entities": rows})[
        "escalate_message"
    ]
    assert not re.search(r"\binventory\b|master[_ ]products", out), out


# --------------------------------------------------------------------------- #
# Round 2
# --------------------------------------------------------------------------- #


def test_unplaced_line_all_uuid_prints_nothing_gate_on(monkeypatch):
    """A token list made only of UUIDs names nothing: no "Couldn't find ." sentence at all."""
    from tests.chatbot.test_multi_code_dym import _answer, _envelope

    _set_gate(monkeypatch, True)
    answer = _answer(_envelope({"SRTWC286": 12}, [UUID], {}))
    assert "Couldn't find ." not in answer.text, answer.text
    assert "Couldn't find ." not in answer.miss_text
    assert not UUID_RE.search(answer.text)


def test_absence_writers_satisfy_states_absence(monkeypatch):
    """Drift guard: every writer of an absence sentence produces a phrase `states_absence` reads."""
    from app.services.chatbot.composers import misses
    from tests.chatbot.test_composers_s4_miss import _breakdown
    from tests.chatbot.test_rearch_r4_bridge_miss import _expected_miss_text
    from tests.chatbot.test_rearch_r8_recheck_round import _dym_incoming_sibling_scenario

    _set_gate(monkeypatch, True)
    parser, resolved, extra = _dym_incoming_sibling_scenario()
    payload = {"resolved": resolved, "gate": extra["gate"], "_exit_kind": "not_found"}
    sibling_text, _offer = _expected_miss_text(
        payload, parser=parser, resolved=resolved, gate=extra["gate"], services=extra["services"], build_result=extra["build_result"]
    )
    writers = {
        "what_you_want_reply": (
            A.what_you_want_reply("SRT5734", ["• product: SRT5734"], leg="incoming", team="sales"),
            "SRT5734",
        ),
        "build_breakdown_msg": (_breakdown(), "SRTBF11850"),
        "build_suggest_offer": (sibling_text, "SRTWT7202"),
    }
    for name, (text, code) in writers.items():
        assert misses.states_absence(text, [code]), (name, text)
    joined = "\n".join(text for text, _code in writers.values()).lower()
    for phrase in misses.ABSENCE_PHRASES:
        assert phrase in joined, phrase
