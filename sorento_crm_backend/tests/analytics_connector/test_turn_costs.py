"""CHAT-TURN-COST: per-turn token + cost datasets in the analytics connector.

Plan: `documentation/plans/analytics/PLAN-chat-turn-cost-13oct.md`. Two datasets join the chat
catalog: `turn_costs` (one row per live chatbot turn, every LLM call of the turn summed and priced)
and `llm_calls` (one row per `ai_assistant_usage_logs` row, priced). Prices come from
`llm_model_prices`; an unpriced model gives `cost_usd = NULL, priced = false`, never 0.

Runs on the bootstrapped database (`pg_session`); every row is ZZT-marked and rolled back.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests._pg_fixture import pg_session, unique_code
from tests.analytics_connector._helpers import BASE, CHAT_VIEW, USE, client_for, make_user, signed

T0 = datetime(2026, 9, 2, 2, 0, 0, tzinfo=timezone.utc)  # 10:00 Malaysia


def _ensure(db) -> None:
    from app.services.analytics_connector.views import ensure_cost_views, ensure_views

    ensure_views(db)
    ensure_cost_views(db)


def _price(db, model: str, inp: str, cached: str | None, out: str) -> None:
    db.execute(
        text("INSERT INTO llm_model_prices (model, input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m) "
             "VALUES (:m, :i, :c, :o) ON CONFLICT (model) DO UPDATE SET input_usd_per_1m = EXCLUDED.input_usd_per_1m, "
             "cached_input_usd_per_1m = EXCLUDED.cached_input_usd_per_1m, output_usd_per_1m = EXCLUDED.output_usd_per_1m"),
        {"m": model, "i": inp, "c": cached, "o": out},
    )


def _usage(db, *, turn_id, contact_id, feature, model, prompt, cached, completion, ms, provider="openai"):
    uid = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO ai_assistant_usage_logs (id, contact_id, feature, provider, model, prompt_tokens, "
             "cached_tokens, completion_tokens, total_tokens, tool_calls_count, response_time_ms, was_answered, "
             "chatbot_turn_id, created_at) VALUES (:id, :c, :f, :p, :m, :pt, :ct, :co, :tt, 0, :ms, true, :t, :at)"),
        {"id": uid, "c": contact_id, "f": feature, "p": provider, "m": model, "pt": prompt, "ct": cached,
         "co": completion, "tt": prompt + completion, "ms": ms, "t": turn_id, "at": T0.replace(tzinfo=None)},
    )
    return uid


def _trace(domain="stock", intent="availability", version=52, cached=None):
    facts = {"message_type": "request", "domain": domain, "intent": intent, "prompt_version": version,
             "tokens": 38300, "prompt_tokens": 38000, "completion_tokens": 300}
    if cached is not None:
        facts["cached_tokens"] = cached
    return [
        {"stage": "received", "status": "ok", "facts": {}},
        {"stage": "understood", "status": "ok", "facts": facts, "raw": {"parser_raw": {"secret": "x"}}},
        {"kind": "tool", "name": "crm_stock", "at": "2026-09-02T02:00:01Z"},
    ]


def _seed(db) -> dict:
    tag = unique_code("tc", alpha=True)
    m_priced, m_unpriced = f"zzt-priced-{tag}", f"zzt-unpriced-{tag}"
    _price(db, m_priced, "0.40", "0.10", "1.60")

    customer_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO customers (id, customer_code, customer_name, is_active) VALUES (:id, :code, :name, true)"),
        {"id": customer_id, "code": unique_code("cust")[:30], "name": f"ZZT Customer {tag}"},
    )
    cid, rid = unique_code("rc"), f"rio-{uuid.uuid4().hex[:12]}"
    db.execute(
        text("INSERT INTO respond_contacts (id, phone_number, name, respond_io_id) VALUES (:id, :p, :n, :r)"),
        {"id": cid, "p": "+60100009999", "n": f"ZZT Contact {tag}", "r": rid},
    )
    db.execute(text("INSERT INTO respond_contact_customers (id, contact_id, customer_id, is_primary) "
                    "VALUES (:id, :c, :cu, true)"),
               {"id": str(uuid.uuid4()), "c": cid, "cu": customer_id})

    def turn(*, is_test=False, trace=None, branch="business", reply="here you go", at=T0, secs=4):
        tid = str(uuid.uuid4())
        envelope = {"message": {"message": {"message": {"type": "text", "text": f"stock {tag} code is 123456"}}},
                    "contact": {"phone": "+60100009999"}}
        db.execute(
            text("INSERT INTO chatbot.turns (id, contact_respond_id, envelope, status, stage, branch_kind, is_test, "
                 "response, trace, created_at, started_at, finished_at) VALUES (:id, :c, CAST(:e AS jsonb), 'done', "
                 "'replied', :b, :t, CAST(:r AS jsonb), CAST(:tr AS jsonb), :at, :at, :f)"),
            {"id": tid, "c": rid, "e": json.dumps(envelope), "b": branch, "t": is_test,
             "r": json.dumps({"reply": {"text": reply}}), "tr": json.dumps(trace or _trace()),
             "at": at, "f": at + timedelta(seconds=secs)},
        )
        return tid

    priced = turn(trace=_trace(cached=36000))
    _usage(db, turn_id=priced, contact_id=cid, feature="chatbot_parser", model=m_priced,
           prompt=38000, cached=36000, completion=300, ms=2500)
    _usage(db, turn_id=priced, contact_id=cid, feature="chatbot_clarifier", model=m_priced,
           prompt=1000, cached=0, completion=100, ms=700)

    unpriced = turn(trace=_trace(domain="order", intent="status"), at=T0 + timedelta(minutes=5))
    _usage(db, turn_id=unpriced, contact_id=cid, feature="chatbot_parser", model=m_unpriced,
           prompt=38000, cached=0, completion=300, ms=2500)

    no_calls = turn(trace=[{"stage": "received", "status": "ok", "facts": {}}], at=T0 + timedelta(minutes=9))
    test_turn = turn(is_test=True, at=T0 + timedelta(minutes=10))
    _usage(db, turn_id=test_turn, contact_id=cid, feature="chatbot_parser", model=m_priced,
           prompt=10, cached=0, completion=1, ms=1)

    topic = f"zzt.{tag}"
    db.execute(text("INSERT INTO chatbot.chat_topics (code, label, definition, version) VALUES (:c, :l, 'zzt', 99)"),
               {"c": topic, "l": f"ZZT topic {tag}"})

    frame_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO conversation_frames (id, contact_id, space_id, channel, contact_respond_id, domain, intent, "
             "status, turn_ids, is_test) VALUES (:id, 'x', 's', 'whatsapp', :c, 'stock', 'availability', 'closed', "
             "ARRAY[:t1, :t2], false)"),
        {"id": frame_id, "c": cid, "t1": priced, "t2": unpriced},
    )
    for tid, domain, intent, version, episode in ((priced, "stock", "availability", 52, frame_id),
                                                    (unpriced, "order", "status", 51, None)):
        db.execute(
            text("INSERT INTO chatbot.chat_ask_facts (id, source, turn_id, ask_seq, contact_respond_id, contact_id, "
                 "customer_id, audience, contact_type, asked_at, domain, intent, outcome, branch_kind, episode_id, "
                 "topic_code, prompt_version) VALUES (:id, 'trace', :t, 1, :r, :c, :cu, 'dealer', 'dealer', :at, :d, :i, "
                 "'answered', 'business', :e, :tp, :v)"),
            {"id": str(uuid.uuid4()), "t": tid, "r": rid, "c": cid, "cu": customer_id, "at": T0, "d": domain,
             "i": intent, "e": episode, "tp": topic if tid == priced else None, "v": version},
        )
    db.flush()
    return {"tag": tag, "topic": topic, "contact_id": cid, "respond_id": rid, "customer_id": customer_id, "frame_id": frame_id,
            "priced": priced, "unpriced": unpriced, "no_calls": no_calls, "test": test_turn,
            "m_priced": m_priced, "m_unpriced": m_unpriced}


def _row(db, view: str, key: str, value: str) -> dict:
    r = db.execute(text(f"SELECT * FROM analytics_connector.{view} WHERE {key} = :v"), {"v": value}).mappings().all()
    assert len(r) == 1, r
    return dict(r[0])


# ------------------------------------------------------------------ views


def test_turn_costs_sums_every_llm_call_of_a_turn_and_prices_it():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        row = _row(db, "turn_costs", "id", s["priced"])
        assert row["contact_id"] == s["contact_id"]
        assert row["contact_respond_id"] == s["respond_id"]
        assert row["contact_name"] == f"ZZT Contact {s['tag']}"
        assert row["customer_name"] == f"ZZT Customer {s['tag']}"
        assert row["episode_id"] == s["frame_id"]
        assert row["branch_kind"] == "business"
        assert row["status"] == "done"
        assert row["domain"] == "stock" and row["intent"] == "availability"
        assert row["topic_code"] == s["topic"] and row["topic_label"] == f"ZZT topic {s['tag']}"
        assert row["outcome"] == "answered"
        assert row["prompt_version"] == 52
        assert row["model"] == s["m_priced"]
        assert row["llm_calls"] == 2
        assert row["prompt_tokens"] == 39000
        assert row["cached_tokens"] == 36000
        assert row["completion_tokens"] == 400
        assert row["total_tokens"] == 39400
        assert row["llm_ms"] == 3200
        assert row["latency_ms"] == 4000
        # parser: (2000*0.40 + 36000*0.10 + 300*1.60)/1e6 = 0.00488; clarifier: (1000*0.40 + 100*1.60)/1e6 = 0.00056
        assert row["cost_usd"] == Decimal("0.005440")
        assert row["priced"] is True
        # Malaysia wall clock, like every other connector view.
        assert row["created_at"].replace(tzinfo=None) == datetime(2026, 9, 2, 10, 0, 0)


def test_unpriced_model_gives_null_cost_never_zero():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        row = _row(db, "turn_costs", "id", s["unpriced"])
        assert row["cost_usd"] is None and row["priced"] is False
        assert row["domain"] == "order" and row["intent"] == "status" and row["prompt_version"] == 51
        assert row["episode_id"] is None and row["topic_code"] is None
        call = _row(db, "llm_calls", "chatbot_turn_id", s["unpriced"])
        assert call["cost_usd"] is None and call["priced"] is False


def test_a_turn_with_one_unpriced_call_costs_null_not_a_partial_sum():
    """Q3 (a): a priced parser plus an unpriced clarifier must not report the parser's cost
    as the turn's cost."""
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        _usage(db, turn_id=s["priced"], contact_id=s["contact_id"], feature="chatbot_clarifier",
               model=s["m_unpriced"], prompt=100, cached=0, completion=10, ms=5)
        db.flush()
        row = _row(db, "turn_costs", "id", s["priced"])
        assert row["llm_calls"] == 3
        assert row["cost_usd"] is None and row["priced"] is False


