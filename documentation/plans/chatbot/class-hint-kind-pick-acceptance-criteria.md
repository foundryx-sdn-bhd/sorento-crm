# UAC - CLASS-HINT-KIND-PICK

Plan: `PLAN-class-hint-kind-pick-06oct.md`. Card: `CARD-class-hint-kind-pick-06oct.md`.

AC-1. "any basin with thickness 1.0mm" (parser: `basin` category confident, `1.0mm`
      thickness spec, domain master_products) never replies "Which one do you mean?" with
      a promotion or customer option, and never stores a `kind_pick` open question.
AC-2. "basin" alone (same hint) never asks a kind pick.
AC-3. The resolver's answer for a confident class word carries no promotion, customer,
      transporter or form match under that token; its only product rows are
      `spec_search` tier; the products tool receives the class's products and never a
      promotion id.
AC-4. The resolve body carries `hint_locked_tokens` with the folded token of every
      confident `product_type` / `category` entity (Q1 reading (b): `product` too); the
      key is absent when there is none; a `confident: false` class hint is not locked.
AC-5. The resolver route leaves a locked token out of the per-token rerun only; a
      mis-hinted record code typed beside it keeps its own rerun (AC-1527).
AC-6. A request without the field, or with an empty list, answers exactly as today
      (`elapsed_ms` aside).
AC-7. (Owner Q1, 6 Oct 2026: "cabana is a brand".) "cabana stock" with the parser hint
      `product`, `brand` or `customer`, over a live Brand row Cabana and promotion /
      attachment rows named cabana, replies with the existing brand-scoped counted set
      ("Here's what you want: Cabana wash basins with stock (N)" and rows), never a kind
      pick, and "cabana" lands on `focus.brands`. The D2 control in
      `test_rearch_r11_catalogue.py` is rewritten to this on the real resolver.
AC-8. No new wording: a zero set, an unknown class and a miss use the existing sentences.
AC-9. Kill test: with the fix reverted, AC-1 to AC-5 and AC-7 are red again.
AC-10. The brand read is exact only (`exact_brand_rows`: name or code, case-insensitive);
       "Sorrento" names no brand there, while `brand_rows_for_word` (order/sales intercept)
       keeps its spelling tier. Nothing is removed from the resolver's tokens.
AC-11. (Owner ruling 6 Oct 2026, replacing the rejected code gate.) A brand entity is
       RESOLVED as the brand with no hits needed: the resolve body sends `brand_tokens`
       (`{token: brand name}`), the resolver answers the token with one exact `brand` match,
       the token is never in `unresolved_tokens`, so a brand word beside a matched code
       ("SORENTO SRTWC8318") leaves nothing unanswered and the spec fallback does not open
       for it. A description beside a code still opens the spec path
       (`test_resolve_brand_routing.py`). No code gate anywhere.
AC-13. (Owner clarification.) Brand identity keys on the parser's `canonical_code` (the
       Known brands name), raw only when canonical_code is empty, never a spelling tier in
       code, never a non-confident entity: "Sorrento" / "srt" with canonical_code "Sorento"
       are Sorento; "Sorrento" with no canonical_code is not a brand.
AC-12. The request domain's own kind is never stripped: "cabana promo" under promotion
       still finds the promotions whose description carries the brand; "cabana catalog"
       under resource_attachment still narrows to the Cabana catalogue.
