'use client';

import { useEffect, useMemo, useState } from 'react';
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
import { ChevronRight, Upload, FileSpreadsheet, Trash2, CloudDownload } from 'lucide-react';
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
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { useStockBalance } from '../hooks/useStock';
import { useHasPermission } from '@/hooks/usePermissions';
import { getWarehouses } from '@/app/(protected)/inventory-management/warehouses/services/warehouseService';
import type { Warehouse } from '@/app/(protected)/inventory-management/warehouses/types/warehouse.types';
import type { Stock } from '../types/stock.types';
import { TemplateUploadDialog } from '@/components/template/TemplateUploadDialog';
import { exportStockBalance, bulkImportStock, validateStockImport } from '../services/stockService';
import { replaceLatestStockList, getCurrentStockListAttachment } from '@/app/(protected)/resource-management/attachments/services/attachmentService';
import { useImportJobDrawer } from '@/components/upload-activity';
import StockBulkDeleteDialog from './StockBulkDeleteDialog';
import { useQueryClient, useQuery } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { generateExcelFile, type ColumnOption } from '@/lib/excel-utils';
import { useRouter } from 'next/navigation';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useAutocountPullAction } from '@/app/(protected)/system-management/import-jobs/autocount-pull/hooks/useAutocountPull';

const EXPORT_FILENAME = 'stock_balance_export.xlsx';

// Human labels for export columns, keyed by react-table column id (accessor key).
const EXPORT_COLUMN_LABELS: Record<string, string> = {
  'product.product_code': 'Product Code',
  'product.product_name': 'Product Name',
  'product.category.category_name': 'Category',
  'warehouse.warehouse_name': 'Warehouse',
  available: 'Available',
  reserved_quantity: 'Reserved',
  quantity: 'Total',
  'product.reorder_level': 'Reorder Level',
  status: 'Status',
};

/** Collapse the quantity aliases onto the accessor keys so display AND export agree. */
function normalizeStock(item: Stock): Stock {
  return {
    ...item,
    available: item.quantity_available ?? item.available ?? ((item.quantity_on_hand ?? item.quantity ?? 0) - (item.quantity_reserved ?? item.reserved_quantity ?? 0)),
    quantity: item.quantity_on_hand ?? item.quantity ?? 0,
    reserved_quantity: item.quantity_reserved ?? item.reserved_quantity ?? 0,
  };
}

