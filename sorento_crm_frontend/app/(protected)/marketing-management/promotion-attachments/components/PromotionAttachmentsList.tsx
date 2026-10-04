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
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import type { PromotionAttachment } from '../types/promotionAttachment.types';
import { usePromotionAttachments } from '../hooks/usePromotionAttachments';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function PromotionAttachmentsList() {
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = usePromotionAttachments({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
  });

  const columns = useMemo<ColumnDef<PromotionAttachment>[]>(
    () => [
      buildSelectColumn<PromotionAttachment>(),
      {
        accessorKey: 'promotion.description',
        header: ({ column }) => <DataGridColumnHeader title="Promotion" column={column} />,
        size: 250,
        cell: ({ row }) => row.original.promotion?.description || row.original.promotion_id || '-',
        meta: { headerTitle: 'Promotion', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'attachment.original_filename',
        header: ({ column }) => <DataGridColumnHeader title="Attachment Filename" column={column} />,
        size: 250,
        cell: ({ row }) => row.original.attachment?.original_filename || '-',
        meta: { headerTitle: 'Attachment Filename', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'attachment.attachment_type.type_name',
        header: ({ column }) => <DataGridColumnHeader title="Attachment Type" column={column} />,
        size: 150,
        cell: ({ row }) => row.original.attachment?.attachment_type?.type_name || '-',
        meta: { headerTitle: 'Attachment Type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'is_primary',
        header: ({ column }) => <DataGridColumnHeader title="Is Primary" column={column} />,
        size: 100,
        cell: ({ row }) => (
          row.original.is_primary ? (
            <Badge variant="primary">Primary</Badge>
          ) : (
            <Badge variant="outline">Secondary</Badge>
          )
        ),
        meta: { headerTitle: 'Is Primary', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'sort_order',
        header: ({ column }) => <DataGridColumnHeader title="Sort Order" column={column} />,
        size: 100,
        cell: ({ row }) => row.original.sort_order ?? '-',
        meta: { headerTitle: 'Sort Order', skeleton: <Skeleton className="h-4 w-16" /> },
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

  const handleRowClick = (row: PromotionAttachment) => {
    if (row.promotion_id) {
      router.push(`/marketing-management/promotions/${row.promotion_id}`);
    }
  };

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
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
                placeholder="Search promotion attachments..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'promotion_attachments_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
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
