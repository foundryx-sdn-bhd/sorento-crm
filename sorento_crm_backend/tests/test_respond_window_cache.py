"""The 24h-window lookup is cached for a few seconds (in-process TTL).

``get_window_state`` calls Respond.io's message list on EVERY caller: opening
one ticket drawer resolves the window for the header, and a send moments later
resolves it again, each a live 15s-timeout HTTP call for an answer that changes
on a DAY scale. A sub-minute cache removes the duplicates without ever hiding a
real window transition.

Run:
    venv/bin/pytest tests/test_respond_window_cache.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import respond_messaging_service as rms


@pytest.fixture(autouse=True)
def _clean_cache():
    rms.reset_window_cache()
    yield
    rms.reset_window_cache()


class _CountingClient:
    def __init__(self, items):
        self.items = items
        self.calls = 0

    def list_messages(self, identifier, limit=50):
        self.calls += 1
        return {"items": self.items}


def _recent_incoming():
    ms = int((datetime.utcnow() - timedelta(hours=2)).timestamp() * 1000)
    return [{"traffic": "incoming", "messageId": 1, "status": [{"timestamp": ms}]}]


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(
        "app.services.integration_service.RespondClient", lambda *a, **k: client
    )


def test_two_lookups_inside_the_ttl_hit_respond_once(monkeypatch):
    client = _CountingClient(_recent_incoming())
    _patch_client(monkeypatch, client)

    first = rms.get_window_state(None, "900000008")
    second = rms.get_window_state(None, "900000008")

    assert client.calls == 1, "the second lookup must be served from the cache"
    assert first["open"] is True
    assert second["open"] is True
    assert second["last_incoming_at"] == first["last_incoming_at"]


def test_the_lookup_repeats_once_the_ttl_has_passed(monkeypatch):
    client = _CountingClient(_recent_incoming())
    _patch_client(monkeypatch, client)

    clock = {"t": 1000.0}
    monkeypatch.setattr(rms, "_monotonic", lambda: clock["t"])

    rms.get_window_state(None, "900000008")
    clock["t"] += rms.WINDOW_CACHE_TTL_SECONDS + 1
    rms.get_window_state(None, "900000008")

    assert client.calls == 2


def test_each_contact_gets_its_own_entry(monkeypatch):
    client = _CountingClient(_recent_incoming())
    _patch_client(monkeypatch, client)

    rms.get_window_state(None, "900000008")
    rms.get_window_state(None, "10025531")

    assert client.calls == 2


def test_checked_at_is_recomputed_on_a_cache_hit(monkeypatch):
    """The cache holds the upstream FACT (last incoming message), not the
    verdict: openness and checked_at are recomputed per call, so a window that
    lapses between two cached lookups still reads closed."""
    old_ms = int((datetime.utcnow() - timedelta(hours=30)).timestamp() * 1000)
    client = _CountingClient([{"traffic": "incoming", "messageId": 1, "status": [{"timestamp": old_ms}]}])
    _patch_client(monkeypatch, client)

    first = rms.get_window_state(None, "900000008")
    second = rms.get_window_state(None, "900000008")

    assert client.calls == 1
    assert first["open"] is False and second["open"] is False
    assert second["checked_at"] >= first["checked_at"]


def test_reset_window_cache_forces_a_fresh_lookup(monkeypatch):
    client = _CountingClient(_recent_incoming())
    _patch_client(monkeypatch, client)

    rms.get_window_state(None, "900000008")
    rms.reset_window_cache()
    rms.get_window_state(None, "900000008")

    assert client.calls == 2
