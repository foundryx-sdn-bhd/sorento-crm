"""CLAUDE-CONNECTOR r3: chat datasets, the read-only role and the chat tools (card sections 4.3, 5, 6).

Ported from r2 onto the new paths (`/api/v1/connector/*`) and the SS-signed call auth
(contract section 3); the data rules are unchanged.

Runs on the bootstrapped database (`pg_session`), which holds the `analytics_connector`
views (`scripts/bootstrap_env.py` creates them, like the scm views). Every row is created
here with a ZZT marker and rolled back.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.services.otp_redaction import mask_otp_text
from tests._pg_fixture import pg_session, unique_code
from tests.analytics_connector._helpers import (
    BASE,
    CHAT_VIEW,
    USE,
    client_for,
    make_user,
    signed,
    tool_call_rows,
)

T0 = datetime(2026, 9, 1, 2, 0, 0)  # naive UTC = 10:00 in Malaysia


def _seed(db, tag: str) -> dict:
    """One company, one customer, two contacts with messages, a bot turn and an episode."""
    company_id = str(uuid.uuid4())
    customer_id = str(uuid.uuid4())
    db.execute(text("INSERT INTO companies (id, name, code) VALUES (:id, :n, :c)"),
               {"id": company_id, "n": f"ZZT Co {tag}", "c": unique_code("co")[:20]})
    db.execute(
        text("INSERT INTO customers (id, customer_code, customer_name, is_active) VALUES (:id, :code, :name, true)"),
        {"id": customer_id, "code": unique_code("cust")[:30], "name": f"ZZT Customer {tag}"},
    )
    contacts = {}
    for label in ("a", "b"):
        cid = unique_code(f"rc{label}")
        rid = f"rio-{uuid.uuid4().hex[:12]}"
        db.execute(
            text("INSERT INTO respond_contacts (id, phone_number, name, respond_io_id, chatbot_profile) "
                 "VALUES (:id, :p, :n, :r, CAST(:prof AS jsonb))"),
            {"id": cid, "p": f"+6010000{uuid.uuid4().int % 10000:04d}", "n": f"ZZT Contact {label} {tag}",
             "r": rid, "prof": json.dumps({"tier": "gold"})},
        )
        contacts[label] = {"id": cid, "respond_io_id": rid}
    db.execute(text("INSERT INTO respond_contact_customers (id, contact_id, customer_id, is_primary) "
                    "VALUES (:id, :c, :cu, true)"),
               {"id": str(uuid.uuid4()), "c": contacts["a"]["id"], "cu": customer_id})
    db.execute(text("INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                    "VALUES (:id, :c, :co)"),
               {"id": str(uuid.uuid4()), "c": contacts["a"]["id"], "co": company_id})

    msgs = [
        ("a", "incoming", f"stock for ZZT-SKU-{tag}?", T0),
        ("a", "outgoing", f"ZZT-SKU-{tag}: got stock", T0 + timedelta(minutes=1)),
        ("a", "outgoing", "Your Sorento sign-in code is 482913", T0 + timedelta(minutes=2)),
        ("b", "incoming", f"price of ZZT-OTHER-{tag} 100%_off", T0 + timedelta(days=1)),
        ("b", "incoming", f"hello {tag}", T0 + timedelta(days=40)),
    ]
    for label, direction, body, at in msgs:
        db.execute(
            text("INSERT INTO chat_histories (channel, contact_id, phone_number, message, sent_at, type) "
                 "VALUES ('whatsapp', :c, :p, :m, :t, :d)"),
            {"c": contacts[label]["respond_io_id"], "p": "+60100000001", "m": body, "t": at, "d": direction},
        )
    turn_id = str(uuid.uuid4())
    envelope = {"message": {"message": {"message": {"type": "text", "text": f"stock for ZZT-SKU-{tag}?"}}},
                "contact": {"phone": "+60100000001", "secret": "should-not-leak"}}
    db.execute(
        text("INSERT INTO chatbot.turns (id, contact_respond_id, envelope, status, is_test, response, trace, created_at, finished_at) "
             "VALUES (:id, :c, CAST(:e AS jsonb), 'done', false, CAST(:r AS jsonb), CAST(:tr AS jsonb), :t, :f)"),
        {"id": turn_id, "c": contacts["a"]["respond_io_id"], "e": json.dumps(envelope),
         "r": json.dumps({"reply": {"text": f"ZZT-SKU-{tag}: got stock"}}),
         "tr": json.dumps([{"stage": "parse", "raw": "session_vars secret"}]),
         "t": T0.replace(tzinfo=None), "f": (T0 + timedelta(seconds=3))},
    )
    db.execute(
        text("INSERT INTO chatbot.turns (id, contact_respond_id, envelope, status, is_test) "
             "VALUES (:id, :c, '{}'::jsonb, 'done', true)"),
        {"id": str(uuid.uuid4()), "c": contacts["a"]["respond_io_id"]},
    )
    db.execute(
        text("INSERT INTO conversation_frames (id, contact_id, space_id, channel, contact_respond_id, domain, intent, "
             "status, summary, last_user_message, turn_ids, is_test) VALUES (:id, 'x', 's', 'whatsapp', :c, 'stock', "
             "'availability', 'closed', :s, :lu, ARRAY['t1','t2'], false)"),
        {"id": str(uuid.uuid4()), "c": contacts["a"]["id"], "s": f"asked stock {tag}",
         "lu": "Your Sorento verification code is 1234"},
    )
    db.flush()
    return {"tag": tag, "contacts": contacts, "customer_id": customer_id, "company_id": company_id, "turn_id": turn_id}


# ------------------------------------------------------------------ views + role


def test_views_exist_and_expose_no_secret_columns():
    with pg_session() as db:
        cols = {
            (r.table_name, r.column_name)
            for r in db.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'analytics_connector'"
            ))
        }
        views = {t for t, _ in cols}
        assert {"messages", "contacts", "bot_turns", "episodes"} <= views
        names = {c for _, c in cols}
        for forbidden in ("phone_number", "phone", "session_vars", "portal_slug", "envelope", "trace",
                          "state_trace", "password", "result"):
            assert forbidden not in names, forbidden


def test_views_mask_otp_codes_like_the_crm_does():
    from app.services.analytics_connector.views import ensure_views

    with pg_session() as db:
        ensure_views(db)  # a reused local DB must not hide a regression in the view body
        seed = _seed(db, unique_code("otp", alpha=True))
        rows = db.execute(text(
            "SELECT text FROM analytics_connector.messages WHERE contact_respond_id = :r ORDER BY sent_at"
        ), {"r": seed["contacts"]["a"]["respond_io_id"]}).scalars().all()
        assert rows[2] == mask_otp_text("Your Sorento sign-in code is 482913")
        assert "482913" not in " ".join(rows)
        ep = db.execute(text(
            "SELECT last_user_message FROM analytics_connector.episodes WHERE contact_id = :c"
        ), {"c": seed["contacts"]["a"]["id"]}).scalar()
        assert ep == mask_otp_text("Your Sorento verification code is 1234")


@pytest.mark.parametrize(
    "sample",
    [
        "Your Sorento sign-in code is 123456",
        "verification code is 99887766 thanks",
        "SIGN-IN CODE IS  4321",
        "code is 123456",  # no keyword: untouched by both
        "sign-in code is 123",  # too short: untouched by both
        "line one\nyour sign-in code is 555555\nline three",
    ],
)
def test_sql_mask_matches_python_mask(sample):
    from app.services.analytics_connector.views import OTP_MASK_SQL

    with pg_session() as db:
        got = db.execute(text(f"SELECT {OTP_MASK_SQL.format(col=':s')}"), {"s": sample}).scalar()
        assert got == mask_otp_text(sample)


def test_views_show_malaysia_time_and_skip_test_turns():
    from app.services.analytics_connector.views import ensure_views

    with pg_session() as db:
        ensure_views(db)
        seed = _seed(db, unique_code("tz", alpha=True))
        first = db.execute(text(
            "SELECT sent_at FROM analytics_connector.messages WHERE contact_respond_id = :r ORDER BY sent_at LIMIT 1"
        ), {"r": seed["contacts"]["a"]["respond_io_id"]}).scalar()
        assert first == T0 + timedelta(hours=8)
        turns = db.execute(text(
            "SELECT id, inbound_text, reply_text, latency_ms FROM analytics_connector.bot_turns WHERE contact_respond_id = :r"
        ), {"r": seed["contacts"]["a"]["respond_io_id"]}).all()
        assert len(turns) == 1
        assert turns[0].inbound_text.startswith("stock for ZZT-SKU-")
        assert turns[0].reply_text.endswith("got stock")


def test_messages_carry_contact_and_customer_names():
    with pg_session() as db:
        seed = _seed(db, unique_code("nm", alpha=True))
        row = db.execute(text(
            "SELECT contact_id, contact_name, customer_name, companies FROM analytics_connector.messages "
            "WHERE contact_respond_id = :r LIMIT 1"
        ), {"r": seed["contacts"]["a"]["respond_io_id"]}).one()
        assert row.contact_id == seed["contacts"]["a"]["id"]
        assert row.contact_name.startswith("ZZT Contact a")
        assert row.customer_name.startswith("ZZT Customer")
        assert "ZZT Co" in row.companies


def test_read_only_role_reads_views_only():
    from app.services.analytics_connector.views import grant_read_role

    role = f"zzt_ro_{uuid.uuid4().hex[:8]}"
    with pg_session() as db:
        db.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        # Membership lets the app DB user (CREATEROLE, not superuser) SET ROLE to it; the
        # transaction rolls back, so the role and the grant never outlive the test.
        db.execute(text(f'GRANT "{role}" TO CURRENT_USER'))
        grant_read_role(db, role)
        db.execute(text("SAVEPOINT ro"))
        db.execute(text(f'SET LOCAL ROLE "{role}"'))
        db.execute(text("SELECT count(*) FROM analytics_connector.messages")).scalar()
        for stmt in (
            "SELECT 1 FROM public.chat_histories LIMIT 1",
            "SELECT 1 FROM public.respond_contacts LIMIT 1",
            "SELECT 1 FROM public.users LIMIT 1",
            "SELECT 1 FROM chatbot.turns LIMIT 1",
            "DELETE FROM analytics_connector.episodes",
            "INSERT INTO analytics_connector.episodes (id) VALUES (gen_random_uuid())",
        ):
            db.execute(text("SAVEPOINT probe"))
            with pytest.raises(Exception) as err:
                db.execute(text(stmt))
            # A view that is not auto-updatable is refused before the privilege check.
            msg = str(err.value).lower()
            assert "permission denied" in msg or "cannot delete from view" in msg \
                or "cannot insert into view" in msg, stmt
            db.execute(text("ROLLBACK TO SAVEPOINT probe"))
        db.execute(text("ROLLBACK TO SAVEPOINT ro"))
        for view in ("messages", "contacts", "bot_turns", "episodes"):
            for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                held = db.execute(
                    text("SELECT has_table_privilege(:r, :v, :p)"),
                    {"r": role, "v": f"analytics_connector.{view}", "p": priv},
                ).scalar()
                assert held is False, (view, priv)


def test_ensure_views_is_idempotent():
    from app.services.analytics_connector.views import ensure_views

    with pg_session() as db:
        ensure_views(db)
        ensure_views(db)


# ------------------------------------------------------------------ bot_turns text masking (security round)


def test_bot_turn_reply_text_and_error_are_otp_masked():
    from app.services.analytics_connector.views import ensure_views

    with pg_session() as db:
        ensure_views(db)
        seed = _seed(db, unique_code("otpr", alpha=True))
        reply = "Your Sorento sign-in code is 482913"
        err = "send failed: your verification code is 777123"
        turn_id = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO chatbot.turns (id, contact_respond_id, envelope, status, is_test, response, error, created_at) "
                 "VALUES (:id, :c, '{}'::jsonb, 'done', false, CAST(:r AS jsonb), :e, :t)"),
            {"id": turn_id, "c": seed["contacts"]["a"]["respond_io_id"], "r": json.dumps({"reply": {"text": reply}}),
             "e": err, "t": T0 + timedelta(hours=1)},
        )
        db.flush()
        row = db.execute(
            text("SELECT reply_text, error FROM analytics_connector.bot_turns WHERE id = :i"), {"i": turn_id}
        ).one()
        assert row.reply_text == mask_otp_text(reply)
        assert "482913" not in row.reply_text
        assert row.error == mask_otp_text(err)
        assert "777123" not in row.error


# ------------------------------------------------------------------ the chat tools over HTTP (signed calls)


class Caller:
    """Sends SS-signed calls as one user."""

    def __init__(self, client, user):
        self.client, self.user = client, user

    def get(self, path, params=None, tool=None):
        from urllib.parse import urlencode

        url = f"{path}?{urlencode(params)}" if params else path
        # tool=None lets the helper name the tool whose manifest route this is (contract 9.2).
        claims = {} if tool is None else {"tool": tool}
        return signed(self.client, "GET", url, self.user.id, **claims)

    def query(self, tool="chat_query", **body):
        return signed(self.client, "POST", f"{BASE}/query", self.user.id, json_body=body, tool=tool)


@pytest.fixture()
def chat(monkeypatch):
    with pg_session() as db:
        with client_for(db, monkeypatch) as client:
            seed = _seed(db, unique_code("api", alpha=True))
            user = make_user(db, "analyst", perms=(USE, CHAT_VIEW))
            yield db, Caller(client, user), seed, user


def test_list_datasets(chat):
    _db, c, _seed_, _u = chat
    resp = c.get(f"{BASE}/datasets")
    assert resp.status_code == 200, resp.text
    keys = {d["key"] for d in resp.json()["datasets"]}
    assert keys == {"messages", "contacts", "bot_turns", "episodes", "turn_costs", "llm_calls"}
    msgs = next(d for d in resp.json()["datasets"] if d["key"] == "messages")
    assert msgs["row_count"] >= 5 and msgs["description"]


def test_describe_dataset(chat):
    _db, c, _seed_, _u = chat
    resp = c.get(f"{BASE}/datasets/messages")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cols = {col["key"]: col for col in body["columns"]}
    assert cols["text"]["type"] == "text" and cols["sent_at"]["type"] == "datetime"
    assert "phone_number" not in cols and "phone" not in cols
    assert body["date_column"] == "sent_at"
    assert len(body["sample"]["rows"]) <= 3
    missing = c.get(f"{BASE}/datasets/nope")
    assert missing.status_code == 404


def test_no_dataset_exposes_a_phone_column(chat):
    """Q3 (a): phone numbers are excluded from every dataset."""
    _db, c, _seed_, _u = chat
    for key in ("messages", "contacts", "bot_turns", "episodes"):
        body = c.get(f"{BASE}/datasets/{key}").json()
        names = {col["key"] for col in body["columns"]}
        assert not {"phone_number", "phone", "session_vars", "portal_slug", "envelope", "trace"} & names, key
        assert "+60100000" not in json.dumps(body), key


def test_query_detail_with_filters(chat):
    _db, c, seed, _u = chat
    resp = c.query(dataset="messages", columns=["sent_at", "direction", "text"],
                   filters=[{"column": "contact_respond_id", "op": "eq", "value": seed["contacts"]["a"]["respond_io_id"]},
                            {"column": "direction", "op": "eq", "value": "outgoing"}],
                   order_by=[{"column": "sent_at", "dir": "asc"}])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == ["sent_at", "direction", "text"]
    assert [r[1] for r in body["rows"]] == ["outgoing", "outgoing"]
    assert body["truncated"] is False
    assert "482913" not in json.dumps(body)


def test_query_group_by_with_measures_and_period(chat):
    _db, c, seed, _u = chat
    resp = c.query(
        dataset="messages",
        filters=[{"column": "text", "op": "contains", "value": seed["tag"]}],
        group_by=["direction", "sent_at:month"],
        measures=[{"fn": "count"}, {"fn": "count_distinct", "column": "contact_id"}],
        period={"from": "2026-09-01", "to": "2026-09-30"},
        order_by=[{"column": "count", "dir": "desc"}],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == ["direction", "sent_at:month", "count", "count_distinct:contact_id"]
    rows = {(r[0], r[1]): r[2] for r in body["rows"]}
    assert rows[("incoming", "2026-09")] == 2
    assert rows[("outgoing", "2026-09")] == 1


def test_contains_escapes_like_wildcards(chat):
    _db, c, seed, _u = chat
    resp = c.query(dataset="messages", columns=["text"],
                   filters=[{"column": "text", "op": "contains", "value": "100%_off"}])
    texts = [r[0] for r in resp.json()["rows"]]
    assert texts and all("100%_off" in t for t in texts)
    resp = c.query(dataset="messages", columns=["text"],
                   filters=[{"column": "text", "op": "contains", "value": "%"},
                            {"column": "contact_respond_id", "op": "eq", "value": seed["contacts"]["a"]["respond_io_id"]}])
    assert resp.json()["rows"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"dataset": "users"},
        {"dataset": "messages", "columns": ["phone_number"]},
        {"dataset": "contacts", "columns": ["phone_number"]},
        {"dataset": "messages", "columns": ["text; DROP TABLE x"]},
        {"dataset": "messages", "filters": [{"column": "text", "op": "regex", "value": "a"}]},
        {"dataset": "messages", "filters": [{"column": "nope", "op": "eq", "value": "a"}]},
        {"dataset": "messages", "group_by": ["sent_at:century"]},
        {"dataset": "messages", "group_by": ["text"], "measures": [{"fn": "sum", "column": "text"}]},
        {"dataset": "messages", "measures": [{"fn": "drop", "column": "id"}]},
        {"dataset": "messages", "limit": 5000},
        {"dataset": "messages", "limit": 1001},
        {"dataset": "messages", "order_by": [{"column": "sent_at", "dir": "sideways"}]},
        {"dataset": "messages", "period": {"from": "not-a-date"}},
    ],
)
def test_query_rejects_anything_outside_the_catalog(chat, body):
    _db, c, _seed_, _u = chat
    resp = c.query(**body)
    assert resp.status_code == 422, resp.text


def test_query_accepts_the_1000_row_cap(chat):
    _db, c, _seed_, _u = chat
    resp = c.query(dataset="messages", columns=["id"], limit=1000)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["rows"]) <= 1000


def test_output_cap_is_120000_characters():
    from app.services.analytics_connector import datasets

    assert datasets.MAX_OUTPUT_CHARS == 120_000
    assert datasets.MAX_ROWS == 1000


def test_query_caps_rows_and_pages(chat):
    _db, c, seed, _u = chat
    flt = [{"column": "text", "op": "contains", "value": seed["tag"]}]
    body = c.query(dataset="messages", columns=["id"], filters=flt,
                   order_by=[{"column": "sent_at", "dir": "asc"}], limit=2).json()
    assert len(body["rows"]) == 2 and body["truncated"] is True and body["next_offset"] == 2
    page2 = c.query(dataset="messages", columns=["id"], filters=flt,
                    order_by=[{"column": "sent_at", "dir": "asc"}], limit=2, offset=2).json()
    assert len(page2["rows"]) == 2 and not set(map(tuple, page2["rows"])) & set(map(tuple, body["rows"]))


def test_query_caps_output_characters(chat, monkeypatch):
    from app.services.analytics_connector import datasets

    db, c, seed, _u = chat
    big = "x" * 5000
    for _ in range(5):
        db.execute(text("INSERT INTO chat_histories (channel, contact_id, phone_number, message, sent_at, type) "
                        "VALUES ('whatsapp', :c, '+60100000001', :m, :t, 'incoming')"),
                   {"c": seed["contacts"]["b"]["respond_io_id"], "m": big + seed["tag"], "t": T0})
    db.flush()
    monkeypatch.setattr(datasets, "MAX_OUTPUT_CHARS", 12000)
    body = c.query(dataset="messages", columns=["text"],
                   filters=[{"column": "text", "op": "contains", "value": big[:10] + "x"}]).json()
    assert len(json.dumps(body)) <= 12000
    assert body["truncated"] is True and 0 < len(body["rows"]) < 5


def test_search_messages(chat):
    _db, c, seed, _u = chat
    resp = c.get(f"{BASE}/messages/search", {"text": f"ZZT-SKU-{seed['tag']}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "text" in body["columns"] and "contact_name" in body["columns"]
    assert "phone_number" not in body["columns"]
    assert len(body["rows"]) == 2
    sent = body["columns"].index("sent_at")
    assert body["rows"][0][sent] >= body["rows"][1][sent]  # newest first


def test_get_conversation_by_name_and_ambiguity(chat):
    _db, c, seed, _u = chat
    resp = c.get(f"{BASE}/conversation", {"contact": f"ZZT Contact a {seed['tag']}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contact"]["id"] == seed["contacts"]["a"]["id"]
    texts = [r[body["columns"].index("text")] for r in body["rows"]]
    assert texts[0].startswith("stock for")  # oldest first
    assert "482913" not in " ".join(texts)
    assert "+60100000" not in json.dumps(body)
    amb = c.get(f"{BASE}/conversation", {"contact": "ZZT Contact"}).json()
    assert len(amb["candidates"]) >= 2 and "rows" not in amb
    assert c.get(f"{BASE}/conversation", {"contact": "zzt-nobody-xyz"}).status_code == 404


def test_every_chat_call_is_audited(chat):
    db, c, _seed_, user = chat
    assert c.query(dataset="messages", columns=["id"], limit=1).status_code == 200
    rows = tool_call_rows(db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.new_values["tool"] == "chat_query"
    assert row.new_values["status"] in ("ok", 200, "200")
    assert "grant_id" in row.new_values and "ms" in row.new_values
    assert row.auth_method == "connector"
    assert row.source == "connector"
