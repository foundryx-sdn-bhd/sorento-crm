"""PV2-EVAL round 11: phone masking (not other numbers), business keys kept, env-blocked deterministic fails."""
from __future__ import annotations

import pytest

from scripts.pv2_battery import judge, report, runner

PHONES = ["+60 12-345 6789", "+60123456789", "60123456789", "012-345 6789", "0123456789",
          "03-5161 2345", "03 5161 2345"]
SURVIVORS = ["12345678.90", "PO 4500123456", "202610-0151", "SO422057", "202606-S0105", "487555417"]


def _digits(p: str) -> str:
    return "".join(ch for ch in p if ch.isdigit())


@pytest.mark.parametrize("phone", PHONES)
def test_mask_phones_masks_each_format(phone):
    out = judge.mask_phones(f"Call {phone} now")
    assert phone not in out and _digits(phone) not in out
    assert out.startswith("Call ") and out.endswith(" now")


def test_mask_phones_keeps_other_numbers():
    text = "Amount 12345678.90, PO 4500123456, DO 202610-0151, SO422057, PO 202606-S0105, id 487555417, qty 25"
    assert judge.mask_phones(text) == text


def test_prompt_masks_reply_and_history_but_keeps_documents():
    p = judge.build_judge_prompt(
        history=[{"user": "who is the salesman", "bot": "Call 012-345 6789 about 202610-0151"}],
        text="ok", reply="Reach us on 03-5161 2345 for PO 4500123456 (012-345 6789)", files=0,
        expect={"kind": "answer", "about": "x"}, facts="", audience="staff")
    for needle in ("012-345 6789", "03-5161 2345", "0123456789"):
        assert needle not in p
    assert "202610-0151" in p and "PO 4500123456" in p


def _digest_of(raw_rows):
    return runner.facts_digest([{"stage": "looked_up", "raw": {"rows": raw_rows}}])


@pytest.mark.parametrize("phone", PHONES)
def test_facts_mask_every_malaysian_phone_format(phone):
    d = _digest_of([{"Contact line": f"Tel {phone}", "Product Code": "SRTWC286-SH"}])
    assert phone not in d and _digits(phone) not in d
    assert "SRTWC286-SH" in d


def test_facts_keep_non_phone_numbers():
    d = _digest_of([{"a": "12345678.90", "b": "PO 4500123456", "c": "202610-0151", "d": "SO422057",
                     "e": "202606-S0105", "f": "487555417", "g": 25}])
    for needle in SURVIVORS:
        assert needle in d, needle


def test_facts_keep_channel_and_meta_but_drop_envelope_contact_profile():
    trace = [{"stage": "looked_up", "raw": {
        "rows": [{"channel": "CHANNELKEEP", "meta": {"k": "METAKEEP"}}],
        "envelope": {"x": "ENVDROP"}, "contact": {"n": "CONTACTDROP"}, "profile": "PROFILEDROP",
        "wamidline": "id wamid.HBgLNjAxMjM0NTY3ODkVAgASGBQ here"}}]
    d = runner.facts_digest(trace)
    assert "CHANNELKEEP" in d and "METAKEEP" in d
    for gone in ("ENVDROP", "CONTACTDROP", "PROFILEDROP", "wamid.HBgL", "wamid."):
        assert gone not in d, gone


def test_facts_do_not_scrub_person_names_in_business_rows():
    d = _digest_of([{"Driver": "Ah Kow", "Salesman": "Siti Roster"}])
    assert "Ah Kow" in d and "Siti Roster" in d


# ---- failures.md for env-blocked domains ----
REASON = "ideation service down (localhost:8200 unreachable)"
META = {"env_blocked": {"ideas": REASON}}


def _row(cid, domain, checks, judge_verdict=None):
    return {"conversation": cid, "domain": domain, "turn": 1, "mode": "v2", "text": "t", "reply": "r",
            "checks": checks, "judge": {"verdict": judge_verdict, "classes": ["stuck"], "reason": "r"} if judge_verdict else None,
            "reasons": [f"{c['check']}: {c['detail']}" for c in checks] + (["judge: stuck: r"] if judge_verdict else []),
            "turn_id": "tid", "ok": False}


def test_env_blocked_deterministic_fails_get_their_own_section():
    rows = [_row("stock-i01", "stock", [{"check": "must", "detail": "m"}]),
            _row("ideas-det", "ideas", [{"check": "must_not", "detail": "bad"}]),
            _row("ideas-judge", "ideas", [], judge_verdict="fail")]
    f = report.render_failures(rows, META)
    assert "Env-blocked domain, deterministic fails" in f
    section = f[f.index("Env-blocked domain, deterministic fails"):]
    assert "ideas-det" in section
    assert "ideas-judge" not in f
    assert "stock-i01" in f and "stock-i01" not in section
    assert f.count(REASON) == 1
