"""Stored secrets are keyed by FIELD_ENCRYPTION_KEY, not JWT_SECRET (AI-KEY-ENCRYPT).

JWT_SECRET is due for rotation (it leaked in git history), and every secret
`app.utils.field_encryption` stored was keyed from it, so rotating it would have
made every stored AI key, Respond.io key and integration credential unreadable.

FIELD_ENCRYPTION_KEY is a comma-separated list of Fernet keys: the first encrypts,
all of them decrypt (MultiFernet), and the old JWT_SECRET-derived key is tried
last so tokens written before the switch keep working until
`scripts/reencrypt_field_secrets.py --apply` re-encrypts them.

Postgres, never sqlite. Every key here is fake.
"""
from __future__ import annotations

import base64
import hashlib
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.config import settings as app_settings
from app.models.ai_assistant import AIAssistantConfig
from app.schemas.ai_assistant import AIAssistantConfigUpdate
from app.services.ai_assistant_service import AIAssistantConfigService
from app.utils.field_encryption import decrypt_secret, encrypt_secret
from tests._pg_fixture import blank_session

OPENAI = "sk-ZZT-fake-openai-key-0000001111"
GEMINI = "AIzaZZT-fake-gemini-key-3333"
RESPOND = "ZZT-fake-respond-key-4444"
CREDS = '{"password": "ZZT-fake-credential-5555"}'

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(app_settings, "jwt_secret", "ZZT-jwt-before-rotation")
    monkeypatch.setattr(app_settings, "field_encryption_key", KEY_A, raising=False)


def _jwt_token(plain: str, jwt_secret: str = "ZZT-jwt-before-rotation") -> str:
    """A token as the pre-FIELD_ENCRYPTION_KEY helper wrote it."""
    key = base64.urlsafe_b64encode(hashlib.sha256(jwt_secret.encode()).digest())
    return Fernet(key).encrypt(plain.encode()).decode()


def _only(token: str, key: str) -> str:
    return Fernet(key.encode()).decrypt(token.encode()).decode()


def _payload(**overrides) -> AIAssistantConfigUpdate:
    fields = {"provider": "openai", "model": "gpt-4o-mini", "temperature": 0, "system_prompt": "",
              "enabled_tools": [], "rag_enabled": True, "is_enabled": True}
    fields.update(overrides)
    return AIAssistantConfigUpdate(**fields)


def _raw(db, table: str, column: str, row_id: str):
    return db.execute(text(f"SELECT {column} FROM {table} WHERE id = :i"), {"i": row_id}).scalar_one()


def test_new_writes_use_field_encryption_key_and_survive_jwt_rotation(monkeypatch):
    with blank_session() as db:
        row_id = AIAssistantConfigService(db).update(_payload(api_key=OPENAI), user_id=None).id
        stored = _raw(db, "ai_assistant_configs", "api_key_ciphertext", row_id)
        assert _only(stored, KEY_A) == OPENAI

        monkeypatch.setattr(app_settings, "jwt_secret", "ZZT-jwt-after-rotation")
        db.expire_all()
        assert db.get(AIAssistantConfig, row_id).api_key_ciphertext == OPENAI
        assert decrypt_secret(encrypt_secret(RESPOND)) == RESPOND


def test_the_first_key_encrypts_and_an_older_listed_key_still_decrypts(monkeypatch):
    old_token = encrypt_secret(RESPOND)  # under KEY_A
    monkeypatch.setattr(app_settings, "field_encryption_key", f"{KEY_B}, {KEY_A}")

    assert decrypt_secret(old_token) == RESPOND
    assert _only(encrypt_secret(RESPOND), KEY_B) == RESPOND


def test_a_jwt_derived_token_still_decrypts_during_rollout():
    assert decrypt_secret(_jwt_token(RESPOND)) == RESPOND


def test_without_field_encryption_key_the_jwt_derived_key_is_still_used(monkeypatch):
    """Dev and CI envs without the var keep working; only --apply insists on it."""
    monkeypatch.setattr(app_settings, "field_encryption_key", "")
    token = encrypt_secret(RESPOND)
    assert decrypt_secret(token) == RESPOND
    assert decrypt_secret(_jwt_token(RESPOND)) == RESPOND


def test_a_malformed_field_encryption_key_fails_loudly(monkeypatch):
    monkeypatch.setattr(app_settings, "field_encryption_key", "not-a-fernet-key")
    with pytest.raises(ValueError, match="FIELD_ENCRYPTION_KEY"):
        encrypt_secret(RESPOND)


def _seed_every_secret_column(db) -> dict[str, str]:
    """One row per table holding a mix: JWT-era tokens, plaintext AI key, current token."""
    cfg_id = AIAssistantConfigService(db).get().id
    db.execute(
        text("UPDATE ai_assistant_configs SET api_key_ciphertext = :p, "
             "anthropic_api_key_ciphertext = :j, gemini_api_key_ciphertext = :c WHERE id = :i"),
        {"p": OPENAI, "j": _jwt_token(OPENAI), "c": encrypt_secret(GEMINI), "i": cfg_id},
    )
    ws_id, integ_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.execute(
        text("INSERT INTO respond_workspaces (id, space_id, api_key_ciphertext, "
             "chatbot_retry_ingress_key_ciphertext) VALUES (:i, 'ZZT-space', :k, :r)"),
        {"i": ws_id, "k": _jwt_token(RESPOND), "r": "ZZT-not-a-token"},
    )
    db.execute(
        text("INSERT INTO integrations (id, name, type, credentials_json) "
             "VALUES (:i, :n, 'autocount', :c)"),
        {"i": integ_id, "n": f"ZZT-{integ_id[:8]}", "c": _jwt_token(CREDS)},
    )
    return {"cfg": cfg_id, "ws": ws_id, "integ": integ_id}


