/**
 * Every place a DataGrid renders inside ANOTHER grid's body, named once.
 *
 * `DataGrid` handles this itself now - a grid that finds an enclosing
 * `DataGridContext` renders unbounded and non-sticky, so it cannot open a
 * scrollport inside a scrollport (`data-grid.nested.test.tsx` proves the
 * behaviour). This file is the other half: a CENSUS, so a new nesting site is a
 * deliberate entry rather than a surprise, and so the reasons stay written down.
 *
 * Why the census is worth having when the default already covers the sites: the
 * default reads REACT context, and two shapes escape it. A grid whose enclosing
 * table is one of the hand-rolled `<table>` carve-outs has no context to read
 * (`CellStockTable` is exactly that, and it is where the production defect was);
 * and a grid rendered through something that breaks the React tree rather than
 * only the DOM tree would too. Neither is caught by a component test, because
 * each is a property of a pair of files rather than of one.
 *
 * Three scans, matching the three shapes nesting takes:
 *
 *   1. `meta.expandedContent` - the row expansion. `DataGridTable` renders it
 *      inside the grid's own provider.
 *   2. a grid-bearing component rendered INSIDE the `<DataGrid>` / `<PanelDataGrid>`
 *      JSX of another - a dialog a list opens and keeps in its own subtree.
 *   3. a grid inside a popover / dialog / sheet. React context reaches a portalled
 *      child, so a popover opened from a CELL is nested even though its columns
 *      array puts it nowhere near the grid in the source.
 *
 * Both scans over-report on purpose (a component whose FILE renders a grid is
 * treated as grid-bearing). Every hit below was read by hand and carries what it
 * actually renders.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const ROOTS = ['app', 'components'];

/**
 * Every `meta.expandedContent` site, and what its expansion renders.
 *
 * Only the two `StockDocumentsPanel` entries are a grid inside a grid. The rest
 * expand into a form, a hand-rolled `<table>` carve-out or a list, and are here
 * so the next one to become a grid is visible as a diff on this file.
 *
 * `POIntakeLinesGrid.tsx` (LineAnnotationPanel) is retired as of the F2 owner
 * hand-test fix (25 Sep 2026, commit 7f64a3a6): the always-expanded note cards
 * became a compact Flag-cell popover instead, so the row no longer uses
 * `meta.expandedContent` / `getExpandedRowModel` at all. The popover holds no
 * grid, so it is not a new site for the floating-surface scan below either.
 */
const EXPANDED_CONTENT_SITES = new Map<string, string>([
  [
    'app/(protected)/procurement-management/packing-lists/components/SpoPlannerTable.tsx',
    'LocationSplitPanel - a form, no grid',
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/BoardCellBreakdownDialog.tsx',
    'BoardLineDecisionPanel - a decision form plus BoardLadderOptionsTable, a hand-rolled table',
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/FulfilmentBoardListView.tsx',
    'BoardLineDecisionPanel - same as above',
  ],
  [
    'app/(protected)/scm/components/PlanRowDialog.tsx',
    'NESTED GRID: StockDocumentsPanel (PanelDataGrid) inside OnHandTable rows. Covered by the context default AND by its own scrollerMaxHeight={false}',
  ],
  [
    'app/(protected)/scm/reorder/components/PlanRowDialogs.tsx',
    'NESTED GRID: StockDocumentsPanel (PanelDataGrid) inside this file-local OnHandTable. Same cover as above',
  ],
  [
    'app/(protected)/scm/components/ProductPerspectiveGrid.tsx',
    'DemandTrendLine (a chart) plus WarehouseBreakdownTable, a hand-rolled table',
  ],
  [
    'app/(protected)/scm/reorder/components/PlanLinesGrid.tsx',
    'PlanRowPanel - a decision form and a chart, no grid',
  ],
  [
    'app/(protected)/system-management/health/components/HealthDashboard.tsx',
    'IntegrationFailuresList - a <ul> of links, no grid',
  ],
  [
    'app/(protected)/project-sales/_shared/components/LinkDocumentDialog.tsx',
    'CandidateExpandPanel - a definition-list of fields plus a hand-rolled <table> of other rows already linked on the line, no grid',
  ],
  [
    'app/(protected)/project-sales/order-inquiries/[id]/components/orderInquiryHeaderLinesColumns.tsx',
    'NESTED GRID: OrderInquiryStockGrid, a thin wrapper over CellStockTable (the hand-rolled <table> carve-out) whose rows open StockDocumentsPanel (PanelDataGrid). The expansion sits inside the OI detail lines grid\'s own provider, so the context default covers it, AND StockDocumentsPanel passes scrollerMaxHeight={false} itself (S3, PLAN-oi-request-cs-reserve.md, stock grid on the OI page)',
  ],
  [
    'app/(protected)/master-data-management/products/[id]/components/ProductSuppliersTab.tsx',
    'NESTED GRID: SupplierCostPrices (PanelDataGrid) under a supplier row, #1305 round 9. Covered by the context default (it names no scrollerMaxHeight of its own)',
  ],
  // The primitives themselves: the prop declaration, the renderer, and the
  // wrapper that forwards `expanded`. Not nesting sites.
  ['components/ui/data-grid.tsx', 'declares the ColumnMeta field'],
  [
    'components/ui/data-grid-table.tsx',
    'renders it (DataGridTableBodyRowExpandded)',
  ],
  ['components/common/PanelDataGrid.tsx', 'forwards expanded/onExpandedChange'],
  [
    'components/ui/data-grid-table-dnd.tsx',
    'AC-RS-69 (PLAN-oi-request-cs-reserve.md section 6d G3): reads meta.expandedContent to skip the drag grip on an expanded-row column - does not render the expansion itself, not a nesting site',
  ],
]);

