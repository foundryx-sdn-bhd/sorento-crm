'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import {
  CellContext,
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { useRouter } from 'next/navigation';
import { LayoutGrid, PackageSearch, RefreshCw, Table2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import { EM_DASH, fmtInt } from '../../lib/format';
import { LiveNumberInput } from '@/components/ui/number-input';
import { describeWindow } from '../../reorder/lib/runListing';
import { useContainerRequestBuild, useContainerRequestHistory } from '../../hooks/useFulfilment';
import type {
  ContainerRequestHistoryProduct,
  ContainerRequestHoldingBlock,
  ContainerRequestRow,
  ContainerRequestSoLine,
} from '../../services/fulfilmentService';
import {
  DrillTable,
  IncomingPlTable,
  OnHandTable,
  PlanRowDialog,
  PoTabs,
  ProjectRetailTabs,
  RIGHT,
  SpoTabs,
  TOTAL_LABEL,
  type PlanDemandLineRow,
  type PlanHistoryPoint,
  type PlanRowDialogKind,
} from '../../components/PlanRowDialog';
import { PlanNumberButton } from '../../components/PlanNumberButton';
import { FormulaTip } from './FormulaTip';
import { ContainerRequestRowDialog } from './ContainerRequestRowDialog';
import { ContainerRequestStatCards } from './ContainerRequestStatCards';
import { summariseContainerRequest } from './containerRequestSummary';
import { RankFactorsPopover } from './RankFactorsPopover';
import { ContainerRequestScheduleMatrix } from './ContainerRequestScheduleMatrix';
import {
  buildContainerRequestMatrix,
  type ContainerRequestMatrixAxis,
  type ContainerRequestMatrixGranularity,
} from './containerRequestMatrix';
import { distinctName } from '@/lib/product-display';

/**
 * Stage 1 of Ms Tee's journey (PLAN-scm-loading-plan-demand-first.md, amended section 4/6b -
 * 20 Aug afternoon): what to ask this supplier for, before any container is chosen. ONE
 * table over the whole stock list - a stock-list product with no open demand still gets a row
 * (`has_demand: false`, muted, unranked), so nothing the stock list holds vanishes into a
 * second table. The product set and the quantities are both derived - off the supplier's
 * stock list and the outstanding sales-order book - so the only three decisions left to her
 * are the supplier (picked above this section), any quantity she disagrees with, and Send.
 *
 * `planHorizonDate` ("Plan until", captain 20 Aug follow-up): an optional cutoff picked next
 * to the supplier, above this section. When set, every demand-derived figure the build
 * returns already excludes what is due after it (backend-side - `container_request_service`
 * mirrors the reorder run's own horizon rule exactly); this component only has to show what
 * was actually applied, which it reads off the build's own echo (`build.data.plan_horizon_date`)
 * rather than the prop, so a stale prop during a refetch can never disagree with the numbers
 * on screen.
 *
 * Also folds in "waiting on production" (captain follow-up, same day): that used to be a
 * second list below this table. A supplier-held unfinished quantity is already on every
 * matched row here as the "They hold" cell (sortable, see the `holding` column), so the
 * second list is gone. The stock-list item codes that matched no product at all had a small
 * block of their own here until 27 Aug (R23); it went, because the unmatched-code queue above
 * this section now lists exactly those codes and lets somebody answer them, and two lists of
 * one thing is one list nobody works down.
 *
 * The header's occasional actions sit behind a gear (R23), for the reason the toolbar's own
 * gear exists: Send is the errand, and a row of equally-weighted outline buttons made the
 * rare things look as important as the routine one.
 *
 * S2 (2 Sep): this renders on the record's Lines tab only. The "Requests sent to X" card
 * that used to sit under this grid as an inline `noticesCard` left for its own Sent tab
 * (`SentRequestsPanel.tsx`); `LoadingPlanView` owns the tab strip and both panels.
 *
 * S5 (2 Sep) put a `has_demand: false` row under a collapsed "N products held with no open
 * demand" line, in a second grid of its own. Retired 15 Sep on the owner's call: the 18 rows
 * behind that line got overlooked. ONE grid over every candidate again - a no-demand row
 * stays muted and rankless, and sorts after every ranked row under the default (unsorted)
 * order, which `displayRows` pre-orders for rather than seeding a sort the grid would have to
 * carry around.
 */

const MATRIX_AXIS_OPTIONS = [
  { value: 'product', label: 'Product' },
  { value: 'order', label: 'Order' },
];

const MATRIX_GRANULARITY_OPTIONS = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
];

/** A source older than this reads as "trust this less" rather than a hard error - the figures
 *  built off it are still real, just possibly overtaken by an upload nobody has run yet. */

/**
 * What "Packed" sorts by: the one quantity the column shows (captain, 27 Aug: the unfinished
 * half left the grid; it still reads in the row dialog). A row neither document names sorts
 * below every row that carries a figure, rather than tying with a real zero.
 */
export function holdingSortValue(row: ContainerRequestRow): number {
  return row.holding_qty ?? -1;
}