def test_backfill_dry_run_counts_then_apply_moves_everything_onto_the_field_key(monkeypatch, capsys):
    from scripts.reencrypt_field_secrets import backfill

    with blank_session() as db:
        ids = _seed_every_secret_column(db)
        before = _raw(db, "respond_workspaces", "api_key_ciphertext", ids["ws"])

        counts = backfill(db, apply=False)
        assert counts["ai_assistant_configs.api_key_ciphertext"]["plaintext"] == 1
        assert counts["ai_assistant_configs.anthropic_api_key_ciphertext"]["old_key"] == 1
        assert counts["ai_assistant_configs.gemini_api_key_ciphertext"]["current"] == 1
        assert counts["respond_workspaces.api_key_ciphertext"]["old_key"] == 1
        assert counts["integrations.credentials_json"]["old_key"] == 1
        # Plaintext where plaintext was never written is reported, not "fixed".
        assert counts["respond_workspaces.chatbot_retry_ingress_key_ciphertext"]["plaintext"] == 1
        assert _raw(db, "respond_workspaces", "api_key_ciphertext", ids["ws"]) == before

        backfill(db, apply=True)
        assert _only(_raw(db, "ai_assistant_configs", "api_key_ciphertext", ids["cfg"]), KEY_A) == OPENAI
        assert _only(_raw(db, "ai_assistant_configs", "anthropic_api_key_ciphertext", ids["cfg"]), KEY_A) == OPENAI
        assert _only(_raw(db, "respond_workspaces", "api_key_ciphertext", ids["ws"]), KEY_A) == RESPOND
        assert _only(_raw(db, "integrations", "credentials_json", ids["integ"]), KEY_A) == CREDS
        assert _raw(db, "respond_workspaces", "chatbot_retry_ingress_key_ciphertext", ids["ws"]) == "ZZT-not-a-token"

        # The point of the lane: after the backfill, rotating JWT_SECRET loses nothing.
        monkeypatch.setattr(app_settings, "jwt_secret", "ZZT-jwt-after-rotation")
        db.expire_all()
        cfg = db.get(AIAssistantConfig, ids["cfg"])
        assert cfg.api_key_ciphertext == OPENAI
        assert cfg.anthropic_api_key_ciphertext == OPENAI
        assert cfg.gemini_api_key_ciphertext == GEMINI
        assert decrypt_secret(_raw(db, "respond_workspaces", "api_key_ciphertext", ids["ws"])) == RESPOND
        assert decrypt_secret(_raw(db, "integrations", "credentials_json", ids["integ"])) == CREDS

        again = backfill(db, apply=True)
        assert again["respond_workspaces.api_key_ciphertext"]["old_key"] == 0
        assert again["ai_assistant_configs.api_key_ciphertext"]["plaintext"] == 0

    out = capsys.readouterr()
    for secret in (OPENAI, GEMINI, RESPOND, CREDS, "ZZT-not-a-token"):
        assert secret not in out.out + out.err


def test_backfill_apply_refuses_without_field_encryption_key(monkeypatch):
    from scripts.reencrypt_field_secrets import backfill

    monkeypatch.setattr(app_settings, "field_encryption_key", "")
    with blank_session() as db:
        with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEY"):
            backfill(db, apply=True)


@pytest.mark.parametrize("environment", ["production", "staging", "", None])
def test_boot_refuses_a_non_dev_environment_without_field_encryption_key(monkeypatch, environment):
    """Unset in prod would silently keep encrypting under the leaked JWT-derived key, and
    the planned JWT_SECRET rotation would then destroy those secrets."""
    from app.utils.field_encryption import assert_field_encryption_ready

    monkeypatch.setattr(app_settings, "field_encryption_key", "")
    with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEY"):
        assert_field_encryption_ready(environment)


@pytest.mark.parametrize("environment", ["development", "dev", "local", "test", "ci", " Development "])
def test_boot_allows_dev_environments_without_field_encryption_key(monkeypatch, environment):
    from app.utils.field_encryption import assert_field_encryption_ready

    monkeypatch.setattr(app_settings, "field_encryption_key", "")
    assert_field_encryption_ready(environment)


def test_boot_accepts_production_with_a_key_and_rejects_a_malformed_one(monkeypatch):
    """A typo must crash at boot, not read every stored secret as unset at runtime."""
    from app.utils.field_encryption import assert_field_encryption_ready

    assert_field_encryption_ready("production")
    monkeypatch.setattr(app_settings, "field_encryption_key", f"{KEY_A},not-a-fernet-key")
    with pytest.raises(ValueError, match="entry 2"):
        assert_field_encryption_ready("development")


def test_api_and_worker_both_run_the_boot_check():
    """The worker never imports app.main, so a guard there alone would not cover it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for entrypoint in ("app/main.py", "worker.py"):
        assert "assert_field_encryption_ready(" in (root / entrypoint).read_text(), entrypoint