/**
 * A grid-bearing component rendered inside another grid's JSX - the dialog or
 * popover a cell opens. Two of the five are real nesting; the other three are a
 * list's own table split into its own file, which renders `<DataGridTable />`
 * inside the PARENT's provider and is therefore the same grid, not a second one.
 */
const IN_GRID_SUBTREE_SITES = new Map<string, string>([
  [
    'app/(protected)/complaint-management/complaint-resolutions/components/ComplaintResolutionsList.tsx:232',
    'ComplaintResolutionTable is a bare <DataGridTable /> inside the PARENT provider, not a second grid',
  ],
  [
    'app/(protected)/complaint-management/complaint-root-causes/components/ComplaintRootCausesList.tsx:234',
    'ComplaintRootCauseTable, same shape as above',
  ],
  [
    'app/(protected)/master-data-management/lookup-sets/components/LookupSetsList.tsx:208',
    'LookupSetTable, same shape as above',
  ],
  [
    'app/(protected)/marketing-management/promotions/components/PromotionsList.tsx:596',
    'NESTED GRID: AttachmentDetailModal opens inside the promotions grid and holds a PanelDataGrid of linkages. Covered by the context default AND its own scrollerMaxHeight={false} (SF-1)',
  ],
  [
    'app/(protected)/scm/reorder/components/PlanLinesGrid.tsx:1333',
    'NESTED GRID: PlanRowDialog opens inside the plan grid and holds DrillTable/OnHandTable/StockDocumentsPanel. Every one already passes scrollerMaxHeight={false}; the context default now agrees with them',
  ],
]);

/**
 * A grid inside a floating surface - a popover, hover card, dialog, sheet, drawer.
 *
 * Its own scan, because scans 1 and 2 both miss it: a column's `cell` renderer is
 * declared in a module-level `ColumnDef` array, OUTSIDE the `<DataGrid>` JSX, so a
 * popover opened from a cell is nowhere near the grid in the SOURCE even though it
 * is inside it in the React tree. That is the shape the entry marked NESTED GRID
 * below has, and it is why this is not folded into scan 2.
 *
 * Everything else here opens as a SIBLING of a list rather than from inside one -
 * no grid context, and each already turns its own bound off because it sits in a
 * dialog or sheet body that scrolls.
 */
