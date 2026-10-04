'use client';

/**
 * DataGrid listing of saved tag sizes (S4, AC-S4-5).
 *
 * Columns: name, width (mm), height (mm), created by, updated, row `...` menu
 * (Edit, Delete) - never inline buttons, per the row-actions standard.
 */

import { useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Ellipsis, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useRowPending } from '@/hooks/useDeferredRowAction';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useDeleteTagSizePreset, useTagSizesQuery } from '../hooks/useTagSizes';
import type { TagSizeRecord } from '../../services/tagSizeService';
import { TagSizeDialog } from './TagSizeDialog';

export function TagSizesList() {
  const { data: sizes = [], isLoading, error, refetch } = useTagSizesQuery();
  // Delete asks nothing (D7): the row dims and a toast counts down with
  // Cancel, exactly like `TagTemplatesList`'s own row delete.
  const deletion = useDeleteTagSizePreset();
  const rowPending = useRowPending<TagSizeRecord>('tag_size_preset');

  const [search, setSearch] = useState('');
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<TagSizeRecord | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sizes;
    return sizes.filter((s) => s.name.toLowerCase().includes(q));
  }, [sizes, search]);

  const columns = useMemo<ColumnDef<TagSizeRecord>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader column={column} title="Name" />,
        size: 220,
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.name}>
            {row.original.name}
          </span>
        ),
      },
      {
        id: 'width_mm',
        header: 'Width (mm)',
        size: 110,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{row.original.width_mm}</span>
        ),
      },
      {
        id: 'height_mm',
        header: 'Height (mm)',
        size: 110,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{row.original.height_mm}</span>
        ),
      },
      {
        id: 'created_by_name',
        header: 'Created by',
        size: 150,
        cell: ({ row }) => (
          <span className="truncate text-muted-foreground" title={row.original.created_by_name ?? ''}>
            {row.original.created_by_name ?? '-'}
          </span>
        ),
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader column={column} title="Updated" />,
        size: 170,
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {formatDateTimeInMalaysia(row.original.updated_at)}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        size: 60,
        cell: ({ row }) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                className="h-7 w-7"
                mode="icon"
                variant="ghost"
                aria-label="More actions"
                onClick={(e) => e.stopPropagation()}
              >
                <Ellipsis className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="bottom" align="start">
              <DropdownMenuItem
                onClick={() => {
                  setEditing(row.original);
                  setDialogOpen(true);
                }}
              >
                Edit size
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onClick={() =>
                  deletion.run({ id: row.original.id, subject: row.original.name })
                }
              >
                Delete size
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ),
        enableResizing: false,
      },
    ],
    [deletion],
  );

  const table = useReactTable({
    data: filtered,
    columns,
    getRowId: (row) => row.id,
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  const listPrimaryAction = (
    <Button
      size="sm"
      onClick={() => {
        setEditing(null);
        setDialogOpen(true);
      }}
    >
      <Plus className="mr-1.5 size-3.5" />
      Add size
    </Button>
  );

  return (
    <>
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            showColumns={false}
            exportConfig={false}
            searchSlot={
              <ListSearchInput
                value={search}
                onChange={setSearch}
                placeholder="Search sizes"
                className="w-64"
              />
            }
            primaryAction={listPrimaryAction}
          />
        </CardHeader>
        <CardTable>
          <DataGrid
            table={table}
            recordCount={filtered.length}
            isLoading={isLoading}
            error={error}
            onRetry={() => void refetch()}
            tableLayout={{ width: 'fixed', columnsResizable: true }}
            rowPending={rowPending}
          >
            <DataGridTable />
            <CardFooter className="justify-center">
              <DataGridPagination />
            </CardFooter>
          </DataGrid>
        </CardTable>
      </Card>

      <TagSizeDialog open={dialogOpen} onOpenChange={setDialogOpen} size={editing} />
    </>
  );
}
