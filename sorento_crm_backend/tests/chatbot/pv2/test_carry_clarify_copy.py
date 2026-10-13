"""Clarify copy (CONTRACT-PV2-13oct.md section 5, last bullet + owner rule 13 Oct 23:50)."""
from __future__ import annotations

import re
import uuid

import pytest

from app.services.chatbot.pv2 import clarify_copy as cc
from tests.chatbot.pv2.carry_fakes import option, pending

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
OPTS = [option("dim_length", "Length"), option("dim_height", "Height")]


def _q(**kw):
    return pending("clarify_spec_attribute", OPTS, asked="Which 600 mm do you mean?", **kw)


def test_options_are_numbered_labels_and_keys_never_print():
    reply = cc.render(_q(), "en")
    lines = reply.split("\n")
    assert lines[0] == "Which 600 mm do you mean?"
    assert any(re.match(r"^1\D+Length$", l) for l in lines)
    assert any(re.match(r"^2\D+Height$", l) for l in lines)
    assert "dim_length" not in reply and "dim_height" not in reply
    assert " · " not in reply


def test_no_options_prints_only_the_question():
    q = pending("clarify_other", [], asked="What would you like me to check?")
    assert cc.render(q, "en") == "What would you like me to check?"


def test_reask_adds_a_prefix_line_before_the_question():
    plain = cc.render(_q(), "en")
    again = cc.render(_q(), "en", reask=True)
    assert again != plain and again.endswith(plain)
    assert again.split("\n")[0] != "Which 600 mm do you mean?"


def test_uuid_label_is_never_printed():
    uid = str(uuid.uuid4())
    q = pending("clarify_entity", [option("c1", uid), option("c2", "Ahmad Trading")], asked="Which one?")
    reply = cc.render(q, "en")
    assert uid not in reply and not UUID_RE.search(reply) and "Ahmad Trading" in reply
    assert cc.safe_label(uid.upper(), "en") != uid.upper()
    assert cc.safe_label("  Ahmad  ", "en") == "Ahmad"
    assert cc.safe_label("", "en").strip()


@pytest.mark.parametrize("language", ["zh", "ms"])
def test_languages_translate_the_fixed_copy(language):
    assert cc.render(_q(), language) != cc.render(_q(), "en")
    assert cc.text("reply_number", language) != cc.text("reply_number", "en")


@pytest.mark.parametrize("language", ["mixed", None, "fr", ""])
def test_mixed_and_unknown_read_as_english(language):
    assert cc.lang(language) == "en"
    assert cc.render(_q(), language, reask=True) == cc.render(_q(), "en", reask=True)


def test_not_found_names_the_phrase_and_the_kind_truthfully():
    q = cc.question_for("entity", "Zzyzx", "en", kind="customer", not_found=True)
    assert "Zzyzx" in q and "customer" in q.lower()


def test_handover_options_are_yes_no_with_bool_payloads():
    yes, no = cc.handover_options("en")
    assert (yes.key, no.key) == ("yes", "no")
    assert yes.payload["value"] is True and no.payload["value"] is False
    assert yes.label != no.label
