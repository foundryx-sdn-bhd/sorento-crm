import json
import re

import pytest

from scripts.chatbot_eval.report import GRADER_VERSION
from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import case_dict, write_case

FAKE = "tests.chatbot_eval.fakes:FakeEngine"
NEVER = "tests.chatbot_eval.fakes:NeverEngine"
TOPICS = {
    "po1": "purchase_order.outstanding", "po2": "purchase_order.status", "do1": "order.delivery_status",
    "inv1": "inventory.availability", "inv2": "inventory.quantity_check",
}


def data(tmp_path, extra=None):
    d = tmp_path / "data"
    for cid, topic in TOPICS.items():
        c = case_dict(cid, topic=topic)
        c.update((extra or {}).get(cid, {}))
        write_case(d, c)
    return d


def go(tmp_path, *args, engine=FAKE, extra=None, out="o"):
    o = tmp_path / out
    rc = main(["--data-dir", str(data(tmp_path, extra)), "--engine", engine, "--out", str(o), *args])
    return rc, o


def ids_of(o):
    return sorted(json.loads(x)["case_id"] for x in (o / "results.jsonl").read_text().splitlines() if x.strip())


def test_topic_prefix_selects_children(tmp_path):
    rc, o = go(tmp_path, "--topics", "purchase_order")
    assert rc == 0 and ids_of(o) == ["po1", "po2"]


def test_full_topic_does_not_select_sibling(tmp_path):
    rc, o = go(tmp_path, "--topics", "inventory.availability")
    assert rc == 0 and ids_of(o) == ["inv1"]


def test_unknown_topic_exit_2_names_it(tmp_path, capsys):
    rc, _ = go(tmp_path, "--topics", "nonexistent_topic", engine=NEVER)
    assert rc == 2 and "nonexistent_topic" in capsys.readouterr().err


@pytest.mark.parametrize("form", ["array", "object"])
def test_ids_file_forms(tmp_path, form):
    f = tmp_path / "ids.json"
    f.write_text(json.dumps(["po1", "do1"] if form == "array" else {"ids": ["po1", "do1"]}))
    rc, o = go(tmp_path, "--ids-file", str(f))
    assert rc == 0 and ids_of(o) == ["do1", "po1"]


def test_unknown_id_exit_2(tmp_path, capsys):
    f = tmp_path / "ids.json"
    f.write_text(json.dumps(["po1", "ghost-id"]))
    rc, _ = go(tmp_path, "--ids-file", str(f), engine=NEVER)
    assert rc == 2 and "ghost-id" in capsys.readouterr().err


def test_topics_and_ids_file_union(tmp_path):
    f = tmp_path / "ids.json"
    f.write_text(json.dumps(["do1"]))
    rc, o = go(tmp_path, "--topics", "inventory.availability", "--ids-file", str(f))
    assert rc == 0 and ids_of(o) == ["do1", "inv1"]


def _estimate(capsys):
    m = re.search(r"selected (\d+) cases, ~(\d+) calls, ~\$([\d.]+)", capsys.readouterr().err)
    assert m, "no 'selected N cases, ~C calls, ~$X' line on stderr"
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def test_estimate_line_counts_history_and_variants(tmp_path, capsys):
    extra = {
        "po1": {"history": [{"role": "user", "text": "u1"}, {"role": "bot", "text": "b"}, {"role": "user", "text": "u2"}]},
        "po2": {"variants": [{"kind": "typo", "text": "v1"}, {"kind": "slang", "text": "v2"}]},
    }
    go(tmp_path, "--topics", "purchase_order", "--variants", "all", extra=extra)
    n, c, usd = _estimate(capsys)
    # po1: (1+2 history) x (1+0) = 3; po2: (1+0) x (1+2) = 3
    assert (n, c) == (2, 6) and usd > 0


def test_estimate_variants_none_ignores_variants(tmp_path, capsys):
    extra = {"po2": {"variants": [{"kind": "typo", "text": "v1"}]}}
    go(tmp_path, "--topics", "purchase_order", "--variants", "none", extra=extra)
    assert _estimate(capsys)[:2] == (2, 2)


def test_estimate_only_exits_0_without_engine(tmp_path, capsys):
    rc, _ = go(tmp_path, "--topics", "purchase_order", "--estimate-only", engine=NEVER)
    assert rc == 0
    assert _estimate(capsys)[0] == 2


def _baseline_dir(tmp_path, labels):
    b = tmp_path / "base"
    b.mkdir()
    rows = []
    for cid, lab in labels.items():
        rows.append({"case_id": cid, "split": "dev", "topic_code": TOPICS[cid], "language": "en", "variant_kind": None,
                     "label": lab, "reasons": [], "latency_ms": 5, "tokens": {"prompt": 0, "cached": 0, "output": 0},
                     "reply_text": "x", "branch_kind": "answer", "delegated": False, "tool_errors": [],
                     "lane_notes": [], "previous_bot_reply": None, "needs_judge": False})
    (b / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (b / "report.json").write_text(json.dumps({"grader_version": GRADER_VERSION}))
    return b


def test_baseline_dir_restricts_delta_to_matched_rows(tmp_path):
    base = _baseline_dir(tmp_path, {"po1": "correct", "po2": "wrong", "do1": "correct", "inv1": "correct", "inv2": "correct"})
    rc, o = go(tmp_path, "--topics", "purchase_order", "--baseline", str(base))
    assert rc == 0
    d = json.loads((o / "report.json").read_text())["delta"]
    assert d["restricted"] is True and d["matched"] == 2
    # now both correct (1.0) vs baseline on those two rows 0.5; the whole baseline would be 0.8
    assert d["total"] == pytest.approx(0.5)


def test_baseline_report_file_keeps_whole_delta(tmp_path):
    rc, o1 = go(tmp_path, out="full")
    rc, o2 = go(tmp_path, "--topics", "purchase_order", "--baseline", str(o1 / "report.json"), out="sub")
    assert json.loads((o2 / "report.json").read_text())["delta"]["restricted"] is False


def test_report_records_selection(tmp_path):
    f = tmp_path / "ids.json"
    f.write_text(json.dumps(["do1"]))
    rc, o = go(tmp_path, "--topics", "purchase_order", "--ids-file", str(f))
    sel = json.loads((o / "report.json").read_text())["selection"]
    assert sel["topics"] in (["purchase_order"], "purchase_order")
    assert sel["ids_file"] == str(f)
    assert sel["cases"] == 3