def test_provider_name_is_matched_case_insensitively():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        model = f"zzt-claude-ci-{s['tag']}"
        _price(db, model, "2.00", "0.10", "10.00")
        _usage(db, turn_id=s["no_calls"], contact_id=s["contact_id"], feature="chatbot_parser", model=model,
               prompt=1000, cached=5000, completion=100, ms=10, provider=" Anthropic ")
        db.flush()
        assert _row(db, "llm_calls", "chatbot_turn_id", s["no_calls"])["cost_usd"] == Decimal("0.003500")


def test_turn_without_llm_calls_costs_zero():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        row = _row(db, "turn_costs", "id", s["no_calls"])
        assert row["llm_calls"] == 0 and row["prompt_tokens"] == 0 and row["total_tokens"] == 0
        assert row["cost_usd"] == 0 and row["priced"] is True
        assert row["domain"] is None and row["prompt_version"] is None and row["outcome"] is None


def test_test_turns_are_not_in_turn_costs():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        n = db.execute(text("SELECT count(*) FROM analytics_connector.turn_costs WHERE id = :i"), {"i": s["test"]}).scalar()
        assert n == 0


def test_turn_costs_masks_otp_in_inbound_text():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        row = _row(db, "turn_costs", "id", s["priced"])
        assert s["tag"] in row["inbound_text"]
        assert row["reply_text"] == "here you go"


