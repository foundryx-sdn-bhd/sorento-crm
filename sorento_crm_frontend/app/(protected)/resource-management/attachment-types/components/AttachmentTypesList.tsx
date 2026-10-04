'use client';

import { useMemo, useState } from 'react';
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
import { Edit2, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { useAttachmentTypes } from '../hooks/useAttachmentTypes';
import type { AttachmentType } from '../types/attachmentType.types';
import AttachmentTypeFormDialog from './AttachmentTypeFormDialog';
import AttachmentTypeDeleteDialog from './AttachmentTypeDeleteDialog';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function AttachmentTypesList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [isFormDialogOpen, setIsFormDialogOpen] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [selectedTypeId, setSelectedTypeId] = useState<string | null>(null);
  const [selectedTypeForDelete, setSelectedTypeForDelete] = useState<AttachmentType | null>(null);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useAttachmentTypes({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
  });

  const columns = useMemo<ColumnDef<AttachmentType>[]>(
    () => [
      buildSelectColumn<AttachmentType>(),
      {
        accessorKey: 'type_name',
        header: ({ column }) => <DataGridColumnHeader title="Type Name" column={column} />,
        size: 200,
        meta: { headerTitle: 'Type Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        size: 250,
        meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'allowed_extensions',
        header: ({ column }) => <DataGridColumnHeader title="Allowed Extensions" column={column} />,
        size: 200,
        meta: { headerTitle: 'Allowed Extensions' },
      },
      {
        accessorKey: 'max_file_size_mb',
        header: ({ column }) => <DataGridColumnHeader title="Max File Size (MB)" column={column} />,
        size: 150,
        meta: { headerTitle: 'Max File Size (MB)' },
      },
      {
        accessorKey: 'max_count_per_entity',
        header: ({ column }) => (
          <DataGridColumnHeader title="Max Files / Record" column={column} />
        ),
        size: 150,
        cell: ({ row }) => (
          <span className={row.original.max_count_per_entity == null ? 'text-muted-foreground' : ''}>
            {row.original.max_count_per_entity ?? 'Unlimited'}
          </span>
        ),
        meta: { headerTitle: 'Max Files / Record' },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => {
          const type = row.original;
          return (
            <div className="flex items-center gap-2">
              <Button
                mode="icon"
                variant="ghost"
                size="sm"
                aria-label="Edit"
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedTypeId(type.id);
                  setIsFormDialogOpen(true);
                }}
              >
                <Edit2 className="size-4" />
              </Button>
              <Button
                mode="icon"
                variant="ghost"
                size="sm"
                aria-label="Delete attachment type"
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedTypeForDelete(type);
                  setIsDeleteDialogOpen(true);
                }}
              >
                <Trash2 className="size-4 text-destructive" />
              </Button>
            </div>
          );
        },
        size: 100,
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
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setSelectedTypeId(null);
        setIsFormDialogOpen(true);
      }}
    >
      <Plus />
      Create Attachment Type
    </Button>
  );

  return (
    <DataGrid
      table={table}
      tableLayout={{ columnsVisibility: true }}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
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
                placeholder="Search attachment types..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'attachment_types_export.xlsx' }}
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

      <AttachmentTypeFormDialog
        open={isFormDialogOpen}
        onOpenChange={(open) => {
          setIsFormDialogOpen(open);
          if (!open) {
            setSelectedTypeId(null);
          }
        }}
        attachmentTypeId={selectedTypeId}
      />

      <AttachmentTypeDeleteDialog
        open={isDeleteDialogOpen}
        onOpenChange={(open) => {
          setIsDeleteDialogOpen(open);
          if (!open) {
            setSelectedTypeForDelete(null);
          }
        }}
        attachmentType={selectedTypeForDelete}
      />
    </DataGrid>
  );
}
