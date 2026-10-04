# PLAN - stakeholder demo follow-ups of 19 August: ladder v2 (ownership groups, horizon, all-or-nothing), agent everywhere, plans page, approve all, fast reader

**Status:** A, B, C, D IMPLEMENTED on `fm/scm-stack-followups-19aug-3` (19 August 2026, evening),
reviewed once, review findings fixed in the same branch. Deferred from this branch, by name:
C1's board preview of an unsaved policy (the `?preview_policy=` lever exists, no UI yet); D1's
Amend / Release verbs on a Plans row (Open only; the verbs live on the SO sheet it opens); D4
(server-stored board draft); B5's `flash-lite` trial (the model is sunset for new API keys,
its successor rejects this REST shape - not measurable today). F closed: SO331506 reads order
date 2024-12-18 / required 2030-01-01, the pasted popover was another document's. E (ladder v2)
waits for the three answers in section 8. Facts verified in code and on the live stack DB
(worktree 11, `sorento_scm_e2e_stack`).

**B5 measured (19 Aug, golden fixtures, real key, `extract_document` called directly):** PO 10
pages 71.6 s -> 12.5 s elapsed (14.2 s wall), all 51 lines and the PO number read; schedule 7
pages 89-123 s -> 16.0 s elapsed (16.9 s wall), 7/7 pages. JPEG q85 body 2.46 MB vs 4.64 MB
PNG. Default model unchanged (`gemini-2.5-flash`).

**Slug:** demo-followups-19aug-ladder-v2. **Sits on:** `PLAN-fulfilment-planning-from-autocount-so.md`
(board, ladder, Order Inquiries), `PLAN-scm-front-planning.md` 3.3/3.3a (reserve, hot-selling,
pool rung), `PLAN-so-book-diff-replanning.md` (planning changes), `PLAN-project-lead-to-so.md`
5b/5c (the reader spikes).

## The captain's ask (19 August 2026, after the stakeholder demo)

1. PO / delivery schedule reading is too slow; "make it absolutely the fastest". Text + image
   together is agreed.
2. Sales Orders toolbar: Upload goes under an Actions menu like Delivery Orders.
3. Capture and display the sales agent on every SO; SCM SO detail gets the SAME view/edit as the
   project sales order page (in place, one Save); agent tag on each SO is "useful information";
   the cell drawer's Documents list shows Agent too.
4. "Is the plan stored, how do I review it" - a Plans page, and a list view of the board so
   Approve all can be seen from an overview.
5. Approve all on the board.
6. Ladder: a far-future line (Jan 2029) is Buy all. No partial decision (reserve half, buy half is
   not fulfilled). Own location has NO reservation - stock at BRW-BB is committed to customers;
   what happens there is "borrow from another sales order", order-back required. Rank weights
   configurable in the UI. Horizon configurable as a policy. Incoming counts toward cover.
7. Explanatory sentences ("Borrowed x for SOxx line xx; xxx goes short by xxx", "242 lines ahead
   wanting ...") go into tooltips.
8. Reserve/borrow must see the whole ownership group: BRW-BB, MWH-BB, DC1-BB are one "BB", owned
   by the BB salespersons (TERA, JEREMY, CONTACT W, JAY, BRENDON, CONTACT Y, CONTACT O, CONTACT AA,
   CASSANDRA). Cross-location borrow only for small quantities; same-location borrow from other
   SOs is the norm; and the donor list must surface the SAME AGENT's other SOs (even higher
   ranked), because the agent can authorise CS to move stock between her own orders.

## What exists today (verified)

- Reader: one Gemini vision call per page, strictly sequential, 170-DPI PNG via PyMuPDF,
  `thinkingBudget=0`, no retries, nothing persisted until the last page
  (`document_extraction.py:205-237`). Live: PO 10 pages 71.6 s; schedule 7 pages 89-123 s.
  After the model: whole-catalogue SELECT per unmatched PO line
  (`project_po_intake_lifecycle.py:928-1015`), trigram scan per unresolved schedule column.
- `sales_orders.sales_agent_id` exists and is filled by the upload (2,233 of 2,249 open uploaded
  SOs on the stack). Never serialized, never shown. Sales Agents master at Master Data ->
  Sales Agents (7 of 55 classified).
- SCM SO detail is read-only; edit is a list modal. Project SO page edits in place.
- Board writes nothing; Approve is a client draft; the write is per SO
  (`POST /sales-orders/{pso}/confirm` -> `so_supply_decisions` + `so_line_allocations`). No Plans
  page. Bulk approve exists only inside one cell. Grid columns = only the dated weeks owed, on a
  `w-max` table (2 columns, rest empty).
