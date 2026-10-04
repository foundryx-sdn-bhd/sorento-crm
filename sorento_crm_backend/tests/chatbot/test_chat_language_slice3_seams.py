"""CHAT-LANGUAGE slice 3 fix round, B1: the two engine-side reprint seams.

(a) `business.run_fetch`, the `outstanding_detail_reask` branch: an out-of-range number against an
open detail offer re-prints the stored (English) offer in the turn's language.
(b) the engine's ask path: which localizer a re-asked `outstanding_detail` question uses
(`engine._ask_localizer`).
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.chatbot import engine, label_catalog
from app.services.chatbot.label_catalog import IDENTITY, Localizer
from app.services.chatbot.lanes import business

OFFER = "Reply with a number for detail:\n1. Sales order list\n2. Delivery order list\n3. Both lists"
ROWS = [
    {"idx": 1, "label": "Sales order list", "value": "so"},
    {"idx": 2, "label": "Delivery order list", "value": "do"},
    {"idx": 3, "label": "Both lists", "value": "both"},
]
MS_OFFER = (
    "Balas dengan nombor untuk butiran:\n1. Senarai pesanan jualan\n"
    "2. Senarai pesanan penghantaran\n3. Kedua-dua senarai"
)


def _run_fetch(localizer):
    ctx = {
        "parse": {
            "output": {
                "outstanding_detail_reask": {
                    "kind": "outstanding_detail",
                    "rows": ROWS,
                    "filters": {"offer_text": OFFER, "product_code": "SRTWC286"},
                }
            }
        },
        "localizer": localizer,
    }
    return business.run_fetch({"ctx": ctx, "gate": {}}, services=SimpleNamespace())


def test_a_run_fetch_reasks_the_open_detail_offer_in_the_turns_language():
    out = _run_fetch(Localizer("ms", label_catalog.defaults("ms")))
    assert out["fetch"]["response"] == MS_OFFER
    # The stored offer and the roster stay English.
    ask = out["fetch"]["outstanding_ask"]
    assert ask["filters"]["offer_text"] == OFFER
    assert ask["last_result_set"] == ROWS


def test_a_run_fetch_reask_without_a_localizer_is_english():
    assert _run_fetch(None)["fetch"]["response"] == OFFER


def test_b_the_ask_path_uses_the_turns_language(monkeypatch):
    seen = []

    def fake_resolve(db, lang, *, dry_run=False):
        seen.append((lang, dry_run))
        return Localizer(lang, label_catalog.defaults(lang))

    monkeypatch.setattr(label_catalog, "resolve", fake_resolve)
    ask = SimpleNamespace(kind="outstanding_detail")
    loc = engine._ask_localizer(ask, object(), {"reply_language": "ms"}, dry_run=True)
    assert loc.lines("Both lists") == "Kedua-dua senarai"
    assert seen == [("ms", True)]
    # No language on the item reads as English (the identity localizer, via resolve).
    monkeypatch.setattr(label_catalog, "resolve", lambda db, lang, *, dry_run=False: IDENTITY if lang == "en" else None)
    assert engine._ask_localizer(ask, object(), {}, dry_run=False) is IDENTITY
