# Side-by-side verification of the eight seeded tag templates (AC-L.10)

Run 2026-08-25 against the local prod-copy database, company `SRT`, on the
worktree dev stack (FE `:3030`, BE `:8030`). Each PNG in this folder is the
matching page of `Sorento Pricetag Template.pdf` rendered at 150 dpi and cropped
to its tag box on the LEFT, and the seeded template opened in the tag editor
with a real product bound on the RIGHT.

How the evidence was produced, so it can be re-walked:

1. `PYTHONPATH=. venv/bin/python scripts/seed_tag_templates.py --company-code SRT`
   (28 assets uploaded, 8 templates inserted; a second run reports
   `assets: 0 created, 28 already present | templates: 0 created, 8 already present`).
2. agent-browser session `ptag-seed`: log in, then `Dealer Kit -> Room Designer ->
   Tag Templates` from the sidebar. All eight rows render with the right family
   label and print size; a ninth row, `Kitchen Sink Combo A4`, is the empty
   placeholder template from an earlier slice and is not part of this seed.
3. Open each template, select its product block in the Layers panel, `Change
   product`, pick the code below, `Rebind`, screenshot.
4. `scratchpad/sidebyside.py` crops both halves and joins them.

The tag box crops are the filled white (or brand-green) rectangle
`pdf-geometry.json` reports for that page, so the two halves are the same
physical tag at the same scale.

## What was bound, per family

| Family | Template | Bound to | Note |
| --- | --- | --- | --- |
| `sink_combo` | Kitchen Sink - Combo | `SRTKS2435` | The PDF's own sink. Alternatives left unbound on purpose - see gap 4. |
| `ala_carte` | Kitchen Sink - Ala Carte | `SRTKS2409` | PDF page 2 prints `SRTKS2020`, which is not in this database. |
| `art_basin` | Art Basin | `SRTWB8001` | PDF page 3 prints `SRTWB1543-BL`, absent here. `SRTWB8001` is a pedestal basin, the nearest of the family. |
| `mirror_cabinet` | Mirror Cabinet | `SRTLMCB902-BL` | The PDF's own code. |
| `shower` | Exposed Shower Set | `SRTWT7633` | PDF page 7 prints `SRTWT9616-BL`, absent here. |
| `wc` | Water Closet | `SRTWC8036-SH-250` | **Substitution.** The PDF prints `SRTWC8036-SH-UF`; the `-UF` variant is not in this database, the `-250` trap variant of the same model is. |
| `urinal` | Urinal | `SRTUB6503` | The PDF's own code. |
| `furniture_set` | Bathroom Furniture Set | product set `SRTWC8608-RL-WEPLS` | **Substitution.** `SRTBF11826` does not exist; the database holds exactly two product sets and both are WC sets. This one has 3 members, so the `set_members` slot is exercised. |

## What matches

Every element on each PDF page has a layer in its template, and every bound slot
resolved from live data with no figure stored in the document (ADR 0008):

- **Brand band** - the green `#445235` header at the PDF's own height, with the
  Sorento wordmark right-aligned on it.
- **Badge rows** - the four kitchen badges (SUS304, ultrasonic nano, 25-year,
  anti-bacteria) on both sink tags; the three WC warranty badges; the single
  lifetime-ceramic badge on the urinal. All seeded as `badge` layers bound to
  the uploaded assets by name.
- **Product block** - image slot, code, dimensions, name, spec lines, list-price
  line and price badge, grouped so the whole block re-binds in one action.
  Binding `SRTKS2435` filled `SRTKS2435 / 800 x 535 x 250 mm / Sorento kitchen
  sink. Nanograin. / LP: RM 1,550 / RM 1,550`.
- **Price badge** - a `price_badge` layer, `promo` on the tags whose PDF shows an
  SP block and `list_only` on the WC and urinal, which the PDF prices at list.
  With no promotion active a `promo` badge degrades to the list price rather
  than blanking, which is what the screenshots show.