const GRID_IN_FLOATING_SURFACE_SITES = new Map<string, string>([
  [
    'app/(protected)/complaint-management/complaints/components/ComplaintFulfilmentOrdersSection.tsx:91',
    'NESTED GRID: the items popover opens from a CELL of this section\'s own grid. It names scrollerMaxHeight="16rem", so the nested default leaves it alone and it keeps its sticky header inside that window',
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/BoardCellBreakdownDialog.tsx:1294',
    'Contributing lines, in a dialog opened as a sibling of the board (a hand-rolled matrix). Already scrollerMaxHeight={false}. (Line moved 1223 -> 1251 when the Decision column gained the Verdict actions and the Product column its chip; 1251 -> 1268 when PLAN-oi-decision-trail-ui.md round 2 added a DecisionTrailButton after the verdict chip in the same cell renderer; 1268 -> 1293 when #1362 round 2 gave a fulfilled line its own card in place of the Suggestion card; 1293 -> 1294 when #1362 round 4 imported soLineLabel; the census is keyed by line, so a site that moves is a one-line diff here rather than a silent pass)',
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/FulfilmentPlanningSheet.tsx:457',
    'Reconciliation lines in a sheet body. Already scrollerMaxHeight={false}',
  ],
  [
    'app/(protected)/project-sales/fulfilment-planning/components/PileQueueDialog.tsx:289',
    'Pile queue in a dialog body. Already scrollerMaxHeight={false}',
  ],
  [
    'app/(protected)/project-sales/order-inquiries/components/OrderInquiryMatrixCellDrilldown.tsx:97',
    'Drilldown dialog, rendered BEFORE the list grid in OrderInquiriesClient, not inside it. Already scrollerMaxHeight={false}. (Line moved 57 -> 97 when the cell drilldown gained its `axis`/`axis_key` filters; the census is keyed by line, so a site that moves is a one-line diff here rather than a silent pass)',
  ],
  [
    'app/(protected)/project-sales/stock-debt/components/StockDebtCellDialog.tsx:684',
    "Demand tab, dialog rendered after the calendar grid closes. Already scrollerMaxHeight={false}. (Line moved 635 -> 588 when R31a folded the R30 SPO/OI columns back into one Covered-by column, still rendering the OI screens' own `OrderInquiryDocumentLink`; 588 -> 636 when R42 linked a covering PO in Covered by and gave the Supply tab its PO-line columns.)",
  ],
  [
    'app/(protected)/project-sales/stock-debt/components/StockDebtCellDialog.tsx:715',
    'Supply tab, same dialog. (Line moved 666 -> 619, same round; 619 -> 667 in R42, same as above.)',
  ],
  [
    'app/(protected)/scm/proforma-invoices/components/ConvertToPackingListDialog.tsx:455',
    "What goes in the container, in a dialog opened as a sibling of the invoice list or the invoice's own detail - no grid context. It KEEPS the default `--grid-max-h` bound rather than turning it off: the dialog body does not scroll, so the table is what scrolls, with its header sticking inside that window while the container size select and Convert stay put",
  ],
  [
    'app/(protected)/scm/proforma-invoices/components/ConvertToPackingListDialog.tsx:468',
    'The DataGridTable of the same grid, not a second one',
  ],
  [
    'app/(protected)/system-management/import-jobs/autocount-pull/components/CompareMappingDialog.tsx:275',
    'Compare mapping rows, in a dialog opened from the Compare tab\'s Mapping button outside any grid\'s JSX, so no grid context. It KEEPS the default bound inside CardTable max-h-[50dvh], like ConvertToPackingListDialog',
  ],
  [
    'app/(protected)/system-management/import-jobs/autocount-pull/components/CompareMappingDialog.tsx:284',
    'The DataGridTable of the same grid, not a second one',
  ],
  [
    'app/(protected)/project-sales/_shared/components/LinkDocumentDialog.tsx:343',
    "S8's candidate DataGrid, in a dialog opened as a sibling of the worklist. Already scrollerMaxHeight={false}",
  ],
  [
    'app/(protected)/project-sales/_shared/components/LinkDocumentDialog.tsx:349',
    'The DataGridTable of the same grid, not a second one',
  ],
  [
    'app/(protected)/project-sales/order-inquiries/[id]/components/ReserveRequestDialog.tsx:257',
    "G6 (PLAN-oi-request-cs-reserve.md section 6d): one row per selected line, in a dialog opened as a sibling of the OI Lines tab (Request CS to reserve). Already scrollerMaxHeight={false}. Round 4 review (24 Sep, AC-RS-89): line moved - the inert outer overflow-x-auto wrapper is gone and columnsDraggable: false was added.",
  ],
  [
    'app/(protected)/project-sales/order-inquiries/[id]/components/ReserveRequestDialog.tsx:269',
    'The DataGridTable of the same grid, not a second one',
  ],
]);