def test_llm_calls_prices_each_call():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        rows = db.execute(
            text("SELECT feature, model, prompt_tokens, cached_tokens, completion_tokens, response_time_ms, cost_usd, "
                 "priced, contact_name FROM analytics_connector.llm_calls WHERE chatbot_turn_id = :t ORDER BY feature"),
            {"t": s["priced"]},
        ).mappings().all()
        assert [r["feature"] for r in rows] == ["chatbot_clarifier", "chatbot_parser"]
        assert rows[0]["cost_usd"] == Decimal("0.000560") and rows[1]["cost_usd"] == Decimal("0.004880")
        assert all(r["priced"] for r in rows)
        assert rows[1]["contact_name"] == f"ZZT Contact {s['tag']}"


def test_anthropic_prompt_tokens_exclude_cache_reads():
    """Anthropic's `input_tokens` does not include `cache_read_input_tokens` (llm_provider.py), so
    the uncached part is the whole prompt count there, not prompt minus cached."""
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        model = f"zzt-claude-{s['tag']}"
        _price(db, model, "2.00", "0.10", "10.00")
        tid = s["no_calls"]
        _usage(db, turn_id=tid, contact_id=s["contact_id"], feature="chatbot_parser", model=model,
               prompt=1000, cached=5000, completion=100, ms=10, provider="anthropic")
        db.flush()
        call = _row(db, "llm_calls", "chatbot_turn_id", tid)
        # (1000*2.00 + 5000*0.10 + 100*10.00)/1e6 = 0.0035
        assert call["cost_usd"] == Decimal("0.003500")


