/**
 * S1-05 (repaired in S5) - the grid owns the horizontal scrollport, alone.
 *
 * S1 gave `DataGridTable` its own `overflow-x-auto` scroller and a definite
 * `min-width` on the table. It still did not scroll: 161 lists wrapped the grid
 * in a Radix `<ScrollArea>`, whose viewport puts a `display: table` element
 * between the scroller and the table. That ancestor shrink-fits, so
 * `data-grid-scroller` measured `scrollWidth === clientWidth` (2178 === 2178 on
 * Orders at 1280), never overflowed, and yet still ate the wheel gesture
 * through `overscroll-x-contain`. The only element that COULD scroll was the
 * Radix viewport, and it never received the event. Net effect: no list scrolled
 * sideways, the right-edge fade never appeared, and the phone pin had nothing
 * to pin against.
 *
 * This is a source scan and not a render test for the same reason
 * `PageHeader.inventory.test.ts` is: what it asserts is a property of the whole
 * tree ("no list re-introduces a second scrollport around the grid"), and a
 * render test can only speak for the one page it mounted. The 162nd
 * copy-pasted `<ScrollArea><DataGridTable /></ScrollArea>` would pass every
 * component test in the repo and fail here, which is the point.
 *
 * If you are adding a list: render `<DataGridTable />` bare inside `CardTable`.
 * The grid brings its own scroller.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

/** Roots scanned: everything a list can live in. */
const ROOTS = ['app', 'components'];

/**
 * The files allowed to put a `ScrollArea` around a grid, each for a reason the
 * scan must not erase.
 */
const EXEMPT = new Map<string, string>([
  // A bounded-height viewport: capped at `max-h-[calc(100vh-14rem)]` so the
  // page keeps its own scroll. That is VERTICAL work the grid does not do,
  // and the cap makes the wrapper a real scrollport rather than a
  // shrink-fitting one.
  //
  // `POIntakeLinesGrid.tsx` used to be here too, wrapping `DataGridTable` in
  // a Radix `ScrollArea` for the same vertical-cap reason. That wrapper was
  // the S1-05 anti-pattern itself: the `display: table` viewport it gave the
  // table shrink-fit horizontally, so `data-grid-scroller` never measured an
  // overflow and the Amount column clipped with no way to reach it (owner
  // hand test, 25 Sep 2026, item 1). It now passes the same vertical budget
  // through `tableLayout.scrollerMaxHeight` as a plain string instead, which
  // is what `DataGridScroller` already reads a caller-supplied height off,
  // so it needs no exemption here any more.
  //
  // `POIntakeAnnotationsGrid.tsx` was here for the same reason too, and is
  // gone for a different one: owner hand test 25 Sep 2026, item 4 removed
  // the grid it wrapped entirely (the Documents tab shows only the PDF now),
  // so the file carries no `DataGridTable` and no `ScrollArea` any more.
  // Same: the panel is embedded in a complaint record and takes a caller-set
  // `maxHeightClassName`.
  [
    'app/(protected)/complaint-management/_shared/LinkedComplaintsPanel.tsx',
    'bounded-height viewport (maxHeightClassName)',
  ],
  // Not a `DataGridTable` at all: `DriveListView` composes `DataGridTableBase`
  // itself and therefore never gets the grid's scroller. Its `ScrollArea` IS
  // the scrollport, so removing it would leave the drive with none.
  [
    'app/(protected)/resource-management/attachment-directories/components/AttachmentsInFolderPanel.tsx',
    'DriveListView composes DataGridTableBase, so it has no grid scroller of its own',
  ],
]);

/** Every `.tsx` under the scanned roots, tests excluded. */
function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === '.next') continue;
        walk(full);
      } else if (
        entry.name.endsWith('.tsx') &&
        !entry.name.includes('.test.')
      ) {
        out.push(full);
      }
    }
  };
  for (const root of ROOTS) walk(root);
  return out;
}

/**
 * A `ScrollArea` open tag through to its close, non-greedy, so a file with
 * several of them is read one at a time.
 */
const SCROLL_AREA_BLOCK = /<ScrollArea(?:\s[^>]*?)?>([\s\S]*?)<\/ScrollArea>/g;

/**
 * The grid components that bring the scroller with them. `DataGridTableBase`
 * is deliberately absent: it is the bare `<table>`, and a caller composing it
 * has to supply its own scrollport.
 */
const SCROLLER_OWNERS = /<DataGridTable[\s/>]/;