/**
 * The netting rule with this row's own numbers in it (F2): what `engine_qty` IS, on the cell
 * that shows it - "= <engine_qty>" is the last term in the string, on the Suggested qty cell,
 * never on the Requested qty input beside it (review round, S3: the hover explains the ENGINE
 * figure, and only the read-only cell shows that figure).
 */
function engineQtyFormulaTitle(row: ContainerRequestRow): string {
  // R6: the fourth term, mocked off `incoming_pl` until Phase 2 sends the real figure (the
  // part of it not yet turned into an SPO).
  const incomingPlUnallocated = row.incoming_pl_unallocated ?? row.incoming_pl;
  return `${fmtInt(row.open_so_need)} need - ${fmtInt(row.on_hand)} on hand - ${fmtInt(row.incoming_spo)} incoming SPO - ${fmtInt(incomingPlUnallocated)} incoming PL (not yet on an SPO) = ${fmtInt(row.engine_qty)}`;
}

/**
 * Whether the Product cell's subtitle says something the code does not (AC-N1). Most of this
 * supplier's rows carry a `product_name` equal to their own `item_code`, so printing it again
 * underneath reads as a defect, not a fact - same rule as the order-inquiry worklist
 * (`orderInquiryWorklistColumns.tsx`), trimmed and case-insensitive because a name and a code
 * that differ only by spacing or case are still the same word to a reader scanning the grid.
 */
export function productNameDiffersFromCode(row: ContainerRequestRow): boolean {
  return distinctName(row.item_code, row.product_name) !== null;
}

/**
 * What the supplier says they hold, in the words of whichever document said it (F1).
 *
 * A stock list states two quantities and they are never summed - packed can go on a
 * container this week, unfinished is a request to their production line. A proforma states
 * one, for one container, and carries a badge saying so, because a promise for a container is
 * not the same fact as stock in their warehouse. Neither reads as a dash rather than a zero:
 * "they have told us nothing" and "they told us they have none" are different answers.
 */
/** "PI 31/07/2026 · 5 blocks" - which statement the figure came off, and out of how many
 *  invoices it was added up (S6, AC-F4). The block count is only said when there is more
 *  than one, because "1 block" is what an invoice is. */
function proformaHoldingNote(row: ContainerRequestRow): string {
  const when = row.holding_as_of ? formatDateInMalaysia(row.holding_as_of) : EM_DASH;
  const blocks = row.holding_blocks ?? 0;
  return blocks > 1 ? `PI ${when} · ${fmtInt(blocks)} blocks` : `PI ${when}`;
}

function HoldingCell({
  row,
  onOpenBlocks,
}: {
  row: ContainerRequestRow;
  onOpenBlocks: () => void;
}) {
  if (row.holding_source === 'proforma') {
    const split = row.blocks ?? [];
    return (
      <div className="flex flex-col text-2xs">
        {/* The sum opens its own parts: five blocks added together is a figure nobody can
            check against the supplier's paper unless the split is one click away. */}
        <PlanNumberButton
          value={fmtInt(row.holding_qty ?? 0)}
          label="Invoice blocks behind this figure"
          onClick={onOpenBlocks}
          disabled={split.length === 0}
        />
        <span className="text-muted-foreground">{proformaHoldingNote(row)}</span>
      </div>
    );
  }
  if (row.holding_source === 'none') {
    return <span className="text-2xs text-muted-foreground">{EM_DASH}</span>;
  }
  return <span className="tabular-nums">{fmtInt(row.qty_packed)}</span>;
}

/** The per-block split behind one proforma row's figure (AC-F4, AC-J2/J3 parity - S10 fix 3). */
function blocksColumns(total: number): ColumnDef<ContainerRequestHoldingBlock>[] {
  return [
    {
      id: 'block',
      header: 'Block',
      cell: ({ row }) => row.original.block_index ?? EM_DASH,
      footer: () => TOTAL_LABEL,
      size: 90,
    },
    {
      id: 'invoice',
      header: 'Invoice',
      cell: ({ row }) => (
        <span className="block truncate" title={row.original.pi_number}>
          {row.original.pi_number}
        </span>
      ),
      size: 220,
    },
    {
      id: 'packed',
      header: 'Packed',
      cell: ({ row }) => fmtInt(row.original.qty),
      footer: () => fmtInt(total),
      size: 110,
      meta: RIGHT,
    },
  ];
}

function BlocksTable({ row }: { row: ContainerRequestRow }) {
  const blocks = useMemo(() => row.blocks ?? [], [row.blocks]);
  const total = useMemo(() => blocks.reduce((s, b) => s + b.qty, 0), [blocks]);
  const columns = useMemo(() => blocksColumns(total), [total]);

  return (
    <DrillTable
      columns={columns}
      rows={blocks}
      getRowId={(b, i) => `${b.pi_number}-${b.block_index ?? 0}-${i}`}
      emptyMessage="No invoice on this plan names this product."
      // Always rendered inside PlanRowDialog's own DialogBody (overflow-y-auto).
      scrollerMaxHeight={false}
    />
  );
}

