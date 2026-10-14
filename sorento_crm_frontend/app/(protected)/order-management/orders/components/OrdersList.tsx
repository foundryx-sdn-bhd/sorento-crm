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
import {
  Plus,
  Upload,
  AlertTriangle,
  Trash2,
  RefreshCw,
  CloudDownload,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn, selectedRowIds } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { OrderRowActions } from '../actions';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { pendingEntityKey, usePendingEntityKeys } from '@/lib/pending-entity-store';
import { useOrders } from '../hooks/useOrders';
import { useOrderStatusSelectQuery } from '../../shared/hooks/use-order-status-select-query';
import type { Order } from '../types/order.types';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDate } from '@/lib/helpers';
import { TemplateUploadDialog } from '@/components/template/TemplateUploadDialog';
import { bulkImportOrders, importOrderTracking, validateOrderTracking, validateDeliveryOrderDetail } from '../services/orderService';
import { OrderTrackingUploadDialog } from './OrderTrackingUploadDialog';
import { OrderLinesImportDialog } from './OrderLinesImportDialog';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  buildDetailSearch,
  decodeAdvancedFilter,
  encodeAdvancedFilter,
} from '@/lib/listNavQuery';
import type { ListQueryFilterGroup } from '@/lib/list-query/listQueryService';
import { useImportJobDrawer } from '@/components/upload-activity';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useAutocountPullAction } from '@/app/(protected)/system-management/import-jobs/autocount-pull/hooks/useAutocountPull';
import { PullScopeDialog } from '@/app/(protected)/system-management/import-jobs/autocount-pull/components/PullScopeDialog';

