"""CHAT-CODE-LOOKUP slice C prompt text contract (R4, R6, Q5; U4.1, U6.1, U6.2, U7.1).

Behaviour is measured live later; these pin the text the parser reads, on stable phrases.
"""
from __future__ import annotations

import re

from app.services.chatbot.head import closed_lists_prompt
from app.services.chatbot.head.code_lookup_prompt import MARKER, SECTION, gate_on

S = re.sub(r"\s+", " ", SECTION).lower()


def test_r4_unlisted_attribute_is_still_emitted_in_the_users_word():
    assert "weight" in S
    assert "not on the list" in S or "not on the attribute list" in S
    assert "own word" in S


def test_r6_code_with_a_count_and_no_purpose_word_is_a_stock_ask():
    assert "5unit" in S and "x 10" in S
    assert "stock ask" in S
    assert "whatever the previous domain" in S or "whatever the last domain" in S or "any previous domain" in S


def test_r6_bare_code_after_an_answer_reuses_that_answers_domain():
    assert "bare code" in S
    assert "reuses" in S or "same domain" in S
    assert "eta" in S


def test_q5_when_deliver_is_outstanding_and_never_asks_a_period():
    assert "when deliver" in S
    assert "outstanding" in S
    assert "period" in S
    assert "delivery-list" in S or "delivery list" in S


def test_e_near_code_and_set_code_paragraphs_survive():
    assert 'a "near code x (name)" line under a word with a digit' in S
    assert "when two or more are equally plausible" in S
    assert 'a "set code x (name)" line is a product set' in S
    assert "pick the set whose name carries it" in S


def test_f_gate_on_needs_both_markers():
    assert MARKER in SECTION
    assert gate_on(f"x {closed_lists_prompt.MARKER} y {MARKER}")
    assert not gate_on(f"x {MARKER}")
    assert not gate_on(f"x {closed_lists_prompt.MARKER}")
    assert not gate_on(None)


# ---- fix round 1 (F6, F7, F8): stable phrases of the lead-written prompt text


def test_f6_no_pick_never_says_confident_false():
    assert "confident false" not in S


def test_f7_asked_attributes_rule_is_master_products_only():
    asked = S[S.index("asked attributes."):]
    asked = asked[: asked.index("a count beside a code.")]
    assert 'domain_hint "master_products"' in asked
    assert "every other domain keeps its own rule" in asked


def test_f8_a_typed_word_not_on_file_is_never_its_own_canonical_code():
    assert "never its own canonical_code" in S


def test_f8_a_set_whose_name_carries_another_spec_gives_way_to_the_set_that_carries_the_asked_one():
    assert "pick the listed set or near set code whose name carries the spec" in S


# ---- R2-6 (crew decision 18 Oct): a near code is picked only when no other listed code is as close


def _near_code_paragraph() -> str:
    start = S.index('a "near code x (name)" line under a word with a digit')
    return S[start : S.index('a "set code x (name)" line is a product set')]


def test_r2_6_swapped_digits_still_pick():
    para = _near_code_paragraph()
    assert "plainly the code the message means" in para
    assert "same digits with letters swapped, missing or one letter off" in para
