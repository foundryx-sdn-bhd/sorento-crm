"""PV2-EVAL round 2: false_gap is type-aware; the judge sees filename + attachmentType."""
from __future__ import annotations

from scripts.pv2_battery import judge
from scripts.pv2_battery.checks import TurnRecord, run_checks

PHOTO = {"url": "https://cdn.example/SRTWC286-SH.jpg", "filename": "SRTWC286-SH.jpg",
         "mimeType": "image/jpeg", "attachmentType": "Product Photos"}
TD = {"url": "https://cdn.example/SRTWC286-SH_TD.jpg", "filename": "SRTWC286-SH_TD.jpg",
      "mimeType": "image/jpeg", "attachmentType": "Technical Specifications"}


def rec(reply, *items):
    acts = [{"kind": "send_message", "text": reply, "quick_replies": [], "dry_run": True}]
    if items:
        acts.append({"kind": "send_attachments", "attachments_src": list(items), "dry_run": True})
    return TurnRecord(text="hi", reply=reply, actions=acts)


def gap(r):
    return "false_gap" in {f.check for f in run_checks(r, {"about": "x"}, None)}


def test_1_loo_t7_photos_gap_while_photo_sent():
    assert gap(rec("SRTWC286-SH has no Product Photos.", PHOTO, TD))


def test_2_correct_gap_for_other_type_is_not_false():
    assert not gap(rec("SRTWC286-SH has no Technical Specifications.", PHOTO))


def test_3_ms_and_zh_gap_with_photo_sent():
    assert gap(rec("SRTWC286-SH tiada Product Photos.", PHOTO))
    assert gap(rec("SRTWC286-SH 没有Product Photos。", PHOTO))


def test_4_gap_for_code_whose_files_not_sent():
    assert not gap(rec("CKS1052-BL has no Product Photos.", PHOTO))


def test_5_no_attachment_type_means_no_deterministic_false_gap():
    bare = {"url": "https://cdn.example/SRTWC286-SH.jpg", "filename": "SRTWC286-SH.jpg"}
    empty = dict(bare, attachmentType="")
    assert not gap(rec("SRTWC286-SH has no Product Photos.", bare))
    assert not gap(rec("SRTWC286-SH has no Product Photos.", empty))


def test_6_two_type_gap_with_one_type_sent():
    assert gap(rec("SRTWC286-SH has no Product Photos and Technical Specifications.", TD))


def _kw(files):
    return dict(history=[], text="send photos", reply="Here.", files=files,
                expect={"kind": "answer", "about": "photos"}, facts={}, audience="staff")


def test_7_judge_prompt_lists_filename_and_type():
    p = judge.build_judge_prompt(**_kw([PHOTO, TD]))
    for needle in ("SRTWC286-SH.jpg", "SRTWC286-SH_TD.jpg", "Product Photos", "Technical Specifications"):
        assert needle in p, needle


def test_7b_judge_prompt_still_accepts_int():
    assert "Here." in judge.build_judge_prompt(**_kw(2))