def test_missing_cached_price_falls_back_to_input_price():
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        model = f"zzt-nocache-{s['tag']}"
        _price(db, model, "1.00", None, "2.00")
        _usage(db, turn_id=s["no_calls"], contact_id=s["contact_id"], feature="chatbot_parser", model=model,
               prompt=1000, cached=500, completion=0, ms=10)
        db.flush()
        assert _row(db, "llm_calls", "chatbot_turn_id", s["no_calls"])["cost_usd"] == Decimal("0.001000")


def test_llm_calls_holds_chatbot_calls_only():
    """Security review: the CRM gates staff-side AI spend behind `system.ai_assistant_settings.view`;
    the connector's chat gate must not widen that, so `llm_calls` carries chatbot rows only."""
    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        staff = _usage(db, turn_id=None, contact_id=None, feature="ai_assistant", model=s["m_priced"],
                       prompt=5, cached=0, completion=5, ms=5)
        extract = _usage(db, turn_id=None, contact_id=s["contact_id"], feature="ai_extract", model=s["m_priced"],
                         prompt=5, cached=0, completion=5, ms=5)
        db.flush()
        n = db.execute(text("SELECT count(*) FROM analytics_connector.llm_calls WHERE id IN (:a, :b)"),
                       {"a": staff, "b": extract}).scalar()
        assert n == 0
        assert db.execute(text("SELECT count(*) FROM analytics_connector.llm_calls WHERE chatbot_turn_id = :t"),
                          {"t": s["priced"]}).scalar() == 2


