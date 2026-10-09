# Evidence - Order inquiry links: AutoCount is the source of truth

Stack C: frontend http://localhost:3080, backend http://localhost:8080, DB sorento_cagent_stack
(shared with other stacks). Worktree /Users/tehjayson/Documents/foundryx/sorento_crm-oi-links,
branch fix/oi-links-24sep at 9319d07b3. Browser session `stackC-evidence` (agent-browser 0.27.0,
isolated), viewports 1280x900 and 375x812.

## S2 (24 Sep)

Two files already committed, from the coder's own from-scratch sandbox pass, not this run:

- `s2-worklist-lines-suggested-column-1280.png`
- `s2-worklist-lines-suggested-column-375.png`

## S3/S4 (25 Sep, this run)

- `s3-worklist-suggested-cell-1280.png` (160210 bytes) - AC-LT-50(a). Worklist Lines list for
  OI-2609-0003 after "Link selected" on all 11 rows: rows M9713SS and C-FHSS14 (x4) read
  "Not linked" (blank PO, blank SPO) with a Suggested cell (`202607-S0080` and
  `SPO-2026/09-0036`, each a clickable document button). `console`/`errors` clean.
- `s3-worklist-suggested-cell-375.png` (52286 bytes) - same screen at 375. Clean.
- `s3-link-selected-toast-1280.png` (164001 bytes) - AC-LT-50(d). Ticking M25-CR +
  MWCX7605-S-RL on OI-2609-0003 and pressing Actions > Link selected produced the toast
  "1 linked from AutoCount, 0 suggested, 1 changed" (M25-CR got AutoCount's own book link
  `202607-S0067`; MWCX7605-S-RL had nothing to offer). Clean.
- `s3-link-selected-toast-375.png` (56023 bytes) - a second, isolated Link selected press on
  MWCX7605-S-RL alone (already covered by the prior press's book step, so nothing left to
  find) produced "0 linked from AutoCount, 0 suggested, 0 changed" at 375. Clean.
- `s3-plus-n-badge-1280.png` / `-375.png` - **not captured, see Not shown.**
- `s3-auto-link-all-dialog-1280.png` (170744 bytes) - AC-LT-50's Auto link all clause, dialog
  only. Actions > "Auto link all..." on the worklist (unscoped, no OI filter) opened the
  "Auto link all" dialog ("Link open order rows to outstanding documents, nearest location and
  earliest purchase order first?" plus a purchase order cut-off date field and Cancel / Auto
  link all buttons). Cancelled, never pressed "Auto link all" - it was not run, per the brief's
  instruction that it would touch every open row on the DB. No toast to report for this one.
