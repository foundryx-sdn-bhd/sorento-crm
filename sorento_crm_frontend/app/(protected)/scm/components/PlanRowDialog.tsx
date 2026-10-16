'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  type ColumnDef,
  type ExpandedState,
  type OnChangeFn,
  type SortingState,
  getCoreRowModel,
  getExpandedRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { getStatusBadgeVariant, formatStatusLabel } from '@/lib/status-badge';
import { cn } from '@/lib/utils';

import { LiveNumberInput } from '@/components/ui/number-input';
import { demandClassBadge } from '../lib/demandClass';
import { EM_DASH, fmtDate, fmtInt, fmtMoney, fmtSupplierCost } from '../lib/format';
import { purchaseOrderStatusPill } from '../lib/purchaseOrderStatus';
import { useContainerRequestDrill } from '../hooks/useContainerRequestDrill';
import { useLocationStock } from '../reorder/hooks/useReorderRun';
import { StockDocumentsPanel } from '../../project-sales/fulfilment-planning/components/StockDocumentsPanel';
import type {
  ContainerRequestDrillIncomingPlRow,
  ContainerRequestDrillPoRow,
  ContainerRequestDrillSpoRow,
} from '../services/containerRequestDrillService';
import { distinctName } from '@/lib/product-display';

/**
 * ONE lightbox for the SCM family (R7, AC-B1/AC-B7).
 *
 * Every figure a buyer would want to argue with opens a dialog naming the DOCUMENTS behind
 * it: the sales orders, the shipping orders, the packing lists, the purchase orders, the
 * stock rows. What this replaces on the loading plan and the SPO planner was a hover popover
 * per number - mouse-only, too narrow for a document table, dismissed by the mouse drifting
 * off it, and (inside a DataGrid cell) painted over by the sticky column beside it, which is
 * why each one carried a `PopoverPortal` workaround.
 *
 * The shell is copied from the reorder-revamp lane's `PlanRowDialogs.tsx` rather than
 * reinvented, so the two screens' lightboxes are the same object to a reader; at whichever
 * merge lands second, that lane re-points its import here and one file survives (plan
 * section 9).
 *
 * The shell knows nothing about any body: it is a titled frame, and the caller renders what
 * belongs inside. A registry keyed on `kind` would have to import every body and so every
 * body's data hook, which is how one dialog comes to fetch for six screens.
 *
 * S9 (3 Sep, plan section 3.9): every body in this file renders on the repo's own `DataGrid`
 * rather than a plain `<table>` - the SPO document detail's own line table
 * (`SPODocumentDetail.tsx`) is the reference this family now matches: `TabsList variant="line"`,
 * fixed-width resizable columns, and a footer TOTAL row under whichever column the cell's own
 * figure sums. `DrillTable` below is the one place that wiring lives, because every body here
 * is the same shape - a caller-held list of rows, no server pagination, no per-user column
 * memory (a dialog's columns are not a personal preference, so `listingKey` is always `null`).
 * `OnHandTable` builds its own table instead of going through it: its rows expand in place,
 * which needs TanStack's expanded-row state passed straight to `useReactTable`.
 */

export type PlanRowDialogKind =
  | 'project'
  | 'retail'
  // Project and retail together, unfiltered (S2): the Need cell's own lightbox, so a figure
  // that is the SUM of two channels has a table that adds up to it too.
  | 'need'
  | 'on_hand'
  | 'spo'
  | 'incoming_pl'
  | 'po'
  | 'po_takes'
  | 'so_coverage'
  // The invoice blocks a loading plan's "They hold" figure is the SUM of (S6): one uploaded
  // file holds five stacked invoices, and a figure that is five numbers added up has to be
  // openable or it cannot be checked against the paper.
  | 'blocks'
  // R5: the PO detail's "Placed" column and the SO detail's "Linked" column, each a figure
  // that is the SUM of several documents on one line - the "Allocated to" card these replace
  // is gone (AC-L3).
  | 'placements'
  | 'links'
  // SO-DELIVERIES-VIEW: the DOs and Cancel SO documents under one sales-order line.
  | 'deliveries';

/** The word in front of the product code. Kept here so the titles cannot drift. */
export const PLAN_ROW_DIALOG_TITLES: Record<PlanRowDialogKind, string> = {
  project: 'Project',
  retail: 'Retail',
  need: 'Need',
  on_hand: 'On hand',
  spo: 'SPO',
  incoming_pl: 'Incoming PL',
  po: 'PO',
  po_takes: 'PO covers',
  so_coverage: 'SO covered',
  blocks: 'Packed',
  placements: 'Placed on',
  links: 'Linked to',
  deliveries: 'Deliveries',
};

// ---------------------------------------------------------------------------
// Table furniture - exported so a body written by another screen looks the same
//
// `ContainerRequestScheduleMatrix.tsx` is the one remaining consumer: its schedule PIVOT is
// not a list of rows (it is a product/SO axis against day/week/month buckets) and so is never
// a `DataGrid` - these stay here for it.
// ---------------------------------------------------------------------------

export function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <th
      className={cn(
        'whitespace-nowrap px-2 py-1.5 font-medium text-muted-foreground',
        right ? 'text-right' : 'text-left',
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  right,
  title,
  className,
  colSpan,
}: {
  children: ReactNode;
  right?: boolean;
  title?: string;
  className?: string;
  colSpan?: number;
}) {
  return (
    <td
      className={cn('px-2 py-1.5', right && 'text-right tabular-nums', className)}
      title={title}
      colSpan={colSpan}
    >
      {children}
    </td>
  );
}

export function EmptyRow({ colSpan, children }: { colSpan: number; children: ReactNode }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-2 py-6 text-center text-muted-foreground">
        {children}
      </td>
    </tr>
  );
}

export function LoadingRows({ colSpan }: { colSpan: number }) {
  return (
    <>
      {Array.from({ length: 3 }).map((_, i) => (
        <tr key={i}>
          <td colSpan={colSpan} className="px-2 py-1.5">
            <Skeleton className="h-4 w-full" />
          </td>
        </tr>
      ))}
    </>
  );
}

export function DocTable({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">{children}</table>
    </div>
  );
}

/** What a body says when a drill has no SPO on its way to any active location for this
 *  product (R7, captain 8 Sep 2026: this used to say "a site pool" - the drill now counts
 *  a project bin too, so "nothing" has to mean nothing anywhere, not nothing in a pool). */
export const NO_SPO_TO_POOL = 'No SPO is on its way for this product.';

function textCell(value: string | null | undefined) {
  return value ? value : <span className="text-muted-foreground">{EM_DASH}</span>;
}

function moneyCell(value: number | null | undefined) {
  return value === null || value === undefined ? (
    <span className="text-muted-foreground">{EM_DASH}</span>
  ) : (
    fmtMoney(value)
  );
}

/** Every right-aligned quantity/money/date column shares this header + cell alignment. */
export const RIGHT: { headerClassName: string; cellClassName: string } = {
  headerClassName: 'text-right',
  cellClassName: 'text-right tabular-nums',
};

/** One `Skeleton` bar, reused across every column's `meta.skeleton` in this file. */
const SKELETON_CELL = <Skeleton className="h-4 w-full" />;

/** The label half of a footer TOTAL row (AC-J3) - under whichever column comes first. */
export const TOTAL_LABEL = <span className="text-muted-foreground">Total</span>;

/**
 * A tab's own table (AC-J2): every row the caller already holds, in the repo's `DataGrid`,
 * with no server pagination (there is nothing left to page - the caller passed the whole
 * list) and no per-user column persistence (`listingKey={null}`: a dialog's columns are not a
 * personal preference). The horizontal scroll a wide table needs is the grid's own
 * `overflow-x-auto` scroller, which stays INSIDE the dialog body - the dialog itself never
 * grows past `max-h-[85vh]`.
 */
