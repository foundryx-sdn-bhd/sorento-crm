# PLAN: product info labels follow the user's language (CHAT-PRODUCT-LABELS)

Status: Plan. Waiting on #1448 (CHAT-LANGUAGE) to merge before any code. Track: small fix (size S,
no migration, no auth/RBAC change, no new ingest surface). Follow-up A of #1448.

## Owner ask (4 Oct 2026)

Product info replies still print English labels for Malay and Chinese contacts. Add them to the
chatbot label catalog so they translate like the rest.

## Facts (#1448 head 8d633987)

- Catalog: `sorento_crm_backend/app/services/chatbot/label_catalog.py`, `LABELS` (:31-387),
  `INLINE` (:484), `VALUE_WORDS` (:579). `resolve()` seeds rows into `translation_memory` on first
  use; a `manual` row wins.
- Product tools are NOT in `fetch.py::_LOCALIZED_TOOLS` (:2624), so `output_structurer` prints
  them in English. The final reply pass (`engine._localize_result`, :2672) runs
  `Localizer.reply` -> `lines()` over the whole reply, whose rule 3 translates any catalogued
  `*<label>:* <value>` label, and the value only when it is exactly a `VALUE_WORDS` entry.
- English with no catalog entry today, from `sorento_crm_mcp/sorento_crm_mcp/presenters.py`:
  - `_products` (:1062-1088): Description, List Price, Dimensions, value "Not defined";
  - `_promotion_products` (:1039-1058): Promotion, Selling Price, List Price, Dimensions;
  - `_product_attachments` (:1094+): Description, Dimensions, Attachment Type, File Name,
    Certificate Number, Valid Until, Validity, File Link.
- `lanes/business/fetch.py`: the synthetic "Specs" field (:1975), the value "not recorded yet"
  (:2926) and the whole-line access note "I can't share the {label} <em dash> please check with
  the office." (:2943, printed one per line at :3212).

## Design (simplest thing that works)

1. Add every label above to `LABELS` with ms + zh text (one fact per entry, same style).
2. Add "Not defined" and "not recorded yet" to `LABELS` and to `VALUE_WORDS`, so they translate
   only as the whole value after a catalogued label (never inside data).
3. Add the access note sentence to `LABELS` (spelled with `—` so the source stays dash-free).
   It always prints as its own line, so `lines()` matches it whole; no `INLINE` entry needed.
   `{label}` stays a placeholder.
4. No change to `_LOCALIZED_TOOLS`, the presenters, or the spec registry labels.

Out of scope (follow-ups B/C or not asked): spec registry labels inside "Specs", product data
values (descriptions, names), the Validity value words (Valid / Expiring soon / Expired), the
"(file link unavailable right now)" value, and `_promotions`' Start Date / End Date.

## Tests (red first)

`sorento_crm_backend/tests/chatbot/test_chat_product_labels.py`, see the UAC.
