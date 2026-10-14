"""PV2-EVAL round 8: ideas conversations are env-blocked while the ideation service is down."""
from __future__ import annotations

from scripts.pv2_battery import report, runner

URL = "http://localhost:8200/ideation"


def test_probe_false_blocks_ideas():
    got = runner.env_blocked_domains(URL, lambda u: False)
    assert set(got) == {"ideas"}
    assert "ideation service down" in got["ideas"] and "localhost:8200" in got["ideas"]


def test_probe_raises_blocks_ideas():
    def boom(u):
        raise OSError("refused")
    assert set(runner.env_blocked_domains(URL, boom)) == {"ideas"}


def test_probe_true_blocks_nothing_and_receives_url():
    seen = []
    assert runner.env_blocked_domains(URL, lambda u: seen.append(u) or True) == {}
    assert seen == [URL]


def test_empty_url_blocks_ideas_without_probing():
    def probe(u):
        raise AssertionError("must not probe")
    for url in (None, ""):
        got = runner.env_blocked_domains(url, probe)
        assert "ideation service not configured" in got["ideas"]


def _r(cid, domain, v2, v1, audience="staff", first=None, classes=None):
    return {"id": cid, "source": "improvised", "domain": domain, "audience": audience, "v2": v2, "v1": v1,
            "first_fail": first, "classes": classes or []}


META = {"v2": {"id": "a", "number": 1, "md5": "m"}, "v1": {"id": "b", "number": 2, "md5": "n"},
        "git_sha": "abc", "judge_agreement": 0.95}
RESULTS = [_r("stock-i01", "stock", "pass", "pass"),
           _r("ideas-i01", "ideas", "fail", "fail", first=1, classes=["stuck"])]
REASON = "ideation service down (localhost:8200 unreachable)"


def _row(table, cid):
    return next(l for l in table.splitlines() if l.startswith("|") and cid in l)


def _section(table, key):
    lines = table.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("##") and key in l.lower())
    return [l for l in lines[start:] if l.startswith("|")]


def test_env_rows_show_env_and_header_lists_them():
    t = report.render_table(RESULTS, dict(META, env_blocked={"ideas": REASON}))
    cells = [c.strip() for c in _row(t, "ideas-i01").strip("|").split("|")]
    assert cells.count("env") >= 2 and "fail" not in cells
    assert f"Env blocked: ideas ({REASON})" in t
    assert "pass" in _row(t, "stock-i01")


def test_totals_exclude_env_rows():
    t = report.render_table(RESULTS, dict(META, env_blocked={"ideas": REASON}))
    ideas = next(l for l in _section(t, "domain") if l.startswith("| ideas"))
    assert ideas.count("0/0") == 2
    staff = next(l for l in _section(t, "audience") if l.startswith("| staff"))
    assert staff.count("1/1") == 2
    src = next(l for l in _section(t, "source") if l.startswith("| improvised"))
    assert src.count("1/1") == 2


def test_no_env_blocked_leaves_table_unchanged():
    t = report.render_table(RESULTS, META)
    assert "Env blocked" not in t
    cells = [c.strip() for c in _row(t, "ideas-i01").strip("|").split("|")]
    assert "fail" in cells and "env" not in cells
    staff = next(l for l in _section(t, "audience") if l.startswith("| staff"))
    assert staff.count("1/2") == 2


def _fail(cid, domain):
    return {"conversation": cid, "domain": domain, "turn": 1, "mode": "v2", "text": "t", "reply": "r",
            "reasons": ["stuck: same"], "turn_id": "tid"}


def test_failures_leave_env_blocked_out_and_state_once():
    f = report.render_failures([_fail("ideas-i01", "ideas"), _fail("stock-i01", "stock")],
                               dict(META, env_blocked={"ideas": REASON}))
    assert "stock-i01" in f and "ideas-i01" not in f
    assert f.count(REASON) == 1
    assert f.index(REASON) < f.index("stock-i01")