/** What the grid holds while a lightbox is open: which figure was clicked, and on which row. */
interface OpenPlanRowDialog {
  kind: PlanRowDialogKind;
  row: ContainerRequestRow;
}

/** The build's SO lines in the shape the shared lightbox lists them (AC-B2). Every line
 *  carries its own channel too (S2): the Need dialog lists project and retail together, and
 *  needs to say which is which. */
function toDemandLines(lines: ContainerRequestSoLine[]): PlanDemandLineRow[] {
  return lines.map((l) => ({
    so_number: l.so_number,
    customer: l.customer_label,
    project: l.project_title,
    agent: l.agent_label,
    price: l.unit_price,
    open_qty: l.open_qty,
    qty: l.qty,
    required_date: l.required_date,
    channel: l.demand_class === 'project' ? 'project' : 'retail',
  }));
}

/**
 * The two 12-month series, zipped per month - the dialog reads one row per month with both
 * channels on it, the sidecar answers one series per channel.
 *
 * Keyed off the project series' buckets because both are zero-filled over the SAME twelve
 * months by `_series`, so there is no month on one and not the other.
 */
function toHistoryPoints(history: ContainerRequestHistoryProduct | undefined): PlanHistoryPoint[] {
  if (!history) return [];
  const retail = new Map(history.retail.months.map((m) => [m.month, m.qty]));
  return history.project.months.map((m) => ({
    month: m.month,
    project_qty: m.qty,
    retail_qty: retail.get(m.month) ?? 0,
  }));
}

