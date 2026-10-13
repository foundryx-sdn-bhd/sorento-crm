"""S4 - prompt-blocks staleness status endpoint (AC-1552, PLAN-chatbot-turn-rearch.md
"The policy": "the Prompts page shows 'domain block out of date'"; coordinator ruling:
staleness is HASH based, `stale = blocks_hash(current rows) != labelled_version.
config_json["blocks_hash"]` - `AIPromptVersion` already carries a JSONB `config_json`
column, `app/models/ai_prompt.py`; there is no second JSONB column, and a `metadata`
attribute would collide with SQLAlchemy's own reserved `Base.metadata`, coordinator
ruling 16 Sep 2026, superseding this file's first cut).

`GET /api/v1/system/chatbot/prompt-blocks/status` does not exist yet (no route named
`prompt-blocks` anywhere under `app/api/v1/system/chatbot.py` today - measured), so
every test is RED on a 404 - the right reason (missing route), same posture as
`test_turns_admin_api.py`'s own original red-first file.

Written against the REAL migrated database (`session_factory` fixture is the blank
scratch schema from `conftest.py`, which does NOT carry the migration-seeded
`chatbot_domains` rows - so this file uses `pg_session`, like
`test_rearch_s4_prompt_blocks.py`, and reads/writes real `ai_prompt_versions` /
`ai_prompt_labels` rows for `chatbot_semantic_parser`, rolled back by `pg_session`'s
own teardown).

**Ambiguity flagged to the captain**: whether "stale" is checked against the
`production` label specifically, or "the labelled version" generically (there could be
a `local`/`dev` label too) is not pinned - this file checks against `production`,
the label every other chatbot prompt migration in this repo treats as the one that
reaches a customer.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.services.user_service import UserPermissionService

from tests._pg_fixture import pg_session

VIEW = "system.chat_history.view"
BASE = "/api/v1/system/chatbot/prompt-blocks/status"
PROMPT_NAME = "chatbot_semantic_parser"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Staleness Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.add(VIEW)
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def pg_db():
    with pg_session() as db:
        yield db


@pytest.fixture()
def client(pg_db):
    def _override_db():
        try:
            yield pg_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _republish(db) -> None:
    """Render the current rows, publish a new version carrying them, and move
    `production` onto it - the manual-publish action AC-1552's banner exists to
    prompt."""
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services.chatbot_parser_prompt import prompt_blocks_hash, render_prompt_blocks

    rendered = render_prompt_blocks(db)
    blocks_hash = prompt_blocks_hash(db)

    next_version = (
        db.query(AIPromptVersion.version)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .order_by(AIPromptVersion.version.desc())
        .first()
    )
    new_version_number = (next_version[0] if next_version else 0) + 1
    new_version = AIPromptVersion(
        name=PROMPT_NAME,
        version=new_version_number,
        type="text",
        template=rendered,
        variables=[],
        # `config_json` is the REAL existing JSONB column (coordinator ruling, 16
        # Sep 2026) - no `metadata` attribute (it would shadow SQLAlchemy's own
        # reserved `Base.metadata` on every declarative model).
        config_json={"blocks_hash": blocks_hash},
    )
    db.add(new_version)
    db.flush()

    label = (
        db.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )
    if label is None:
        label = AIPromptLabel(name=PROMPT_NAME, label="production", version_id=new_version.id)
        db.add(label)
    else:
        label.version_id = new_version.id
    db.flush()


class TestStalenessLifecycle:
    def test_false_right_after_publish(self, client, pg_db) -> None:
        _republish(pg_db)

        resp = client.get(BASE)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stale"] is False, body
        assert body["published_version"] is not None, body
        assert body["current_hash"] == body["published_hash"], body

    def test_true_after_a_domain_row_update(self, client, pg_db) -> None:
        _republish(pg_db)

        pg_db.execute(
            text(
                "UPDATE chatbot_domains SET switch_words = switch_words || :extra "
                "WHERE name = 'inventory'"
            ),
            {"extra": ["ZZT-staleness-word"]},
        )
        pg_db.flush()

        resp = client.get(BASE)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stale"] is True, body
        assert body["current_hash"] != body["published_hash"], body

    def test_false_again_after_republish(self, client, pg_db) -> None:
        _republish(pg_db)
        pg_db.execute(
            text(
                "UPDATE chatbot_domains SET switch_words = switch_words || :extra "
                "WHERE name = 'inventory'"
            ),
            {"extra": ["ZZT-staleness-word-2"]},
        )
        pg_db.flush()

        stale_resp = client.get(BASE)
        assert stale_resp.json()["stale"] is True

        _republish(pg_db)

        resp = client.get(BASE)
        assert resp.status_code == 200, resp.text
        assert resp.json()["stale"] is False, resp.json()


class TestGuardedByChatHistoryView:
    def test_403_without_the_view_grant(self, client) -> None:
        _GRANTS.clear()
        resp = client.get(BASE)
        assert resp.status_code == 403, resp.text
        assert VIEW in resp.text

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
