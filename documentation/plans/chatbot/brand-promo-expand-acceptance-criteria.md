# UAC: BRAND-PROMO-EXPAND

Plan: `PLAN-brand-promo-expand-06oct.md`.

- AC-1 A parser entity hinted `brand` or `category` whose only resolver hits are `promotion`
  rows keeps its hint after reconciliation. Nothing is traced under `reconciled` for it.
- AC-2 After a promotion-domain turn naming a brand and a category, `focus.extra` holds no
  `promotion` row for those words; the brand sits on `focus.brands`.
- AC-3 A later inventory turn (reuse or replace_combine) sends no promotion-hinted token to
  the resolver and no `promotion_ids` to the inventory tool.
- AC-4 A promotion-domain turn naming a brand still fetches that brand's promotions (the
  resolver expansion is unchanged for the promotion fetch).
- AC-5 Photo shape: a product with uuid plus the brand SORENTO under inventory leaves the brand
  on the focus as a brand; the fill turn "1" answers with the availability line.
- AC-6 No parser prompt change; no typo/word/fuzzy matching added; fixtures use fake ids only.
