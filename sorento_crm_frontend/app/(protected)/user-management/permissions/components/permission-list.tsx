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
import { Ellipsis, Plus, Trash2 } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { formatDateTimeSafe } from '@/lib/helpers';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardFooter,
  CardHeader,
  CardTable,
} from '@/components/ui/card';
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
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { UserPermission, UserRole } from '@/app/models/user';
import { useRoleSelectQuery } from '../../roles/hooks/use-role-select-query';
import PermissionDeleteDialog from './permission-delete-dialog';
import PermissionEditDialog from './permission-edit-dialog';
import PermissionGroupDeleteDialog from './permission-group-delete-dialog';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

const PermissionList = () => {
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
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [groupDeleteDialogOpen, setGroupDeleteDialogOpen] = useState(false);
  const [editPermission, setEditPermission] = useState<UserPermission | null>(
    null,
  );
  const [deletePermission, setDeletePermission] =
    useState<UserPermission | null>(null);
  const [deletePermissionIds, setDeletePermissionIds] = useState<string[]>([]);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);

  // Query state management
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();

  // Role select query
  const {
    data: roleList,
    error: roleListError,
    refetch: refetchRoleList,
  } = useRoleSelectQuery();

  // Fetch permissions from the server API
  const fetchPermissions = async ({
    pageIndex,
    pageSize,
    sorting,
    searchQuery,
  }: DataGridApiFetchParams): Promise<DataGridApiResponse<UserPermission>> => {
    const sortField = sorting?.[0]?.id || 'createdAt';
    const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';

    const params = new URLSearchParams({
      page: String(pageIndex + 1),
      limit: String(pageSize),
      ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
      ...(searchQuery ? { query: searchQuery } : {}),
      ...(selectedRole && selectedRole !== 'all'
        ? { roleId: selectedRole }
        : {}),
    });

    const response = await apiFetch(
      `/api/user-management/permissions?${params.toString()}`,
    );

    if (!response.ok) {
      throw new Error(
        'Oops! Something didn’t go as planned. Please try again in a moment',
      );
    }

    return response.json();
  };

  // Permissions query
  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      'user-permissions',
      pagination,
      sorting,
      searchQuery,
      selectedRole,
    ],
    queryFn: () =>
      fetchPermissions({
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        filters: [
          ...(selectedRole ? [{ id: 'role', value: selectedRole }] : []),
        ],
        searchQuery,
      }),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });

  // Handle row selection
  const handleRoleSelection = (roleId: string) => {
    setSelectedRole(roleId);
    setPagination({ ...pagination, pageIndex: 0 }); // Reset to first page when filtering
  };

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  useEffect(() => {
    const selectedRowIds = Object.keys(rowSelection);
    if (selectedRowIds.length > 0) {
      setDeletePermissionIds(selectedRowIds);
    } else {
      setDeletePermissionIds([]);
    }
  }, [rowSelection]);

  // Column definitions
  const columns = useMemo<ColumnDef<UserPermission>[]>(
    () => [
      buildSelectColumn<UserPermission>(),
      {
        id: 'name',
        accessorKey: 'name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Permission" column={column} />
        ),
        cell: (info) => info.getValue(),
        size: 150,
        enableSorting: true,
        enableHiding: false,
        meta: {
          headerTitle: 'Permission',
          skeleton: <Skeleton className="w-28 h-8" />,
        },
      },
      {
        id: 'slug',
        accessorKey: 'slug',
        header: ({ column }) => (
          <DataGridColumnHeader title="Slug" column={column} />
        ),
        cell: (info) => {
          const value = info.getValue() as string;

          return <Badge variant="secondary">{value}</Badge>;
        },
        size: 150,
        enableSorting: true,
        enableHiding: false,
        meta: {
          headerTitle: 'Slug',
          skeleton: <Skeleton className="w-14 h-8" />,
        },
      },
      {
        id: 'description',
        accessorKey: 'description',
        header: ({ column }) => (
          <DataGridColumnHeader title="Description" column={column} />
        ),
        cell: (info) => {
          const value = info.getValue() as string;

          return <div className="truncate">{value}</div>;
        },
        size: 300,
        enableSorting: false,
        enableHiding: false,
        meta: {
          headerTitle: 'Description',
          skeleton: <Skeleton className="w-28 h-8" />,
        },
      },
      {
        id: 'createdAt',
        accessorKey: 'createdAt',
        header: ({ column }) => (
          <DataGridColumnHeader title="Created At" column={column} />
        ),
        cell: (info) => {
          const value = info.getValue() as string | null;
          return formatDateTimeSafe(value, '-');
        },
        enableSorting: true,
        enableHiding: false,
        meta: {
          headerTitle: 'Created At',
          skeleton: <Skeleton className="w-20 h-8" />,
        },
      },
      {
        id: 'actions',
        header: 'Actions',
        cell: ({ row }) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button mode="icon" variant="ghost" aria-label="More actions">
                <Ellipsis />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="bottom" align="start">
              <DropdownMenuItem
                onClick={() => {
                  setEditPermission(row.original);
                  setEditDialogOpen(true);
                }}
              >
                Edit permission
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                onClick={() => {
                  setDeletePermission(row.original);
                  setDeleteDialogOpen(true);
                }}
              >
                Delete permission
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ),
        size: 90,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
        meta: {
          headerTitle: 'Actions',
          skeleton: <Skeleton className="size-5" />,
        },
      },
    ],
    [],
  );

  const [columnOrder, setColumnOrder] = useState<string[]>(
    columns.map((column) => column.id as string),
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row: UserPermission) => row.id,
    state: {
      pagination,
      sorting,
      columnOrder,
      rowSelection,
    },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onColumnOrderChange: setColumnOrder,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setEditPermission(null);
        setEditDialogOpen(true);
      }}
    >
      <Plus />
      Add Permission
    </Button>
  );

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        error={error}
        onRetry={() => void refetch()}
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
                  placeholder="Search permissions"
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: Boolean(selectedRole && selectedRole !== 'all'),
                activeCount: selectedRole && selectedRole !== 'all' ? 1 : 0,
                content: (
                  <div className="space-y-2">
                    <Label>Role</Label>
                    <SearchableSelect
                      onChange={handleRoleSelection}
                      value={selectedRole || 'all'}
                      placeholder="Filter by role"
                      triggerClassName="w-full"
                      loadError={roleListError}
                      onRetry={() => void refetchRoleList()}
                      options={[
                        { value: 'all', label: 'All roles' },
                        ...(roleList ?? []).map((role: UserRole) => ({
                          value: role.id,
                          label: role.name,
                        })),
                      ]}
                    />
                    {selectedRole && selectedRole !== 'all' && (
                      <div className="flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRoleSelection('all')}
                        >
                          Clear filter
                        </Button>
                      </div>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: 'permissions_export.xlsx' }}
              primaryAction={listPrimaryAction}
              bulkActions={[
                {
                  key: 'delete',
                  label: `Delete ${deletePermissionIds.length} permission${deletePermissionIds.length !== 1 ? 's' : ''}`,
                  icon: Trash2,
                  destructive: true,
                  onClick: () => setGroupDeleteDialogOpen(true),
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
      </DataGrid>

      <PermissionEditDialog
        open={editDialogOpen}
        closeDialog={() => setEditDialogOpen(false)}
        permission={editPermission}
      />

      {deletePermission && (
        <PermissionDeleteDialog
          open={deleteDialogOpen}
          closeDialog={() => setDeleteDialogOpen(false)}
          permission={deletePermission}
        />
      )}

      {deletePermissionIds && (
        <PermissionGroupDeleteDialog
          open={groupDeleteDialogOpen}
          closeDialog={() => {
            setGroupDeleteDialogOpen(false);
            setRowSelection({});
          }}
          permissionIds={deletePermissionIds}
        />
      )}
    </>
  );
};

export default PermissionList;
