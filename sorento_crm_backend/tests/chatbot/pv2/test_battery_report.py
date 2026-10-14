"""PV2-EVAL UAC-8: result roll-up and markdown output. Pure."""
from __future__ import annotations

from scripts.pv2_battery import report

META = {"v2": {"id": "v2-uuid-aaaa", "number": 12, "md5": "md5v2aaa"},
        "v1": {"id": "f3dc8c35-4fc8-41b1-89eb-a6ef14e71cea", "number": 91, "md5": "f447ad501b72a8e2fa6abd142b51e80e"},
        "git_sha": "0690ef90a", "judge_agreement": 0.95}


def test_conversation_result():
    assert report.conversation_result([{"ok": True}, {"ok": True}]) == "pass"
    assert report.conversation_result([{"ok": True}, {"ok": False}]) == "fail"


def test_combine_reps():
    assert report.combine_reps(["pass", "pass", "pass"]) == "pass"
    assert report.combine_reps(["fail", "fail"]) == "fail"
    assert report.combine_reps(["pass", "fail", "pass"]) == "flaky"


def result(cid, v2, v1, domain="stock", audience="staff", source="real", first=None, classes=None):
    return {"id": cid, "source": source, "domain": domain, "audience": audience, "v2": v2, "v1": v1,
            "first_fail": first, "classes": classes or []}


RESULTS = [
    result("stock-r01", "pass", "fail", first=2, classes=["leak"]),
    result("eta-i02", "fail", "fail", domain="eta", audience="dealer", source="improvised", first=1, classes=["stuck"]),
]


def test_table_header_and_rows():
    t = report.render_table(RESULTS, META)
    for needle in ("v2-uuid-aaaa", "md5v2aaa", "f3dc8c35-4fc8-41b1-89eb-a6ef14e71cea",
                   "f447ad501b72a8e2fa6abd142b51e80e", "0690ef90a", "stock-r01", "eta-i02", "leak", "stuck"):
        assert needle in t, needle
    assert "PROVISIONAL" not in t
    row = next(l for l in t.splitlines() if "stock-r01" in l)
    for needle in ("real", "stock", "staff", "pass", "fail"):
        assert needle in row, needle


def test_table_totals_sections():
    t = report.render_table(RESULTS, META).lower()
    for needle in ("domain", "audience", "source", "improvised"):
        assert needle in t


def test_provisional_when_agreement_low_or_missing():
    assert "PROVISIONAL" in report.render_table(RESULTS, dict(META, judge_agreement=0.8))
    assert "PROVISIONAL" in report.render_table(RESULTS, dict(META, judge_agreement=None))


def test_render_failures():
    rows = [{"conversation": "stock-r01", "turn": 3, "text": "the second one", "reply": "Which product?",
             "reasons": ["stuck: same reply", "judge: lost_ask"], "turn_id": "turn-abc123", "ok": False}]
    f = report.render_failures(rows)
    for needle in ("stock-r01", "3", "the second one", "stuck: same reply", "lost_ask", "turn-abc123"):
        assert needle in f, needle
