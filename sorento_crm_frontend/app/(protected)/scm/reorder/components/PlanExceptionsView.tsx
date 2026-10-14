'use client';

import { useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { AlertCircle, ArrowLeft, Search, ShieldCheck, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { cn } from '@/lib/utils';
import { EM_DASH, fmtInt } from '../../lib/format';
import { computedAtLabel, dayLabel } from '../lib/coverageTimeline';
import { usePlanExceptions, useDecidePlanException } from '../hooks/usePlanExceptions';
import { PlanExceptionReviewSheet } from './PlanExceptionReviewSheet';
import {
  ACTION_LABELS,
  EXCEPTION_STATUS_LABELS,
  EXCEPTION_TYPE_LABELS,
  type PlanException,
  type PlanExceptionStatus,
  type PlanExceptionType,
} from '../types/planException.types';
import { distinctName } from '@/lib/product-display';

/**
 * Plan Exceptions (UAC Group D).
 *
 * **An exception is a disagreement between the plan and supply already placed**, and the
 * screen's value is the reduction: a restatement that moved 412 lines produces six rows
 * anyone has to act on (AC-D2b). So the header states BOTH figures. Showing only the six
 * would look like a thin result rather than a filter doing its job, and showing only the
 * 412 would be a queue nobody could work.
 *
 * The staleness note is on the screen, not in a footnote, because the journey is explicit
 * about it: nothing here reacts to a project moving until the sales-order book is
 * re-uploaded, and the system is only ever as current as that upload.
 *
 * Ordering is the SERVER's (open first, then severity), and each row's actions arrive
 * ranked by the item's reading (AC-D10). This grid never re-sorts the actions - doing so
 * by quantity would silently undo the one rule the feature exists to enforce.
 *
 * Live: the Phase-1 fixture is deleted and this component was untouched by the swap, which
 * is what the mocked-service boundary was for.
 */

/** Shares `/scm/reorder` with three other grids; an empty key opts out of persistence. */
const NO_COLUMN_PERSISTENCE = '';

const numMeta = { headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' };

/**
 * Types mapped onto the SHARED pill palette - no new colour vocabulary. A shortfall that
 * moved earlier is the one that can miss a customer date, so it takes the alarm tone; the
 * other three are states to work, not emergencies.
 */
const TYPE_PILL_CODE: Record<PlanExceptionType, string> = {
  shortfall_earlier: 'rejected',
  supply_early: 'pending',
  supply_surplus: 'pending',
  supply_wrong_location: 'new',
};

const STATUS_PILL_CODE: Record<PlanExceptionStatus, string> = {
  open: 'new',
  approved: 'resolved',
  rejected: 'rejected',
};

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'approved', label: EXCEPTION_STATUS_LABELS.approved },
  { value: 'rejected', label: EXCEPTION_STATUS_LABELS.rejected },
  { value: 'all', label: 'All statuses' },
];

export interface PlanExceptionsViewProps {
  /** Opaque run key. Null reads the newest completed plan. Never rendered. */
  runId?: string | null;
  /** Returns to the buy co-pilot. This report has no row in that grid to filter back to,
   *  so it needs its own way out - the tile that used to open it is gone. */
  onBack?: () => void;
}

export function PlanExceptionsView({ runId = null, onBack }: PlanExceptionsViewProps) {
  const query = { run_id: runId };
  const { data, isLoading, isError, error, refetch } = usePlanExceptions(query);
  const decide = useDecidePlanException(query);

  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [searchQuery, setSearchQuery] = useState('');
  // Open by default: the queue is what is left to decide. Landing on "all" would make the
  // first action, every time, a click to narrow it.
  const [statusFilter, setStatusFilter] = useState<string>('open');
  const [reviewing, setReviewing] = useState<PlanException | null>(null);

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    if (statusFilter === 'all') return all;
    return all.filter((r) => r.status === statusFilter);
  }, [data?.rows, statusFilter]);

  const columns = useMemo<ColumnDef<PlanException>[]>(
    () => [
      {
        accessorKey: 'product_code',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate font-medium" title={row.original.product_code}>
              {row.original.product_code}
            </div>
            {distinctName(row.original.product_code, row.original.product_name) ? (
              <div
                className="truncate text-2xs text-muted-foreground"
                title={row.original.product_name}
              >
                {row.original.product_name}
              </div>
            ) : null}
          </div>
        ),
        size: 210,
        meta: { headerTitle: 'Product', skeleton: <Skeleton className="h-8 w-40" /> },
      },
      {
        accessorKey: 'exception_type',
        header: ({ column }) => <DataGridColumnHeader title="What disagrees" column={column} />,
        cell: ({ row }) => (
          <span
            className={cn(
              STATUS_PILL_BASE,
              statusPillClass(TYPE_PILL_CODE[row.original.exception_type]),
            )}
          >
            {EXCEPTION_TYPE_LABELS[row.original.exception_type]}
          </span>
        ),
        size: 220,
        meta: { headerTitle: 'What disagrees' },
      },
      {
        accessorKey: 'quantity',
        header: ({ column }) => <DataGridColumnHeader title="Quantity" column={column} />,
        cell: ({ row }) => <span className="font-medium">{fmtInt(row.original.quantity)}</span>,
        size: 110,
        meta: { headerTitle: 'Quantity', ...numMeta },
      },
      {
        accessorKey: 'po_number',
        header: ({ column }) => <DataGridColumnHeader title="Placed supply" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          if (!r.po_number) {
            return <span className="text-muted-foreground">{EM_DASH}</span>;
          }
          return (
            <div className="min-w-0">
              <div className="truncate" title={r.po_number}>
                {r.po_number}
              </div>
              <div className="truncate text-2xs text-muted-foreground">
                {r.po_expected_date ? `due ${dayLabel(r.po_expected_date)}` : 'no expected date'}
                {r.warehouse_code ? ` · ${r.warehouse_code}` : ''}
              </div>
            </div>
          );
        },
        size: 190,
        meta: { headerTitle: 'Placed supply' },
      },
      {
        id: 'reading',
        accessorFn: (r) => r.reading.velocity.value ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Reads as" column={column} />,
        // The compact form. The sources live in the review panel (AC-D12) - putting four
        // field names in a grid cell would bury the values they qualify.
        cell: ({ row }) => {
          const rd = row.original.reading;
          const parts = [rd.lifecycle.value, rd.velocity.value, rd.business.value].filter(
            Boolean,
          );
          return (
            <span className="truncate text-2xs text-muted-foreground" title={parts.join(' · ')}>
              {parts.length ? parts.join(' · ') : EM_DASH}
            </span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Reads as' },
      },
      {
        id: 'proposed',
        accessorFn: (r) => r.actions[0]?.code ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Proposed" column={column} />,
        cell: ({ row }) => {
          const first = row.original.actions[0];
          if (!first) return <span className="text-muted-foreground">{EM_DASH}</span>;
          return (
            <span className="truncate" title={first.rationale}>
              {ACTION_LABELS[first.code]}
            </span>
          );
        },
        size: 200,
        meta: { headerTitle: 'Proposed' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Decision" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          return (
            <div className="flex min-w-0 flex-col gap-1">
              <span className={cn(STATUS_PILL_BASE, statusPillClass(STATUS_PILL_CODE[r.status]))}>
                {EXCEPTION_STATUS_LABELS[r.status]}
              </span>
              {r.decided_by ? (
                <span className="truncate text-2xs text-muted-foreground">
                  {r.decided_by} · {computedAtLabel(r.decided_at)}
                </span>
              ) : null}
            </div>
          );
        },
        size: 170,
        meta: { headerTitle: 'Decision' },
      },
      {
        id: 'review',
        header: '',
        cell: ({ row }) => (
          <Button size="sm" variant="outline" onClick={() => setReviewing(row.original)}>
            {row.original.status === 'open' ? 'Review' : 'View'}
          </Button>
        ),
        size: 110,
        meta: { headerTitle: 'Review' },
      },
    ],
    [],
  );

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (r) => r.exception_id,
    state: { pagination, sorting, globalFilter: searchQuery },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onGlobalFilterChange: setSearchQuery,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    globalFilterFn: (row, _id, value) => {
      const q = String(value ?? '').toLowerCase();
      if (!q) return true;
      const r = row.original;
      return (
        r.product_code.toLowerCase().includes(q) ||
        (r.product_name ?? '').toLowerCase().includes(q) ||
        (r.po_number ?? '').toLowerCase().includes(q)
      );
    },
  });

  if (isLoading) {
    return <Skeleton className="h-72 w-full rounded-xl" data-testid="plan-exceptions-loading" />;
  }

  if (isError) {
    return (
      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        <span className="flex size-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertCircle className="size-5" />
        </span>
        <p className="text-sm font-medium">Plan exceptions could not be loaded.</p>
        <p className="text-2xs text-muted-foreground">{(error as Error)?.message}</p>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => void refetch()}>
            Try again
          </Button>
          {onBack ? (
            <Button size="sm" variant="ghost" onClick={onBack}>
              <ArrowLeft className="size-3.5" />
              Back to plan
            </Button>
          ) : null}
        </div>
      </Card>
    );
  }

  const counts = data?.counts;
  const total = data?.rows.length ?? 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 break-words">
          {onBack ? (
            <Button variant="ghost" size="sm" className="-ms-2 mb-1 h-7 gap-1 px-2 text-muted-foreground" onClick={onBack}>
              <ArrowLeft className="size-3.5" />
              Back to plan
            </Button>
          ) : null}
          <h3 className="text-base font-semibold">Plan exceptions</h3>
          <p className="text-2xs text-muted-foreground">
            {counts
              ? // Both figures, because the reduction IS the screen (AC-D2b).
                `${fmtInt(counts.exception_count)} of ${fmtInt(
                  counts.delta_count,
                )} changed lines disagree with supply already placed · ${fmtInt(
                  counts.open_count,
                )} still open`
              : 'No batch yet'}
          </p>
          {/* The honest limit, on the screen rather than in a footnote. */}
          <p className="text-2xs text-muted-foreground">
            {data?.last_upload_at
              ? `Current as of the last order-book upload, ${computedAtLabel(data.last_upload_at)}. A change nobody has uploaded is not here yet.`
              : 'No order book has been uploaded yet, so nothing can disagree with it.'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute start-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Product or PO"
              className="h-8 w-52 ps-8"
              aria-label="Search plan exceptions"
            />
            {searchQuery ? (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute end-2 top-1/2 -translate-y-1/2 text-muted-foreground"
                aria-label="Clear search"
              >
                <X className="size-3.5" />
              </button>
            ) : null}
          </div>
          <SearchableSelect
            value={statusFilter}
            onChange={setStatusFilter}
            options={STATUS_OPTIONS}
            className="w-40"
            id="exception-status-filter"
          />
        </div>
      </div>

      {total === 0 ? (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <ShieldCheck className="size-5" />
          </span>
          <p className="text-sm font-medium">Nothing disagrees with placed supply.</p>
          <p className="text-2xs text-muted-foreground">
            Exceptions appear when a re-uploaded order book contradicts a purchase order that
            is already out.
          </p>
        </Card>
      ) : (
        <DataGrid
          table={table}
          recordCount={table.getFilteredRowModel().rows.length}
          listingKey={NO_COLUMN_PERSISTENCE}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          emptyMessage="No exception matches this search and filter."
        >
          <Card>
            <CardHeader className="py-3">
              <span className="text-2xs text-muted-foreground">
                Approving a move writes an allocation decision. No purchase order is amended
                without it.
              </span>
            </CardHeader>
            <CardTable>
              <DataGridTable />
            </CardTable>
            <CardFooter>
              <DataGridPagination />
            </CardFooter>
          </Card>
        </DataGrid>
      )}

      <PlanExceptionReviewSheet
        exception={reviewing}
        open={reviewing !== null}
        onOpenChange={(open) => {
          if (!open) setReviewing(null);
        }}
        isSaving={decide.isPending}
        onDecide={(input) => {
          decide.mutate(
            { productCode: reviewing?.product_code ?? '', input },
            { onSuccess: () => setReviewing(null) },
          );
        }}
      />
    </div>
  );
}

export default PlanExceptionsView;