- `s4-lightbox-highlight-go-to-linked-line-1280.png` (145101 bytes) - AC-LT-50(b)/AC-S4/R13.
  Clicking the Suggested cell's document (`202607-S0080`) on the M9713SS row opens the same
  lines-grid lightbox a real link uses (heading "202607-S0080", "Purchase order"), with a
  "Go to suggested line" button (the R13 idiom - no separate "Suggested for" panel). Pressing
  it highlights (light-blue row) the exact PO line the cascade suggested (SKU M9713SS,
  location BRW, 179 remaining, no S/O - the open stock line the row's item would draw from,
  not merely a line matching the row's own SO). Clean.
- `s4-lightbox-highlight-go-to-linked-line-375.png` (50412 bytes) - same dialog at 375, same
  row highlighted. Clean.
- `s4-on-po-spo-row-1280.png` (140022 bytes) - AC-LT-51. OI-2609-0602 worklist Lines, scrolled
  to the PO/SPO/Suggested columns: several rows (e.g. SRTWT182-GM, CB6408, CB6633) show a real
  PO document with a "via SPO" badge in the PO column, the matching SPO document in the SPO
  column, and a blank ("-") Suggested cell - all book-named real links, none of them touched
  by this run. Clean.
- `s4-on-po-spo-row-375.png` (55319 bytes) - same rows at 375, PO column with "via SPO" badges
  visible (table scrolls horizontally to reach SPO/Suggested at this width, which is expected
  DataGrid behaviour, not a clipping defect). Clean.
- `s4-lines-tab-po-via-spo-1280.png` (157855 bytes) - AC-LT-51's second clause. OI-2609-0602's
  own detail page (`/project-sales/order-inquiries/<id>`), Lines tab, scrolled to PO/SPO/
  Suggested: rows with a "via SPO" PO badge, a real SPO document, and a blank Suggested cell.
  The State column (seen in a preceding, unsaved intermediate screen of the same page) reads
  "On PO/SPO" for exactly these rows and "To Buy" for the still-unlinked ones. Clean.
- `s4-lines-tab-po-via-spo-375.png` (56169 bytes) - same detail-page Lines tab at 375, PO "via
  SPO" badges visible. Clean.

## Not shown

- **`+N` badge** - no row across OI-2609-0602, OI-2609-0735 or OI-2609-0003 ever held more
  than one suggested link after any of the three Link selected/Auto-place attempts run in this
  session (each row's suggestion was a single document). Per the brief, not captured; no
  substitute screenshot taken.
- **Auto link all's own toast** - not captured, and deliberately not run (it touches every open
  row on the DB per the brief's own warning). Only its confirmation dialog is evidenced
  (`s3-auto-link-all-dialog-1280.png`), and it was Cancelled.

## Defect observed during this run (not fixed, not undone)

Pressing "Link selected" on OI-2609-0003 with **all 11 visible rows ticked** (via the header
"select all" checkbox, rather than hand-picking only the still-unlinked rows) included four
C-FHSS14 rows that already carried a REAL link (`state = placed`, `po_ref = spo_ref =
SPO-2026/09-0036`, written 9 Sep 2026). After that press, all four came back `state = raised`
(To buy), `po_ref`/`spo_ref` blank, each replaced by a SUGGESTED link to the same document
`SPO-2026/09-0036` (trigger `worklist`, `suggested_at` 25 Sep 08:34). See the before/after SQL
below - the C-FHSS14 rows are the only ones whose PO/SPO columns went from populated to blank
across this whole run.

This looks like it contradicts plan section 3.4's G5 guard ("no existing real link, book-named
or not, is ever re-dealt... only the row's own unlinked remainder is offered a suggestion") and
3.6 ("real links always win... [Link selected] never turns a suggestion into a real link" -
the reverse, demoting a real link to a suggestion, is not called out as something it should
ever do either). The row was never `cancelled`/`actioned`/`rejected`/`redirected_to_pool`,
the only listed triggers for dropping a real link.

Caveat: I could not determine from the DB alone whether AutoCount's book actually names
SPO-2026/09-0036 for this SO419851/C-FHSS14 line (no audit_logs rows exist for this action -
the link/suggested-link tables carry no history), so I cannot rule out this being the S5
conversion script's intended class-(b) treatment of a *legacy* cascade link happening live
instead of via the offline script - but Link selected/Auto link all are specified to never do
that themselves (3.4's G5 guard exists precisely to stop this). Flagging for the reviewer and
the owner to judge; not fixed, not reverted (no write SQL, no code change, per the brief).

## Data left as found

Before (read at start of this run):

```
select o.inquiry_no, r.item_code, r.state, r.po_ref, r.spo_ref, r.actioned_at
from projects.order_inquiry_rows r
join projects.order_inquiries o on o.id = r.order_inquiry_id
where o.inquiry_no in ('OI-2609-0602','OI-2609-0735','OI-2609-0003')
order by o.inquiry_no, r.item_code;
-- 51 rows. OI-2609-0602: 9 rows placed (real links), 5 rows raised (CB2829-DIY, SRTWB103,
-- SRTWB104, SRTWT9610-GM, TPE-9201-300), all blank PO/SPO. OI-2609-0735: 20 rows, all raised
-- or cancelled, all blank PO/SPO. OI-2609-0003: C-FHSS14 x4 placed (SPO-2026/09-0036),
-- MFG6630-PP and MHS1028 placed (PO-2026/09-0010), M25-CR/M9713SS/MKT5529SS-DIY/
-- MWCX7605-S-RL/MWCY7605 raised or cancelled, all blank PO/SPO.

select o.inquiry_no, r.item_code, s.document, s.qty, s.trigger, s.suggested_at
from projects.order_inquiry_suggested_links s
join projects.order_inquiry_rows r on r.id = s.row_id
join projects.order_inquiries o on o.id = r.order_inquiry_id
order by s.suggested_at;
-- 0 rows (table empty).
```

After (read at end of this run):

```
-- order_inquiry_rows for the same 3 inquiries: 51 rows, same shape, except:
--   OI-2609-0003 / M25-CR: raised -> placed, po_ref 202607-S0067 (NEW REAL LINK, AutoCount
--     book-named - left as found, this is truth per the owner's ruling).
--   OI-2609-0003 / C-FHSS14 (all 4 rows): placed, po_ref=spo_ref=SPO-2026/09-0036 ->
--     raised, po_ref/spo_ref blank (REAL LINK REMOVED - see "Defect observed" above; left
--     as found, no Unlink action available since nothing was wrongly WRITTEN, only removed).
-- Every other row in the 51 is unchanged from the before read.

select o.inquiry_no, r.item_code, s.document, s.qty, s.trigger, s.suggested_at
from projects.order_inquiry_suggested_links s ...
-- 5 rows now:
--   OI-2609-0003 / C-FHSS14 x4 -> SPO-2026/09-0036, qty 3, trigger worklist (from the
--     demoted real link above)
--   OI-2609-0003 / M9713SS -> 202607-S0080, qty 3, trigger worklist (new suggestion, row was
--     always unlinked)
```

Rows that changed, named:

- **Real link written (AutoCount book, truth, left as found):** OI-2609-0003 / M25-CR ->
  `placed`, PO `202607-S0067`.
- **Real link removed, suggestion written in its place (see Defect section, left as found,
  not reverted):** OI-2609-0003 / C-FHSS14 (all 4 rows) -> `raised`, suggested
  `SPO-2026/09-0036`.
- **Suggested link written on a previously fully-unlinked row (expected, left as found):**
  OI-2609-0003 / M9713SS -> suggested `202607-S0080`.
- OI-2609-0602 and OI-2609-0735: no row changed state or gained/lost a link across this run
  (every Link selected press against them returned all-zero counts).

No UPDATE/DELETE SQL was run. No Unlink action was used (nothing was wrongly WRITTEN by this
run that Unlink could correct - the C-FHSS14 case is a removal, not an addition). "Auto link
all" (unscoped, DB-wide) was opened and Cancelled, never run. The detail page's single-header
"Auto link" (`Order inquiry options > Auto link`, scoped to `inquiry_id` only, confirmed via
its request payload) was triggered once by accident while looking for the same confirmation
dialog the worklist's "Auto link all..." has - it returned all-zero counts
(`placed_rows/allocations/products_touched/book_linked_rows/suggested_rows/changed_rows` all
0) for OI-2609-0602, so it changed nothing.

## Captain verification of the Link selected observation (25 Sep, read-only SQL)

- The four C-FHSS14 rows on OI-2609-0003 (sales order SO419851) held their SPO-2026/09-0036 link
  from the cascade, not from AutoCount: `scm.order_link_claim` carries that claim with
  `source = order_inquiry`, claimed 8 Sep 2026 13:27, `spo_allocation_id` set, no `po_line_id`.
- The AutoCount book names a different document for those lines: `scm.order_link_claim` row
  `source = autocount`, po_number 202607-S0110, claimed 9 Sep 2026 08:20, `po_line_id` set. PO
  202607-S0110 is `active`; its C-FHSS14 lines are all `closed` (received) apart from two `open`
  lines, so the book step wrote no real link for them on this press.
- Net effect of the press on these four rows: a cascade-only real link was removed and a
  suggestion to the same SPO written in its place, while the row went back to To buy. Plan
  section 3.4, G5 guard (dated 25 Sep 2026): "no existing real link, book-named or not, is ever
  re-dealt". Left for the reviewer and owner to rule on; nothing undone.
