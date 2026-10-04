# Text glossary S1+S2 Phase 2 - live verification, 10 Sep 2026

Lane stack: backend `venv/bin/uvicorn app.main:app --reload --port 8081` against a
private DB copy (`sorento_ai_automation_tg`, bootstrapped via `scripts/bootstrap_env.py`
+ every catalog module installed for the default tenant); FE `PORT=3081 npm run dev`
with `FASTAPI_INTERNAL_URL=http://localhost:8081`. Logged in as `person38@example.com`
(admin role, seeded directly since the private DB starts with no users).

## Setup

- Seeded a Jiexia supplier (`suppliers` row, company-stamped) since the private DB has
  no reference data.
- Uploaded `tests/scm/fixtures/jiexia_proforma_invoice_sample.xls` alone first (Test ->
  Confirm, currency RMB - the fixture states none), producing PI-2609-001 and
  PI-2609-002 (one per container block).
- Re-opened Start > Upload proforma invoice, uploaded ONLY
  `jiexia_packing_list_sample.xls` on the SAME supplier - this time both container
  blocks auto-resolved ("Attaches to" showed the two just-created PI numbers), Confirm
  written both packing-row sets. Note: uploading invoice + packing list TOGETHER in one
  shot left one container's "Attaches to" combobox searching for a match against
  invoices that do not exist yet (nothing on file to match), so it never resolves on a
  brand-new install - splitting into two uploads (invoice first, packing list second)
  is the reliable path on an empty database.

## Golden path (sidebar clicks: Procurement > Supply Chain > Proforma Invoices)

1. Opened PI-2609-001, Packing tab. Four unmatched rows read raw Chinese with a dash in
   `Description (EN)`.
2. Clicked the dash on `水箱`, typed "Water tank", Enter. Toast, `PUT
   .../translations` 200. Reload: `translation_memory` holds `盆 -> Basin`... (correct
   pair `水箱 -> Water tank`), `source = manual`, `created_by` resolved to the actor.
3. Clicked the dash on `盖板`, typed "Cover plate", Enter - same result.
4. Opened PI-2609-002 (a DIFFERENT invoice, never edited directly): its own `水箱` and
   `盖板` packing rows show "Water tank" / "Cover plate" too, confirming R4 (every PI on
   file re-binds, not just the one edited) with REAL cross-invoice data, not a fixture.
5. System Management > Translations lists both entries: source text, English, `manual`,
   actor name, updated timestamp - editable in place, no "Text Glossary" entry anywhere
   (R8/R11).
6. Screenshots: `packing-tab-1280.png`, `packing-tab-375.png` (both usable and
   non-clipped, table scrolls horizontally at 375 like every other PI grid),
   `translations-admin-1280.png`.

## Bug found and fixed during this run

Pressing Enter fired the save correctly once, but the browser (never jsdom - the vitest
suite never showed this) fired a SECOND identical `onBlur` after the cell swapped from
the `<Input>` back to the read-mode button: removing a still-focused element from the
DOM makes the browser dispatch a real `blur` on it, replaying `DescriptionEnCell`'s
`onBlur={save}` with the same already-saved value before React finished unmounting. The
second PUT hit `uq_translation_memory_phrase` and 500'd (network tab showed one 200 then
one 500 per Enter-save). Fixed with a `savingRef` guard in `DescriptionEnCell.tsx`: reset
to `false` when a NEW edit starts, set `true` the moment a save is dispatched, and
`save()` is a no-op while it is already `true`. Verified live afterward: exactly one PUT
(200) per Enter-save, no console errors.
