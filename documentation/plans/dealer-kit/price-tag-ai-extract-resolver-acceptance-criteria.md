# UAC: price tag AI extract through the entity resolver; Add part only with a combo

Plan: PLAN-price-tag-ai-extract-resolver.md

## S1 One matcher

- AC-S1-1 `[BE]` An extracted `product_code` of `Srt6536 DIY` on a company holding active product
  `SRT6536-DIY` returns `product_code="SRT6536-DIY"`, `match="product"`, `product_id=<row id>`.
  Same for `srt384-6 diy` -> `SRT384-6-DIY` and `SRTKT74SS BL` -> `SRTKT74SS-BL` (parametrized
  over dash / space / case variants; the separator matrix
  `tests/test_entity_resolver_separator_matrix.py` is the reference).
- AC-S1-2 `[BE]` A code with no exact match keeps the raw text, `match=None`, both ids `None`.
- AC-S1-3 `[BE]` A code that matches a `ProductSet.set_code` returns `match="product_set"` and
  `product_set_id`.
- AC-S1-4 `[BE]` A product of another company is not matched (company scope of the portal token).
- AC-S1-5 `[BE]` `_canonical_product_code` no longer exists; the service imports and calls
  `resolve_references` (assert with a spy that it is called once per extract with the whole
  code list, `enable_prefix_fallback=False`, `enable_embedding_fallback=False`).
- AC-S1-6 `[FE]` The extract dialog shows "Matched product" / "Matched set" / "Not found" per row
  from the payload alone; `lookupTagItems` is not called during extract.
- AC-S1-7 `[FE]` Apply adds a line for every matched row (product or set) and toasts "Not found:
  ..." for the rest, unchanged from today.

## S2 Add part only with a combo

- AC-S2-1 `[FE]` A product line whose combos lookup returned an empty list shows no "Add part"
  search. Existing part rows on that line still render with Remove.
- AC-S2-2 `[FE]` A product line whose lookup returned one or more combos shows "Add part".
- AC-S2-3 `[FE]` Before the lookup answers, no "Add part" search (no flash).
- AC-S2-4 `[BE]` Create / replace lines with a non-empty `parts` on a product line whose product
  has no `ProductCombo` returns 422 naming the line index; the request is unchanged.
- AC-S2-5 `[BE]` The same payload on a product with a combo is accepted (regression).

## S3 One row when a line has one tag and no parts

- AC-S3-1 `[FE]` On the detail Lines tab a product line with one tag and no parts renders ONE
  `<tr>`: Type badge, code, name, qty, the tag's List Price, Sell Price (and override line),
  Remarks, tag status (Designed / No tag, plus Changed when the tag has a pending change) and
  the Review / Design buttons. No `1a` text anywhere on that row.
- AC-S3-2 `[FE]` A line with two tags renders the line row plus `1a` and `1b` tag rows, as today.
- AC-S3-3 `[FE]` A line with one tag and one or more parts renders the line row, the part rows
  and the `1a` tag row, as today.
- AC-S3-4 `[FE]` The Design button on a folded row opens the designer for that tag (same
  `openDesignerForTag(tag.id)`), and Review opens the change review for it.

## S4 Designer rail: one block per line when the line has one tag and no parts

- AC-S4-1 `[FE]` A line with one tag, no parts and no open group renders one block: code, name
  (when it differs from the code), `Qty <n> / <family> / LP <price>` (or SP / Override per the
  existing rules), the designed check, the open-pins count. No "1a" text. Clicking the block
  selects that tag (`onSelect(tag.id)`), and the block carries the selected highlight.
- AC-S4-2 `[FE]` "Use template for tag 1a" and the Review button (when the tag has a pending
  change) are on that block; no Remove button.
- AC-S4-3 `[FE]` A line with two tags renders the line block plus `1a` / `1b` tag rows, as today.
- AC-S4-4 `[FE]` A line with one tag and an open group keeps the tag row (the Split / Pick one
  controls live there).

## S5 Tag Size panel collapsible

