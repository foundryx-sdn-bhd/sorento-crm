"""CA-3 review round: the subject filter refuses an unknown code, and a year-against-year
variance skips a ratio or distinct measure instead of raising."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from tests._chat_report_seed import seed_world
from tests.test_report_ask_chat_route import (  # noqa: F401  (client / db are fixtures)
    _as_contact,
    _ask,
    _code,
    _ok,
    _staff,
    client,
    db,
)


def test_an_unknown_subject_code_is_422_unknown_subject(client, db):
    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO chatbot_domains (id, name, label, intents, tools, escalation_team_code, switch_words, "
            "narrowing, takes_date_filter, supported, ladder, sort_order) "
            "SELECT gen_random_uuid(), 'zzt_inventory', 'zzt', ARRAY[]::text[], ARRAY[]::text[], 'customer_service', "
            "ARRAY[]::text[], '{}'::jsonb, false, true, '{}', 99 "
            "WHERE NOT EXISTS (SELECT 1 FROM chatbot_domains WHERE name = 'zzt_inventory')"
        )
    )
    seed_world(db)
    contact = _staff(db)
    resp = _ask(client, contact, group_by="topic", top_n=5, subjects=["zzt_nonsense"])
    assert resp.status_code == 422 and _code(resp) == "unknown_subject", resp.text
    assert _ok(client, contact, group_by="topic", top_n=5, subjects=["zzt_inventory"])["status"] == "ok"


def test_variance_skips_a_ratio_and_a_distinct_measure():
    from app.services.reports import engine
    from app.services.reports import registry as reg

    ratio = reg.Column("r", "R", "integer", "measure", lambda c: None, ratio_of=("a", "b"))
    distinct = reg.Column("d", "D", "integer", "measure", lambda c: None, agg="count_distinct")
    plain = reg.Column("a", "A", "integer", "measure", lambda c: None)
    ctx = SimpleNamespace(period=SimpleNamespace(start=date(2025, 1, 1), end_exclusive=date(2026, 4, 1)))
    cells = {
        "2026": {"2026-01": {"r": "0.5", "d": "3", "a": "4"}},
        "2025": {"2025-01": {"r": "0.4", "d": "2", "a": "1"}},
    }
    row, total = engine._variance(ctx, ["2025", "2026"], {"2026": {"01": cells["2026"]["2026-01"]}, "2025": {"01": cells["2025"]["2025-01"]}}, [ratio, distinct, plain])
    assert all(set(v) <= {"a"} for v in (row or {}).values()), row
    assert set(total or {}) <= {"a"}, total
