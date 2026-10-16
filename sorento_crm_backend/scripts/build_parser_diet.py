"""Build the PROMPT-DIET stage 1 parser body.

Starts from the stored v94 text (`alembic/data/chatbot_semantic_parser.v94.txt`, never edited),
applies the #1574 ablation cuts (imported from `scripts.build_parser_ablation`, minus the ids that
no longer match v94), then the stage 1 edits. Each replacement is exact: `old` must occur exactly
once in the text at the moment it is applied. A skipped cut that starts matching raises too.
Writes `alembic/data/chatbot_semantic_parser.diet-s1-20261016.txt`.

Run from sorento_crm_backend/:  venv/bin/python -m scripts.build_parser_diet
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scripts.build_parser_ablation import CUTS  # noqa: E402

SOURCE = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.v94.txt"
OUTPUT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.diet-s1-20261016.txt"

SKIPPED_ABLATION_IDS = frozenset({
    "C05", "C23", "C25", "C34", "C39a", "C39b", "C42a", "C42b", "C43a",
    "C43c", "C43d", "C44", "C45", "C47c", "C47d", "C53a", "C53b", "C57",
})

_R = "━" * 35


def _banner(title: str) -> str:
    return f"{_R}\n{title}\n{_R}\n\n"


_IN_ADD = "Everything below is IN ADDITION to the OUTPUT object above. Emit "

#: (edit_id, old, new) exact replacements, applied in order after the cuts.
STAGE1_EDITS: list[tuple[str, str, str]] = [
    # S1-02 (AC5): FLYER FLAG keeps only the routing cue.
    (
        "S1-02",
        """== FLYER FLAG ==
