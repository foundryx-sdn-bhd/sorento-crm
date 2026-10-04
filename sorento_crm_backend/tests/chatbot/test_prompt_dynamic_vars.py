"""PROMPT-DYNAMIC S2/S3: registry variables render from their tables at request time
(PLAN-prompt-dynamic-30sep D1-D6; UAC AC-PD-1..AC-PD-5).

Runs on the real migrated database (`pg_session`, rolled back). `session.commit()` inside
it commits only the session's own transaction, which is what fires the `after_commit`
cache hook under test.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.ai_prompt import AIPromptVersion
from app.models.chatbot_policy import ChatbotDomain, ChatbotStatusWord
from app.services import ai_prompt_registry, chatbot_prompt_vars
from app.services.ai_prompt_registry import PROMPT_KEYS, render, validate_template
from tests._pg_fixture import pg_session

KEY = "chatbot_semantic_parser"


@pytest.fixture(autouse=True)
def _fresh_cache():
    chatbot_prompt_vars.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    chatbot_prompt_vars.clear_cache()
    ai_prompt_registry.bust_cache()


def _version(db, template: str) -> AIPromptVersion:
    top = db.execute(text("SELECT coalesce(max(version), 0) FROM ai_prompt_versions WHERE name = :n"), {"n": KEY}).scalar()
    row = AIPromptVersion(
        id=str(uuid.uuid4()), name=KEY, version=int(top) + 1, template=template,
        commit_message="test", config_json={},
    )
    db.add(row)
    db.flush()
    return row


def _names(db) -> list[str]:
    return list(db.execute(text("SELECT name FROM chatbot_domains ORDER BY sort_order, name")).scalars())


def test_parser_key_declares_every_registry_variable():
    assert set(PROMPT_KEYS[KEY].registry_variables) == {
        "domains", "domain_words", "domains_detail", "statuses", "status_values", "order_status_values",
        "status_field_values",
        "entity_kinds", "entity_kinds_detail", "specs", "brands", "teams", "agents",
        "access_levels",
    }


def test_registry_tokens_are_known_and_never_missing():
    unknown, missing = validate_template(KEY, "no lists here {{current_date}}")
    assert unknown == [] and missing == []
    unknown, _ = validate_template(KEY, "{{domains}} {{statuses}} {{current_date}}")
    assert unknown == []
    unknown, _ = validate_template(KEY, "{{not_a_registry}} {{current_date}}")
    assert unknown == ["not_a_registry"]


def test_render_fills_domains_from_the_table():
    with pg_session() as db:
        v = _version(db, "ONE of: {{domains}} | null. Date {{current_date}}")
        out, version = render(db, KEY, current_date="2026-09-30", override_version_id=v.id)
        assert out == f"ONE of: {' | '.join(_names(db))} | null. Date 2026-09-30"
        assert "sales" in _names(db)
        assert version == v.version


def test_a_committed_domain_reaches_the_next_render_without_a_publish():
    with pg_session() as db:
        v = _version(db, "{{domains}} {{current_date}}")
        before, _ = render(db, KEY, current_date="d", override_version_id=v.id)
        name = f"zzt_{uuid.uuid4().hex[:8]}"
        db.add(ChatbotDomain(name=name, label=name, sort_order=999))
        db.commit()
        after, version = render(db, KEY, current_date="d", override_version_id=v.id)
        assert name not in before
        assert f"| {name} " in after
        assert version == v.version  # same version: no publish happened


def test_the_cache_serves_until_a_registry_commit(monkeypatch):
    calls = {"n": 0}
    real = chatbot_prompt_vars.VARIABLES["domains"]

    def counting(db):
        calls["n"] += 1
        return real.render(db)

    monkeypatch.setitem(
        chatbot_prompt_vars.VARIABLES, "domains",
        chatbot_prompt_vars.RegistryVariable(
            real.name, real.label, real.source, real.href, real.tables, counting, real.count
        ),
    )
    with pg_session() as db:
        chatbot_prompt_vars.render_value(db, "domains")
        chatbot_prompt_vars.render_value(db, "domains")
        assert calls["n"] == 1
        db.execute(text("SELECT 1"))
        db.commit()  # a commit that wrote no registry row keeps the cache
        chatbot_prompt_vars.render_value(db, "domains")
        assert calls["n"] == 1
        db.add(ChatbotStatusWord(domain="order", value=f"zzt_{uuid.uuid4().hex[:6]}", label="x"))
        db.commit()
        chatbot_prompt_vars.render_value(db, "domains")
        assert calls["n"] == 2


def test_a_removed_token_is_not_put_back():
    with pg_session() as db:
        v = _version(db, "domain_hint = ONE of: my own words | null {{current_date}}")
        out, _ = render(db, KEY, current_date="d", override_version_id=v.id)
        assert out == "domain_hint = ONE of: my own words | null d"


def test_status_words_are_seeded_with_the_sales_words():
    with pg_session() as db:
        rows = {
            r.value: (r.domain, list(r.trigger_words))
            for r in db.query(ChatbotStatusWord).all()
        }
        for value in ("outstanding", "delivered", "so_outstanding", "do_outstanding",
                      "outstanding_both", "sales_report", "sales_analysis", "top_selling"):
            assert value in rows, value
        sales_words = {w for v in ("sales_report", "sales_analysis", "top_selling") for w in rows[v][1]}
        assert {"sales", "sales report", "top selling", "sales analysis", "best selling"} <= sales_words
        assert rows["sales_report"][0] == "sales"


def test_statuses_render_every_tagged_row_with_its_words():
    """Owner answer 4 (2 Oct 2026): `{{statuses}}` and `{{status_values}}` render the rows
    tagged for them (`prompt_lists`), each with its words; an untagged row stays out."""
    with pg_session() as db:
        value = f"zzt_{uuid.uuid4().hex[:6]}"
        hidden = f"zzt_{uuid.uuid4().hex[:6]}"
        db.add(ChatbotStatusWord(domain="order", value=value, label="a test status",
                                 trigger_words=["zzt word"], sort_order=999,
                                 prompt_lists=["statuses", "status_values"]))
        db.add(ChatbotStatusWord(domain="order", value=hidden, label="not listed",
                                 trigger_words=["zzt hidden"], sort_order=1000))
        db.flush()
        # Arrows are padded and lines wrapped to the owner's layout (test_prompt_dynamic_formats),
        # so compare with the whitespace folded.
        out = " ".join(chatbot_prompt_vars.render_statuses(db).split())
        assert f'- "{value}" -> a test status: "zzt word".' in out
        assert '- "outstanding" -> orders NOT yet delivered: "outstanding", "pending"' in out
        assert hidden not in out and '"sales_report"' not in out
        values = chatbot_prompt_vars.VARIABLES["status_values"].render(db).split("|")
        assert values[0] == "outstanding" and value in values and hidden not in values


def test_domain_words_are_the_curated_list_and_fall_back_to_the_union_without_it():
    """Owner answer 2 (2 Oct 2026): the curated `chatbot_domain_words` list; an install
    without it falls back to every switch word and status word."""
    with pg_session() as db:
        words = chatbot_prompt_vars.VARIABLES["domain_words"].render(db)
        assert words.startswith("stock, incoming, ETA") and "top selling" not in words
        db.execute(text("DELETE FROM chatbot_domain_words"))
        db.flush()
        words = chatbot_prompt_vars.VARIABLES["domain_words"].render(db)
        assert "top selling" in words and "stock" in words


def test_teams_and_agents_fall_back_to_the_code_lists_on_an_empty_table():
    from app.modules.chatbot.lane_vocabulary import escalation_teams, suggested_agents

    with pg_session() as db:
        db.execute(text("DELETE FROM agent_teams"))
        db.execute(text("DELETE FROM access_agents"))
        assert chatbot_prompt_vars.VARIABLES["teams"].render(db) == "|".join(escalation_teams())
        assert chatbot_prompt_vars.VARIABLES["agents"].render(db) == "|".join(suggested_agents())


def test_access_levels_render_as_the_json_list_the_wording_had():
    with pg_session() as db:
        db.execute(text("DELETE FROM contact_access_types"))
        for i, name in enumerate(["Sorento Dealer", "Mocha Dealer", "End User"]):
            db.execute(
                text("INSERT INTO contact_access_types (code, name, is_active, sort_order, keywords, created_at, updated_at) "
                     "VALUES (:c, :n, true, :s, '[]', now(), now())"),
                {"c": f"zzt{i}", "n": name, "s": i},
            )
        assert chatbot_prompt_vars.VARIABLES["access_levels"].render(db) == '["Sorento Dealer","Mocha Dealer","End User"]'


def test_describe_lists_every_source_with_count_and_usage():
    with pg_session() as db:
        rows = {r["name"]: r for r in chatbot_prompt_vars.describe(db, "x {{domains}} y")}
        assert set(rows) == set(chatbot_prompt_vars.VARIABLE_NAMES)
        assert rows["domains"]["used"] is True and rows["brands"]["used"] is False
        assert rows["domains"]["count"] == len(_names(db))
        assert rows["domains"]["href"] == "/system-management/chatbot-domains"
        assert rows["domains"]["last_changed"] is not None
