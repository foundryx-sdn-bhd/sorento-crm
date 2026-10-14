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
import { AlertCircle, ArrowLeft, ClipboardCheck, Search, X } from 'lucide-react';
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
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover';
import { EM_DASH, fmtInt, fmtSupplierCost } from '../../lib/format';
import { computedAtLabel, dayLabel } from '../lib/coverageTimeline';
import { isLegacyRun, planGrainLabel, type RunGrainState } from '../lib/planGrain';
import { decimalPlacesOf, fmtQty } from '../lib/qtyPrecision';
import { usePoWorklist, useSetKeyedStatus } from '../hooks/usePoWorklist';
import {
  KEYED_STATUS_LABELS,
  type KeyedStatus,
  type PoWorklistRow,
} from '../types/poWorklist.types';
import { distinctName } from '@/lib/product-display';

/**
 * The PO creation worklist (UAC Group E2).
 *
 * **Joey executes, she does not decide.** Mr Loo already chose a quantity and a
 * supplier on the Summary Order Report, so this screen carries no Accept, no
 * Reject and no quantity field. It shows what was decided (AC-E2.1) and adds the
 * three things somebody keying a purchase order needs and the report does not
 * hold: when the stock is needed, when the order therefore had to be placed, and
 * whether the PO has been keyed into AutoCount.
 *
 * The keyed status is MANUAL because nothing can detect it - no AutoCount
 * integration exists (AC-E2.2) - and filtering to not-keyed is the primary use of
 * the screen (AC-E2.4), so the filter sits in the toolbar rather than behind a
 * menu. It uses the shared status pill (AC-E2.3), mapped onto the existing palette
 * rather than adding a colour vocabulary of its own.
 *
 * A decision to buy NOTHING is a row (AC-E2.5), saying no PO is needed. Filtered
 * out, a use-pool decision would be indistinguishable from one nobody made, and
 * the worklist could not be reconciled one-for-one against the decisions.
 *
 * Phase 1 serves `lib/poWorklistMockStore`; Phase 2 flips the flag in
 * `services/poWorklistService.ts` and this component is untouched.
 */

/**
 * This grid shares `/scm/reorder` with the buy co-pilot, the allocation list and
 * the order summary, and pathname-derived column persistence would have them
 * clobber each other's saved columns. An empty key opts out, as the others do.
 */
const NO_COLUMN_PERSISTENCE = '';

const numMeta = { headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' };

/**
 * The keyed states mapped onto the SHARED pill palette (AC-E2.3): no new colours.
 * Not keyed is neutral, keying is the amber in-flight tone every pending form uses,
 * and keyed is the emerald the repo already means "done" by.
 */
const KEYED_PILL_CODE: Record<KeyedStatus, string> = {
  not_keyed: 'new',
  keying: 'pending',
  keyed: 'resolved',
};

/**
 * The default is OUTSTANDING - not keyed plus keying - not `not_keyed` alone.
 *
 * AC-E2.4 says filtering to not-keyed is the primary use, and the thing that is actually
 * being asked for is "what is left to do". A row somebody is mid-way through keying is
 * still left to do, and on a shared queue it is the row you most want to see: it is how
 * the second person knows not to start it.
 *
 * Filtering it out also made the screen contradict itself. The tile counts everything not
 * keyed, so marking a row `keying` left the tile reading 1 beside an empty list saying no
 * row matches - the same figure, disagreeing with itself one line apart.
 */
const OUTSTANDING = 'outstanding';

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: OUTSTANDING, label: 'Still to key' },
  { value: 'not_keyed', label: KEYED_STATUS_LABELS.not_keyed },
  { value: 'keying', label: KEYED_STATUS_LABELS.keying },
  { value: 'keyed', label: KEYED_STATUS_LABELS.keyed },
  { value: 'all', label: 'All statuses' },
];

function KeyedPill({ status }: { status: KeyedStatus }) {
  return (
    <span className={cn(STATUS_PILL_BASE, statusPillClass(KEYED_PILL_CODE[status]))}>
      {KEYED_STATUS_LABELS[status]}
    </span>
  );
}

export interface PoWorklistViewProps {
  /** Opaque run key. Null reads the newest completed plan. Never rendered. */
  runId?: string | null;
  /** Returns to the buy co-pilot. This report has no row in that grid to filter back to,
   *  so it needs its own way out - the tile that used to open it is gone. */
  onBack?: () => void;
}

