# Browser evidence, #904 (the section 7 follow-up)

15 Sep 2026, agent-browser 0.27.0 headless, isolated session `oi-pairing` (closed at the
end). Stack as found, nothing started or stopped: FE `http://localhost:3086`, BE `:8086`,
lane worktree at `0436330d0`, database `sorento_ai_automation_0913` with `scm.committed_v`
already at migration 511. No worker and no upload: the copy already holds the rows the
#886 code raised, which is what makes it the right substrate for this pass - it shows the
NEW readers over OLD data.

Navigated by sidebar from `/`: Procurement > Supply Chain > Order Inquiries.

## 1. The PO column names the purchase order behind a shipment (AC-R-35)

Searched `SO368872`. The `SRTWC286-SH` row, one line tall at 44px:

| Qty | PO | SPO | Taken by PO/SPO | Remaining |
| --- | --- | --- | --- | --- |
| 364 | `202510-S0078` | `SPO-2026/04-0043` | 124 | **0** |

The PO cell holds one trigger, `backing-documents-trigger-beed6f4c-...`, whose text is
`202510-S0078`. That number is NOT on a `po` link of its own here - it is the
`source_po_number` the shipping order link carries, which is the whole of 7.2's frontend
half: once the importer stops linking a purchase order line for units already on its own
ship, the PO column would otherwise go blank on exactly the rows purchasing wants to trace.

Clicking it opens the row's own lightbox:

```
Backing documents
124 of 364
SPO  SPO-2026/04-0043   from PO 202510-S0078   BRW-IB - 62   23/04/2026   Confirmed
PO   202510-S0078                              BRW-IB - 62   01/01/2026   Confirmed
```

Both documents, 62 each, 124 of 364 - this copy still carries the DOUBLE LINK the pre-7.2
importer wrote, and that is expected here. It is also the clearest possible picture of what
7.2 fixes: the same 62 units owed twice, on a purchase order line and on the ship that line
became. A re-upload after this lane deploys writes one link of 62, not two.

Screenshot: `04-so368872-po-via-source-po-1280.png` (1280x800, the row and the cards).

## 2. Buy, over the same row (7.3 / AC-R-36, AC-R-38)

* Unfiltered, the whole list: **Buy 153,556**.
* Filtered to `SO368872` (15 rows): **Buy 0**.

The sales order line ordered 364 and has delivered 352, so twelve are owed and 124 are
already on documents - there is nothing left for purchasing to buy, and the card says so.
Before 7.3 this row alone read Buy 240 (`364 - 124`). The Remaining column on the row reads
`0` off the same capped figure, which is the point of AC-R-38: the card, the column, the
view and the plan's own SELECT all answer one question with one number.

## 3. 375px

Same row, same two cells, measured at `375x812`
(`05-so368872-375px.png`): `document.scrollWidth == clientWidth == 375`, so the PAGE does
not overflow; the grid's own container scrolls (341 visible, 2,664 wide). The row stays 44px,
PO reads `202510-S0078` and SPO reads `SPO-2026/04-0043`, neither clipped
(`scrollWidth <= clientWidth` on both cells).

## Console, errors, network

`errors` empty. Zero `[error]` or `[warn]` lines in `console`. Every `/api/v1/` request
returned 200 - no failures, and no 4xx.
