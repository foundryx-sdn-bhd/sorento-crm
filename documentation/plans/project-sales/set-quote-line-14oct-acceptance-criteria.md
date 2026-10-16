# UAC - A product set is ONE quotation line (SET-QUOTE-LINE)

**Status:** agreed 14 Oct 2026. Owner answers on card + mockup v1: Q1 (a), Q2 (a), Q3 (a), Q4 (b) company default only, Q5 (a); parts are NOT listed on the line, they go in the COMPLETE SET text box. Mockup v2 (`documentation/mockups/set-quote-line/index.html`, commit 8ab10d987) carries those changes and was APPROVED by the owner on 14 Oct ('ok can'). Naming ruling the same day: one word "Non-standard" for every line flag (section G).
**Plan:** `PLAN-set-quote-line-14oct.md`
**Mock:** `documentation/mockups/set-quote-line/index.html`

## Journey

A salesperson quotes a development. The customer's bill of quantities says "WC CWC1009-RL, 1,046 sets". Today they add three lines (pedestal, cistern, seat cover), price one, zero the others and type "c/w" text. After this lane they pick **CWC1009-RL** once: one line, one quantity, one rate, and the line's Complete set box already reads "CWCX1009-RL + CWCY1009 + CWC1009-SC". The series the scope is quoted from holds the SET's price and maximum discount, so the rate fills in, the floor is checked against the set, and a rate below the floor needs approval before the quotation is issued. The PDF prints one row. When the customer's PO arrives, the SO draft splits the set into its parts the way AutoCount SOs are written today.

## A - The line names a set

