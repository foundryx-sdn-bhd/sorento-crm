# UAC - Brand scope applies per company (BRAND-SCOPE-PER-COMPANY)

Plan: `PLAN-brand-scope-per-company-6oct.md`. World: companies SRT (Sorento) and MCH (Mocha);
brands SORENTO + CABANA under SRT, none under MCH; products `SRT-A` (SORENTO), `SRT-B` (CABANA),
`SRT-N` (SRT, no brand), `MCH-1`, `MCH-2` (MCH, no brand). "Dual" = contact in SRT and MCH with
`brand_ids = [SORENTO]`; "Mocha-only" = contact in MCH only with `brand_ids = [SORENTO]`;
"Sorento-only" = contact in SRT only with `brand_ids = [SORENTO]`.

- AC-1 Session criterion: on a session stamped `{SRT, MCH}` + `[SORENTO]`, `db.query(Product)`
  returns `SRT-A`, `MCH-1`, `MCH-2` and neither `SRT-B` nor `SRT-N`.
- AC-2 The same holds through a join from a line table to Product and through an aliased Product.
- AC-3 `product_in_scope_clauses` and `complaint_in_scope_clauses` keep MCH lines and complaints
  and drop `SRT-B` / `SRT-N` ones on that session.
- AC-4 `out_of_scope_product_codes` / `out_of_scope_product_ids` for scope `[SORENTO]` name
  `SRT-B` and `SRT-N` only; MCH codes are in scope.
- AC-5 `scope_product_filter` (orders analytics) keeps MCH product ids.
- AC-6 Sentinel: scope `{NO_BRAND_ID}` hides EVERY product, MCH included, in the criterion, the
  Core clauses and the guard helpers.
- AC-7 Parent lane unchanged: every existing CONTACT-BRAND-SCOPE test stays green (the SRT-only
  world sees SORENTO-brand products only; NULL-brand SRT products stay out).
- AC-8 REST via X-API-Key + contact_id/space_id, dual contact: stock balance, products search and
  orders by product name `SRT-A`, `MCH-1`, `MCH-2`; never `SRT-B` or `SRT-N`.
- AC-9 REST, Mocha-only contact with `[SORENTO]`: `MCH-1`, `MCH-2` returned; no SRT product
  (company scope).
- AC-10 REST, Sorento-only contact: exactly as today (`SRT-A` only).
- AC-11 Chatbot replay, dual contact: "stock MCH-1" is answered; "stock SRT-B" and "stock SRT-N"
  equal the reply for a code that does not exist.
- AC-12 Worker path: a fresh session stamped with `brand_ids=[SORENTO]` (as the two workers do)
  sees MCH products and `SRT-A` only.
- AC-13 A brand row later created under MCH and added to a contact's list makes MCH a restricted
  company for that contact: its NULL-brand products go out, products of that brand stay in.
- AC-14 The staff GET/PUT `/contacts/{id}/brands` behaviour is unchanged (existing API tests green).
