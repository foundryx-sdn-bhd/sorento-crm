"""SO-TRANSFERABLE: AutoCount's sales-order header `Transferable` (T/F) on the ingest surface.

  AC-TR-1  `transferable: "F"` stores `is_transferable = false`, `"T"` true; JSON bools too
  AC-TR-2  a re-push that omits it (or sends null) leaves the stored value alone
  AC-TR-3  a value that is not a boolean word fails the record and writes nothing
  AC-TR-4  read-back answers `transferable`
  AC-TR-5  a push that never stated it leaves the column NULL (unknown, not "T")

The field is AutoCount-owned (plan D3): written here and nowhere else.
"""
from __future__ import annotations

import random

import pytest

from tests.test_ingest_documents import (  # noqa: F401  (`env` is a fixture)
    INGEST_SO,
    READ_SO,
    _so_record,
    env,
)


@pytest.mark.parametrize(
    "sent,stored",
    [("F", False), ("T", True), ("f", False), ("t", True), (False, False), (True, True)],
)
def test_the_flag_is_stored_as_autocount_states_it(env, sent, stored):
    """AC-TR-1. AutoCount prints T/F; the ESB may pass it through raw or as a JSON bool."""
    record = _so_record(env, transferable=sent)

    res = env.post(INGEST_SO, [record])

    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["outcome"] == "created", res.text
    header = env.header("sales_orders", record["source_ref"])
    assert header["is_transferable"] is stored


def test_a_push_that_never_states_it_leaves_the_column_unknown(env):
    """AC-TR-5. NULL is "AutoCount never told us", which is not the same fact as "T"."""
    record = _so_record(env)

    env.post(INGEST_SO, [record])

    assert env.header("sales_orders", record["source_ref"])["is_transferable"] is None


@pytest.mark.parametrize("repush", [{}, {"transferable": None}])
def test_a_repush_that_omits_it_keeps_the_stored_value(env, repush):
    """AC-TR-2. An ESB build that does not send the field must not blank a stated F."""
    record = _so_record(env, transferable="F")
    env.post(INGEST_SO, [record])

    again = {k: v for k, v in record.items() if k != "transferable"}
    again.update(repush)
    res = env.post(INGEST_SO, [again])

    assert res.json()["records"][0]["outcome"] in {"updated", "unchanged"}, res.text
    assert env.header("sales_orders", record["source_ref"])["is_transferable"] is False


def test_a_later_push_can_flip_it(env):
    """AC-TR-1. F means "not confirmed for the queue yet" (owner, 1 Oct 2026); once AutoCount
    marks the order T the next push says so and the order counts again."""
    record = _so_record(env, transferable="F")
    env.post(INGEST_SO, [record])

    env.post(INGEST_SO, [{**record, "transferable": "T"}])

    assert env.header("sales_orders", record["source_ref"])["is_transferable"] is True


def test_a_value_that_is_not_a_boolean_word_fails_the_record(env):
    """AC-TR-3. Storing a guess would decide whether an order is demand."""
    record = _so_record(env, transferable="X")

    res = env.post(INGEST_SO, [record])

    entry = res.json()["records"][0]
    assert entry["outcome"] == "failed", res.text
    assert env.header("sales_orders", record["source_ref"]) is None


@pytest.mark.parametrize("sent,read_back", [("F", False), ("T", True), (None, None)])
def test_read_back_answers_transferable(env, sent, read_back):
    """AC-TR-4. The ESB's compare reads what Sorento holds under the payload's own name."""
    extra = {} if sent is None else {"transferable": sent}
    record = _so_record(env, **extra)
    env.post(INGEST_SO, [record])

    res = env.read(READ_SO, [record["source_ref"]])

    assert res.status_code == 200, res.text
    found = res.json()["records"]
    assert len(found) == 1, res.text
    assert "transferable" in found[0]
    assert found[0]["transferable"] is read_back


def test_the_ss_v2_contract_shape_true_false_and_omitted(env):
    """Contract pin for ss#106 (SS-SO-TRANSFERABLE): SorentoSink sends `transferable` as a JSON
    BOOLEAN on the sales-order header, and OMITS the key when AutoCount's value is null.

    One batch in the v2 shape the sink pushes (DocKey refs, `customer_code`/`customer_name`,
    `doc_date`, no `status` - v2 derives it): `false` stores False (not transferable, out of
    Stock Debt and the ladder), `true` stores True, an omitted key stores NULL (unknown,
    counted like T).
    """

    def v2_record(doc_key: int, **extra) -> dict:
        record = {
            "source_ref": f"ZZTDOC:SO:{doc_key}",
            "so_number": f"ZZTDOC-SO{doc_key}",
            "customer_code": f"ZZTDOC-C{doc_key}",
            "customer_name": f"ZZTDOC Customer {doc_key}",
            "doc_date": "2026-09-28",
            "lines": [
                {
                    "source_ref": f"ZZTDOC:SO:{doc_key}:1",
                    "product_ref": env.product_ref,
                    "qty_ordered": 10,
                    "qty_delivered": 0,
                    "required_date": "2026-11-15",
                }
            ],
        }
        record.update(extra)
        return record

    base = random.randint(10_000_000, 99_999_999)
    sent_false = v2_record(base, transferable=False)
    sent_true = v2_record(base + 1, transferable=True)
    omitted = v2_record(base + 2)
    assert "transferable" not in omitted

    res = env.post(INGEST_SO, [sent_false, sent_true, omitted])

    assert res.status_code == 200, res.text
    assert [r["outcome"] for r in res.json()["records"]] == ["created"] * 3, res.text
    stored = {
        key: env.header("sales_orders", rec["source_ref"])["is_transferable"]
        for key, rec in (("false", sent_false), ("true", sent_true), ("omitted", omitted))
    }
    assert stored == {"false": False, "true": True, "omitted": None}