- **A1** A quotation line carries a product OR a set, never both. Sending both is refused (422) and the database refuses it too.
- **A2** Saving a line that names a set stores: set code as the printed code, set name as the description (unless the user typed one), set list price (override, else ticked members; may be empty), and a frozen copy of the parts (code, description, quantity per set, which part is the pedestal). The parts copy is not shown on the screen; it is what the SO draft splits (E2).
- **A2b** Complete set (owner hand test 14 Oct, item 3 (a)): the line UI no longer shows a Complete set or Priced per input; the columns stay and keep printing (PDF, Excel, sign page). A set line's Complete set is ALWAYS its part codes joined by " + " ("2 x CODE" when the quantity is not 1, cut to 100 chars), set on every set change; moving a line from a set to a product (or to nothing) clears it. A product line's Complete set is left as stored (new product lines print it empty).
- **A3** UOM defaults to `SET` when the line has none.
- **A4** A set belonging to another company is refused (as not found). An inactive set is refused on a NEW pick; a line already holding a set that later went inactive still saves.
- **A5** Changing the line from one set to another (or to a product) refreshes the parts copy and clears the picked photo/drawing; any other edit keeps the parts copy as quoted (Q5 a). Moving from a set to a product clears the parts copy.
- **A6** Revise copies the set and its parts copy to the new version.
- **A7** A line with neither product nor set still needs a description (today's rule); a set line does not show "Custom item".

## B - Price, floor, standard

- **B1** Picking a set fills the rate with the series' selling price for that SET when the scope's series names one; otherwise the rate is left alone (as for products).
- **B2** A set line is standard when the scope's series has a row for that set; non-standard otherwise. A scope with no series: standard.
- **B3** Floor, in order: (1) the series row for the set, when both selling price and max discount are set: price x (1 - max/100), level "series"; (2) else the company default rule only, applied to the SET list price (Q4 b: no product or category rule, no category walk).
- **B4** A percent company default with no set list price yields no floor (as for a product with no list price). An absolute company default applies as is.
- **B5** A set line below its floor is flagged, notifies management on the transition into breach, and blocks Issue until approved, exactly as a product line.
- **B6** The live check on the grid gives the same verdict for a set line as the save does.
- **B7** "Recheck alerts" re-evaluates set lines.

## C - Series: the set's own price row


- **C1** A series has a Sets list: set, name, parts, selling price, max discount %, floor. Add a set, type the numbers, remove a set. Same permissions as the series Products table.
- **C2** Sheet import: a code that is a set writes ONE series_sets row for that set with the sheet price/discount (a code-only row writes the set row with no numbers, so the set is standard, and never blanks numbers already there); the parts get rows with no price (#1680 D3, unchanged).
- **C3** Sheet import: a family code (e.g. `SRTWC8608`) writes a series_sets row for EVERY active set in the family, each with the sheet price/discount. Replace mode removes series_sets rows whose set is not on the sheet; append keeps them.
- **C4** The load report's resolved entry for a set or family code names the sets it priced (their codes).
- **C5** Company scope: another company's sets never appear or resolve.

## D - Documents

- **D1** PDF, Excel and the customer sign page print a set line as one row: PRODUCT CODE = set code, DESCRIPTION = set name, COMPLETE SET = the line's Complete set text (filled with the parts per A2b), QTY with UOM, one rate, one total. No document-side fallback: the documents print the stored text as they do today.
- **D2** The photo on a set line comes from the pedestal part's product files.

## E - SO draft

- **E1** A customer PO line that matches a set line (by the set code) drafts one SO line per part: the pedestal at the quoted set rate, the other parts at 0.00, quantity = PO quantity x part quantity; each SO line carries the quotation line id and explosion source `set`.
- **E2** The parts come from the line's frozen copy, not today's set master (a set edited after quoting still drafts what was quoted).
- **E3** An AutoCount item package for the code still wins over the set line (unchanged order); old quotations quoted as component lines still split by today's fallback.
- **E4** Each part keeps its OWN unit (the part's base UOM, frozen with the parts), not the PO's "SETS". The split also works after the set is deleted (the frozen parts are enough).
- **E5** A PO line naming a set code that splits from a quoted set line raises no "has no product" (unresolved_product) stop; a PO line with an unknown code still does. Owner ruling 14 Oct (a): a set PO line drafts UNPHASED and CS types the delivery date on the SO draft; mapping a schedule column to a SET is a follow-up lane.

## F - Grid (frontend)

- **F1** The product picker offers sets and products together, sets first; searching by set code or name finds them. A set option shows ONLY its code, on one line (owner hand test item 1); the Set badge stays on the picked value in the cell.
- **F2** A set line shows the Set badge with the code, in edit and read mode. No parts list under the description (owner, mock v1); the parts show in the Complete set box of the line details.
- **F3** A set line's quantity cell shows the SAME stock pill as a product line (owner item 2): "N avail" where N is the complete sets available in the chosen Stock at group (the smallest of each part's available quantity divided by its quantity per set), red when 0; clicking it opens the same stock popover, listing each part with its own stock table.
- **F4** Usable at 375 and 1280; the complete-sets count under Qty is fully readable.
- **F5** Picking a set fills brand (the pedestal part's brand) AT PICK when the line's brand is blank; a typed brand is kept.
- **F6** The picker, the set fill and the complete-sets count read sets through `GET /api/v1/master-data/product-sets/select` (signed-in session, own company only), because the sales roles that edit quotations do not hold `master_data.product_sets.view`. The count is returned only to callers who hold `projects.projects.view` with the inventory module on; others get none.

- **F7** Lines do not expand (owner item 4). Each row has a Notes icon; it opens a small popover with the Notes box only; the icon shows as filled when the line has a note.

## G - One word: Non-standard

- **G1** Every quotation line flag reads "Non-standard". A line with neither product nor set: tooltip "Not a catalogue product". A linked product or set outside the series: tooltip "Outside the series this scope is quoted from". Never two pills on one line.
- **G2** The word "Custom" appears nowhere for a quotation line: the description error reads "A non-standard item needs a description", the Recheck toast "reads as non-standard", the recheck summary fallback "Non-standard item".
