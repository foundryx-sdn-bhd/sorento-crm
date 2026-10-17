"""CHAT-COMPOSER S0 red tests (UAC AC-0.1, AC-0.2, AC-0.3).

The gate, the shared value / timestamp printers moved byte for byte into
`composers/format.py`, and the import boundary. The new package does not exist yet:
every composers import is inside its test so each fails on its own.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config import settings

EM = "\u2014"
COMPOSERS_DIR = Path(__file__).resolve().parents[2] / "app" / "services" / "chatbot" / "composers"


def test_gate_defaults_off(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("CHAT_COMPOSER_ENABLED", raising=False)
    assert Settings().chat_composer_enabled is False
    monkeypatch.setenv("CHAT_COMPOSER_ENABLED", "true")
    assert Settings().chat_composer_enabled is True


def test_enabled_reads_settings_at_call_time(monkeypatch):
    from app.services.chatbot import composers

    monkeypatch.setattr(settings, "chat_composer_enabled", False, raising=False)
    assert composers.enabled() is False
    monkeypatch.setattr(settings, "chat_composer_enabled", True, raising=False)
    assert composers.enabled() is True


def test_fetch_helpers_are_the_format_helpers():
    from app.services.chatbot.composers import format as fmt
    from app.services.chatbot.lanes.business import fetch

    assert fetch._fmt_value is fmt.value
    assert fetch._fmt_ts is fmt.timestamp
    assert fetch._EM_DASH == fmt.EM_DASH == EM
    assert fetch._ISO_RE is fmt.ISO_RE


# Literal outputs captured from main's fetch._fmt_value / _fmt_ts on 17 Oct (HEAD 2dd7cf0e5e).
VALUE_MATRIX = [
    (None, EM),
    ("", EM),
    (True, "Yes"),
    (False, "No"),
    (0, "0"),
    (5, "5"),
    (5.5, "5.5"),
    ("2026-09-27T00:00:00", "27/09/2026"),
    ("2026-09-27T10:11:12", "27/09/2026 10:11:12"),
    ("2026-09-27", "2026-09-27"),
    ("*REPLACE", "∗REPLACE"),
    ("plain", "plain"),
    ([1, None], "1, " + EM),
    ({"a": 1, "b": ""}, "1"),
    ({}, EM),
    ([], ""),
]


@pytest.mark.parametrize("raw,expected", VALUE_MATRIX, ids=[repr(r) for r, _ in VALUE_MATRIX])
def test_value_matrix_unchanged(raw, expected):
    from app.services.chatbot.composers import format as fmt

    assert fmt.value(raw) == expected


TS_MATRIX = [
    (None, None),
    ("", None),
    ("2026-09-27", "27/09/2026"),
    ("2026-09-27T10:11:12", "27/09/2026 10:11:12"),
    ("2026-09-27 00:00:00", "27/09/2026"),
    ("junk", None),
]


@pytest.mark.parametrize("raw,expected", TS_MATRIX, ids=[repr(r) for r, _ in TS_MATRIX])
def test_timestamp_matrix_unchanged(raw, expected):
    from app.services.chatbot.composers import format as fmt

    assert fmt.timestamp(raw) == expected


def test_label_line():
    from app.services.chatbot.composers import format as fmt

    assert fmt.label_line("Total", 60) == "*Total:* 60"
    assert fmt.label_line("X", None) == "*X:* " + EM
    assert fmt.label_line("Code", "*REPLACE") == "*Code:* ∗REPLACE"


def test_composers_import_boundary():
    assert COMPOSERS_DIR.is_dir(), "app/services/chatbot/composers/ must exist"
    files = sorted(COMPOSERS_DIR.glob("*.py"))
    assert files, "composers/ holds no module"
    banned = ("engine", "head", "tail", "dialogue", "lanes")
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            dotted: list[str] = []
            if isinstance(node, ast.Import):
                dotted = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    # a relative import climbs inside the chatbot package
                    base = "app.services.chatbot." + base if node.level > 1 else "app.services.chatbot.composers." + base
                dotted = [base, *[f"{base}.{a.name}" for a in node.names]]
            for name in dotted:
                if not name.startswith("app.services.chatbot"):
                    continue
                parts = name.split(".")[3:]
                if (parts and parts[0] in banned) or parts[:2] == ["turn", "compose"]:
                    pytest.fail(f"{path.name} imports {name}")
