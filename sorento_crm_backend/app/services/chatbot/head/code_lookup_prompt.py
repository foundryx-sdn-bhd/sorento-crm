"""The parser prompt's CODE LOOKUP section (CHAT-CODE-LOOKUP slice A, rule R1), as code.

Appended to a CLOSED LISTS parser version by `scripts/publish_code_lookup_prompt.py`, which
publishes it UNLABELLED; a run pins it with `prompt_overrides`, and the production label is
moved only by the owner. Its marker is the gate, with the CLOSED LISTS one: the candidate
block lists near codes, and grounding checks a product pick against the block, only for a
template that carries both (`gate_on`), so every other version keeps today's parser input.
"""
from __future__ import annotations

from app.services.chatbot.head import closed_lists_prompt

MARKER = "== CODE LOOKUP =="

SECTION = """

== CODE LOOKUP ==
Product codes in the candidates block. A "near code X (name)" line under a word with a digit is a code on file
spelt near a word that is NOT on file as typed. Pick it (hint "product", canonical_code X) only when it is plainly
the code the message means: the same digits with letters swapped, missing or one letter off. A near code with other
digits is another product. When exactly one near code is plainly meant, pick it: the assistant answers that code and
names it. When two or more are equally plausible, or none is plainly meant, pick none (canonical_code null, confident
false): the assistant asks which code.
A "set code X (name)" line is a product set: hint "product", canonical_code X; with a spec word (P-trap, S-trap, a size),
pick the set whose name carries it.
"""


def gate_on(template: str | None) -> bool:
    """True when a RAW template carries both sections (rendered values never decide it)."""
    return bool(template) and MARKER in template and closed_lists_prompt.MARKER in template