/**
 * Nesting the React tree cannot see: the enclosing table is a hand-rolled
 * `<table>` carve-out, so the inner grid finds no `DataGridContext` and the
 * default cannot fire. Each of these MUST turn its own bound off by hand.
 *
 * This is where the production defect lived, so the entry names the file that has
 * to carry the prop, and `data-grid-scroller.inventory.test.ts` is what checks it
 * is still there.
 */
const CONTEXT_BLIND_SITES = new Map<string, string>([
  [
    'app/(protected)/project-sales/fulfilment-planning/components/CellStockTable.tsx',
    'StockDocumentsPanel opens in a row of this hand-rolled <table> (the carve-out FulfilmentBoardMatrix documents), so there is no grid context. StockDocumentsPanel.tsx passes scrollerMaxHeight={false} itself. Also mounted from BorrowAddDialog (the Borrow modal\'s Source table) since local-supplier-oi-routing - same hand-rolled table, same StockDocumentsPanel expansion, no new site to add here',
  ],
]);

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === '.next') continue;
        walk(full);
      } else if (/\.tsx$/.test(entry.name) && !entry.name.includes('.test.')) {
        // `.tsx` only. A `.ts` file declares types and constants with the same
        // Capitalised shape as a component, and letting those into the ownership
        // map turns the transitive walk below into a walk over most of the app.
        out.push(full);
      }
    }
  };
  ROOTS.forEach(walk);
  return out;
}

/**
 * Where a tag's component comes from, resolved through this file's OWN imports.
 *
 * Deliberately not a name-to-file index built over the whole app: names repeat
 * (`Toolbar`, `Row`, `Table`), so an index picks whichever file it walked first
 * and the answer changes with directory order. An import statement says exactly
 * which module the tag on this line refers to.
 */
function importedFrom(src: string, file: string): Map<string, string> {
  const out = new Map<string, string>();
  const dir = path.dirname(file);
  for (const [, clause, spec] of src.matchAll(
    /import\s+([^;]*?)\s+from\s+'([^']+)'/g,
  )) {
    const target = spec.startsWith('@/')
      ? spec.slice(2)
      : spec.startsWith('.')
        ? path.normalize(path.join(dir, spec))
        : null;
    if (!target) continue; // a package, never one of ours
    for (const [, name] of clause.matchAll(/\b([A-Z]\w*)\b/g))
      out.set(name, target);
  }
  return out;
}

