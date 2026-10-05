# UAC: ATTACH-GROUNDING-TYPO

- AC-1: "m495 techincal drawings" parsed as `product_attachment` with entity
  `{raw: "techincal drawings", hint: "attachment_type", canonical_code: "technical drawing"}`
  keeps the attachment_type entity, keeps domain `product_attachment`, and the turn answers
  with the product's technical drawing files (not a product list).
- AC-2: the #833 misfilings stay demoted: "zebra" / "pink colour" (canonical "photo"), "f trap"
  and "thickness 1.2 mm" (canonical "technical drawing") are never document types.
- AC-3: a typo with no backing word and no canonical on the list ("drawngs" with canonical
  null) behaves as before (said back as a word nothing matched).
- AC-4: no fuzzy matching: a raw that shares no exact word with the parser's code is not
  rescued by it.
- AC-5: `ctx.parse._parser_raw` holds the verdict as it was before grounding (the
  attachment_type entity as the parser emitted it).
