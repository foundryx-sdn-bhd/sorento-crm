'use client';

import { useMemo, useState } from 'react';
import {
  ColumnDef,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import {
  AlertCircle,
  AlertTriangle,
  ArrowRightLeft,
  CalendarClock,
  CheckCircle2,
  PackageOpen,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { ConfirmActionDialog } from '../../components/ConfirmActionDialog';
import { EM_DASH, fmtInt, fmtMoney, fmtSigned } from '../../lib/format';
import {
  SOURCE_LABEL,
  SUPPLY_STAGE_LABEL,
  computedAtLabel,
  coverVerdict,
  dayLabel,
  firstLateSupply,
  monthLabel,
  shortfallWhen,
  supplySplit,
} from '../lib/coverageTimeline';
import { useAcceptCoverageTransfer, useCoverageTimeline } from '../hooks/useCoverage';
import { CAN_ACCEPT_COVERAGE_TRANSFER } from '../services/coverageService';
import type {
  CoverageRow,
  CoverageTimeline,
  CoverageTransferProposal,
  CoverageUndatedDemand,
} from '../types/coverage.types';
import { distinctName } from '@/lib/product-display';

/**
 * Coverage Timeline inside the row-click explanation dialog (UAC Group B).
 *
 * What this screen has to make readable at a glance, in this order: whether the
 * answer is "use the pool" or "buy", where the first shortfall is, and why a
 * POSITIVE closing balance can still be a real shortage.
 *
 * That last one is the whole point of the dated engine (AC-B4). A PO of 200
 * arriving 25 Aug against an order due 3 Aug leaves the closing balance at +133
 * while the order is 67 short, so a screen that leads with the closing balance
 * reports a covered position where none exists. Here the shortfall is stated first,
 * in destructive tone, the late supply is named with the gap in days, and the
 * closing balance carries an explicit "does not clear it" caption whenever the two
 * disagree. It never renders as a healthy figure on its own.
 *
 * Every balance on screen is the server's. Nothing here accumulates: the month
 * headings are display grouping only (AC-B3) and the on-order / in-transit split is
 * read off the same rows, so the presentation layer cannot drift from the maths.
 *
 * Phase 1 serves `lib/coverageMockStore`; Phase 2 flips the flag in
 * `services/coverageService.ts` and this component is untouched.
 */

/** Timeline grids live inside a dialog on a route that already owns a grid, so an
 *  empty key opts them out of pathname-derived column persistence. Three grids
 *  sharing `/scm/reorder` would otherwise clobber each other's saved columns. */
const NO_COLUMN_PERSISTENCE = '';

const GRID_LAYOUT = { width: 'fixed', columnsResizable: true, dense: true } as const;

/** One figure in the balance strip. `tone` is the whole reason this exists. */
function Figure({
  label,
  value,
  hint,
  tone = 'plain',
  testId,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'plain' | 'short' | 'good' | 'muted';
  testId?: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-border px-3 py-2" data-testid={testId}>
      <div className="truncate text-2xs uppercase tracking-wide text-muted-foreground" title={label}>
        {label}
      </div>
      <div
        className={cn(
          'truncate text-lg font-semibold tabular-nums leading-tight',
          tone === 'short' && 'text-scm-stockout',
          tone === 'good' && 'text-scm-incoming',
          tone === 'muted' && 'text-muted-foreground',
        )}
        title={value}
      >
        {value}
      </div>
      {hint ? (
        <div className="text-2xs text-muted-foreground" title={hint}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}

function SectionTitle({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <div className="mb-1 flex flex-wrap items-center gap-2">
      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {children}
      </span>
      {count != null ? (
        <Badge variant="secondary" appearance="light" size="xs">
          {fmtInt(count)}
        </Badge>
      ) : null}
    </div>
  );
}

// ── the dated grid ──────────────────────────────────────────────────────────

/** Stable row key. Events carry no id (by design, AC-B2), so position is the key. */
function rowKey(row: CoverageRow, index: number): string {
  return `${index}-${row.event.at ?? 'opening'}-${row.event.ref}`;
}

const MOVEMENT_LABEL: Record<string, string> = {
  opening: 'Opening',
  demand: 'Demand',
  supply: 'Supply',
};

function TimelineGrid({ timeline }: { timeline: CoverageTimeline }) {
  const rows = timeline.rows;
  const shortfallAt = timeline.shortfall?.at ?? null;
  const shortfallRef = timeline.shortfall?.ref ?? null;

  const columns = useMemo<ColumnDef<CoverageRow>[]>(
    () => [
      {
        id: 'at',
        accessorFn: (r) => r.event.at ?? '',
        header: 'Date',
        cell: ({ row }) => {
          const e = row.original.event;
          // The event that tipped the balance under the floor is marked here rather
          // than by tinting the row, so it survives column reordering.
          const isShortfall = !!shortfallAt && e.at === shortfallAt && e.ref === shortfallRef;
          return (
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="truncate tabular-nums" title={e.at ? dayLabel(e.at) : 'Opening'}>
                {e.at ? dayLabel(e.at) : 'Opening'}
              </span>
              {isShortfall ? (
                <Badge variant="destructive" appearance="light" size="xs">
                  short
                </Badge>
              ) : null}
            </span>
          );
        },
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Date', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'movement',
        accessorFn: (r) => r.event.kind,
        header: 'Movement',
        cell: ({ row }) => {
          const e = row.original.event;
          const stage = e.supply_stage ? SUPPLY_STAGE_LABEL[e.supply_stage] : null;
          return (
            <span className="truncate" title={stage ?? MOVEMENT_LABEL[e.kind] ?? e.kind}>
              {stage ?? MOVEMENT_LABEL[e.kind] ?? e.kind}
            </span>
          );
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Movement' },
      },
      {
        id: 'ref',
        accessorFn: (r) => r.event.ref,
        header: 'Document',
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.event.ref}>
            {row.original.event.ref || EM_DASH}
          </span>
        ),
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Document' },
      },
      {
        id: 'label',
        accessorFn: (r) => r.event.label,
        header: 'Detail',
        cell: ({ row }) => (
          <span className="truncate text-muted-foreground" title={row.original.event.label}>
            {row.original.event.label || EM_DASH}
          </span>
        ),
        size: 190,
        enableSorting: false,
        meta: { headerTitle: 'Detail' },
      },
      {
        id: 'location',
        accessorFn: (r) => r.event.location,
        header: 'Location',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.event.location}>
            {row.original.event.location || EM_DASH}
          </span>
        ),
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Location' },
      },
      {
        id: 'qty',
        accessorFn: (r) => r.event.qty,
        header: 'Quantity',
        cell: ({ row }) => {
          const e = row.original.event;
          if (e.kind === 'opening') return <span className="text-muted-foreground">{EM_DASH}</span>;
          return (
            <span className={cn('tabular-nums', e.qty < 0 ? 'text-scm-stockout' : 'text-scm-incoming')}>
              {e.qty > 0 ? '+' : ''}
              {fmtSigned(e.qty)}
            </span>
          );
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Quantity', cellClassName: 'text-right', headerClassName: 'text-right' },
      },
      {
        id: 'balance',
        accessorFn: (r) => r.balance,
        header: 'Balance',
        cell: ({ row }) => {
          const short = row.original.balance < timeline.floor;
          return (
            <span
              className={cn('tabular-nums font-medium', short && 'text-scm-stockout')}
              title={String(row.original.balance)}
            >
              {fmtSigned(row.original.balance)}
            </span>
          );
        },
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Balance', cellClassName: 'text-right', headerClassName: 'text-right' },
      },
    ],
    [shortfallAt, shortfallRef, timeline.floor],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row, index) => rowKey(row, index),
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      listingKey={NO_COLUMN_PERSISTENCE}
      tableLayout={GRID_LAYOUT}
      emptyMessage="No dated movements in the horizon."
      // Months are a DISPLAY grouping only (AC-B3). The rows arrive already ordered
      // and already accumulated, so a heading can never move a balance.
      renderGroupHeader={(row: CoverageRow, previous: CoverageRow | null) => {
        const key = row.event.at ? row.event.at.slice(0, 7) : null;
        const prevKey = previous?.event.at ? previous.event.at.slice(0, 7) : null;
        if (previous && key === prevKey) return null;
        return key ? monthLabel(`${key}-01`) : 'Opening';
      }}
    >
      <div className="overflow-hidden rounded-lg border border-border">
        <DataGridTable />
      </div>
    </DataGrid>
  );
}

// ── undated demand ─────────────────────────────────────────────────────────

function UndatedGrid({ rows }: { rows: CoverageUndatedDemand[] }) {
  const columns = useMemo<ColumnDef<CoverageUndatedDemand>[]>(
    () => [
      {
        accessorKey: 'so_number',
        header: 'Sales order',
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.so_number}>
            {row.original.so_number || EM_DASH}
          </span>
        ),
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Sales order' },
      },
      {
        accessorKey: 'item_code',
        header: 'Item',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.item_code}>
            {row.original.item_code || EM_DASH}
          </span>
        ),
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Item' },
      },
      {
        accessorKey: 'location',
        header: 'Location',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.location}>
            {row.original.location || EM_DASH}
          </span>
        ),
        size: 130,
        enableSorting: false,
        meta: { headerTitle: 'Location' },
      },
      {
        accessorKey: 'qty',
        header: 'Quantity',
        cell: ({ row }) => <span className="tabular-nums">{fmtInt(row.original.qty)}</span>,
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Quantity', cellClassName: 'text-right', headerClassName: 'text-right' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row, index) => `${index}-${row.so_number}-${row.location}`,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      listingKey={NO_COLUMN_PERSISTENCE}
      tableLayout={GRID_LAYOUT}
      emptyMessage="Every commitment carries a date."
    >
      <div className="overflow-hidden rounded-lg border border-border">
        <DataGridTable />
      </div>
    </DataGrid>
  );
}

