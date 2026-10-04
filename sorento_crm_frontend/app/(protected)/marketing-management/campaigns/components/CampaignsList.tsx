'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Plus, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { useCampaigns } from '../hooks/useCampaigns';
import type { Campaign } from '../types/campaign.types';
import { CAMPAIGN_STATUSES, campaignStatusLabel } from '../types/campaign.types';
import { formatDate } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function CampaignsList() {
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list has to read it (S3-01).
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(state.filters.status ?? 'all');
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [statusFilter, searchQuery]);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useCampaigns({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter !== 'all' ? statusFilter : undefined,
  });

  // D3: the row opens the campaign, chevron or no chevron - the chevron was
  // decoration on a row nothing could open. The href carries the list state the
  // detail page's Back and pager read back out of the URL, the status filter
  // included: it lives outside TanStack, so the grid cannot append it.
  const rowHref = (row: Campaign) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      statusFilter !== 'all' ? { status: statusFilter } : undefined,
    );
    return `/marketing-management/campaigns/${row.id}${search ? `?${search}` : ''}`;
  };

  const columns = useMemo<ColumnDef<Campaign>[]>(
    () => [
      buildSelectColumn<Campaign>(),
      {
        accessorKey: 'campaign_code',
        header: ({ column }) => <DataGridColumnHeader title="Campaign Code" column={column} />,
        size: 150,
        meta: { headerTitle: 'Campaign Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'campaign_name',
        header: ({ column }) => <DataGridColumnHeader title="Campaign Name" column={column} />,
        size: 250,
        meta: { headerTitle: 'Campaign Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'campaign_type.type_name',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => row.original.campaign_type?.type_name || '-',
        size: 150,
        meta: { headerTitle: 'Type' },
      },
      {
        accessorKey: 'start_date',
        header: ({ column }) => <DataGridColumnHeader title="Start Date" column={column} />,
        cell: ({ row }) => formatDate(new Date(row.original.start_date)),
        size: 120,
        meta: { headerTitle: 'Start Date' },
      },
      {
        accessorKey: 'end_date',
        header: ({ column }) => <DataGridColumnHeader title="End Date" column={column} />,
        cell: ({ row }) => row.original.end_date ? formatDate(new Date(row.original.end_date)) : '-',
        size: 120,
        meta: { headerTitle: 'End Date' },
      },
      {
        accessorKey: 'budget',
        header: ({ column }) => <DataGridColumnHeader title="Budget" column={column} />,
        cell: ({ row }) => {
          const budget = row.original.budget;
          return budget ? new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(budget) : '-';
        },
        size: 120,
        meta: { headerTitle: 'Budget' },
      },
      {
        accessorKey: 'spent',
        header: ({ column }) => <DataGridColumnHeader title="Spent" column={column} />,
        cell: ({ row }) => {
          const spent = row.original.spent;
          return spent ? new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(spent) : '-';
        },
        size: 120,
        meta: { headerTitle: 'Spent' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const status = row.original.status;
          return (
            <Badge status={status}>
              {campaignStatusLabel(status)}
            </Badge>
          );
        },
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: () => <ChevronRight className="text-muted-foreground/70 size-3.5" />,
        size: 40,
        enableHiding: false,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/marketing-management/campaigns/new')}>
      <Plus />
      Create Campaign
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      standardToolbar={false}
      tableLayout={{ columnsVisibility: true }}
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
                placeholder="Search campaigns..."
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
                      onChange={setStatusFilter}
                      options={[
                        { value: 'all', label: 'All statuses' },
                        ...CAMPAIGN_STATUSES.map((s) => ({ value: s.value, label: s.label })),
                      ]}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  {statusFilter !== 'all' && (
                    <div className="flex justify-end">
                      <Button variant="ghost" size="sm" onClick={() => setStatusFilter('all')}>
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'campaigns_export.xlsx' }}
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
