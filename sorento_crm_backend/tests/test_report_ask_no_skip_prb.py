"""AC-B10 (config, no code): no client-sent param can skip the report limit."""
from __future__ import annotations

import pytest

from app.services import rate_limit
from tests._sales_report_do_seed import client, db  # noqa: F401  (fixtures)
from tests.test_report_ask_rate_limit_setting import _settings_row
from tests.test_report_ask_route import _ask, _full


@pytest.mark.parametrize("name", ["refine", "uncounted", "skip_limit", "no_count"])
def test_b10_a_skip_the_limit_param_is_refused_not_honoured(client, db, monkeypatch, name):
    n = {"hits": 0}

    def _hit(bucket, ident, *, limit, window_seconds):
        n["hits"] += 1
        return rate_limit.RateResult(allowed=True)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    _settings_row(db, report_ask_rate_limit=10, report_ask_rate_window_minutes=10)
    resp = _ask(client, _full(db), **{name: "true"})
    assert resp.status_code == 422 and resp.json().get("code") == "unknown_param", resp.text
    assert n["hits"] == 0
