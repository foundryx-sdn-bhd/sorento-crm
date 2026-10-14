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
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { EM_DASH, fmtInt, fmtTrimmedDecimal } from '../../lib/format';
import { acceptSuggestedLevel } from '../services/reorderRunService';
import { todayRunKey } from '../hooks/useReorderRun';
import type { ReorderRecommendation } from '../types/reorder.types';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { distinctName } from '@/lib/product-display';

/**
 * Items the plan could not size because nobody has set a reorder level for them.
 *
 * The plan runs on the level the buyer owns, so an item without one has no quantity we are
 * entitled to propose. Guessing would be the engine deciding again, and leaving the item
 * out would report "nothing to do" for stock that has simply never been set up. So it gets
 * its own band, carrying the suggestion and the months it came from, and one click turns it
 * into a plannable item.
 *
 * Accepting copies the suggestion into the buyer's own level AT THE VALUE ON SCREEN. A
 * later refresh moves the suggestion, never the number somebody agreed to.
 */

/** Shares `/scm/reorder` with the other grids; an empty key opts out of persistence. */
const NO_COLUMN_PERSISTENCE = '';

const numMeta = { headerClassName: 'text-end', cellClassName: 'text-end tabular-nums' };

/** "0.4 a day x 30 day lead + 14 days safety" - the arithmetic, not just the answer. */
function basisText(r: ReorderRecommendation): string {
  const b = r.suggestion_basis;
  if (!b) return EM_DASH;
  if (b.no_movement) return `Nothing moved in ${b.window_days ?? 90} days`;
  if (b.adu == null || b.lead_time_days == null) return EM_DASH;
  return `${fmtTrimmedDecimal(b.adu, 3)} a day x ${fmtInt(b.lead_time_days)} day lead + ${fmtInt(b.safety_days ?? 14)} days safety`;
}

