'use client';

import { useMemo, useState } from 'react';

import { useQuery, useQueryClient } from '@tanstack/react-query';
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
import { LoaderCircleIcon, Mail, Plus, Trash2, UserCheck, UserX } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { formatDateSafe, formatDateTimeInMalaysia, getInitials } from '@/lib/helpers';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { PillOverflow, PillOverflowList } from '@/components/common/PillOverflow';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar, type ToolbarAction } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Switch } from '@/components/ui/switch';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { User, UserRoleSimple, UserStatus } from '@/app/models/user';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { UserRowActions } from '../actions';
import { pendingEntityKey, usePendingEntityKeys } from '@/lib/pending-entity-store';
import {
  fetchUsersListPage,
  usersListFilters,
  usersListQueryKey,
} from '../lib/listQuery';
import { setDailySlaSummarySubscription } from '../services/userService';
import { useEntityMutation } from '@/hooks/useEntityMutation';
import { useRoleSelectQuery } from '../../roles/hooks/use-role-select-query';
import { getUserStatusProps, UserStatusProps } from '../constants/status';
import UserInviteDialog from './user-add-dialog';
import { toast } from '@/lib/toast';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import type { Table as ReactTable } from '@tanstack/react-table';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

type UserFilterField = 'role' | 'status' | 'trashed';
type UserFilterCondition = { id: string; field: UserFilterField; value: string };

interface UsersToolbarProps {
  table: ReactTable<User>;
  searchInput: string;
  setSearchInput: (value: string) => void;
  searchSettling: boolean;
  searchQuery: string;
  isFetching: boolean;
  resetSearch: (value: string) => void;
  selectedRole: string | null;
  setSelectedRole: (value: string) => void;
  selectedStatus: string | null;
  setSelectedStatus: (value: string) => void;
  selectedTrashed: string;
  setSelectedTrashed: (value: string) => void;
  setPagination: React.Dispatch<React.SetStateAction<PaginationState>>;
  bulkActionPending: boolean;
  runBulkAction: (
    action: 'delete' | 'activate' | 'deactivate' | 'permanent_delete' | 'resend_invite',
  ) => void;
  roleList: UserRoleSimple[] | undefined;
  /** The role options read's error (L5): the role filter says so instead of looking empty. */
  roleListError: unknown;
  refetchRoleList: () => void;
  addUserButton: React.ReactNode;
}

/**
 * The list toolbar (search + filters + bulk actions), hoisted to module scope
 * so it keeps a stable component identity across parent re-renders. Defining
 * this inside UserList made every keystroke in the search box create a NEW
 * function type, so React unmounted and remounted the whole toolbar - and
 * with it the search input, dropping focus after every character.
 */