def test_read_only_role_reads_the_cost_views_and_no_base_table():
    from app.services.analytics_connector.views import grant_read_role

    role = f"zzt_ro_{uuid.uuid4().hex[:8]}"
    with pg_session() as db:
        _ensure(db)
        db.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        db.execute(text(f'GRANT "{role}" TO CURRENT_USER'))
        grant_read_role(db, role)
        db.execute(text("SAVEPOINT ro"))
        db.execute(text(f'SET LOCAL ROLE "{role}"'))
        db.execute(text("SELECT count(*) FROM analytics_connector.turn_costs")).scalar()
        db.execute(text("SELECT count(*) FROM analytics_connector.llm_calls")).scalar()
        for stmt in (
            "SELECT 1 FROM public.llm_model_prices LIMIT 1",
            "SELECT 1 FROM public.ai_assistant_usage_logs LIMIT 1",
            "SELECT 1 FROM chatbot.chat_ask_facts LIMIT 1",
            "SELECT 1 FROM chatbot.chat_topics LIMIT 1",
            "UPDATE analytics_connector.llm_calls SET model = 'x'",
        ):
            db.execute(text("SAVEPOINT probe"))
            with pytest.raises(Exception) as err:
                db.execute(text(stmt))
            msg = str(err.value).lower()
            assert "permission denied" in msg or "cannot update view" in msg, stmt
            db.execute(text("ROLLBACK TO SAVEPOINT probe"))
        db.execute(text("ROLLBACK TO SAVEPOINT ro"))
        for view in ("turn_costs", "llm_calls"):
            for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                held = db.execute(text("SELECT has_table_privilege(:r, :v, :p)"),
                                  {"r": role, "v": f"analytics_connector.{view}", "p": priv}).scalar()
                assert held is False, (view, priv)


def test_cost_views_expose_no_secret_columns():
    with pg_session() as db:
        _ensure(db)
        names = set(db.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics_connector' "
            "AND table_name IN ('turn_costs', 'llm_calls')"
        )).scalars())
        assert names, "views missing"
        for forbidden in ("phone_number", "phone", "session_vars", "envelope", "trace", "user_id", "api_key"):
            assert forbidden not in names, forbidden


# ------------------------------------------------------------------ prices + backfill


def test_seeded_prices_are_only_the_sourced_ones():
    from app.services.analytics_connector import prices

    assert set(prices.SEED_PRICES) == {"gpt-5.4-mini", "gpt-4.1-mini", "claude-sonnet-5-5"}
    with pg_session() as db:
        prices.seed_prices(db)
        prices.seed_prices(db)  # idempotent
        got = db.execute(text(
            "SELECT input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m FROM llm_model_prices WHERE model = 'gpt-5.4-mini'"
        )).one()
        assert tuple(got) == (Decimal("0.75"), Decimal("0.075"), Decimal("4.5"))


def test_seed_never_overwrites_an_edited_price():
    from app.services.analytics_connector import prices

    with pg_session() as db:
        prices.seed_prices(db)
        db.execute(text("UPDATE llm_model_prices SET input_usd_per_1m = 9 WHERE model = 'gpt-4.1-mini'"))
        prices.seed_prices(db)
        assert db.execute(text("SELECT input_usd_per_1m FROM llm_model_prices WHERE model = 'gpt-4.1-mini'")).scalar() == 9


