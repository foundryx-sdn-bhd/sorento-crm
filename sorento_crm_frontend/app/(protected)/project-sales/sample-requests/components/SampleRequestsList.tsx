'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import {
  type ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Plus } from 'lucide-react';
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
import { Skeleton } from '@/components/ui/skeleton';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { useHasPermission } from '@/hooks/usePermissions';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { formatDateInMalaysia } from '@/lib/helpers';
import { getUserLookup } from '@/services/userSelectService';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import {
  DELIVERY_MODE_LABELS,
  type SampleRequestRow,
  type SampleRequestStatus,
} from '@/types/sampleRequest';
import { useSampleRequestsQuery } from '../hooks/useSampleRequests';

const BASE_PATH = '/project-sales/sample-requests';

/** `open` is the server default (the coordinator queue); `all` names every status. */
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'open', label: 'Open requests' },
  { value: 'all', label: 'All statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'processing', label: 'Processing' },
  { value: 'customer_confirmed', label: 'Customer confirmed' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'cancelled', label: 'Cancelled' },
];

const ALL_KEYS: SampleRequestStatus[] = [
  'draft',
  'submitted',
  'processing',
  'customer_confirmed',
  'delivered',
  'cancelled',
];

function statusParam(filter: string): string | undefined {
  if (filter === 'open') return undefined;
  if (filter === 'all') return ALL_KEYS.join(',');
  return filter;
}

/** The inverse of {@link statusParam}: the URL carries the `status` param `rowHref` wrote. */
function statusFilterFromParam(param: string | undefined): string {
  if (!param) return 'open';
  return param === ALL_KEYS.join(',') ? 'all' : param;
}

export function deliveryText(row: Pick<SampleRequestRow, 'delivery_date'>) {
  return row.delivery_date ? formatDateInMalaysia(row.delivery_date) : '-';
}

export default function SampleRequestsList() {
  const router = useRouter();
  const canCreate = useHasPermission('projects.sample_requests.create');
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState('open');
  const [salespersonFilter, setSalespersonFilter] = useState('__all__');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(statusFilterFromParam(state.filters.status));
    setSalespersonFilter(state.filters.salesperson_id ?? '__all__');
  });

  const { data: salespeople = [] } = useQuery({
    queryKey: ['user-lookup', 'with-inactive'],
    queryFn: () => getUserLookup({ include_inactive: true }),
    staleTime: 5 * 60_000,
  });

  const status = statusParam(statusFilter);
  const salespersonId = salespersonFilter !== '__all__' ? salespersonFilter : undefined;
  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } =
    useSampleRequestsQuery({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      status,
      salespersonId,
    });

  useResetPageOnFilterChange(setPagination, [statusFilter, salespersonFilter, searchQuery]);

  const rowHref = (row: SampleRequestRow) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      { status, salesperson_id: salespersonId },
    );
    return `${BASE_PATH}/${row.id}${search ? `?${search}` : ''}`;
  };

  const columns = useMemo<ColumnDef<SampleRequestRow>[]>(
    () => [
      buildSelectColumn<SampleRequestRow>(),
      {
        accessorKey: 'request_no',
        header: ({ column }) => <DataGridColumnHeader title="Number" column={column} />,
        size: 130,
        cell: ({ row }) => (
          <span className="truncate font-medium tabular-nums" title={row.original.request_no}>
            {row.original.request_no}
          </span>
        ),
        meta: { headerTitle: 'Number', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'submitted_at',
        header: ({ column }) => <DataGridColumnHeader title="Submitted" column={column} />,
        size: 120,
        // A naive UTC stamp: shown as the Malaysia calendar day, like every other form.
        cell: ({ row }) =>
          row.original.submitted_at ? formatDateInMalaysia(row.original.submitted_at) : '-',
        meta: { headerTitle: 'Submitted', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'status',
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 150,
        enableSorting: false,
        cell: ({ row }) => (
          <span className={`${STATUS_PILL_BASE} ${statusPillClass(row.original.status)}`}>
            {row.original.status_label}
          </span>
        ),
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'delivery_date',
        header: ({ column }) => <DataGridColumnHeader title="Delivery" column={column} />,
        size: 120,
        cell: ({ row }) => deliveryText(row.original),
        meta: { headerTitle: 'Delivery', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
        size: 200,
        cell: ({ row }) => (
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="truncate" title={row.original.customer_name ?? undefined}>
              {row.original.customer_name || '-'}
            </span>
            {!row.original.customer_code && row.original.customer_name && (
              <Badge variant="secondary">No debtor code</Badge>
            )}
          </div>
        ),
        meta: { headerTitle: 'Customer', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'project_name',
        header: ({ column }) => <DataGridColumnHeader title="Project Title" column={column} />,
        size: 220,
        enableSorting: false,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.project_name ?? undefined}>
            {row.original.project_name || '-'}
          </span>
        ),
        meta: { headerTitle: 'Project Title', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'delivery_mode',
        header: ({ column }) => <DataGridColumnHeader title="Mode" column={column} />,
        size: 140,
        enableSorting: false,
        cell: ({ row }) =>
          row.original.delivery_mode ? DELIVERY_MODE_LABELS[row.original.delivery_mode] : '-',
        meta: { headerTitle: 'Mode', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'salesperson_name',
        header: ({ column }) => <DataGridColumnHeader title="Salesperson" column={column} />,
        size: 150,
        enableSorting: false,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.salesperson_name ?? undefined}>
            {row.original.salesperson_name || '-'}
          </span>
        ),
        meta: { headerTitle: 'Salesperson', skeleton: <Skeleton className="h-4 w-24" /> },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination?.total ?? 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  const filtersActiveCount =
    (statusFilter !== 'open' ? 1 : 0) + (salespersonFilter !== '__all__' ? 1 : 0);

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination?.total ?? 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      standardToolbar={false}
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
                placeholder="Search..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: filtersActiveCount > 0,
              activeCount: filtersActiveCount,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter}
                      onChange={setStatusFilter}
                      placeholder="Status"
                      triggerClassName="mt-1 w-full"
                      options={STATUS_OPTIONS}
                    />
                  </div>
                  <div>
                    <Label>Salesperson</Label>
                    <SearchableSelect
                      value={salespersonFilter}
                      onChange={setSalespersonFilter}
                      placeholder="Salesperson"
                      triggerClassName="mt-1 w-full"
                      options={[
                        { value: '__all__', label: 'All salespeople' },
                        ...salespeople.map((u) => ({
                          value: u.id,
                          label: u.name?.trim() || 'Unnamed user',
                          searchText: u.name ?? '',
                        })),
                      ]}
                    />
                  </div>
                  {filtersActiveCount > 0 && (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setStatusFilter('open');
                          setSalespersonFilter('__all__');
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'sample_requests_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={
              canCreate ? (
                <Button onClick={() => router.push(`${BASE_PATH}/new`)}>
                  <Plus />
                  Create
                </Button>
              ) : undefined
            }
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