export default function OrdersList() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { notifyImportQueued } = useImportJobDrawer();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [trackingUploadOpen, setTrackingUploadOpen] = useState(false);
  const [orderLinesImportOpen, setOrderLinesImportOpen] = useState(false);
  const [advancedFilter, setAdvancedFilter] = useState<ListQueryFilterGroup | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [linesFilter, setLinesFilter] = useState<'all' | 'yes' | 'no'>('all');

  const { data: orderStatuses = [] } = useOrderStatusSelectQuery();

  // AutoCount pull - one more secondary action beside the two imports, the same shared
  // hook Products and Stock Balance use (PLAN-autocount-do-pull-crm-30sep.md).
  const autocountPull = useAutocountPullAction('delivery_orders');
  // A DO snapshot is a DocDate window (owner decision 30 Sep, mock section 1), so the click
  // asks for it first; with an open pull the click reviews it and the dialog never shows.
  const [pullScopeOpen, setPullScopeOpen] = useState(false);

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(state.filters.order_status_id ?? 'all');
    const lines = state.filters.has_order_lines;
    setLinesFilter(lines === 'yes' || lines === 'no' ? lines : 'all');
    setAdvancedFilter(decodeAdvancedFilter<ListQueryFilterGroup>(state.filters.advFilter));
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [searchQuery]);

  const { data, isLoading, isPlaceholderData, isError, error, refetch, isFetching } = useOrders({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    order_status_id: statusFilter === 'all' ? undefined : statusFilter,
    has_order_lines: linesFilter,
    advancedFilter: advancedFilter ?? undefined,
  });

  const handleUploadTemplate = async (data: unknown[]) => {
    try {
      const result = await bulkImportOrders(data);
      toast.success(`Successfully imported: ${result.created} created, ${result.updated} updated`);
      if (result.errors.length > 0) {
        toast.warning(`${result.errors.length} error(s) occurred during import`);
      }
      queryClient.invalidateQueries({ queryKey: ['orders'] });
    } catch (error) {
      throw error; // Let the dialog handle the error display
    }
  };

  const handleUploadTracking = async (file: File) => {
    const result = await importOrderTracking(file);
    // Job is queued, will be processed in background - progress in the upload drawer
    notifyImportQueued();
    queryClient.invalidateQueries({ queryKey: ['orders'] });
    queryClient.invalidateQueries({ queryKey: ['import-jobs'] });
    return result;
  };

  // The whole row opens the record. The grid appends its own page/sort/search;
  // the filters it does not know about ride in this query string, and the pager
  // rebuilds the list's query key from both.
  // A delivery order whose action is counting down stays on the list, dimmed,
  // until the window lapses - the toast holds the Cancel, this says which row.
  const pendingKeys = usePendingEntityKeys();
  const rowPending = (row: Order) => pendingKeys.has(pendingEntityKey('order', row.id));

  // Delete selected asks nothing either (D7): one action per selected row, ONE
  // countdown over them, one Cancel that withdraws the lot, and every selected row
  // dimmed by the same `rowPending` a single delete uses.
  const bulkDeletion = useDeferredBulkAction({
    actionKey: 'order.delete',
    entityType: 'order',
    describe: (count) => `${count} delivery order${count === 1 ? '' : 's'}`,
    invalidateKeys: [['orders']],
    onStarted: () => setRowSelection({}),
  });

  const rowHref = (row: Order) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        order_status_id: statusFilter === 'all' ? undefined : statusFilter,
        has_order_lines: linesFilter !== 'all' ? linesFilter : undefined,
        advFilter: encodeAdvancedFilter(advancedFilter),
      },
    );
    const qs = search ? `?${search}` : '';
    return `/order-management/orders/${row.id}${qs}`;
  };

  const columns = useMemo<ColumnDef<Order>[]>(
    () => [
      buildSelectColumn<Order>(),
      {
        accessorKey: 'order_number',
        header: ({ column }) => <DataGridColumnHeader title="Delivery Order Number" column={column} />,
        size: 150,
        meta: { headerTitle: 'Delivery Order Number', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'debtor_name',
        header: ({ column }) => <DataGridColumnHeader title="Debtor Name" column={column} />,
        cell: ({ row }) => row.original.debtor_name || '-',
        size: 200,
        meta: { headerTitle: 'Debtor Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        // DO-BRANCH-NAME: the AutoCount branch the DO ingest resolves, so the chatbot's
        // Branch line can be cross-checked here.
        accessorKey: 'branch_name',
        header: ({ column }) => <DataGridColumnHeader title="Branch" column={column} />,
        cell: ({ row }) =>
          row.original.branch_name ? (
            <span className="truncate block" title={row.original.branch_name}>
              {row.original.branch_name}
            </span>
          ) : (
            '-'
          ),
        enableSorting: false,
        size: 180,
        meta: { headerTitle: 'Branch', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'order_date',
        header: ({ column }) => <DataGridColumnHeader title="Delivery Order Date" column={column} />,
        cell: ({ row }) => row.original.order_date ? formatDate(new Date(row.original.order_date)) : '-',
        size: 120,
        meta: { headerTitle: 'Delivery Order Date', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'estimated_delivery_date',
        header: ({ column }) => <DataGridColumnHeader title="Estimated Delivery" column={column} />,
        cell: ({ row }) => row.original.estimated_delivery_date ? formatDate(new Date(row.original.estimated_delivery_date)) : '-',
        size: 150,
        meta: { headerTitle: 'Estimated Delivery', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'actual_delivery_date',
        header: ({ column }) => <DataGridColumnHeader title="Actual Delivery" column={column} />,
        cell: ({ row }) => row.original.actual_delivery_date ? formatDate(new Date(row.original.actual_delivery_date)) : '-',
        size: 150,
        meta: { headerTitle: 'Actual Delivery', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'delivery_days',
        header: ({ column }) => <DataGridColumnHeader title="Delivery Days (2)" column={column} />,
        cell: ({ row }) => {
          if (row.original.delivery_days === null || row.original.delivery_days === undefined) return '-';
          return (
            <div className="flex items-center gap-2">
              <span>{row.original.delivery_days}</span>
              {row.original.kpi_warning && (
                <AlertTriangle className="size-4 text-amber-500" />
              )}
            </div>
          );
        },
        size: 130,
        meta: { headerTitle: 'Delivery Days (2)', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'debtor_code',
        header: ({ column }) => <DataGridColumnHeader title="Debtor Code" column={column} />,
        cell: ({ row }) => row.original.debtor_code || '-',
        size: 120,
        meta: { headerTitle: 'Debtor Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'agent',
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        cell: ({ row }) => row.original.agent || '-',
        size: 100,
        meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'is_cancelled',
        header: ({ column }) => <DataGridColumnHeader title="Cancelled" column={column} />,
        cell: ({ row }) => (row.original.is_cancelled ? 'Yes' : 'No'),
        size: 90,
        meta: { headerTitle: 'Cancelled', skeleton: <Skeleton className="h-4 w-12" /> },
      },
      {
        accessorKey: 'remarks_cs',
        header: ({ column }) => <DataGridColumnHeader title="Remarks CS" column={column} />,
        cell: ({ row }) => row.original.remarks_cs || '-',
        size: 220,
        minSize: 80,
        meta: { headerTitle: 'Remarks CS', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'order_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => row.original.order_type || '-',
        size: 100,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'order_status.status_name',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const status = row.original.order_status;
          return status ? (
            <Badge status={status.status_name}>
              {status.status_name}
            </Badge>
          ) : (
            '-'
          );
        },
        size: 150,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => <OrderRowActions order={row.original} />,
        size: 60,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
      },
    ],
    [],
  );

  const quickFilterActive = statusFilter !== 'all' || linesFilter !== 'all';

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
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/order-management/orders/new')}>
      <Plus />
      Create Delivery Order
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      rowPending={rowPending}
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
                placeholder="Search delivery orders..."
                className="w-56 max-w-full"
              />
            }
            filters={{
              kind: 'listQuery',
              resourceKey: 'orders',
              advancedFilter,
              onApply: (f) => {
                setAdvancedFilter(f);
                setPagination((p) => ({ ...p, pageIndex: 0 }));
              },
              getPayload: () => ({
                filter: advancedFilter ?? undefined,
                quick_search: searchQuery || undefined,
                order_status_id: statusFilter === 'all' ? undefined : statusFilter,
                has_order_lines: linesFilter === 'all' ? undefined : linesFilter,
              }),
            }}
            exportConfig={{
              kind: 'listQuery',
              resourceKey: 'orders',
              filename: 'delivery_orders_export.xlsx',
              getPayload: () => ({
                filter: advancedFilter ?? undefined,
                quick_search: searchQuery || undefined,
                order_status_id: statusFilter === 'all' ? undefined : statusFilter,
                has_order_lines: linesFilter === 'all' ? undefined : linesFilter,
              }),
            }}
            primaryAction={listPrimaryAction}
            secondaryActions={[
              {
                key: 'refresh',
                label: 'Refresh',
                icon: RefreshCw,
                onClick: () => void refetch(),
              },
              {
                key: 'import-tracking',
                label: 'Import tracking',
                icon: Upload,
                onClick: () => setTrackingUploadOpen(true),
                dataGuideTarget: 'order-management.delivery-orders.import-button',
              },
              {
                key: 'import-lines',
                label: 'Import delivery order lines',
                icon: Upload,
                onClick: () => setOrderLinesImportOpen(true),
              },
              ...(autocountPull.visible
                ? [
                    {
                      key: 'autocount-pull',
                      label: autocountPull.label,
                      icon: CloudDownload,
                      onClick: () => {
                        if (autocountPull.hasOpenPull) void autocountPull.onSelect();
                        else setPullScopeOpen(true);
                      },
                    },
                  ]
                : []),
            ]}
            bulkActions={[
              {
                key: 'delete',
                label: 'Delete',
                icon: Trash2,
                destructive: true,
                onClick: () =>
                  bulkDeletion.run(selectedRowIds(table).map((id) => ({ id }))),
              },
            ]}
          />
          {/* Quick status / lines filters retained below the canonical toolbar
              (these are server-side params, distinct from the advanced filter). */}
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Label htmlFor="orders-quick-status" className="text-xs text-muted-foreground">
                Status
              </Label>
              <SearchableSelect
                value={statusFilter}
                onChange={(v) => {
                  setStatusFilter(v);
                  setPagination((p) => ({ ...p, pageIndex: 0 }));
                }}
                id="orders-quick-status"
                size="sm"
                triggerClassName="w-48"
                options={[
                  { value: 'all', label: 'All statuses' },
                  ...orderStatuses.map((status) => ({
                    value: status.id,
                    label: status.status_name,
                  })),
                ]}
                placeholder="Status"
              />
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="orders-quick-lines" className="text-xs text-muted-foreground">
                Delivery order lines
              </Label>
              <SearchableSelect
                value={linesFilter}
                onChange={(v) => {
                  setLinesFilter(v as 'all' | 'yes' | 'no');
                  setPagination((p) => ({ ...p, pageIndex: 0 }));
                }}
                id="orders-quick-lines"
                size="sm"
                triggerClassName="w-56"
                options={[
                  { value: 'all', label: 'All delivery orders' },
                  { value: 'yes', label: 'With delivery order lines' },
                  { value: 'no', label: 'Without delivery order lines' },
                ]}
                placeholder="Delivery order lines"
              />
            </div>
            {quickFilterActive && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setStatusFilter('all');
                  setLinesFilter('all');
                  setPagination((p) => ({ ...p, pageIndex: 0 }));
                }}
              >
                Clear quick filters
              </Button>
            )}
          </div>
        </CardHeader>
        {isError ? (
          <div className="px-5 pb-2 text-sm text-destructive">
            {error instanceof Error ? error.message : 'Failed to load delivery orders'}
          </div>
        ) : null}
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
      <TemplateUploadDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        onUpload={handleUploadTemplate}
      />
      <OrderTrackingUploadDialog
        open={trackingUploadOpen}
        onOpenChange={setTrackingUploadOpen}
        onTest={validateOrderTracking}
        onUpload={handleUploadTracking}
      />
      <OrderLinesImportDialog
        open={orderLinesImportOpen}
        onOpenChange={setOrderLinesImportOpen}
        onTest={validateDeliveryOrderDetail}
        onSuccess={() => {
          notifyImportQueued();
          queryClient.invalidateQueries({ queryKey: ['orders'] });
          queryClient.invalidateQueries({ queryKey: ['import-jobs'] });
        }}
      />
      {autocountPull.visible && (
        <PullScopeDialog
          open={pullScopeOpen}
          onOpenChange={setPullScopeOpen}
          onPull={(scope) => autocountPull.onSelect(scope)}
        />
      )}
    </DataGrid>
  );
}
