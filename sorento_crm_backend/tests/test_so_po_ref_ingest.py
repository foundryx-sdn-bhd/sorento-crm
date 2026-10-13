"""SO-PO-REF ingest (U1-U6): `po_ref` is its own canonical header field.

  U1  stored trimmed as sent, `po_ref_key` = fold
  U2  a re-push overwrites both
  U3  a re-push that SENDS it blank clears both
  U4  a re-push with NO key leaves both alone
  U5  never touches `project_label` / `project_label_source`
  U6  over 255 characters is rejected like `ref`
"""
from __future__ import annotations

from .test_ingest_documents import INGEST_SO, _so_record, env  # noqa: F401


def _po(env, record):
    row = env.header("sales_orders", record["source_ref"])
    assert row is not None, "the order must exist before its PO ref can be read"
    return row["po_ref"], row["po_ref_key"]


def test_u1_create_stores_trimmed_raw_and_folded_key(env):
    record = _so_record(env, po_ref="  PO-4500 123 ")

    res = env.post(INGEST_SO, [record])

    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["outcome"] == "created", res.text
    assert _po(env, record) == ("PO-4500 123", "4500123")


def test_u2_repush_overwrites_both_columns(env):
    record = _so_record(env, po_ref="PO-4500 123")
    env.post(INGEST_SO, [record])

    res = env.post(INGEST_SO, [{**record, "po_ref": "4500777"}])

    assert res.json()["records"][0]["outcome"] == "updated", res.text
    assert _po(env, record) == ("4500777", "4500777")


def test_u3_repush_sent_blank_clears_both(env):
    for blank in ("", "   "):
        record = _so_record(env, po_ref="PO-4500 123")
        env.post(INGEST_SO, [record])

        res = env.post(INGEST_SO, [{**record, "po_ref": blank}])

        assert res.json()["records"][0]["outcome"] in {"updated", "unchanged"}, res.text
        assert _po(env, record) == (None, None), blank


def test_u4_repush_without_the_key_leaves_both_untouched(env):
    record = _so_record(env, po_ref="PO-4500 123")
    env.post(INGEST_SO, [record])

    again = {k: v for k, v in record.items() if k != "po_ref"}
    res = env.post(INGEST_SO, [again])

    assert res.json()["records"][0]["outcome"] in {"updated", "unchanged"}, res.text
    assert _po(env, record) == ("PO-4500 123", "4500123")


def test_u5_po_ref_does_not_touch_the_project_label(env):
    record = _so_record(env, po_ref="4500123")
    record["ref"] = "THE MET KL"

    env.post(INGEST_SO, [record])

    row = env.header("sales_orders", record["source_ref"])
    assert row["project_label"] == "THE MET KL"
    assert row["project_label_source"] == "ref"
    assert row["po_ref"] == "4500123"


def test_u5_an_so_with_only_po_ref_gets_no_project(env):
    record = _so_record(env, po_ref="4500123")

    env.post(INGEST_SO, [record])

    row = env.header("sales_orders", record["source_ref"])
    assert row["po_ref"] == "4500123"
    assert row["project_label"] is None
    assert row["project_label_source"] is None


def test_u6_over_255_characters_fails_the_record(env):
    record = _so_record(env, po_ref="9" * 256)

    res = env.post(INGEST_SO, [record])

    assert res.json()["records"][0]["outcome"] == "failed", res.text
    assert env.header("sales_orders", record["source_ref"]) is None


def test_u6_exactly_255_is_accepted(env):
    record = _so_record(env, po_ref="9" * 255)

    res = env.post(INGEST_SO, [record])

    assert res.json()["records"][0]["outcome"] == "created", res.text
    assert _po(env, record)[0] == "9" * 255
