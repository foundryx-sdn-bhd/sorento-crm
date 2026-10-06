# PLAN: the parser's document type is trusted (ATTACH-GROUNDING-TYPO, option c)

Status: in review, PR #1490 (small fix track: backend only, no migration, no auth change). Owner hand test PASS; reviewer VERDICT ready.
Owner decision 6 Oct 2026 19:30: option (c), trust the parser. Supersedes the word-overlap
guard this PR first shipped (rejected: no word or typo matching in code).

## Bug (prod 5 Oct 2026 12:02, prompt v42)

"m495 techincal drawings": the parser returned domain `product_attachment` /
`check_product_attachment` with the entity
`{raw: "techincal drawings", hint: "attachment_type", canonical_code: "technical drawing"}`.
Grounding demoted it to `{hint: "specification", spec_key: ""}` and flipped the domain to
`master_products`: the reply was a product list, no files.

## Cause

`app/services/chatbot/head/grounding.py` re-read the customer's own words for every
`attachment_type` entity: unless every word was on the attachment word list (exact, #833),
the entity was ground against the specification registry, demoted to a spec or an empty
spec, and the domain flipped to `master_products`. The parser's `canonical_code` was never
consulted.

## Fix (option c)

1. A confident `attachment_type` entity (`confident` not false) whose `canonical_code`, whole
   and case-insensitive, is a listed document type is kept exactly as the parser filed it.
   Listed = the parser prompt's taxonomy as the owner listed it (photo, video, technical
   drawing, 3d model, certificate, installation guide) or an `attachment_types.type_name` /
   `.code`. With no `canonical_code`, `raw` is compared the same exact way.
2. Any other `attachment_type` entity is dropped from the entities (trace note
   `dropped: not a listed document type`).
3. Deleted: the word-overlap guard (`_parser_document_type`), the attachment branch's
   spec / category demotion, and the `product_attachment -> master_products` domain flip.
   No word matching and no aliases are read for a document type.
4. `parse._parser_raw` carries a copy of the verdict taken before grounding (kept from the
   first round).

#833 tests that asserted a parser-filed document type is re-read as a spec ("f trap",
"pink colour", "zebra", sizes) now assert the ruling; their spec-grounding assertions run on
the category path, where the same words still ground.

## UAC

See `attach-grounding-typo-acceptance-criteria.md`.