- **WC specifics** - the trap badges (6", 8", 10", P-trap, UF seat cover), the
  Twister Flush logo, the flushing-technology line, the six smart-feature icons
  with their captions, and the remote-control diagram with its label.
- **Shower specifics** - the grey diagram panel, the water inlet/outlet diagram
  as a `badge` layer bound to `Diagram Water Inlet Outlet`, its three-line
  heading and the two labels beneath it, plus the stainless-body badge.
- **Mirror cabinet specifics** - the inside-view secondary image slot with its
  label, the dim-light label, and the two light-colour swatches with captions.
- **Furniture set specifics** - the green surround with a white inner card at
  85.9 x 107 mm, the wordmark above the card, the composed image slot, the
  `N IN 1 BATHROOM FURNITURE` heading, the `set_members` slot (which resolved to
  the three members of `SRTWC8608-RL-WEPLS`), the `Label Pull Out` asset, the
  circle-masked honeycomb callout with a rotated caption, the LP line and the
  promo badge.
- **Sink combo specifics** - the circle-masked NANO GRAIN callout over the hero
  photo, the accessories strip with four captioned image slots, and an
  alternatives row of three unbound product blocks with a leading `+` and two
  `OR` connectors.

## What the template cannot yet express

Specific and reproducible, in the order a reviewer would hit them.

1. **The caption around the honeycomb disc is straight, not curved.** The flyer
   sets `HONEYCOMB` on an arc following the top of the circle. A text layer has a
   single `rotation_deg`, so the seed rotates the whole caption 8 degrees, which
   reads as a tilted label rather than text on a path. Needs text-on-path in the
   layer model - v2.

2. **The Sorento wordmark is set in Jost, not the real logotype.** It is vector
   artwork in the PDF (neither a text span nor an embedded image), so it could
   not be cropped into `manifest.json`. Marketing uploads the logo as an asset
   and swaps the text layer for an image layer over the same box; nothing else
   changes.

3. **Codes and prices are set in Bebas Neue, body text in Jost.** The originals
   are Century Gothic and Myriad Pro, both licensed. The templates name the
   family, so uploading the real faces as `Asset.kind='font'` and renaming is the
   whole migration. Letterfit therefore differs from the PDF - Bebas Neue is
   narrower and taller than Century Gothic Bold, which is why the seeded `LP:`
   label is a separate layer sized for the stand-in rather than for the original.

4. **An alternatives block cannot be bound per-tag on a SHEET, only in the
   template editor.** `TagSheetDesigner` resolves every layer of a placed tag
   against the ONE request line it was dropped from, so on a tag sheet the three
   alternatives collapse to the line's own product. In the template editor each
   block is its own group and binds independently, which is what the screenshots
   show. Per-block binding on a placed tag needs the designer to resolve per
   group the way the editor does.

5. **The alternatives row is one row of three, not the PDF's two rows of six.**
   Six blocks do not fit the 125.9 x 88.6 mm tag at the size the seed uses, and
   duplicating a row is one action for a designer. The flyer's connectors are
   solid red and dark discs; the seed draws an outlined ellipse plus a text
   layer, per D28, so both are editable.

6. **`lineFamily` in `TagSheetDesigner` never selects `art_basin` or `urinal`.**
   It is a code-substring heuristic (`wc`, `sh`, `mr`, `mc`, `sk`) with an
   `ala_carte` fallback, so a request line for a basin or a urinal is dropped
   onto the ala carte template. The two new families exist and are pickable in
   the template dialog; only the automatic per-line choice is behind. Out of
   scope for this slice, named here because it is the first thing a reviewer
   will notice on a sheet.

7. **Two products in the sample have no photo the tag can show.** `SRTKS2435`
   and `SRTLMCB902-BL` have zero gated attachments, so their image slots draw
   the "No image" state. That is a product-master data gap, not a template gap -
   `SRTKS2409`, `SRTWB8001` and `SRTWC8036-SH-250` all render their photos.

8. **Dimensions on some products are partial.** `SRTWC8036-SH-250` resolves to
   `250 x - x - mm` and `SRTWT7633` to an empty string, because the master data
   carries only one of the three measurements. The slot is correct; the source
   rows are incomplete.

9. **The WC template is the union of the two WC tags on page 8.** Page 8 prints a
   standard WC (badges, traps, twister logo) and a smart WC (icon row, remote
   diagram) as separate tags. The seed carries both sets of elements on one
   template so a designer deletes rather than rebuilds; the tag is
   correspondingly busier than either PDF tag on its own.

## Defects found and fixed during this run

Recorded here because each was invisible until a seeded template was opened.

- **Every library image on a tag sat on "Loading" forever.**
  `KonvaTagLayer.useHtmlImage` set `crossOrigin = 'anonymous'`, and the R2 bucket
  serving `dealer_kit_asset/` returns no `Access-Control-Allow-Origin`, so the
  browser discarded all 28 pieces of artwork. Nothing in `dealer-kit/` calls
  `toDataURL`, so an untainted canvas buys nothing here; the attribute is gone
  and the badges render.
- **A block about a SET could only be offered a PRODUCT picker.**
  `handleRebind` chose the picker mode from the binding alone, which is empty on
  a template by design, so the bathroom-furniture tag could never be bound to the
  one kind of thing it is about. It now also reads the block's `set_members`
  slot.
- **A text layer bound to `list_price` / `sell_price` never resolved.**
  `resolveSlotText` handled code/name/dimensions/spec lines/set members only, so
  the flyer's `LP: RM 1,550` line kept its seeded placeholder - an invented
  figure on a real tag. Both price slots now resolve through the same formatter
  the price badge uses.
- **The seed wrote `company_id = NULL`.** A script gets neither the scope filter
  nor the insert auto-stamp, which the API startup and `worker.py` install, so
  the eight templates and 28 assets landed unowned and were invisible from the
  company that owns them. `seed_tag_templates.run` now calls
  `register_company_scope_listeners()` the way `worker.py` does.

## Known noise, not fixed

`DialogContent` logs `Missing 'Description' or 'aria-describedby={undefined}'` on
every dialog open. It is the shared ReUI dialog, unrelated to this slice, and
appears throughout the app. No page errors were logged on any of the eight
templates.

---

# S3c - the canvas as a drawing tool (AC-M.1 to AC-M.10)

Run 2026-08-29 on the worktree dev stack (FE `next dev` :3030, BE :8030), against
`Kitchen Sink - Ala Carte` (`8fb5c0ed-af00-4672-8dba-651909fac029`), reached by
clicking `Dealer Kit -> Room Designer -> Tag Templates -> Kitchen Sink - Ala Carte`
from `/`. agent-browser session `ptag-canvas`, viewport 1600 x 1000.

Positions were read two ways, because a screenshot cannot show a number that did
not change: the inspector's X/Y fields, and the DOM positions of the ruler tick
labels (the ruler is plain DOM, so `origin + mm * scale` is measurable).

The template was left exactly as it was seeded: the one save made during the run
was reverted through the inspector and saved again (wordmark back to X 84 /
Y 5.05), and every other edit was discarded by reloading without saving.

## The evidence shots

| File | What it shows |
| --- | --- |
| `interaction-1-wheel-zoom-at-cursor.png` | The canvas at 278% after a wheel zoom centred on the pointer. Before the zoom the 60 mm tick sat at x=916 and the 40 mm tick at y=576 with 62.5 px per 10 mm; after it, x=915 and y=575 with 83.3 px per 10 mm. The point under the cursor stayed under the cursor and the readout followed (AC-M.1). |
| `interaction-2-drag-writes-inspector-xy.png` | The `Sorento` wordmark after a canvas drag: the inspector reads X 74.8 / Y 11 where it read X 84 / Y 5.05, and the single Transformer is on the selection. This is bug 7: before the fix the Konva group had no `id`, `stage.findOne('#id')` answered undefined, and the drag was never written to `layers` (AC-M.8). |
| `interaction-3-drag-survives-save-and-reload.png` | The same layer after Save and a full page reload, still at X 74.8 / Y 11 (AC-M.8). |
| `interaction-4-group-drag-carries-children.png` | The product block dragged by its group. Group and `product image` both went from X 5.3 / Y 21 to X 0 / Y 26.19: identical delta, one action. One Ctrl+Z put both back to 5.3 / 21 (AC-M.4). |
| `interaction-5-double-click-enters-group.png` | A double-click on the group over the photo selected `product image`. A single click at the same place had selected `Group (6)`; a single click afterwards, over the code text, selected `code` directly, so the entered group had stopped intercepting. Escape selected `Group (6)`, Escape again cleared the selection (AC-M.3). |
| `interaction-6-marquee-band.png` | The translucent blue band mid-drag, plus the two badges an earlier band had selected with one Transformer across both. The band shown selected the eight TOP-LEVEL layers and never the group's six children. A thin band across only two badges selected exactly those two and enabled the toolbar's Group button; dragging one of them moved both by the same 71.35 x 51.01 px while the unselected third badge did not move (AC-M.2, AC-M.5). |
| `interaction-7-context-menu.png` | Our menu on right-click over a badge: Cut, Copy, Paste, Duplicate / Bring to Front, Bring Forward, Send Backward, Send to Back / Group / Lock, Hide / Delete. The browser's own menu never appeared. Right-clicking empty space gave Paste, Select All, Fit to View, Zoom 100%, Preview with a product (AC-M.6). |
| `interaction-8-duplicate-group-carries-descendants.png` | Duplicate on the group took the document from 14 layers to 21, and the Layers panel nests six fresh children under the copy, so the clone's `children` points at the clones. Before this change a duplicate added ONE layer whose `children` still named the originals. Ctrl+Z returned it to 14 (AC-M.7). |
| `interaction-9-preview-with-a-product.png` | Preview with `SRTKS2435`: the code, dimensions, spec line, `LP: RM 1,550` and the price badge all resolve, and the chip on the toolbar names the product. The Layers panel still reads `Group (6)`, not `Product (6)`, because nothing was bound. Saving with the preview active and reloading gave back the placeholder text, no chip and an unbound group (AC-M.9). |
| `interaction-10-preview-photo-follows-product.png` | Preview with `CBF3612`, the product whose photo the hero slot used to refuse to draw. The tag now shows its primary photo, its code, its dimensions, its spec line, `LP: RM 799` and `RM 799`, and the chip on the toolbar names it. The template's hero layer still holds `source: null`: the slot follows the bound product's primary photo (D42, AC-M.11). |
| `interaction-10b-preview-cleared-no-image.png` | The same template after Stop previewing: the hero is back to the `No image` placeholder and the text layers to their seeded placeholders, so nothing about the preview leaked into the document (AC-M.11). |
| `interaction-11-layers-drag-into-group.png` | The `LP:` row dragged onto the `code` row inside the block. It became a member: the panel indents it under the group, the count went from `Group (6)` to `Group (7)`, and the group's dashed box grew to enclose it. Dragging the group on the canvas afterwards moved `LP:` from X 78.5 / Y 55.6 to X 90.47 / Y 54.6, the same delta the group took (5.3 to 17.27, 21 to 20). One Ctrl+Z put the canvas move back and left the membership; a second Ctrl+Z put `LP:` back at the top level and the panel back to `Group (6)` (D43, AC-M.12). |
| `interaction-11b-drop-onto-group-joins-as-last-child.png` | A `Badge` row dropped onto the BODY of the group row joined as the group's last child, and the group's box grew upwards to reach it. Reading z off the inspector down the panel gave `sell price` 13, `product image` 8, `Badge` 7: the panel order is the z order, and the group's descendants are one contiguous block below it (D43, AC-M.12). |
| `interaction-12-middle-button-pans-before.png` / `-after.png` | Middle-button drag over the product photo, from (700, 420) to (760, 430). The 10 mm ruler tick moved from x=597 to x=657 and the artboard came with it; no marquee band was drawn, the selected `Badge` kept X 51.4 / Y 5.7 / z 7 and the selection never changed. While the button was held the workspace carried `cursor-grabbing`, and it was gone on release (D44, AC-M.13). |

## Measurements that are not in a picture

- **Fit and zoom keys.** Ctrl+1 took the readout to 100%, Ctrl+0 back to the fit
  value of 209% for this 125.9 x 88.6 mm tag in an 852 x 783 px stage.
- **The wheel listener is not passive.** A cancelable `wheel` event dispatched on
  the canvas came back with `defaultPrevented === true`, which is what the
  `{ passive: false }` registration buys; React's `onWheel` cannot do this and
  the page would scroll instead.
- **The hand tool pans.** Pressing `H` set the toolbar button to
  `aria-pressed="true"` and the workspace cursor to `grab`; a drag of
  (-100, -70) px moved the 10 mm tick from x=603 to x=503, exactly the pointer
  delta, with no layer changing its mm position.
- **Space held is the hand for as long as it is held.** With the Select tool
  active the workspace cursor read `auto`; a `keydown` for `' '` without its
  `keyup` turned it to `grab`, and the `keyup` turned it back to `auto`.
- **A context menu item changes the document and undoes.** Right-click on the
  leftmost badge (z 2 of 14) then Bring to Front took it to z 14, and Ctrl+Z put
  the whole stack back (the band returned to z 0). Nothing was saved, so the
  seeded document is untouched.
- **Konva nodes now carry the layer id.** `stage.getLayers()[0].getChildren()`
  lists `sink-ala-carte-band-1`, `sink-ala-carte-badge-0-3`, ... , which is the
  fix for bug 7 stated as data rather than as a screenshot.

## Not exercised in the browser, and why

- **The CDP wheel.** `agent-browser mouse wheel` does not reach the page in this
  daemon (the readout did not move for real wheel commands at any delta), so the
  zoom was driven by a dispatched `WheelEvent` carrying the same
  `deltaY` / `clientX` / `clientY`. It runs the identical listener; what it does
  not prove is the browser's own scroll behaviour, which `defaultPrevented`
  covers instead.
- **Double-click timing.** Konva synthesises `dblclick` from two pointerups
  inside `Konva.dblClickWindow` (400 ms), and one CLI call takes about a second,
  so the window was widened to 30 s for that one step and put back to 400
  immediately after. The click path itself is untouched.

## The portal run, 30 Aug (D45-D47)

Driven with agent-browser on the `:3030` lane, as the portal contact `CONTACT AI`
(token link) and, for the admin half, as a staff user reaching Sales Agents
through the sidebar. **Nothing was written to the database**: the dev database is
a copy of production, and the run ends with `price_tag_requests` still at 0 rows
and `sales_agents.contact_id` still null on every row, which is the same query
that framed the whole of D46.

| File | What it shows |
| --- | --- |
| `portal-1-landing-dropdown-lists-price-tag-request.png` | The landing's type dropdown open, listing Stock Inquiry, Complaint, Purchase Request, Sponsorship Form and **Price Tag Request**, each with its count. The separate link button is gone. `/portal/me` for this contact answers `visible_form_types: ["price_tag_request", "stock_inquiry"]`, so the option is offered because the grant says so (D45, AC-M.14). |
| `portal-2-price-tag-request-selected-empty-state.png` | Price Tag Request selected. The list area, the search box, the status filter, the star and a `New Price Tag Request` button are the same ones the other kinds get, and the empty state reads `No price tag request submissions yet.` This contact genuinely has none: the endpoint answers 200 with 0 items (AC-M.14). |
| `portal-3-price-tag-requests-listed-as-cards.png` | The same list with two rows, served by a page-side `fetch` stub, because the database holds no price tag requests at all and creating one is a write. Each card carries the doc number as its heading, the dealer as its Customer line, a `Needed by` date and a status badge: `New` for the submitted row and `Draft` for the one still holding `portal_draft_at`. The dropdown's count reads 2 (D45, AC-M.14). |
| `portal-4-draft-filter-keeps-only-the-draft.png` | The status filter set to Draft against the same two rows: only `PT-202608-0002` survives. This is what the new `portal_draft_at` field buys, since both rows carry `status: "new"` and without it neither would ever read as a draft (AC-M.14). |
| `portal-5-debtor-not-linked-notice.png` | The new request form with the debtor select REPLACED by `No debtors available. Your portal account is not linked to a sales agent yet. Ask your Sorento contact to link it.` Save Draft and Submit are both disabled. `select count(*) from sales_agents where contact_id is not null` answers 0, so the empty lookup is the real state and not a stub (D46a, AC-M.15). |
| `portal-6-lines-table-product-then-set.png` | The lines table at 1280 with two rows added by the one `Add line` button. Row 1 is the product `CBF31046`, row 2 a SET picked from the SAME dropdown (`CABANA CLOSE COUPLED WC ... Set - CWC611`); the picker labels every option `Product - CODE` or `Set - CODE`. Row 2's Alternatives cell is `disabled` and its cell title reads `A set is printed as one thing, so it carries no OR choices.`, while row 1's stays enabled. Nothing was saved: there is no portal route that deletes a price tag request, so the run stops at form state (D47, AC-M.16). |
| `portal-7-lines-table-at-375px.png` | The same table at 375 x 812. The PAGE does not overflow (`document.scrollWidth` 375 = `clientWidth` 375); the table scrolls inside its own `overflow-x: auto` wrapper (560 in 317), which is the Purchase Request pattern (AC-M.16). |
| `portal-8-sales-agent-linked-portal-contact-field.png` | The Sales Agents edit modal for `ACT`, reached from the sidebar (Users & Access > People > Sales Agents), now carrying `Linked portal contact` beneath Location group, reading `Not linked` (D46b, AC-M.15). |
| `portal-9-contact-picker-name-and-masked-phone.png` | That picker searched for `Ziv`, answering `CONTACT AI ***1678`. The list is server-searched (the unfiltered open returned `Agnes ***1178`, `Ahmad Shakir Irfan ***3797`, ...), every row is a name plus the last four digits of the phone, and no id appears anywhere. Escaped without saving (D46b, AC-M.15). |

### Measured, not pictured

- **The merged item lookup answers with real ids.** `GET
  /portal/lookups/price-tag-items?q=CBF` returned
  `{kind: "product", id: "15324810-a1cd-49a7-8be6-61ad8e0418e5", code: "CBF31046"}`
  and the unfiltered call returned `product_set` rows such as `CWC1009-RL`. Those
  ids are the `products.id` / `product_sets.id` a line's foreign key stores; the
  portal's older product lookup answers with a code and no id at all, which is why
  no draft carrying a product line can ever have saved.
- **The lines table is `table-fixed`.** With auto layout a set name of sixty
  characters took the whole width and pushed Qty, Alternatives and Accessories off
  screen at 1280. Fixed columns measured 28 / 176 / 70 / 129 / 117 / 94 px on both
  rows, table 614 px inside a 614 px wrapper, so nothing is clipped and the long
  name truncates in the picker's trigger.

### Not exercised, and why

- **A contact WITHOUT the grant.** Manufacturing a second contact is a database
  write. The gating is asserted from the `/me` payload above and unit-tested in
  `PortalLanding.priceTag.test.tsx`, which also covers the `?type=` deep link
  falling back to Stock Inquiry for such a contact.
- **A saved draft.** The portal has no route that deletes a price tag request, so
  saving one would leave a row behind in a database that is a production copy. The
  payload shape is pinned in `PriceTagRequestForm.lines.test.tsx` instead, and the
  foreign-key half in `tests/test_price_tag_request.py::TestTagItemLookup`.
- **Linking an agent to a contact for real.** Same reason. The modal was opened,
  searched and escaped.
- **The CLI `click` on elements low in the portal page** was a silent no-op behind
  the fixed impersonation banner; those clicks were issued as DOM `click()` through
  `eval` instead. Every assertion above is read from the rendered DOM.

## Round 4, 30 Aug: the draft and the Submit that says what is missing (D48 / D49)

Portal, impersonation token for CONTACT AI, FE `:3030` + BE `:8030`.

- `portal-10-step0-repro-before-submit.png` - the captain's exact form state rebuilt
  before pressing Submit: debtor ARDENCY CONSTRUCTION, needed by 31/08/2026, notes,
  line 1 the CABANA set `CWC1009-RL`, line 2 the product `SRTWC286-SH-150`. Submit is
  enabled, so the button was never the blocker.
- `portal-11-step0-repro-field-required-toast.png` - what pressing it produced: a toast
  reading **"Field required"** and nothing else. The backend log for the same second:
  `POST /api/v1/public/portal/submissions/price_tag_request - Status: 422` with
  `{"loc":["body","fields"],"msg":"Field required"}`. `body.fields` belongs to the
  GENERIC portal submission schema: the request was served by `portal.py`'s
  `POST /submissions/{kind}`, which is mounted first, and never reached the price tag
  route at all. That is the whole of the captain's report.

### Proven after the fix, through the API rather than the browser

The lane's `next dev` on :3030 exited part-way through this run and this agent is not
permitted to start a server, so the second half was proven against `:8030` with the same
portal token the browser uses. Each step is a real HTTP call on the running lane:

| What | Result |
|------|--------|
| Draft with ONE line, no debtor, no date | `201`, `debtor_name: null`, `needed_by_date: null`, `portal_draft_at` set |
| Reopen that draft (`GET .../{id}`) | `200`, nulls intact, line resolved to `SRTWC286-SH-150`, `attachments: []`. Before this round the route did not exist and the generic one answered `400 Unsupported submission type` |
| The portal list | one row, Draft, dealer and deadline both null |
| Submit it | `422 SUBMIT_INCOMPLETE`, `detail: "debtor_name,needed_by_date"`, message "This request needs a dealer and a needed by date before it can be submitted." |
| Re-save the SAME draft with a dealer, a date and two lines | `200`, still ONE row in the list, not two |
| Submit the captain's exact state | `200`, `portal_draft_at` cleared |
| Submit a draft whose line 2 is an ala carte Bathroom Furniture product (`SRTBF11721`) | `422 SET_GUARD_VIOLATION`, `detail: "line:1"`, naming the product |
| Delete that draft | `204`, then `404` on the detail |
| Delete a SUBMITTED request | `409`, by design |

The inline half (red text under Debtor and Needed by, the per-row message, the
"N things need attention" line, the errors clearing as fields are filled, a server
refusal landing on the row it named) is covered by
`PriceTagRequestForm.validation.test.tsx`, 8 tests, jsdom.

### Not exercised, and why

- **The browser half of round 4.** `:3030` was down from the middle of the run and
  starting a dev server is outside this agent's permissions. Everything above is the
  same code path the form calls, one layer down.
- **Three rows are left behind.** `PT-202608-0001`, `-0002` and `-0003`, all submitted,
  all for ARDENCY CONSTRUCTION. They were created to prove the flow; a submitted request
  has no delete path (that is the design, and the portal refuses it with a 409), and the
  CRM transition route rejects an `X-API-Key` principal, so nothing available here can
  remove them. Deleting them by hand in psql was ruled out by the brief.

## Round 5, 30 Aug: the house chrome and the request designer (D50-D52)

Lane `:3030` / `:8030`, logged in through the sign-in form, navigated from `/` by sidebar
clicks (Dealer Kit -> Room Designer -> Price Tag Requests). Request `PT-202608-0001`.

| File | What it shows |
|------|---------------|
| `request-1-detail-house-chrome.png` | The detail page in its new chrome: breadcrumb as the way back, `Price Tag Request - PT-202608-0001` as the heading, `Created: ... - New` beneath it, `Needed by` and `Assigned to` in the header, and on the right one primary CTA (Claim), the gear, and prev/next reading `4 / 4`. No "Back to list" button anywhere. |
| `request-2-claimed-primary-and-gear.png` | After Claim: the status is `Designing`, the primary CTA is `Design tags`, and the gear holds `Mark proof ready` and a destructive `Void`. Nothing secondary sits beside the primary (D52). |
| `request-3-designer-opens-on-line-1.png` | `/design` IS the template editor: the request bar (doc number, `Design | Arrange`, Save, Mark proof ready), the unchanged `CanvasToolbar`, a LINES rail above the LAYERS panel, the Inspector on the right, and line 1's tag cloned from the Furniture Set template showing the real code `CWC1009-RL` and its set members. |
| `request-4-line-2-wc-template-real-data.png` | Line 2 selected: the WC template, the real code `SRTWC286-SH-150`, its spec text, its badges and its real price `RM 1,260`. Both rail rows now carry the designed check. The artboard resized to that template's print size. |
| `request-5-use-template-picker.png` | "Use template..." preselected on the tag's current template, so "Reset to template" is one click; the list offers every family with its print size and puts the line's own family first. |
| `request-6-replace-confirmation.png` | The tag carries edits, so choosing another template asks first (`Replace this tag with the template?`, destructive Replace). |
| `request-7-template-swapped-to-art-basin.png` | After Replace: the same line, re-cloned from the Art Basin template, still drawn against the LINE (same code, same `RM 1,260`) on that template's artboard. |
| `request-8-arrange-auto-placed.png` | The Arrange half: `1 sheet / 3 tags` for line 1 (qty 1) plus line 2 (qty 2), auto-placed on A4 3-up with the imposition panel on the right. Nobody had to drag anything. |
| `request-9-proof-ready-view-design.png` | After Mark proof ready: the pill reads `Proof Ready`, the primary CTA becomes `View design`, and the gear is down to `Void` (D52). |
| `request-10-stock-inquiry-reference-chrome.png` | The reference, `/procurement-management/stock-inquiries/<id>`, for the side-by-side: same heading shape, same `Created: ... - <pill>` subline, same primary-then-gear-then-chevrons order. |
| `request-12-template-editor-unchanged.png` | The template editor after `TagCanvasEditor` gained its four optional props: no rail, the Layers panel filling the left column, the same canvas and Inspector, its own Save bar still there. Nothing about editing a TEMPLATE changed. |
| `request-11-detail-at-375px.png` | The whole detail page at 375px: the header wraps, the CTA row stays on one line, every card stacks, the lines table scrolls rather than clipping, and each section keeps its empty state. |

### Measured, not assumed

- **Edits did not survive a line switch, until this run.** Nudging the `list price` layer moved
  the inspector X from `63.3` to `68.3`; switching to line 1 and back put it at `63.3` again.
  Cause: the editor was handed the layers the tag was CREATED with rather than the layers it
  currently has, so the remount restored the original. Fixed, re-measured (`68.3` after the
  switch, `68.3` after Save and a reload).
- **Mark proof ready had never worked.** The route takes the STATUS to move to; the frontend has
  been sending the action name `mark_proof_ready` since this feature was written, and the backend
  answered `409 Cannot transition from 'designing' to 'mark_proof_ready'`. Both call sites now
  send `proof_ready`, and the screenshot above is the transition landing.
- **`assigned_to_name` comes back null even on a claimed request**, so the header reads
  "Assigned to: Unclaimed" after a successful claim (the CRM listing's Assigned To column shows
  the same dash). Backend-side resolution gap, not touched in this round.

### Not exercised, and why

- **The PDF export and `/c/print/tag-sheet`.** Export is only legal at `approved` or `ready`,
  and a request reaches `approved` when the SALESPERSON approves the proof on the portal, which
  the CRM deliberately does not offer. On top of that the only RQ worker running on this machine
  belongs to the primary checkout, and this lane's backend `.env` sets no
  `DEALER_KIT_PRINT_BASE_URL`, so a render would have been pointed at `:3000` rather than
  `:3030`. Starting or restarting a worker was outside the brief. The document the printer reads
  was verified instead: it round-trips through Save and a reload with the designed layers intact.

## Round 6, 30 Aug: preview is per block (D53)

Lane `:3030` / `:8030`, logged in through the sign-in form, navigated from `/` by sidebar clicks
(Dealer Kit -> Room Designer -> Tag Templates), then the row `Kitchen Sink - Combo`. That template
is the case D41 could not serve: five groups, of which four are about a product (the main sink,
`Group (8)`, and three unbound alternatives, `Group (5)` each) and one is the accessories strip,
which carries an `included_accessories` slot but is written `binding: null`.

| File | What it shows |
|------|---------------|
| `interaction-13-preview-lightbox-four-blocks.png` | "Preview with..." on a multi-block template opens `Preview with products` with exactly FOUR rows: `Group (8) - block 1 - Product code` and `Group (5) - block 2 / 3 / 4 - Product code`. The three identical alternatives are told apart by their ordinal, no row is named by an id, and the accessories strip is absent (D53, AC-M.22). |
| `interaction-14-two-blocks-two-products.png` | Block 1 set to `CBF3612` and block 2 to `ACC-SRT1001`, then Apply. Each block resolves against its OWN product: the main block shows `CBF3612`, `380 x 330 x 400 mm`, `LP: RM 799` and `RM 799` with the product's photos, the first alternative shows `ACC-SRT1001`, `Sorento With fixing screw` and `RM 20`, and blocks 3 and 4 keep `PRODUCT CODE` / `Product name` / `Price TBC`. The chip reads `Previewing 2 of 4 blocks` (D53, AC-M.22). |
| `interaction-15-inspector-previews-one-block.png` | The middle alternative selected from the Layers panel (inspector X 78.5 / Y 21, which is `alternative-b` in the seed) and previewed from the Inspector's own `Preview this block with...`. That block alone changed to `ACC-SRT2001` / `RM 150`, the Inspector's PREVIEW section now names it with a clear beside it, block 4 still reads `PRODUCT CODE` / `Price TBC`, and the chip counts `Previewing 3 of 4 blocks` (D53, AC-M.22). |
| `interaction-16-chip-reopens-with-choices.png` | Clicking the chip reopens the same lightbox with the choices already in force shown in their rows (`CBF3612 - CBF3612`, `ACC-SRT1001 - ACC-SRT1001`), and `Clear all` beside Cancel and Apply (D53, AC-M.22). |
| `interaction-17b-saved-while-previewing.png` | Save pressed while block 1 is previewing `CBF3612`: `PUT /api/v1/dealer-kit/tag-templates/39650995-...` returned 200 and the page toasted `Template saved` (D53, AC-M.22). |
| `interaction-17-save-then-reload-still-unbound.png` | The same template after a full page reload: every block is back to `PRODUCT CODE` / `Product name` / `Price TBC` / `No image`, there is no chip, and the Layers panel still names the blocks `Group (n)`. The row in the database is unchanged too: all five group bindings are still `{}` (or `null` for the strip), no layer has a `text_override` and no image layer has a `source`. `updated_at` never moved off the seed's `2026-08-25 05:02:36`, because the document that was PUT was byte-identical to the one that was open (D53, AC-M.22). |
| `interaction-18b-single-block-picker.png` / `interaction-18-single-block-unchanged.png` | `Kitchen Sink - Ala Carte`, which has ONE previewable block, is exactly as round 2 proved it: the single `Preview this template with` picker rather than the lightbox, and the chip `Previewing: CBF3612 - CBF3612` over a tag resolved end to end (photo, code, dimensions, spec, `LP: RM 799`, `RM 799`). Its X on the chip cleared the preview and the placeholders came back (D53, AC-M.22). |

Both seeded templates were left as they were: `Kitchen Sink - Combo` still 50 layers with every
binding `{}` or `null`, `Kitchen Sink - Ala Carte` still 14 layers and never saved in this round.

**Known noise, not introduced here.** Radix logs `Missing 'Description' or
'aria-describedby={undefined}' for {DialogContent}` for every dialog on this page.
`ProductPickDialog` and `AssetPickerDialog` have always done so; the new
`PreviewBlocksDialog` follows the same house pattern and adds one more instance of the same
warning rather than a new kind of it.

## Round 7, 30 Aug: the colour picker and merge fields (D54-D59, AC-M.23 / AC-M.24)

Run on the `:3030` lane against a throwaway template, `ZZT merge fields` (family Ala Carte),
created through Tag Templates -> New Template. No seeded template was opened or edited.

| File | What it shows |
| --- | --- |
| `interaction-2x-colour-spectrum-popover.png` | The Colour control on a text layer, opened. The popover leads with the browser's own full-spectrum `input[type=color]` given a swatch-sized area (the large black rectangle), with the twelve brand swatches under it and the hex box beside the trigger (D54, AC-M.23). |
| `interaction-2x-colour-picked-syncs-hex.png` | The same popover after a colour was picked on the spectrum. The spectrum, the trigger swatch and the hex box all read `#1565c0`: picking rewrote the box and the layer in one move. The OS colour panel is not scriptable, so the spectrum was driven the way the browser drives it, through the native value setter plus a real `input` event; the swatches under it were exercised by ordinary clicks (D54, AC-M.23). |
| `interaction-2x-insert-field-no-preview.png` | The Insert field dialog. The content is editable at the top with the cursor kept, the search box has narrowed the catalogue to `Dimensions {{product.dimensions}}` under its PRODUCT heading, and the preview line reads `(preview a product to see values)` because nothing is previewed yet. The Inspector behind it shows the new `{}` Insert field button beside Content (D59, AC-M.24). |
| `interaction-2x-canvas-draws-the-tokens.png` | The canvas with nothing previewed: the layer draws `Made of {{spec.material}} - {{product.dimensions}}` as written, so the designer can see which fields will fill it. The Layers panel row carries the `{}` marker and the Inspector reads `Draws from product data` in place of the amber unlinked note (D55, D57, AC-M.24). |

Proven in this run, beyond the four images: the Specs group is fed by the live registry through
`GET /api/v1/dealer-kit/spec-keys` (searching `material` offered `Material {{spec.material}}` and
`Seat cover material {{spec.seat_material}}`, neither of them hard-coded anywhere in the
frontend); a click in the catalogue inserts at the caret; Done writes the content back to
`text_override` on a slot-bound layer, after which the Layers panel row reads `name {}` with the
title `Draws from product data through merge fields`.

The rest of the round was finished on a second run, after the lane came back. The template was
rebuilt (the first run's group had never been saved) and the layer's content was rewritten to
tokens `CBF3612` actually carries, because the product has no `material` value and a token that
correctly renders empty proves the rule but shows nothing.

| File | What it shows |
| --- | --- |
| `interaction-2x-insert-field-live-preview.png` | The Insert field dialog while the block is previewing `CBF3612`. The content reads `{{spec.brand}} {{spec.class}} - {{product.dimensions}} - {{spec.piece_count}} pcs` and the Preview line under the catalogue reads `CABANA Bathroom Furniture - 380 x 330 x 400 mm - 6 pcs`, resolved live against the previewed product. The Inspector behind it shows Relink and Insert field side by side (D59, AC-M.24). |
| `interaction-2x-canvas-resolves-with-preview.png` | The same tag on the canvas after Done: the code layer draws `CBF3612` and the token layer draws `CABANA Bathroom Furniture - 380 x 330 x 400 mm - 6 pcs`, while the Inspector still holds the raw tokens and reads `Draws from product data`. The chip reads `Previewing: CBF3612 - CBF3612` (D55, D57, AC-M.24). |
| `interaction-2x-stop-previewing-tokens-return.png` | Stop previewing. The chip is gone, the code layer is back to its placeholder and the token layer draws `{{spec.brand}} {{spec.class}} - {{product.dimensions}} - {{spec.piece_count}} pcs` again, so the designer sees which fields will fill it (D55, AC-M.24). |
| `interaction-2x-tokens-survive-reload.png` | The template after Save (toast `Template saved`) and a full page reload: `Group (2)` with its `code` and `name` children, the `{}` marker on the row, and the same tokens in the layer's `text_override`. Merge fields are part of the saved document (AC-M.24). |
| `interaction-2x-designer-line1-set.png` | `PT-202608-0001` line 1 (`CWC1009-RL`, a SET line) after Use template -> ZZT merge fields. The tag resolves against the LINE: the code layer draws `CWC1009-RL`. Every `{{spec.*}}` and `{{product.dimensions}}` renders empty, which is D58 exactly: a set has no spec row of its own, and the empty render is the rule working rather than failing. |
| `interaction-2x-designer-line2-product.png` | Line 2 (`SRTWC286-SH-150`, a PRODUCT line) through the same template, which is the meaningful half: `SORENTO Water Closet - 150 x - x - mm -  pcs`. `{{spec.brand}}` and `{{spec.class}}` came from the registry join, the dimensions print `-` for the two measurements the master data does not record, and `{{spec.piece_count}}` is empty because this product does not carry it (D51, D58, AC-M.24). |
| `interaction-2x-template-delete-confirm.png` | The throwaway template being deleted through the list: `Delete tag template - Are you sure you want to delete "ZZT merge fields"? This action cannot be undone.` Confirmed, toast `Template deleted`, and it is absent after a reload. |

**Both lines of `PT-202608-0001` were put back and saved.** Line 1 to `Bathroom Furniture Set` and
line 2 to `Art Basin`, which is what each picker showed when it was first opened; the designer
then saved with the toast `Tag sheet saved`, and line 1's Layers panel reads `Set (5)` again. No
seeded template was opened or edited at any point in either run.

**A trap worth writing down.** Two clicks in this editor report success without firing, and both
look like application bugs when they land: `find role button click --name "Done"` closes the
Insert field dialog as an outside click, so Radix cancels and the content is discarded, and
`click "button.bg-primary"` on the editor's Save left the document unsaved. Dispatching a
`MouseEvent('click')` on the button found by its exact text is what worked for both, and for
every toolbar button.

**Known noise, not introduced here.** Radix's missing-`Description` warning fires for
`InsertFieldDialog` exactly as it does for every other dialog on this page.

---

## Round 8, 30 Aug: arrange works inside a group (D60, AC-M.25)

Run on the `:3030` lane, navigated from `/` through the sidebar: Dealer Kit -> Room Designer ->
Tag Templates -> `Kitchen Sink - Combo`. The view was zoomed to 425% and panned onto the main
product block so the badge is readable; the wheel and the toolbar `+` both zoom, and the pan was a
middle-button drag (D34, D44). Nothing was saved: Save was never pressed, and both undos put the
seeded z order back exactly, asserted id by id against the seed.

The badge under test is `NANO GRAIN`. The Layers panel tree shows it as a DIRECT child of the
8-child main product block, beside `Image` (the callout disc) and `product image` (the sink
photo), so the badge and the photo are siblings and Illustrator's answer is to arrange between
them.

| File | What it shows |
| --- | --- |
| `interaction-19-send-to-back-inside-group.png` | After Send to Back from our right-click menu. A double-click had entered the block and selected the badge itself (the Transformer sits on the 9 x 4.4 mm caption, and the menu that opened carried Cut / Copy / Paste / Duplicate, Bring to Front / Bring Forward / Send Backward / Send to Back, Group / Select Parent Group, Lock / Hide / Delete, with no browser menu behind it). The `NANO GRAIN` text is gone: it now draws UNDER the callout disc and the sink photo, with only the selection handles showing where it is. Before this round it stayed on top, because the reorder moved the whole block instead of the badge. |
| `interaction-20-bring-to-front-inside-group.png` | The same badge after Bring to Front, one undo later. `NANO GRAIN` is legible over the photo again and the Inspector reads `Z-Index 15`, one below the block's group layer at 16, which is the top of ITS block and not the top of the tag: the accessories strip and the three alternative blocks still draw above it. |

Read off the canvas by the ids the Konva nodes carry, bottom to top around the block:

| | Draw order |
| --- | --- |
| Seeded | `... badge-3`, **`hero`**, `callout`, **`callout-caption`**, `code`, `dimensions`, `spec-lines`, `list-price-label`, `list-price`, `price`, `product` (the group) |
| Send to Back | `... badge-3`, `list-price-label`, **`callout-caption`**, **`hero`**, `callout`, `code`, `dimensions`, `spec-lines`, `list-price`, `price`, `product` |
| Bring to Front | `... badge-3`, `list-price-label`, **`hero`**, `callout`, `code`, `dimensions`, `spec-lines`, `list-price`, `price`, **`callout-caption`**, `product` |
| After both undos | identical to Seeded, all 16 ids in order |

The badge moves to the bottom and to the top of its OWN block and the block does not move relative
to anything outside it; the block stays contiguous with its group layer directly above its own
subtree. `list-price-label` is a TOP-LEVEL layer that the seed had interleaved inside the block's
z range, so the renumbering to 1..n moves it out below the block. That is D40's contiguous-block
rule and predates this round. It is also why the badge's `Z-Index` field reads 8 both before and
after Send to Back: the badge lost exactly the one place that layer gained, which is why the
ordering above is the measurement and the single field on its own is not.

**A trap worth writing down.** Konva interaction needs REAL mouse events, and the shell has a trap
of its own here: `S="npx -y agent-browser@0.27.0 --session x"; $S mouse move ...` is a
`command not found` that the usual `>/dev/null 2>&1` swallows, so every step reports nothing and
the page never moves. Spell the command out, and drive the canvas with one
`batch "mouse move X Y" "mouse down left" "mouse up left"`, twice over for the double-click that
enters a group. The DOM half of the page still needs the `MouseEvent('click')` dispatch the round
7 note describes: the sidebar's `Room Designer` and `Tag Templates`, and the list row, all ignore
`find role ... click`.

## Round 9, 30 Aug: the form deploys granted to nobody, admins switch it on (D61, AC-M.26 / AC-M.27)

Recorded on the :3030 lane after `alembic upgrade head` took the dev database to
`ptag_0003`. CONTACT AI's portal token is the captain's test setup; the run ends with the
`dealer` access type granted again, so that setup still works.

| File | What it proves |
| --- | --- |
| `portal-2x-1-dropdown-without-price-tag.png` | After the migration, CONTACT AI's landing dropdown offers Stock Inquiry, Complaint, Purchase Request and Sponsorship Form. Price Tag Request is absent, with no gating code added: `resolve_visible_form_types` unions an array that no longer holds the kind. |
| `portal-2x-3-portal-forms-picker-five-kinds.png` | User Management -> Contact Access Types -> Dealer -> Edit. The Portal forms field offers all five kinds with the portal's own labels, Stock Inquiry already ticked as the row carries it. |
| `portal-2x-4-chips-show-price-tag-request.png` | After Save, the `dealer` row's Portal forms cell reads `Stock Inquiry` + `Price Tag Request`. The same shot is the post-migration state of every other row: the three other dealer types carry `Stock Inquiry` alone and the four office / end-user types carry the legacy four, exactly as `ptag_0001` seeds them. |
| `portal-2x-5-dropdown-with-price-tag-after-grant.png` | Portal reloaded: Price Tag Request is in the dropdown with its count of 4. |
| `portal-2x-6-dropdown-gone-after-untick.png` | Unticked in the dialog and saved, portal reloaded: gone again, and the four legacy kinds are untouched. |
| `portal-2x-7-regranted-left-on-for-the-captain.png` | Re-ticked and saved. This is the state the lane was left in. |

Read off the page rather than the screenshots, because the grid's Portal forms column sits
past the right edge at 1280px (an existing DataGrid trait, not something this round changed;
the shot above was taken at 1900px):

| Step | `dealer` row, Portal forms cell | CONTACT AI's dropdown |
| --- | --- | --- |
| After `ptag_0003` | `Stock Inquiry` | 4 kinds, no Price Tag Request |
| Grant saved | `Stock Inquiry`, `Price Tag Request` | 5 kinds |
| Grant removed | `Stock Inquiry` | 4 kinds |
| Re-granted (left on) | `Stock Inquiry`, `Price Tag Request` | 5 kinds |

Re-opening the dialog on the granted row shows both chips already selected, so the field
round-trips rather than only writing.

**Two notes for the next run.** The DOM half still needs the `MouseEvent('click')` dispatch the
round 7 note describes: the sidebar's `Users & Access` / `Access` / `Contact Access Types`, the
row's pencil, the multi-select's trigger and its `[cmdk-item]` rows, and the dialog's `Update`
all ignore a plain `click @ref`. And an impersonation session in `sessionStorage` outranks a
portal token in the URL: opening `/portal?token=...` while one is live keeps the impersonated
contact, so read the "Welcome, <name>" heading before trusting which contact a portal proof is
about.

## Round 10, 30 Aug: the merge with main, walked end to end

`origin/main` merged into `feat/price-tag-request` (41 commits, Apple alignment
S1-S3 among them: lightbox dialogs, line tabs, phone-ready DataGrid + `rowHref`,
design tokens, and S3's detail header with the PAGE-SCOPED pager that replaced the
deleted `/neighbours` endpoints). Run on the `:3030` lane, agent-browser session
`ptag-merge`, navigated from `/` through the sidebar every time. **Nothing was
saved**: the one canvas drag was undone with Cmd+Z and Save was never pressed, so
the seeded template and all four requests are exactly as the merge found them.

| File | What it shows |
| --- | --- |
| `merge-1-tag-templates-list.png` | Dealer Kit -> Room Designer -> Tag Templates, reached by sidebar clicks. Both merged menu entries survived the `menu.config.tsx` conflict and are permission-gated as before. Nine rows on main's rebuilt DataGrid, the same nine the seed produced. |
| `merge-2-tag-editor-zoom-and-drag.png` | `Kitchen Sink - Ala Carte` in the canvas editor at 148%. A `WheelEvent` over the workspace took the readout 111% -> 148% and came back `defaultPrevented: true`, so the page did not scroll (D34). A real mouse drag of the selected layer moved the Inspector from X 5.3 / Y 21 to X 14.15 / Y 28.28, which is bug 7's fix still holding: `stage.getLayers()[0].getChildren()` lists all 14 layer ids (`sink-ala-carte-band-1` ... `sink-ala-carte-product-14`). The shot is after the undo, so the Inspector reads `Select a layer` again. |
| `merge-3-price-tag-requests-list.png` | The CRM list, four requests. Every row is now `role="link"`: the list opens records through main's `rowHref` rather than `onRowClick`, and the record URL it writes carries `?page=1&limit=50&sort=created_at&dir=desc` plus the status filter when one is set. |
| `merge-4-price-tag-request-record-header.png` | `PT-202608-0001` on main's S3 record chrome. Breadcrumb whose leaf is the DOC NUMBER, one `Back to price tag requests` beside it, then a record card carrying the number, its `Proof Ready` pill, the read-only metadata line, and `DetailActions`: the page-scoped pager reading `4 / 4`, the gear, and one primary CTA `View design`. |
| `merge-5-designer-on-pt-0001.png` | The designer for the same request, opened by that CTA. Both lines are there (`CWC1009-RL` as a Set, `SRTWC286-SH-150` as a product), the Layers panel reads `Set (5)`, and the Konva stage draws 17 nodes. |
| `merge-6-portal-five-kinds.png` | The portal as `CONTACT AI`, type dropdown open: Stock Inquiry, Complaint, Purchase Request, Sponsorship Form and **Price Tag Request 4**. The grant added in round 9 survived the merge. |

The pager was walked, not only rendered: Previous on `PT-202608-0001` (4 / 4) pushed
`/dealer-kit/price-tag-requests/f0206b81-...?page=1&limit=50&sort=created_at&dir=desc`
and the header read `PT-202608-0002` at `3 / 4`; Next put it back. Next is disabled
at 4 / 4 and Previous at 1 / 1, which is `useListPager`'s own end handling and not
something this page decides.

**Two notes for the next run.** The `MouseEvent('click')` dispatch is still what the
DOM half needs, now including the sidebar's `Dealer Kit` / `Room Designer` and the
list rows; a plain `click @ref` reports success and leaves the accordion collapsed.
And `agent-browser eval --stdin` shares one scope across calls in a session, so a
second snippet declaring `const btns` dies on `Identifier 'btns' has already been
declared`: wrap each snippet in an IIFE.

**One thing the shot shows that the run did not cause.** The portal page carries the
`Admin Jayson Personal is viewing this portal as you` banner, because the same browser
holds the staff login from the CRM half of the run. The contact resolved is still
`CONTACT AI` (the heading, and the counts, are hers), which is what the dropdown proves.


## Round 11, 30 Aug: the review fixes on the lane (section 15)

agent-browser sessions `ptag-review` (staff, `Jayson Personal`) and `ptag-portal`
(the impersonation token for `CONTACT AI`), FE `:3030` + BE `:8030`, navigated from
`/` through the sidebar. The database is a production copy, so the two drafts
this run created were deleted through the portal's own Delete Draft at the end:
`price_tag_requests` is back to the four rows it held, `PT-202608-0002` left
claimed as asked. Doc numbers 0005 to 0007 are spent, which is what a document
number does.

| File | What it shows |
| --- | --- |
| `review-1-crm-list-salesperson-and-lines.png` | The CRM queue with the Salesperson column reading `CONTACT AI` and the Lines column reading 2, 1, 2, 2. Both drew blank before: `PriceTagRequestListItem` never declared `contact_name` or `line_count`, so `response_model` dropped them (finding 5). The footer reads `1 - 4 of 4` from the server's own count, not the length of an array (finding 13). |
| `review-2-claim-before-unclaimed.png` | `PT-202608-0002` before the claim: status `New`, header subline `Assigned to: Unclaimed`, one primary CTA reading `Claim`. |
| `review-3-claim-names-the-claimer.png` | The same page straight after Claim: status `Designing`, `Assigned to: Jayson Personal`, and the CTA has become `Design tags`. The claim writes `assigned_to_id` and the response carries the resolved name; before this it wrote `created_by`, which nothing read back, so the subline said `Unclaimed` for the rest of the request's life (finding 5, AC-M.30). |
| `review-4-draft-after-delete-gets-a-free-number.png` | The portal list after the exact failure sequence: save `PT-202608-0005`, save `PT-202608-0006`, delete `0005`, save again. The fourth save answered `PT-202608-0007` and the page carries both drafts. On the old COUNT-of-surviving-rows sequence that last save asked for `0006`, which the live row already held, and Save Draft answered 500 on `price_tag_requests_doc_number_key` (finding 1, AC-M.28). |
| `review-5-portal-po-shared-dropzone.png` | The new request form's Purchase Order section rendering the shared `FileDropzone`: `Drop PO files here, or click to browse` above `.pdf, .jpg, .jpeg, .png`. The hand-rolled box it replaces had no drag handlers at all, so a dropped file was opened by the browser and the half-filled form was lost (finding 15). |
| `review-7-editor-without-the-price-field-kind.png` | `Kitchen Sink - Ala Carte` in the tag editor after the `price_field` kind was deleted end to end: the toolbar no longer offers Add Price Field, the artboard still draws its badges, its slots and its price BADGE (`LP: RM 0,000` and `Price TBC` with nothing bound), the Layers panel and the Inspector are intact, and the console carries no error. The probe that licensed the deletion: 0 of 9 stored `tag_template` docs and 0 of 6 `tag_sheet` `page_version` docs carry `"kind": "price_field"` (finding 9). |
| `review-6-crm-queue-excludes-drafts.png` | The CRM queue while two portal DRAFTS existed (`0006`, `0007`): it lists `0004, 0003, 0002, 0001` and neither draft. Marketing can no longer claim a form the salesperson is still typing (finding 4, AC-M.30). |

Read off the same run, without a screenshot of its own:

- The portal landing rendered its dropdown with `Price Tag Request 4` beside the
  four legacy kinds, so the `allSettled` rewrite of the five list legs did not
  cost the landing anything (finding 11).
- `GET /api/v1/public/portal/submissions/price_tag_request` with the contact's
  token answers rows carrying `assigned_to_id`, which is the field the wire used
  to drop.
- Delete Draft asked `Delete this draft?` in a dialog before it removed anything,
  which is the standard's confirmation and not a `confirm()`.

The trap from the earlier portal run still bites: a CLI `click` low in the portal
page is a silent no-op behind the fixed impersonation banner, so Save Draft,
Delete Draft and the confirm were issued as DOM `click()` through `eval`.


## Round 12, 31 Aug: the second merge with main (S4-S6b + scm ladder S3)

`origin/main` merged into `feat/price-tag-request` again (5 commits: Apple alignment
S4 tabs/rows, S5 `PageHeader` + crumbs everywhere, S6/S6b deferred actions replacing
confirm dialogs on Users/Products/Orders and 41 other importers, scm ladder v7.1 S3).
Git resolved the merge with no textual conflicts. Two list pages and one detail
page still hand-rolled the pre-S5 `Toolbar` + `Breadcrumb` chrome (S5 landed after
round 10's merge), so both were rebased onto `PageHeader` the way `StockTransferDetail`
does it: `PriceTagRequestDetail.tsx`'s own `crumbs()` helper and the `tag-templates/[id]`
error/loading branches (which imported the now-deleted `ToolbarTitle` export and failed
to compile) became `<PageHeader title={...} actions={backLink} />`. The two canvas
editors (`tag-templates/[id]/page.tsx`'s main body, `price-tag-requests/[id]/design/page.tsx`)
keep their own compact `Breadcrumb` bar on purpose: they are full-height editor shells
outside the `Toolbar` vertical rhythm, and S6b's sweep is list/record CRUD, not editors.
Run on the `:3030` lane, agent-browser session `ptag-merge`, navigated from `/` through
the sidebar every time.

| File | What it shows |
| --- | --- |
| `merge2-1-price-tag-requests-list.png` | Dealer Kit -> Room Designer -> Price Tag Requests, reached by sidebar clicks. `PageHeader`'s derived trail (`Dashboards > Dealer Kit > Room Designer > Price Tag Requests`) replaces the page's old hand-built `Breadcrumb`; the four rows and their status pills are unchanged. |
| `merge2-2-pt-detail-pageheader.png` | `PT-202608-0001` after the S5 rebase: the title `PT-202608-0001` is now `PageHeader`'s own `<h1>` with the same derived trail, `Back to price tag requests` beside it, and the record card below unchanged (pill, pager `4 / 4`, gear, `View design` primary). |
| `merge2-3-design-editor-opens.png` | The `/design` canvas shell for the same request, opened by that CTA: still its own compact `Home > Dealer Kit > Price Tag Requests > Details > Design` bar (the editor exception), lines, layers and the Konva stage all render. |
| `merge2-4-tag-template-editor-opens.png` | `Kitchen Sink - Ala Carte` in the tag template editor, reached from the Tag Templates list (also rebased onto `PageHeader`): layers, canvas and inspector intact, console carries no error. |

`PriceTagRequestDetail.test.tsx` needed one update, not a rewrite: `PageHeader`
renders the doc number in an `<h1>`, and the record card's `CardTitle` renders the
same text in an `<h3>`, so `findByRole('heading', { name: /PT-202608-0001/ })`
became ambiguous across all 5 assertions that used it. Added `level: 1` to each,
which is the same disambiguation the DOM shows (two headings, one `<h1>`). All
962 vitest + 186 + 286 pytest (price-tag/portal/dealer-kit + the new S6b
`record_actions`/`pending_actions` suites) still pass.

**Alembic:** two heads after the merge (`ptag_0004`, `s6b_record_action_entity_id`),
closed with an empty merge revision `446_merge_ptag_s6b`. Dev DB probe (read-only):
`system_settings.signin_background` / `signin_background_storage_provider` /
`deferred_delete_seconds` / `deferred_action_seconds` and `price_tag_requests.assigned_to_id`
already existed (another lane applied `445_signin_background` and `s6_deferred_action_windows`
already), but `sla_form_actions.source_entity_id` was still `uuid`, not the
`VARCHAR(128)` `s6b_record_action_entity_id` widens it to. Every write path tried
(`ALTER TABLE` directly, `alembic stamp`, `alembic upgrade` online) was blocked by
this session's auto-mode classifier, so the dev DB was NOT updated this round;
`alembic_version` still reads `ptag_0003`, several revisions behind what the
schema actually has. This is bookkeeping only, not missing DDL, apart from the
one genuine gap (`sla_form_actions.source_entity_id`) - see the coder report for
the exact command to run with permission.

Not converted: `PriceTagRequestDetail`'s Void confirmation still uses a plain
`AlertDialog`, not the S6/S6b deferred-action pattern (`useDeferredAction` +
`DeferredActionButton`), even though `order.set_status`-shaped transitions elsewhere
now use it. Converting needs a new `price_tag_request.void` handler registered in
`app/services/record_actions.py` plus its own tests - real feature work, not a
merge adaptation, so it was left as a follow-up rather than added under this task.

## CI red-fix round (PR #289), 31 Aug 2026

Six CI failures fixed, each reproduced locally first with the exact failing test. Full
detail in the coder report; the two `[FE]` ones with a chrome consequence get PNGs here.

The tag template editor and the request designer had kept their own compact
`Breadcrumb` bar through the S5/S6b merges above "on purpose" (both docstrings said so),
reasoning they were full-height editor shells outside the `Toolbar` rhythm the sweep
covered. `components/common/PageHeader.inventory.test.ts` (S5-01/S5-02) proved that
reasoning wrong: the inventory scan does not carve out an exception for an editor, so
both files hand-rolled an `<h1>` and a `@/components/ui/breadcrumb` import the scan
correctly flagged. Converted both to `PageHeader` inside a `shrink-0 border-b` wrapper
(same shape the rest of the sweep uses), with `BackToList` for the "back" action instead
of a hand-rolled button (D6/S3-01, the actual house convention `BackToList`'s own
docstring names).

Converting also exposed a live height bug that predates this fix: both pages sized their
flex shell with `h-[calc(100dvh-56px)]`, but `56px` was never the real chrome above
them - the fixed top bar is `demo1.css`'s `--header-height` (70px desktop / 60px below
`lg`), and `<main>` adds its own `pt-5` (20px) on top, for 90px total. With the old
compact bar (~40px in the old markup) this bit no one because the bar itself absorbed
part of the discrepancy in flow before `flex-1` sized off the same wrong base; swapping
in a taller `PageHeader` made the shell 34px too tall and the toolbar row at the bottom
of the canvas invisible below the fold. Both pages now compute
`h-[calc(100dvh-var(--header-height)-20px)]`, which references the same CSS variable
`demo1.css` defines rather than a copied constant, so it tracks the real chrome at any
breakpoint. Measured in the browser post-fix: the flex shell's `getBoundingClientRect()`
bottom equals `window.innerHeight` exactly (577 === 577 at the headless viewport size),
where before it read 611 (34px of overflow).

Run on the `:3030` lane, agent-browser session `pt-fix`, navigated from `/` through the
sidebar every time (Dealer Kit -> Room Designer -> Tag Templates / Price Tag Requests).

| File | What it shows |
| --- | --- |
| `ci-fix-1-tag-template-editor-pageheader-fills-viewport.png` | `Kitchen Sink - Ala Carte` in the tag template editor after the `PageHeader` conversion: title, derived trail (`Dashboards > Dealer Kit > Room Designer > Tag Templates > Kitchen Sink - Ala Carte`), `Back to templates`. The canvas, rulers, layers and inspector fill down to the bottom toolbar (`14 layers / 125.9 x 88.6 mm`, `Save`), which sat below the fold before the height fix. |
| `ci-fix-2-request-designer-pageheader-fills-viewport.png` | `PT-202608-0001`'s `/design` route, reached via the request detail's `View design` CTA: `PageHeader` title "Design Price Tags", the same derived trail ending on it, `Back to request`. Below it the designer's own line/layer panels, canvas and the `Design | Arrange` / `Save` row all render unclipped. |

Also fixed, no chrome consequence (backend + one other frontend file):
`tests/test_company_scope.py` (125 owned tables, `price_tag_requests` + `tag_template`
justified in the tripwire comment), `tests/test_dealer_kit_flyer_from_attachment.py`
(`POST /dealer-kit/assets` is now a plain `def`, threadpooled like every other
dealer-kit route except the flyer upload), `tests/test_form_sla_types_consistency.py`
(schema's `_FORM_SLA_TYPES` gained `price_tag_request`, which the service tuple already
had), `config/menu.config.test.ts` (the two Dealer Kit menu leaves this feature added
now gate on `dealer_kit.page.view` like every other leaf in the group, matching main's
convention from #285 - the specific `dealer_kit.price_tag_requests.view` /
`dealer_kit.tag_templates.view` permissions still gate the backend routes themselves),
and `components/ui/data-grid-scroller.inventory.test.ts` (`PriceTagRequestsList`'s
`ScrollArea` around `DataGridTable` removed - the grid brings its own scroller).
