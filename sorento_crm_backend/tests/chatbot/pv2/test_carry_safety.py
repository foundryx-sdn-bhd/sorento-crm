"""Owner rule: no word or fuzzy matching on user text in code. CARRY modules may not import
re / difflib / rapidfuzz or call .lower() / .casefold() / ilike / startswith on anything."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PV2 = Path(__file__).resolve().parents[3] / "app" / "services" / "chatbot" / "pv2"
MODULES = ["apply", "state", "history", "clarify_copy", "bridge", "answer", "turn"]
REQUIRED = {"apply", "state", "history", "clarify_copy", "bridge", "answer", "turn"}
BANNED_IMPORTS = {"re", "difflib", "rapidfuzz"}
BANNED_ATTRS = {"lower", "casefold", "ilike", "startswith"}


def violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in BANNED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in BANNED_IMPORTS:
                found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
            found.append(f".{node.attr} (line {node.lineno})")
    return found


def test_scanner_catches_each_banned_form():
    src = "import re\nfrom difflib import get_close_matches\nimport rapidfuzz.fuzz\nx.lower()\ny.casefold()\nq.ilike('a')\nz.startswith('b')\n"
    assert len(violations(src)) == 7
    assert violations("import json\nx = a.upper()\n") == []


@pytest.mark.parametrize("name", MODULES)
def test_carry_module_has_no_text_matching(name: str):
    path = PV2 / f"{name}.py"
    if not path.exists():
        assert name not in REQUIRED, f"{path} must exist"
        pytest.skip(f"{name}.py not present yet")
    assert violations(path.read_text()) == []
