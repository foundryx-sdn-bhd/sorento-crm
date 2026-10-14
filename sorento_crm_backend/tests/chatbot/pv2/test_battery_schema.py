"""PV2-EVAL UAC-1/2/3: battery loader, validator and coverage. Pure, no database."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.pv2_battery import schema


def _write(dir_: Path, name: str, convs: list[dict], domain: str = "stock") -> None:
    (dir_ / name).write_text(yaml.safe_dump({"domain": domain, "conversations": convs}), encoding="utf-8")


def _conv(cid="stock-r01", **over) -> dict:
    c = {
        "id": cid, "source": "improvised", "domain": "stock", "audience": "staff",
        "shapes": ["refine"],
        "turns": [{"text": "check stock srtwc286", "expect": {"kind": "answer", "about": "stock for SRTWC286"}}],
    }
    c.update(over)
    return c


def test_constants_match_uac1():
    assert schema.DOMAINS == ("stock", "eta", "attachments", "order_status", "outstanding", "reports",
                              "promotions", "ideas", "handover", "mixed")
    assert schema.AUDIENCES == ("staff", "dealer", "dealer_nogrant", "unlinked")
    assert set(schema.SHAPES) == {
        "refine", "reply_to_question", "number_pick", "yes_no", "free_text_reply", "topic_switch",
        "switch_back", "point_into_answer", "multi_product", "mixed_types", "qty_carried",
        "language_mix", "typo", "particle", "small_talk", "unclear", "permission"}
    assert set(schema.KINDS) == {"answer", "ask", "handover", "refuse", "any"}
    assert str(schema.BATTERY_DIR).endswith("tests/chatbot/pv2/battery")


def test_load_valid_battery(tmp_path):
    _write(tmp_path, "stock.yaml", [_conv(), _conv("stock-r02", source="real", origin="crew/x.tsv T1")])
    convs = schema.load_battery(tmp_path)
    assert [c.id for c in convs] == ["stock-r01", "stock-r02"]
    c = convs[1]
    assert c.source == "real" and c.origin == "crew/x.tsv T1" and c.domain == "stock"
    assert c.audience == "staff" and c.shapes == ["refine"] or tuple(c.shapes) == ("refine",)
    assert c.turns[0].text == "check stock srtwc286"
    assert c.turns[0].expect["kind"] == "answer"
    assert "stock.yaml" in str(c.file)


def test_file_domain_default_applies(tmp_path):
    conv = _conv()
    del conv["domain"]
    _write(tmp_path, "eta.yaml", [conv], domain="eta")
    assert schema.load_battery(tmp_path)[0].domain == "eta"


def test_load_ignores_contacts_and_calibration(tmp_path):
    _write(tmp_path, "stock.yaml", [_conv()])
    (tmp_path / "contacts.yaml").write_text("audiences: {}\n", encoding="utf-8")
    (tmp_path / "calibration.yaml").write_text("[]\n", encoding="utf-8")
    assert len(schema.load_battery(tmp_path)) == 1


@pytest.mark.parametrize("over,needle", [
    ({"domain": "bogus"}, "domain"),
    ({"audience": "martian"}, "audience"),
    ({"shapes": ["nonsense"]}, "shape"),
    ({"turns": []}, "turns"),
    ({"id": "Bad_ID"}, "id"),
    ({"source": "real"}, "origin"),
])
def test_battery_errors_name_file_and_id(tmp_path, over, needle):
    _write(tmp_path, "stock.yaml", [_conv(**over)])
    with pytest.raises(schema.BatteryError) as ei:
        schema.load_battery(tmp_path)
    msg = str(ei.value)
    assert "stock.yaml" in msg
    assert needle in msg.lower()
    if "id" not in over:
        assert "stock-r01" in msg


def test_unknown_kind_and_missing_about(tmp_path):
    bad_kind = _conv(turns=[{"text": "hi", "expect": {"kind": "shrug", "about": "x"}}])
    _write(tmp_path, "stock.yaml", [bad_kind])
    with pytest.raises(schema.BatteryError, match="kind"):
        schema.load_battery(tmp_path)
    no_about = _conv(turns=[{"text": "hi", "expect": {"kind": "answer"}}])
    _write(tmp_path, "stock.yaml", [no_about])
    with pytest.raises(schema.BatteryError, match="about"):
        schema.load_battery(tmp_path)


def test_duplicate_id_across_files(tmp_path):
    _write(tmp_path, "stock.yaml", [_conv()])
    _write(tmp_path, "eta.yaml", [_conv(domain="eta")], domain="eta")
    with pytest.raises(schema.BatteryError, match="stock-r01"):
        schema.load_battery(tmp_path)


def _many(n, **over):
    return [schema.Conversation(
        id=f"c{i}", source="improvised", origin=None, domain="stock", audience="staff",
        shapes=["refine"], turns=[schema.Turn("t", {"kind": "any", "about": "x"})], file="f.yaml", **over)
        for i in range(n)]


def test_validate_coverage_flags_a_tiny_battery():
    problems = schema.validate_coverage(_many(3))
    assert problems and all(isinstance(p, str) for p in problems)
    joined = " ".join(problems).lower()
    assert "60" in joined and "real" in joined


def test_contacts_ok_and_missing_audience(tmp_path):
    p = tmp_path / "contacts.yaml"
    full = {a: {"respond_id": f"{100000000 + i}", "note": "fits"} for i, a in enumerate(schema.AUDIENCES)}
    p.write_text(yaml.safe_dump({"audiences": full}), encoding="utf-8")
    got = schema.load_contacts(p)
    assert set(got) == set(schema.AUDIENCES) and got["staff"] == "100000000"
    del full["dealer"]
    p.write_text(yaml.safe_dump({"audiences": full}), encoding="utf-8")
    with pytest.raises(schema.BatteryError, match="dealer"):
        schema.load_contacts(p)


def test_load_calibration(tmp_path):
    p = tmp_path / "calibration.yaml"
    rows = [{"id": "cal-1", "history": [], "text": "t", "reply": "r",
             "expect": {"kind": "answer", "about": "x"}, "label": "pass", "why": "ok"}]
    p.write_text(yaml.safe_dump(rows), encoding="utf-8")
    assert schema.load_calibration(p)[0]["label"] == "pass"


# ---- the real battery (data written by the other tester) ----

def test_real_battery_loads_and_covers():
    convs = schema.load_battery(schema.BATTERY_DIR)
    assert schema.validate_coverage(convs) == []


def test_real_battery_point_into_answer_has_one_and_many_product_documents():
    convs = [c for c in schema.load_battery(schema.BATTERY_DIR) if "point_into_answer" in c.shapes]
    assert len(convs) >= 3


def test_real_contacts_and_calibration():
    contacts = schema.load_contacts(schema.BATTERY_DIR / "contacts.yaml")
    assert set(contacts) == set(schema.AUDIENCES)
    cal = schema.load_calibration(schema.BATTERY_DIR / "calibration.yaml")
    assert len(cal) >= 20
    assert sum(1 for r in cal if r["label"] == "pass") >= 8
    assert sum(1 for r in cal if r["label"] == "fail") >= 8
    for r in cal:
        assert {"id", "history", "text", "reply", "expect", "label", "why"} <= set(r)


def test_real_battery_has_no_phone_numbers_or_contact_ids():
    phone = re.compile(r"(?<!\d)(?:\+?60|0)1\d[- ]?\d{7,8}(?!\d)|\+\d{10,}")
    nine = re.compile(r"(?<![\w-])\d{9}(?![\w-])")
    for f in sorted(schema.BATTERY_DIR.glob("*.yaml")):
        if f.name == "contacts.yaml":
            continue
        text = f.read_text(encoding="utf-8")
        assert not phone.search(text), f"phone-like number in {f.name}"
        assert not nine.search(text), f"9-digit id in {f.name}"