def test_backfill_takes_cached_tokens_from_the_turn_trace():
    from app.services.analytics_connector import backfill

    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        legacy = _usage(db, turn_id=s["priced"], contact_id=s["contact_id"], feature="chatbot_parser",
                        model=s["m_priced"], prompt=1, cached=0, completion=1, ms=1)
        no_fact = _usage(db, turn_id=s["no_calls"], contact_id=s["contact_id"], feature="chatbot_parser",
                         model=s["m_priced"], prompt=1, cached=0, completion=1, ms=1)
        db.execute(text("UPDATE ai_assistant_usage_logs SET cached_tokens = NULL WHERE id IN (:a, :b)"),
                   {"a": legacy, "b": no_fact})
        backfill.backfill_cached_tokens(db)
        got = dict(db.execute(text("SELECT id::text, cached_tokens FROM ai_assistant_usage_logs WHERE id IN (:a, :b)"),
                              {"a": legacy, "b": no_fact}).all())
        assert got[legacy] == 36000
        assert got[no_fact] == 0


def test_backfill_takes_prompt_version_from_the_turn_trace():
    from app.services.analytics_connector import backfill

    with pg_session() as db:
        _ensure(db)
        s = _seed(db)
        db.execute(text("UPDATE chatbot.chat_ask_facts SET prompt_version = NULL WHERE turn_id IN (:a, :b)"),
                   {"a": s["priced"], "b": s["unpriced"]})
        backfill.backfill_prompt_version(db)
        got = dict(db.execute(text("SELECT turn_id::text, prompt_version FROM chatbot.chat_ask_facts "
                                   "WHERE turn_id IN (:a, :b)"), {"a": s["priced"], "b": s["unpriced"]}).all())
        assert got == {s["priced"]: 52, s["unpriced"]: 52}


# ------------------------------------------------------------------ writers


def test_parser_usage_row_carries_cached_tokens():
    from app.services.chatbot.usage import record_parser_usage

    with pg_session() as db:
        turn_id = str(uuid.uuid4())
        record_parser_usage(
            db, usage={"provider": "openai", "model": "gpt-5.4-mini", "prompt_tokens": 100, "completion_tokens": 5,
                       "total_tokens": 105, "cached_tokens": 64},
            response_time_ms=12, contact_respond_id="rio-none", answered=True, chatbot_turn_id=turn_id,
        )
        row = db.execute(text("SELECT feature, cached_tokens FROM ai_assistant_usage_logs WHERE chatbot_turn_id = :t"),
                         {"t": turn_id}).one()
        assert tuple(row) == ("chatbot_parser", 64)


def test_clarifier_usage_row_is_recorded_for_the_turn():
    from app.services.chatbot.usage import CLARIFIER_FEATURE, record_turn_llm_usage

    assert CLARIFIER_FEATURE == "chatbot_clarifier"
    with pg_session() as db:
        turn_id = str(uuid.uuid4())
        record_turn_llm_usage(
            db, feature=CLARIFIER_FEATURE,
            usage={"provider": "openai", "model": "gpt-5.4-mini", "prompt_tokens": 900, "completion_tokens": 40,
                   "total_tokens": 940, "cached_tokens": 0},
            response_time_ms=30, contact_respond_id="rio-none", answered=True, chatbot_turn_id=turn_id,
        )
        row = db.execute(text("SELECT feature, prompt_tokens, completion_tokens FROM ai_assistant_usage_logs "
                              "WHERE chatbot_turn_id = :t"), {"t": turn_id}).one()
        assert tuple(row) == ("chatbot_clarifier", 900, 40)


def test_llm_call_capture_collects_usage_of_calls_made_inside_it(monkeypatch):
    from app.services.chatbot import llm_call
    from app.services.llm_provider import ChatResult

    class Fake:
        def chat(self, messages, model=None, **kw):
            return ChatResult(content="{}", prompt_tokens=10, completion_tokens=2, total_tokens=12,
                              tool_calls=[], raw=None, cached_tokens=4)

    monkeypatch.setattr("app.services.llm_provider.get_provider", lambda *a, **k: Fake())
    llm_call.chat("openai", "k", "gpt-x", [])  # outside a capture: nothing collected, nothing breaks
    with llm_call.capture_usage() as calls:
        llm_call.chat("openai", "k", "gpt-x", [])
    assert calls == [{"provider": "openai", "model": "gpt-x", "prompt_tokens": 10, "completion_tokens": 2,
                      "total_tokens": 12, "cached_tokens": 4}]


