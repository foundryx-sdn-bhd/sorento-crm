'use client';

/**
 * The captain's review queue (UAC AC-6.1).
 *
 * A request is a batch of people waiting on one decision, so the row answers
 * the two questions that decide whether he opens it: who asked, and how many
 * people are waiting.
 */

import { useEffect, useMemo, useState } from 'react';

import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type RowSelectionState,
  type SortingState,
} from '@tanstack/react-table';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { useOnboardingRequestActions } from '../actions';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateInMalaysia } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { NewOnboardingRequestDialog } from './NewOnboardingRequestDialog';
import {
  ONBOARDING_STATUS_LABELS,
  ONBOARDING_STATUS_PILL_CODES,
  type OnboardingRequestStatus,
  type OnboardingRequestSummary,
} from '@/components/common/onboarding/types';
import { statusPillClass, STATUS_PILL_BASE } from '@/lib/status-pill';
import { useOnboardingRequests } from '../hooks/useOnboardingRequests';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';

const STATUS_OPTIONS = [
  { value: 'all', label: 'All statuses' },
  ...(Object.keys(ONBOARDING_STATUS_LABELS) as OnboardingRequestStatus[]).map((status) => ({
    value: status,
    label: ONBOARDING_STATUS_LABELS[status],
  })),
];

/** Stored naive UTC, read as Malaysia wall-clock, never as the browser's zone. */
function day(value: string | null): string {
  return value ? formatDateInMalaysia(value) : '-';
}

/**
 * The row's "..." (D15): the same set the record's gear renders, so a captain can
 * revoke a link that is already out without opening the request first. Its own
 * component because the action set is a hook.
 */
function OnboardingRowActions({ request }: { request: OnboardingRequestSummary }) {
  const { actions } = useOnboardingRequestActions(request, { surface: 'toast' });
  return <RowActionsMenu ariaLabel="onboarding request" actions={actions} />;
}

