# UAC: SPEC-CLASS-QUADRATIC

Plan: `PLAN-spec-class-quadratic.md`.

- AC-SCQ-01: `search_specs(exact=True, free_terms=[<15-line code list>, *tokens])` issues fewer
  than 50 SQL statements for the class-resolution stage (was ~10,000 on main).
- AC-SCQ-02: within one request, the class vocabulary (ProductCategory rows, stored class
  labels, the `class` registry row) is read from the database at most once per source,
  however many terms or spans are resolved.
- AC-SCQ-03: `resolve_classes_for_term` returns the same list for every term as before the
  change (label, synonym, stored label, registry synonym, shipped `CLASS_SYNONYMS`), including
  after a category row is added or changed inside the same session.
- AC-SCQ-04: `_exact_classes` runs once per distinct free term inside `search_specs`; the
  two-types (`None`) verdict is unchanged.
- AC-SCQ-05: every existing test touching `product_spec_search`, `product_class_signal`, the
  resolve route and chatbot grounding passes unchanged.
- AC-SCQ-06: the replayed 15-line message resolves in well under the n8n budget on the test DB
  (wall time and SQL count reported before and after in the PR).