export function ContainerRequestSection({
  planId,
  supplierId,
  supplierName,
  qtyFor,
  onQtyChange,
  remarkFor,
  onRemarkChange,
  readOnly = false,
}: {
  /** The plan this section belongs to (R2). Supplier and cut-off are the plan row's, so the
   *  build is asked for by plan id and nothing on this screen can disagree with it. */
  planId: string;
  supplierId: string;
  supplierName: string;
  /** The quantity to show and send for a row: the record page owns the typed edits, because
   *  Save and Send live on ITS toolbar (R5) and both have to act on what the grid shows. */
  qtyFor: (row: ContainerRequestRow) => number;
  onQtyChange: (rowKey: string, qty: number) => void;
  /** The instruction to the supplier for this row (R11) - same field, same save as the
   *  preview's own Remarks input. */
  remarkFor: (row: ContainerRequestRow) => string;
  onRemarkChange: (rowKey: string, remark: string) => void;
  /** A cancelled plan is a record of what was asked, not a form (AC-A8). */
  readOnly?: boolean;
}) {
  const router = useRouter();
  const build = useContainerRequestBuild(planId);

  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [view, setView] = useState<'table' | 'schedule'>('table');
  // AC-N3: client-side, every row is already on the client. Filters the grid and the Schedule
  // view alike; the stat cards and Save (N) read `rows` unfiltered.
  const [searchQuery, setSearchQuery] = useState('');
  const [matrixAxis, setMatrixAxis] = useState<ContainerRequestMatrixAxis>('product');
  const [matrixGranularity, setMatrixGranularity] =
    useState<ContainerRequestMatrixGranularity>('week');
  // The row whose breakdown is open. Held as its KEY, not the row object, so a refresh
  // behind an open dialog shows the NEW numbers rather than the ones it opened on.
  const [openRowKey, setOpenRowKey] = useState<string | null>(null);
  // The figure whose documents are open (R7). ONE state for all eight columns: two of them
  // could never be open at once, and a state per column is eight ways to leave one behind.
  const [dialog, setDialog] = useState<OpenPlanRowDialog | null>(null);

  const rows = useMemo(() => build.data?.rows ?? [], [build.data]);
  const soLines = useMemo(() => build.data?.lines ?? [], [build.data]);

  // AC-N3: case-insensitive substring over item_code, product_name and set_code. `rows`
  // itself stays unfiltered - the stat cards and Save (N) read it directly.
  const searchTerm = searchQuery.trim().toLowerCase();
  // AC-F2/AC-N5: a no-demand row has no `rank`, and the sort comparator that would push it
  // after every ranked row only runs once a column is actually clicked (`sorting` starts
  // empty, and TanStack Table leaves an unsorted model in array order). Pre-ordering here -
  // ranked rows first in the build's own order, no-demand rows after - gets that placement
  // right on the very first render, with no sort state to seed and nothing to undo when a
  // real sort is picked.
  const displayRows = useMemo(() => {
    const filtered = searchTerm
      ? rows.filter((r) =>
          [r.item_code, r.product_name, r.set_code].some(
            (v) => v && v.toLowerCase().includes(searchTerm),
          ),
        )
      : rows;
    const ranked = filtered.filter((r) => r.has_demand !== false);
    const noDemand = filtered.filter((r) => r.has_demand === false);
    return [...ranked, ...noDemand];
  }, [rows, searchTerm]);

  const linesByProduct = useMemo(() => {
    const map = new Map<string, ContainerRequestSoLine[]>();
    for (const l of soLines) {
      const arr = map.get(l.product_id);
      if (arr) arr.push(l);
      else map.set(l.product_id, [l]);
    }
    return map;
  }, [soLines]);

  // The schedule matrix's product-axis rows follow the SAME rank the ranked demand table
  // already shows - the two are two views of one order, not two independent sorts. Rows come
  // back from the build already sorted by rank; this just carries that rank along by product_id
  // for the matrix builder to key off.
  const rankByProductId = useMemo(() => {
    const map = new Map<string, number>();
    for (const r of rows) {
      if (r.rank !== null) map.set(r.product_id, r.rank);
    }
    return map;
  }, [rows]);

  // AC-N3: the Schedule view reads the SAME filter, applied by product - a line whose product
  // did not match the query is not on the plan she asked to see, whichever axis it is grouped
  // by (an order row disappears entirely once none of its lines match).
  const matchedProductIds = useMemo(
    () => new Set(displayRows.map((r) => r.product_id)),
    [displayRows],
  );
  const matrixSoLines = useMemo(
    () => (searchTerm ? soLines.filter((l) => matchedProductIds.has(l.product_id)) : soLines),
    [soLines, matchedProductIds, searchTerm],
  );

  const matrix = useMemo(
    () => buildContainerRequestMatrix(matrixSoLines, matrixAxis, matrixGranularity, rankByProductId),
    [matrixSoLines, matrixAxis, matrixGranularity, rankByProductId],
  );

  const historyRef = useRef(new Map<string, ContainerRequestHistoryProduct>());

  // Read through refs for the same reason the history sidecar is: a column definition that
  // depended on the record page's edit state would rebuild the whole grid on every keystroke.
  // Cell functions run on every render, so the typed figure still paints.
  const qtyForRef = useRef(qtyFor);
  qtyForRef.current = qtyFor;
  const onQtyChangeRef = useRef(onQtyChange);
  onQtyChangeRef.current = onQtyChange;
  const remarkForRef = useRef(remarkFor);
  remarkForRef.current = remarkFor;
  const onRemarkChangeRef = useRef(onRemarkChange);
  onRemarkChangeRef.current = onRemarkChange;
  const readOnlyRef = useRef(readOnly);
  readOnlyRef.current = readOnly;

  // A row edited to 0 leaves the request without being deleted from the grid - she can still
  // see and change her mind about it, it just does not go on the document.
  //
  // AC-Q4: a cancelled plan is a record of what was asked, not a form - there is no input left
  // to disable, so it renders as plain text instead. No formula title here either way (review
  // round, S3): the hover that explains the arithmetic belongs on the Suggested qty cell that
  // shows `engine_qty`, not on this one's typed override.
  const renderQtyCell = useCallback((ctx: CellContext<ContainerRequestRow, unknown>) => {
    const original = ctx.row.original;
    const value = qtyForRef.current(original);
    if (readOnlyRef.current) {
      return <span className="tabular-nums">{fmtInt(value)}</span>;
    }
    return (
      <LiveNumberInput
        min={0}
        rules={{ min: 0 }}
        className="h-8 w-24 tabular-nums"
        value={value}
        onCommit={(next) => onQtyChangeRef.current(original.row_key, next)}
      />
    );
  }, []);

  // R11: the plan table's own Remarks input, next to the qty edit - the same field the
  // preview's Remarks cell writes.
  const renderRemarkCell = useCallback((ctx: CellContext<ContainerRequestRow, unknown>) => {
    const original = ctx.row.original;
    return (
      <Input
        type="text"
        className="h-8 w-40"
        value={remarkForRef.current(original)}
        disabled={readOnlyRef.current}
        placeholder="Instruction to the supplier"
        onChange={(e) => onRemarkChangeRef.current(original.row_key, e.target.value)}
      />
    );
  }, []);

  const columns = useMemo<ColumnDef<ContainerRequestRow>[]>(
    () => [
      {
        id: 'rank',
        // AC-N5: a row with no rank (has_demand: false) sorts after every ranked row, once a
        // sort is actually applied, rather than tying with rank 1.
        accessorFn: (row) => row.rank ?? Number.MAX_SAFE_INTEGER,
        header: ({ column }) => (
          <span className="flex items-center gap-1">
            <DataGridColumnHeader title="Rank" column={column} />
            <FormulaTip
              label="How the rank is worked out"
              formula="score = sum(weight x value) / sum(weight)"
              terms={[
                { name: 'Need-by date', weight: 'x3', note: 'earliest SO date, sooner is higher' },
                { name: 'Demand class', weight: 'x3', note: 'project 1.0, retail 0.4' },
                { name: 'Document age', weight: 'x1', note: 'oldest SO date, older is higher' },
              ]}
              footer="Each value is scaled against the other rows here; a factor with no value is left out. Rank 1 = highest score. Hover a score for its factors; weights live in Policies."
            />
          </span>
        ),
        cell: ({ row }) => {
          const original = row.original;
          const muted = original.has_demand === false;
          return (
            <div className="flex items-center gap-1">
              <span
                className={cn('tabular-nums', muted ? 'text-muted-foreground/60' : 'text-muted-foreground')}
              >
                {original.rank ?? EM_DASH}
              </span>
              {!muted ? (
                <RankFactorsPopover
                  line={{ rank_score: original.rank_score, factors: original.rank_factors }}
                />
              ) : null}
            </div>
          );
        },
        size: 110,
        enableSorting: true,
        meta: { headerTitle: 'Rank' },
      },
      {
        id: 'product',
        // AC-N5: sorts by the code every row shows, product or set alike.
        accessorFn: (row) => row.item_code ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          const original = row.original;
          const muted = original.has_demand === false;
          return (
            <button
              type="button"
              // The grid is the scan surface; the breakdown behind it is one click away, the
              // same move the fulfilment board's cell makes.
              onClick={() => setOpenRowKey(original.row_key)}
              title="What this row is made of"
              className={cn(
                'flex min-w-0 flex-col text-start underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
                muted && 'opacity-70',
              )}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate font-medium" title={original.item_code ?? ''}>
                  {original.item_code ?? EM_DASH}
                </span>
                {original.row_kind === 'set' ? (
                  // The supplier's code names our SET, so the row is the whole WC and every
                  // figure on it is the driver member's (R19). Badged rather than explained:
                  // the driver's own code sits underneath, which is the explanation.
                  <Badge
                    variant="secondary"
                    appearance="light"
                    size="sm"
                    className="shrink-0"
                    data-testid="set-badge"
                  >
                    Set
                  </Badge>
                ) : null}
              </span>
              {original.row_kind === 'set' ? (
                <span
                  className="truncate text-2xs text-muted-foreground"
                  title={`Figures from ${original.driver_item_code ?? 'its driver member'}`}
                >
                  {original.driver_item_code ?? EM_DASH}
                </span>
              ) : productNameDiffersFromCode(original) ? (
                <span
                  className="truncate text-2xs text-muted-foreground"
                  title={original.product_name ?? ''}
                >
                  {original.product_name}
                </span>
              ) : null}
            </button>
          );
        },
        size: 210,
        enableSorting: true,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'suggested_qty',
        // S3 (owner, 15 Sep): the formula's own answer, read-only - "every time we change the
        // suggested quantity manually, we forgot what's the original suggested quantity" is
        // answered by keeping `engine_qty` on screen beside the input rather than only in its
        // hover title. Sortable on the engine figure, which is what this column now shows.
        accessorFn: (row) => row.engine_qty,
        header: ({ column }) => (
          <span className="flex items-center gap-1">
            <DataGridColumnHeader title="Suggested qty" column={column} />
            <FormulaTip
              label="How the suggested quantity is worked out"
              formula="suggested = need - on hand - incoming SPO - incoming PL (not yet on an SPO)"
              terms={[
                { name: 'Need', note: 'open SO lines, project and retail, until the plan date' },
                { name: 'On hand', note: 'every active location' },
                { name: 'Incoming SPO', note: 'every active location' },
                {
                  name: 'Incoming PL',
                  note: 'unreceived packing lists, minus whatever of them already has an SPO',
                },
              ]}
              footer="Never below 0. An open PO is shown beside it, not subtracted."
            />
          </span>
        ),
        cell: ({ row }) => {
          const original = row.original;
          const muted = original.has_demand === false;
          return (
            <span
              className={cn('tabular-nums', muted && 'text-muted-foreground/60')}
              title={engineQtyFormulaTitle(original)}
            >
              {fmtInt(original.engine_qty)}
            </span>
          );
        },
        size: 110,
        enableSorting: true,
        meta: { headerTitle: 'Suggested qty' },
      },
      {
        id: 'requested_qty',
        // Captain's call, review round 12 Sep: sortable on the row's own SAVED override (not
        // the live-typed value mid-edit) - "what am I asking most of" is a real question over
        // 60 rows, and this is the same rule the old single column sorted by.
        accessorFn: (row) => row.suggested_qty,
        header: ({ column }) => <DataGridColumnHeader title="Requested qty" column={column} />,
        cell: renderQtyCell,
        size: 140,
        enableSorting: true,
        meta: { headerTitle: 'Requested qty' },
      },
      {
        id: 'remark',
        header: ({ column }) => <DataGridColumnHeader title="Remarks" column={column} />,
        cell: renderRemarkCell,
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Remarks' },
      },
      {
        id: 'open_so_need',
        accessorFn: (row) => row.open_so_need,
        header: ({ column }) => <DataGridColumnHeader title="Need" column={column} />,
        // S2: the figure that used to be plain text now opens its OWN dialog - project and
        // retail together, since Need is the sum of both.
        cell: ({ row }) => {
          const original = row.original;
          if (original.has_demand === false) {
            return (
              <span className="text-2xs text-muted-foreground" title="Need">
                No open demand
              </span>
            );
          }
          return (
            <PlanNumberButton
              value={fmtInt(original.open_so_need)}
              label="Open demand, project and retail"
              disabled={!original.open_so_need}
              onClick={() => setDialog({ kind: 'need', row: original })}
            />
          );
        },
        size: 100,
        enableSorting: true,
        meta: { headerTitle: 'Need' },
      },
      {
        accessorKey: 'project_qty',
        header: ({ column }) => <DataGridColumnHeader title="Project" column={column} />,
        // The SO drill sits on the channel figure it explains (captain, 27 Aug): the "Open
        // SOs" count column is gone, and each channel opens its own lines.
        cell: ({ row }) => (
          <PlanNumberButton
            value={fmtInt(row.original.project_qty)}
            label="Open project sales orders"
            // Nothing to open is not a link: a figure of zero with no line behind it would
            // open an empty table, which teaches the eye to stop clicking every one of them.
            disabled={
              !row.original.project_qty ||
              (linesByProduct.get(row.original.product_id) ?? []).every(
                (l) => l.demand_class !== 'project',
              )
            }
            onClick={() => setDialog({ kind: 'project', row: row.original })}
          />
        ),
        size: 140,
        enableSorting: true,
        meta: { headerTitle: 'Project' },
      },
      {
        accessorKey: 'retail_qty',
        header: ({ column }) => <DataGridColumnHeader title="Retail" column={column} />,
        // Retail carries the unclassified lines too: the column already counts them (no
        // Unclassified column, AC-A2.2), so the drill lists what the figure counts.
        cell: ({ row }) => (
          <PlanNumberButton
            value={fmtInt(row.original.retail_qty)}
            label="Open retail sales orders"
            disabled={
              !row.original.retail_qty ||
              (linesByProduct.get(row.original.product_id) ?? []).every(
                (l) => l.demand_class === 'project',
              )
            }
            onClick={() => setDialog({ kind: 'retail', row: row.original })}
          />
        ),
        size: 140,
        enableSorting: true,
        meta: { headerTitle: 'Retail' },
      },
      {
        accessorKey: 'on_hand',
        header: ({ column }) => <DataGridColumnHeader title="On hand" column={column} />,
        // Openable at zero too, unlike the demand cells: "nothing at any active location" is
        // the answer the location table gives, and it is the one the buyer came for.
        cell: ({ row }) => (
          <PlanNumberButton
            value={fmtInt(row.original.on_hand)}
            label="Stock by location"
            onClick={() => setDialog({ kind: 'on_hand', row: row.original })}
          />
        ),
        size: 90,
        enableSorting: true,
        meta: { headerTitle: 'On hand' },
      },
      {
        accessorKey: 'incoming_spo',
        header: ({ column }) => <DataGridColumnHeader title="SPO" column={column} />,
        cell: ({ row }) => (
          <PlanNumberButton
            value={fmtInt(row.original.incoming_spo)}
            label="Shipping orders on their way, any active location"
            onClick={() => setDialog({ kind: 'spo', row: row.original })}
          />
        ),
        size: 80,
        enableSorting: true,
        meta: { headerTitle: 'SPO' },
      },
      {
        accessorKey: 'incoming_pl',
        header: ({ column }) => <DataGridColumnHeader title="Incoming PL" column={column} />,
        // A reference: a packing list names no destination, so it can be read beside the ask
        // but never subtracted from it (Q1). The lightbox says which lists they are.
        cell: ({ row }) => (
          <PlanNumberButton
            value={fmtInt(row.original.incoming_pl)}
            label="Packing lists on their way, reference only"
            onClick={() => setDialog({ kind: 'incoming_pl', row: row.original })}
          />
        ),
        size: 100,
        enableSorting: true,
        meta: { headerTitle: 'Incoming PL' },
      },
      {
        id: 'total_supply',
        // AC-N5: sortable off the same sum the cell prints, so the two cannot disagree.
        accessorFn: (row) => row.on_hand + row.incoming_spo + row.incoming_pl,
        header: ({ column }) => <DataGridColumnHeader title="Total supply" column={column} />,
        // R7: On hand + SPO + Incoming PL, plain - no lightbox of its own, the three source
        // columns and theirs are untouched.
        cell: ({ row }) => (
          <span className="tabular-nums">
            {fmtInt(row.original.on_hand + row.original.incoming_spo + row.original.incoming_pl)}
          </span>
        ),
        size: 110,
        enableSorting: true,
        meta: { headerTitle: 'Total supply' },
      },
      {
        accessorKey: 'outstanding_po',
        header: ({ column }) => <DataGridColumnHeader title="PO" column={column} />,
        cell: ({ row }) => (
          <PlanNumberButton
            value={fmtInt(row.original.outstanding_po)}
            label="Purchase orders still to come, reference only"
            onClick={() => setDialog({ kind: 'po', row: row.original })}
          />
        ),
        size: 80,
        enableSorting: true,
        meta: { headerTitle: 'PO' },
      },
      {
        id: 'holding',
        // Sortable: `accessorFn` gives the sort its number; the cell shows the figure.
        accessorFn: holdingSortValue,
        header: ({ column }) => <DataGridColumnHeader title="Packed" column={column} />,
        cell: ({ row }) => (
          <HoldingCell
            row={row.original}
            onOpenBlocks={() => setDialog({ kind: 'blocks', row: row.original })}
          />
        ),
        size: 100,
        enableSorting: true,
        sortDescFirst: true,
        meta: { headerTitle: 'Packed' },
      },
    ],
    [renderQtyCell, renderRemarkCell, linesByProduct],
  );

  const table = useReactTable({
    columns,
    data: displayRows,
    getRowId: (row) => row.row_key,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // AC-B8: the sidecar is asked for the products ON SCREEN, never the whole supplier. A
  // 120-product stock list would otherwise pay for 240 monthly series to read 25 rows.
  const historyProductIds = useMemo(
    () => table.getRowModel().rows.map((r) => r.original.product_id),
    // The row model is rebuilt when any of these move; `table` itself is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [table, displayRows, pagination, sorting],
  );
  const history = useContainerRequestHistory(supplierId, historyProductIds);
  historyRef.current = useMemo(() => {
    const map = new Map<string, ContainerRequestHistoryProduct>();
    for (const p of history.data?.products ?? []) map.set(p.product_id, p);
    return map;
  }, [history.data]);

  // `qtyFor` follows the record page's edits, so the cards move as she types - which is the
  // whole point of putting them above the grid.
  const summary = summariseContainerRequest(rows, qtyFor);

  const openRow = openRowKey
    ? (rows.find((r) => r.row_key === openRowKey) ?? null)
    : null;

  if (build.isLoading) {
    return (
      <div className="space-y-4">
        <Card className="p-4">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="mt-3 h-24 w-full rounded-lg" />
        </Card>
      </div>
    );
  }

  if (build.isError) {
    return (
      <div className="space-y-4">
        <Card className="flex flex-col items-center gap-3 p-8 text-center">
          <p className="text-sm font-medium text-destructive">{build.error.message}</p>
          <Button variant="outline" size="sm" onClick={() => build.refetch()}>
            <RefreshCw className="size-4" />
            Try again
          </Button>
        </Card>
      </div>
    );
  }

  // AC-A1: the "no stock list yet" card is GONE. A plan with a file on it (stock list or
  // proforma) lists what the file names; a plan with none lists what we buy from this
  // supplier crossed with what customers are owed (the file-or-links rule, 12 Sep 2026), so a
  // supplier who has never sent a stock list still gets a table; "They hold" reads their
  // newest proforma, or a dash.
  if (!build.data || rows.length === 0) {
    // Review round (12 Sep): a statement on file that named nothing of ours is a MATCHING
    // gap, not an empty universe - the file-or-links rule (slice 1) means a file with real
    // rows on it produced zero rows here, and "start a new plan" told her to redo work the
    // Supplier codes tab already exists to finish.
    const hasStatement = Boolean(build.data?.stock_list_as_of);
    return (
      <div className="space-y-4">
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <PackageSearch className="size-5" />
          </span>
          <p className="text-sm font-medium">
            {hasStatement
              ? 'This file named nothing in our catalogue yet.'
              : `Nothing to ask ${supplierName} for right now.`}
          </p>
          <p className="text-2xs text-muted-foreground">
            {hasStatement ? (
              'Match its codes on the Supplier codes tab.'
            ) : (
              <>
                No open customer demand on what they supply, and nothing of theirs on file.
                Start a new plan from the loading plans list to hand over a newer stock list
                or proforma.
              </>
            )}
          </p>
        </Card>
      </div>
    );
  }


  // AC-N7 (review round, 12 Sep): the window, both ends, ONE string - the heading and the
  // Outstanding card's sub-label read the same `describeWindow` call so they cannot say two
  // different things about what this build was worked out against.
  const windowLabel = describeWindow(build.data.plan_horizon_start, build.data.plan_horizon_date);

  return (
    <div className="space-y-4">
      {/* The cards carry the swatches, which is why there is no legend row under them (r4). */}
      <ContainerRequestStatCards
        summary={summary}
        planHorizonStart={build.data.plan_horizon_start}
        planHorizonDate={build.data.plan_horizon_date}
      />

      <DataGrid
        table={table}
        recordCount={displayRows.length}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        emptyMessage={
          searchTerm
            ? 'No product matches'
            : 'No open customer demand for what this supplier supplies.'
        }
      >
        <Card>
          {/* The heading, and nothing else (R5): Send and the gear moved to the record's own
              toolbar, where the rest of the plan's actions already sit. */}
          <CardHeader className="py-3">
            <h3 className="text-sm font-semibold">
              What to ask {supplierName}
              {windowLabel === 'every open order' ? ' for' : ` to cover ${windowLabel}`}
            </h3>
          </CardHeader>

          <div className="flex flex-col gap-3 border-t border-border px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div
                className="inline-flex rounded-md border border-input"
                role="group"
                aria-label="Request view"
              >
                <Button
                  type="button"
                  size="sm"
                  variant={view === 'table' ? 'primary' : 'ghost'}
                  className="rounded-e-none"
                  aria-pressed={view === 'table'}
                  onClick={() => setView('table')}
                >
                  <Table2 className="size-4" aria-hidden />
                  Table
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={view === 'schedule' ? 'primary' : 'ghost'}
                  className="rounded-s-none border-s border-input"
                  aria-pressed={view === 'schedule'}
                  onClick={() => setView('schedule')}
                >
                  <LayoutGrid className="size-4" aria-hidden />
                  Schedule
                </Button>
              </div>
              {/* AC-N3: client-side over item_code/product_name/set_code - every row is
                  already on the client, so there is no round trip to debounce. */}
              <ListSearchInput
                value={searchQuery}
                onChange={setSearchQuery}
                placeholder="Search product"
                className="w-full sm:w-64"
              />
            </div>

            {view === 'schedule' ? (
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2">
                  <Label htmlFor="request-matrix-rows" className="text-xs text-muted-foreground">
                    Rows
                  </Label>
                  <div className="w-32">
                    <SearchableSelect
                      id="request-matrix-rows"
                      value={matrixAxis}
                      onChange={(v) => setMatrixAxis(v as ContainerRequestMatrixAxis)}
                      options={MATRIX_AXIS_OPTIONS}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Label htmlFor="request-matrix-by" className="text-xs text-muted-foreground">
                    By
                  </Label>
                  <div className="w-32">
                    <SearchableSelect
                      id="request-matrix-by"
                      value={matrixGranularity}
                      onChange={(v) => setMatrixGranularity(v as ContainerRequestMatrixGranularity)}
                      options={MATRIX_GRANULARITY_OPTIONS}
                    />
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          {view === 'table' ? (
            <>
              <CardTable>
                <DataGridTable />
              </CardTable>
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            </>
          ) : matrix.rows.length === 0 ? (
            <div className="p-6 text-center">
              <p className="text-sm font-medium">No open sales-order lines behind this request.</p>
              <p className="mt-1 text-2xs text-muted-foreground">
                The schedule reads the same open SO lines the Table view totals - nothing to
                bucket by date yet.
              </p>
            </div>
          ) : (
            <div className="p-4">
              <ContainerRequestScheduleMatrix
                buckets={matrix.buckets}
                rows={matrix.rows}
                rowHeader={matrixAxis === 'order' ? 'Order' : 'Product'}
                cells={matrix.cells}
              />
            </div>
          )}
        </Card>
      </DataGrid>

      {openRow ? (
        <ContainerRequestRowDialog
          row={openRow}
          askQty={qtyFor(openRow)}
          soLines={linesByProduct.get(openRow.product_id) ?? []}
          history={historyRef.current.get(openRow.product_id)}
          historyLoading={history.isFetching}
          onClose={() => setOpenRowKey(null)}
        />
      ) : null}

      {/* The documents behind one figure (R7). Every column's lightbox is this one dialog, so
          the eight of them cannot drift apart; only the body inside it changes. */}
      {dialog ? (
        <PlanRowDialog
          kind={dialog.kind}
          productCode={dialog.row.item_code ?? dialog.row.product_name ?? 'This product'}
          productName={
            dialog.row.row_kind === 'set'
              ? // A set row's figures are the driver member's (R19), and so are its documents:
                // the dialog says whose rather than leaving the reader to guess.
                `${dialog.row.set_name ?? dialog.row.product_name ?? ''} · figures from ${dialog.row.driver_item_code ?? 'its driver member'}`
              : dialog.row.product_name
          }
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        >
          {dialog.kind === 'project' || dialog.kind === 'retail' || dialog.kind === 'need' ? (
            <ProjectRetailTabs
              channel={dialog.kind}
              lines={toDemandLines(
                // Need lists both channels unfiltered (S2); Project/Retail keep the split
                // they always had.
                dialog.kind === 'need'
                  ? (linesByProduct.get(dialog.row.product_id) ?? [])
                  : (linesByProduct.get(dialog.row.product_id) ?? []).filter((l) =>
                      dialog.kind === 'project'
                        ? l.demand_class === 'project'
                        : l.demand_class !== 'project',
                    ),
              )}
              history={toHistoryPoints(historyRef.current.get(dialog.row.product_id))}
              horizon={build.data?.plan_horizon_date ?? null}
              loading={build.isFetching || history.isFetching}
            />
          ) : dialog.kind === 'on_hand' ? (
            // R7 (captain 8 Sep 2026): this grid's own On hand cell counts every active
            // location, so the lightbox behind it must too, or the total stops footing.
            <OnHandTable productId={dialog.row.product_id} scope="all" />
          ) : dialog.kind === 'spo' ? (
            <SpoTabs supplierId={supplierId} productId={dialog.row.product_id} />
          ) : dialog.kind === 'blocks' ? (
            <BlocksTable row={dialog.row} />
          ) : dialog.kind === 'incoming_pl' ? (
            <IncomingPlTable
              supplierId={supplierId}
              productId={dialog.row.product_id}
              onOpenShipment={(shipmentId) =>
                router.push(`/procurement-management/packing-lists/${shipmentId}`)
              }
            />
          ) : (
            <PoTabs supplierId={supplierId} productId={dialog.row.product_id} />
          )}
        </PlanRowDialog>
      ) : null}
    </div>
  );
}

export default ContainerRequestSection;
