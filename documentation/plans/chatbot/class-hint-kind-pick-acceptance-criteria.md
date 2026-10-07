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
AC-5. (R2, 7 Oct 2026.) The resolver route reruns a locked token like any other, and
      drops the five name kinds (promotion, customer, attachment, transporter, form) from
      what the rerun found for it, matches and did-you-mean neighbours alike; a record
      key it names EXACTLY under a wrong hint still resolves there (a container number
      hinted `product` is the inbound shipment, owner ruling 5 of hand pass 12,
      `test_rearch_r12_handpass12_d.py`). A mis-hinted record code typed beside it keeps
      its own rerun (AC-1527).
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
       (R2, crew decision (a), 7 Oct 2026.) Under order/sales a brand-HINTED word is the
       intercept's alone: the typed word decides there (27 Sep R1, the owner's own
       transcript: "sorneto" parsed as Mocha lists Sorento), and the engine's
       canonical_code brand read (AC-13) does not run on it, so a word the intercept
       could not place is said back once and filters nothing, never swallowed ("DO brand
       zorbix" parsed as Mocha: `test_samantha_27sep_r7_owner_replay.py::
       test_an_unknown_brand_word_is_one_line_and_no_filter`, the original test, kept).
       Every other entity there, and every entity in the other domains, reads AC-13.
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
AC-14. (R2, CHATBOT-TRIO console case 12, PR #1525 comment 6032784666.) A paging turn
       ("another 10", or a bare count answering "how many should I show?") whose parse
       re-states the carried set's brand as a current-message entity under
       `entity_op: "reuse"` pages the carried set (the next rows, same header); the echo
       is dropped before `turn/apply._names_a_subject` and the resolver see it, so it is
       never an attachment type ask, never a kind pick and never a new set. A parse that
       declares a NEW value (`replace_combine`) beside the count is left to the parser as
       before.