export function PoWorklistView({ runId = null, onBack }: PoWorklistViewProps) {
  const query = { run_id: runId };
  const { data, isLoading, isPlaceholderData, isError, error, refetch } = usePoWorklist(query);
  const setStatus = useSetKeyedStatus(query);

  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [searchQuery, setSearchQuery] = useState('');
  // Defaults to what is still to key, because that IS the screen's primary use (AC-E2.4).
  // Landing on "all" would make the first action, every time, a click to narrow it.
  const [statusFilter, setStatusFilter] = useState<string>(OUTSTANDING);

  // The run's own two grain fields, as the server states them. A legacy run is the one
  // with a NULL contract version, so the version is read, never inferred from the grain.
  const runGrain = useMemo<RunGrainState | null>(
    () =>
      data
        ? {
            decision_grain: data.decision_grain,
            front_planning_contract_version: data.front_planning_contract_version,
          }
        : null,
    [data],
  );

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    if (statusFilter === 'all') return all;
    if (statusFilter === OUTSTANDING) {
      // A use-pool row has no purchase order, so it is never outstanding work - but it is
      // still a row under "All statuses", which is what keeps the worklist reconcilable
      // one-for-one against the decisions (AC-E2.5).
      return all.filter((r) => r.chosen_qty > 0 && r.keyed_status !== 'keyed');
    }
    return all.filter((r) => r.keyed_status === statusFilter);
  }, [data?.rows, statusFilter]);

  const columns = useMemo<ColumnDef<PoWorklistRow>[]>(
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
                title={row.original.product_name ?? undefined}
              >
                {row.original.product_name}
              </div>
            ) : null}
          </div>
        ),
        size: 220,
        meta: { headerTitle: 'Product', skeleton: <Skeleton className="h-8 w-40" /> },
      },
      {
        accessorKey: 'chosen_qty',
        header: ({ column }) => <DataGridColumnHeader title="Order qty" column={column} />,
        cell: ({ row }) =>
          // Zero is the use-pool decision, and it says so rather than showing a bare 0
          // that reads as a data error (AC-E2.5).
          row.original.chosen_qty === 0 ? (
            <span className="text-muted-foreground" title="Covered from stock, no PO needed">
              No PO needed
            </span>
          ) : (
            <span className="font-medium">
              {fmtQty(row.original.chosen_qty, decimalPlacesOf(row.original.uom_decimal_places))}
            </span>
          ),
        size: 130,
        meta: { headerTitle: 'Order qty', ...numMeta },
      },
      {
        id: 'suggested',
        accessorFn: (r) => r.suggested_qty,
        header: ({ column }) => (
          <DataGridColumnHeader title="Engine said" column={column} />
        ),
        // Kept beside the decision so a difference stays visible rather than being
        // something only the report remembers.
        cell: ({ row }) => (
          <span
            className={cn(
              row.original.chosen_qty !== row.original.suggested_qty &&
                'text-muted-foreground',
            )}
            title="What the reorder policy proposed before anyone decided"
          >
            {fmtQty(row.original.suggested_qty, decimalPlacesOf(row.original.uom_decimal_places))}
          </span>
        ),
        size: 120,
        meta: { headerTitle: 'Engine said', ...numMeta },
      },
      {
        accessorKey: 'chosen_supplier_name',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        cell: ({ row }) =>
          row.original.chosen_supplier_name ? (
            <span className="truncate" title={row.original.chosen_supplier_name}>
              {row.original.chosen_supplier_name}
            </span>
          ) : (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ),
        size: 200,
        meta: { headerTitle: 'Supplier' },
      },
      {
        // The run's own grain decides what this column carries (AC-F09): a product
        // decision shows the split it was allocated into, a location decision shows
        // the location it belongs to. The two never appear together.
        id: 'where',
        header: ({ column }) => <DataGridColumnHeader title="Locations" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          const dp = decimalPlacesOf(r.uom_decimal_places);
          if (r.warehouse_code) {
            return (
              <span className="truncate" title={r.warehouse_name ?? r.warehouse_code}>
                {r.warehouse_code}
              </span>
            );
          }
          const split = r.location_allocations ?? [];
          if (split.length === 0) {
            return <span className="text-muted-foreground">{EM_DASH}</span>;
          }
          return (
            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="rounded-sm text-2xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`Location split for ${r.product_code}`}
                >
                  {split.length} location{split.length === 1 ? '' : 's'}
                </button>
              </PopoverTrigger>
              <PopoverPortal>
                <PopoverContent align="start" collisionPadding={8} className="w-64 p-0">
                  <div className="border-b px-3 py-2 text-2xs font-semibold">
                    Split back to locations
                  </div>
                  {split.map((a) => (
                    <div
                      key={a.warehouse_code}
                      className="flex items-center justify-between gap-3 border-b px-3 py-1.5 text-2xs last:border-b-0"
                    >
                      <span className="truncate" title={a.warehouse_name}>
                        {a.warehouse_code}
                      </span>
                      <span className="tabular-nums">{fmtQty(a.allocated_qty, dp)}</span>
                    </div>
                  ))}
                </PopoverContent>
              </PopoverPortal>
            </Popover>
          );
        },
        size: 130,
        enableSorting: false,
        meta: { headerTitle: 'Locations' },
      },
      {
        accessorKey: 'need_by',
        header: ({ column }) => <DataGridColumnHeader title="Need by" column={column} />,
        cell: ({ row }) =>
          row.original.need_by ? (
            dayLabel(row.original.need_by)
          ) : (
            // Most of the book: nothing committed is uncovered, so the buy is a policy
            // replenishment rather than a response to an order that will otherwise miss.
            // Saying so beats a blank cell, which reads as missing data.
            <span
              className="text-2xs text-muted-foreground"
              title="No committed order is uncovered, so this is a policy replenishment"
            >
              no dated shortfall
            </span>
          ),
        size: 150,
        meta: { headerTitle: 'Need by' },
      },
      {
        accessorKey: 'place_by',
        header: ({ column }) => <DataGridColumnHeader title="Place by" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          if (!r.place_by) {
            return (
              <span
                className="text-2xs text-muted-foreground"
                title={
                  r.need_by
                    ? 'No lead time recorded, so a place-by date cannot be derived'
                    : 'No need-by date to work back from'
                }
              >
                {r.need_by ? 'no lead time' : EM_DASH}
              </span>
            );
          }
          return (
            <span className={cn(r.is_late && 'font-medium text-scm-stockout')}>
              {dayLabel(r.place_by)}
              {r.is_late ? (
                <span className="ms-1 text-2xs uppercase" title="Place-by date has passed">
                  late
                </span>
              ) : null}
            </span>
          );
        },
        size: 170,
        meta: { headerTitle: 'Place by' },
      },
      {
        id: 'cash',
        accessorFn: (r) => r.cash_committed ?? 0,
        header: ({ column }) => (
          <DataGridColumnHeader title="Cash (ex-works)" column={column} />
        ),
        cell: ({ row }) => {
          const r = row.original;
          // The COST decides whether there is anything to show. A missing currency does
          // not: a blank code means the base currency, so gating on it hid a real
          // committed figure behind "no cost recorded".
          if (r.cash_committed === null) {
            return (
              <span
                className="text-2xs text-muted-foreground"
                title="No recorded purchase cost for this item and supplier"
              >
                no cost recorded
              </span>
            );
          }
          return (
            <span
              title={`${fmtInt(r.chosen_qty)} at ${fmtSupplierCost(r.last_po_cost, r.last_po_currency)}`}
            >
              {fmtSupplierCost(r.cash_committed, r.last_po_currency)}
            </span>
          );
        },
        size: 170,
        meta: { headerTitle: 'Cash (ex-works)', ...numMeta },
      },
      {
        accessorKey: 'decided_by',
        header: ({ column }) => <DataGridColumnHeader title="Decided" column={column} />,
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate" title={row.original.decided_by}>
              {row.original.decided_by}
            </div>
            <div className="truncate text-2xs text-muted-foreground">
              {computedAtLabel(row.original.decided_at)}
            </div>
          </div>
        ),
        size: 170,
        meta: { headerTitle: 'Decided' },
      },
      {
        // ONE column, not two. The pill is the at-a-glance signal (AC-E2.3) and the select
        // is the control (AC-E2.2), and a select already renders its own value - so a
        // separate status column repeated every row's state beside itself, on a screen
        // whose stated requirement is avoiding information fatigue.
        accessorKey: 'keyed_status',
        header: ({ column }) => (
          <DataGridColumnHeader title="Keyed into AutoCount" column={column} />
        ),
        cell: ({ row }) => {
          const r = row.original;
          if (r.chosen_qty === 0) {
            // Nothing to key: there is no purchase order. The cell says that rather
            // than offering a control that would record a fiction.
            return (
              <span className="text-2xs text-muted-foreground">nothing to key</span>
            );
          }
          return (
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <KeyedPill status={r.keyed_status} />
                <SearchableSelect
                  value={r.keyed_status}
                  onChange={(v: string) =>
                    setStatus.mutate({
                      productCode: r.product_code,
                      input: {
                        run_id: data?.run_id ?? '',
                        keyed_status: v as KeyedStatus,
                        // A location-grain row is its own purchase order, keyed on its
                        // own; a product-grain row names no location and keys the product.
                        ...(r.warehouse_code ? { warehouse_code: r.warehouse_code } : {}),
                      },
                    })
                  }
                  options={[
                    { value: 'not_keyed', label: KEYED_STATUS_LABELS.not_keyed },
                    { value: 'keying', label: KEYED_STATUS_LABELS.keying },
                    { value: 'keyed', label: KEYED_STATUS_LABELS.keyed },
                  ]}
                  disabled={setStatus.isPending}
                  id={`keyed-status-${r.product_code}${r.warehouse_code ? `-${r.warehouse_code}` : ''}`}
                  size="sm"
                  triggerClassName="w-28"
                  renderTriggerLabel={() => <span className="text-2xs">Change</span>}
                />
              </div>
              {r.keyed_by ? (
                <span className="text-2xs text-muted-foreground">
                  {r.keyed_by} · {computedAtLabel(r.keyed_at)}
                </span>
              ) : null}
            </div>
          );
        },
        size: 250,
        meta: { headerTitle: 'Keyed into AutoCount' },
      },
    ],
    [data?.run_id, setStatus],
  );

  const table = useReactTable({
    data: rows,
    columns,
    // A location-grain run lists one row per product AND location, so the product
    // code alone is not unique on it.
    getRowId: (r) => (r.warehouse_code ? `${r.product_code}:${r.warehouse_code}` : r.product_code),
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
        (r.chosen_supplier_name ?? '').toLowerCase().includes(q)
      );
    },
  });

  if (isLoading) {
    return <Skeleton className="h-72 w-full rounded-xl" data-testid="po-worklist-loading" />;
  }

  if (isError) {
    return (
      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        <span className="flex size-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertCircle className="size-5" />
        </span>
        <p className="text-sm font-medium">The PO worklist could not be loaded.</p>
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

  const total = data?.rows.length ?? 0;
  const notKeyed = (data?.rows ?? []).filter(
    (r) => r.chosen_qty > 0 && r.keyed_status !== 'keyed',
  ).length;

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
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold">PO worklist</h3>
            {/* Which grain this list is keyed on. A fact about the run, not a control. */}
            {runGrain ? (
              <Badge
                variant={isLegacyRun(runGrain) ? 'secondary' : 'info'}
                appearance="light"
                size="sm"
                data-testid="worklist-grain-chip"
              >
                {planGrainLabel(runGrain)}
              </Badge>
            ) : null}
          </div>
          <p className="text-2xs text-muted-foreground">
            {total === 0
              ? 'Nothing decided yet, so there is nothing to key'
              : `${fmtInt(notKeyed)} of ${fmtInt(total)} still to key${
                  data?.as_of ? ` · decided ${dayLabel(data.as_of)}` : ''
                }`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute start-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Product or supplier"
              className="h-8 w-52 ps-8"
              aria-label="Search the worklist"
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
          {/* In the toolbar, not behind a menu: filtering to not-keyed is the primary
              use of this screen (AC-E2.4). */}
          <SearchableSelect
            value={statusFilter}
            onChange={setStatusFilter}
            options={STATUS_OPTIONS}
            className="w-44"
            id="keyed-status-filter"
          />
        </div>
      </div>

      {total === 0 ? (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <ClipboardCheck className="size-5" />
          </span>
          <p className="text-sm font-medium">No decisions to key yet.</p>
          <p className="text-2xs text-muted-foreground">
            Set an order quantity on the order summary and it appears here.
          </p>
        </Card>
      ) : (
        <DataGrid
          isPlaceholderData={isPlaceholderData}
          table={table}
          recordCount={table.getFilteredRowModel().rows.length}
          listingKey={NO_COLUMN_PERSISTENCE}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          emptyMessage="No row matches this search and filter."
        >
          <Card>
            <CardHeader className="py-3">
              <span className="text-2xs text-muted-foreground">
                Joey executes what was decided. To change a quantity or a supplier, go back
                to the order summary.
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
    </div>
  );
}

export default PoWorklistView;
