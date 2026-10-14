"""AC-CANCEL-CRM R6: the chatbot's SO status reader reports ordered and delivered net of a
derived cancelled qty.

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md. Lives here, not in
`tests/test_autocount_cancel_effects.py`, because it imports `app.services.chatbot` directly
and only `tests/chatbot/` may (`tests/chatbot/test_import_boundary.py`, AC-002).
"""
from __future__ import annotations

from decimal import Decimal

from app.services.chatbot.so_status import _net_of_cancel


def test_chatbot_reads_ordered_and_delivered_net_of_the_cancel():
    assert _net_of_cancel(Decimal("10"), Decimal("10"), Decimal("6")) == (Decimal("4"),
                                                                          Decimal("4"))
    assert _net_of_cancel(Decimal("10"), Decimal("3"), None) == (Decimal("10"), Decimal("3"))