export default function StockBalanceGrid() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { notifyImportQueued } = useImportJobDrawer();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'product.product_code', desc: false }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [warehouseId, setWarehouseId] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [selectAllMatching, setSelectAllMatching] = useState(false);
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);

  const canDeleteStock = useHasPermission('inventory.stock.delete');

  // AutoCount pull - one more secondary action beside Import (PLAN-autocount-pull-review.md).
  const autocountPull = useAutocountPullAction('stock_balances', 'inventory.stock.autocount_pull');

  const { data: stockListAttachment } = useQuery({
    queryKey: ['current-stock-list-attachment'],
    queryFn: () => getCurrentStockListAttachment(),
  });

  const { data: warehousesData } = useQuery({
    queryKey: ['warehouses-select-stock'],
    queryFn: () => getWarehouses({ pageIndex: 0, pageSize: 100, sorting: [], searchQuery: '', is_active: true }),
  });
  const warehouses: Warehouse[] = warehousesData?.data ?? [];

  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useStockBalance({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    warehouse_id: warehouseId || undefined,
    status: statusFilter || undefined,
  });

  const normalizedData = useMemo(() => (data?.data ?? []).map(normalizeStock), [data]);
  const total = data?.pagination.total || 0;

  // Reset the cross-page "select all" whenever the result set changes.
  useEffect(() => {
    setSelectAllMatching(false);
  }, [searchQuery, warehouseId, statusFilter, pagination.pageIndex, pagination.pageSize]);

  const columns = useMemo<ColumnDef<Stock>[]>(
    () => [
      ...(canDeleteStock ? [buildSelectColumn<Stock>()] : []),
      {
        accessorKey: 'product.product_code',
        header: ({ column }) => <DataGridColumnHeader title="Product Code" column={column} />,
        cell: ({ row }) => row.original.product?.product_code || '-',
        size: 150,
        meta: { headerTitle: 'Product Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'product.product_name',
        header: ({ column }) => <DataGridColumnHeader title="Product Name" column={column} />,
        cell: ({ row }) => row.original.product?.product_name || '-',
        size: 250,
        meta: { headerTitle: 'Product Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'product.category.category_name',
        header: ({ column }) => <DataGridColumnHeader title="Category" column={column} />,
        cell: ({ row }) => row.original.product?.category?.category_name || '-',
        size: 150,
        meta: { headerTitle: 'Category' },
      },
      {
        accessorKey: 'warehouse.warehouse_name',
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        cell: ({ row }) => row.original.warehouse?.warehouse_name || '-',
        size: 150,
        meta: { headerTitle: 'Warehouse' },
      },
      {
        accessorKey: 'available',
        header: ({ column }) => <DataGridColumnHeader title="Available" column={column} />,
        cell: ({ row }) => <span className="font-medium">{row.original.available ?? 0}</span>,
        size: 100,
        meta: { headerTitle: 'Available' },
      },
      {
        accessorKey: 'reserved_quantity',
        header: ({ column }) => <DataGridColumnHeader title="Reserved" column={column} />,
        cell: ({ row }) => row.original.reserved_quantity ?? 0,
        size: 100,
        meta: { headerTitle: 'Reserved' },
      },
      {
        accessorKey: 'quantity',
        header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
        cell: ({ row }) => row.original.quantity ?? 0,
        size: 100,
        meta: { headerTitle: 'Total' },
      },
      {
        accessorKey: 'product.reorder_level',
        header: ({ column }) => <DataGridColumnHeader title="Reorder Level" column={column} />,
        cell: ({ row }) => row.original.product?.reorder_level || '-',
        size: 120,
        meta: { headerTitle: 'Reorder Level' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const status = row.original.status || 'normal';
          return (
            <Badge status={status}>
              {(status || '').charAt(0).toUpperCase() + (status || '').slice(1)}
            </Badge>
          );
        },
        size: 100,
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
    [canDeleteStock],
  );

  const table = useReactTable({
    columns,
    data: normalizedData,
    pageCount: Math.ceil(total / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: canDeleteStock,
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

  const handleRowClick = (row: Stock) => {
    if (row.product_id && row.warehouse_id) {
      router.push(`/inventory-management/stock/${row.product_id}/${row.warehouse_id}`);
    }
  };

  const handleUploadTemplate = async (rows: any[], _helpers?: unknown, file?: File) => {
    await bulkImportStock(rows);
    notifyImportQueued();
    if (file) {
      try {
        await replaceLatestStockList(file);
        queryClient.invalidateQueries({ queryKey: ['current-stock-list-attachment'] });
        queryClient.invalidateQueries({ queryKey: ['attachments'] });
      } catch (attachErr) {
        toast.error(attachErr instanceof Error ? attachErr.message : 'Failed to save as Stock List attachment');
      }
    }
    toast.success('Import job queued successfully. Processing in background. Please refresh after a while to see results.', {
      duration: 5000,
      action: { label: 'View Status', onClick: () => router.push(`/system-management/import-jobs`) },
    });
    queryClient.invalidateQueries({ queryKey: ['stock-balance'] });
  };

  // All-records (server) export: fetch the full filtered set, build the xlsx with
  // the chosen columns. Decoupled from the grid - the page never loads all rows. (D4/F)
  const exportAllRecords = async (chosenColumnIds: string[]) => {
    const all = await exportStockBalance({ warehouse_id: warehouseId || undefined, status: statusFilter || undefined });
    const rows = all.map(normalizeStock).map((item) => {
      const out: Record<string, unknown> = {};
      for (const id of chosenColumnIds) out[id] = resolveExportValue(item, id);
      return out;
    });
    const cols: ColumnOption[] = chosenColumnIds.map((id) => ({
      key: id,
      label: EXPORT_COLUMN_LABELS[id] ?? id,
      selected: true,
    }));
    await generateExcelFile(rows, cols, EXPORT_FILENAME);
    toast.success(`Exported ${rows.length} record(s)`);
  };

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      onRowClick={handleRowClick}
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
                placeholder="Search stock..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: Boolean(warehouseId || statusFilter),
              activeCount: (warehouseId ? 1 : 0) + (statusFilter ? 1 : 0),
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Warehouse</Label>
                    <SearchableSelect
                      value={warehouseId || 'all'}
                      onChange={(v) => setWarehouseId(v === 'all' ? '' : v)}
                      options={[
                        { value: 'all', label: 'All warehouses' },
                        ...warehouses.map((w) => ({ value: w.id, label: w.warehouse_name ?? '' })),
                      ]}
                      placeholder="All warehouses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter || 'all'}
                      onChange={(v) => setStatusFilter(v === 'all' ? '' : v)}
                      options={[
                        { value: 'all', label: 'All statuses' },
                        { value: 'critical', label: 'Critical' },
                        { value: 'low', label: 'Low' },
                        { value: 'normal', label: 'Normal' },
                        { value: 'overstock', label: 'Overstock' },
                      ]}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  {(warehouseId || statusFilter) && (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setWarehouseId('');
                          setStatusFilter('');
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: EXPORT_FILENAME, allRecords: { onExport: exportAllRecords } }}
            selectAllMatching={
              canDeleteStock || total > normalizedData.length
                ? {
                    total,
                    loadedCount: normalizedData.length,
                    active: selectAllMatching,
                    onSelectAll: () => setSelectAllMatching(true),
                    onClear: () => {
                      setSelectAllMatching(false);
                      setRowSelection({});
                    },
                  }
                : undefined
            }
            secondaryActions={[
              {
                key: 'import',
                label: 'Import',
                icon: Upload,
                onClick: () => setUploadDialogOpen(true),
              },
              ...(autocountPull.visible
                ? [
                    {
                      key: 'autocount-pull',
                      label: autocountPull.label,
                      icon: CloudDownload,
                      // Zero-arg on purpose: the toolbar passes the click event to `onClick`,
                      // and a pull takes an optional scope object, not an event.
                      onClick: () => void autocountPull.onSelect(),
                    },
                  ]
                : []),
              stockListAttachment
                ? {
                    key: 'stock-list',
                    label: 'Stock List',
                    icon: FileSpreadsheet,
                    href: `/resource-management/attachments/${stockListAttachment.id}`,
                  }
                : {
                    key: 'stock-list',
                    label: 'Stock List',
                    icon: FileSpreadsheet,
                    disabled: true,
                    disabledReason: 'Import a stock file to create the Stock List attachment',
                  },
            ]}
            bulkActions={
              canDeleteStock
                ? [
                    {
                      key: 'delete',
                      label: 'Delete',
                      icon: Trash2,
                      destructive: true,
                      onClick: () => setBulkDeleteDialogOpen(true),
                    },
                  ]
                : []
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
      <TemplateUploadDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        onTest={async (rows: any[]) => validateStockImport(rows)}
        onUpload={handleUploadTemplate}
      />
      <StockBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={setBulkDeleteDialogOpen}
        stockIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
    </DataGrid>
  );
}

/** Resolve an export cell value for a nested/flat column id off a normalized Stock. */
function resolveExportValue(item: Stock, id: string): unknown {
  return id.split('.').reduce<any>((acc, key) => (acc == null ? acc : acc[key]), item);
}
