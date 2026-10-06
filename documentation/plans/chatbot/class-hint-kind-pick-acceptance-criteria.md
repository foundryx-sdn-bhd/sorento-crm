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
AC-7. D2 control `test_rearch_r11_catalogue.py::TestGenuinelyAmbiguousWordStillAsks`
      stays green under Q1 reading (a).
AC-8. No new wording: a zero set, an unknown class and a miss use the existing sentences.
AC-9. Kill test: with the fix reverted, AC-1 to AC-5 are red again.
