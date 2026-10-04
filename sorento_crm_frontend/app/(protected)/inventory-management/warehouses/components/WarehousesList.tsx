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
import { Plus, Upload, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn, selectedRowIds } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { Skeleton } from '@/components/ui/skeleton';
import { TemplateUploadDialog } from '@/components/template/TemplateUploadDialog';
import { useImportJobDrawer } from '@/components/upload-activity';
import { useWarehouses } from '../hooks/useWarehouses';
import { bulkImportWarehouses, validateWarehouseImport } from '../services/warehouseService';
import type { Warehouse } from '../types/warehouse.types';
import WarehouseBulkDeleteDialog from './WarehouseBulkDeleteDialog';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function WarehousesList() {
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
  } = useDebouncedSearch();
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from. Before this the only way in was a chevron button at the end of
  // the row, which is a target the width of a thumbnail on a list of warehouses.
  const rowHref = (row: Warehouse) => {
    const search = buildDetailSearch({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
    });
    return `/inventory-management/warehouses/${row.id}${search ? `?${search}` : ''}`;
  };

  const handleImportUpload = async (rows: Record<string, unknown>[]) => {
    await bulkImportWarehouses(rows);
    notifyImportQueued();
    toast.success(
      'Import job queued successfully. Processing in background. Refresh shortly to see results.',
      {
        duration: 5000,
        action: {
          label: 'View Status',
          onClick: () => router.push('/system-management/import-jobs'),
        },
      },
    );
    queryClient.invalidateQueries({ queryKey: ['warehouses'] });
  };

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useWarehouses({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
  });

  const columns = useMemo<ColumnDef<Warehouse>[]>(
    () => [
      buildSelectColumn<Warehouse>(),
      {
        accessorKey: 'warehouse_code',
        header: ({ column }) => <DataGridColumnHeader title="System Location" column={column} />,
        size: 150,
        meta: { headerTitle: 'System Location', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'warehouse_name',
        header: ({ column }) => <DataGridColumnHeader title="System Location Description" column={column} />,
        size: 240,
        cell: ({ row }) => row.original.warehouse_name || '-',
        meta: { headerTitle: 'System Location Description', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'location',
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        size: 200,
        meta: { headerTitle: 'Warehouse', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'zones_count',
        header: ({ column }) => <DataGridColumnHeader title="Zones" column={column} />,
        size: 100,
        cell: ({ row }) => row.original.zones_count || 0,
        meta: { headerTitle: 'Zones' },
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.is_active ? 'success' : 'secondary'}>
            {row.original.is_active ? 'Active' : 'Inactive'}
          </Badge>
        ),
        size: 100,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'fulfilment_planning',
        header: ({ column }) => <DataGridColumnHeader title="Fulfilment planning" column={column} />,
        cell: ({ row }) => (
          <Badge
            variant={row.original.fulfilment_planning ? 'success' : 'secondary'}
          >
            {row.original.fulfilment_planning ? 'On' : 'Off'}
          </Badge>
        ),
        size: 160,
        meta: { headerTitle: 'Fulfilment planning' },
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
    columnResizeMode: 'onChange',
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/inventory-management/warehouses/new')}>
      <Plus />
      Create Warehouse
    </Button>
  );

  return (
    <DataGrid table={table} recordCount={data?.pagination.total || 0} isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      standardToolbar={false}
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
                placeholder="Search warehouses..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'warehouses_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
            secondaryActions={[
              {
                key: 'import',
                label: 'Import',
                icon: Upload,
                onClick: () => setUploadDialogOpen(true),
              },
            ]}
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
      <TemplateUploadDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        onTest={async (rows) =>
          validateWarehouseImport(rows as Record<string, unknown>[]).then((r) => ({
            valid: r.valid,
            errors: r.errors,
            warnings: r.warnings.map((w) => {
              if (typeof w === 'string') return w;
              const row = w.row !== undefined ? `Row ${w.row}: ` : '';
              return `${row}${w.message ?? ''}`;
            }),
            summary: r.summary,
          }))
        }
        onUpload={async (rows) => handleImportUpload(rows as Record<string, unknown>[])}
      />
      <WarehouseBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={setBulkDeleteDialogOpen}
        warehouseIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
    </DataGrid>
  );
}
