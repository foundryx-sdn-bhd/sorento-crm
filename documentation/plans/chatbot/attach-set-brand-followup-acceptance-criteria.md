# UAC: ATTACH-SET-BRAND-FOLLOWUP

Plan: `PLAN-attach-set-brand-followup-06oct.md`.

- AC-1: After "which tap has technical drawing" is answered as a counted set with a brand
  breakdown, "sorento" calls `crm_master_product_attachments_list` with the SAME
  `attachment_type_ids` the first turn's call carried, beside the Sorento product ids.
- AC-2: Every row the brand follow-up lists is a file of that document type; the header
  ("Sorento taps with technical drawings") and the rows agree.
- AC-3: A count answer ("10") after the same first turn carries the same `attachment_type_ids`.
- AC-4: `set_key` written by `set_page_carry` records the document-type ids the first answer
  fetched under; a fetch with no attachment-type entity writes no such key, and a carry
  without the key behaves exactly as before (products only).
- AC-5: No wording change: the first turn's reply and the follow-up's header are byte-equal
  to what main produces for the same world.
- AC-6: A product-scoped attachment fetch (a product code plus a document word) is unchanged
  (#1502 direction preserved).
