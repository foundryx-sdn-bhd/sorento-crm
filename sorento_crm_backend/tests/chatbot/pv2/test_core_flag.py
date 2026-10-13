"""PV2-CORE: the parser-mode flag (CONTRACT-PV2-13oct.md section 6). Default v1."""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import text

from app.models.user import SystemSetting
from app.services.chatbot.pv2.flag import parser_mode


def _envelope(console: bool):
    return SimpleNamespace(
        dry_run=console, ingress="console" if console else "respond_io",
        model_extra={"console_origin": True} if console else {},
    )


def _set_system(db, mode: str | None):
    db.execute(text("delete from system_settings"))
    if mode is not None:
        db.add(SystemSetting(chatbot_parser_mode=mode))
    db.flush()


def test_default_is_v1(session_factory):
    db = session_factory()
    _set_system(db, None)
    assert parser_mode(db, SimpleNamespace(chatbot_parser_mode=None), _envelope(False)) == "v1"
    _set_system(db, "v1")
    assert parser_mode(db, None, None) == "v1"


def test_column_default_is_v1(session_factory):
    db = session_factory()
    db.execute(text("delete from system_settings"))
    db.add(SystemSetting())
    db.flush()
    db.execute(text("update system_settings set chatbot_parser_mode = default"))
    assert db.execute(text("select chatbot_parser_mode from system_settings")).scalar() == "v1"


def test_console_turn_is_v2(session_factory):
    db = session_factory()
    _set_system(db, "v1")
    assert parser_mode(db, SimpleNamespace(chatbot_parser_mode=None), _envelope(True)) == "v2"


def test_prompts_screen_run_is_not_console(session_factory):
    db = session_factory()
    _set_system(db, "v1")
    env = SimpleNamespace(dry_run=True, ingress="console", model_extra={})
    assert parser_mode(db, None, env) == "v1"


def test_test_contact_is_v2(session_factory):
    db = session_factory()
    _set_system(db, "v1")
    assert parser_mode(db, SimpleNamespace(chatbot_parser_mode="v2"), _envelope(False)) == "v2"


def test_system_v2_with_contact_override_v1(session_factory):
    db = session_factory()
    _set_system(db, "v2")
    assert parser_mode(db, SimpleNamespace(chatbot_parser_mode=None), _envelope(False)) == "v2"
    assert parser_mode(db, SimpleNamespace(chatbot_parser_mode="v1"), _envelope(False)) == "v1"


def test_garbage_contact_value_follows_system(session_factory):
    db = session_factory()
    _set_system(db, "v1")
    assert parser_mode(db, SimpleNamespace(chatbot_parser_mode="v3"), None) == "v1"
