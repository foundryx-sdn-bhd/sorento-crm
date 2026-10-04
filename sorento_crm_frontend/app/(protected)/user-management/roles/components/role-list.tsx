'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
} from '@tanstack/react-table';
import {
  Ellipsis,
  Plus,
  UserRound,
} from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { PillOverflow, PillOverflowList } from '@/components/common/PillOverflow';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import {
  DataGrid,
  DataGridApiFetchParams,
  DataGridApiResponse,
} from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { UserRole } from '@/app/models/user';
import RoleDefaultDialog from './role-default-dialog';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import RoleEditDialog from './role-edit-dialog';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

const RoleList = () => {
  // List state management
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'createdAt', desc: true },
  ]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // Form state management
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [defaultDialogOpen, setDefaultDialogOpen] = useState(false);

  const [editRole, setEditRole] = useState<UserRole | null>(null);
  // Delete asks nothing (D7): the row dims and a toast counts down with Cancel.
  // The old dialog re-read the role afterwards to check it had really gone; the
  // server now answers that itself, through the pending action's outcome.
  const deletion = useDeferredRowAction({
    actionKey: 'role.delete',
    entityType: 'role',
    successMessage: 'Role deleted',
    invalidateKeys: [['user-roles'], ['user-role-select']],
  });
  const rowPending = useRowPending<UserRole>('role');
  const [defaultRole, setDefaultRole] = useState<UserRole | null>(null);

  // Query state management
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();

  // Role list
  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['user-roles', pagination, sorting, searchQuery],
    queryFn: () =>
      fetchRoles({
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      }),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });

  // Fetch roles from the server API
  const fetchRoles = async ({
    pageIndex,
    pageSize,
    sorting,
    filters,
    searchQuery,
  }: DataGridApiFetchParams): Promise<DataGridApiResponse<UserRole>> => {
    const sortField = sorting?.[0]?.id || '';
    const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';

    const params = new URLSearchParams({
      page: String(pageIndex + 1),
      limit: String(pageSize),
      ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
      ...(searchQuery ? { query: searchQuery } : {}),
      ...Object.fromEntries(
        (filters || []).map((f) => [f.id, String(f.value)]),
      ),
    });

    const response = await apiFetch(
      `/api/user-management/roles?${params.toString()}`,
    );

    if (!response.ok) {
      throw new Error(
        'Oops! Something didn’t go as planned. Please try again in a moment.',
      );
    }

    return response.json();
  };

  // Table settings
  const columns = useMemo<ColumnDef<UserRole>[]>(
    () => [
      buildSelectColumn<UserRole>(),
      {
        accessorKey: 'name',
        id: 'name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Role" column={column} visibility />
        ),
        cell: ({ row, getValue }) => {
          const value = getValue() as string;
          const isDefault = row.original.isDefault;

          return (
            <div className="flex items-center flex-wrap gap-2">
              {value}
              {isDefault && (
                <Badge variant="outline">
                  <UserRound className="text-success" />
                  default
                </Badge>
              )}
            </div>
          );
        },
        size: 200,
        enableSorting: true,
        enableHiding: false,
        meta: {
          headerTitle: 'Role',
          skeleton: <Skeleton className="w-28 h-7" />,
        },
      },
      {
        accessorKey: 'slug',
        id: 'slug',
        header: ({ column }) => (
          <DataGridColumnHeader title="Slug" column={column} visibility />
        ),
        size: 125,
        cell: (info) => {
          const value = info.getValue() as string;

          return <Badge variant="outline">{value}</Badge>;
        },
        enableSorting: true,
        enableHiding: true,
        meta: {
          headerTitle: 'slug',
          skeleton: <Skeleton className="w-14 h-7" />,
        },
      },
      {
        accessorKey: 'permissions',
        id: 'permissions',
        header: 'Permissions',
        cell: (info) => {
          const permissions = info.getValue() as { slug: string }[] | undefined;

          if (!permissions || permissions.length === 0) {
            return <span>-</span>;
          }

          // One row line (owner rule, 29 Sep 2026): as many slugs as the column fits, the
          // rest behind "+N", every slug in the popover (`PillOverflow`) - the fixed
          // "first 3 + 'N more'" still wrapped onto two lines at the default width.
          return (
            <PillOverflow
              ariaLabel={`Permissions of ${info.row.original.name}`}
              testId={`role-permissions-${info.row.original.id}`}
              items={permissions.map((permission) => ({
                key: permission.slug,
                label: permission.slug,
              }))}
              renderPopover={(items) => <PillOverflowList items={items} />}
            />
          );
        },
        minSize: 350,
        enableSorting: false,
        enableHiding: true,
        meta: {
          headerTitle: 'Permissions',
          skeleton: <Skeleton className="w-44 h-7" />,
        },
      },
      {
        id: 'actions',
        header: 'Actions',
        cell: ({ row }) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button className="h-7 w-7" mode="icon" variant="ghost" aria-label="More actions">
                <Ellipsis />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="bottom" align="start">
              <DropdownMenuItem
                onClick={() => {
                  setEditRole(row.original);
                  setEditDialogOpen(true);
                }}
              >
                Edit role
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={row.original.isDefault}
                onClick={() => {
                  setDefaultRole(row.original);
                  setDefaultDialogOpen(true);
                }}
              >
                Set as default
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onClick={() =>
                  deletion.run({ id: row.original.id, subject: row.original.name })
                }
              >
                Delete role
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ),
        size: 75,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
        meta: {
          skeleton: <Skeleton className="size-5" />,
        },
      },
    ],
    [deletion],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row: UserRole) => row.id,
    state: {
      pagination,
      sorting,
      rowSelection,
    },
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

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setEditRole(null);
        setEditDialogOpen(true);
      }}
    >
      <Plus />
      Add Role
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
        rowPending={rowPending}
        tableLayout={{
          columnsResizable: true,
          columnsPinnable: true,
          columnsMovable: true,
          columnsVisibility: true,
        }}
        tableClassNames={{
          edgeCell: 'px-5',
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
                  isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                  placeholder="Search roles"
                  className="w-64"
                />
              }
              exportConfig={{ filename: 'roles_export.xlsx' }}
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

      <RoleEditDialog
        open={editDialogOpen}
        closeDialog={() => setEditDialogOpen(false)}
        role={editRole}
      />

      {defaultRole && (
        <RoleDefaultDialog
          open={defaultDialogOpen}
          closeDialog={() => setDefaultDialogOpen(false)}
          role={defaultRole}
        />
      )}
    </>
  );
};

export default RoleList;