- Ladder: Reserve own -> Reserve pool -> timely SPO -> Buy residual; partial inherent; no horizon;
  Borrow never proposed; donors = other locations' free stock + other PROJECTS' holds; engine
  knows no agent. Rank weights = DB row `scm.priority_policy` (no UI).
- Pool today = `warehouses.pool_warehouse_id` = the SITE parent (BRW-BB -> BRW). Live: plain
  BRW holds 892k on hand, MWH 61k, DC1 47k, WH3 52k. Suffix groups BB/HP/IB/IR/NTC/RSV/SMC exist
  at all five sites (BRW, DC1, MWH, RSW, WH3).

## 0. Journey (design before schema)

**Actor:** the project planner (CS / office) on the Fulfilment Planning board, after an
AutoCount SO book upload. Purchasing reads Order Inquiries. The sales agent is never asked; she
is NAMED so the planner can phone her.

**First screen:** the worklist. Each SO row now shows its Agent. She ticks orders, "Plan
together". The grid fills the width; weeks owed are the columns.

**One decision per line, fewest possible:** every cell row carries ONE proposal that is whole:
`Buy all` (beyond the horizon, or nothing can cover it in full), or `Cover` (incoming + pool +
borrow within the ownership group that together cover the WHOLE line). Never "reserve 213, buy
145". The proposal names the donor SO and its agent when it borrows ("Borrow 145 from SO371334
line 2 at MWH-BB · agent JEREMY · order-back raised"). Approve, Amend, or Reject.

**Approve all:** one button approves every proposal on the board; a list view (one row per
line: SO, agent, product, date, proposal, verdict) shows what is about to be committed;
"Confirm all approved" writes them in one call.

**What she holds at the end:** a Plans page listing every confirmed decision (SO, agent,
revision, components, who/when), openable to Amend or Release; Order Inquiries raised for every
Buy and every order-back; the SO detail page shows the agent and the active plan.

**Policies she can set (Supply Chain -> Policies -> Fulfilment):** rank weights, horizon
(months), small-quantity threshold for cross-group borrow.

## 1. Workstreams

### A. Cheap and independent (start now) - FE + thin BE

A1. Sales Orders toolbar: second `secondaryAction` (Refresh) so the shared toolbar renders the
    Actions dropdown (`data-grid-list-toolbar.tsx:465-503`); Upload lives there. Add sales order
    stays primary.
A2. Agent everywhere: serialize `sales_agent` (code + person label) on the SCM SO schema; list
    column + filter; detail header field; worklist row; cell drawer Documents list column; board
    row payload (`_Row`) carries `agent_code`.
A3. Tooltips: OI Instruction column shows verb + qty, the "Borrowed ... goes short by" note
    behind an info icon; cell drawer "N lines ahead wanting ..." and the rung reason sentences
    behind the same icon.
A4. Grid fills the width: table `w-full` with `min-w` per column; product column sticky.
A5. SCM SO detail view = edit, in place, same layout as the project SO page
    (`SalesOrderDetailClient.tsx` pattern, one edit session, header Save/Cancel, `?edit=1`):
    header fields Order type, Customer, Priority, Requested delivery, **Agent** (clearable
    SearchableSelect over sales agents); lines SKU + qty. PUT `/scm/sales-orders/{id}` gains
    `sales_agent_id`. The list modal becomes create-only.

### B. Reader: absolutely the fastest (start now)

B1. Pages concurrent: `extract_document` runs page calls through a bounded pool (8 wide,
    setting `document_ai_page_concurrency`), order preserved in the result. Target 10 pages in
    about 10-15 s.
