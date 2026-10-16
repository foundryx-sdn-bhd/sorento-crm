'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  type ColumnDef,
  type ExpandedState,
  type OnChangeFn,
  getCoreRowModel,
  getExpandedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { ChevronDown, ChevronRight } from 'lucide-react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Skeleton } from '@/components/ui/skeleton';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { cn } from '@/lib/utils';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { StockDocumentsPanel } from '../../../project-sales/fulfilment-planning/components/StockDocumentsPanel';
import { EM_DASH, fmtDate, fmtInt, fmtMoney, fmtSupplierCost } from '../../lib/format';
import { useLocationStock, useRecommendationDemand } from '../hooks/useReorderRun';
import type { PlanDemandHistoryLine, PlanDemandLine } from '../services/reorderRunService';
import {
  getPoHistoryToPool,
  getSpoHistory,
  type PoHistoryLine,
  type SpoShipment,
} from '../services/planEditsService';
import type { PoReceipt } from '../lib/poCover';
import type { PlanLine } from '../lib/planLine';
import { isGroupedLine } from '../lib/planLineGrouping';
import { distinctName } from '@/lib/product-display';

/**
 * Six numbers, six lightboxes (plan 4.6).
 *
 * Every figure on the collapsed row that a buyer would want to argue with now opens a dialog
 * naming the DOCUMENTS behind it - the sales orders, the shipping orders, the purchase orders,
 * the stock rows. What this replaces was a hover popover per number: a mouse-only affordance,
 * six of them per row, each too narrow for a document table and each dismissed by the mouse
 * drifting off it.
 *
 * ONE dialog is mounted per grid, keyed by which number was pressed, so two can never be open
 * at once and the grid does not build six subtrees per row.
 *
 * S9 (3 Sep, PLAN-scm-loading-plan-feedback-2sep.md section 3.9): every body renders on the
 * repo's `DataGrid` rather than a plain `<table>`, matching `scm/components/PlanRowDialog.tsx`
 * (the SPO document detail's own line table is the reference both now follow) - fixed-width
 * resizable columns and a footer TOTAL row under whichever column the cell's own figure sums.
 * `DrillTable` is this file's own copy of that wiring, kept separate rather than imported (see
 * the module doc above): at whichever merge lands second, that lane re-points here and one
 * file survives.
 */

export type PlanDialogKind = 'suggested' | 'project' | 'retail' | 'on_hand' | 'spo' | 'po';

export interface PlanDialogRequest {
  kind: PlanDialogKind;
  line: PlanLine;
}

/**
 * The SITE POOL location this row's supply is counted at (R15): the pool warehouse itself,
 * never its project bins (BRW-BB, BRW-AM).
 *
 * `pool_warehouse_code` is the answer (plan 5.11): the row's own pool, named by the backend.
 * A grouped product row carries the pool's ID but has no member sitting AT the pool to read
 * a code off - a run only writes recommendations for locations with demand, so on real data
 * (32MM TAIL PIECE COUPLING) there often is none, and naming the first member instead printed
 * "to BRW-BB", a project bin, beside a count that deliberately excludes it.
 *
 * The member scan below stays as the fallback for a run frozen before the field existed.
 * Null when it cannot be named at all, and the dialogs then drop the location from their
 * wording rather than name the wrong one.
 */
export function poolLocationLabel(line: PlanLine): string | null {
  if (line.rec.pool_warehouse_code) return line.rec.pool_warehouse_code;
  if (!isGroupedLine(line)) return line.rec.warehouse_code ?? null;
  const poolId = line.rec.pool_warehouse_id ?? null;
  const members = line.__group.members;
  // The member that IS the pool: by id, then by the rule that names one - a pool location's
  // own `pool_warehouse_id` points at itself.
  const atPool =
    (poolId ? members.find((m) => m.rec.pool_warehouse_code) : undefined)?.rec
      .pool_warehouse_code ??
    (poolId ? members.find((m) => m.warehouse_id === poolId) : undefined)?.rec
      .warehouse_code ??
    members.find((m) => m.warehouse_id && m.rec.pool_warehouse_id === m.warehouse_id)?.rec
      .warehouse_code;
  return atPool ?? null;
}