/** `a/b/Foo` -> the real file, trying the extensions and the index form. */
function resolveModule(target: string): string | null {
  for (const candidate of [
    `${target}.tsx`,
    `${target}.ts`,
    `${target}/index.tsx`,
  ]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

const GRID_TAG = /<(DataGrid|PanelDataGrid|DataGridTable)\b/;
/** Tags that are part of a grid rather than a second one nested in it. */
const GRID_PARTS = new Set([
  'DataGrid',
  'PanelDataGrid',
  'DataGridTable',
  'DataGridPagination',
  'DataGridColumnHeader',
  'DataGridContainer',
  'DataGridListToolbar',
]);

/** The line ranges that sit inside a `<DataGrid>` / `<PanelDataGrid>` element. */
function gridSubtreeRanges(lines: string[]): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let depth = 0;
  let start: number | null = null;
  lines.forEach((line, index) => {
    const lineNo = index + 1;
    const selfClosing = (
      line.match(/<(?:DataGrid|PanelDataGrid)\b[^>]*\/>/g) ?? []
    ).length;
    const opens =
      (line.match(/<(?:DataGrid|PanelDataGrid)\b(?![\w])/g) ?? []).length -
      selfClosing;
    const closes = (line.match(/<\/(?:DataGrid|PanelDataGrid)>/g) ?? []).length;
    if (opens > 0 && depth === 0) start = lineNo;
    depth += opens - closes;
    if (depth <= 0 && start !== null) {
      out.push([start, lineNo]);
      start = null;
      depth = 0;
    }
  });
  return out;
}

describe('nested DataGrid census', () => {
  const files = sourceFiles();
  const src = new Map(files.map((f) => [f, fs.readFileSync(f, 'utf8')]));

  it('every meta.expandedContent site is named, with what its expansion renders', () => {
    const found = files
      .filter((f) => /expandedContent/.test(src.get(f)!))
      .sort();
    expect(found).toEqual([...EXPANDED_CONTENT_SITES.keys()].sort());
  });

  it('every grid-bearing component rendered inside another grid is named', () => {
    // "Grid-bearing" is one hop deep on purpose: a component whose own file renders
    // a grid. A dialog that wraps another component that renders one is caught by
    // the second component's own entry, and going deeper turns the scan into a walk
    // over most of the app for no extra finding.
    const rendersGrid = (file: string) => {
      const text =
        src.get(file) ??
        (fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '');
      return GRID_TAG.test(text);
    };

    const found = new Set<string>();
    for (const [file, text] of src) {
      const lines = text.split('\n');
      const ranges = gridSubtreeRanges(lines);
      if (ranges.length === 0) continue;
      const imports = importedFrom(text, file);
      const declaredHere = new Set(
        [
          ...text.matchAll(
            /^\s*(?:export\s+)?(?:default\s+)?function\s+([A-Z]\w*)/gm,
          ),
          ...text.matchAll(/^\s*(?:export\s+)?const\s+([A-Z]\w*)\s*[:=]/gm),
        ].map((m) => m[1]),
      );
      for (const [from, to] of ranges) {
        for (let i = from; i <= to; i += 1) {
          for (const [, name] of lines[i - 1].matchAll(/<([A-Z]\w*)\b/g)) {
            if (GRID_PARTS.has(name)) continue;
            // Declared in this same file: it is part of THIS list, not a nested one.
            if (declaredHere.has(name) && !imports.has(name)) continue;
            const target = imports.get(name);
            const resolved = target ? resolveModule(target) : null;
            if (resolved && rendersGrid(resolved)) found.add(`${file}:${i}`);
          }
        }
      }
    }
    expect([...found].sort()).toEqual([...IN_GRID_SUBTREE_SITES.keys()].sort());
  });

  it('every grid inside a popover, dialog, sheet or drawer is named', () => {
    const SURFACE =
      '(PopoverContent|HoverCardContent|DialogContent|SheetContent|TooltipContent|DrawerContent)';
    const found = new Set<string>();
    for (const [file, text] of src) {
      const lines = text.split('\n');
      let depth = 0;
      let start: number | null = null;
      lines.forEach((line, index) => {
        const lineNo = index + 1;
        const selfClosing = (
          line.match(new RegExp(`<${SURFACE}\\b[^>]*/>`, 'g')) ?? []
        ).length;
        const opens =
          (line.match(new RegExp(`<${SURFACE}\\b(?![\\w])`, 'g')) ?? [])
            .length - selfClosing;
        const closes = (line.match(new RegExp(`</${SURFACE}>`, 'g')) ?? [])
          .length;
        if (opens > 0 && depth === 0) start = lineNo;
        depth += opens - closes;
        if (depth <= 0 && start !== null) {
          for (let i = start; i <= lineNo; i += 1) {
            if (GRID_TAG.test(lines[i - 1])) found.add(`${file}:${i}`);
          }
          start = null;
          depth = 0;
        }
      });
    }
    expect([...found].sort()).toEqual(
      [...GRID_IN_FLOATING_SURFACE_SITES.keys()].sort(),
    );
  });

  it('a grid nested under a hand-rolled table turns its own bound off, since context cannot reach it', () => {
    for (const file of CONTEXT_BLIND_SITES.keys()) {
      expect(fs.existsSync(file), file).toBe(true);
    }
    // The one site today: `CellStockTable` opens `StockDocumentsPanel`, and the
    // panel is what carries the prop.
    const panel = fs.readFileSync(
      'app/(protected)/project-sales/fulfilment-planning/components/StockDocumentsPanel.tsx',
      'utf8',
    );
    expect(panel).toMatch(/scrollerMaxHeight=\{false\}/);
  });

  it('every enumerated file still exists', () => {
    for (const key of [
      ...EXPANDED_CONTENT_SITES.keys(),
      ...[...IN_GRID_SUBTREE_SITES.keys()].map((k) => k.replace(/:\d+$/, '')),
      ...[...GRID_IN_FLOATING_SURFACE_SITES.keys()].map((k) =>
        k.replace(/:\d+$/, ''),
      ),
      ...CONTEXT_BLIND_SITES.keys(),
    ]) {
      expect(fs.existsSync(key), key).toBe(true);
    }
  });
});
