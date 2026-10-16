'use client';

import { useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type SortingState,
} from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import {
  isSearchInFlight,
  useDebouncedSearch,
} from '@/hooks/useDebouncedSearch';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useHasPermission } from '@/hooks/usePermissions';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { CONTACT_TAG_PERMS } from '../lib/permissions';
import { useContactTagsQuery } from '../hooks/useContactTags';
import { settingsCount } from '../lib/settingsCount';
import type { ContactTag } from '../types/contactTag.types';
import ContactTagFormDialog from './ContactTagFormDialog';

/** Delete is a server-deferred action: the toast is the countdown, Cancel the way back. */
function RowDelete({ tag }: { tag: ContactTag }) {
  const remove = useDeferredAction({
    actionKey: 'contact_tag.delete',
    entityType: 'contact_tag',
    entityId: tag.id,
    verb: 'Deleting',
    subject: tag.name,
    surface: 'toast',
    successMessage: 'Contact tag deleted',
    invalidateKeys: [
      ['contact-tags'],
      ['contact-tag-options'],
      ['respond-contacts'],
    ],
  });
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Delete ${tag.name}`}
      title="Delete"
      disabled={remove.isPending || remove.isBlocked}
      onClick={(e) => {
        e.stopPropagation();
        remove.start();
      }}
    >
      <Trash2 className="size-4 text-destructive" />
    </Button>
  );
}

export default function ContactTagsAdmin() {
  const canEdit = useHasPermission(CONTACT_TAG_PERMS.edit);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ContactTag | null>(null);

  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
  });

  const listParams = useMemo(
    () => ({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      filters: {} as Record<string, string>,
    }),
    [pagination, sorting, searchQuery],
  );
  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } =
    useContactTagsQuery(listParams);

  const rowHref = useCallback(
    (tag: ContactTag) => {
      const search = buildDetailSearch(listParams, listParams.filters);
      return `/user-management/contact-tags/${tag.id}${search ? `?${search}` : ''}`;
    },
    [listParams],
  );

  const columns = useMemo<ColumnDef<ContactTag>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Name" column={column} />
        ),
        size: 180,
        meta: {
          headerTitle: 'Name',
          skeleton: <Skeleton className="h-4 w-28" />,
        },
        cell: ({ row }) => (
          <span
            className="block truncate font-medium"
            title={row.original.name}
          >
            {row.original.name}
          </span>
        ),
      },
      {
        accessorKey: 'description',
        header: ({ column }) => (
          <DataGridColumnHeader title="Description" column={column} />
        ),
        size: 200,
        enableSorting: false,
        meta: {
          headerTitle: 'Description',
          skeleton: <Skeleton className="h-4 w-40" />,
        },
        cell: ({ row }) =>
          row.original.description ? (
            <span className="block truncate" title={row.original.description}>
              {row.original.description}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
      },
      {
        id: 'settings',
        accessorFn: (row) => settingsCount(row.settings),
        header: ({ column }) => (
          <DataGridColumnHeader title="Settings" column={column} />
        ),
        size: 110,
        enableSorting: false,
        meta: {
          headerTitle: 'Settings',
          skeleton: <Skeleton className="h-4 w-14" />,
        },
        cell: ({ row }) => {
          const n = settingsCount(row.original.settings);
          return n === 0 ? (
            <span className="text-muted-foreground">None</span>
          ) : (
            <span>{`${n} setting${n === 1 ? '' : 's'}`}</span>
          );
        },
      },
      {
        id: 'differs_count',
        accessorFn: (row) => row.differs_count ?? 0,
        header: ({ column }) => (
          <DataGridColumnHeader title="Differs" column={column} />
        ),
        size: 90,
        enableSorting: false,
        meta: {
          headerTitle: 'Differs',
          skeleton: <Skeleton className="h-4 w-10" />,
        },
        cell: ({ row }) =>
          row.original.differs_count == null ? (
            <span className="text-muted-foreground">-</span>
          ) : row.original.differs_count > 0 ? (
            <Link
              href={`/user-management/contacts?${new URLSearchParams({
                tag: row.original.id,
                tag_settings: 'differs',
              }).toString()}`}
              className="tabular-nums text-primary hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {row.original.differs_count}
            </Link>
          ) : (
            <span className="tabular-nums text-muted-foreground">0</span>
          ),
      },
      {
        id: 'contact_count',
        accessorFn: (row) => row.contact_count,
        header: ({ column }) => (
          <DataGridColumnHeader title="Contacts" column={column} />
        ),
        size: 90,
        enableSorting: false,
        meta: {
          headerTitle: 'Contacts',
          skeleton: <Skeleton className="h-4 w-10" />,
        },
        cell: ({ row }) => (
          <span className="tabular-nums">{row.original.contact_count}</span>
        ),
      },
      {
        accessorKey: 'sort_order',
        header: ({ column }) => (
          <DataGridColumnHeader title="Sort order" column={column} />
        ),
        size: 90,
        meta: {
          headerTitle: 'Sort order',
          skeleton: <Skeleton className="h-4 w-10" />,
        },
        cell: ({ row }) => (
          <span className="tabular-nums">{row.original.sort_order}</span>
        ),
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        size: 100,
        meta: {
          headerTitle: 'Status',
          skeleton: <Skeleton className="h-5 w-16" />,
        },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="success" appearance="light">
              Active
            </Badge>
          ) : (
            <Badge variant="secondary" appearance="light">
              Inactive
            </Badge>
          ),
      },
      ...(canEdit
        ? [
            {
              id: 'actions',
              header: '',
              size: 90,
              enableSorting: false,
              enableHiding: false,
              enableResizing: false,
              cell: ({ row }: { row: { original: ContactTag } }) => (
                <div
                  className="flex items-center justify-end gap-1"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Edit ${row.original.name}`}
                    title="Edit"
                    onClick={() => {
                      setEditing(row.original);
                      setDialogOpen(true);
                    }}
                  >
                    <Pencil className="size-4" />
                  </Button>
                  <RowDelete tag={row.original} />
                </div>
              ),
            } satisfies ColumnDef<ContactTag>,
          ]
        : []),
    ],
    [canEdit],
  );

  const total = data?.pagination?.total ?? 0;
  const table = useReactTable({
    columns,
    data: data?.data ?? [],
    pageCount: Math.ceil(total / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const primaryAction = canEdit ? (
    <Button
      onClick={() => {
        setEditing(null);
        setDialogOpen(true);
      }}
    >
      <Plus className="size-4 mr-2" />
      Add tag
    </Button>
  ) : undefined;

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      emptyMessage={
        <div className="flex flex-col items-center gap-1">
          <h3 className="text-base font-medium text-foreground">
            No contact tags
          </h3>
          <p className="text-sm text-muted-foreground">
            Tags you add appear here.
          </p>
        </div>
      }
      tableLayout={{
        width: 'fixed',
        columnsVisibility: true,
        columnsResizable: true,
      }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(
                  searchSettling,
                  isFetching,
                  searchQuery,
                )}
                placeholder="Search tags..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'contact_tags_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={primaryAction}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
      <ContactTagFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        tag={editing}
      />
    </DataGrid>
  );
}
