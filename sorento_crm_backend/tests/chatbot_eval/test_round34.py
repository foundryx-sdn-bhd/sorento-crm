"""EVAL-BENCH round 34 (owner c04/c19): a certificate ask naming a scheme wants that scheme's certificates.

The file envelope carries no scheme, so the gold names the scheme's files: `cert_scheme` and `scheme_files` (fileHash list).
Synthetic data only.
"""
from __future__ import annotations

from scripts.chatbot_eval.targets import grade_turn
from tests.chatbot_eval.test_targets import _att, item


def _cert(file_hash):
    return {"filename": f"{file_hash}.pdf", "url": "u", "mimeType": "application/pdf",
            "attachmentType": "Certification", "fileHash": file_hash}


GOLD = dict(attachment_type="certification", attachment_type_name="Certification", attachment_type_names=["span"],
            cert_scheme="SPAN", scheme_files=["h1", "h2"])


def _grade(hashes, gold=GOLD):
    return grade_turn(_att([_cert(h) for h in hashes], asked="span"), item("attachment", **gold))


def test_red_a_certificate_of_another_scheme_fails():
    res = _grade(["h9"])
    assert res["passed"] is False, res["checks"]
    assert any(ok is False for name, ok in res["checks"].items() if name not in {"asked_type", "files", "type"}), res["checks"]


def test_red_a_mix_of_the_scheme_and_another_scheme_fails():
    res = _grade(["h1", "h9"])
    assert res["passed"] is False, res["checks"]


def test_guard_a_mix_in_the_other_order_fails_too():
    res = _grade(["h9", "h1"])
    assert res["passed"] is False, res["checks"]
    assert res["checks"]["scheme"] is False, res["checks"]


def test_guard_the_schemes_own_certificates_pass():
    res = _grade(["h1", "h2"])
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_guard_a_gold_without_a_scheme_accepts_any_certification_file():
    gold = {k: v for k, v in GOLD.items() if k not in {"cert_scheme", "scheme_files"}}
    res = _grade(["h9"], gold)
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_guard_a_scheme_with_no_files_on_record_and_an_honest_none_passes():
    gold = dict(GOLD, scheme_files=[], none_on_record=True)
    res = grade_turn(_att([], asked="span", reply="There is no SPAN certificate on record for SRTA100."),
                     item("attachment", **gold))
    assert res["passed"] is True, (res["reason"], res["checks"])