/** " to BRW", or nothing at all when the pool cannot be named. */
function toPool(pool: string | null): string {
  return pool ? ` to ${pool}` : '';
}

/** Any one member's recommendation id - the backend resolves product + run off it. */
function anyRecId(line: PlanLine): string | null {
  if (!isGroupedLine(line)) return line.rec.id;
  return line.__group.members[0]?.rec.id ?? null;
}

/** Price on a demand line - what the customer pays, in ringgit. */
function priceCell(value: number | null | undefined) {
  return value === null || value === undefined ? (
    <span className="text-muted-foreground">{EM_DASH}</span>
  ) : (
    fmtMoney(value)
  );
}

function textCell(value: string | null | undefined) {
  return value ? value : <span className="text-muted-foreground">{EM_DASH}</span>;
}

/** Every right-aligned quantity/money/date column shares this header + cell alignment. */
const RIGHT: { headerClassName: string; cellClassName: string } = {
  headerClassName: 'text-right',
  cellClassName: 'text-right tabular-nums',
};

/** One `Skeleton` bar, reused across every column's `meta.skeleton` in this file. */
const SKELETON_CELL = <Skeleton className="h-4 w-full" />;

/** The label half of a footer TOTAL row (AC-J3) - under whichever column comes first. */
const TOTAL_LABEL = <span className="text-muted-foreground">Total</span>;

/**
 * A tab's own table (AC-J2): every row the caller already holds, no server pagination and no
 * per-user column persistence (`listingKey={null}` - a dialog's columns are not a personal
 * preference). See `scm/components/PlanRowDialog.tsx`'s own `DrillTable` for the full reasoning;
 * this is the same shape, duplicated rather than imported per this file's own module doc.
 */
