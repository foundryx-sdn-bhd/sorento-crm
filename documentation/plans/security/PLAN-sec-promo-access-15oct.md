# PLAN: SEC-PROMO-ACCESS - chatbot promo files fail-open past contact access

Status: in progress (small fix track, security: security-reviewer required). Lane SEC-PROMO-ACCESS.

## Finding
A dealer contact holding only access type `dealer` asked "Any promo for Cabana WC?" and got 6 Cabana
files, including OFFICE USE and end_user flyers. The chatbot sent `access_levels=[]` to the backend;
the backend turned the empty list into "no filter".

## Fix (defence in depth)
1. Chatbot: fail closed when the brand gate empties the contact's levels, reusing the existing
   "no access to <brand> promotions" wording.
2. Backend: on contact-scoped promotion reads, an empty access list means "nothing", never "no filter".
   Staff / internal callers (no access_levels supplied at all) unchanged.
3. Sweep the other contact-scoped document paths for the same pattern.

## Tests (red first)
- Backend: dealer-scoped request with `access_levels=[]` (or present-but-empty) returns zero Cabana files.
- Chatbot: brand_gate_empty / empty recomposed list -> no-access reply, no fetch.
