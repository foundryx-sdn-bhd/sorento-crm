# PLAN: a typo'd attachment type is no longer demoted to an empty spec (ATTACH-GROUNDING-TYPO)

Status: in progress (small fix track: backend only, no migration, no auth change).

## Bug (prod 5 Oct 2026 12:02, prompt v42)

"m495 techincal drawings": the parser returned domain `product_attachment` /
`check_product_attachment` with the entity
`{raw: "techincal drawings", hint: "attachment_type", canonical_code: "technical drawing"}`.
Grounding demoted it to `{hint: "specification", spec_key: ""}` and flipped the domain to
`master_products`: the reply was a product list, no files.

## Cause

- `app/services/chatbot/head/grounding.py` attachment branch: `_on_attachment_list` reads only
  `raw`, and every word must be on `vocab.attachment_words` exactly ("techincal" is not, and
  fuzzy matching was removed on purpose in #833, `_same`). Nothing grounds, so the entity
  becomes `Grounded("", "", rest)`, an empty specification, and the domain flips.
- The parser's own `canonical_code` ("technical drawing", on the list) is never consulted.
- `engine.py` passes the grounded verdict as `parse._parser_raw`, so the trace shows the
  demoted entity, not what the parser said.

## Fix

1. The parser's `canonical_code` counts as the document type when it is on the attachment
   list AND the customer's own words back it: at least one word of `raw` is, exactly (case,
   NFKC and a plural "s" aside, the `_same` rule), a word of that canonical code. "techincal
   drawings" -> "technical drawing" shares "drawing": kept as the document type. The #833
   misfilings keep their demotion, because the parser's code shares no word with what was
   typed: "zebra" -> "photo", "pink colour" -> "photo", "f trap" / "thickness 1.2 mm" ->
   "technical drawing". No fuzzy matching, no keyword list on user text: the only words read
   are the parser's own code.
2. `parse._parser_raw` carries a copy of the verdict taken before grounding.

Neither on the list (raw nor backed canonical): current behaviour, unchanged.

## UAC

See `attach-grounding-typo-acceptance-criteria.md`.