function DrillTable<TRow extends object>({
  columns,
  rows,
  getRowId,
  isLoading,
  emptyMessage,
}: {
  columns: ColumnDef<TRow>[];
  rows: TRow[];
  getRowId?: (row: TRow, index: number) => string;
  isLoading?: boolean;
  emptyMessage: string;
}) {
  const table = useReactTable({
    columns,
    data: rows,
    getRowId,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={Boolean(isLoading)}
      listingKey={null}
      tableLayout={{
        width: 'fixed',
        columnsResizable: true,
        // Every caller of this local DrillTable renders inside PlanRowDialog's own
        // DialogBody (overflow-y-auto) - M5-05's default max-height would double-bound it.
        scrollerMaxHeight: false,
      }}
      emptyMessage={emptyMessage}
    >
      <DataGridTable />
    </DataGrid>
  );
}

// ---------------------------------------------------------------------------
// Project / Retail - the orders behind a channel's number
// ---------------------------------------------------------------------------

/**
 * One channel's demand, twice: what is still open at this location, and what the product's
 * order history says over the channel's own window. Both come from the SAME endpoint the row
 * already used (`demand?channel=`); the open list is its default scope and the history list
 * is `scope=product`, which the backend already answers with `history_lines`.
 */
function DemandTabs({
  line,
  runId,
  channel,
}: {
  line: PlanLine;
  runId: string | null;
  channel: 'project' | 'retail';
}) {
  const recId = anyRecId(line);
  // AC-S3.1: a grouped (product-grain) row's own Project/Retail cell sums every member
  // location, so the "open" tab has to query the SAME scope the history tab already does
  // (`scope=product`) - reading only `recId`'s one member location otherwise, which is the
  // "Project 3, 0 open" mismatch the plan measured. An ungrouped row has no members to widen
  // to and keeps reading exactly as it did before (no scope argument at all).
  const groupScopeArgs: ['product'] | [] = isGroupedLine(line) ? ['product'] : [];
  const open = useRecommendationDemand(
    runId,
    recId ?? '',
    Boolean(runId && recId),
    channel,
    ...groupScopeArgs,
  );
  const history = useRecommendationDemand(
    runId,
    recId ?? '',
    Boolean(runId && recId),
    channel,
    'product',
  );

  const openLines: PlanDemandLine[] = useMemo(() => open.data?.lines ?? [], [open.data]);
  const historyLines: PlanDemandHistoryLine[] = useMemo(
    () => history.data?.history_lines ?? [],
    [history.data],
  );
  const openLabel =
    channel === 'project'
      ? `Order inquiries (${fmtInt(openLines.length)} open)`
      : `Open sales orders (${fmtInt(openLines.length)})`;
  const docHeader = channel === 'project' ? 'Inquiry' : 'Sales order';
  const dateHeader = channel === 'project' ? 'Needed' : 'Required';

  const openTotal = useMemo(() => openLines.reduce((s, l) => s + (l.qty || 0), 0), [openLines]);
  const historyTotal = useMemo(
    () => historyLines.reduce((s, l) => s + (l.qty || 0), 0),
    [historyLines],
  );

  const openColumns = useMemo<ColumnDef<PlanDemandLine>[]>(
    () => [
      {
        id: 'so_number',
        header: docHeader,
        cell: ({ row }) => row.original.so_number,
        footer: () => TOTAL_LABEL,
        size: 130,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'customer_label',
        header: 'Customer',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_label}>
            {row.original.customer_label}
          </span>
        ),
        size: 170,
      },
      {
        id: 'project_title',
        header: 'Project',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.project_title ?? undefined}>
            {textCell(row.original.project_title ?? null)}
          </span>
        ),
        size: 170,
      },
      {
        id: 'agent_label',
        header: 'Agent',
        cell: ({ row }) => textCell(row.original.agent_label),
        size: 90,
      },
      {
        id: 'unit_price',
        header: 'Price',
        cell: ({ row }) => priceCell(row.original.unit_price),
        size: 100,
        meta: RIGHT,
      },
      {
        id: 'qty',
        header: 'Qty',
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(openTotal),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'required_date',
        header: dateHeader,
        cell: ({ row }) => fmtDate(row.original.required_date ?? null),
        size: 110,
        meta: RIGHT,
      },
    ],
    [docHeader, dateHeader, openTotal],
  );

  const historyColumns = useMemo<ColumnDef<PlanDemandHistoryLine>[]>(
    () => [
      {
        id: 'so_number',
        header: 'Sales order',
        cell: ({ row }) => row.original.so_number,
        footer: () => TOTAL_LABEL,
        size: 130,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'customer_label',
        header: 'Customer',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_label}>
            {row.original.customer_label}
          </span>
        ),
        size: 170,
      },
      {
        id: 'project_title',
        header: 'Project',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.project_title ?? undefined}>
            {textCell(row.original.project_title ?? null)}
          </span>
        ),
        size: 170,
      },
      {
        id: 'agent_label',
        header: 'Agent',
        cell: ({ row }) => textCell(row.original.agent_label),
        size: 90,
      },
      {
        id: 'unit_price',
        header: 'Price',
        cell: ({ row }) => priceCell(row.original.unit_price),
        size: 100,
        meta: RIGHT,
      },
      {
        id: 'qty',
        header: 'Qty',
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(historyTotal),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'order_date',
        header: 'Date',
        cell: ({ row }) => fmtDate(row.original.order_date ?? null),
        size: 100,
        meta: RIGHT,
      },
    ],
    [historyTotal],
  );

  return (
    <Tabs defaultValue="open">
      <TabsList variant="line">
        <TabsTrigger value="open">{openLabel}</TabsTrigger>
        <TabsTrigger value="history">
          {`SO history (${fmtInt(history.data?.history_total ?? historyLines.length)})`}
        </TabsTrigger>
      </TabsList>

      <TabsContent value="open">
        <DrillTable
          columns={openColumns}
          rows={openLines}
          getRowId={(l, i) => `${l.so_number}-${i}`}
          isLoading={open.isLoading}
          emptyMessage="Nothing open on this channel for this product."
        />
      </TabsContent>

      <TabsContent value="history">
        <DrillTable
          columns={historyColumns}
          rows={historyLines}
          getRowId={(l, i) => `${l.so_number}-${i}`}
          isLoading={history.isLoading}
          emptyMessage="No orders on this channel in the window."
        />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// On hand - the site pool's stock, row by row, with the documents under each
// ---------------------------------------------------------------------------

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

function OnHandTable({ line }: { line: PlanLine }) {
  const productId = line.product_id;
  const stock = useLocationStock(productId, Boolean(productId));
  const [openRow, setOpenRow] = useState<string | null>(null);

  /**
   * The SITE POOL rows only (R12/R15/R16) - never a project bin, which holds stock already
   * spoken for by an Order Inquiry and would double-count against the plan's own netting.
   *
   * EVERY pool, zeros included: the server states one row per site whatever it holds (R16),
   * because "DC1 has none" is what a buyer choosing where to buy into needs to read, and a
   * missing row says only that nobody told them. There is no fall-back to the whole list:
   * a table with no pool row at all is a product held nowhere, which the empty state says.
   */
  const rows = useMemo(
    () => ((stock.data?.locations ?? []) as OnHandLocation[]).filter((l) => l.is_pool),
    [stock.data],
  );

  const total = rows.reduce((sum, l) => sum + (l.on_hand || 0), 0);

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
        footer: () => <span className="text-muted-foreground">Site pools</span>,
        size: 120,
        meta: {
          skeleton: SKELETON_CELL,
          expandedContent: (loc: OnHandLocation) =>
            productId ? (
              <StockDocumentsPanel productId={productId} warehouseId={loc.warehouse_id} />
            ) : null,
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
    [productId, total],
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
        emptyMessage="No site pool holds this product."
      >
        <DataGridTable />
      </DataGrid>
      {/* R7: the newest `stock.updated_at` for the product (or the last stock upload),
          never the moment this dialog asked. */}
      {stock.data?.as_of ? (
        <p className="text-2xs text-muted-foreground">
          Stock as of {formatDateTimeInMalaysia(stock.data.as_of)}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SPO - what is on the water for the pool
// ---------------------------------------------------------------------------

function spoColumns(totalQty: number): ColumnDef<SpoShipment>[] {
  return [
    {
      id: 'spo_number',
      header: 'SPO',
      cell: ({ row }) => row.original.spo_number,
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
      id: 'qty',
      header: 'Qty',
      cell: ({ row }) => fmtInt(row.original.qty),
      footer: () => fmtInt(totalQty),
      size: 90,
      meta: RIGHT,
    },
    {
      id: 'received_qty',
      header: 'Received',
      cell: ({ row }) => fmtInt(row.original.received_qty),
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
      id: 'arrived_at',
      header: 'Arrived',
      cell: ({ row }) => fmtDate(row.original.arrived_at),
      size: 100,
      meta: RIGHT,
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => row.original.status,
      size: 100,
    },
  ];
}

function SpoTabs({ line, runId }: { line: PlanLine; runId: string | null }) {
  const productId = line.product_id;
  const pool = poolLocationLabel(line);
  // Review fix round B (AC-7 amended): a single, non-grouped LOCATION-grain line names
  // its own warehouse, so the modal narrows to it instead of the product-wide site-pool
  // sum - the same grain rule the PO book already applies. A grouped (pool) line or a
  // product-grain line (no warehouse of its own) passes nothing.
  const warehouseId = !isGroupedLine(line) && line.warehouse_id ? line.warehouse_id : undefined;
  const spo = useQuery({
    queryKey: ['plan-lines', runId, 'spo-history', productId, warehouseId ?? null],
    queryFn: () => getSpoHistory(runId as string, productId as string, warehouseId),
    enabled: Boolean(runId && productId),
    retry: false,
  });

  const open: SpoShipment[] = useMemo(() => spo.data?.open ?? [], [spo.data]);
  const history: SpoShipment[] = useMemo(() => spo.data?.history ?? [], [spo.data]);
  const openTotal = useMemo(() => open.reduce((s, r) => s + r.qty, 0), [open]);
  const historyTotal = useMemo(() => history.reduce((s, r) => s + r.qty, 0), [history]);
  const openColumns = useMemo(() => spoColumns(openTotal), [openTotal]);
  const historyColumns = useMemo(() => spoColumns(historyTotal), [historyTotal]);

  return (
    <Tabs defaultValue="open">
      <TabsList variant="line">
        <TabsTrigger value="open">{`Open${toPool(pool)} (${fmtInt(open.length)})`}</TabsTrigger>
        <TabsTrigger value="history">
          {`History${toPool(pool)} (${fmtInt(history.length)})`}
        </TabsTrigger>
      </TabsList>
      <TabsContent value="open">
        <DrillTable
          columns={openColumns}
          rows={open}
          // A shipment number is not unique across lines (a multi-line SPO receipts more
          // than one row under the same document) - the index disambiguates.
          getRowId={(r, i) => `${r.spo_number}-${i}`}
          isLoading={spo.isLoading}
          emptyMessage={`Nothing on the water${toPool(pool)}.`}
        />
      </TabsContent>
      <TabsContent value="history">
        <DrillTable
          columns={historyColumns}
          rows={history}
          getRowId={(r, i) => `${r.spo_number}-${i}`}
          isLoading={spo.isLoading}
          emptyMessage={`No shipment has landed${toPool(pool)} for this product.`}
        />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// PO - what is already ordered, and what we have ordered before
// ---------------------------------------------------------------------------

function PoTabs({
  line,
  runId,
  poReceipts,
}: {
  line: PlanLine;
  runId: string | null;
  poReceipts: PoReceipt[];
}) {
  const productId = line.product_id;
  const pool = poolLocationLabel(line);
  const history = useQuery({
    queryKey: ['plan-lines', runId, 'po-history', productId, pool],
    queryFn: () => getPoHistoryToPool(runId as string, productId as string, pool),
    enabled: Boolean(runId && productId),
    retry: false,
  });

  const historyLines: PoHistoryLine[] = useMemo(() => history.data?.history ?? [], [history.data]);
  const openTotal = useMemo(
    () => poReceipts.reduce((s, r) => s + (r.remaining || 0), 0),
    [poReceipts],
  );
  const historyTotal = useMemo(
    () => historyLines.reduce((s, l) => s + (l.qty || 0), 0),
    [historyLines],
  );

  // The open PO book carries the document, what is still to come and when - it is a netting
  // source, not a price record, so it names no supplier or unit price. Those two columns
  // belong to the History tab, which reads the purchase records.
  const openColumns = useMemo<ColumnDef<PoReceipt>[]>(
    () => [
      {
        id: 'po_number',
        header: 'PO',
        cell: ({ row }) => textCell(row.original.po_number),
        footer: () => TOTAL_LABEL,
        size: 140,
        meta: { skeleton: SKELETON_CELL },
      },
      {
        id: 'remaining',
        header: 'Still to come',
        cell: ({ row }) => fmtInt(row.original.remaining),
        footer: () => fmtInt(openTotal),
        size: 120,
        meta: RIGHT,
      },
      {
        id: 'expected_date',
        header: 'ETA',
        cell: ({ row }) => fmtDate(row.original.expected_date),
        size: 100,
        meta: RIGHT,
      },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => row.original.status,
        size: 110,
      },
    ],
    [openTotal],
  );

  const historyColumns = useMemo<ColumnDef<PoHistoryLine>[]>(
    () => [
      {
        id: 'po_number',
        header: 'PO',
        cell: ({ row }) => row.original.po_number,
        footer: () => TOTAL_LABEL,
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
        id: 'qty',
        header: 'Qty',
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(historyTotal),
        size: 90,
        meta: RIGHT,
      },
      {
        id: 'unit_cost',
        header: 'Unit price',
        cell: ({ row }) =>
          row.original.unit_cost === null ? (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ) : (
            fmtSupplierCost(row.original.unit_cost, row.original.currency)
          ),
        size: 120,
        meta: RIGHT,
      },
      {
        id: 'issued_at',
        header: 'Issued',
        cell: ({ row }) => fmtDate(row.original.issued_at),
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
        cell: ({ row }) => row.original.status,
        size: 100,
      },
    ],
    [historyTotal],
  );

  return (
    <Tabs defaultValue="open">
      <TabsList variant="line">
        <TabsTrigger value="open">
          {`Open${toPool(pool)} (${fmtInt(poReceipts.length)})`}
        </TabsTrigger>
        <TabsTrigger value="history">
          {`History${toPool(pool)} (${fmtInt(historyLines.length)})`}
        </TabsTrigger>
      </TabsList>

      <TabsContent value="open">
        <DrillTable
          columns={openColumns}
          rows={poReceipts}
          getRowId={(r, i) => `${r.po_number}-${i}`}
          emptyMessage={`Nothing on order${toPool(pool)}.`}
        />
      </TabsContent>

      <TabsContent value="history">
        <DrillTable
          columns={historyColumns}
          rows={historyLines}
          getRowId={(l, i) => `${l.po_number}-${i}`}
          isLoading={history.isLoading}
          emptyMessage={
            pool
              ? `No purchase order raised here names ${pool} as its destination.`
              : 'No purchase order raised here names this product.'
          }
        />
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------

const TITLES: Record<PlanDialogKind, string> = {
  suggested: 'Suggested qty',
  project: 'Project demand',
  retail: 'Retail demand',
  on_hand: 'On hand BRW',
  spo: 'SPO',
  po: 'PO',
};

/**
 * The one dialog the grid mounts. `request` names which number was pressed and on which
 * row; closing it clears the request.
 *
 * `ledger` is passed in rather than built here: the Suggested-qty body is the existing
 * `OrderQtyLedger`, which needs the whole plan context (cover, PO book, economics, trend)
 * that only the grid holds. One body, two containers - it was a popover, it is a dialog.
 */
export function PlanRowDialog({
  request,
  onOpenChange,
  runId,
  ledger,
  poReceipts = [],
}: {
  request: PlanDialogRequest | null;
  onOpenChange: (open: boolean) => void;
  runId: string | null;
  ledger?: React.ReactNode;
  poReceipts?: PoReceipt[];
}) {
  if (!request) return null;
  const { kind, line } = request;
  const pool = poolLocationLabel(line);
  const title =
    (kind === 'po' || kind === 'spo') && pool
      ? `${TITLES[kind]} - ${line.sku} - to ${pool}`
      : `${TITLES[kind]} - ${line.sku}`;

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] w-full flex-col overflow-hidden p-0 sm:max-w-[95vw]">
        <DialogHeader className="shrink-0 space-y-1 border-b p-4 sm:p-6">
          <DialogTitle className="min-w-0 break-words">{title}</DialogTitle>
          {/* The product name IS the description - Radix wants one, and a second sentence
              explaining the dialog would be an on-screen explanation. */}
          <DialogDescription className="truncate text-xs" title={distinctName(line.sku, line.product_name) ?? undefined}>
            {distinctName(line.sku, line.product_name)}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
          {kind === 'suggested' ? (
            ledger ?? <p className="text-sm text-muted-foreground">Nothing to explain here.</p>
          ) : kind === 'project' || kind === 'retail' ? (
            <DemandTabs line={line} runId={runId} channel={kind} />
          ) : kind === 'on_hand' ? (
            <OnHandTable line={line} />
          ) : kind === 'spo' ? (
            <SpoTabs line={line} runId={runId} />
          ) : (
            <PoTabs line={line} runId={runId} poReceipts={poReceipts} />
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
