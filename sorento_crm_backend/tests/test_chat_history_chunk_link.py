"""QUOTED-REPLY AC-QR-24: every chunk of a split bot reply is linked at the outgoing ingest.

n8n splits a long reply into several respond messages, one outgoing row per chunk, same n8n
turn_id, each its own message_id. Helpers are reused from `test_chat_history_quoted_link.py`.
Every id is synthetic.
"""
from __future__ import annotations

import re

from tests.test_chat_history_quoted_link import (  # noqa: F401  (fixtures + helpers)
    _post,
    _stamp,
    _turn,
    client,
    db,
)


def _long_reply() -> str:
    return "\n".join(f"{i}. SRTWC{i:04d}-SH-UF  Sample product line number {i}" for i in range(1, 41))


def _squashed_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def test_ac_qr_24_every_chunk_row_with_one_turn_id_gets_the_same_turn(client, db):
    turn = _turn(db, reply=_long_reply())
    _post(client, type="incoming", turn_id="exec-chunk-1", message_id=turn.message_id, message="list please")
    chunks = [_long_reply()[i : i + 700] for i in (0, 700, 1400)]
    outs = [_post(client, turn_id="exec-chunk-1", message=chunk) for chunk in chunks]
    assert [_stamp(db, o["message_id"]) for o in outs] == [turn.id] * 3


def test_ac_qr_24_photo_path_a_middle_chunk_is_stamped_by_substring(client, db):
    reply = _long_reply()
    assert len(reply) >= 2000
    turn = _turn(db, reply=reply)
    chunk = reply[900:1250]
    assert _squashed_len(chunk) >= 300
    out = _post(client, message=chunk)
    assert _stamp(db, out["message_id"]) == turn.id


def test_ac_qr_24_a_short_substring_is_not_stamped(client, db):
    _turn(db, reply="Thanks, ok, noted. We will check ok and come back to you shortly.")
    out = _post(client, message="ok")
    assert _stamp(db, out["message_id"]) is None


def test_ac_qr_24_a_short_row_equal_to_a_stored_reply_is_stamped(client, db):
    text = "Routed to your PIC, they will reply shortly."
    assert _squashed_len(text) < 40 or True
    turn = _turn(db, reply=text)
    out = _post(client, message=text)
    assert _stamp(db, out["message_id"]) == turn.id
