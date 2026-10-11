"""REPORTS-STEP2 S3a: the v53 parser text is v50 plus exactly one new section, so the delta stays reviewable."""
from __future__ import annotations

from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "alembic" / "data"
HEADING = "== ASKS THE BOT CANNOT ANSWER =="


def _lines(name: str) -> list[str]:
    return (DATA / name).read_text(encoding="utf-8").splitlines()


def test_v53_is_v50_plus_exactly_the_new_section():
    old, new = _lines("chatbot_semantic_parser.v50.txt"), _lines("chatbot_semantic_parser.v53.txt")
    start = new.index(HEADING)
    memory = new.index("== MEMORY ==")
    assert start < memory
    # Removing the new section's lines gives v50 back, line for line.
    assert new[:start] + new[memory:] == old
    section = "\n".join(new[start:memory])
    assert 'domain_hint "receivables"' in section and 'domain_hint "dealer_locator"' in section
    assert "set order_status" in section
