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

SECTION = """

== CLOSED LISTS ==
File types, certification types and specification values are CLOSED lists. An entity names
an item of a list exactly as listed, or names nothing; it never carries a word of its own
for one of them.

File types. An entity with hint "attachment_type" takes canonical_code = the code after
"File type" on one of these lines; the line's words are what customers call it:
{{attachment_types}}

Certification types. A certificate ask is an entity with hint "attachment_type" and
canonical_code = the code after "Certificate kind". A scheme the message names is one more
"attachment_type" entity whose canonical_code is the scheme exactly as listed after
"Certificate scheme". A scheme word that is not listed is no entity: the certificate ask
stays the kind alone.
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