# ------------------------------------------------------------------ over the connector


@pytest.fixture()
def caller(monkeypatch):
    with pg_session() as db:
        with client_for(db, monkeypatch) as client:
            _ensure(db)
            seed = _seed(db)
            user = make_user(db, "analyst", perms=(USE, CHAT_VIEW))

            def get(path):
                return signed(client, "GET", path, user.id)

            def query(**body):
                return signed(client, "POST", f"{BASE}/query", user.id, json_body=body, tool="chat_query")

            yield seed, get, query


def test_describe_documents_every_cost_column(caller):
    _s, get, _q = caller
    for key in ("turn_costs", "llm_calls"):
        resp = get(f"{BASE}/datasets/{key}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["description"]
        cols = {c["key"]: c for c in body["columns"]}
        assert all(c["description"] for c in cols.values()), key
        assert cols["cost_usd"]["type"] == "number" and cols["cost_usd"]["measurable"]
        assert cols["prompt_tokens"]["measurable"] and cols["cached_tokens"]["measurable"]
        assert not {"phone", "phone_number", "contact_phone"} & set(cols)
    tc = {c["key"] for c in get(f"{BASE}/datasets/turn_costs").json()["columns"]}
    assert {"domain", "intent", "topic_code", "topic_label", "outcome", "prompt_version", "model", "llm_calls",
            "latency_ms", "episode_id", "branch_kind", "inbound_text", "contact_name", "customer_name"} <= tc


def test_cost_per_category_over_the_connector(caller):
    s, _g, query = caller
    resp = query(dataset="turn_costs", group_by=["domain", "intent"],
                 filters=[{"column": "contact_id", "op": "eq", "value": s["contact_id"]}],
                 measures=[{"fn": "count"}, {"fn": "sum", "column": "cost_usd"}, {"fn": "sum", "column": "total_tokens"},
                           {"fn": "avg", "column": "latency_ms"}])
    assert resp.status_code == 200, resp.text
    rows = {(r[0], r[1]): r[2:] for r in resp.json()["rows"]}
    assert rows[("stock", "availability")][0] == 1
    assert rows[("stock", "availability")][1] == pytest.approx(0.00544)
    assert rows[("order", "status")][1] is None  # the only turn is unpriced


def test_cost_filters_compare_numbers(caller):
    s, _g, query = caller
    resp = query(dataset="turn_costs", columns=["id"],
                 filters=[{"column": "contact_id", "op": "eq", "value": s["contact_id"]},
                          {"column": "cost_usd", "op": "gt", "value": 0.001}])
    assert resp.status_code == 200, resp.text
    assert [r[0] for r in resp.json()["rows"]] == [s["priced"]]


@pytest.mark.parametrize("value", ["lots", "nan", "inf", "-Infinity", "1e400", True, False, [1], {"a": 1}])
def test_cost_filters_reject_what_is_not_a_finite_number(caller, value):
    _s, _g, query = caller
    bad = query(dataset="turn_costs", filters=[{"column": "cost_usd", "op": "gt", "value": value}])
    assert bad.status_code == 422, value


def test_list_datasets_includes_the_cost_datasets(caller):
    _s, get, _q = caller
    keys = {d["key"] for d in get(f"{BASE}/datasets").json()["datasets"]}
    assert {"turn_costs", "llm_calls"} <= keys


def test_manifest_offers_the_cost_datasets():
    from app.services.analytics_connector.manifest import build_manifest

    tools = {t["name"]: t for t in build_manifest()["tools"]}
    assert {"turn_costs", "llm_calls"} <= set(tools["chat_query"]["input_schema"]["properties"]["dataset"]["enum"])
    assert "turn_costs" in tools["chat_list_datasets"]["description"]
