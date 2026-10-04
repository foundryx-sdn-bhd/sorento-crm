# PLAN: chatbot replies follow the user's language (CHAT-LANGUAGE)

Status: Build complete, slices 1-4 reviewed (reviewer + security-reviewer, kill tests) and in hand test; slice 5 (required-fields helper, #1445 landed 3 Oct) in progress under the lead pattern (tester, coder, reviewer subagents). Track: full (size L, cloud lane; touches the turn engine and the translation module). The direction was approved by the owner on 2 Oct 2026. Behaviour card Q1-Q5 all answered (a) on 2 Oct (owner Q1-Q3, crew Q4-Q5).

## Owner ask

Every chatbot reply follows the USER'S language, in all asks, including business answers that
are fixed English label/value lines today (stock, PO/SO/SPO, outstanding, top-selling). Use the
translation module.

## Facts (origin/main, 2 Oct)

- Translation module `app/services/translation_service.py`. It reads memory first (`translate`,
  :88), fills gaps with AI (`_ai_fill`, :182), and a manual row wins. It is hard-wired to zh->en:
  `_has_source_script` (:54-57) only lets text containing CJK reach the model. Table
  `translation_memory` (`app/models/translation_memory.py`): unique `(source_text, source_lang,
  target_lang)`, `source in ('manual','ai')`, not company-scoped. Admin API
  `app/api/v1/system/translations.py` (list/put/delete, `system.translations.*`).
- Reply language today is chosen only on the fallback lane: `lanes/fallback.py:111`
  `pick_language`, used at `engine.py:6582-6626`. Business answers are always English.
- Business answers are rendered by `lanes/business/fetch.py::output_structurer` (:2604). The
  `*label:* value` lines come from `_item_line` (:3015), summary lines (:2989), the intro
  (:2961-2964), flag lines (:3034-3040) and the footer (:3183). `turn/compose.py::compose` (:319)
  prints that `lane_text` verbatim (:399-400) and adds its own English sentences: "No stock found
  for ..." (:449), the escalate offer (:569), and others.
- Presenter envelope fields are `{key?, label, value, granted_value?}`
  (`sorento_crm_mcp/presenters.py`). The stock paths are `_stock` (:1304), `_stock_compact`
  (:1393) and `_stock_availability` (:1514), with the verdict tails `_AVAILABILITY_TAILS`
  (:1465) and `REFER_TO_SALESMAN` (:935). The intros are at :78, :116 and :119. A compact
  location line's label is the location CODE (data, :1446).
- `field_access.FIELD_LABELS` (:129) holds admin-facing import-mapping labels, used only by
  `scm/import_mapping_service.py`. Its wording differs from the chatbot's on purpose ("ETC
  (estimated time of container closing)"), so it is NOT merged. The catalog is keyed by the
  same field keys, and one test pins that every key both tables share is catalogued.
- The conversation carries state in `respond_contacts.session_vars` between turns
  (`turn/state.py:58`). The reply language rides there; no migration is needed.

## Design

1. **Label catalog** `app/services/chatbot/label_catalog.py` (new, pure):
   - `LABELS: dict[str, dict[str, str]]`, keyed by the ENGLISH source text (the same key
     `translation_memory.source_text` uses), each entry `{"ms": ..., "zh": ...}`.
   - `FIELD_KEYS: dict[str, str]`, field key -> English label, so a field is translated by key
     when it has one and by its exact English label otherwise.
   - Sentences may carry `{token}` placeholders, e.g. "no stock at the moment, ETA {eta}.".
   - `tokens_match(src, dst)`: the same token multiset. A translation failing it is dropped.
   - `Localizer(lang, table)`: `label(field)`, `text(s)` (exact or template match, values
     re-inserted from the English match) and `tail(title)` for the "<code> x <qty>: <sentence>"
     availability titles. Anything not in the table comes back unchanged. That is the
     allowlist, and the reason a value can never be touched.
2. **Resolve once per turn, render purely** (the `chatbot/copy.resolve` pattern):
   - `label_catalog.resolve(db, lang)` makes one SELECT of `translation_memory` rows
     (`source_lang='en'`, `target_lang=lang`, `source_text IN catalog`).
   - A row wins (manual or ai). A catalog default with no row is inserted as `source='ai'`
     (`ON CONFLICT DO NOTHING`), so it appears on the Translations page and a staff edit
     (`manual`) wins from then on.
   - It returns a `Localizer`. No model call (card Q4). `en` resolves to the identity
     localizer with no DB read.
   - `translation_service` gets `upsert_defaults(db, rows, source_lang, target_lang)` (the
     write) and `lookup_pairs`. The zh->en path is untouched.
3. **Per-message detection** `app/services/chatbot/language.py` (new, pure):
   - `detect(message, *, strip=[codes, names]) -> 'en'|'ms'|'zh'|None`, per the card rules.
   - `choose(detected, conversation, saved) -> lang` is `pick_language`'s order with the
     message first.
   - The engine stores the chosen language on `session_vars["reply_language"]`.
4. **Wiring:**
   - The engine resolves the language before the business lane runs. The trigger carries
     `trigger["localizer"]`.
   - `output_structurer` reads it (default identity) at every render site listed above.
   - `turn/compose.py` gets the same localizer via `ctx` for its own sentences.
   - The fallback lane's `pick_language` call takes the chosen language as its first
     candidate, so both lanes agree.
5. **Escalate offer** (card Q5) stays English until the slice that replaces the two English
   text matchers with the pending's `escalate_offered` flag.

## Slices (one PR, commits per slice)

1. **Stock** (this slice): catalog + Localizer + resolve + detection + wiring.
   - Covers the stock detailed, compact and availability modes, the intros, flags, footer,
     "No stock found for", and the quantity ask.
   - Tests: catalog token parity; every stock label catalogued (pinned against the presenter
     literals); Localizer leaves values/unknown labels alone; detection table (card examples);
     `output_structurer` renders card examples A-D byte-exact; resolve writes missing defaults
     once and a manual row wins; session carry (example E).
2. **PO / SO / SPO / orders rows**: labels in `_purchase_orders_placed`, `_po_last_cost`,
   `_orders_so_outstanding`, `_orders_list`, `_spo_last_receipt` and the clearance pairs.
3. **Outstanding report + top selling**: these are finished text in the presenter. Each line
   prefix becomes a catalog entry; the presenter is untouched and the backend localizes the
   known line prefixes.
4. **compose.py sentences + escalate offer** (after the matcher change), the canned copy
   keys still English-only (`chatbot_reply_copy.py:279-340`), and the Translations admin page
   filter by language pair.

5. **Required-field helper (after PR #1445 lands)**: `app/services/chatbot/required_fields.py`
   carries fixed English in `FieldSpec.question`, `_miss_line` and `_pick_line`. Make them
   language-aware: `question` becomes a catalog key (English source text) rendered through the
   turn's `Localizer`, and the two line builders take the localizer like `compose` does. Start
   this slice only once #1445 is on main; until then nothing in this lane touches that file.

## REPORT-ENGINE (#1447) coordination

The catalog is the shared contract: key = English source text, field-key index, `{token}`
templates, and a `Localizer` passed in as data. A generic renderer calls `localizer.label(field)`
and `localizer.text(sentence)`; it never owns a string table of its own.

## Risks

- **Presenter tests assert English:** untouched, because the presenter stays English and the
  translation is backend-side at render.
- **Escalate regex:** see Q5. `compose`'s English offer is unchanged in slice 1.
- **`_codes_without_rows` matches `label == "Product Code"`:** figures are never mutated; only
  the rendered strings change.
- **Concurrent first use:** `ON CONFLICT DO NOTHING` handles two turns inserting the same
  default.

## Trigger to generalise later

Detection is a word list. If the owner reports wrong-language replies on real traffic, the
trigger to add a model judgement is a measured miss rate, not a guess.
