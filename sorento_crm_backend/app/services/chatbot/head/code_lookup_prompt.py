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
names it. When two or more are equally plausible, or none is plainly meant, pick none (canonical_code null): the assistant
asks which code. A typed word that is not on file is never its own canonical_code: it is a listed code or null.
A "set code X (name)" line is a product set: hint "product", canonical_code X; with a spec word (P-trap, S-trap, a size),
pick the set whose name carries it.
When the typed code's own name carries another spec than the one the message names (a typed set named S-TRAP 250MM, a
message saying P-trap), pick the listed set or near set code whose name carries the spec the message names.

Asked attributes. For domain_hint "master_products", an attribute the message asks about that is not on the
attribute list (weight, material, warranty) is still emitted in requested_attributes, in the user's own word: the
assistant gives its value or says it is not recorded. This overrides the closed vocabulary of REQUESTED ATTRIBUTES
for master_products only; every other domain keeps its own rule.

A count beside a code. A product code with a count beside it ("5unit", "x 10", "3 pcs") and no purpose word (price,
ETA, drawing, order, delivery) is a stock ask whatever the previous domain: domain_hint "inventory", intent_hint
"check_stock", domain_in_message true, entity_op "replace", the count as quantity. This overrides BARE ENTITY
CONTINUATION for a code with a count.

A bare code. A bare code after an answer, with no count and no purpose word, reuses that answer's domain (after an
ETA answer, its ETA; after a stock answer, its stock), as BARE ENTITY CONTINUATION says.

When deliver. A question asking WHEN goods will be delivered to, or reach, a customer or a shop, in any wording or
language, is an outstanding-order ask, for example "when deliver", "when will ... be delivered", "bila hantar", "几时送",
and their misspellings ("wen deliver", "whn deliver", "deliverd"). Such questions are ADDED to the "outstanding" word
list of ORDER_STATUS FILTER: they count as the current message's own explicit words, and "Never infer from context"
does not apply to them. Such a message about a product (and a customer) is status "outstanding", domain_hint "order",
intent_hint "check_order", the product and the customer as entities. It is not a delivery list ask, so the assistant
never asks which period for it. An arrival ask about stock coming in ("when ... arrive", "ETA", "sampai dari kilang",
"到货") stays incoming, and a "delivered" / "sudah hantar" ask stays status "delivered".

A spec word that picks a set or a variant is not a requested attribute; requested_attributes holds only attributes
the message asks about and never one it does not name (no "discontinued" unless the message asks whether it is discontinued).
"""


def gate_on(template: str | None) -> bool:
    """True when a RAW template carries both sections (rendered values never decide it)."""
    return bool(template) and MARKER in template and closed_lists_prompt.MARKER in template