B2. Text + image together: when the PDF page has a text layer (`page.get_text()` non-empty),
    send the text block in the same user turn as the image ("The page's own text layer, for
    exact codes and numbers; the image is the authority for strike-throughs, handwriting,
    highlights"). Prompts gain an optional `{{page_text}}` variable.
B3. Persist per page: pass `on_page` from both callers; `extracted_json.pages[i]` and a
    `pages_done` counter land as each page finishes; FE progress "page 4 of 10".
B4. Post-processing: load the catalogue once per run (`_resolve_product_loosely` memo);
    schedule `_product_by_similarity` in one trigram query for all unresolved columns; drop the
    per-line `flush()`.
B5. Measure: golden set (PLAN-project-lead-to-so 5a) before/after, record numbers in this plan.
    Try `gemini-2.5-flash-lite` on the schedule cell matrix on the same set; adopt only if
    cell accuracy holds.
B6. JPEG at quality 85 instead of PNG for the page image (smaller body, same read) - measure.

### C. Policies UI (start now)

C1. `scm.priority_policy` gets an admin API (GET active, PUT weights + class weights) and a
    "Fulfilment" tab on Supply Chain -> Policies: weight sliders per factor, preview on a chosen
    board (`?preview_policy=` exists), Save activates a new revision.
C2. New fulfilment settings on the same policy row (or a sibling `scm.fulfilment_policy`):
    `buy_all_horizon_days` (default 180), `cross_group_borrow_max_qty` (default 50),
    `cross_group_borrow_max_pct` (default 10). Migration + seed.
C3. Ownership group on the agent: `sales_agents.location_group` (e.g. `BB`), editable in
    Master Data -> Sales Agents; seed BB for the nine names the captain gave. Warehouse group =
    the suffix after the hyphen (`BRW-BB` -> `BB`); plain site codes (`BRW`) are pools.

### D. Plans page + Approve all (start now; FE against the existing confirm route, the batch
    route added in the same PR)

D1. Plans page (Project Sales -> Plans): DataGrid over `so_supply_decisions` (active +
    superseded): SO, customer, agent, revision, state, components summary, decided by/at; row
    -> the SO sheet; verbs Amend / Release. Re-mount the Allocation section on the SO page.
D2. Board list view toggle (Grid | List): one row per line with proposal + verdict + agent.
D3. Approve all (board-wide, draft) + `POST /fulfilment-planning/confirm-all` (body: per-order
    line sets; one transaction per order, result per order, no partial silent success).
D4. Optional: server-stored board draft (`so_supply_board_drafts` keyed by user + order set)
    so a reload does not lose the session. Do after D1-D3.

### E. Ladder v2 - `front_planning_engine.propose_line` + supply service (captain's answers,
19 Aug late evening: pool stays a Reserve source; the own `-BB` location is never reserved;
positive Available inside the group is takeable; the horizon is purchasing's coverage DATE)

Vocabulary: own location L = `BRW-BB`; site S = the prefix (`BRW`); pool P(S) = the plain site
warehouse (`BRW`, 892k on hand live); other pools = `MWH`, `DC1`, `WH3`; ownership group G = the
suffix (`BB`) = every `*-BB` location; the line's agent A = `sales_orders.sales_agent_id`.

