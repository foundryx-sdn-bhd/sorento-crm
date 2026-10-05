"""ORDER-BROADEN: the prompt change ships as a patch file, and the patch never drifts.

The owner's rule (5 Oct 2026): a MISS reply inviting other dates never moves the domain,
and "all products" is never a stock / ETA question. It is a parser PROMPT rule (owner
ruling: understanding lives in the prompt, never in code), delivered here as an
addendum to the production text's BROADEN AXIS block plus the unified diff crew sequences
onto #1429's combined version (PARSER-PER-AUDIENCE, migration `ppa_0001`). No competing
prompt version migration is written in this lane.

These tests run everywhere (no provider). The live reading of the patched text is
`test_order_broaden_live_parser.py` (opt-in, `CHATBOT_LIVE_LLM=1`).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.chatbot import _order_broaden_prompt as ob


def test_the_anchor_is_the_keep_rules_last_line_in_the_production_text():
    text = ob.prod_text()
    assert text.count(ob.ANCHOR) == 1
    before = text.split(ob.ANCHOR)[0]
    keep = before.rsplit("\n", 4)[1:]
    assert any("KEEP domain_hint and intent_hint from the previous turn" in line for line in keep), keep
    # Inside BROADEN AXIS, before DEMAND QTY.
    assert before.rfind("== BROADEN AXIS ==") > before.rfind("== DEMAND QTY ==")


def test_the_addendum_states_the_rule_and_the_worked_verdict():
    addendum = ob.addendum_text()
    for needle in (
        "A MISS reply never moves the domain",
        '"all products" is NEVER a stock / incoming / ETA question',
        'domain_hint "order"',
        'intent_hint "check_order"',
        'broaden_axis "product"',
        'broaden_to "all"',
        'entity_op "reuse"',
        "entities []",
        'broaden_axis "customer"',
        'broaden_axis "date"',
        'NEVER domain_hint "incoming" or "inventory"',
    ):
        assert needle in addendum, needle
    # The repo's own writing rule: no em or en dashes anywhere in the prompt.
    assert not re.search("[–—]", addendum)
    # Indented like the bullet it extends (four spaces), every non-blank line.
    assert all(line.startswith("    ") for line in addendum.splitlines() if line.strip())
    assert addendum.endswith("\n") and not addendum.endswith("\n\n")


def test_the_patched_text_keeps_the_block_structure():
    text = ob.patched_text()
    start = text.index("== BROADEN AXIS ==")
    end = text.index("== DEMAND QTY ==")
    block = text[start:end]
    assert ob.ANCHOR in block
    assert "A MISS reply never moves the domain" in block
    # The bullet that follows the KEEP rule is still there, after the addendum.
    assert block.index("A MISS reply never moves the domain") < block.index("  - Leave scope_intent null.")
    # Nothing outside the block changed.
    prod = ob.prod_text()
    assert text[:start] == prod[: prod.index("== BROADEN AXIS ==")]
    assert text[end:] == prod[prod.index("== DEMAND QTY ==") :]
    assert text.count("{{current_date}}") == prod.count("{{current_date}}") == 1


def test_the_committed_patch_is_the_diff_of_the_addendum():
    """The .patch file crew applies is derived, never hand-edited: it must equal the
    unified diff `_order_broaden_prompt.unified_patch()` builds from the two text files."""
    assert ob.PATCH.exists(), "run: python -m tests.chatbot._order_broaden_prompt"
    assert ob.PATCH.read_text(encoding="utf-8") == ob.unified_patch()


@pytest.mark.skipif(shutil.which("patch") is None, reason="GNU patch not installed")
def test_the_committed_patch_applies_cleanly_to_the_production_text():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / ob.PROD.relative_to(ob.BACKEND)
        target.parent.mkdir(parents=True)
        target.write_text(ob.prod_text(), encoding="utf-8")
        run = subprocess.run(
            ["patch", "-p0", "--batch", "-i", str(ob.PATCH)],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert target.read_text(encoding="utf-8") == ob.patched_text()


def test_the_production_file_itself_is_untouched():
    """`pdyn_0003` compares this file to prod's live text character for character."""
    assert "A MISS reply never moves the domain" not in ob.prod_text()
