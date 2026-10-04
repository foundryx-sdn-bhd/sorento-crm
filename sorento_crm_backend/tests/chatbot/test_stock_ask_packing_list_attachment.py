"""Chatbot stock ask v2 S3, AC-SA314: an `incoming` entry with a packing list
attaches it to the reply via a `send_attachments` action; without one, no
attachment action. `_stock_ask_packing_list_files` is the seam engine.py's
`tasks_after_reply` call site reads (see engine.py near `_attachments_src`);
`_send_actions` is the existing action-list builder every other domain's
attachment already flows through - this proves the two compose correctly.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models.base import set_company_scope
from app.services.chatbot.engine import _send_actions, _stock_ask_packing_list_files
from app.services.chatbot.turn.compose import Answer
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.inventory_service import StockService

from tests._mc_lookup_seed import product, stock
from tests._pg_fixture import blank_session
# AC-002/`tests/chatbot/test_import_boundary.py`: only a file under `tests/chatbot/` may
# import `app.services.chatbot` - so the seed helpers Blocking 2/3's tests already built in
# `tests/test_stock_availability_block.py` are reused from here, not reimplemented.
from tests.test_stock_availability_block import (
    _attachment,
    _category_of,
    _contact,
    _entry,
    _incoming_line,
    _incoming_shipment,
    _policy_row,
    _wh,
)


def _envelope(entries):
    return {"stock_availability": entries}


def _incoming_entry(**over):
    base = {
        "product_id": "p1",
        "product_code": "SRTW2000",
        "needs_quantity": False,
        "requested_qty": 150,
        "branch": "incoming",
        "eta": "19/10/2026",
        "packing_list": {
            "filename": "packing-list.pdf",
            "file_path": "/attachments/pl.pdf",
            "mime_type": "application/pdf",
        },
    }
    base.update(over)
    return base


def test_incoming_entry_with_packing_list_yields_one_file():
    files = _stock_ask_packing_list_files([_envelope([_incoming_entry()])])
    assert files == [
        {
            "url": "/attachments/pl.pdf",
            "filename": "packing-list.pdf",
            "mimeType": "application/pdf",
        }
    ]


def test_incoming_entry_without_packing_list_yields_nothing():
    """Toggle off (`packing_list_allowed` false): the entry carries no
    `packing_list` key at all, gated server-side - this reads that, no file."""
    entry = _incoming_entry(packing_list=None)
    assert _stock_ask_packing_list_files([_envelope([entry])]) == []


def test_non_incoming_branches_never_attach_even_with_a_stray_packing_list():
    for branch in ("too_big", "in_stock", "no_incoming"):
        entry = _incoming_entry(branch=branch)
        assert _stock_ask_packing_list_files([_envelope([entry])]) == []


def test_multi_product_turn_attaches_only_the_incoming_ones():
    entries = [
        _incoming_entry(product_code="SRT5674", branch="in_stock", packing_list=None),
        _incoming_entry(product_code="SRTW2000", branch="incoming"),
    ]
    files = _stock_ask_packing_list_files([_envelope(entries)])
    assert len(files) == 1
    assert files[0]["url"] == "/attachments/pl.pdf"


def test_send_actions_emits_send_attachments_when_answer_files_carries_the_packing_list():
    files = _stock_ask_packing_list_files([_envelope([_incoming_entry()])])
    answer = Answer(text="SRTW2000 x 150: ❌ ETA 19/10/2026.")
    answer.files.extend(files)
    reply = {
        "text": answer.text,
        "quick_replies": None,
        "result_set": None,
        "attachments_src": answer.files or None,
    }

    actions = _send_actions(reply, dry_run=False)

    assert [a["kind"] for a in actions] == ["send_message", "send_attachments"]
    assert actions[1]["attachments_src"] == files


def test_send_actions_emits_no_attachment_action_without_one():
    reply = {
        "text": "SRT5674 x 150: ❌ No stock and no incoming. Please refer to your salesman.",
        "quick_replies": None,
        "result_set": None,
        "attachments_src": None,
    }

    actions = _send_actions(reply, dry_run=False)

    assert [a["kind"] for a in actions] == ["send_message"]


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def test_b3_toggle_off_produces_no_send_attachments_action(db):
    """Should fix 2, review round 1 (AC-SA314): every test above feeds
    `_stock_ask_packing_list_files` and `_send_actions` hand-built synthetic
    entries - none proves that a REAL B3 entry, resolved by `StockService` for a
    contact whose `packing_list_allowed` is off, ends the same way. The engine's
    `tasks_after_reply` call site (`engine.py`) attaches whatever `packing_list` an
    `incoming` entry carries; it is safe today only because `inventory_service.py`
    is the sole producer of that key - this wires the server's own gate into the
    engine's own actions, end to end."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 200
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    att = _attachment(db)
    shipment = _incoming_shipment(db, eta=date(2026, 10, 19), attachment_id=att.id)
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id)
    contact = _contact(db, packing_list_allowed=False)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 5}
    )
    entry = _entry(result, p.id)
    assert entry["branch"] == "incoming"
    assert entry["packing_list"] is None

    files = _stock_ask_packing_list_files([{"stock_availability": result["stock_availability"]}])
    assert files == []

    reply = {
        "text": "SRTW2000 x 5: ❌ ETA 19/10/2026.",
        "quick_replies": None,
        "result_set": None,
        "attachments_src": files or None,
    }
    actions = _send_actions(reply, dry_run=False)
    assert [a["kind"] for a in actions] == ["send_message"]