// ── transfer proposals ─────────────────────────────────────────────────────

function TransferGrid({
  rows,
  onAccept,
  isAccepting,
}: {
  rows: CoverageTransferProposal[];
  onAccept: (proposal: CoverageTransferProposal) => void;
  isAccepting: boolean;
}) {
  const columns = useMemo<ColumnDef<CoverageTransferProposal>[]>(
    () => [
      {
        accessorKey: 'from_pool_code',
        header: 'From',
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.from_pool_code}>
            {row.original.from_pool_code}
          </span>
        ),
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'From' },
      },
      {
        accessorKey: 'available_qty',
        header: 'They hold',
        cell: ({ row }) => <span className="tabular-nums">{fmtInt(row.original.available_qty)}</span>,
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'They hold', cellClassName: 'text-right', headerClassName: 'text-right' },
      },
      {
        accessorKey: 'qty',
        header: 'Would move',
        cell: ({ row }) => (
          <span className="tabular-nums font-medium">{fmtInt(row.original.qty)}</span>
        ),
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Would move', cellClassName: 'text-right', headerClassName: 'text-right' },
      },
      {
        accessorKey: 'transfer_cost',
        header: 'Transfer cost',
        cell: ({ row }) => (
          <span className="tabular-nums">{fmtMoney(row.original.transfer_cost)}</span>
        ),
        size: 130,
        enableSorting: false,
        meta: {
          headerTitle: 'Transfer cost',
          cellClassName: 'text-right',
          headerClassName: 'text-right',
        },
      },
      {
        accessorKey: 'lead_time_days',
        header: 'Lead time (days)',
        cell: ({ row }) => (
          <span className="tabular-nums">{fmtInt(row.original.lead_time_days)}</span>
        ),
        size: 140,
        enableSorting: false,
        meta: {
          headerTitle: 'Lead time (days)',
          cellClassName: 'text-right',
          headerClassName: 'text-right',
        },
      },
      {
        accessorKey: 'arrives_at',
        header: 'Arrives',
        cell: ({ row }) => (
          <span className="truncate tabular-nums" title={dayLabel(row.original.arrives_at)}>
            {row.original.arrives_at ? dayLabel(row.original.arrives_at) : EM_DASH}
          </span>
        ),
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Arrives' },
      },
      {
        id: 'accept',
        header: () => <span className="sr-only">Accept</span>,
        cell: ({ row }) => (
          <div className="flex justify-end">
            <Button
              variant="outline"
              size="sm"
              disabled={isAccepting || !CAN_ACCEPT_COVERAGE_TRANSFER}
              // `proposal_ref` is the key for the action and is never rendered.
              onClick={() => onAccept(row.original)}
              title={
                CAN_ACCEPT_COVERAGE_TRANSFER
                  ? `Accept the transfer from ${row.original.from_pool_code}`
                  : 'Accepting a transfer is not available yet'
              }
            >
              Accept
            </Button>
          </div>
        ),
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Accept', cellClassName: 'text-right' },
      },
    ],
    [isAccepting, onAccept],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.proposal_ref,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      listingKey={NO_COLUMN_PERSISTENCE}
      tableLayout={GRID_LAYOUT}
      emptyMessage="No other site holds this item."
    >
      <div className="overflow-hidden rounded-lg border border-border">
        <DataGridTable />
      </div>
    </DataGrid>
  );
}

