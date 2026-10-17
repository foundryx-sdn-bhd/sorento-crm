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
for one of them. This section OVERRIDES every earlier rule on how an attachment_type's
canonical_code is written: the earlier rules still say WHICH kind of document is meant, and
canonical_code is never one of their taxonomy words (photo, video, technical drawing,
3D model, certificate) but the code of the matching line below.

File types. An entity with hint "attachment_type" takes canonical_code = the code after
"File type" on the line whose name or words mean the kind asked for (a drawing, TD, CAD or
spec sheet is the line whose words say drawing; a photo or image the line whose words say
photo):
{{attachment_types}}

Certification types. A certificate ask (cert, certificate, sijil, or a scheme named) is an
entity with hint "attachment_type" and canonical_code = the code after "Certificate kind";
a message with a certificate word always carries this entity, whatever scheme word stands
beside it ("span certificate", "pps cert").
A scheme the message names that IS listed after "Certificate scheme" is one more
"attachment_type" entity: raw the word typed, canonical_code the scheme exactly as listed
(this replaces the earlier rule that leaves a scheme's canonical_code null). A scheme word
that is not listed is no entity of its own: the certificate ask stays the kind alone.
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
