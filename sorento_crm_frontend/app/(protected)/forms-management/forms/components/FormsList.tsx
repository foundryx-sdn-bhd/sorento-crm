'use client';

import { useCallback, useMemo, useState } from 'react';
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
import { Plus, Trash2 } from 'lucide-react';
import { FormRowActions } from '../actions';
import { Badge } from '@/components/ui/badge';
import LookupBoundLabel from '@/components/common/LookupBoundLabel';
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
import { useForms } from '../hooks/useForms';
import type { Form } from '../types/form.types';
import { formatDate } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import FormBulkDeleteDialog from './FormBulkDeleteDialog';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import Link from 'next/link';

export default function FormsList() {
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'updated_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<string>('all');

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(state.filters.status ?? 'all');
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [statusFilter, searchQuery]);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useForms({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter !== 'all' ? statusFilter : undefined,
  });

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from.
  // Memoised, and in the columns' deps: a columns memo that captured the first
  // `rowHref` would keep linking every row to page 1 of an unfiltered list.
  const rowHref = useCallback(
    (row: Form) => {
      const search = buildDetailSearch(
        {
          pageIndex: pagination.pageIndex,
          pageSize: pagination.pageSize,
          sorting,
          searchQuery,
        },
        {
          status: statusFilter !== 'all' ? statusFilter : undefined,
        },
      );
      const qs = search ? `?${search}` : '';
      return `/forms-management/forms/${row.id}${qs}`;
    },
    [pagination.pageIndex, pagination.pageSize, sorting, searchQuery, statusFilter],
  );

  const columns = useMemo<ColumnDef<Form>[]>(
    () => [
      buildSelectColumn<Form>(),
      {
        accessorKey: 'code',
        header: ({ column }) => <DataGridColumnHeader title="Form Code" column={column} />,
        cell: ({ row }) => (
          // The row opens the form, and the code is the same link said out loud:
          // copyable, middle-clickable, and visibly the way in.
          <Link
            href={rowHref(row.original)}
            className="truncate font-medium text-primary hover:underline"
            title={row.original.code}
            onClick={(event) => event.stopPropagation()}
          >
            {row.original.code}
          </Link>
        ),
        size: 180,
        minSize: 120,
        meta: { headerTitle: 'Form Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Form Name" column={column} />,
        cell: ({ row }) => (
          <div className="truncate" title={row.original.name}>
            {row.original.name}
          </div>
        ),
        size: 250,
        minSize: 150,
        meta: { headerTitle: 'Form Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'form_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => (
          <Badge variant="outline" className="font-normal">
            <LookupBoundLabel
              table="forms"
              column="form_type"
              value={row.original.form_type || 'marketing'}
            />
          </Badge>
        ),
        size: 110,
        minSize: 90,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'purpose',
        header: ({ column }) => <DataGridColumnHeader title="Purpose" column={column} />,
        cell: ({ row }) => {
          const purpose = row.original.purpose;
          if (!purpose || purpose.trim() === '') {
            return <span className="text-muted-foreground">-</span>;
          }
          return (
            <div className="truncate" title={purpose}>
              {purpose}
            </div>
          );
        },
        size: 200,
        minSize: 120,
        meta: { headerTitle: 'Purpose', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'language',
        header: ({ column }) => <DataGridColumnHeader title="Language" column={column} />,
        size: 100,
        meta: { headerTitle: 'Language' },
      },
      {
        accessorKey: 'version',
        header: ({ column }) => <DataGridColumnHeader title="Version" column={column} />,
        size: 100,
        meta: { headerTitle: 'Version' },
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
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Last Updated" column={column} />,
        cell: ({ row }) => formatDate(new Date(row.original.updated_at)),
        size: 150,
        meta: { headerTitle: 'Last Updated' },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => (
          <FormRowActions
            form={{ id: row.original.id, name: row.original.name, code: row.original.code }}
          />
        ),
        size: 40,
        enableHiding: false,
      },
    ],
    [rowHref],
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
    enableColumnResizing: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/forms-management/forms/new')}>
      <Plus />
      Create Form
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      standardToolbar={false}
      tableLayout={{ columnsVisibility: true, columnsResizable: true }}
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
                placeholder="Search forms..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: statusFilter !== 'all',
              activeCount: statusFilter !== 'all' ? 1 : 0,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter}
                      onChange={setStatusFilter}
                      options={[
                        { value: 'all', label: 'All statuses' },
                        { value: 'active', label: 'Active' },
                        { value: 'inactive', label: 'Inactive' },
                      ]}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  {statusFilter !== 'all' && (
                    <div className="flex justify-end">
                      <Button variant="ghost" size="sm" onClick={() => setStatusFilter('all')}>
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'forms_export.xlsx' }}
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
      <FormBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={setBulkDeleteDialogOpen}
        formIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
    </DataGrid>
  );
}