export function NeedsLevelView({
  rows,
  isLoading,
  isError,
  error,
  runId,
}: {
  rows: ReorderRecommendation[];
  isLoading: boolean;
  isError?: boolean;
  error?: unknown;
  runId?: string | null;
}) {
  const qc = useQueryClient();
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
  } = useDebouncedSearch();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [pending, setPending] = useState<string | null>(null);
  // What has been accepted, before the next run reflects it. Without this the row looks
  // untouched and the click reads as broken.
  const [accepted, setAccepted] = useState<Record<string, number>>({});

  const accept = useMutation({
    mutationFn: ({ r }: { r: ReorderRecommendation }) =>
      acceptSuggestedLevel(r.product_id as string, (r.warehouse_id as string) ?? null),
    onMutate: ({ r }) => setPending(r.id),
    onSettled: () => setPending(null),
    onSuccess: (_res, { r }) => {
      setAccepted((m) => ({ ...m, [r.id]: r.suggested_level ?? 0 }));
      void qc.invalidateQueries({ queryKey: ['scm', 'reorder', 'needs-level-recs', runId] });
      void qc.invalidateQueries({ queryKey: todayRunKey });
      // Said plainly: the level is stored now, but THIS plan was already computed without
      // it. Pretending the row is now planned would be the dishonest version.
      toast.success(`Level set for ${r.sku}. The next plan will size it.`);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed to accept the level'),
  });

  const columns = useMemo<ColumnDef<ReorderRecommendation>[]>(
    () => [
      {
        accessorKey: 'sku',
        header: ({ column }) => <DataGridColumnHeader title="SKU" column={column} />,
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate text-xs font-medium" title={row.original.sku}>
              {row.original.sku}
            </div>
            {distinctName(row.original.sku, row.original.product_name) ? (
              <div
                className="truncate text-2xs text-muted-foreground"
                title={row.original.product_name ?? undefined}
              >
                {row.original.product_name}
              </div>
            ) : null}
          </div>
        ),
        size: 240,
        meta: { headerTitle: 'SKU' },
      },
      {
        accessorKey: 'warehouse_code',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        cell: ({ row }) => (
          <span className="text-xs">{row.original.warehouse_code ?? EM_DASH}</span>
        ),
        size: 120,
        meta: { headerTitle: 'Location' },
      },
      {
        accessorKey: 'segment',
        header: ({ column }) => <DataGridColumnHeader title="Sells to" column={column} />,
        cell: ({ row }) => (
          <span className="text-xs capitalize">{row.original.segment ?? EM_DASH}</span>
        ),
        size: 100,
        meta: { headerTitle: 'Sells to' },
      },
      {
        accessorKey: 'on_hand',
        header: ({ column }) => <DataGridColumnHeader title="On hand" column={column} />,
        cell: ({ row }) => <span className="text-xs">{fmtInt(row.original.on_hand ?? 0)}</span>,
        size: 100,
        meta: { headerTitle: 'On hand', ...numMeta },
      },
      {
        accessorKey: 'outstanding_sales',
        header: ({ column }) => <DataGridColumnHeader title="Sold" column={column} />,
        cell: ({ row }) => (
          <span className="text-xs">{fmtInt(row.original.outstanding_sales ?? 0)}</span>
        ),
        size: 100,
        meta: { headerTitle: 'Sold', ...numMeta },
      },
      {
        accessorKey: 'suggested_level',
        header: ({ column }) => <DataGridColumnHeader title="Suggested" column={column} />,
        cell: ({ row }) => {
          const taken = accepted[row.original.id];
          return (
            <span className="text-xs font-medium">
              {fmtInt(taken ?? row.original.suggested_level ?? 0)}
            </span>
          );
        },
        size: 110,
        meta: { headerTitle: 'Suggested', ...numMeta },
      },
      {
        id: 'basis',
        header: ({ column }) => <DataGridColumnHeader title="How we got it" column={column} />,
        cell: ({ row }) => {
          const t = basisText(row.original);
          return (
            <span className="block truncate text-2xs text-muted-foreground" title={t}>
              {t}
            </span>
          );
        },
        size: 300,
        enableSorting: false,
        meta: { headerTitle: 'How we got it' },
      },
      {
        id: 'actions',
        header: () => <span className="text-end">Set level</span>,
        cell: ({ row }) => {
          const r = row.original;
          const busy = pending === r.id;
          const done = accepted[r.id] != null;
          return (
            <div className="flex justify-end">
              <Button
                size="sm"
                variant={done ? 'primary' : 'outline'}
                disabled={busy || done}
                onClick={() => accept.mutate({ r })}
              >
                {busy ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : done ? (
                  <Check className="size-3.5" aria-hidden />
                ) : null}
                {done ? 'Level set' : 'Accept'}
              </Button>
            </div>
          );
        },
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Set level', headerClassName: 'text-end' },
      },
    ],
    [accept, accepted, pending],
  );

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (r) => r.id,
    state: { pagination, sorting, globalFilter: searchQuery },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
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
        r.sku.toLowerCase().includes(q) ||
        (r.product_name ?? '').toLowerCase().includes(q) ||
        (r.warehouse_code ?? '').toLowerCase().includes(q)
      );
    },
  });

  if (isLoading) {
    return <Skeleton className="h-72 w-full rounded-xl" data-testid="needs-level-loading" />;
  }

  if (isError) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        {error instanceof Error ? error.message : 'Failed to load these rows.'}
      </Card>
    );
  }

  if (!rows.length) {
    return (
      <Card className="p-8 text-center">
        <p className="text-sm font-medium">Every item in this plan has a level.</p>
        <p className="mt-1 text-2xs text-muted-foreground">
          Nothing was left unsized.
        </p>
      </Card>
    );
  }

  const outstanding = rows.filter((r) => accepted[r.id] == null).length;

  return (
    <DataGrid
      table={table}
      recordCount={table.getFilteredRowModel().rows.length}
      listingKey={NO_COLUMN_PERSISTENCE}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
      tableClassNames={{
        headerSticky: 'sticky top-0 z-(--z-sticky-content) bg-background shadow-sm',
      }}
      emptyMessage="No unsized item matches this search."
    >
      <Card>
        <CardHeader className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-sm font-semibold">
            Needs a level
            <span className="ms-2 text-2xs font-normal text-muted-foreground">
              {fmtInt(outstanding)} still to set of {fmtInt(rows.length)}
            </span>
          </h3>
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder="Search SKU, product, or location..."
            className="w-full sm:w-72"
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}

export default NeedsLevelView;
