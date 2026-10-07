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

Round 2 (owner hand test on the local copy, 7 Oct 2026):

- AC-7: After a brand page listed N taps, a document word alone ("pic" / "photo?", the
  parser's attachment_type entity with `entity_op: reuse`) calls the attachments tool with
  exactly the N listed product ids and that document type's `attachment_type_ids`; every row
  is a file of that type. No product the page did not list (the resolver's code-tier match
  for the class word, the COLD TAP family on the copy) is fetched.
- AC-8: After the page, `focus.products` holds the listed rows (uuid + code) and the carried
  class word is gone; the page's own carry (`focus.set_page`) is unchanged, so "another N"
  still continues the page.
- AC-9: A parser echo of listed codes (`current_message: false`) beside the document word
  changes nothing: the fetch still covers every listed row, not the echoed handful.
- AC-10: The page's own brand named again beside the document word, or alone, is a reuse:
  the listed rows stay on the focus and the document word that follows reads about them.
- AC-11: The parser's `Current subject` line names at most five products per axis and says
  how many more there are.