// ── the panel ──────────────────────────────────────────────────────────────

export interface CoverageTimelinePanelProps {
  /** Human product code. Never an id. */
  productCode: string;
  productName?: string | null;
  /** A pool code, or the row's own location code - the backend resolves its pool.
   *  null on a network (aggregated) row, which has no single pool to date against. */
  poolCode: string | null;
  /** The level the balance must not fall below: 0 for project demand, ROP otherwise. */
  floor: number;
  /** The dialog's open flag. Nothing is fetched until a row is actually opened. */
  enabled: boolean;
}

export function CoverageTimelinePanel({
  productCode,
  productName,
  poolCode,
  floor,
  enabled,
}: CoverageTimelinePanelProps) {
  const query = {
    product_code: productCode,
    pool_code: poolCode ?? '',
    floor,
    product_name: productName ?? null,
  };
  const { data, isLoading, isError, error, refetch } = useCoverageTimeline(query, enabled);
  const accept = useAcceptCoverageTransfer(query);
  const [pendingTransfer, setPendingTransfer] = useState<CoverageTransferProposal | null>(null);

  // ---- no pool to date against (network row) --------------------------------
  if (!poolCode) {
    return (
      <div className="rounded-lg border border-dashed border-border p-4 text-center">
        <PackageOpen className="mx-auto size-5 text-muted-foreground" aria-hidden />
        <p className="mt-1 text-sm text-muted-foreground">
          This row is planned across the network, so there is no single pool to date it against.
        </p>
        <p className="mt-1 text-2xs text-muted-foreground">
          Open a per-location row to see its coverage timeline.
        </p>
      </div>
    );
  }

  // ---- loading --------------------------------------------------------------
  if (isLoading) {
    return (
      <div className="space-y-3" aria-label="Loading coverage timeline" aria-busy="true">
        <Skeleton className="h-10 w-full rounded-lg" />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-40 w-full rounded-lg" />
      </div>
    );
  }

  // ---- error ---------------------------------------------------------------
  if (isError || !data) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-center">
        <AlertCircle className="size-5 text-destructive" aria-hidden />
        <p className="max-w-sm text-sm text-muted-foreground">
          {error instanceof Error ? error.message : 'Failed to load the coverage timeline.'}
        </p>
        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const dated = data.rows.filter((r) => r.event.kind !== 'opening');
  const late = firstLateSupply(data.rows, data.shortfall);
  const split = supplySplit(data.rows);
  const verdict = coverVerdict(
    data.use_stock,
    data.buy_qty,
    data.availability.pool_location,
    data.shortfall,
  );
  // A positive closing balance with a real shortfall is NOT healthy: the supply that
  // lifts it is dated after the demand it is being read as covering.
  const closingMisleading = !!data.shortfall && data.closing_balance >= data.floor;

  return (
    <div className="space-y-4">
      {/* Verdict - the one line a planner acts on. */}
      <div
        className={cn(
          'flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between',
          verdict.tone === 'stock'
            ? 'border-scm-incoming/40 bg-scm-incoming-soft'
            : 'border-scm-stockout/40 bg-scm-stockout-soft',
        )}
        data-testid="coverage-verdict"
      >
        <div className="flex min-w-0 items-start gap-2">
          {verdict.tone === 'stock' ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-scm-incoming" aria-hidden />
          ) : (
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-scm-stockout" aria-hidden />
          )}
          <div className="min-w-0 break-words">
            <div className="text-sm font-semibold">{verdict.headline}</div>
            {verdict.note ? (
              <div className="text-2xs font-medium text-scm-stockout" data-testid="coverage-verdict-note">
                {verdict.note}
              </div>
            ) : null}
            <div className="text-2xs text-muted-foreground">
              {data.product_code}
              {distinctName(data.product_code, data.product_name) ? ` · ${data.product_name}` : ''} · pool {data.pool_code} ·{' '}
              {data.locations.length} {data.locations.length === 1 ? 'location' : 'locations'}
            </div>
          </div>
        </div>
        <div className="shrink-0 text-2xs text-muted-foreground">
          Computed {computedAtLabel(data.computed_at)}
        </div>
      </div>

      {/* Shortfall FIRST, before any balance, so a covered-looking closing figure
          can never be the first thing read. */}
      {data.shortfall ? (
        <div
          className="rounded-lg border border-scm-stockout/50 bg-scm-stockout-soft p-3"
          data-testid="coverage-shortfall"
        >
          <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
            <span className="text-sm font-semibold text-scm-stockout">
              Short {fmtInt(data.shortfall.qty)} {shortfallWhen(data.shortfall.at)}
            </span>
            <span className="min-w-0 break-words text-2xs text-muted-foreground">
              {data.shortfall.ref}
              {data.shortfall.label ? ` · ${data.shortfall.label}` : ''}
            </span>
          </div>
          {late ? (
            <div className="mt-1.5 flex items-start gap-1.5 text-xs">
              <CalendarClock className="mt-0.5 size-3.5 shrink-0 text-scm-stockout" aria-hidden />
              <span className="min-w-0 break-words">
                {late.ref} brings {fmtInt(late.qty)} on {dayLabel(late.at)},{' '}
                <span className="font-medium">{fmtInt(late.days_late)} days too late</span> for this
                order.
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Balances. */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Figure
          label="Opening balance"
          value={fmtSigned(data.opening_balance)}
          testId="coverage-opening"
        />
        <Figure
          label="Peak deficit"
          value={fmtInt(data.peak_deficit)}
          tone={data.peak_deficit > 0 ? 'short' : 'muted'}
          hint={data.peak_deficit > 0 ? 'worst point, not just the first' : 'never goes short'}
          testId="coverage-peak"
        />
        <Figure
          label="Closing balance"
          value={fmtSigned(data.closing_balance)}
          tone={closingMisleading ? 'muted' : data.closing_balance < data.floor ? 'short' : 'good'}
          hint={
            closingMisleading
              ? `does not clear the shortfall ${shortfallWhen(data.shortfall?.at ?? null)}`
              : undefined
          }
          testId="coverage-closing"
        />
        <Figure
          label="Incoming"
          value={fmtInt(split.in_transit)}
          hint="allocated shipments"
          testId="coverage-incoming"
        />
        <Figure
          label="Ordered"
          value={fmtInt(data.qty_ordered_not_incoming)}
          tone="muted"
          hint="on a PO, nothing shipped yet"
          testId="coverage-ordered"
        />
      </div>

      {/* Cover sources - what could cover it now, and how it resolves. */}
      <section aria-label="Cover sources">
        <SectionTitle>Cover sources</SectionTitle>
        <div className="grid grid-cols-3 gap-2">
          <Figure label="Own bin" value={fmtInt(data.availability.own)} />
          <Figure
            label="Shared pool"
            value={fmtInt(data.availability.pool)}
            hint={data.availability.pool_location || undefined}
          />
          <Figure
            label="Other bins"
            value={fmtInt(data.availability.other)}
            hint={data.availability.other_locations.join(', ') || undefined}
          />
        </div>
        <div className="mt-2 overflow-hidden rounded-lg border border-border">
          {data.allocations.length ? (
            data.allocations.map((a, i) => (
              <div
                key={`${a.source_type}-${a.location}-${i}`}
                className="flex items-center justify-between gap-2 border-b px-3 py-1.5 text-sm last:border-b-0"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate" title={SOURCE_LABEL[a.source_type] ?? a.source_type}>
                    {SOURCE_LABEL[a.source_type] ?? a.source_type}
                  </span>
                  {a.location ? (
                    <span className="truncate text-2xs text-muted-foreground" title={a.location}>
                      {a.location}
                    </span>
                  ) : null}
                  {a.needs_claim ? (
                    <Badge variant="warning" appearance="light" size="xs">
                      needs a claim
                    </Badge>
                  ) : null}
                </span>
                <span className="shrink-0 tabular-nums">{fmtInt(a.qty)}</span>
              </div>
            ))
          ) : (
            <div className="px-3 py-2 text-2xs text-muted-foreground">
              Nothing to resolve: no line is short at this pool.
            </div>
          )}
        </div>
        <div className="mt-1.5 text-2xs text-muted-foreground">
          Buy {fmtInt(data.buy_qty)}
          {data.use_stock ? ' · committed demand covered from existing stock' : ''}
        </div>
      </section>

      {/* The dated timeline. */}
      <section aria-label="Coverage timeline">
        <SectionTitle count={dated.length}>Dated movements</SectionTitle>
        {dated.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-4 text-center">
            <PackageOpen className="mx-auto size-5 text-muted-foreground" aria-hidden />
            <p className="mt-1 text-sm font-medium">Nothing dated against this stock</p>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              {fmtSigned(data.opening_balance)} on hand, no open order or supply in the horizon.
            </p>
          </div>
        ) : (
          <TimelineGrid timeline={data} />
        )}
        {data.excluded_event_count > 0 ? (
          <p className="mt-1.5 text-2xs text-muted-foreground">
            {fmtInt(data.excluded_event_count)}{' '}
            {data.excluded_event_count === 1 ? 'event' : 'events'} beyond{' '}
            {dayLabel(data.horizon_end)} excluded ({fmtInt(data.horizon_months)}-month horizon).
          </p>
        ) : null}
      </section>

      {/* Undated demand - listed, never dated today and never dropped. */}
      <section aria-label="Undated demand">
        <SectionTitle count={data.undated_demand.length}>Undated demand</SectionTitle>
        {data.undated_demand.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-3 py-2 text-2xs text-muted-foreground">
            Every commitment carries a date.
          </p>
        ) : (
          <>
            <UndatedGrid rows={data.undated_demand} />
            <p className="mt-1.5 text-2xs text-muted-foreground">
              Not on the balance above: an undated commitment cannot be placed on the axis.
            </p>
          </>
        )}
      </section>

      {/* Cross-site cover - a proposal, never netted. */}
      <section aria-label="Transfer proposals">
        <SectionTitle count={data.transfer_proposals.length}>Transfer proposals</SectionTitle>
        {data.transfer_proposals.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-3 py-2 text-2xs text-muted-foreground">
            No other site holds this item.
          </p>
        ) : (
          <>
            <TransferGrid
              rows={data.transfer_proposals}
              onAccept={setPendingTransfer}
              isAccepting={accept.isPending}
            />
            <p className="mt-1.5 flex items-start gap-1.5 text-2xs text-muted-foreground">
              <ArrowRightLeft className="mt-0.5 size-3 shrink-0" aria-hidden />
              Not counted in the balance above: moving stock between sites costs money and time.
            </p>
          </>
        )}
      </section>

      <ConfirmActionDialog
        open={!!pendingTransfer}
        onOpenChange={(o) => !o && setPendingTransfer(null)}
        title="Accept this transfer?"
        description={
          pendingTransfer ? (
            <>
              This moves {fmtInt(pendingTransfer.qty)} of {data.product_code} from{' '}
              {pendingTransfer.from_pool_code} to {data.pool_code}, at{' '}
              {fmtMoney(pendingTransfer.transfer_cost)} and{' '}
              {fmtInt(pendingTransfer.lead_time_days)} days lead time
              {pendingTransfer.arrives_at ? `, arriving ${dayLabel(pendingTransfer.arrives_at)}` : ''}.
            </>
          ) : null
        }
        confirmLabel="Accept transfer"
        isBusy={accept.isPending}
        onConfirm={() => {
          if (!pendingTransfer) return;
          const ref = pendingTransfer.proposal_ref;
          setPendingTransfer(null);
          accept.mutate(ref);
        }}
      />
    </div>
  );
}

export default CoverageTimelinePanel;