- AC-S5-1 `[FE]` `TagSizeControl` renders collapsed by default: the "Tag Size" heading with the
  current size beside it (`95 x 44.5 mm`), and no preset select, W / H inputs or "Apply to all
  lines" button in the document.
- AC-S5-2 `[FE]` Clicking the heading opens it (select, inputs, buttons present); clicking again
  collapses it. The trigger is a button with `aria-expanded`.
- AC-S5-3 `[FE]` With `localStorage["dealer-kit.tag-size.open"] === "1"` it renders open; opening
  or closing writes the key. A throwing `localStorage` does not break the render (collapsed).
- AC-S5-4 `[FE]` Existing `TagSizeControl.test.tsx` cases that interact with the select or
  inputs open the panel first and still pass.

## S6 price_tag_update is a configurable use case

- AC-S6-1 `[BE]` `"price_tag_update" in TEMPLATE_DEFAULT_USE_CASES`; `GET` of the template
  defaults endpoint lists a row for it; `set_default` for it with a template whose one param
  maps to `message` is accepted.
- AC-S6-2 `[BE]` `build_context_vars(db, use_case="price_tag_update", business_id=<request id>,
  identifier=...)` returns `entity_number == request.doc_number`, `status == request.status`,
  and a non-empty `portal_url` when the portal link resolves.
- AC-S6-3 `[BE]` With a valid default configured and the window closed, `notify_salesperson`
  sends the template (the `send_text_or_template` spy sees `use_case="price_tag_update"` and
  context vars carrying `entity_number`) and logs a success row.
- AC-S6-4 `[FE]` The WhatsApp Templates settings page renders a "Price Tag Request - Update"
  row in the update group with "Set template" when unset.

## S7 The price tag send is addressed and logged by respond_io_id

- AC-S7-1 `[BE]` For a request whose contact has `respond_io_id="900000008"`,
  `notify_salesperson` calls `send_text_or_template` with `identifier="900000008"` and both the
  success and the failed `IntegrationLog` rows carry `external_reference="900000008"` and an
  endpoint ending `contact/id:900000008/message`.
- AC-S7-2 `[BE]` A contact with no `respond_io_id` falls back to the contact's id (today's
  behaviour), so nothing is dropped.
- AC-S7-3 `[BE]` `GET /api/v1/system/respond-outbox?business_table=price_tag_requests` returns
  that row with `contact_name` and `contact_phone` filled and `message_text` equal to the sent
  copy.

## S8 Rail badge never overlaps the actions

- AC-S8-1 `[FE]` On a tag row (and a folded line block) with open pins, the count element is a
  sibling of the row button inside the action group, not a descendant of the button, and it
  precedes the "Use template for tag 1a" button in DOM order.
- AC-S8-2 `[FE]` The row button carries a right padding class that clears the action group
  (`pr-20` or wider).

## S9 Done from the designer's pin popover

- AC-S9-1 `[FE]` Opening a pin marker on the canvas shows the comment with a "Done" button when
  the comment is open, "Reopen" when it is resolved.
- AC-S9-2 `[FE]` Clicking Done calls `setReviewCommentResolved(requestId, commentId, true)`, then
  the list is re-fetched: the marker turns grey, the caption reads "Round N / Done", the rail
  count for that tag drops by one and the CTA reads `Mark design ready` with the new count.
- AC-S9-3 `[FE]` A failed PATCH toasts "Could not update the change request" and the pin stays
  open.
- AC-S9-4 `[FE]` `TagCanvasEditor` without `onReviewPinResolve` renders the popover with no
  Done button (the template page and any read-only surface are unchanged).

## S10 Approved: back to the designer to print and hand over

- AC-S10-1 `[FE]` `priceTagActions('approved', ...)` returns `design` labelled "Open design"
  first, for `print_by` office and self alike; the same at `ready_for_collection`. The other
  actions at those statuses are unchanged (existing cases in `priceTagRequestActions.test.ts`
  keep passing with the new first element).
- AC-S10-2 `[FE]` The detail page at `approved` shows "Open design" as the primary and the
  per-tag Design buttons in the Actions column.