describe('The grid is the only horizontal scrollport (S1-05)', () => {
  it('no list wraps DataGridTable in a ScrollArea', () => {
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      const src = fs.readFileSync(file, 'utf8');
      if (!src.includes('<ScrollArea')) continue;
      SCROLL_AREA_BLOCK.lastIndex = 0;
      for (const match of src.matchAll(SCROLL_AREA_BLOCK)) {
        if (!SCROLLER_OWNERS.test(match[1])) continue;
        if (EXEMPT.has(file)) continue;
        offenders.push(file);
      }
    }
    expect([...new Set(offenders)]).toEqual([]);
  });

  it('every exemption still exists, so a stale reason cannot hide a new offender', () => {
    const missing = [...EXEMPT.keys()].filter((file) => !fs.existsSync(file));
    expect(missing).toEqual([]);
  });

  it('every exemption still needs its wrapper', () => {
    // A file that lost its `ScrollArea` has to lose its exemption too,
    // otherwise the next list that copies the file's name inherits a pass.
    const stale = [...EXEMPT.keys()].filter(
      (file) => !fs.readFileSync(file, 'utf8').includes('<ScrollArea'),
    );
    expect(stale).toEqual([]);
  });
});

/**
 * B2 (M5 review run 1) - a grid inside a `DialogBody`/`SheetBody` that already owns the
 * scroll viewport opts OUT of M5-05's own bounded scroller with
 * `tableLayout.scrollerMaxHeight: false`, or that ancestor's `overflow-y-auto` nests a
 * second scrollport inside the grid's default one. Several call sites reach the literal
 * through a shared shell's own pass-through prop rather than writing it inline -
 * `PanelDataGrid`'s `scrollerMaxHeight` prop, and the `scrollerMaxHeight` prop on the
 * exported `DrillTable` in `scm/components/PlanRowDialog.tsx` and the file-local one in
 * `scm/reorder/components/PlanRowDialogs.tsx` - so the CALLER carries the literal, not
 * always the grid's own definition.
 *
 * Enumerated by file with the count expected right now, the same shape `EXEMPT` above
 * uses for the ScrollArea rule: a file that gains or loses an occurrence without a
 * matching edit here fails loudly instead of drifting silently. `PoPlanCard.tsx` is the
 * one deliberate absence - it renders the exported `DrillTable` directly on a plain page
 * card, not inside a dialog, so it keeps the bounded default and must stay off this list.
 */
