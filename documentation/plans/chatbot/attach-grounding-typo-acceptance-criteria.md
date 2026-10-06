# UAC: ATTACH-GROUNDING-TYPO (option c, trust the parser)

- AC-1: "SRTWC286 TD DRAW", "m495 techincal drawings" (parser canonical `technical drawing`)
  and "<code> phtos" (parser canonical `photo`) stay `product_attachment`, fetch through
  `crm_master_product_attachments_list` and reply with that type's files.
- AC-2: a confident attachment_type whose canonical_code is a listed type (photo, video,
  technical drawing, 3d model, certificate, installation guide, or an `attachment_types`
  name or code, whole and case-insensitive) is kept as filed, whatever the customer typed.
- AC-3: an attachment_type whose canonical_code is not a listed type (or is a word of one, or
  the parser was not confident) is dropped; no attachment fetch follows from it.
- AC-4: no word or typo matching and no alias lookup decides a document type; no attachment
  entity is demoted to a specification and the domain is never flipped to master_products
  for it.
- AC-5: `ctx.parse._parser_raw` holds the verdict as it was before grounding.
