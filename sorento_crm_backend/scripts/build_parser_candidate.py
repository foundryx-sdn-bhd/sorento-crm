"""Build the PROMPT-PROD-LABEL candidate parser body.

Starts from the code constant, applies the two committed addenda patches (ORDER-BROADEN
and the rogue-conversation pair, read from the same addendum files the tests read), then
the owner's console edits from prod. Writes `alembic/data/chatbot_semantic_parser.candidate-20261010.txt`,
which `alembic/versions/ppl_0001_parser_candidate.py` publishes.

Run from sorento_crm_backend/:  venv/bin/python -m scripts.build_parser_candidate
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

OUTPUT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.candidate-20261010.txt"

_CERT_LINE = '  - "cert","certificate","Ikram"                          → attachment_type "certificate"'

#: (old, new) exact replacements; each old must occur exactly once in the text.
OWNER_EDITS: list[tuple[str, str]] = [
    (
        "certificate, forms, shipment, GRN, sold the most",
        "certificate, installation guide, forms, shipment, GRN, sold the most",
    ),
    (
        "→ a product's photos, images, technical drawings, 3D models, technical specs,",
        "→ a product's photos, images, installation guide, technical drawings, 3D models, technical specs,",
    ),
    (
        "forwarder, consignee, delivery warehouse, free days, container location,",
        "forwarder, consignee, warehouse, free days, container location,",
    ),
    (
        "← delivery warehouse, which warehouse it goes to",
        "← warehouse, which warehouse it goes to",
    ),
    (
        _CERT_LINE,
        _CERT_LINE + '\n  - "installation guide", "installation manual" → attachment_type "installation_guide"',
    ),
    (
        "taxonomy value (photo, video, technical drawing, 3D model, certificate), never",
        "taxonomy value (photo, video, technical drawing, 3D model, certificate, installation guide), never",
    ),
    (
        "photo, image, technical drawing/spec, 3D model, certificate. Counts when NAMED",
        "photo, image, technical drawing/spec, 3D model, certificate, installation guide. Counts when NAMED",
    ),
    (
        "(photo | technical drawing | 3D model | certificate), translating",
        "(photo | technical drawing | 3D model | certificate | installation guide), translating",
    ),
    (
        "exactly one of: photo|technical drawing|3D model|certificate; OPTIONAL",
        "exactly one of: photo|technical drawing|3D model|certificate|installation guide; OPTIONAL",
    ),
    (
        "domain product_attachment (photo/drawing)",
        "domain product_attachment (photo/drawing/installation guide)",
    ),
    (
        "kind of document (photo, video, technical drawing, 3D model,",
        "kind of document (photo, video, technical drawing, installation guide, 3D model,",
    ),
    (
        "product code, description; dimension, size",
        "product code, description; product details, dimension, size",
    ),
]


def apply_owner_edits(text: str) -> str:
    for old, new in OWNER_EDITS:
        count = text.count(old)
        if count != 1:
            raise ValueError(f"owner edit anchor found {count} times, expected once: {old!r}")
        text = text.replace(old, new)
    return text


def build() -> str:
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT
    from tests.chatbot import _order_broaden_prompt, _rogue_convo_prompt

    text = _order_broaden_prompt.patched_text(SEMANTIC_PARSER_PROMPT)
    text = _rogue_convo_prompt.patched_text(text)
    return apply_owner_edits(text)


def main() -> None:
    text = build()
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(text)} chars)")


if __name__ == "__main__":
    main()
