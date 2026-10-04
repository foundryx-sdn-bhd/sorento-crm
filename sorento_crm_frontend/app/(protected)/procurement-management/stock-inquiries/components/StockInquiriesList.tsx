'use client';

import { useMemo, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
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
import { Plus, Trash2, Check } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import {
  buildSelectColumn,
  selectedRowIds,
} from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { revisionBadgeLabel, withRevisionSuffix } from '@/lib/document-number';
import { useListingViewPreferences } from '@/lib/listing-column-preferences/useListingViewPreferences';
import { useStockInquiries } from '../hooks/useStockInquiries';
import { statusPillClass, STATUS_PILL_BASE } from '@/lib/status-pill';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import type { StockInquiry } from '../types/stockInquiry.types';
import { STOCK_INQUIRY_STATUS_LABELS } from '../types/stockInquiry.types';
import StockInquiryBulkDeleteDialog from './StockInquiryBulkDeleteDialog';
import { EntityDownloadsButton } from '@/components/my-downloads/EntityDownloadsButton';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';

/**
 * The listing's shipped default, used until the user has left one behind.
 *
 * A revision is new work for whoever reads the list, so it sorts as if created
 * then rather than staying buried at its original submission date.
 * `last_activity_at` resolves server-side to coalesce(last_revised_at, created_at).
 */
const DEFAULT_SORTING: SortingState = [{ id: 'last_activity_at', desc: true }];

/**
 * Shape of what this page stores in the opaque `filters` blob. BUMP THIS whenever
 * `StockInquiriesFilters` changes, so blobs written by the old shape are discarded
 * rather than applied (AC-B4).
 */
const FILTERS_VERSION = 1;

type StockInquiriesFilters = { statuses: string[] };

export default function StockInquiriesList() {
  const router = useRouter();
  const pathname = usePathname();
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [searchQuery]);

  // The sort and the status filter are remembered per user. `pathname` is the same
  // listing key DataGrid derives for the column preferences, so both writers address
  // one row. Page number and search text are deliberately NOT remembered.
  const {
    sorting,
    setSorting,
    filters: viewFilters,
    setFilters: setViewFilters,
    isLoading: isViewPrefsLoading,
  } = useListingViewPreferences<StockInquiriesFilters>({
    listingKey: pathname,
    defaultSorting: DEFAULT_SORTING,
    filtersVersion: FILTERS_VERSION,
  });

  const statusFilter = useMemo(
    () => viewFilters?.statuses ?? [],
    [viewFilters],
  );

  // Back hands the list its own query string back, and the pager keeps rewriting
  // it, so the list reads it (S3-01). One hook, every list. Sorting and the
  // status filter are remembered per user by `useListingViewPreferences`, which
  // is the stronger memory, so the URL only restores the page and the search.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    resetSearch(state.searchQuery);
  });

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useStockInquiries({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    statuses: statusFilter,
    // One fetch, with the remembered view already applied (AC-B3).
    enabled: !isViewPrefsLoading,
  });

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from.
  const rowHref = (row: StockInquiry) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        status: statusFilter.length ? statusFilter.join(',') : undefined,
      },
    );
    const qs = search ? `?${search}` : '';
    return `/procurement-management/stock-inquiries/${row.id}${qs}`;
  };

  const applyStatusFilter = (next: string[]) => {
    // An empty selection is stored as no filter at all, so returning shows the
    // unfiltered listing (AC-C2).
    setViewFilters(next.length ? { statuses: next } : null);
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };

  const toggleStatusFilter = (value: string) => {
    applyStatusFilter(
      statusFilter.includes(value)
        ? statusFilter.filter((v) => v !== value)
        : [...statusFilter, value],
    );
  };

  const statusFilterLabel = (() => {
    const labels = statusFilter.map((v) => STOCK_INQUIRY_STATUS_LABELS[v] ?? v);
    if (labels.length === 0) return '';
    if (labels.length === 1) return labels[0];
    return `${labels[0]} +${labels.length - 1} more`;
  })();

  const columns = useMemo<ColumnDef<StockInquiry>[]>(
    () => [
      buildSelectColumn<StockInquiry>(),
      {
        accessorKey: 'inquiry_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Stock inquiry number" column={column} />
        ),
        size: 150,
        cell: ({ row }) => {
          // The `-R{n}` suffix is derived from the denormalized counter, never
          // stored (UAC N2/N3).
          const number = withRevisionSuffix(
            row.original.inquiry_number,
            row.original.revision_no,
          );
          return (
            <span className="truncate" title={number ?? undefined}>
              {number || '-'}
            </span>
          );
        },
        meta: {
          headerTitle: 'Stock inquiry number',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      {
        accessorKey: 'revision_no',
        header: ({ column }) => (
          <DataGridColumnHeader title="Rev" column={column} />
        ),
        size: 80,
        enableSorting: false,
        // Denormalized on the row - no per-row query (UAC H4).
        cell: ({ row }) => {
          const label = revisionBadgeLabel(row.original.revision_no);
          if (!label) return <span className="text-muted-foreground"> - </span>;
          return (
            <Badge variant="secondary" title={label}>
              {label}
            </Badge>
          );
        },
        meta: {
          headerTitle: 'Rev',
          skeleton: <Skeleton className="h-4 w-10" />,
        },
      },
      {
        accessorKey: 'product_code',
        header: ({ column }) => (
          <DataGridColumnHeader title="Product Code" column={column} />
        ),
        size: 150,
        cell: ({ row }) => row.original.product_code || '-',
        meta: {
          headerTitle: 'Product Code',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'item_description',
        header: ({ column }) => (
          <DataGridColumnHeader title="Item Description" column={column} />
        ),
        size: 200,
        cell: ({ row }) => row.original.item_description || '-',
        meta: {
          headerTitle: 'Item Description',
          skeleton: <Skeleton className="h-4 w-32" />,
        },
      },
      {
        accessorKey: 'project_customer',
        header: ({ column }) => (
          <DataGridColumnHeader title="Project Customer" column={column} />
        ),
        size: 150,
        cell: ({ row }) => row.original.project_customer || '-',
        meta: {
          headerTitle: 'Project Customer',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'project_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Project Name" column={column} />
        ),
        size: 150,
        cell: ({ row }) => row.original.project_name || '-',
        meta: {
          headerTitle: 'Project Name',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'quantity',
        header: ({ column }) => (
          <DataGridColumnHeader title="Quantity" column={column} />
        ),
        size: 100,
        cell: ({ row }) => row.original.quantity || '-',
        meta: {
          headerTitle: 'Quantity',
          skeleton: <Skeleton className="h-4 w-16" />,
        },
      },
      {
        accessorKey: 'delivery_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Delivery Date" column={column} />
        ),
        cell: ({ row }) => row.original.delivery_date ?? '-',
        size: 150,
        meta: {
          headerTitle: 'Delivery Date',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'remark',
        header: ({ column }) => (
          <DataGridColumnHeader title="Remark" column={column} />
        ),
        cell: ({ row }) => {
          const remark = row.original.remark;
          return remark ? (
            <span className="line-clamp-2" title={remark}>
              {remark}
            </span>
          ) : (
            '-'
          );
        },
        size: 180,
        meta: {
          headerTitle: 'Remark',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      {
        id: 'last_activity_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Last activity" column={column} />
        ),
        size: 150,
        cell: ({ row }) => {
          const { created_at, revision_no, last_revised_at } = row.original;
          const isRevised = (revision_no ?? 0) > 0 && last_revised_at != null;
          const raw = isRevised ? last_revised_at : created_at;
          if (raw == null) return '-';
          const formatted = formatDateTimeInMalaysia(raw);
          if (!formatted) return '-';
          return isRevised ? `Revised ${formatted}` : formatted;
        },
        meta: {
          headerTitle: 'Last activity',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'salesperson',
        header: ({ column }) => (
          <DataGridColumnHeader title="Salesperson" column={column} />
        ),
        size: 150,
        cell: ({ row }) => row.original.salesperson || '-',
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
        size: 110,
        cell: ({ row }) => {
          const status = row.original.status;
          if (!status) return '-';
          return (
            <span className={`${STATUS_PILL_BASE} ${statusPillClass(status)}`}>
              {STOCK_INQUIRY_STATUS_LABELS[status] ?? status}
            </span>
          );
        },
        meta: {
          headerTitle: 'Status',
          skeleton: <Skeleton className="h-4 w-16" />,
        },
      },
      {
        accessorKey: 'assigned_to_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Assigned To" column={column} />
        ),
        size: 160,
        enableSorting: false,
        cell: ({ row }) => (
          <span
            className="truncate"
            title={row.original.assigned_to_name ?? undefined}
          >
            {row.original.assigned_to_name ?? '-'}
          </span>
        ),
        meta: {
          headerTitle: 'Assigned To',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'handled_by_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Handled By" column={column} />
        ),
        size: 160,
        enableSorting: false,
        cell: ({ row }) => (
          <span
            className="truncate"
            title={row.original.handled_by_name ?? undefined}
          >
            {row.original.handled_by_name ?? '-'}
          </span>
        ),
        meta: {
          headerTitle: 'Handled By',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'print_count',
        header: ({ column }) => (
          <DataGridColumnHeader title="Print Count" column={column} />
        ),
        size: 110,
        enableSorting: false,
        cell: ({ row }) => (
          <EntityDownloadsButton
            entityType="stock_inquiry"
            entityId={row.original.id}
            label={row.original.inquiry_number ?? undefined}
            count={row.original.print_count ?? 0}
          />
        ),
        meta: {
          headerTitle: 'Print Count',
          skeleton: <Skeleton className="h-4 w-12" />,
        },
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
    <Button
      onClick={() =>
        router.push('/procurement-management/stock-inquiries/new')
      }
    >
      <Plus />
      Create Stock Inquiry
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading || isViewPrefsLoading}
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
                placeholder="Search stock inquiries..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: statusFilter.length > 0,
              activeCount: statusFilter.length,
              activeSummary: {
                label: statusFilterLabel,
                onClear: () => applyStatusFilter([]),
              },
              content: (
                <div className="space-y-1">
                  <div className="px-1 py-1 text-xs font-medium text-muted-foreground">
                    Filter by status
                  </div>
                  <Separator />
                  <div className="py-1">
                    {Object.entries(STOCK_INQUIRY_STATUS_LABELS).map(
                      ([value, label]) => {
                        const checked = statusFilter.includes(value);
                        return (
                          <button
                            key={value}
                            type="button"
                            onClick={() => toggleStatusFilter(value)}
                            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
                          >
                            <span
                              className={cn(
                                'flex size-4 items-center justify-center rounded-sm border border-primary',
                                checked
                                  ? 'bg-primary text-primary-foreground'
                                  : 'opacity-50 [&_svg]:invisible',
                              )}
                            >
                              <Check className="size-3.5" />
                            </span>
                            <span>{label}</span>
                          </button>
                        );
                      },
                    )}
                  </div>
                  {statusFilter.length > 0 && (
                    <>
                      <Separator />
                      <div className="flex justify-end pt-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => applyStatusFilter([])}
                        >
                          Clear filters
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'stock_inquiries_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
            bulkActions={[
              {
                key: 'delete',
                label: 'Delete',
                icon: Trash2,
                destructive: true,
                onClick: () => setBulkDeleteDialogOpen(true),
              },
            ]}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
      <StockInquiryBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={(open) => {
          setBulkDeleteDialogOpen(open);
          if (!open) setRowSelection({});
        }}
        inquiryIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
    </DataGrid>
  );
}
