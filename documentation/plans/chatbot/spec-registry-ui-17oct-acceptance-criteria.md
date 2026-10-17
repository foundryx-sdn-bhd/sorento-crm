# UAC: spec-registry-ui (17 Oct 2026)

Plan: `PLAN-spec-registry-ui-17oct.md`. Screen: Master Data > Product Specifications > a field's record page.

## AC-1 Chatbot meaning (card Q1)
- AC-1.1 The Details tab shows "What this means to the chatbot" for every field, view and edit, same position.
  Empty in view mode renders "-".
- AC-1.2 Saving it stores `parser_hint` (trimmed, one line: line breaks collapse to a space, at most 200 chars;
  201 is refused with a 422 naming the limit). Needs `master_data.spec_registry.edit`; a caller holding only
  `master_data.products.edit` gets 403.
- AC-1.3 `{{specs}}` prints it on that field's line: `Specification trap_length ("Trap outlet length"): a number in mm.
  Means: <hint>. Words: trap.` (enum and boolean lines likewise, after the head). A blank hint prints nothing.
- AC-1.4 The next parser render after the commit carries the new text (registry cache cleared on commit), no publish.
- AC-1.5 `seed_spec_registry` never writes `parser_hint`.

## AC-2 One word, one field (card Q2)
- AC-2.1 Saving a NEW word (a value's word or an "other name") that normalises equal to another ACTIVE OR INACTIVE
  field's word, name (label) or key is refused: 409, code `spec_registry_word_on_other_key`, message
  `"<word>" is already a word for <Other field label>.` Nothing in the save is stored.
- AC-2.2 Words the field already held before the save are not judged (shipped overlaps keep saving).
- AC-2.3 A suppressed word on the other field does not count (it is not that field's word any more).
- AC-2.4 The words grid shows the refusal under the input; the draft keeps the typed word.

## AC-3 Name is the owner's once edited (card Q3)
- AC-3.1 PATCH with a changed `label` on a shipped field marks it owned; a later `seed_spec_registry` keeps the
  edited label and still repairs the other seed-owned columns.
- AC-3.2 An unedited shipped label is still repaired.

## AC-4 Unit read-only (card Q4)
- AC-4.1 Details edit mode shows Unit as text with the short reason beside it; no input.
- AC-4.2 The save payload no longer carries `unit`.

## AC-5 Try in chatbot (card Q5)
- AC-5.1 The record gear has "Try in chatbot" (visible with `master_data.spec_registry.edit`).
- AC-5.2 Running a message calls the real parser with the current rendered prompt and returns the picks:
  each specification (field label + value as the page shows it), product class, and any closed-list item
  (file type / certification type). Writes no row anywhere and sends nothing.
- AC-5.3 Parser unavailable (no key, timeout, bad JSON): the dialog shows an error line, no crash.
- AC-5.4 Message at most 500 chars; empty is refused client side.

## AC-6 Links and history
- AC-6.1 The gear links to Attachment Types and Lookup Sets.
- AC-6.2 A History tab lists this field's audit rows (`AuditTrail`, entity `product_spec_registry`), empty state
  heading + hint.

## AC-7 Layout
- AC-7.1 Usable, non-clipped at 375px and 1280px; View and Edit share the same layout.
