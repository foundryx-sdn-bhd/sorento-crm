"""AI assistant provider keys are encrypted at rest (AI-KEY-ENCRYPT).

`ai_assistant_configs.*_ciphertext` used to hold the key in plain text. The column
must now hold a Fernet token from `app.utils.field_encryption` (the helper the
Respond.io workspace keys already use), while every reader still gets the plain
key from the ORM attribute. Rows written before the change stay readable until
the backfill script encrypts them.

Postgres, never sqlite: the point is what the column physically holds, so the raw
value is read back with SQL, beneath the ORM.

Every key here is fake.
"""
from __future__ import annotations

from sqlalchemy import text

from app.api.v1.system.ai_assistant import get_ai_assistant_config, update_ai_assistant_config
from app.models.ai_assistant import AIAssistantConfig
from app.schemas.ai_assistant import AIAssistantConfigUpdate
from app.services.ai_assistant_service import AIAssistantConfigService
from app.utils.field_encryption import decrypt_secret, encrypt_secret
from tests._pg_fixture import blank_session

OPENAI = "sk-ZZT-fake-openai-key-0000001111"
ANTHROPIC = "sk-ant-ZZT-fake-anthropic-key-2222"
GEMINI = "AIzaZZT-fake-gemini-key-3333"

KEY_COLUMNS = ("api_key_ciphertext", "anthropic_api_key_ciphertext", "gemini_api_key_ciphertext")


def _payload(**overrides) -> AIAssistantConfigUpdate:
    fields = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0,
        "system_prompt": "",
        "enabled_tools": [],
        "rag_enabled": True,
        "is_enabled": True,
    }
    fields.update(overrides)
    return AIAssistantConfigUpdate(**fields)


def _raw(db, row_id) -> dict[str, str | None]:
    """The three key columns as Postgres holds them, bypassing the ORM type."""
    got = db.execute(
        text(
            "SELECT api_key_ciphertext, anthropic_api_key_ciphertext, gemini_api_key_ciphertext "
            "FROM ai_assistant_configs WHERE id = :i"
        ),
        {"i": row_id},
    ).one()
    return dict(zip(KEY_COLUMNS, got))


def _save_all_three(db) -> AIAssistantConfig:
    return AIAssistantConfigService(db).update(
        _payload(api_key=OPENAI, anthropic_api_key=ANTHROPIC, gemini_api_key=GEMINI),
        user_id=None,
    )


def test_saved_keys_are_ciphertext_in_the_column_not_the_key():
    with blank_session() as db:
        row = _save_all_three(db)
        raw = _raw(db, row.id)

        for column, plain in zip(KEY_COLUMNS, (OPENAI, ANTHROPIC, GEMINI)):
            stored = raw[column]
            assert stored and stored != plain, column
            assert plain not in stored, column
            assert stored.startswith("gAAAAA"), column  # Fernet token, version 0x80
            assert decrypt_secret(stored) == plain, column


def test_readers_get_the_plain_key_from_the_attribute():
    """Every call site (ideation_turn_service, llm_provider.resolve_api_key, ...)
    reads the attribute as the key; a fresh load must decrypt."""
    from app.services.llm_provider import resolve_api_key

    with blank_session() as db:
        row_id = _save_all_three(db).id
        db.expire_all()
        row = db.get(AIAssistantConfig, row_id)

        assert row.api_key_ciphertext == OPENAI
        assert row.anthropic_api_key_ciphertext == ANTHROPIC
        assert row.gemini_api_key_ciphertext == GEMINI
        assert resolve_api_key(row, "openai") == OPENAI
        assert resolve_api_key(row, "anthropic") == ANTHROPIC


def test_a_legacy_plaintext_row_still_reads_as_the_key():
    with blank_session() as db:
        row_id = AIAssistantConfigService(db).get().id
        db.execute(
            text("UPDATE ai_assistant_configs SET api_key_ciphertext = :k WHERE id = :i"),
            {"k": OPENAI, "i": row_id},
        )
        db.expire_all()

        assert db.get(AIAssistantConfig, row_id).api_key_ciphertext == OPENAI


def test_a_token_under_another_secret_reads_as_no_key(monkeypatch):
    """Fernet-shaped but undecryptable (JWT_SECRET changed): no key, never the token
    itself, which would otherwise be posted to the provider as if it were a key."""
    from app.config import settings as app_settings

    with blank_session() as db:
        row_id = _save_all_three(db).id
        monkeypatch.setattr(app_settings, "jwt_secret", "ZZT-a-different-secret")
        db.expire_all()

        row = db.get(AIAssistantConfig, row_id)
        assert row.api_key_ciphertext is None


def test_the_config_api_never_returns_a_key():
    with blank_session() as db:
        put = update_ai_assistant_config(
            _payload(api_key=OPENAI, anthropic_api_key=ANTHROPIC, gemini_api_key=GEMINI),
            user={"id": None},
            db=db,
        )
        got = get_ai_assistant_config(_user={}, db=db)
        raw = _raw(db, put.id)

        for body in (put.model_dump_json(), got.model_dump_json()):
            for secret in (OPENAI, ANTHROPIC, GEMINI, *raw.values()):
                assert secret not in body
        assert got.api_key_masked == "****1111"
        assert got.anthropic_api_key_masked == "****2222"
        assert got.gemini_api_key_masked == "****3333"


def test_backfill_dry_run_counts_without_writing_then_apply_encrypts(capsys):
    from scripts.encrypt_ai_assistant_keys import backfill

    with blank_session() as db:
        row_id = AIAssistantConfigService(db).get().id
        legacy_anthropic = encrypt_secret(ANTHROPIC)
        db.execute(
            text(
                "UPDATE ai_assistant_configs SET api_key_ciphertext = :o, "
                "anthropic_api_key_ciphertext = :a, gemini_api_key_ciphertext = NULL WHERE id = :i"
            ),
            {"o": OPENAI, "a": legacy_anthropic, "i": row_id},
        )

        counts = backfill(db, apply=False)
        assert counts == {
            "api_key_ciphertext": {"plaintext": 1, "encrypted": 0, "empty": 0},
            "anthropic_api_key_ciphertext": {"plaintext": 0, "encrypted": 1, "empty": 0},
            "gemini_api_key_ciphertext": {"plaintext": 0, "encrypted": 0, "empty": 1},
        }
        assert _raw(db, row_id)["api_key_ciphertext"] == OPENAI  # dry run wrote nothing

        backfill(db, apply=True)
        raw = _raw(db, row_id)
        assert raw["api_key_ciphertext"] != OPENAI
        assert decrypt_secret(raw["api_key_ciphertext"]) == OPENAI
        assert raw["anthropic_api_key_ciphertext"] == legacy_anthropic  # untouched
        assert raw["gemini_api_key_ciphertext"] is None

        again = backfill(db, apply=True)
        assert again["api_key_ciphertext"] == {"plaintext": 0, "encrypted": 1, "empty": 0}
        assert _raw(db, row_id) == raw

    out = capsys.readouterr()
    for secret in (OPENAI, ANTHROPIC, legacy_anthropic):
        assert secret not in out.out + out.err
