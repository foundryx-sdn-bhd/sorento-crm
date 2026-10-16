"""Build the PROMPT-DIET stage 1 parser body.

Starts from the stored v94 text (`alembic/data/chatbot_semantic_parser.v94.txt`, never edited),
applies the #1574 ablation cuts (imported from `scripts.build_parser_ablation`, minus the ids that
no longer match v94 and the 8 restored delivery-state word lists, see RESTORED_ABLATION_IDS), then the stage 1 edits (the section 43
contradiction, two missing OUTPUT commas). Each replacement is exact: `old` must occur exactly
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

#: #1574 cuts deliberately NOT applied: the delivery-state word lists decide between outstanding /
#: so_outstanding / delivered and the ranking basis (the v111 hand test read "belum DO" wrong).
#: Each restored cut's `old` must still occur exactly once in the source, so drift is loud.
RESTORED_ABLATION_IDS = frozenset({"C15b", "C15c", "C19a", "C19b", "C19c", "C33c", "C33d", "C41e"})

#: (edit_id, old, new) exact replacements, applied in order after the cuts.
STAGE1_EDITS: list[tuple[str, str, str]] = [
    # S1-05 (AC8): one rule for a bare number reply.
    (
        "S1-05",
        "Never emit BOTH an entity and a reference_position for the same message.",
        "A bare number reply (no code or name typed) gives reference_positions only and never also copies the picked option into entities.",
    ),
    # S1-06a (AC9): the missing comma after requested_attributes.
    (
        "S1-06a",
        '  "requested_attributes": []\n  "contains_flyer": false,',
        '  "requested_attributes": [],\n  "contains_flyer": false,',
    ),
    # S1-06c (AC9): the missing comma after the asks key.
    (
        "S1-06c",
        '"asks": [] one',
        '"asks": [], one',
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
        if cut_id in RESTORED_ABLATION_IDS:
            count = source_text.count(old)
            if count != 1:
                raise ValueError(f"cut {cut_id} is restored but its old text occurs {count} times in the source, expected exactly 1")
            continue
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
