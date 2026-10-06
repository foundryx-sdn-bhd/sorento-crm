"""STOCK-QTY-PARTIAL-FILL: the trap-spec and list-fragment prompt rules ship as a patch
file, the patch never drifts, and the code fallback carries the same text.

Owner, 6 Oct 2026: "Any WC with S trap 300mm available" parsed `trap_length 300` at 10:07
and `dim_width 300` at 14:19 on the same prompt (v47). The rule is a parser PROMPT rule
(owner: the parser decides, no word-shape rules in code), delivered here as two addenda to
the production text (`alembic/data/chatbot_semantic_parser.trap-specs.addendum.txt`,
`...list-fragment.addendum.txt`) plus the unified diff crew sequences onto the live version.
No prompt version migration is written in this lane (PROMPT-DYNAMIC, #1405: the live text is
the owner's; the ORDER-BROADEN precedent, #1495).

These tests run everywhere (no provider). The live reading of the patched text is
`test_trap_specs_live_parser.py` (opt-in, `CHATBOT_LIVE_LLM=1`, crew runs it on the Mac).
UAC AC-9 to AC-12.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.chatbot import _trap_specs_prompt as tp

DASHES = "[" + chr(0x2013) + chr(0x2014) + "]"


def test_the_anchors_occur_once_each_in_the_production_text():
    text = tp.prod_text()
    assert text.count(tp.SPEC_ANCHOR) == 1
    assert text.count(tp.LIST_ANCHOR) == 1
    # The spec anchor closes the SPECIFICATIONS block's examples; the list anchor sits in
    # QUANTITIES and LAST_ANSWER.
    spec_at = text.index(tp.SPEC_ANCHOR)
    assert text.rfind("SPECIFICATIONS: WHAT A PRODUCT IS DESCRIBED BY", 0, spec_at) > 0
    list_at = text.index(tp.LIST_ANCHOR)
    assert text.rfind("QUANTITIES and LAST_ANSWER:", 0, list_at) > 0


def test_the_trap_addendum_states_the_rules_and_the_worked_verdicts():
    addendum = tp.trap_addendum_text()
    for needle in (
        "TRAP SPECS",
        "spec_key trap_type, spec_value s_trap",
        "spec_value p_trap",
        "spec_key trap_length, spec_value 300",
        "NEVER dim_width, dim_length, dim_height",
        "BEFORE or AFTER",
        '"Any WC with s trap 300mm available?"',
        '"Any WC with S trap 300mm available"',
        "never\n      dim_width 300",
        '"Srtwc8066-sh s trap 250mm 50pc"',
        "product SRTWC8066-SH quantity 50",
        '"FIND S TRAP 200MM SORENTO WC"',
        "brand Sorento",
        '"tandas s trap 300mm ada stok?"',
        '"any water closet 600mm wide"',
        "specification dim_width\n      600",
        "intent_hint check_stock",
        # Q4 (a), owner 6 Oct 2026: a bare spec after a product answer is that product's
        # variant, carried by the EXISTING fields (entity_op reuse + the specification).
        "A SPEC SAID RIGHT AFTER A PRODUCT ANSWER",
        'entity_op "reuse"',
        'never "replace_combine"',
        '"How about p trap"',
        "never master_products",
    ):
        assert needle in addendum, needle
    assert not re.search(DASHES, addendum)
    # Indented like the SPECIFICATIONS bullets it extends (two spaces), every non-blank line.
    assert all(line.startswith("  ") for line in addendum.splitlines() if line.strip())
    assert addendum.endswith("\n") and not addendum.endswith("\n\n")


def test_the_list_fragment_addendum_states_the_rule():
    addendum = tp.list_addendum_text()
    for needle in (
        "A FRAGMENT of ONE listed code",
        '"I need 8152 6 unit"',
        '"code": "SRTWC8152-SH-300"',
        "canonical_code = the listed code",
        "placed on none (mode null",
        "answered at once; the other lines stay\n    open",
    ):
        assert needle in addendum, needle
    assert not re.search(DASHES, addendum)
    assert all(line.startswith("  ") for line in addendum.splitlines() if line.strip())
    assert addendum.endswith("\n") and not addendum.endswith("\n\n")


def test_the_patched_text_keeps_the_block_structure():
    text = tp.patched_text()
    prod = tp.prod_text()
    # The trap rules sit inside the SPECIFICATIONS block, after its last example and
    # before the next banner.
    spec_start = text.index("SPECIFICATIONS: WHAT A PRODUCT IS DESCRIBED BY")
    spec_end = text.index("━━━", text.index("TRAP SPECS"))
    block = text[spec_start:spec_end]
    assert tp.SPEC_ANCHOR in block and "TRAP SPECS" in block
    assert block.index(tp.SPEC_ANCHOR) < block.index("TRAP SPECS")
    # The fragment rule sits right before the "NOT on the list" bullet.
    assert text.index("A FRAGMENT of ONE listed code") < text.index(tp.LIST_ANCHOR)
    assert text.rfind("QUANTITIES and LAST_ANSWER:", 0, text.index("A FRAGMENT of ONE listed code")) > 0
    # Nothing before the first insertion changed, and the tail after the last is the
    # production tail.
    first = min(prod.index(tp.LIST_ANCHOR), prod.index(tp.SPEC_ANCHOR))
    assert text[:first] == prod[:first]
    tail_from = prod.index(tp.SPEC_ANCHOR) + len(tp.SPEC_ANCHOR)
    assert text.endswith(prod[tail_from:])
    assert text.count("{{current_date}}") == prod.count("{{current_date}}") == 1
    assert len(text) == len(prod) + len(tp.trap_addendum_text()) + len(tp.list_addendum_text())


def test_the_committed_patch_is_the_diff_of_the_addenda():
    """The .patch file crew applies is derived, never hand-edited: it must equal the
    unified diff `_trap_specs_prompt.unified_patch()` builds from the text files."""
    assert tp.PATCH.exists(), "run: python -m tests.chatbot._trap_specs_prompt"
    assert tp.PATCH.read_text(encoding="utf-8") == tp.unified_patch()


@pytest.mark.skipif(shutil.which("patch") is None, reason="GNU patch not installed")
def test_the_committed_patch_applies_cleanly_to_the_production_text():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / tp.PROD.relative_to(tp.BACKEND)
        target.parent.mkdir(parents=True)
        target.write_text(tp.prod_text(), encoding="utf-8")
        run = subprocess.run(
            ["patch", "-p0", "--batch", "-i", str(tp.PATCH)],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert target.read_text(encoding="utf-8") == tp.patched_text()


def test_the_production_file_itself_is_untouched():
    """`pdyn_0003` compares this file to prod's live text character for character."""
    prod = tp.prod_text()
    assert "TRAP SPECS" not in prod
    assert "A FRAGMENT of ONE listed code" not in prod


