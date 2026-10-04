'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { PlayCircle, RefreshCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar, type ToolbarAction } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { EM_DASH, fmtDate, fmtInt, fmtMoney } from '../../lib/format';
import { describeWindow } from '../lib/runListing';
import { runHistoryKey, todayRunKey, useReorderRuns } from '../hooks/useReorderRun';
import { createReorderRun, type ReorderRunHistoryItem } from '../services/reorderRunService';
import { runStatusReading, runStartedLabel } from '../lib/runListing';
import { RunPlanningModal, type ManualPlanInputs } from './RunPlanningModal';
import { useUploadDataActions } from './UploadDataMenu';

/**
 * The plans list (`/scm/reorder`).
 *
 * A plan is a record like any other now: a DataGrid row that opens at `/scm/reorder/{id}`.
 * What stood here before was `RunHistoryPanel` - a card of hand-rolled buttons UNDER the
 * latest plan's own grid, eight per page, which resolved a `?plan=` deep link against page
 * one only and silently ignored anything older.
 *
 * One primary button, Start Plan. The file uploads that feed the next run sit in the
 * Actions menu beside Refresh, because they feed a run that has not happened yet.
 */
export function ReorderRunsGrid({ autoOpenRun = false }: { autoOpenRun?: boolean }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [modalOpen, setModalOpen] = useState(autoOpenRun);
  const [starting, setStarting] = useState(false);

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = useReorderRuns({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
  });

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  const uploads = useUploadDataActions(() => {
    void queryClient.invalidateQueries({ queryKey: todayRunKey });
    void queryClient.invalidateQueries({ queryKey: runHistoryKey });
  });

  const secondaryActions = useMemo<ToolbarAction[]>(
    () => [
      ...uploads.actions,
      { key: 'refresh', label: 'Refresh', icon: RefreshCw, onClick: () => void refetch() },
    ],
    [uploads.actions, refetch],
  );

  /**
   * Start a plan and go straight to it (plan 4.2). The run is accepted (202) long before it
   * finishes, so the navigation is on the ACCEPT: the plan page shows its own progress state
   * until the worker writes the recommendations. Waiting here would leave the buyer on a
   * list with a spinner and no way to see what they just started.
   */
  const start = async (inputs: ManualPlanInputs) => {
    setStarting(true);
    try {
      const created = await createReorderRun({
        warehouse_codes: inputs.warehouse_codes,
        product_codes: inputs.product_codes,
        budget_id: null,
        plan_horizon_start: inputs.plan_horizon_start || null,
        plan_horizon_date: inputs.plan_horizon_date || null,
        demand_class: inputs.demand_class,
        so_numbers: inputs.so_numbers,
      });
      setModalOpen(false);
      void queryClient.invalidateQueries({ queryKey: runHistoryKey });
      void queryClient.invalidateQueries({ queryKey: todayRunKey });
      router.push(`/scm/reorder/${created.run_id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start the plan');
    } finally {
      setStarting(false);
    }
  };

  const columns = useMemo<ColumnDef<ReorderRunHistoryItem>[]>(
    () => [
      {
        id: 'started_at',
        accessorFn: (row) => row.started_at ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Plan" visibility column={column} />,
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-2">
            {/* `truncate` needs its `title`: the badges beside it eat into the column's
                fixed width, so the timestamp is the part that clips. */}
            <span
              className="truncate text-sm font-medium tabular-nums"
              title={runStartedLabel(row.original.started_at)}
            >
              {runStartedLabel(row.original.started_at)}
            </span>
            {/* The scheduled run, told apart from one a person started. Absent until the
                backend emits `is_scheduled` (PHASE 2) - never guessed from the clock. */}
            {row.original.is_scheduled ? (
              <Badge variant="secondary" appearance="light" size="sm">
                daily
              </Badge>
            ) : null}
            {/* The run the low stock report tool started over WhatsApp (AC-4). Same
                identity column, no new one: it is another thing to know about WHICH plan
                this is, and only the backend says so (`requested_via`). */}
            {row.original.requested_via === 'chat' ? (
              <Badge variant="secondary" appearance="light" size="sm">
                via chat
              </Badge>
            ) : null}
            {/* AC-5.4: the superseded run stays readable and labelled here - a Re-plan
                (S5, G8) never hides its history, it only says a newer one replaced it. */}
            {row.original.superseded_by_run_id ? (
              <Badge variant="secondary" appearance="light" size="sm">
                superseded
              </Badge>
            ) : null}
          </div>
        ),
        size: 190,
        enableSorting: true,
        meta: { headerTitle: 'Plan', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        // S8 (Phase 3 fix round): both ends of the window, the same wording the header
        // and subtitle already use (`describeWindow`), so the plans list never states the
        // end alone while a run also carries a start.
        id: 'plan_horizon_date',
        accessorFn: (row) => row.plan_horizon_date ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Sales orders needed" visibility column={column} />
        ),
        cell: ({ row }) => {
          const text = describeWindow(
            row.original.plan_horizon_start, row.original.plan_horizon_date,
          );
          return text === 'every open order' ? (
            <span className="text-muted-foreground" title="Every open order counted">
              {text}
            </span>
          ) : (
            <span className="tabular-nums">{text}</span>
          );
        },
        size: 190,
        enableSorting: true,
        meta: { headerTitle: 'Sales orders needed', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'warehouses',
        accessorFn: (row) => row.warehouse_codes.join(', '),
        header: ({ column }) => (
          <DataGridColumnHeader title="Warehouses" visibility column={column} />
        ),
        cell: ({ row }) => {
          const codes = row.original.warehouse_codes ?? [];
          const full = codes.join(', ');
          // A plan launched with no warehouse scope stores EVERY active warehouse, so the
          // cell read "60 warehouses" for what the buyer asked for as "all". The backend
          // says which it is - only it knows how many active warehouses there are.
          if (!codes.length || row.original.is_all_warehouses) {
            return (
              <span className="text-muted-foreground" title={full || undefined}>
                All
              </span>
            );
          }
          const shown = codes.length > 3 ? `${codes.length} warehouses` : full;
          return (
            <span className="block truncate" title={full}>
              {shown}
            </span>
          );
        },
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Warehouses', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'products',
        accessorFn: (row) => row.product_count ?? -1,
        header: ({ column }) => <DataGridColumnHeader title="Products" visibility column={column} />,
        // Null is the WHOLE catalogue (the daily run's own scope), not an unknown - the
        // backend stores no product list for a run that narrowed to nothing.
        cell: ({ row }) =>
          row.original.product_count === null || row.original.product_count === undefined ? (
            <span className="text-muted-foreground">All</span>
          ) : (
            <span className="tabular-nums">{fmtInt(row.original.product_count)}</span>
          ),
        size: 110,
        enableSorting: true,
        meta: { headerTitle: 'Products', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      {
        id: 'lines',
        accessorFn: (row) => row.summary?.recommendation_count ?? -1,
        header: ({ column }) => <DataGridColumnHeader title="Lines" visibility column={column} />,
        cell: ({ row }) =>
          row.original.summary ? (
            <span className="tabular-nums">
              {fmtInt(row.original.summary.recommendation_count)}
            </span>
          ) : (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ),
        size: 100,
        enableSorting: true,
        meta: { headerTitle: 'Lines', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      {
        id: 'decided',
        accessorFn: (row) => row.decided_product_count ?? -1,
        header: ({ column }) => <DataGridColumnHeader title="Decided" visibility column={column} />,
        // Products, never locations (R14): a product across three bins is one decision.
        cell: ({ row }) => {
          const decided = row.original.decided_product_count;
          // The products the plan actually wrote rows for. `product_count` is the SCOPE and
          // is null on the daily run, which would leave the commonest plan reading "12 / -".
          const total = row.original.planned_product_count ?? row.original.product_count;
          if (decided === null || decided === undefined) {
            return <span className="text-muted-foreground">{EM_DASH}</span>;
          }
          return (
            <span className="tabular-nums" title="Products decided on this plan">
              {fmtInt(decided)} / {total === null || total === undefined ? EM_DASH : fmtInt(total)}
            </span>
          );
        },
        size: 110,
        enableSorting: true,
        meta: { headerTitle: 'Decided', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      {
        id: 'status',
        accessorFn: (row) => row.status,
        header: ({ column }) => <DataGridColumnHeader title="Status" visibility column={column} />,
        cell: ({ row }) => {
          const reading = runStatusReading(row.original);
          return (
            <Badge variant={reading.variant} appearance="light" size="sm">
              {reading.label}
            </Badge>
          );
        },
        size: 120,
        enableSorting: true,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-5 w-16" /> },
      },
      {
        id: 'cash',
        accessorFn: (row) => row.summary?.total_cash_impact ?? -1,
        header: ({ column }) => (
          <DataGridColumnHeader title="Cash if all accepted" visibility column={column} />
        ),
        cell: ({ row }) =>
          row.original.summary ? (
            <span className="tabular-nums">
              {fmtMoney(row.original.summary.total_cash_impact)}
            </span>
          ) : (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ),
        size: 170,
        enableSorting: true,
        meta: { headerTitle: 'Cash if all accepted', skeleton: <Skeleton className="h-4 w-20" /> },
      },
    ],
    [],
  );

  const rows = data?.data ?? [];

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: data?.pagination.total_pages ?? 0,
    getRowId: (row: ReorderRunHistoryItem) => row.run_id,
    state: { pagination, sorting },
    columnResizeMode: 'onChange',
    manualPagination: true,
    manualSorting: true,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => setModalOpen(true)}>
      <PlayCircle className="size-4" />
      Start Plan
    </Button>
  );

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total ?? 0}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        emptyMessage="No plans yet. Start Plan builds one from the order book you last uploaded."
        // The whole row opens the plan (A3) - there is nothing else to do with a run.
        onRowClick={(row) => router.push(`/scm/reorder/${row.run_id}`)}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        listingKey="scm.reorder.run"
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              secondaryActions={secondaryActions}
              primaryAction={listPrimaryAction}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                  placeholder="Search plans by warehouse"
                  className="w-full sm:w-64"
                />
              }
              onRefresh={() => void refetch()}
              isRefreshing={isFetching}
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

      {uploads.dialogs}

      <RunPlanningModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        onSubmit={start}
        isSubmitting={starting}
      />
    </>
  );
}