- AC-S10-3 `[FE]` The designer at `approved` + office shows "Export PDF" and the primary
  "Mark ready for collection" in the request bar; no "Mark design ready". Clicking Export
  calls `exportTagSheet(requestId)` once with no sheet filter; clicking Mark ready calls
  `markReadyForCollection(requestId)`, toasts "Marked ready for collection" and re-fetches
  the request.
- AC-S10-4 `[FE]` The designer at `approved` + self shows "Export PDF" only; at `designing` the
  bar is exactly as today (the existing "exactly one button" test still passes).

## S11 The PDF render finds the frontend without a second setting

- AC-S11-1 `[BE]` With `DEALER_KIT_PRINT_BASE_URL` unset and `FRONTEND_BASE_URL=https://fe.example`,
  `_tag_sheet_print_url(download_id)` starts with `https://fe.example/c/print/tag-sheet/`; the
  same for `_print_url`.
- AC-S11-2 `[BE]` With both set, `DEALER_KIT_PRINT_BASE_URL` wins.
- AC-S11-3 `[BE]` With neither set, the base is `http://localhost:3000` (today's default).

## S12 One candidate is not a choice

- AC-S12-1 `[FE]` A combo whose choice group "Kitchen Tap" has one candidate fills the line
  with a resolved part row (code shown, role "Kitchen Tap"), no "Not sure, any of 1" select.
- AC-S12-2 `[FE]` A group with two candidates still yields the open row.

## S13 No open-row copy

- AC-S13-1 `[FE]` The text "Marketing will prepare one tag per option" appears nowhere in the
  form for a line with an open row.

## S14 Restore a removed package part

- AC-S14-1 `[FE]` After removing the "Kitchen Tap" row, the package warning reads
  "Missing: Kitchen Tap" and carries a "Restore" button; clicking it puts the row back (resolved
  when the group has one candidate, open otherwise) and the warning clears.
- AC-S14-2 `[FE]` Restore on a line missing one fixed part and one group adds exactly those
  two rows and leaves the rows already present untouched.
- AC-S14-3 `[FE]` No Restore button when the line has no `combo_id` or no warning.

## S15 Spec fields carry no unit

- AC-S15-1 `[FE]` `{{spec.diameter}}` with value "407" and unit "mm" renders "407".
  `L{{spec.dim_length}}XW{{spec.dim_width}}XH{{spec.dim_height}}mm` renders
  `L860XW480XH250mm`. `{{product.dimensions}}` still renders the composed string.

## S16 Copy the rendered text

- AC-S16-1 `[FE]` Inspector Content for a layer whose content holds a token shows the rendered
  text beneath the textarea and a button labelled "Copy rendered text"; clicking writes that
  text to the clipboard and toasts "Copied".
- AC-S16-2 `[FE]` A layer whose content has no token shows no preview line.

## S17 Price badge amount honours Text colour

- AC-S17-1 `[FE]` An unboxed price badge with `textColor: '#FFFFFF'` and a resolved amount
  draws the amount `Text` with fill `#FFFFFF`; with no amount the placeholder stays `#999999`.
- AC-S17-2 `[FE]` The boxed badge still draws label, amount and NETT with `textColor`.

## S18 Parts on the tag

- AC-S18-1 `[FE]` Insert field lists "Parts (codes)" and "Parts (names)" in the Line group.
  `{{line.parts}}` on a line with parts `[SRTKT71SS-BL]` renders `SRTKT71SS-BL`; two parts
  join with ", ". `{{line.parts_names}}` renders the names the same way.
- AC-S18-2 `[FE]` On a product binding (template page) both resolve to null (the token's
  unanswered form); on a line with no parts, to an empty string.

## Verification

- pytest: the two new BE files green; `tests/test_ai_extract_service.py` and
  `tests/test_price_tag_*` still green.
- vitest: the two touched FE spec files green.
- agent-browser on :3080: portal price tag form, paste the 13-line text from the owner's 15 Sep
  screenshot as a .txt, Extract with AI, expect >= 9 "Matched product" rows; after Apply, a line
  for `SRTWT8203` shows no "Add part" if it has no combo.