def test_the_order_broaden_patch_and_this_one_touch_different_lines():
    """Both patches are sequenced onto the same live text by crew; they must apply in
    either order (no overlapping hunk)."""
    from tests.chatbot import _order_broaden_prompt as ob

    both = tp.patched_text(ob.patched_text())
    assert "A MISS reply never moves the domain" in both and "TRAP SPECS" in both
    assert tp.patched_text(text=ob.patched_text()) == ob.patched_text(text=tp.patched_text())


def test_the_code_fallback_carries_the_same_addenda_once():
    """AC-12: a fresh install's v1 (`seed_prompt_registry` from the constant) teaches the
    rules in exactly the patch's words, inserted at the same anchors."""
    from app.services import chatbot_parser_prompt as cpp

    text = cpp.SEMANTIC_PARSER_PROMPT
    assert cpp.TRAP_SPECS_ADDENDUM == tp.trap_addendum_text()
    assert cpp.LIST_FRAGMENT_ADDENDUM == tp.list_addendum_text()
    assert text.count(tp.trap_addendum_text()) == 1
    assert text.count(tp.list_addendum_text()) == 1
    assert text.index(tp.SPEC_ANCHOR) < text.index("TRAP SPECS")
    assert text.index("A FRAGMENT of ONE listed code") < text.index(tp.LIST_ANCHOR)
    # Idempotent: applying again changes nothing.
    assert cpp.with_trap_specs(text) == text


def test_the_applier_is_the_code_fallbacks_own():
    """One applier (`chatbot_parser_prompt.with_trap_specs`), read by the patch builder
    in strict mode, so the fallback and the patch cannot insert at different places."""
    from app.services import chatbot_parser_prompt as cpp

    assert tp.patched_text() == cpp.with_trap_specs(tp.prod_text(), strict=True)
    with pytest.raises(ValueError):
        cpp.with_trap_specs("no anchor here\n", strict=True)
    assert cpp.with_trap_specs("no anchor here\n") == "no anchor here\n"
