'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  RowSelectionState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Plus, Trash2, ChevronRight } from 'lucide-react';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  useEmailTemplates,
} from '../hooks/useEmailTemplates';
import type { EmailTemplate } from '../types/emailTemplate.types';
import EmailTemplateForm from './EmailTemplateForm';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function EmailTemplatesList() {
  const router = useRouter();
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 50 });
  const {
    value: queryInput,
    setValue: setQueryInput,
    debouncedValue: query,
    isSettling: querySettling,
  } = useDebouncedSearch();
  const [showForm, setShowForm] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [query]);

  const params = useMemo(
    () => ({
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
      query: query.trim() || undefined,
    }),
    [pagination, query],
  );

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = useEmailTemplates(params);
  const rows = data?.data ?? [];
  const total = data?.pagination?.total ?? 0;
  // Delete asks nothing (D7): the row dims and a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'email_template.delete',
    entityType: 'email_template',
    successMessage: 'Email template deleted',
    invalidateKeys: [['email-templates']],
  });
  const rowPending = useRowPending<EmailTemplate>('email_template');

  const columns = useMemo<ColumnDef<EmailTemplate>[]>(
    () => [
      buildSelectColumn<EmailTemplate>(),
      {
        accessorKey: 'code',
        header: ({ column }) => <DataGridColumnHeader title="Code" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary">
            {row.original.code}
          </Badge>
        ),
        size: 200,
        meta: { headerTitle: 'Code', skeleton: <Skeleton className="h-6 w-32" /> },
      },
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium" title={row.original.name}>
              {row.original.name}
            </span>
            {row.original.description && (
              <span className="text-xs text-muted-foreground truncate max-w-[260px]" title={row.original.description}>
                {row.original.description}
              </span>
            )}
          </div>
        ),
        size: 280,
        meta: { headerTitle: 'Name' },
      },
      {
        accessorKey: 'subject',
        header: ({ column }) => <DataGridColumnHeader title="Subject" column={column} />,
        cell: ({ row }) => (
          <span className="truncate max-w-[320px] block" title={row.original.subject}>
            {row.original.subject}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Subject' },
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Active" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.is_active ? 'success' : 'secondary'}>
            {row.original.is_active ? 'Yes' : 'No'}
          </Badge>
        ),
        size: 90,
        meta: { headerTitle: 'Active' },
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.updated_at),
        size: 160,
        meta: { headerTitle: 'Updated' },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0 text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                deletion.run({ id: row.original.id, subject: row.original.name });
              }}
              aria-label="Delete"
            >
              <Trash2 className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0"
              onClick={(e) => {
                e.stopPropagation();
                router.push(`/system-management/email-templates/${row.original.id}`);
              }}
              aria-label="View"
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        ),
        size: 140,
      },
    ],
    [router, deletion],
  );

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: (updater) => {
      const next = typeof updater === 'function' ? updater(pagination) : updater;
      setPagination(next);
    },
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => setShowForm(true)}>
      <Plus className="mr-1 size-4" /> Add template
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      onRowClick={(t) => t?.id && router.push(`/system-management/email-templates/${t.id}`)}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      rowPending={rowPending}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={queryInput}
                onChange={setQueryInput}
                isSettling={isSearchInFlight(querySettling, isFetching, query)}
                placeholder="Search by code, name, subject"
                className="w-64"
              />
            }
            exportConfig={{ filename: 'email_templates_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter className="flex justify-between border-t px-4 py-3">
          <DataGridPagination />
        </CardFooter>
      </Card>

      <EmailTemplateForm
        open={showForm}
        onOpenChange={setShowForm}
        onSaved={(created) => {
          router.push(`/system-management/email-templates/${created.id}`);
        }}
      />
    </DataGrid>
  );
}