If the current message asks for a flyer (also "brochure", "promo flyer", "marketing
flyer") \u2014 in any language or phrasing \u2014 set top-level "contains_flyer": true.
Otherwise false. Do NOT emit flyer as an entity; downstream handles it.
- set domain_hint = promotion, intent_hint = check_promotion
    brand/category named
""",
        """== FLYER FLAG ==
A flyer or brochure ask ("promo flyer", "marketing flyer") is a promotion ask: domain_hint = promotion, intent_hint = check_promotion. Flyer is never an entity.
""",
    ),
    # S1-03 (AC6): CORRECTION / DOUBT section (the OUTPUT key stays).
    (
        "S1-03",
        """== CORRECTION / DOUBT ==
If the user indicates the previous answer was WRONG or incomplete, set correction = true.

""",
        "",
    ),
    # S1-04 (AC7): REFERENCE TARGET section, its OUTPUT value and its cross-reference.
    (
        "S1-04a",
        """== REFERENCE TARGET (which set a positional reply means) ==
When "Previous response" contains the marker "[<N> did-you-mean suggestions active]" AND the current message refers to item position(s), also set a top-level "reference_target":
  - "dym"    -> the reply is bare number(s), one or more ("2", "1, 4", "1 and 4"), OR names a suggestion ("suggestion 2", "the 2nd suggestion").
  - "result" -> the reply explicitly qualifies a result item ("product 2", "the 2nd one", "the 2nd product", "price of the first").
If the marker is absent, set reference_target = null. reference_positions is unchanged (the raw 1-based positions). Do NOT resolve \u2014 downstream maps the positions to the set named by reference_target.

""",
        "",
    ),
    (
        "S1-04b",
        '  "reference_target": "result|dym|null \u2014 WHICH set a positional reply targets; null unless a [<N> did-you-mean suggestions active] marker is present (see REFERENCE TARGET)",\n',
        '  "reference_target": null,\n',
    ),
    (
        "S1-04c",
        "keep their existing handling (see REFERENCE TARGET and\n    COMPANY-NAME REPLY).",
        "keep their existing handling (see COMPANY-NAME REPLY).",
    ),
    # S1-05 (AC8): one rule for a bare number reply.
    (
        "S1-05",
        "Never emit BOTH an entity and a reference_position for the same message.",
        "A bare number reply (no code or name typed) gives reference_positions only and never also copies the picked option into entities.",
    ),
    # S1-06 (AC9): comma fix, announced keys inside the OUTPUT object, entity sub-keys stated once.
    (
        "S1-06a",
        '  "requested_attributes": []\n  "contains_flyer": false,',
        '  "requested_attributes": [],\n  "contains_flyer": false,',
    ),
    (
        "S1-06b",
        '  "anaphora": { "backward_reference": false }\n}\nReturn exactly one JSON object.\n',
        '  "anaphora": { "backward_reference": false },\n'
        '  "group_by": "customer|transporter|date|product|warehouse|supplier|null", also "month" or "year" on a sales analysis,\n'
        '  "top_n": an integer, or null,\n'
        '  "sales_channel": "dealer|project|null",\n'
        '  "proceed_anyway": true|false|null,\n'
        '  "sales_basis": "ordered|delivered|invoiced|null",\n'
        '  "sales_company": string|null,\n'
        '  "rank_by": "quantity|amount|null",\n'
        '  "basis": "delivered|ordered|unclear|null",\n'
        '  "rank_group": "item|category|unclear|null",\n'
        '  "rank_direction": "top|bottom|null",\n'
        '  "self_reference": true|false\n'
        '}\n'
        'Return exactly one JSON object.\n'
        'Every entity also carries "quantity" (a number, or null), "spec_key" (a key from the Specification lines of the policy block, or null) '
        'and "spec_value" (one of that key\'s choices, the value after "=", a number for a numeric key, true/false for a yes-or-no key, or null).\n',
    ),
    # S1-07 (AC10): banner boxes that only titled or announced keys.
    (
        "S1-07a",
        _banner("TWO MORE OUTPUT KEYS, AND THE SO / PO / SPO VOCABULARY")
        + _IN_ADD
        + 'these two keys on every\nobject, exactly as if they were listed there:\n\n  "group_by": "customer|transporter|date|product|warehouse|supplier|null",\n  "top_n": an integer, or null\n\n',
        "",
    ),
    ("S1-07b", _banner("LAST PURCHASE COST"), ""),
    (
        "S1-07c",
        _banner("SALES REPORT")
        + _IN_ADD
        + 'this key on every\nobject, exactly as if it were listed there:\n\n  "sales_channel": "dealer|project|null"\n\n',
        "",
    ),
    (
        "S1-07d",
        _banner("THE OPEN TASK, PER-PRODUCT QUANTITIES, AND PROCEEDING")
        + _IN_ADD
        + 'this key on every\nobject, exactly as if it were listed there, and emit "quantity" on every entity:\n\n  "proceed_anyway": true|false|null\n  entities[].quantity: a number, or null\n\n',
        "",
    ),
    (
        "S1-07e",
        _banner("SALES ANALYSIS")
        + _IN_ADD
        + 'these keys on every\nobject, exactly as if they were listed there:\n\n  "sales_basis": "ordered|delivered|invoiced|null"\n  "sales_company": string|null\n\nand group_by may also be "month" or "year".\n\n',
        "",
    ),
    (
        "S1-07f",
        _banner("SPECIFICATIONS: WHAT A PRODUCT IS DESCRIBED BY")
        + _IN_ADD
        + 'these two keys on every\nentity, exactly as if they were listed there:\n\n  entities[].spec_key: a key from the Specification lines of the policy block, or null\n  entities[].spec_value: one of that key\'s choices (the value after "="), a number for a\n    numeric key, true/false for a yes-or-no key, or null\n\n',
        "",
    ),
    (
        "S1-07g",
        _banner("QUANTITY")
        + _IN_ADD
        + 'this key on every\nentity, exactly as if it were listed there:\n\n  "quantity": a number, or null\n\n',
        "",
    ),
    ("S1-07h", _banner("KNOWN BRANDS"), ""),
    (
        "S1-07i",
        _banner("TOP SELLING")
        + _IN_ADD
        + 'these four keys on\nevery object, exactly as if they were listed there:\n\n  "rank_by": "quantity|amount|null",\n  "basis": "delivered|ordered|unclear|null",\n  "rank_group": "item|category|unclear|null",\n  "rank_direction": "top|bottom|null"\n\n',
        "",
    ),
    (
        "S1-07j",
        _banner("SELF REFERENCE")
        + _IN_ADD
        + 'this key on every\nobject, exactly as if it were listed there:\n\n  "self_reference": true|false\n\n',
        "",
    ),
]


def _apply(text: str, edit_id: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"edit {edit_id}: old text occurs {count} times, expected exactly 1")
    return text.replace(old, new, 1)


def build(source_text: str) -> str:
    text = source_text
    for cut_id, old, new in CUTS:
        if cut_id in SKIPPED_ABLATION_IDS:
            if text.count(old) == 1:
                raise ValueError(f"cut {cut_id} is skipped but its old text now matches exactly once (drift)")
            continue
        text = _apply(text, cut_id, old, new)
    for edit_id, old, new in STAGE1_EDITS:
        text = _apply(text, edit_id, old, new)
    return text


def main() -> None:
    text = build(SOURCE.read_text(encoding="utf-8"))
    with open(OUTPUT, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"wrote {OUTPUT} ({len(text)} chars)")


if __name__ == "__main__":
    main()
