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
import type { PublicHoliday } from '../types/workCalendar.types';
import { usePublicHolidays } from '../hooks/useWorkCalendar';
import PublicHolidayFormDialog from './PublicHolidayFormDialog';
import PublicHolidayDeleteDialog from './PublicHolidayDeleteDialog';

export default function PublicHolidaysList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'date', desc: false }]);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [selectedHoliday, setSelectedHoliday] = useState<PublicHoliday | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = usePublicHolidays({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
  });

  const columns = useMemo<ColumnDef<PublicHoliday>[]>(
    () => [
      buildSelectColumn<PublicHoliday>(),
      {
        accessorKey: 'date',
        header: ({ column }) => <DataGridColumnHeader title="Date" column={column} />,
        cell: ({ row }) => row.original.date,
        size: 140,
        meta: { headerTitle: 'Date', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Holiday" column={column} />,
        size: 220,
        meta: { headerTitle: 'Holiday', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        size: 300,
        cell: ({ row }) => row.original.description || '-',
        meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <Button
              mode="icon"
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                setSelectedHoliday(row.original);
                setIsFormOpen(true);
              }}
              aria-label="Edit"
            >
              <Edit2 className="size-4" />
            </Button>
            <Button
              mode="icon"
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                setSelectedHoliday(row.original);
                setIsDeleteOpen(true);
              }}
              aria-label="Delete holiday"
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ),
        size: 100,
        enableHiding: false,
      },
    ],
    [],
  );

  const table = useReactTable({
    data: data?.data || [],
    columns,
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
    pageCount: data ? Math.ceil(data.pagination.total / pagination.pageSize) : 0,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setSelectedHoliday(null);
        setIsFormOpen(true);
      }}
    >
      <Plus className="size-4 mr-2" />
      Add Holiday
    </Button>
  );

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ columnsVisibility: true }}
      >
        <Card>
          <CardHeader className="block space-y-3">
            <div>
              <h3 className="text-base font-semibold">Public Holidays</h3>
              <p className="text-sm text-muted-foreground">Dates excluded from delivery calculations.</p>
            </div>
            <DataGridListToolbar
              table={table}
              exportConfig={{ filename: 'public_holidays_export.xlsx' }}
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
      </DataGrid>

      <PublicHolidayFormDialog
        open={isFormOpen}
        onOpenChange={setIsFormOpen}
        holiday={selectedHoliday}
      />
      <PublicHolidayDeleteDialog
        open={isDeleteOpen}
        onOpenChange={setIsDeleteOpen}
        holiday={selectedHoliday}
      />
    </>
  );
}