export function DrillTable<TRow extends object>({
  columns,
  rows,
  getRowId,
  isLoading,
  emptyMessage,
  scrollerMaxHeight,
}: {
  columns: ColumnDef<TRow>[];
  rows: TRow[];
  getRowId?: (row: TRow, index: number) => string;
  isLoading?: boolean;
  emptyMessage: ReactNode;
  /**
   * Pass `false` for a caller inside a `DialogBody`/`SheetBody` that already owns the
   * scroll viewport (B2, M5 review run 1) - most of `DrillTable`'s callers are inside
   * `PlanRowDialog` and want that. Omit it for a caller rendering this on a plain page
   * (e.g. `PoPlanCard`), which wants the grid's own bounded, sticky-header default.
   */
  scrollerMaxHeight?: string | false;
}) {
  const table = useReactTable({
    columns,
    data: rows,
    getRowId,
    getCoreRowModel: getCoreRowModel(),
    // F15 (review round): the DataGrid rule (CLAUDE.md) is `columnResizeMode: 'onChange'`
    // alongside `columnsResizable: true` - every lightbox that shares this shell inherits it
    // from here rather than each caller having to remember it.
    columnResizeMode: 'onChange',
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={Boolean(isLoading)}
      listingKey={null}
      tableLayout={{ width: 'fixed', columnsResizable: true, scrollerMaxHeight }}
      emptyMessage={emptyMessage}
    >
      <DataGridTable />
    </DataGrid>
  );
}

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/** `2026-04` reads `Apr 26`. The bucket is a month, so it is never rendered as a date. */
export function monthLabel(month: string | null): string {
  if (!month) return EM_DASH;
  const [year, m] = month.split('-');
  const name = MONTHS[Number(m) - 1];
  if (!name || !year) return month;
  return `${name} ${year.slice(2)}`;
}

// ---------------------------------------------------------------------------
// Project / Retail - the orders behind a channel's number, and the 12-month series
// ---------------------------------------------------------------------------

/** One open sales-order line behind the Project or Retail figure. */
export interface PlanDemandLineRow {
  so_number: string | null;
  customer: string | null;
  project: string | null;
  agent: string | null;
  /** What the customer pays, in ringgit. Null when the line carries no price. */
  price: number | null;
  /** Gross open qty before SPO netting (R1, `PLAN-loading-plan-project-spo-only.md`); `qty`
   *  is the balance still to ship. Equal to `qty` on a retail line, which has no SPO
   *  placements to be netted by. */
  open_qty: number;
  qty: number;
  required_date: string | null;
  /** The sales order's own page, when the caller can name one. */
  href?: string | null;
  /** Which channel this line is on - only read by the Need dialog's Channel column (S2). */
  channel?: 'project' | 'retail';
}

/** One month of the two 12-month series (AC-B2 / AC-B6). */
export interface PlanHistoryPoint {
  month: string;
  project_qty: number;
  retail_qty: number;
}

/**
 * The index of a series' biggest month (S1, AC-A2): the FIRST occurrence wins a tie, and a
 * series that never goes above 0 has no peak at all - `-1`, which no row index ever equals.
 */
function peakIndexOf(values: number[]): number {
  let bestIndex = -1;
  let bestValue = 0;
  values.forEach((value, index) => {
    if (value > bestValue) {
      bestValue = value;
      bestIndex = index;
    }
  });
  return bestIndex;
}

/** One cell of the 12-month history table (S1): tinted and marked when it is its column's
 *  peak, so the row it landed on reads at a glance rather than by scanning every month. */
function PeakValueCell({
  qty,
  isPeak,
  mark,
}: {
  qty: number;
  isPeak: boolean;
  mark: 'project' | 'retail' | 'total';
}) {
  return (
    <span
      data-peak={isPeak ? mark : undefined}
      className={cn(isPeak && 'rounded bg-primary/10 px-1.5 py-0.5 font-semibold')}
    >
      {fmtInt(qty)}
    </span>
  );
}

/**
 * One channel's demand, twice: what is still open before the plan's cut-off, and what the
 * product's order history says over the last twelve months.
 *
 * `channel='need'` (S2) is project and retail TOGETHER, unfiltered - the Need cell's own
 * lightbox, with an extra Channel column on the open tab and a Total column on the history
 * tab, so a figure that is the sum of two channels has a table (and a peak) that says so.
 *
 * Controlled and pure - the loading-plan grid already holds both payloads (the build's
 * `include_lines` read and the history read), so a second fetch here would ask the server for
 * what the caller is holding. `initialTab='history'` is how the Project peak / Retail peak
 * cells open the same dialog on the series they name (AC-B6).
 */
export function ProjectRetailTabs({
  channel,
  lines,
  history,
  initialTab = 'open',
  horizon = null,
  loading,
}: {
  channel: 'project' | 'retail' | 'need';
  lines: PlanDemandLineRow[];
  history: PlanHistoryPoint[];
  initialTab?: 'open' | 'history';
  /** The plan's cut-off date, when one is set (S3). The open tab's own label states it -
   *  there is no header context string any more. */
  horizon?: string | null;
  loading?: boolean;
}) {
  const total = useMemo(() => lines.reduce((sum, l) => sum + (l.qty || 0), 0), [lines]);
  const projectPeakIndex = useMemo(
    () => peakIndexOf(history.map((p) => p.project_qty)),
    [history],
  );
  const retailPeakIndex = useMemo(
    () => peakIndexOf(history.map((p) => p.retail_qty)),
    [history],
  );
  const totalPeakIndex = useMemo(
    () => peakIndexOf(history.map((p) => p.project_qty + p.retail_qty)),
    [history],
  );
  // AC-J3: the history tab foots every series it shows - the peak cell above states the
  // biggest month, this states the whole twelve.
  const projectTotal = useMemo(
    () => history.reduce((sum, p) => sum + (p.project_qty || 0), 0),
    [history],
  );
  const retailTotal = useMemo(
    () => history.reduce((sum, p) => sum + (p.retail_qty || 0), 0),
    [history],
  );
  // S3: the tab used to name the LINE COUNT ("Open project SO lines (2)"); it now names the
  // sum of qty, which is what the cell itself shows.
  const openLabel = horizon
    ? `Open before cut-off ${formatDateInMalaysia(horizon)} (${fmtInt(total)})`
    : `Open (${fmtInt(total)})`;

  const openColumns = useMemo<ColumnDef<PlanDemandLineRow>[]>(() => {
    const columns: ColumnDef<PlanDemandLineRow>[] = [
      {
        id: 'so_number',
        header: 'Sales order',
        cell: ({ row }) => {
          const l = row.original;
          return l.href ? (
            <a className="hover:underline" href={l.href}>
              {l.so_number ?? 'Not numbered'}
            </a>
          ) : (
            (l.so_number ?? 'Not numbered')
          );
        },
        footer: () => TOTAL_LABEL,
        size: 130,
        meta: { skeleton: SKELETON_CELL },
      },
    ];
    if (channel === 'need') {
      // AC-B2: which channel each line came off, since the two now sit in one table.
      columns.push({
        id: 'channel',
        header: 'Channel',
        cell: ({ row }) => (row.original.channel === 'project' ? 'Project' : 'Retail'),
        size: 90,
      });
    }
    columns.push(
      {
        id: 'customer',
        header: 'Customer',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer ?? undefined}>
            {textCell(row.original.customer)}
          </span>
        ),
        size: 170,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'project',
        header: 'Project',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.project ?? undefined}>
            {textCell(row.original.project)}
          </span>
        ),
        size: 170,
      },
      {
        id: 'agent',
        header: 'Agent',
        cell: ({ row }) => textCell(row.original.agent),
        size: 90,
      },
      {
        id: 'price',
        header: 'Price',
        cell: ({ row }) => moneyCell(row.original.price),
        size: 100,
        meta: RIGHT,
      },
      {
        id: 'open_qty',
        header: 'Open',
        cell: ({ row }) => fmtInt(row.original.open_qty),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'qty',
        header: 'Balance',
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(total),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'required_date',
        header: 'Required',
        cell: ({ row }) => fmtDate(row.original.required_date),
        size: 110,
        meta: RIGHT,
      },
    );
    return columns;
  }, [total, channel]);

  const historyColumns = useMemo<ColumnDef<PlanHistoryPoint>[]>(() => {
    const columns: ColumnDef<PlanHistoryPoint>[] = [
      {
        id: 'month',
        header: 'Month',
        cell: ({ row }) => monthLabel(row.original.month),
        footer: () => TOTAL_LABEL,
        size: 110,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'project_qty',
        header: 'Project',
        cell: ({ row }) => (
          <PeakValueCell
            qty={row.original.project_qty}
            isPeak={row.index === projectPeakIndex}
            mark="project"
          />
        ),
        footer: () => fmtInt(projectTotal),
        size: 100,
        meta: RIGHT,
      },
      {
        id: 'retail_qty',
        header: 'Retail',
        cell: ({ row }) => (
          <PeakValueCell
            qty={row.original.retail_qty}
            isPeak={row.index === retailPeakIndex}
            mark="retail"
          />
        ),
        footer: () => fmtInt(retailTotal),
        size: 100,
        meta: RIGHT,
      },
    ];
    if (channel === 'need') {
      // AC-B3: the Need dialog's own column, one column no channel dialog needs.
      columns.push({
        id: 'total_qty',
        header: 'Total',
        cell: ({ row }) => (
          <PeakValueCell
            qty={row.original.project_qty + row.original.retail_qty}
            isPeak={row.index === totalPeakIndex}
            mark="total"
          />
        ),
        footer: () => fmtInt(projectTotal + retailTotal),
        size: 100,
        meta: RIGHT,
      });
    }
    return columns;
  }, [channel, projectPeakIndex, retailPeakIndex, totalPeakIndex, projectTotal, retailTotal]);

  return (
    <Tabs defaultValue={initialTab}>
      <TabsList variant="line">
        <TabsTrigger value="open">{openLabel}</TabsTrigger>
        <TabsTrigger value="history">12-month history</TabsTrigger>
      </TabsList>

      <TabsContent value="open">
        <DrillTable
          columns={openColumns}
          rows={lines}
          getRowId={(l, i) => `${l.so_number ?? 'unnumbered'}-${i}`}
          isLoading={loading}
          emptyMessage="Nothing open on this channel for this product."
          // ProjectRetailTabs is only ever rendered inside PlanRowDialog's DialogBody
          // (overflow-y-auto).
          scrollerMaxHeight={false}
        />
      </TabsContent>

      <TabsContent value="history">
        <DrillTable
          columns={historyColumns}
          rows={history}
          getRowId={(p) => p.month}
          isLoading={loading}
          emptyMessage="Nothing was ordered in the last twelve months."
          scrollerMaxHeight={false}
        />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// On hand - the site pools' stock, row by row, with the documents under each
// ---------------------------------------------------------------------------

/** One site-pool stock row, as `useLocationStock` returns it. */
interface OnHandLocation {
  warehouse_id: string;
  warehouse_code: string | null;
  on_hand: number;
  reserved: number;
  free: number;
  so_qty: number;
  spo_qty: number;
  available: number;
  is_pool?: boolean;
  po_qty?: number | null;
}

/**
 * The container-request grid's On hand lightbox and the SPO planner's own (AC-B3 / AC-G3),
 * each expanding to the documents standing behind that location.
 *
 * `scope` has NO default on purpose - a caller states which cell it is footing rather than
 * silently inheriting whatever this file's last edit left in place:
 *
 * - `'all'` (both current callers - `ContainerRequestSection`, `SpoPlannerTable`): EVERY
 *   active location, site pool and project bin alike (R7, captain 8 Sep 2026, reverses
 *   F2/26 Aug). Those two cells' own netting widened to count a project bin's stock -
 *   `container_request_service._stock_context`'s docstring is the record - because the
 *   demand side already counts the project need that stock covers; a lightbox that still
 *   dropped the bin would stop footing the cell it opened from.
 * - `'pools'`: the SITE POOL rows only. Nothing in this file currently asks for it - the
 *   REORDER screen's own On hand lightbox never widened, but it keeps a private, unexported
 *   `OnHandTable` in `reorder/components/PlanRowDialogs.tsx` rather than importing this one,
 *   so `'pools'` exists here for completeness and any future caller that genuinely needs the
 *   old rule, not for a caller that exists today.
 *
 * A response with no pool row at all, under `'pools'`, falls back to everything it was
 * given, rather than showing an empty table for a product that plainly has stock somewhere.
 *
 * Builds its own `useReactTable` rather than going through `DrillTable`: the expanding row
 * needs TanStack's own expanded-row state, which `DrillTable`'s callers never do.
 */
export function OnHandTable({
  productId,
  scope,
}: {
  productId: string;
  scope: 'all' | 'pools';
}) {
  const stock = useLocationStock(productId, Boolean(productId));
  const [openRow, setOpenRow] = useState<string | null>(null);

  const rows = useMemo(() => {
    const locations = (stock.data?.locations ?? []) as OnHandLocation[];
    if (scope === 'pools') {
      const pools = locations.filter((l) => l.is_pool);
      return pools.length ? pools : locations;
    }
    return locations;
  }, [stock.data, scope]);

  const total = rows.reduce((sum, l) => sum + (l.on_hand || 0), 0);
  const footerLabel = scope === 'pools' ? 'Site pools' : 'Every active location';

  const columns = useMemo<ColumnDef<OnHandLocation>[]>(
    () => [
      {
        id: 'expand',
        header: '',
        cell: ({ row }) =>
          row.getIsExpanded() ? (
            <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
          ) : (
            <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />
          ),
        size: 32,
        enableResizing: false,
      },
      {
        id: 'location',
        header: 'Location',
        cell: ({ row }) => (
          <span title={row.original.warehouse_code ?? undefined}>
            {textCell(row.original.warehouse_code)}
          </span>
        ),
        footer: () => <span className="text-muted-foreground">{footerLabel}</span>,
        size: 120,
        meta: {
          skeleton: SKELETON_CELL,
          expandedContent: (loc: OnHandLocation) => (
            <StockDocumentsPanel productId={productId} warehouseId={loc.warehouse_id} />
          ),
        },
      },
      {
        id: 'on_hand',
        header: 'On hand',
        cell: ({ row }) => fmtInt(row.original.on_hand),
        footer: () => fmtInt(total),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'reserved',
        header: 'Reserved',
        cell: ({ row }) => fmtInt(row.original.reserved),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'free',
        header: 'Free',
        cell: ({ row }) => fmtInt(row.original.free),
        size: 80,
        meta: RIGHT,
      },
      {
        id: 'so_qty',
        header: 'SO qty',
        cell: ({ row }) => fmtInt(row.original.so_qty),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'spo_qty',
        header: 'SPO qty',
        cell: ({ row }) => fmtInt(row.original.spo_qty),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'available',
        header: 'Available',
        cell: ({ row }) => (
          <span className={cn(row.original.available < 0 && 'text-destructive')}>
            {fmtInt(row.original.available)}
          </span>
        ),
        size: 100,
        meta: RIGHT,
      },
      {
        id: 'po_qty',
        // `po_qty` arrives with the reorder lane's own extension of this endpoint; until it
        // merges the column reads as "not stated", never as zero.
        header: 'PO qty',
        cell: ({ row }) =>
          row.original.po_qty === null || row.original.po_qty === undefined ? (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ) : (
            fmtInt(row.original.po_qty)
          ),
        size: 90,
        meta: RIGHT,
      },
    ],
    [productId, total, footerLabel],
  );

  const expanded: ExpandedState = openRow ? { [openRow]: true } : {};
  const onExpandedChange: OnChangeFn<ExpandedState> = (updater) => {
    const next = typeof updater === 'function' ? updater(expanded) : updater;
    const openIds = Object.keys(next).filter((id) => (next as Record<string, boolean>)[id]);
    setOpenRow(openIds[0] ?? null);
  };

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (l) => l.warehouse_id,
    state: { expanded },
    onExpandedChange,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  return (
    <div className="space-y-2">
      <DataGrid
        table={table}
        recordCount={rows.length}
        isLoading={stock.isLoading}
        listingKey={null}
        tableLayout={{
          width: 'fixed',
          columnsResizable: true,
          // Always rendered inside PlanRowDialog's own DialogBody (overflow-y-auto).
          scrollerMaxHeight: false,
        }}
        onRowClick={(loc) => setOpenRow((cur) => (cur === loc.warehouse_id ? null : loc.warehouse_id))}
        emptyMessage="No stock rows for this product."
      >
        <DataGridTable />
      </DataGrid>
      {/* The newest stock timestamp for the product, never the moment this dialog asked. */}
      {stock.data?.as_of ? (
        <p className="text-2xs text-muted-foreground">
          Stock as of {formatDateTimeInMalaysia(stock.data.as_of)}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SPO - what is on the water for the site pools
// ---------------------------------------------------------------------------

/** Columns shared by the open and history tabs - only the footer total differs. */
function spoColumns(totalQty: number): ColumnDef<ContainerRequestDrillSpoRow>[] {
  return [
    {
      id: 'spo_number',
      header: 'SPO',
      cell: ({ row }) => textCell(row.original.spo_number),
      footer: () => TOTAL_LABEL,
      size: 140,
      meta: { skeleton: SKELETON_CELL },
    },
    {
      // S4: what "Packing list" used to say (Draft / Not shipped) is now the status pill;
      // this column is only ever the container the SPO landed on.
      id: 'container',
      header: 'Container',
      cell: ({ row }) => textCell(row.original.container_number),
      size: 140,
    },
    {
      id: 'to',
      header: 'To',
      cell: ({ row }) => textCell(row.original.warehouse_code),
      size: 90,
    },
    {
      id: 'qty',
      header: 'Qty',
      cell: ({ row }) => fmtInt(row.original.qty),
      footer: () => fmtInt(totalQty),
      size: 90,
      meta: RIGHT,
    },
    {
      id: 'received',
      header: 'Received',
      cell: ({ row }) => fmtInt(row.original.received),
      size: 100,
      meta: RIGHT,
    },
    {
      id: 'eta',
      header: 'ETA',
      cell: ({ row }) => fmtDate(row.original.eta),
      size: 100,
      meta: RIGHT,
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => {
        const r = row.original;
        // An allocation nobody has put on a shipment yet has no status word to format -
        // "Not shipped" says the same thing the Container column's own dash used to.
        if (!r.shipment_id) {
          return (
            <Badge variant="secondary" appearance="light" size="md">
              Not shipped
            </Badge>
          );
        }
        return (
          <Badge variant={getStatusBadgeVariant(r.status)} appearance="light" size="md">
            {formatStatusLabel(r.status)}
          </Badge>
        );
      },
      size: 130,
    },
  ];
}

function spoRowId(r: ContainerRequestDrillSpoRow, i: number): string {
  return `${r.spo_number ?? 'unnumbered'}-${r.shipment_id}-${i}`;
}

/**
 * The shipping orders behind the SPO cell (AC-B4), open first and then what has landed.
 *
 * Rows come from `/container-requests/drill?kind=spo`, whose total IS the cell - see that
 * service's docstring for why the reader is `spo_allocations` and not the purchase-order
 * table (migration 420 moved every SPO document out of it). The open tab foots to that same
 * total; the history tab has none to defer to, so it sums its own rows (AC-J3).
 *
 * EVERY active location, site pool and project bin alike (R7, captain 8 Sep 2026, reverses
 * F2/26 Aug) - `container_request_drill._spo_rows`' docstring is the record. Both current
 * callers (`ContainerRequestSection`, `SpoPlannerTable`) open this off a cell that widened
 * the same way, so there is no narrower variant to choose between here.
 */
export function SpoTabs({ supplierId, productId }: { supplierId: string; productId: string }) {
  const drill = useContainerRequestDrill(supplierId, productId, 'spo');
  const open = (drill.data?.rows ?? []) as ContainerRequestDrillSpoRow[];
  const history = (drill.data?.history ?? []) as ContainerRequestDrillSpoRow[];

  const openTotal = drill.data?.total ?? open.reduce((s, r) => s + r.qty, 0);
  const historyTotal = history.reduce((s, r) => s + r.qty, 0);
  const openColumns = useMemo(() => spoColumns(openTotal), [openTotal]);
  const historyColumns = useMemo(() => spoColumns(historyTotal), [historyTotal]);

  return (
    <Tabs defaultValue="open">
      <TabsList variant="line">
        {/* S3: the count of rows read as "how many documents", when the number the cell
            actually names is the quantity they carry. Plain "Open" (R7, captain 8 Sep
            2026), never "to pools" - a row here may be on its way to a project bin. */}
        <TabsTrigger value="open">{`Open (${fmtInt(openTotal)})`}</TabsTrigger>
        <TabsTrigger value="history">{`History (${fmtInt(historyTotal)})`}</TabsTrigger>
      </TabsList>
      <TabsContent value="open">
        <DrillTable
          columns={openColumns}
          rows={open}
          getRowId={spoRowId}
          isLoading={drill.isLoading}
          emptyMessage={NO_SPO_TO_POOL}
          // SpoTabs is only ever rendered inside a lightbox's own scrolling DialogBody.
          scrollerMaxHeight={false}
        />
      </TabsContent>
      <TabsContent value="history">
        <DrillTable
          columns={historyColumns}
          rows={history}
          getRowId={spoRowId}
          isLoading={drill.isLoading}
          emptyMessage="No shipping order has landed here for this product."
          scrollerMaxHeight={false}
        />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// Incoming PL - packing lists on their way, reference only
// ---------------------------------------------------------------------------

/**
 * The packing lists behind the Incoming PL cell (AC-B4). One table, no tabs: a packing list
 * that has arrived is already the On hand dialog, so there is no landed half to show.
 */
export function IncomingPlTable({
  supplierId,
  productId,
  onOpenShipment,
}: {
  supplierId: string;
  productId: string;
  /** Opens the packing list. Absent = the number is plain text. */
  onOpenShipment?: (shipmentId: string) => void;
}) {
  const drill = useContainerRequestDrill(supplierId, productId, 'incoming_pl');
  const rows = (drill.data?.rows ?? []) as ContainerRequestDrillIncomingPlRow[];
  const total = drill.data?.total ?? rows.reduce((s, r) => s + r.qty, 0);

  const columns = useMemo<ColumnDef<ContainerRequestDrillIncomingPlRow>[]>(
    () => [
      {
        // S5: the Packing list column is gone - the Container cell keeps the same link, and
        // a row with no container number yet still reads a dash rather than losing the door
        // onto the packing list.
        id: 'container_number',
        header: 'Container',
        cell: ({ row }) => {
          const r = row.original;
          const label = r.container_number ?? EM_DASH;
          return onOpenShipment ? (
            <button
              type="button"
              className="underline-offset-2 hover:underline"
              onClick={() => onOpenShipment(r.shipment_id)}
            >
              {label}
            </button>
          ) : (
            label
          );
        },
        footer: () => TOTAL_LABEL,
        size: 150,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'supplier_name',
        header: 'Supplier',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.supplier_name ?? undefined}>
            {textCell(row.original.supplier_name)}
          </span>
        ),
        size: 190,
      },
      {
        id: 'qty',
        header: 'Qty',
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(total),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'eta',
        header: 'ETA',
        cell: ({ row }) => fmtDate(row.original.eta),
        size: 100,
        meta: RIGHT,
      },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => (
          <Badge variant={getStatusBadgeVariant(row.original.status)} appearance="light" size="md">
            {formatStatusLabel(row.original.status)}
          </Badge>
        ),
        size: 120,
      },
    ],
    [onOpenShipment, total],
  );

  return (
    <DrillTable
      columns={columns}
      rows={rows}
      getRowId={(r) => r.shipment_id}
      isLoading={drill.isLoading}
      emptyMessage="Nothing is on its way on a packing list for this product."
      // IncomingPlTable is only ever rendered inside a lightbox's own scrolling DialogBody.
      scrollerMaxHeight={false}
    />
  );
}

// ---------------------------------------------------------------------------
// PO - what is already ordered, and what was ordered before
// ---------------------------------------------------------------------------

/**
 * Columns shared by the open and history tabs. `tab` decides two things that must agree with
 * each other and with the tab's own label: which column carries the footer total (still-to-come
 * on Open, qty-ordered on History - History's own `still_to_come` is always 0 on a closed line,
 * so summing it there would read "Total outstanding 0" under a tab that already says otherwise)
 * and whether a line counts as on order for the status pill. The backend's History predicate
 * (`line_status <> 'open' OR qty_ordered <= qty_received`) leaves `still_to_come` nonzero on a
 * line that closed short, so History never derives the pill off it - every History row reads
 * Completed (or Cancelled/Draft off its own status).
 */
function poColumns(
  tab: 'open' | 'history',
  total: number,
): ColumnDef<ContainerRequestDrillPoRow>[] {
  const footerLabel = tab === 'open' ? 'Total outstanding' : 'Total ordered';
  return [
    {
      id: 'po_number',
      header: 'PO',
      cell: ({ row }) => textCell(row.original.po_number),
      // S6: the purchase-order list's own word for this figure.
      footer: () => <span className="text-muted-foreground">{footerLabel}</span>,
      size: 120,
      meta: { skeleton: SKELETON_CELL },
    },
    {
      id: 'supplier_name',
      header: 'Supplier',
      cell: ({ row }) => (
        <span className="block truncate" title={row.original.supplier_name ?? undefined}>
          {textCell(row.original.supplier_name)}
        </span>
      ),
      size: 170,
    },
    {
      id: 'qty_ordered',
      header: 'Qty',
      cell: ({ row }) => fmtInt(row.original.qty_ordered),
      footer: tab === 'history' ? () => fmtInt(total) : undefined,
      size: 90,
      meta: RIGHT,
    },
    {
      id: 'still_to_come',
      header: 'Outstanding',
      cell: ({ row }) => fmtInt(row.original.still_to_come),
      footer: tab === 'open' ? () => fmtInt(total) : undefined,
      size: 110,
      meta: RIGHT,
    },
    {
      id: 'unit_price',
      header: 'Unit price',
      cell: ({ row }) =>
        row.original.unit_price === null ? (
          <span className="text-muted-foreground">{EM_DASH}</span>
        ) : (
          fmtSupplierCost(row.original.unit_price, row.original.currency)
        ),
      size: 120,
      meta: RIGHT,
    },
    {
      id: 'issued',
      header: 'Issued',
      cell: ({ row }) => fmtDate(row.original.issued),
      size: 100,
      meta: RIGHT,
    },
    {
      id: 'eta',
      // S6: the purchase-order list heads this "Delivery date" - "ETA" was the only column
      // in the family still using the word.
      header: 'Delivery date',
      cell: ({ row }) => fmtDate(row.original.eta),
      size: 120,
      meta: RIGHT,
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => {
        const r = row.original;
        // The same pill the purchase-order list itself wears (S6): Outstanding / Completed
        // is DERIVED off `still_to_come`, not the raw stored status word - but only on the
        // Open tab, where `still_to_come > 0` means what it says. History rows can close short
        // (`still_to_come` stays nonzero) and still belong under a tab called History, so
        // History never counts as on order.
        const pill = purchaseOrderStatusPill({
          status: r.status ?? '',
          is_on_order: tab === 'open' && r.still_to_come > 0,
        });
        return (
          <Badge variant={pill.variant} appearance="light" size="md">
            {pill.label}
          </Badge>
        );
      },
      size: 110,
    },
  ];
}

function poRowId(r: ContainerRequestDrillPoRow, i: number): string {
  return `${r.purchase_order_id}-${i}`;
}

/** The purchase-order lines behind the PO cell (AC-B4): open first, then the last 12 months. */
export function PoTabs({ supplierId, productId }: { supplierId: string; productId: string }) {
  const drill = useContainerRequestDrill(supplierId, productId, 'po');
  const open = (drill.data?.rows ?? []) as ContainerRequestDrillPoRow[];
  const history = (drill.data?.history ?? []) as ContainerRequestDrillPoRow[];

  const openStillToCome = drill.data?.total ?? open.reduce((s, r) => s + r.still_to_come, 0);
  // S3: the History tab names the quantity that WAS ordered, not what is still owed on it -
  // a closed PO's still-to-come is always 0, so that sum would read "History (0)" forever. The
  // footer sums the same figure (fix round, Opus review), so the tab label and the footer never
  // disagree on a closed book.
  const historyQtyOrdered = history.reduce((s, r) => s + r.qty_ordered, 0);
  const openColumns = useMemo(() => poColumns('open', openStillToCome), [openStillToCome]);
  const historyColumns = useMemo(
    () => poColumns('history', historyQtyOrdered),
    [historyQtyOrdered],
  );

  return (
    <Tabs defaultValue="open">
      <TabsList variant="line">
        <TabsTrigger value="open">{`Open (${fmtInt(openStillToCome)})`}</TabsTrigger>
        <TabsTrigger value="history">{`History (${fmtInt(historyQtyOrdered)})`}</TabsTrigger>
      </TabsList>
      <TabsContent value="open">
        <DrillTable
          columns={openColumns}
          rows={open}
          getRowId={poRowId}
          isLoading={drill.isLoading}
          emptyMessage="Nothing is on order for this product."
          // PoTabs is only ever rendered inside a lightbox's own scrolling DialogBody.
          scrollerMaxHeight={false}
        />
      </TabsContent>
      <TabsContent value="history">
        <DrillTable
          columns={historyColumns}
          rows={history}
          getRowId={poRowId}
          isLoading={drill.isLoading}
          emptyMessage="No purchase order in the last twelve months names this product."
          scrollerMaxHeight={false}
        />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// SPO planner - the two pickers (R21, AC-G1/AC-G2)
// ---------------------------------------------------------------------------

/** One field a picker's advanced filter can match on (S3). */
interface PickerFilterField {
  id: string;
  label: string;
  options: { value: string; label: string }[];
}

/** One condition row in a picker's Filters popover - field + value, ANDed with the rest. */
interface PickerFilterCondition {
  id: string;
  field: string;
  value: string;
}

/**
 * The unique, sorted values a field's own value select offers - built off the picker's WHOLE
 * row set, never the currently-shown one, so a condition someone is about to add still offers
 * every choice rather than narrowing itself the moment a search or another condition applies.
 */
function distinctFieldOptions<T>(
  rows: T[],
  pick: (row: T) => string | null | undefined,
): { value: string; label: string }[] {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const row of rows) {
    const v = pick(row);
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push({ value: v, label: v });
  }
  out.sort((a, b) => a.label.localeCompare(b.label));
  return out;
}

/**
 * The advanced-filter condition editor a picker's `Filters` popover renders (S3): field select,
 * value select, remove - the same shape the users list's own "Advanced filters" popover uses
 * (`user-list.tsx`), parameterised on `fields` so the SO and PO pickers share one implementation
 * rather than two copies that drift the first time one of them changes.
 */
function PickerFilterConditions({
  fields,
  conditions,
  onChange,
  onApply,
  onClear,
}: {
  fields: PickerFilterField[];
  conditions: PickerFilterCondition[];
  onChange: (next: PickerFilterCondition[]) => void;
  onApply: () => void;
  onClear: () => void;
}) {
  const addCondition = () =>
    onChange([...conditions, { id: crypto.randomUUID(), field: fields[0]?.id ?? '', value: '' }]);
  const updateCondition = (id: string, patch: Partial<PickerFilterCondition>) =>
    onChange(conditions.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  const removeCondition = (id: string) => onChange(conditions.filter((c) => c.id !== id));

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">Advanced filters</p>
      <div className="space-y-2">
        {conditions.map((cond) => {
          const field = fields.find((f) => f.id === cond.field) ?? fields[0];
          return (
            <div key={cond.id} className="grid grid-cols-[1fr_1fr_auto] gap-2">
              <SearchableSelect
                value={cond.field}
                onChange={(v) => updateCondition(cond.id, { field: v, value: '' })}
                options={fields.map((f) => ({ value: f.id, label: f.label }))}
              />
              <SearchableSelect
                value={cond.value}
                onChange={(v) => updateCondition(cond.id, { value: v })}
                options={field?.options ?? []}
                placeholder="Any"
                clearable
              />
              <Button
                type="button"
                mode="icon"
                variant="ghost"
                onClick={() => removeCondition(cond.id)}
                aria-label="Remove filter condition"
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          );
        })}
      </div>
      <div className="flex gap-2">
        <Button type="button" variant="outline" size="sm" className="flex-1" onClick={addCondition}>
          <Plus className="size-4" />
          Add condition
        </Button>
        <Button type="button" variant="outline" size="sm" className="flex-1" onClick={onApply}>
          Apply
        </Button>
      </div>
      <Button variant="outline" size="sm" className="w-full" onClick={onClear}>
        Clear filters
      </Button>
    </div>
  );
}

/** One PO this SPO can draw from. Structurally the planner's own `SpoPoTake`. */
export interface PoTakeRow {
  po_line_id: string;
  po_number: string | null;
  supplier_name: string | null;
  /** The PO's own document date, distinct from `expected_date` (when the line is due). */
  po_date: string | null;
  expected_date: string | null;
  /** What the cascade took from this line. */
  qty: number;
  /** What the line has open, which is what it could give if a neighbour were unticked. */
  open_qty: number;
  /** How much of this line an EARLIER SPO already pulled, and its number(s) - oldest first
   *  (S5). `qty === 0 && taken_qty > 0` is a TAKEN row: never this cascade's own take, never
   *  tickable. */
  taken_qty: number;
  taken_by: string[];
}

/**
 * Which POs this SPO draws from, oldest DOCUMENT first (Q8, AC-G1), each one tickable and
 * the suggested takes pre-ticked.
 *
 * Controlled and fetch-free on purpose: the SPO planner already holds `po_takes` in its
 * payload and owns the cascade that re-walks the takes when a tick changes. A picker that
 * fetched would be a second opinion about the same rows.
 */
export function PoTakesPicker({
  takes,
  tickedIds,
  onChange,
  coveredQty,
  packedQty,
  bucketHits,
}: {
  takes: PoTakeRow[];
  tickedIds: string[];
  onChange: (ids: string[]) => void;
  /** What the ticked takes cover, for the footer. */
  coveredQty: number;
  /** What the shipment line packs, for the footer. */
  packedQty: number;
  /**
   * PO line ids whose date fell in the schedule week that opened this picker (S4, AC-D3) -
   * those rows carry `data-bucket-hit` and the `bg-primary/10` row tint, so the click reads
   * as "these rows" once the dialog opens. `DataGrid`'s `rowClassName`/`rowAttributes` are
   * the per-row hook this reads through.
   */
  bucketHits?: Set<string>;
}) {
  const toggle = (id: string, on: boolean) =>
    onChange(on ? [...tickedIds, id] : tickedIds.filter((x) => x !== id));

  // S3: search + filter, over the FULL `takes` array - ticks and the footer's totals never
  // move with them (AC-C5); only which rows the grid shows does.
  // S5: a row `qty === 0 && taken_qty > 0` is occupied by ANOTHER SPO entirely - never this
  // cascade's own take, so it renders grey and its checkbox is always unticked and disabled.
  const isTaken = (t: PoTakeRow) => t.qty === 0 && t.taken_qty > 0;
  const [searchInput, setSearchInput] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [draftConditions, setDraftConditions] = useState<PickerFilterCondition[]>([]);
  const [appliedConditions, setAppliedConditions] = useState<PickerFilterCondition[]>([]);

  const fields = useMemo<PickerFilterField[]>(
    () => [
      { id: 'po_number', label: 'PO', options: distinctFieldOptions(takes, (t) => t.po_number) },
      {
        id: 'supplier_name',
        label: 'Supplier',
        options: distinctFieldOptions(takes, (t) => t.supplier_name),
      },
    ],
    [takes],
  );

  const rows = useMemo(() => {
    let out = takes;
    const needle = appliedSearch.trim().toLowerCase();
    if (needle) {
      out = out.filter(
        (t) =>
          (t.po_number ?? '').toLowerCase().includes(needle) ||
          (t.supplier_name ?? '').toLowerCase().includes(needle),
      );
    }
    for (const cond of appliedConditions) {
      if (!cond.value) continue;
      if (cond.field === 'po_number') out = out.filter((t) => t.po_number === cond.value);
      if (cond.field === 'supplier_name') out = out.filter((t) => t.supplier_name === cond.value);
    }
    return out;
  }, [takes, appliedSearch, appliedConditions]);

  const activeCount = appliedConditions.filter((c) => c.value).length;
  const isFiltered = Boolean(appliedSearch.trim()) || activeCount > 0;
  // F5 (review round): the footer's denominator is how many POs can actually be TICKED,
  // not every row the grid draws - a taken row is greyed and its checkbox is disabled, so
  // counting it made "N of M POs" read as though M included POs nobody could ever tick.
  const tickableCount = useMemo(() => takes.filter((t) => !isTaken(t)).length, [takes]);

  const columns = useMemo<ColumnDef<PoTakeRow>[]>(
    () => [
      {
        id: 'select',
        header: '',
        cell: ({ row }) => {
          const t = row.original;
          const taken = isTaken(t);
          return (
            <Checkbox
              checked={!taken && tickedIds.includes(t.po_line_id)}
              disabled={taken}
              onCheckedChange={(checked) => toggle(t.po_line_id, !!checked)}
              aria-label={`Draw from ${t.po_number ?? t.po_line_id}`}
            />
          );
        },
        size: 40,
        enableResizing: false,
      },
      {
        id: 'po_number',
        header: 'PO',
        cell: ({ row }) => textCell(row.original.po_number),
        footer: () => TOTAL_LABEL,
        size: 130,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'supplier_name',
        header: 'Supplier',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.supplier_name ?? undefined}>
            {textCell(row.original.supplier_name)}
          </span>
        ),
        size: 170,
      },
      {
        id: 'po_date',
        header: 'Doc date',
        cell: ({ row }) => fmtDate(row.original.po_date),
        size: 100,
        meta: RIGHT,
      },
      {
        // S3: the family's own word for this column (`PoTabs`, the purchase-order list).
        id: 'expected_date',
        header: 'Delivery date',
        cell: ({ row }) => fmtDate(row.original.expected_date),
        size: 120,
        meta: RIGHT,
      },
      {
        id: 'open_qty',
        header: 'Outstanding',
        cell: ({ row }) => fmtInt(row.original.open_qty),
        size: 110,
        meta: RIGHT,
      },
      {
        // S5: what an EARLIER SPO already pulled off this line - dash when none, the SPO
        // number(s) as the tooltip AND under the figure, so the reader never has to hover
        // to see who has it.
        id: 'taken_qty',
        header: 'Taken',
        cell: ({ row }) => {
          const t = row.original;
          if (!(t.taken_qty > 0)) return <span className="tabular-nums">{EM_DASH}</span>;
          const names = t.taken_by.join(', ');
          return (
            <div className="flex flex-col items-end" title={names}>
              <span className="tabular-nums">{fmtInt(t.taken_qty)}</span>
              <span className="truncate text-2xs text-muted-foreground">{names}</span>
            </div>
          );
        },
        size: 110,
        meta: RIGHT,
      },
      {
        // S5: renamed from "Taken" (this cascade's own draw) to avoid reading like the
        // column above it - "This SPO" matches the schedule legend's own words.
        id: 'qty',
        header: 'This SPO',
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(coveredQty),
        size: 90,
        meta: RIGHT,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tickedIds, coveredQty],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (t) => t.po_line_id,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="space-y-2">
      <DataGridListToolbar
        table={table}
        searchSlot={
          <ListSearchInput
            value={searchInput}
            onChange={(next) => {
              setSearchInput(next);
              if (!next) setAppliedSearch('');
            }}
            onSubmit={() => setAppliedSearch(searchInput)}
            placeholder="Search POs"
            className="w-full sm:w-64"
          />
        }
        filters={{
          kind: 'custom',
          active: activeCount > 0,
          activeCount,
          content: (
            <PickerFilterConditions
              fields={fields}
              conditions={draftConditions}
              onChange={setDraftConditions}
              onApply={() => setAppliedConditions(draftConditions)}
              onClear={() => {
                setDraftConditions([]);
                setAppliedConditions([]);
              }}
            />
          ),
        }}
        exportConfig={false}
        showColumns={false}
      />
      <DataGrid
        table={table}
        recordCount={rows.length}
        listingKey={null}
        tableLayout={{
          width: 'fixed',
          columnsResizable: true,
          // Always rendered inside PlanRowDialog's own DialogBody (overflow-y-auto).
          scrollerMaxHeight: false,
        }}
        rowClassName={(t) =>
          cn(
            isTaken(t) && 'text-muted-foreground',
            bucketHits?.has(t.po_line_id) && 'bg-primary/10',
          )
        }
        rowAttributes={(t) => ({
          ...(isTaken(t) ? { 'data-taken': 'true' } : {}),
          ...(bucketHits?.has(t.po_line_id) ? { 'data-bucket-hit': 'true' } : {}),
        })}
        emptyMessage="No open PO can back this line."
      >
        <DataGridTable />
      </DataGrid>
      <p className="border-t pt-2 text-2xs text-muted-foreground">
        {`${fmtInt(tickedIds.length)} of ${fmtInt(tickableCount)} POs · covers ${fmtInt(coveredQty)} of packed ${fmtInt(packedQty)}`}
        {isFiltered ? ` · ${fmtInt(rows.length)} of ${fmtInt(takes.length)} shown` : ''}
      </p>
    </div>
  );
}

/** One piece of demand this SPO could cover. Structurally the planner's `SpoCoverageLine`. */
export interface SoCoverageRow {
  key: string;
  kind: 'project' | 'retail';
  document: string | null;
  customer_name: string | null;
  required_date: string | null;
  qty: number;
  warehouse_code: string | null;
  /** How much of this row an EARLIER SPO already covers, and its number(s) - oldest first
   *  (S5). `qty === 0 && taken_qty > 0` is a TAKEN row: never tickable. */
  taken_qty: number;
  taken_by: string[];
  /** `sales_orders.demand_class` as stored - what the SO ITSELF is classified as, distinct
   *  from `kind` (which family the row came from). A project row is always `'project'`; a
   *  book line carries whatever its own sales order was stamped, including `null` (R3). */
  demand_class: 'project' | 'retail' | null;
}

/**
 * Which demand this SPO is pointed at (Q4, AC-G2): project rows first, then retail, in the
 * order the server walked them, pre-ticked to the packed quantity.
 *
 * What no tick claims is stated as Unassigned rather than quietly attached to the first order
 * in the list. Controlled and fetch-free for the same reason as `PoTakesPicker`.
 */
export function SoCoveragePicker({
  coverage,
  tickedKeys,
  onChange,
  unassigned,
  takes,
  onTakeChange,
  spoQty,
  bucketHits,
  readOnly,
  onOrderChange,
}: {
  coverage: SoCoverageRow[];
  tickedKeys: string[];
  onChange: (keys: string[]) => void;
  unassigned: number;
  /** What each ticked row actually GETS out of this SPO, by `key` (AC-I2's Take column).
   *  Omitted by a caller that holds no walk - the column then does not render at all,
   *  rather than reading 0 for every row and being mistaken for one. */
  takes?: Record<string, number>;
  /** Editing a Take cell (R19) - present together with `takes` turns the column from a
   *  plain figure into a numeric input; absent (or `readOnly`) leaves it read-only. */
  onTakeChange?: (key: string, qty: number) => void;
  /** The SPO line's own quantity - only needed alongside `onTakeChange`, for the "total
   *  taken exceeds the SPO qty" refusal (AC-I2). */
  spoQty?: number;
  /** SO coverage keys whose date fell in the schedule week that opened this picker (S4,
   *  AC-D3) - see `PoTakesPicker`'s own doc for the row hook this reads through. */
  bucketHits?: Set<string>;
  /** J2: the SPO document's "SO covered" lightbox reuses this picker to LIST what a line
   *  already covers rather than tick anything - no select column, no Take input, and the
   *  footer states a count instead of "Unassigned" (there is nothing left to assign, it is
   *  already committed). */
  readOnly?: boolean;
  /** The order the rows are CURRENTLY shown in, project tab then retail tab, each in its
   *  own active sort (S7, review round 1). The caller's cascade walks this, so "the rows
   *  after the one I edited" means the rows after it ON SCREEN - which is the only order
   *  the operator can see. Reported on mount and on every re-sort. */
  onOrderChange?: (keys: string[]) => void;
}) {
  const editable = !readOnly && Boolean(onTakeChange) && Boolean(takes);

  // S5: a row `qty === 0 && taken_qty > 0` is covered by ANOTHER SPO entirely - never
  // tickable, so it renders grey and its checkbox is always unticked and disabled.
  const isTaken = (c: SoCoverageRow) => c.qty === 0 && c.taken_qty > 0;

  const totalQty = useMemo(() => coverage.reduce((s, c) => s + c.qty, 0), [coverage]);
  const totalTaken = useMemo(
    () => (takes ? coverage.reduce((s, c) => s + (takes[c.key] ?? 0), 0) : null),
    [coverage, takes],
  );

  // Read by the FROZEN `columns` below (same technique `SpoPlannerTable`'s own qty input
  // uses, and for the same reason): a `columns` array rebuilt whenever a take changes
  // remounts whatever `flexRender` renders for that cell, which drops the Take input's
  // focus and caret after every keystroke.
  const tickedKeysRef = useRef(tickedKeys);
  tickedKeysRef.current = tickedKeys;
  // S1 (review round 1): the footer sums are read through refs too. Frozen inside the
  // columns memo they stated whatever the totals were AT MOUNT, so the Take footer sat at
  // its seeded figure while every cell above it moved.
  const totalQtyRef = useRef(totalQty);
  totalQtyRef.current = totalQty;
  const totalTakenRef = useRef(totalTaken);
  totalTakenRef.current = totalTaken;
  const takesRef = useRef(takes);
  takesRef.current = takes;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const onTakeChangeRef = useRef(onTakeChange);
  onTakeChangeRef.current = onTakeChange;

  // S3: search + filter, over the FULL `coverage` array - see `PoTakesPicker` for why.
  const [searchInput, setSearchInput] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [draftConditions, setDraftConditions] = useState<PickerFilterCondition[]>([]);
  const [appliedConditions, setAppliedConditions] = useState<PickerFilterCondition[]>([]);
  // R18: one tab per family, the footer shared beneath both.
  const [tab, setTab] = useState<'project' | 'retail'>('project');

  const fields = useMemo<PickerFilterField[]>(
    () => [
      {
        id: 'document',
        label: 'Sales order',
        options: distinctFieldOptions(coverage, (c) => c.document),
      },
      {
        id: 'customer_name',
        label: 'Customer',
        options: distinctFieldOptions(coverage, (c) => c.customer_name),
      },
      {
        id: 'warehouse_code',
        label: 'Location',
        options: distinctFieldOptions(coverage, (c) => c.warehouse_code),
      },
    ],
    [coverage],
  );

  const filtered = useMemo(() => {
    let out = coverage;
    const needle = appliedSearch.trim().toLowerCase();
    if (needle) {
      out = out.filter(
        (c) =>
          (c.document ?? '').toLowerCase().includes(needle) ||
          (c.customer_name ?? '').toLowerCase().includes(needle),
      );
    }
    for (const cond of appliedConditions) {
      if (!cond.value) continue;
      if (cond.field === 'document') out = out.filter((c) => c.document === cond.value);
      if (cond.field === 'customer_name') out = out.filter((c) => c.customer_name === cond.value);
      if (cond.field === 'warehouse_code') out = out.filter((c) => c.warehouse_code === cond.value);
    }
    return out;
  }, [coverage, appliedSearch, appliedConditions]);

  const activeCount = appliedConditions.filter((c) => c.value).length;
  const isFiltered = Boolean(appliedSearch.trim()) || activeCount > 0;

  // R18: partitioned by `kind` (which family the row came from - project rows are always
  // order-inquiry rows, retail rows are always book lines), not `demand_class` (what the
  // SO itself is classified as, which the Class cell still reads and can disagree with
  // `kind` - see that cell's own comment). Each tab lists only its own family.
  const projectRows = useMemo(() => filtered.filter((c) => c.kind === 'project'), [filtered]);
  const retailRows = useMemo(() => filtered.filter((c) => c.kind === 'retail'), [filtered]);
  const projectCount = useMemo(
    () => coverage.filter((c) => c.kind === 'project').length,
    [coverage],
  );
  const retailCount = useMemo(
    () => coverage.filter((c) => c.kind === 'retail').length,
    [coverage],
  );

  const columns = useMemo<ColumnDef<SoCoverageRow>[]>(() => {
    const cols: ColumnDef<SoCoverageRow>[] = [];
    if (!readOnly) {
      cols.push({
        id: 'select',
        header: '',
        cell: ({ row }) => {
          const c = row.original;
          const taken = isTaken(c);
          return (
            <Checkbox
              checked={!taken && tickedKeysRef.current.includes(c.key)}
              disabled={taken}
              onCheckedChange={(checked) =>
                onChangeRef.current(
                  checked
                    ? [...tickedKeysRef.current, c.key]
                    : tickedKeysRef.current.filter((x) => x !== c.key),
                )
              }
              aria-label={`Cover ${c.document ?? c.key}`}
            />
          );
        },
        size: 40,
        enableResizing: false,
        enableSorting: false,
      });
    }
    cols.push(
      {
        id: 'document',
        accessorFn: (c) => c.document ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Sales order" column={column} />,
        cell: ({ row }) => textCell(row.original.document),
        footer: () => TOTAL_LABEL,
        size: 130,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'customer_name',
        accessorFn: (c) => c.customer_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_name ?? undefined}>
            {textCell(row.original.customer_name)}
          </span>
        ),
        size: 170,
      },
      {
        // R3: the SAME pill the sales-order list paints for `demand_class`, not a private
        // Project/Retail string - an inquiry row's own SO has no `demand_class` to read, so
        // it reads "Project" off the row's `demand_class` (always 'project' for that family)
        // with "· inquiry" naming where the row itself came from. Not one of the sortable
        // columns (AC-I4 names the other seven, not this one).
        id: 'kind',
        header: 'Class',
        enableSorting: false,
        cell: ({ row }) => {
          const cls = demandClassBadge(row.original.demand_class);
          return (
            <div className="flex items-center gap-1">
              <Badge variant={cls.variant} appearance="light" size="md">
                {cls.label}
              </Badge>
              {row.original.kind === 'project' ? (
                <span className="text-2xs text-muted-foreground">· inquiry</span>
              ) : null}
            </div>
          );
        },
        size: 130,
      },
      {
        // S3: the family's own word for this column (`PoTabs`, the purchase-order list).
        id: 'required_date',
        accessorFn: (c) => c.required_date ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Delivery date" column={column} />,
        cell: ({ row }) => fmtDate(row.original.required_date),
        size: 120,
        meta: RIGHT,
      },
      {
        id: 'qty',
        accessorFn: (c) => c.qty,
        header: ({ column }) => <DataGridColumnHeader title="Outstanding" column={column} />,
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(totalQtyRef.current),
        size: 110,
        meta: RIGHT,
      },
      {
        // S5: what an EARLIER SPO already covers of this row - dash when none, the SPO
        // number(s) as the tooltip AND under the figure.
        id: 'taken_qty',
        accessorFn: (c) => c.taken_qty,
        header: ({ column }) => <DataGridColumnHeader title="Taken" column={column} />,
        cell: ({ row }) => {
          const c = row.original;
          if (!(c.taken_qty > 0)) return <span className="tabular-nums">{EM_DASH}</span>;
          const names = c.taken_by.join(', ');
          return (
            <div className="flex flex-col items-end" title={names}>
              <span className="tabular-nums">{fmtInt(c.taken_qty)}</span>
              <span className="truncate text-2xs text-muted-foreground">{names}</span>
            </div>
          );
        },
        size: 110,
        meta: RIGHT,
      },
    );
    if (takes) {
      cols.push({
        id: 'take',
        accessorFn: (c) => takesRef.current?.[c.key] ?? 0,
        header: ({ column }) => <DataGridColumnHeader title="Take" column={column} />,
        cell: ({ row }) => {
          const c = row.original;
          const ticked = tickedKeysRef.current.includes(c.key);
          const value = ticked ? (takesRef.current?.[c.key] ?? 0) : 0;
          if (!editable) {
            return <span className="tabular-nums">{fmtInt(value)}</span>;
          }
          const invalid = ticked && value > c.qty;
          return (
            <LiveNumberInput
              min={0}
              step={1}
              rules={{ min: 0, integer: true }}
              className={cn(
                'h-8 w-20 tabular-nums',
                invalid &&
                  'border-destructive text-destructive focus-visible:ring-destructive/40',
              )}
              value={value}
              disabled={!ticked || isTaken(c)}
              aria-label={`Take from ${c.document ?? c.key}`}
              onCommit={(next) => onTakeChangeRef.current?.(c.key, next)}
            />
          );
        },
        footer: () => fmtInt(totalTakenRef.current ?? 0),
        size: 100,
        meta: RIGHT,
      });
    }
    cols.push({
      id: 'warehouse_code',
      accessorFn: (c) => c.warehouse_code ?? '',
      header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
      cell: ({ row }) => textCell(row.original.warehouse_code),
      size: 110,
    });
    return cols;
    // Frozen at mount - `readOnly` and whether `takes` is present never flip for an already
    // mounted picker (the caller either passes a walk or does not); the cells read the live
    // ticks/takes through the refs above instead, same reason `SpoPlannerTable`'s own qty
    // input freezes its `columns`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [projectSorting, setProjectSorting] = useState<SortingState>([
    { id: 'required_date', desc: false },
  ]);
  const [retailSorting, setRetailSorting] = useState<SortingState>([
    { id: 'required_date', desc: false },
  ]);

  const projectTable = useReactTable({
    columns,
    data: projectRows,
    getRowId: (c) => c.key,
    state: { sorting: projectSorting },
    onSortingChange: setProjectSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const retailTable = useReactTable({
    columns,
    data: retailRows,
    getRowId: (c) => c.key,
    state: { sorting: retailSorting },
    onSortingChange: setRetailSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const activeTable = tab === 'project' ? projectTable : retailTable;

  // S7 (review round 1): the order the operator is LOOKING at - project tab first, then
  // retail, each in its own active sort - handed back to the caller so its Take cascade
  // re-flows the rows after the edited one on screen rather than the rows after it in the
  // server's own walk. Reported through a signature so a re-render that changed nothing
  // does not re-notify.
  const orderedKeys = [
    ...projectTable.getSortedRowModel().rows.map((r) => r.original.key),
    ...retailTable.getSortedRowModel().rows.map((r) => r.original.key),
  ];
  const orderSignature = orderedKeys.join('|');
  const onOrderChangeRef = useRef(onOrderChange);
  onOrderChangeRef.current = onOrderChange;
  useEffect(() => {
    onOrderChangeRef.current?.(orderSignature ? orderSignature.split('|') : []);
  }, [orderSignature]);

  // AC-I2: a take above its own row's outstanding, or the total above the SPO qty, marks
  // the offending cell(s) destructive (the Take cell above) and states which, ONE sentence,
  // in the footer that would otherwise say "Unassigned".
  const overRow = editable
    ? coverage.find((c) => tickedKeys.includes(c.key) && (takes?.[c.key] ?? 0) > c.qty)
    : undefined;
  const overCap =
    editable && spoQty !== undefined && totalTaken !== null ? totalTaken > spoQty : false;
  const validationMessage = overRow
    ? `${overRow.document ?? 'A row'}'s take exceeds its outstanding of ${fmtInt(overRow.qty)}.`
    : overCap
      ? `Total taken ${fmtInt(totalTaken ?? 0)} exceeds the SPO qty ${fmtInt(spoQty ?? 0)}.`
      : null;

  const footerLine = readOnly
    ? `${fmtInt(filtered.length)} sales order${filtered.length === 1 ? '' : 's'} · qty ${fmtInt(totalQty)}`
    : (validationMessage ??
      `Unassigned ${fmtInt(unassigned)}${
        isFiltered ? ` · ${fmtInt(filtered.length)} of ${fmtInt(coverage.length)} shown` : ''
      }`);

  return (
    <div className="space-y-2">
      <DataGridListToolbar
        table={activeTable}
        searchSlot={
          <ListSearchInput
            value={searchInput}
            onChange={(next) => {
              setSearchInput(next);
              if (!next) setAppliedSearch('');
            }}
            onSubmit={() => setAppliedSearch(searchInput)}
            placeholder="Search sales orders"
            className="w-full sm:w-64"
          />
        }
        filters={{
          kind: 'custom',
          active: activeCount > 0,
          activeCount,
          content: (
            <PickerFilterConditions
              fields={fields}
              conditions={draftConditions}
              onChange={setDraftConditions}
              onApply={() => setAppliedConditions(draftConditions)}
              onClear={() => {
                setDraftConditions([]);
                setAppliedConditions([]);
              }}
            />
          ),
        }}
        exportConfig={false}
        showColumns={false}
      />
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'project' | 'retail')}>
        <TabsList variant="line">
          <TabsTrigger value="project">{`Project (${fmtInt(projectCount)})`}</TabsTrigger>
          <TabsTrigger value="retail">{`Retail (${fmtInt(retailCount)})`}</TabsTrigger>
        </TabsList>
        <TabsContent value="project" className="mt-2 focus-visible:outline-none">
          <DataGrid
            table={projectTable}
            recordCount={projectRows.length}
            listingKey={null}
            tableLayout={{ width: 'fixed', columnsResizable: true, scrollerMaxHeight: false }}
            rowClassName={(c) =>
              cn(isTaken(c) && 'text-muted-foreground', bucketHits?.has(c.key) && 'bg-primary/10')
            }
            rowAttributes={(c) => ({
              ...(isTaken(c) ? { 'data-taken': 'true' } : {}),
              ...(bucketHits?.has(c.key) ? { 'data-bucket-hit': 'true' } : {}),
            })}
            emptyMessage="No open project demand this SPO could cover."
          >
            <DataGridTable />
          </DataGrid>
        </TabsContent>
        <TabsContent value="retail" className="mt-2 focus-visible:outline-none">
          <DataGrid
            table={retailTable}
            recordCount={retailRows.length}
            listingKey={null}
            tableLayout={{ width: 'fixed', columnsResizable: true, scrollerMaxHeight: false }}
            rowClassName={(c) =>
              cn(isTaken(c) && 'text-muted-foreground', bucketHits?.has(c.key) && 'bg-primary/10')
            }
            rowAttributes={(c) => ({
              ...(isTaken(c) ? { 'data-taken': 'true' } : {}),
              ...(bucketHits?.has(c.key) ? { 'data-bucket-hit': 'true' } : {}),
            })}
            emptyMessage="No open retail demand this SPO could cover."
          >
            <DataGridTable />
          </DataGrid>
        </TabsContent>
      </Tabs>
      <p
        className={cn(
          'border-t pt-2 text-2xs',
          validationMessage ? 'font-medium text-destructive' : 'text-muted-foreground',
        )}
      >
        {footerLine}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The shell
// ---------------------------------------------------------------------------

/**
 * The one dialog a grid mounts, titled "<Kind> · <product code>". The product name is Radix's
 * required description, but it renders VISIBLY only when it says something the title does not:
 * on this master `product_name` is often identical to the item code (case-insensitive, once
 * trimmed), and printing "SRTWB241" under a title that already reads "SPO · SRTWB241" is the
 * repeat the captain flagged, not new information. When it matches (or is empty) the
 * description falls back to `sr-only` holding the code, so Radix still has one for assistive
 * tech and nothing repeats on screen.
 *
 * S3: the header used to carry a `context` string beside the title - the figure and its
 * qualifier, "2,876 before cut-off 30/09/2026". It is gone; each tab now states its own sum
 * in its own label (`ProjectRetailTabs`' open tab, SPO/PO's Open and History), so the total
 * is never claimed twice in two places that could drift.
 */
export function PlanRowDialog({
  kind,
  productCode,
  productName,
  open = true,
  onOpenChange,
  children,
}: {
  kind: PlanRowDialogKind;
  productCode: string;
  productName?: string | null;
  open?: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  const showName = distinctName(productCode, productName) !== null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] w-full flex-col overflow-hidden p-0 sm:max-w-[95vw]">
        <DialogHeader className="shrink-0 space-y-1 border-b p-4 sm:p-6">
          <DialogTitle className="min-w-0 break-words">
            {`${PLAN_ROW_DIALOG_TITLES[kind]} · ${productCode}`}
          </DialogTitle>
          <DialogDescription
            className={showName ? 'truncate text-xs' : 'sr-only'}
            title={showName ? (productName ?? undefined) : undefined}
          >
            {showName ? productName : productCode}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">{children}</DialogBody>
      </DialogContent>
    </Dialog>
  );
}

export default PlanRowDialog;