Order, per line, owed Q with required date D:
0. **Coverage date:** `reorder_coverage_until` set and D is after it -> `Buy Q now` ("beyond
   purchasing's coverage, 31 Oct 2026"). Stop. Purchasing reorders up to that date; what lies
   beyond it is not covered by the regular reorder and is bought immediately.
1. **Timely incoming** (SPO arriving by D, rank-attributed) counts toward cover.
2. **Pool reserve:** P(S) first, then the other site pools; capacity = `max(min(free,
   available), 0)` (signed Available = on hand - SO qty + SPO qty, never below zero), hot-selling
   gate of 3.3a retained.
3. **Group take:** sibling locations of G at the other sites (`MWH-BB`, `DC1-BB`, ...) with
   POSITIVE Available -> take from them. The own location L is never a source.
4. **Group borrow:** other sales orders' committed quantity at G's locations (L first, then the
   siblings): donors ranked LOWER than this line by the active priority policy are proposed
   automatically; the SAME AGENT's sales orders are offered at any rank, marked "same agent -
   she can authorise". Every borrow carries an order-back: an Order Inquiry Buy row for the
   donor SO line, urgency = the donor's required date. Source type `order`.
5. **Cross-group borrow** (free stock at locations outside G, the existing `other_location`
   donors): offered only when Q <= `cross_group_borrow_max_qty` or Q <= `max_pct` % of that
   location's free stock.
6. **Whole-line rule:** if 1+2+3+4(+5) cover the WHOLE of Q, propose that composition in rung
   order; otherwise propose `Buy Q` (the partial components are dropped, not mixed in).
7. The own-location Reserve rung is REMOVED (the strip still shows L's figures, read-only).

### F. Rank: check the popover fact

The pasted popover showed Delivery date 2026-08-03 / Order date 2024-11-04 for a line dated
01/01/2030. Verify `need_by_date` reads the line's required date on that line; fix if not.

## 2. Tests

- pytest: engine rules (horizon, whole-line, group borrow, same-agent donor, cross-group cap),
  policy API, confirm-all, agent serialization, reader concurrency + per-page persist, golden
  set accuracy unchanged.
- vitest: toolbar dropdown, agent column/field, tooltips, grid width, plans page states, list
  view, approve all.
- agent-browser evidence run on :3050 for the board journey.

## 3. Delivery

Per memory "follow-ups skip pipeline after first pass": fix + targeted tests + one reviewer +
merge-shaped push, CI gates, captain merges. Each workstream is its own PR off main so A-D land
while E is confirmed. Coders on sonnet from exact briefs; file ownership per brief.

### G. Tag an outstanding PO to an Order Inquiry (captain, 20 Aug: "identify which
outstanding PO has quantity to fulfil this order inquiry, tag it, and the quantity to be
ordered is deducted")

Facts (scouted 20 Aug): outstanding supplier POs land in `purchase_orders` / `purchase_order_lines`
(AutoCount book; line balance = `qty_ordered - qty_received` on `line_status='open'`, index on
`(product_id, warehouse_id, line_status)`). A PO is NOT supply in any netting view - `on_order_v`
reads `spo_allocations` only (migration 337; PLAN-scm-purchasing-fulfilment S9: "supply is the
SPO allocation, never the purchase order"). This feature is a deliberate, evidence-carrying
exception on the DEMAND side only: tagging does not make the PO supply, it retires the inquiry's
claim on the reorder suggestion. The lever already exists: `committed_v`'s confirmed leg counts
OI ORDER rows only in `state='raised'` (demand.py:296), so a new state removes the demand with
no view change. Today "mark actioned" already does that silently with no record of what covered
it - the new state replaces that hole with evidence.

Design:
- `order_inquiry_rows` gains `po_ref` (String(80)) and `po_line_id` (FK purchase_order_lines,
  SET NULL); new state `placed` (vocabulary: raised | actioned | cancelled | placed).
- Candidates for a raised ORDER row: open PO lines of the same product, balance minus qty
  already tagged by other placed rows, soonest `expected_date` first; recommend the earliest
  line whose remaining balance covers the row.
- Tag verb "Place on PO" on the worklist row (and per-project page): sets po_ref / po_line_id /
  state='placed', writes an OrderLinkClaim (source 'order_inquiry', resolved so_line_id +
  po_line_id) for the audit trail; the note reads "Placed on <po> (<supplier>), expected <date>".
  Untag returns the row to raised and clears the fields.
- Netting effect: a placed row leaves `project_confirmed_committed`, so the next reorder run's
  suggested qty drops by the placed quantity - the captain's deduction. A second row can never
  tag the same PO quantity (the candidate query nets placed claims per po_line_id).
- Worklist: PO no / Supplier columns show the tag (extend the `_PLACED_PO_ID` coalesce);
  state filter gains Placed.
- Known adjacent hole, logged not fixed here: `sales_order_lines.purchasing_status='ordered'`
  is not excluded from `committed_v` (double-count risk independent of this feature).

**G2 - 20 Aug afternoon rulings (captain, live-testing the shipped G):**

1. **Auto-place, not click-by-click.** "We need to link already at first already instead of
   suggesting and needing the users to click 1 by 1." The system creates the placements
   itself whenever a raised ORDER row and outstanding PO lines for its product both exist;
   the user reviews and UNPLACES by exception. The dialog survives as override + audit
   (see what was auto-placed, unplace, place manually), not as the workflow.
2. **Cascade from the earliest.** "Take from the earliest PO, then subsequently from
   subsequent PO": allocation splits the needed qty across PO lines in soonest
   `expected_date` order (ties by document sequence), each line giving
   `min(remaining balance, still needed)`, partial coverage allowed - the uncovered
   remainder stays a raised Buy. This REPLACES G's single-line "recommend the earliest
   line whose remaining balance covers the row".
3. **POs only.** SPO- prefixed documents are not Place-on-PO candidates (and the PO book
   page filters them out) - "those are SPO, not PO". SPO coverage stays the engine's job.
4. Auto-place runs idempotently at three moments: after an outstanding PO book import,
   after an OI decision confirm spawns/updates ORDER rows, and on demand from a worklist
   "Auto-place" action. Every auto placement writes the same OrderLinkClaim evidence with
   an auto marker; unplace always available.

## 8. Open questions (captain)

1. Plain site pools (BRW 892k on hand, MWH, DC1, WH3): still a RESERVE source for a BRW-BB line,
   own site first then the others? (Plan assumes yes.)
2. Inside the ownership group, a location with POSITIVE Available (e.g. MWH-BB on hand 500, SO
   qty 0): take it as free ("take from MWH-BB"), or is everything in a `-BB` location treated as
   some SO's stock and only borrowable? (Plan assumes positive Available = free to take.)
3. Horizon default: 180 days? Small-qty cross-group default: 50 units or 10% of the line?