const SCROLLER_MAX_HEIGHT_FALSE_SITES = new Map<string, number>([
  [
    'app/(protected)/complaint-management/_shared/LinkedComplaintsPanel.tsx',
    1, // the original site this rule copies - its own maxHeightClassName ScrollArea
  ],
  [
    'app/(protected)/project-sales/_shared/components/RequestReserveDialog.tsx',
    // G6 (PLAN-oi-request-cs-reserve.md section 6d): one DataGrid, one row per
    // selected line, inside its own DialogBody (overflow-y-auto).
    1,
  ],
  [
    'app/(protected)/project-sales/order-inquiries/[id]/components/ReserveConfirmDialog.tsx',
    // OI-STAGED-NOTIFY: the staged lines of the Reserve confirmation, inside its own
    // DialogBody (overflow-y-auto).
    1,
  ],
  [
    'app/(protected)/project-sales/order-inquiries/[id]/components/OrderInquiryLineHistoryDialog.tsx',
    // PLAN-oi-no-double-count-25sep.md S0 (G2, G3): the History dialog's Rows tab, one
    // DataGrid of the line's rows inside its own DialogBody (overflow-y-auto); the grid's
    // own scroller carries the sideways scroll at 375.
    1,
  ],
  [
    'app/(protected)/project-sales/order-inquiries/[id]/components/orderInquiryHeaderLinesColumns.tsx',
    // OI-LINE-SPLIT (UAC D3): a split line's splits, a few rows in the Lines tab's own
    // expanded row; the outer Lines grid owns the vertical scroll.
    1,
  ],
  [
    'app/(protected)/project-sales/order-inquiries/components/OrderInquiryDocumentDialog.tsx',
    2, // two PanelDataGrid bodies (PO, SPO) inside one DialogBody that already owns the scroll
  ],
  [
    'app/(protected)/project-sales/order-inquiries/components/OrderInquiryMatrixCellDrilldown.tsx',
    1, // PanelDataGrid inside its DialogBody
  ],
  [
    'app/(protected)/project-sales/stock-debt/components/StockDebtCellDialog.tsx',
    2, // two PanelDataGrid tabs (Demand, Supply) inside one DialogBody
  ],
  [
    'app/(protected)/project-sales/stock-debt/components/SnapshotDrillDialog.tsx',
    1, // the per-OI PanelDataGrid inside the drill's DialogBody
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/BoardCellBreakdownDialog.tsx',
    1, // PanelDataGrid inside its DialogBody
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/FulfilmentPlanningSheet.tsx',
    1, // PanelDataGrid inside its SheetBody
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/PileQueueDialog.tsx',
    1, // PanelDataGrid inside its DialogBody
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/StockDocumentsPanel.tsx',
    // Opens in three places, all of them a dialog body that already scrolls. Stated at the
    // call site rather than left to `DataGrid`'s nested-grid default, because on the
    // fulfilment board's cell breakdown the enclosing table is `CellStockTable`'s hand-rolled
    // `<table>` carve-out, not a DataGrid - so there is no grid context there to read.
    1,
  ],
  [
    'app/(protected)/resource-management/attachments/components/AttachmentDetailModal.tsx',
    1, // PanelDataGrid inside DialogContent's own scrolling body (SF-1, M5 run 3 review)
  ],
  [
    'app/(protected)/scm/components/PlanRowDialog.tsx',
    // 2 direct DataGrid instances (OnHandTable, PoTakesPicker) + 7 calls to this file's
    // own exported DrillTable (ProjectRetailTabs x2, SpoTabs x2, IncomingPlTable x1,
    // PoTabs x2) + `SoCoveragePicker`'s own 2 (R18, purchasing consolidation batch: the
    // "SO covered" picker split into a project/retail tab pair, same shape
    // `ProjectRetailTabs` already uses, so its one grid became two) - every one is
    // inside PlanRowDialog's own DialogBody.
    11,
  ],
  [
    'app/(protected)/scm/reorder/components/PlanRowDialogs.tsx',
    // This file's own local DrillTable (never imported from the file above - see this
    // file's own module doc) + its own OnHandTable, both only ever rendered inside
    // PlanRowDialog's DialogBody.
    2,
  ],
  [
    'app/(protected)/scm/purchase-orders/[id]/components/PoLinePlacementsBody.tsx',
    1, // DrillTable, always opened inside PlanRowDialog's DialogBody, never on a plain page
  ],
  [
    'app/(protected)/scm/sales-orders/[id]/components/SoLineLinksBody.tsx',
    1, // DrillTable, same as PoLinePlacementsBody
  ],
  [
    'app/(protected)/scm/sales-orders/[id]/components/SoLineHistoryBody.tsx',
    2, // the delivered-DOs DrillTable and the Cancel SO DrillTable, same as SoLineLinksBody
  ],
  [
    'app/(protected)/scm/loading-plan/components/ContainerRequestSection.tsx',
    1, // BlocksTable's DrillTable, only rendered inside PlanRowDialog's DialogBody
  ],
  [
    'app/(protected)/sla-management/sla-policies/components/SLAPolicyTiersTable.tsx',
    1, // TierUsersSheetContent's PanelDataGrid inside the tier users Sheet body
  ],
  [
    'app/(protected)/project-sales/_shared/components/LinkDocumentDialog.tsx',
    1, // S8's DataGrid candidate table inside the dialog's own DialogBody
  ],
]);

const SCROLLER_MAX_HEIGHT_FALSE =
  /scrollerMaxHeight:\s*false|scrollerMaxHeight=\{false\}/g;

describe('A grid inside a Dialog/Sheet body opts out of the bounded scroller (B2, M5 review run 1)', () => {
  it('every scrollerMaxHeight: false site is enumerated with a reason', () => {
    const found = new Map<string, number>();
    for (const file of sourceFiles()) {
      const src = fs.readFileSync(file, 'utf8');
      const matches = src.match(SCROLLER_MAX_HEIGHT_FALSE);
      if (matches) found.set(file, matches.length);
    }
    expect(Object.fromEntries(found)).toEqual(
      Object.fromEntries(SCROLLER_MAX_HEIGHT_FALSE_SITES),
    );
  });

  it('every enumerated file still exists', () => {
    const missing = [...SCROLLER_MAX_HEIGHT_FALSE_SITES.keys()].filter(
      (file) => !fs.existsSync(file),
    );
    expect(missing).toEqual([]);
  });

  it('PoPlanCard keeps the bounded default - the one plain-page caller of DrillTable', () => {
    const src = fs.readFileSync(
      'app/(protected)/scm/purchase-orders/[id]/components/PoPlanCard.tsx',
      'utf8',
    );
    expect(src).not.toMatch(SCROLLER_MAX_HEIGHT_FALSE);
  });
});