const UsersToolbar = ({
  table,
  searchInput,
  setSearchInput,
  searchSettling,
  searchQuery,
  isFetching,
  resetSearch,
  selectedRole,
  setSelectedRole,
  selectedStatus,
  setSelectedStatus,
  selectedTrashed,
  setSelectedTrashed,
  setPagination,
  bulkActionPending,
  runBulkAction,
  roleList,
  roleListError,
  refetchRoleList,
  addUserButton,
}: UsersToolbarProps) => {
  const initialConditionsFromApplied = (): UserFilterCondition[] => {
    const out: UserFilterCondition[] = [];
    if ((selectedRole || 'all') !== 'all') {
      out.push({ id: crypto.randomUUID(), field: 'role', value: selectedRole || 'all' });
    }
    if ((selectedStatus || 'all') !== 'all') {
      out.push({ id: crypto.randomUUID(), field: 'status', value: selectedStatus || 'all' });
    }
    if (selectedTrashed !== 'exclude') {
      out.push({ id: crypto.randomUUID(), field: 'trashed', value: selectedTrashed });
    }
    return out;
  };

  const [draftConditions, setDraftConditions] = useState<UserFilterCondition[]>(() => initialConditionsFromApplied());

  const addCondition = () => {
    setDraftConditions((prev) => [...prev, { id: crypto.randomUUID(), field: 'role', value: 'all' }]);
  };

  const updateCondition = (id: string, patch: Partial<UserFilterCondition>) => {
    setDraftConditions((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  };

  const removeCondition = (id: string) => {
    setDraftConditions((prev) => prev.filter((c) => c.id !== id));
  };

  const applyConditions = () => {
    let role = 'all';
    let status = 'all';
    let trashed = 'exclude';
    for (const c of draftConditions) {
      if (c.field === 'role' && c.value) role = c.value;
      if (c.field === 'status' && c.value) status = c.value;
      if (c.field === 'trashed' && c.value) trashed = c.value;
    }
    setSelectedRole(role);
    setSelectedStatus(status);
    setSelectedTrashed(trashed);
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  const filtersActiveCount =
    ((selectedRole || 'all') !== 'all' ? 1 : 0) +
    ((selectedStatus || 'all') !== 'all' ? 1 : 0) +
    (selectedTrashed !== 'exclude' ? 1 : 0);

  const bulkActions: ToolbarAction[] = [
    {
      key: 'activate',
      label: 'Bulk activate',
      icon: UserCheck,
      disabled: bulkActionPending || selectedTrashed === 'only',
      onClick: () => runBulkAction('activate'),
    },
    {
      key: 'deactivate',
      label: 'Bulk deactivate',
      icon: UserX,
      disabled: bulkActionPending || selectedTrashed === 'only',
      onClick: () => runBulkAction('deactivate'),
    },
    {
      key: 'resend_invite',
      label: 'Bulk send invitation',
      icon: Mail,
      disabled: bulkActionPending || selectedTrashed === 'only',
      onClick: () => runBulkAction('resend_invite'),
    },
    {
      key: 'delete',
      label: 'Trash',
      icon: Trash2,
      destructive: true,
      disabled: bulkActionPending || selectedTrashed === 'only',
      onClick: () => runBulkAction('delete'),
    },
    ...(selectedTrashed === 'only'
      ? [
          {
            key: 'permanent_delete',
            label: 'Permanently delete',
            icon: Trash2,
            destructive: true,
            disabled: bulkActionPending,
            onClick: () => runBulkAction('permanent_delete'),
          } as ToolbarAction,
        ]
      : []),
  ];

  return (
    <CardHeader className="block">
      <DataGridListToolbar
        table={table}
        searchSlot={
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
            onSubmit={() => resetSearch(searchInput)}
            placeholder="Search users"
            className="w-full sm:w-40 md:w-64"
          />
        }
        filters={{
          kind: 'custom',
          active: filtersActiveCount > 0,
          activeCount: filtersActiveCount,
          content: (
            <div className="space-y-3">
              <p className="text-sm font-medium">Advanced filters</p>
              <div className="space-y-2">
                {draftConditions.map((cond) => (
                  <div key={cond.id} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                    <SearchableSelect
                      value={cond.field}
                      onChange={(v) =>
                        updateCondition(cond.id, {
                          field: v as UserFilterField,
                          value: v === 'trashed' ? 'exclude' : 'all',
                        })
                      }
                      options={[
                        { value: 'role', label: 'Role' },
                        { value: 'status', label: 'Status' },
                        { value: 'trashed', label: 'Trashed' },
                      ]}
                    />
                    {cond.field === 'role' ? (
                      <SearchableSelect
                        value={cond.value}
                        onChange={(v) => updateCondition(cond.id, { value: v })}
                        options={[
                          { value: 'all', label: 'All roles' },
                          ...(roleList ?? []).map((role) => ({
                            value: role.id,
                            label: role.name,
                          })),
                        ]}
                        loadError={roleListError}
                        onRetry={refetchRoleList}
                      />
                    ) : cond.field === 'status' ? (
                      <SearchableSelect
                        value={cond.value}
                        onChange={(v) => updateCondition(cond.id, { value: v })}
                        options={[
                          { value: 'all', label: 'All users' },
                          ...Object.entries(UserStatusProps).map(([status, { label }]) => ({
                            value: status,
                            label,
                          })),
                        ]}
                      />
                    ) : (
                      <SearchableSelect
                        value={cond.value}
                        onChange={(v) => updateCondition(cond.id, { value: v })}
                        options={[
                          { value: 'exclude', label: 'Active only' },
                          { value: 'only', label: 'Trashed only' },
                          { value: 'all', label: 'All' },
                        ]}
                      />
                    )}
                    <Button type="button" mode="icon" variant="ghost" onClick={() => removeCondition(cond.id)} aria-label="Remove filter condition">
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                ))}
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="outline" size="sm" className="flex-1" onClick={addCondition}>
                  <Plus className="size-4" />
                  Add condition
                </Button>
                <Button type="button" variant="outline" size="sm" className="flex-1" onClick={applyConditions}>
                  Apply
                </Button>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => {
                  setDraftConditions([]);
                  setSelectedRole('all');
                  setSelectedStatus('all');
                  setSelectedTrashed('exclude');
                  setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                }}
              >
                Clear filters
              </Button>
            </div>
          ),
        }}
        exportConfig={{ filename: 'users_export.xlsx' }}
        bulkActions={bulkActions}
        primaryAction={addUserButton}
      />
    </CardHeader>
  );
};

const UserList = () => {
  const queryClient = useQueryClient();
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'createdAt', desc: true },
  ]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [inviteDialogOpen, setInviteDialogOpen] = useState(false);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [selectedStatus, setSelectedStatus] = useState<string | null>('all');
  const [selectedTrashed, setSelectedTrashed] = useState<string>('exclude');
  const [bulkActionPending, setBulkActionPending] = useState(false);
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [bulkConfirmAction, setBulkConfirmAction] = useState<'delete' | 'activate' | 'deactivate' | 'permanent_delete' | 'resend_invite' | null>(null);

  // Role select query
  const {
    data: roleList,
    error: roleListError,
    refetch: refetchRoleList,
  } = useRoleSelectQuery();

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setSelectedRole(state.filters.roleId ?? 'all');
    setSelectedStatus(state.filters.status ?? 'all');
    setSelectedTrashed(state.filters.trashed ?? 'exclude');
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [searchQuery]);

  /**
   * The daily-summary switch. Optimistic (S7-01): it moves on press and goes
   * back only if the server refuses, rather than sitting disabled through the
   * write AND the list's refetch. The row is patched under both spellings
   * because the service normalises the backend's snake_case into camel and a
   * cached page can be holding either.
   */
  const dailySummarySubscription = useEntityMutation<
    { userId: string; subscribed: boolean },
    void
  >({
    mutationFn: ({ userId, subscribed }) => setDailySlaSummarySubscription(userId, subscribed),
    keys: [['user-users']],
    matchRow: (row, variables) => row.id === variables.userId,
    patchRow: ({ subscribed }) => ({
      dailySlaSummarySubscribed: subscribed,
      daily_sla_summary_subscribed: subscribed,
    }),
    errorMessage: 'Could not change the conversation summary setting',
  });

  // The list query, built through the shared key + fetch so the detail page's
  // pager reads THIS cache entry instead of asking the server again.
  const listParams = useMemo(
    () => ({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      filters: usersListFilters({
        role: selectedRole,
        status: selectedStatus,
        trashed: selectedTrashed,
      }),
    }),
    [pagination, sorting, searchQuery, selectedRole, selectedStatus, selectedTrashed],
  );

  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: usersListQueryKey(listParams),
    queryFn: () => fetchUsersListPage(listParams),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from. The search lives in component state, not in the grid's
  // `globalFilter`, so the href writes the whole query itself rather than
  // leaving `query` to the grid.
  const rowHref = (row: User) => {
    const search = buildDetailSearch(listParams, listParams.filters);
    return `/user-management/users/${row.id}${search ? `?${search}` : ''}`;
  };

  // A user whose trashing is counting down stays on the list, dimmed, until the
  // window lapses - the toast holds the Cancel, this says which row it is for.
  const pendingKeys = usePendingEntityKeys();
  const rowPending = (row: User) => pendingKeys.has(pendingEntityKey('user', row.id));

  const selectedRowIds = useMemo(() => Object.keys(rowSelection), [rowSelection]);

  const runBulkAction = (action: 'delete' | 'activate' | 'deactivate' | 'permanent_delete' | 'resend_invite') => {
    if (selectedRowIds.length === 0) return;
    setBulkConfirmAction(action);
    setBulkConfirmOpen(true);
  };

  const runBulkActionConfirm = async () => {
    const action = bulkConfirmAction;
    if (!action || selectedRowIds.length === 0) return;
    setBulkActionPending(true);
    try {
      const res = await apiFetch('/api/user-management/users/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_ids: selectedRowIds,
          action: action === 'permanent_delete' ? 'permanent_delete' : action,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.message || 'Bulk action failed');
      toast.success(data.message || 'Done');
      setRowSelection({});
      setBulkConfirmOpen(false);
      setBulkConfirmAction(null);
      queryClient.invalidateQueries({ queryKey: ['user-users'] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Bulk action failed');
    } finally {
      setBulkActionPending(false);
    }
  };

  const bulkConfirmConfig =
    bulkConfirmAction === 'delete'
      ? {
          title: 'Confirm delete',
          description: `Are you sure you want to trash ${selectedRowIds.length} user(s)? They can be restored from the Trashed filter.`,
          actionLabel: 'Trash',
        }
      : bulkConfirmAction === 'resend_invite'
        ? {
            title: 'Confirm resend invitation',
            description: `Are you sure you want to send invitation links to ${selectedRowIds.length} user(s)?`,
            actionLabel: 'Send invitations',
          }
      : bulkConfirmAction === 'permanent_delete'
        ? {
            title: 'Permanently delete users',
            description: `Are you sure you want to permanently delete ${selectedRowIds.length} trashed user(s)? This cannot be undone.`,
            actionLabel: 'Permanently delete',
          }
        : bulkConfirmAction === 'activate'
          ? {
              title: 'Confirm activate',
              description: `Are you sure you want to activate ${selectedRowIds.length} user(s)?`,
              actionLabel: 'Activate',
            }
          : bulkConfirmAction === 'deactivate'
            ? {
                title: 'Confirm deactivate',
                description: `Are you sure you want to deactivate ${selectedRowIds.length} user(s)?`,
                actionLabel: 'Deactivate',
              }
            : { title: '', description: '', actionLabel: '' };

  const columns = useMemo<ColumnDef<User>[]>(
    () => [
      buildSelectColumn<User>({ size: 40 }),
      {
        accessorKey: 'name',
        id: 'name',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="User"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const user = row.original;
          const avatarUrl = user.avatar || null;
          const initials = getInitials(user.name || user.email);

          return (
            <div className="flex items-center gap-3">
              <Avatar className="size-8">
                {avatarUrl && (
                  <AvatarImage src={avatarUrl} alt={user.name || ''} />
                )}
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
              <div className="space-y-px">
                <div className="font-medium text-sm">{user.name}</div>
                <div className="text-muted-foreground text-xs">
                  {user.email}
                </div>
              </div>
            </div>
          );
        },
        size: 300,
        meta: {
          headerTitle: 'Name',
          skeleton: (
            <div className="flex items-center gap-3">
              <Skeleton className="size-8 rounded-full" />
              <div className="space-y-1">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-24" />
              </div>
            </div>
          ),
        },
        enableSorting: true,
        enableHiding: false,
      },
      {
        accessorKey: 'role_name',
        id: 'roles',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Roles"
            visibility={true}
            column={column}
          />
        ),
        size: 180,
        // One row line (owner rule, 29 Sep 2026): as many roles as the column fits, the
        // rest behind "+N", every role in the popover (`PillOverflow`).
        cell: ({ row }) => {
          const roles: { id: string; name: string }[] = row.original.roles ?? [];
          if (!roles.length) return <span className="text-muted-foreground"> - </span>;
          return (
            <PillOverflow
              ariaLabel={`Roles of ${row.original.name || row.original.email}`}
              testId={`user-roles-${row.original.id}`}
              items={roles.map((r) => ({ key: r.id, label: r.name }))}
              renderPopover={(items) => <PillOverflowList items={items} />}
            />
          );
        },
        meta: {
          headerTitle: 'Roles',
          skeleton: <Skeleton className="w-28 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'status',
        id: 'status',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Status"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const statusProps = getUserStatusProps(
            row.original.status as UserStatus,
          );
          const isTrashed = row.original.isTrashed;

          return (
            <div className="inline-flex gap-2.5">
              {/* The pill resolves its own colour and draws its own dot from the
                  raw status (D2); pairing the two by hand is how two lists came
                  to disagree about a colour. */}
              <Badge status={row.original.status}>{statusProps.label}</Badge>
              {isTrashed && (
                <Badge variant="destructive" appearance="light">
                  Trashed
                </Badge>
              )}
            </div>
          );
        },
        size: 125,
        meta: {
          headerTitle: 'Status',
          skeleton: <Skeleton className="w-14 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'dailySlaSummarySubscribed',
        id: 'dailySlaSummarySubscribed',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Conversation Summary"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const user = row.original;
          const userId = user.id;
          const subscribed =
            user.dailySlaSummarySubscribed ??
            user.daily_sla_summary_subscribed ??
            true;
          return (
            <div
              className="flex items-center gap-2"
              onClick={(e) => e.stopPropagation()}
            >
              <Switch
                checked={Boolean(subscribed)}
                aria-label="Daily conversation summary"
                onCheckedChange={(next) =>
                  dailySummarySubscription.mutate({ userId, subscribed: next })
                }
              />
            </div>
          );
        },
        size: 170,
        meta: {
          headerTitle: 'Conversation Summary',
          skeleton: <Skeleton className="w-10 h-6" />,
        },
        enableSorting: false,
        enableHiding: true,
      },
      {
        accessorKey: 'createdAt',
        id: 'createdAt',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Joined"
            visibility={true}
            column={column}
          />
        ),
        cell: (info) => formatDateSafe(info.getValue() as string | null),
        size: 150,
        meta: {
          headerTitle: 'Joined',
          skeleton: <Skeleton className="w-20 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'lastSignInAt',
        id: 'lastSignInAt',
        accessorFn: (row: User & { last_sign_in_at?: string | null }) =>
          row.last_sign_in_at ?? row.lastSignInAt ?? null,
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Last Sign In"
            visibility={true}
            column={column}
          />
        ),
        cell: (info) => {
          const v = info.getValue() as string | null;
          return v ? formatDateTimeInMalaysia(v) : 'Never';
        },
        size: 175,
        meta: {
          headerTitle: 'Last Sign In',
          skeleton: <Skeleton className="w-20 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => <UserRowActions user={row.original as User} />,
        meta: {
          skeleton: <Skeleton className="size-4" />,
        },
        size: 80,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
      },
    ],
    [dailySummarySubscription],
  );

  const [columnOrder, setColumnOrder] = useState<string[]>(() =>
    columns.map((column) => column.id as string),
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row: User) => row.id,
    state: {
      pagination,
      sorting,
      columnOrder,
      rowSelection,
    },
    columnResizeMode: 'onChange',
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

  // The one offer this listing makes, in both places it belongs: the toolbar,
  // and the empty state's next step (S5-06).
  const addUserButton = (
    <Button
      onClick={() => {
        setInviteDialogOpen(true);
      }}
    >
      <Plus />
      Add user
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
        rowHref={rowHref}
        rowPending={rowPending}
        standardToolbar={false}
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
          <UsersToolbar
            table={table}
            searchInput={searchInput}
            setSearchInput={setSearchInput}
            searchSettling={searchSettling}
            searchQuery={searchQuery}
            isFetching={isFetching}
            resetSearch={resetSearch}
            selectedRole={selectedRole}
            setSelectedRole={setSelectedRole}
            selectedStatus={selectedStatus}
            setSelectedStatus={setSelectedStatus}
            selectedTrashed={selectedTrashed}
            setSelectedTrashed={setSelectedTrashed}
            setPagination={setPagination}
            bulkActionPending={bulkActionPending}
            runBulkAction={runBulkAction}
            roleList={roleList}
            roleListError={roleListError}
            refetchRoleList={() => void refetchRoleList()}
            addUserButton={addUserButton}
          />
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>

      <UserInviteDialog
        open={inviteDialogOpen}
        closeDialog={() => setInviteDialogOpen(false)}
      />

      <AlertDialog
        open={bulkConfirmOpen}
        onOpenChange={(open) => {
          if (!open) setBulkConfirmAction(null);
          setBulkConfirmOpen(open);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{bulkConfirmConfig.title}</AlertDialogTitle>
            <AlertDialogDescription>
              {bulkConfirmConfig.description}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={bulkActionPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                runBulkActionConfirm();
              }}
              disabled={bulkActionPending}
              className={
                bulkConfirmAction === 'delete' || bulkConfirmAction === 'permanent_delete'
                  ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90'
                  : undefined
              }
            >
              {bulkActionPending ? (
                <>
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  {bulkConfirmConfig.actionLabel}...
                </>
              ) : (
                bulkConfirmConfig.actionLabel
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

    </>
  );
};

export default UserList;
