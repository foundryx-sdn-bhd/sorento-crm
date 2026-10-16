"""MODEL-PRICES CU-3: every usage writer records the provider's cached_tokens."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

APP = Path(__file__).resolve().parent.parent / "app"


def test_cu3_every_usage_log_constructor_passes_cached_tokens():
    missing, total = [], 0
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if name != "AIAssistantUsageLog":
                continue
            total += 1
            if "cached_tokens" not in {k.arg for k in node.keywords}:
                missing.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    assert total >= 10, f"expected the known writers, found {total}"
    assert missing == []


def test_cu3_spec_search_usage_row_records_the_providers_cached_tokens(monkeypatch):
    import uuid

    from app.models.ai_assistant import AIAssistantUsageLog
    from app.models.product import ProductCategory, UnitOfMeasure
    from app.services import product_spec_understanding as understanding
    from app.services.product_class_signal import backfill_category_signals
    from app.services.product_spec_registry import seed_spec_registry
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        db.add_all([
            ProductCategory(id=str(uuid.uuid4()), category_code="SRT-KS", category_name="SRT-KS"),
            UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZT-PCS", uom_name="Piece"),
        ])
        db.flush()
        backfill_category_signals(db)
        seed_spec_registry(db)
        result = SimpleNamespace(content=json.dumps({"specs": []}), prompt_tokens=5000, completion_tokens=10,
                                 total_tokens=5010, cached_tokens=4096)
        provider = SimpleNamespace(chat=lambda *a, **k: result)
        monkeypatch.setattr(understanding, "_resolve_provider", lambda d: (provider, "openai", "gpt-test"))
        understanding.understand_phrase(db, "a white toilet")
        row = db.query(AIAssistantUsageLog).filter_by(feature="spec_search").one()
        assert row.cached_tokens == 4096
