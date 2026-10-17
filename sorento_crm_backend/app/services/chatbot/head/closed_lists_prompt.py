"""The parser prompt's CLOSED LISTS section (CHAT-CLOSED-LISTS S3, design D2), as code.

Appended to a parser version by `scripts/publish_closed_lists_prompt.py`, which publishes it
UNLABELLED; a run pins it with `prompt_overrides`, and the production label is moved only by
the owner. The section's marker is also the gate: the engine builds the per-turn candidate
block, and grounding validates picks against the lists, only for a template that carries it
(`gate_on`), so every other version keeps today's parser input byte for byte.

The two lists are registry variables (`chatbot_prompt_vars`), rendered from their tables at
each read; the candidate lines come from `head/candidates.py`.
"""
from __future__ import annotations

MARKER = "== CLOSED LISTS =="

#: The gated schema's named "no item is the kind asked" value (S6, 17 Oct 2026): with null as
#: the only way out, the parser forced an unlisted kind onto the nearest listed type.
NOT_LISTED = "none of these"

SECTION = """

== CLOSED LISTS ==
File types, certification types and specification values are CLOSED lists. An entity names
an item of a list exactly as listed, or names nothing; it never carries a word of its own
for one of them. Every entity has one more key, "listed_item": for an "attachment_type"
entity, the item below it means (the code after "File type", "Certificate kind" or
"Certificate scheme", exactly as written there); null for every other entity, and null when
no item below is what the message asks for. This section OVERRIDES every earlier rule on
how an attachment_type is written: the earlier rules still say WHICH kind of document is
meant, and listed_item says which item of these lists that is (a drawing, TD, CAD or spec
sheet is the item that holds drawings; a photo, image or picture the item that holds photos).
A file asked for in any language is the same entity and the same item: a message in Malay
or Chinese asking to see or send a kind of file carries that attachment_type entity exactly
as the English ask would. listed_item is the item that IS the kind asked, never the nearest one: when no line below is
that kind (a brochure, a BIM file or a warranty card when no such line is listed), listed_item
is "none of these" and the assistant asks which file, offering what the product really has.

File types (each line: the code, the name, and what it holds):
{{attachment_types}}

Certification types. A certificate ask (cert, certificate, sijil, or a scheme named) is an
"attachment_type" entity whose listed_item is the code after "Certificate kind"; a message
with a certificate word always carries this entity, whatever scheme word stands beside it
("span certificate", "pps cert"). A scheme the message names that IS listed after
"Certificate scheme" is one more "attachment_type" entity: raw the word typed, listed_item
the scheme exactly as listed. A scheme word that is not listed is no entity of its own: the
certificate ask stays the kind alone.
{{certification_types}}

Candidates for words of this message. The user turn may end with this block: per word or
phrase of the message, what is on file under it, each saying what it is:
- "code X (name)": hint "product", canonical_code X.
- "product type T (K = V)": hint "category", canonical_code V.
- "spec K = V (label)": hint "specification", spec_key K, spec_value V.
- "spec K = N": a numeric specification, spec_key K, spec_value the number N.
- "near ...": the same, for a word spelt near it; pick it only when it is what the
  message means.
Pick the candidate the message means, or none. A product type or a specification the
message names is never also a file type: a file type is an entity only when the message
asks for a file. When nothing listed fits a word the message needs, leave canonical_code
(and spec_value) null and set confident false: the assistant asks.
"""


def gate_on(template: str | None) -> bool:
    """True when a RAW template carries the section (rendered values never decide it)."""
    return bool(template) and MARKER in template
