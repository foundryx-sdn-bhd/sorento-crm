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
import {
  isSearchInFlight,
  useDebouncedSearch,
} from '@/hooks/useDebouncedSearch';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { useHasPermission } from '@/hooks/usePermissions';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { formatDateInMalaysia } from '@/lib/helpers';
import { getUserLookup } from '@/services/userSelectService';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import type { NewProductApplicationSummary } from '@/types/newProductApplication';
import { useNewProductApplicationsQuery } from '../hooks/useNewProductApplications';

const BASE_PATH = '/procurement-management/new-product-applications';
const LISTING_KEY = 'procurement.new_product_applications.view';

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '__all__', label: 'All statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
];

const day = (value: string | null | undefined) =>
  value ? formatDateInMalaysia(value) : '-';

const truncated = (value: string | null | undefined) => (
  <span className="truncate" title={value ?? undefined}>
    {value || '-'}
  </span>
);

export default function NewProductApplicationsList() {
  const router = useRouter();
  const canCreate = useHasPermission(
    'procurement.new_product_applications.create',
  );
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState('__all__');
  const [applicantFilter, setApplicantFilter] = useState('__all__');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(state.filters.status ?? '__all__');
    setApplicantFilter(state.filters.applicant_id ?? '__all__');
  });

  const { data: applicants = [] } = useQuery({
    queryKey: ['user-lookup', 'with-inactive'],
    queryFn: () => getUserLookup({ include_inactive: true }),
    staleTime: 5 * 60_000,
  });

  const status = statusFilter !== '__all__' ? statusFilter : undefined;
  const applicantId =
    applicantFilter !== '__all__' ? applicantFilter : undefined;
  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } =
    useNewProductApplicationsQuery({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      status,
      applicantId,
    });

  useResetPageOnFilterChange(setPagination, [
    statusFilter,
    applicantFilter,
    searchQuery,
  ]);

  const rowHref = (row: NewProductApplicationSummary) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      { status, applicant_id: applicantId },
    );
    return `${BASE_PATH}/${row.id}${search ? `?${search}` : ''}`;
  };

  const columns = useMemo<ColumnDef<NewProductApplicationSummary>[]>(
    () => [
      buildSelectColumn<NewProductApplicationSummary>(),
      {
        accessorKey: 'application_no',
        header: ({ column }) => (
          <DataGridColumnHeader title="Number" column={column} />
        ),
        size: 130,
        cell: ({ row }) => (
          <span
            className="truncate font-medium tabular-nums"
            title={row.original.application_no}
          >
            {row.original.application_no}
          </span>
        ),
        meta: {
          headerTitle: 'Number',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      {
        id: 'status',
        accessorKey: 'status',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        size: 130,
        cell: ({ row }) => (
          <span
            className={`${STATUS_PILL_BASE} ${statusPillClass(row.original.status)}`}
          >
            {row.original.status_label}
          </span>
        ),
        meta: {
          headerTitle: 'Status',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'stock_inquiry_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Inquiry" column={column} />
        ),
        size: 120,
        enableSorting: false,
        cell: ({ row }) => truncated(row.original.stock_inquiry_number),
        meta: {
          headerTitle: 'Inquiry',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Customer" column={column} />
        ),
        size: 220,
        cell: ({ row }) => truncated(row.original.customer_name),
        meta: {
          headerTitle: 'Customer',
          skeleton: <Skeleton className="h-4 w-28" />,
        },
      },
      {
        accessorKey: 'first_line',
        header: ({ column }) => (
          <DataGridColumnHeader title="First line" column={column} />
        ),
        size: 260,
        enableSorting: false,
        cell: ({ row }) => truncated(row.original.first_line),
        meta: {
          headerTitle: 'First line',
          skeleton: <Skeleton className="h-4 w-36" />,
        },
      },
      {
        accessorKey: 'lines_total',
        header: ({ column }) => (
          <DataGridColumnHeader title="Lines" column={column} />
        ),
        size: 80,
        enableSorting: false,
        cell: ({ row }) => row.original.lines_total,
        meta: {
          headerTitle: 'Lines',
          skeleton: <Skeleton className="h-4 w-8" />,
        },
      },
      {
        id: 'lines_done',
        accessorKey: 'lines_done',
        header: ({ column }) => (
          <DataGridColumnHeader title="Done" column={column} />
        ),
        size: 100,
        enableSorting: false,
        cell: ({ row }) =>
          `${row.original.lines_done} / ${row.original.lines_total}`,
        meta: {
          headerTitle: 'Done',
          skeleton: <Skeleton className="h-4 w-12" />,
        },
      },
      {
        accessorKey: 'needed_by',
        header: ({ column }) => (
          <DataGridColumnHeader title="Needed by" column={column} />
        ),
        size: 120,
        cell: ({ row }) => day(row.original.needed_by),
        meta: {
          headerTitle: 'Needed by',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Date" column={column} />
        ),
        size: 120,
        // A naive UTC stamp: shown as the Malaysia calendar day, like every other form.
        cell: ({ row }) => day(row.original.created_at),
        meta: {
          headerTitle: 'Date',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
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
    (statusFilter !== '__all__' ? 1 : 0) +
    (applicantFilter !== '__all__' ? 1 : 0);

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination?.total ?? 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      listingKey={LISTING_KEY}
      emptyMessage="No applications yet"
      standardToolbar={false}
      tableLayout={{
        width: 'fixed',
        columnsResizable: true,
        columnsVisibility: true,
      }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(
                  searchSettling,
                  isFetching,
                  searchQuery,
                )}
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
                    <Label>Applicant</Label>
                    <SearchableSelect
                      value={applicantFilter}
                      onChange={setApplicantFilter}
                      placeholder="Applicant"
                      triggerClassName="mt-1 w-full"
                      options={[
                        { value: '__all__', label: 'All applicants' },
                        ...applicants.map((u) => ({
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
                          setStatusFilter('__all__');
                          setApplicantFilter('__all__');
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'new_product_applications_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={
              canCreate ? (
                <Button onClick={() => router.push(`${BASE_PATH}/new`)}>
                  <Plus />
                  New
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
