# Browser verification run, oi-sheet-pairing-repair

14 Sep 2026, tester agent, agent-browser 0.27.0 headless, isolated session `oi-pairing`
(closed at the end). Stack as found, not started or stopped by this run: FE
`http://localhost:3080`, BE `http://localhost:8080`, worker on redis db 7, lane worktree at
`6e4de9ebc`, database `sorento_ai_automation_0913` (the 3am 14 Sep prod copy, BEFORE the
owner's upload - it held 28 `order_inquiry_links` when the run started).

**Verdict: PASS on AC-R-26 to AC-R-31, and the owner's own row lands exactly where the plan
said it should.** No console error, no page error, no failed request.

## 1. Upload, preview, apply

Navigated by sidebar from `/`: **Procurement > Supply Chain > Reorder Planning**
(`/scm/reorder`), Actions > "Upload order inquiry sheet", attached the owner's
`JAN - DEC 2026 ORDERabc.xlsx` (1,048,257 bytes), pressed Test.

Preview panel (read off the screen; two screenshots per lane, and they are spent on
the two things a number cannot show):

```
Rows: 15,797 - Would import: 8,269 - Skipped: 855 - Errors: 0
No errors - You can upload this file.
Warnings (2): 75 cited documents we could not link: ... 202511-S0097 and 63 more
```

Confirm upload -> `POST /api/v1/scm/order-inquiry/apply` **202**; the upload drawer showed
`Importing... 0 / 15797 rows`, and the worker finished it in about one minute.

Job `a15b4ad2-dfe7-48c7-8005-bca6aac2f285`, `/system-management/import-jobs/...`:

| figure | value | expected |
| --- | --- | --- |
| Total rows | 15,797 | 15,797 |
| Processed | 15,797 / 15,797 | - |
| Successful | 14,938 | - |
| Created (rows raised) | **8,269** | 8,269 |
| Failed | 0 | - |
| Skipped | 859 | - |

Skipped breaks down as `qty_exceeds_ordered` 473 + `no_line_for_item` 382 +
`order_not_plannable` 4. The first two are `rows_line_not_found` = **855**, exactly the
expected figure and exactly what the preview said; the job's 859 adds the four orders that
are not project demand, which the preview counts separately.

`restates_an_instalment` = **6,669** ("Counted into a delivery this file already states").
That is R3 doing its work on the customer's roll-up tabs, and 8,269 + 6,669 = 14,938
successful, which reconciles.

Counted straight off the database, for the two link figures the screens do not print:

```
rows raised by this file                                  8,269   (expected 8,269)
rows carrying at least one link   (= links_written)       5,740   (expected 5,740)
rows carrying an auto link   (= links_from_autocount)     5,487   (expected 5,487)
links written by this file                                6,744
links in the database afterwards                          6,772   (= 28 before + 6,744)
```

**Every expected number matched to the row.**

## 2. The owner's own row: SO347594 / CB2154-DIY

Navigated by sidebar to **Procurement > Supply Chain > Order Inquiries**
(`/project-sales/order-inquiries`), searched `SO347594`.

The grid's header row reads `... | Supplier | PO | SPO | Taken by PO/SPO | ...`.

Exactly ONE CB2154-DIY row on that sales order:

| Qty | PO | SPO | Taken | Remaining |
| --- | --- | --- | --- | --- |
| 87 | `-` | `SPO-2026/01-0140` | 0 | 0 |

The PO cell is the muted dash, which is the case the brief anticipated: the book put this
quantity on the shipping order, so the PO column has nothing of its own to say. The SPO cell
carries the confirmed mark and one clickable number
(`backing-documents-trigger-spo-c61b8be7-...`), no coverage headline and no info icon - one
`svg` in the cell, which is the mark.

Clicking the number opened the lightbox (`02-so347594-cb2154-diy-lightbox.png`):

```
Backing documents
87 of 87
SPO   SPO-2026/01-0140
      from PO 202511-S0097
      BRW-BB - 87
      14/01/2026
      Confirmed
```

This is the whole lane in one screen. Before the repair the same row landed on the cancelled
August-extract ghost line and on SPO-2025/11-0075 (from PO 202509-S0078), 87 of 87 on
somebody else's containers.

## 3. The pill, and a row with nothing behind it

`SO349754` / `SRTWB7249`, six distinct shipping orders:

* the cell reads `SPO-2026/01-0091` then a `+5` pill, on one line, 44px tall;
* testids `backing-documents-trigger-spo-<row>` and `backing-documents-pill-spo-<row>`;
* clicking the PILL opened the same lightbox: `233 of 233` over SEVEN entries -
  `SPO-2026/01-0091` 56, `SPO-2026/01-0098` 20, `SPO-2026/01-0112` 11, `SPO-2026/01-0112` 65,
  `SPO-2026/02-0033` 46, `SPO-2026/04-0050` 22, `SPO-2026/04-0054` 13, every one of them
  `from PO 202510-S0121`.

Seven links, SIX distinct numbers, so the pill reads `+5`: `SPO-2026/01-0112` appears twice
(two containers of one document) and is one number in the cell. That is AC-R-27's rule
observed live on real data rather than in a fixture.

`SO358013` / `CB2828-DIY`, a row the cascade found nothing for: PO cell reads
`Not found (new order)`, SPO cell reads `-`, and neither cell holds a `button`, `a` or
`[role=button]` - nothing to click, as AC-R-29 requires.

## 4. One line, at 1280px and at 375px

Measured rather than eyeballed, on the unfiltered list (25 rows per page):

* **1280x800**: every row 44px. 0 rows with a coverage headline in either cell, 0 rows with
  an extra icon in the PO cell, 7 rows reading `Not found (new order)`, 7 rows with a dash in
  SPO, 16 with a dash in PO.
* **375x812** (`03-375px-po-spo-columns.png`): `document.scrollWidth == clientWidth == 375`,
  so the PAGE does not overflow; the grid's own container scrolls
  (`overflow-x-auto overscroll-x-contain`, clientWidth 341, scrollWidth 2,664). Rows stay
  44px. Scrolled to the PO/SPO columns: PO 150px wide reading `202511-S0082`, SPO 160px
  reading `SPO-2025/12-0069`, neither cell's text clipped (`scrollWidth <= clientWidth`).

## 5. Console, errors, network

`console` carried only i18next init, Fast Refresh and `JWT token extracted successfully`
debug lines - no error, no warning. `errors` was empty throughout. Every `/api/v1/` request
returned 200 except the deliberate `202` from `POST /api/v1/scm/order-inquiry/apply`. The
list is served by
`GET /api/v1/project-sales/order-inquiries?page=1&limit=25&sort=delivery_date&dir=asc`.

## Verdict per criterion

| AC | verdict | what was seen |
| --- | --- | --- |
| AC-R-26 | PASS | SO347594/CB2154-DIY: the SPO number is the only clickable thing in its cell, no pill, no headline, no info icon; the PO cell of that same row is the muted dash |
| AC-R-27 | PASS | SO349754/SRTWB7249: seven links, `SPO-2026/01-0112` twice, six distinct numbers, `+5` |
| AC-R-28 | PASS | the `+5` pill opened `backing-documents-<row>` listing all seven entries; the number trigger opened the same dialog on the CB2154-DIY row |
| AC-R-29 | PASS | SO358013/CB2828-DIY: `Not found (new order)` / `-`, zero clickable elements in either cell |
| AC-R-30 | not seen | no bundled row appeared in the pages walked; covered by vitest `AC-R-30` against the D1 fixture |
| AC-R-31 | PASS | header order `Supplier`, `PO`, `SPO`, `Taken by PO/SPO`; PO 150px and SPO 160px at 375px, text truncating rather than wrapping; every row 44px |

## One note for the reviewer, not a defect

The preview's warning names 75 cited documents it could not link, `202511-S0097` among them.
That is the citation path (source 3) reporting a document with no capacity LEFT after the
reference path had already spent it - the CB2154-DIY row proves the same purchase order was
followed correctly through `from_po_number` to its allocation. Worth a sentence in the PR so
nobody reads the warning as the pairing having missed it.

## Run notes

The session's Chromium wedged once (a pegged renderer after a burst of keyboard input in the
search box) and stopped answering the daemon. Killed ONLY that session's own process group -
its user-data-dir `agent-browser-chrome-ee7e85c6-...`, started at 15:11 today - and left the
other agent's `agent-browser-chrome-499864ba-...` (running since 12 Sep) untouched, then
signed in again and re-walked from the sidebar. `close --all` was never used.
