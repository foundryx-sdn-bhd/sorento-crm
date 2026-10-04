'use client';

/**
 * CRM DataGrid listing for price tag requests.
 *
 * Phase 1: mock data via `priceTagRequestService`. Follows the complaints
 * listing pattern (DataGrid + search + filters + row click to detail).
 */

import { useEffect, useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { ChevronRight, Search, UserPlus, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import {
  priceTagStatusLabel,
  priceTagStatusPillClass,
} from '@/lib/price-tag-status';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { formatDate, formatDateTimeInMalaysia } from '@/lib/helpers';
import { AUTO_UPDATE_STATUSES } from '@/lib/dealer-kit/product-data-changes';
import {
  listPriceTagRequests,
  claimPriceTagRequest,
  type PriceTagRequestSummary,
} from '../../services/priceTagRequestService';

const STATUS_OPTIONS = [
  { value: '__all__', label: 'All statuses' },
  { value: 'new', label: 'New' },
  { value: 'designing', label: 'Designing' },
  { value: 'proof_ready', label: 'Design Ready' },
  { value: 'changes_requested', label: 'Changes Requested' },
  { value: 'approved', label: 'Approved' },
  { value: 'ready_for_collection', label: 'Ready for collection' },
  { value: 'collected', label: 'Collected' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'void', label: 'Void' },
];

export default function PriceTagRequestsList() {
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'created_at', desc: true },
  ]);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('__all__');
  const [data, setData] = useState<{
    data: PriceTagRequestSummary[];
    pagination: { total: number };
  } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);

  // Reset page on filter change
  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [searchQuery, statusFilter]);

  // Fetch data
  const fetchData = async () => {
    const sort = sorting[0];
    const result = await listPriceTagRequests({
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
      sort: sort?.id,
      dir: sort?.desc ? 'desc' : 'asc',
      query: searchQuery || undefined,
      status: statusFilter !== '__all__' ? statusFilter : undefined,
    });
    setData(result);
    setLoadError(null);
  };

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchData()
      .catch((e: unknown) => {
        if (cancelled) return;
        setData(null);
        setLoadError(e);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pagination.pageIndex, pagination.pageSize, sorting, searchQuery, statusFilter]);

  const handleRetry = () => {
    setIsLoading(true);
    fetchData()
      .catch((e: unknown) => {
        setData(null);
        setLoadError(e);
      })
      .finally(() => setIsLoading(false));
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await fetchData();
    } catch (e) {
      // Rows on screen stay; the grid only shows a failure in place of no rows, so say it here.
      setLoadError(e);
      if (data?.data?.length) toast.error(e instanceof Error ? e.message : 'Could not refresh the list.');
    } finally {
      setIsRefreshing(false);
    }
  };

  // Carried into the record URL so its prev/next pager walks the SAME searched,
  // sorted and filtered page the reader was on (S3-03). The status filter has to
  // ride along too: the pager cannot rebuild a filter the URL never named.
  const detailSearch = buildDetailSearch(
    {
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
    },
    { status: statusFilter !== '__all__' ? statusFilter : undefined },
  );

  const detailHref = (row: PriceTagRequestSummary) =>
    `/dealer-kit/price-tag-requests/${row.id}${detailSearch ? `?${detailSearch}` : ''}`;

  const handleClaim = async (
    e: React.MouseEvent,
    requestId: string,
  ) => {
    e.stopPropagation();
    try {
      await claimPriceTagRequest(requestId);
      toast.success('Request claimed');
      await fetchData();
    } catch {
      toast.error('Failed to claim request');
    }
  };

  const filtersActiveCount = statusFilter !== '__all__' ? 1 : 0;

  const columns = useMemo<ColumnDef<PriceTagRequestSummary>[]>(
    () => [
      {
        accessorKey: 'doc_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Doc Number" column={column} />
        ),
        size: 170,
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.doc_number}>
            {row.original.doc_number}
          </span>
        ),
        meta: {
          headerTitle: 'Doc Number',
          skeleton: <Skeleton className="h-4 w-28" />,
        },
      },
      {
        accessorKey: 'debtor_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Customer" column={column} />
        ),
        size: 200,
        cell: ({ row }) => (
          // A portal draft has no dealer yet (D48a).
          <span className="truncate" title={row.original.debtor_name ?? '-'}>
            {row.original.debtor_name ?? '-'}
          </span>
        ),
        meta: {
          headerTitle: 'Customer',
          skeleton: <Skeleton className="h-4 w-32" />,
        },
      },
      {
        accessorKey: 'contact_name',
        // Resolved per page rather than stored, so the query cannot order by it.
        enableSorting: false,
        header: ({ column }) => (
          <DataGridColumnHeader title="Salesperson" column={column} />
        ),
        size: 150,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.contact_name ?? undefined}>
            {row.original.contact_name ?? '-'}
          </span>
        ),
        meta: {
          headerTitle: 'Salesperson',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        size: 140,
        cell: ({ row }) => {
          const status = row.original.status;
          if (!status) return '-';
          return (
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${priceTagStatusPillClass(status)}`}
            >
              {priceTagStatusLabel(status)}
            </span>
          );
        },
        meta: {
          headerTitle: 'Status',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      {
        accessorKey: 'needed_by_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Need by" column={column} />
        ),
        size: 130,
        cell: ({ row }) =>
          row.original.needed_by_date
            ? formatDate(new Date(row.original.needed_by_date))
            : '-',
        meta: {
          headerTitle: 'Need by',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'line_count',
        // Resolved per page rather than stored, so the query cannot order by it.
        enableSorting: false,
        header: ({ column }) => (
          <DataGridColumnHeader title="Lines" column={column} />
        ),
        size: 80,
        cell: ({ row }) => row.original.line_count,
        meta: {
          headerTitle: 'Lines',
          skeleton: <Skeleton className="h-4 w-8" />,
        },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Created" column={column} />
        ),
        size: 160,
        cell: ({ row }) =>
          row.original.created_at
            ? formatDateTimeInMalaysia(row.original.created_at)
            : '-',
        meta: {
          headerTitle: 'Created',
          skeleton: <Skeleton className="h-4 w-28" />,
        },
      },
      {
        accessorKey: 'assigned_to_name',
        // Resolved per page rather than stored, so the query cannot order by it.
        enableSorting: false,
        header: ({ column }) => (
          <DataGridColumnHeader title="Assigned To" column={column} />
        ),
        size: 150,
        cell: ({ row }) => {
          if (row.original.assigned_to_name) {
            return (
              <span className="truncate" title={row.original.assigned_to_name}>
                {row.original.assigned_to_name}
              </span>
            );
          }
          if (row.original.status === 'new') {
            return (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={(e) => handleClaim(e, row.original.id)}
              >
                <UserPlus className="size-3.5 mr-1" />
                Claim
              </Button>
            );
          }
          return <span className="text-muted-foreground">-</span>;
        },
        meta: {
          headerTitle: 'Assigned To',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'data_changed_tag_count',
        // Stored, refreshed for a touched row only (AC-D4) - the query
        // cannot order by a value that is not always current.
        enableSorting: false,
        header: ({ column }) => (
          <DataGridColumnHeader title="Product data" column={column} />
        ),
        size: 170,
        cell: ({ row }) => {
          const count = row.original.data_changed_tag_count ?? 0;
          if (count <= 0) return null;
          const status = row.original.status;
          const label =
            status && AUTO_UPDATE_STATUSES.includes(status)
              ? `Product data updated · ${count}`
              : `Product data changed · ${count}`;
          return (
            <Badge size="sm" variant="warning" appearance="light">
              {label}
            </Badge>
          );
        },
        meta: {
          headerTitle: 'Product data',
          skeleton: <Skeleton className="h-4 w-32" />,
        },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: () => (
          <ChevronRight className="text-muted-foreground/70 size-3.5" />
        ),
        size: 40,
        enableHiding: false,
      },
    ],
    // handleClaim is stable because fetchData is recreated every render, but
    // in Phase 1 (mock) this is acceptable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data ?? [],
    pageCount: Math.ceil((data?.pagination.total ?? 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
  });

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total ?? 0}
      isLoading={isLoading}
      error={loadError}
      onRetry={handleRetry}
      rowHref={detailHref}
      standardToolbar={false}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <div className="relative">
                <Search className="size-4 text-muted-foreground absolute start-3 top-1/2 -translate-y-1/2" />
                <Input
                  placeholder="Search requests..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="ps-9 w-64"
                />
                {searchQuery && (
                  <Button
                    mode="icon"
                    variant="dim"
                    aria-label="Clear search"
                    title="Clear search"
                    className="absolute end-1.5 top-1/2 -translate-y-1/2 h-6 w-6"
                    onClick={() => setSearchQuery('')}
                  >
                    <X />
                  </Button>
                )}
              </div>
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
                      options={STATUS_OPTIONS}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  {filtersActiveCount > 0 && (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setStatusFilter('__all__')}
                      >
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            onRefresh={() => void handleRefresh()}
            isRefreshing={isRefreshing}
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