export function OnboardingRequestList() {
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState('all');
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(state.filters.status_key ?? 'all');
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const firstPage = () => setPagination((p) => ({ ...p, pageIndex: 0 }));

  /**
   * Anything that changes WHICH rows match sends the reader back to page one.
   * Searching from page 3 otherwise narrows the set to two rows and then asks
   * for the third page of it, which renders as an empty grid and reads like
   * "nothing matched". On a CHANGE only - the mount run used to stamp page 1
   * over the page `useListStateFromUrl` had just restored from the URL.
   */
  useResetPageOnFilterChange(setPagination, [searchQuery]);

  const isFiltered = searchQuery.trim().length > 0 || statusFilter !== 'all';

  const listParams = {
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    statusKey: statusFilter === 'all' ? undefined : statusFilter,
  };

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useOnboardingRequests(listParams);

  // A selection that survives a page or filter change points at rows that are
  // no longer on screen, which is what makes a bulk action act on the wrong set.
  useEffect(() => {
    setRowSelection({});
  }, [searchQuery, statusFilter, pagination.pageIndex, pagination.pageSize, sorting]);

  const columns = useMemo<ColumnDef<OnboardingRequestSummary>[]>(
    () => [
      buildSelectColumn<OnboardingRequestSummary>(),
      {
        id: 'title',
        accessorFn: (row) => row.title,
        header: ({ column }) => <DataGridColumnHeader title="Request" column={column} />,
        size: 220,
        meta: { headerTitle: 'Request', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) => (
          <span className="font-medium truncate block" title={row.original.title}>
            {row.original.title}
          </span>
        ),
      },
      {
        id: 'company_name',
        accessorFn: (row) => row.company_name,
        header: ({ column }) => <DataGridColumnHeader title="Company" column={column} />,
        size: 180,
        meta: { headerTitle: 'Company', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => (
          <span className="truncate block" title={row.original.company_name}>
            {row.original.company_name}
          </span>
        ),
      },
      {
        id: 'requester_name',
        accessorFn: (row) => row.requester_name,
        header: ({ column }) => <DataGridColumnHeader title="From" column={column} />,
        size: 180,
        enableSorting: false,
        meta: { headerTitle: 'From', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate" title={row.original.requester_name}>
              {row.original.requester_name}
            </p>
            <p className="text-xs text-muted-foreground truncate" title={row.original.requester_email}>
              {row.original.requester_email}
            </p>
          </div>
        ),
      },
      {
        id: 'people_count',
        accessorFn: (row) => row.people_count,
        header: ({ column }) => <DataGridColumnHeader title="People" column={column} />,
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'People', skeleton: <Skeleton className="h-4 w-10" /> },
        cell: ({ row }) => <span>{row.original.people_count}</span>,
      },
      {
        id: 'status',
        accessorFn: (row) => row.status,
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => (
          <span
            className={`${STATUS_PILL_BASE} ${statusPillClass(
              ONBOARDING_STATUS_PILL_CODES[row.original.status],
            )}`}
          >
            {ONBOARDING_STATUS_LABELS[row.original.status]}
          </span>
        ),
      },
      {
        id: 'submitted_at',
        accessorFn: (row) => row.submitted_at ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Submitted" column={column} />,
        size: 140,
        meta: { headerTitle: 'Submitted', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => (
          <span className="truncate block" title={day(row.original.submitted_at)}>
            {day(row.original.submitted_at)}
          </span>
        ),
      },
      {
        id: 'expires_at',
        accessorFn: (row) => row.expires_at,
        header: ({ column }) => <DataGridColumnHeader title="Link expires" column={column} />,
        size: 140,
        meta: { headerTitle: 'Link expires', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => (
          <span className="truncate block" title={day(row.original.expires_at)}>
            {day(row.original.expires_at)}
          </span>
        ),
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        size: 60,
        enableHiding: false,
        enableSorting: false,
        cell: ({ row }) => <OnboardingRowActions request={row.original} />,
      },
    ],
    [],
  );

  const total = data?.pagination.total ?? 0;

  const table = useReactTable({
    columns,
    data: data?.data ?? [],
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: (updater) =>
      setPagination((current) => {
        const next = typeof updater === 'function' ? updater(current) : updater;
        // Growing the page size keeps the OLD page index, which on the last
        // page addresses rows that no longer exist at the new size.
        return next.pageSize !== current.pageSize ? { ...next, pageIndex: 0 } : next;
      }),
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <NewOnboardingRequestDialog />
  );

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      // An empty queue and a filter that matched nothing need different words:
      // "create one to get started" is wrong advice when there are requests,
      // just not these ones.
      emptyMessage={
        isFiltered
          ? 'No requests match your filters.'
          : 'No onboarding requests yet. Create one to get started.'
      }
      rowHref={(row) => {
        // The whole row opens the record, carrying the list query the pager
        // rebuilds its key from.
        const qs = buildDetailSearch(
          {
            pageIndex: pagination.pageIndex,
            pageSize: pagination.pageSize,
            sorting,
            searchQuery,
          },
          { status_key: statusFilter === 'all' ? undefined : statusFilter },
        );
        const id = (row as OnboardingRequestSummary).id;
        return `/user-management/onboarding-requests/${id}${qs ? `?${qs}` : ''}`;
      }}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search requests..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: statusFilter !== 'all',
              activeCount: statusFilter !== 'all' ? 1 : 0,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter}
                      onChange={(value) => {
                        setStatusFilter(value || 'all');
                        firstPage();
                      }}
                      options={STATUS_OPTIONS}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                      // "Completed with failures" is wider than the filter
                      // popover, and cut short it reads as plain "Completed".
                      wrapOptions
                    />
                  </div>
                  {statusFilter !== 'all' ? (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setStatusFilter('all');
                          firstPage();
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  ) : null}
                </div>
              ),
            }}
            exportConfig={{ filename: 'onboarding_requests_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
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
