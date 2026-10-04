'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Layers, LoaderCircleIcon, SquarePen, Trash2, UserMinus } from 'lucide-react';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import DetailActions from '@/components/common/DetailActions';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useDeferredRowAction, useRowPending } from '@/hooks/useDeferredRowAction';
import { useHasPermission } from '@/hooks/usePermissions';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { formatDate } from '@/lib/helpers';
import { useCustomerMultiPicker } from '@/app/(protected)/order-management/customers/hooks/useCustomerMultiPicker';
import {
  CUSTOMER_GROUP_CUSTOMERS_PREFIX,
  customerGroupsPagerQuery,
  useAddCustomerGroupCustomers,
  useCustomerGroup,
  useCustomerGroupCustomers,
  useUpdateCustomerGroup,
} from '../../hooks/useCustomerGroups';
import { formatAccountLevels, ledgerCountLabel } from '../../lib/accounts';
import type { GroupLedger } from '../../types/customerGroup.types';

const LIST_PATH = '/order-management/customer-groups';

/** The group record: header card (name, ledger count, accounts, prev/next, Edit, Delete) and
 *  a Ledgers tab. Edit swaps the name for an input in place. */
export default function CustomerGroupDetail({ groupId }: { groupId: string }) {
  const router = useRouter();
  const canEdit = useHasPermission('order_management.customers.edit');
  const canDelete = useHasPermission('order_management.customers.delete');
  const { data: group, isLoading, isError } = useCustomerGroup(groupId);
  const update = useUpdateCustomerGroup();

  const [isEditing, setIsEditing] = useState(false);
  const [name, setName] = useState('');

  // Delete asks nothing (D7): the countdown takes the primary buttons' place.
  const deletion = useDeferredAction({
    actionKey: 'customer_group.delete',
    entityType: 'customer_group',
    entityId: groupId,
    verb: 'Deleting',
    subject: group?.name ?? '',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Customer group deleted',
    invalidateKeys: [['customer-groups'], ['customer-group'], ['customer']],
    onCommitted: () => router.push(LIST_PATH),
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (isError || !group) {
    return (
      <div className="space-y-4">
        <Card className="flex flex-col items-center gap-2 p-10 text-center">
          <div className="text-sm font-semibold">Customer group not found</div>
        </Card>
      </div>
    );
  }

  const accounts = formatAccountLevels(group.account_levels);

  const beginEdit = () => {
    setName(group.name);
    setIsEditing(true);
  };

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await update.mutateAsync({ id: group.id, data: { name: trimmed } });
      setIsEditing(false);
    } catch {
      // The mutation already toasted the reason; the session stays open.
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0 space-y-1">
              {isEditing ? (
                <div className="flex items-center gap-2">
                  <label htmlFor="customer-group-edit-name" className="sr-only">
                    Name
                  </label>
                  <Input
                    id="customer-group-edit-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    maxLength={255}
                    className="h-9 w-full sm:w-96"
                  />
                </div>
              ) : (
                <CardTitle className="min-w-0 break-words text-lg">{group.name}</CardTitle>
              )}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                <span>{ledgerCountLabel(group.ledger_count)}</span>
                {accounts ? <span>{accounts}</span> : null}
                {group.sales_agent_mixed ? (
                  <Badge variant="warning" appearance="light" size="sm">
                    Agent: Mixed
                  </Badge>
                ) : group.sales_agent_label ? (
                  <span>Agent: {group.sales_agent_label}</span>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                {group.created_at ? (
                  <span>Created: {formatDate(new Date(group.created_at))}</span>
                ) : null}
                {group.updated_at ? (
                  <span>Updated: {formatDate(new Date(group.updated_at))}</span>
                ) : null}
              </div>
            </div>
            {isEditing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsEditing(false)}
                  disabled={update.isPending}
                >
                  Cancel
                </Button>
                <Button size="sm" onClick={save} disabled={update.isPending || !name.trim()}>
                  {update.isPending ? (
                    <LoaderCircleIcon className="me-2 size-4 animate-spin" />
                  ) : null}
                  Save
                </Button>
              </div>
            ) : (
              <DetailActions
                pager={{
                  ...customerGroupsPagerQuery,
                  detailPath: LIST_PATH,
                  currentId: groupId,
                  ariaLabel: 'customer group',
                }}
                pendingAction={deletion.countdown}
                primary={
                  <div className="flex items-center gap-2">
                    {canDelete ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-1.5 text-destructive"
                        disabled={deletion.isPending || deletion.isBlocked}
                        onClick={() => deletion.start()}
                      >
                        <Trash2 className="size-4" />
                        Delete
                      </Button>
                    ) : null}
                    {canEdit ? (
                      <Button variant="primary" size="sm" className="gap-1.5" onClick={beginEdit}>
                        <SquarePen className="size-4" />
                        Edit
                      </Button>
                    ) : null}
                  </div>
                }
              />
            )}
          </div>
        </CardHeader>
      </Card>

      <Tabs defaultValue="ledgers" className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="ledgers">
            <Layers />
            <span>Ledgers</span>
            <Badge variant="secondary" appearance="light" size="sm">
              {group.ledger_count}
            </Badge>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="ledgers" className="mt-0 focus-visible:outline-none">
          <LedgersTab groupId={groupId} canEdit={canEdit} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function LedgersTab({ groupId, canEdit }: { groupId: string; canEdit: boolean }) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'customer_code', desc: false }]);
  const {
    value: searchQuery,
    setValue: setSearchQuery,
    debouncedValue: debouncedSearch,
    isSettling: debouncedSearchSettling,
  } = useDebouncedSearch();
  useResetPageOnFilterChange(setPagination, [debouncedSearch, sorting]);

  const { data, isLoading, isPlaceholderData, isError, error, isFetching } =
    useCustomerGroupCustomers(groupId, {
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery: debouncedSearch,
    });
  const add = useAddCustomerGroupCustomers(groupId);
  // A ledger already in THIS group is shown but cannot be ticked; the rest say where they are.
  const picker = useCustomerMultiPicker(
    (option) => option.customerGroupId === groupId,
    (option) => (option.customerGroupName ? `in ${option.customerGroupName}` : 'no group'),
  );

  // Remove asks nothing (D7): the row dims and a toast counts down with Cancel. The payload
  // names the group so the server clears it only while the ledger is still in it.
  const remove = useDeferredRowAction({
    actionKey: 'customer.remove_from_group',
    entityType: 'customer',
    verb: 'Removing',
    successMessage: 'Ledger removed from group',
    invalidateKeys: [
      CUSTOMER_GROUP_CUSTOMERS_PREFIX,
      ['customer-groups'],
      ['customer-group'],
      ['customer'],
    ],
  });
  const { run: runRemove } = remove;
  const rowPending = useRowPending<GroupLedger>('customer');

  const rows = useMemo<GroupLedger[]>(() => data?.data ?? [], [data]);
  const total = data?.pagination.total ?? 0;

  const columns = useMemo<ColumnDef<GroupLedger>[]>(() => {
    const base: ColumnDef<GroupLedger>[] = [
      {
        accessorKey: 'customer_code',
        header: ({ column }) => <DataGridColumnHeader title="Code" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.customer_code}>
            {row.original.customer_code}
          </span>
        ),
        size: 150,
        meta: { headerTitle: 'Code', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_name}>
            {row.original.customer_name}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'sales_agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        enableSorting: false,
        cell: ({ row }) => {
          const { sales_agent_code, sales_agent_name } = row.original;
          const label = sales_agent_code
            ? sales_agent_name
              ? `${sales_agent_code} - ${sales_agent_name}`
              : sales_agent_code
            : null;
          return label ? (
            <span className="block truncate" title={label}>
              {label}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'account_level',
        header: ({ column }) => <DataGridColumnHeader title="Account level" column={column} />,
        enableSorting: false,
        cell: ({ row }) =>
          row.original.account_level ? (
            <Badge variant="info" appearance="light" size="md">
              {`Account ${row.original.account_level}`}
            </Badge>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 150,
        meta: { headerTitle: 'Account level', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.is_active ? 'success' : 'secondary'}>
            <BadgeDot />
            {row.original.is_active ? 'Active' : 'Inactive'}
          </Badge>
        ),
        size: 120,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-14" /> },
      },
    ];
    if (!canEdit) return base;
    return [
      ...base,
      {
        id: 'remove',
        header: () => <span className="sr-only">Remove</span>,
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Remove ${row.original.customer_code}`}
            onClick={(e) => {
              // The row is a link to the customer; this button is not.
              e.preventDefault();
              e.stopPropagation();
              runRemove({
                id: row.original.id,
                subject: row.original.customer_name,
                payload: { customer_group_id: groupId },
              });
            }}
          >
            <UserMinus className="size-4" />
            Remove
          </Button>
        ),
        size: 120,
        enableSorting: false,
        enableResizing: false,
        meta: { headerTitle: 'Remove', skeleton: <Skeleton className="h-6 w-20" /> },
      },
    ];
  }, [canEdit, runRemove, groupId]);

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(total / pagination.pageSize),
    rowCount: total,
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

  return (
    <div className="space-y-3">
      {isError ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {error instanceof Error ? error.message : 'Failed to load ledgers.'}
        </div>
      ) : null}

      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        listingKey="order_management.customers.view::customer_group_ledgers"
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        emptyMessage={
          <div className="py-4 text-center">
            <p className="text-sm font-medium text-foreground">No ledgers in this group</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Add the ledgers that belong together
            </p>
          </div>
        }
        rowHref={(row) => `/order-management/customers/${row.id}`}
        rowPending={rowPending}
      >
        <Card>
          <CardHeader className="block">
            <div className="flex flex-wrap items-center gap-2">
              <ListSearchInput
                value={searchQuery}
                onChange={setSearchQuery}
                isSettling={isSearchInFlight(debouncedSearchSettling, isFetching, debouncedSearch)}
                placeholder="Search ledgers..."
                className="w-full sm:w-64"
              />
              {canEdit ? (
                <>
                  <SearchableMultiSelect
                    value={picker.selected}
                    onChange={picker.setSelected}
                    fetchOptions={picker.fetchOptions}
                    selectedOptions={picker.selectedOptions}
                    placeholder="Select ledgers"
                    emptyMessage="No customers match."
                    disabled={add.isPending}
                    className="w-full sm:w-80"
                  />
                  <Button
                    type="button"
                    disabled={picker.selected.length === 0 || add.isPending}
                    onClick={() =>
                      add.mutate(picker.selected, { onSuccess: () => picker.clear() })
                    }
                  >
                    {picker.selected.length === 0
                      ? 'Add ledgers'
                      : picker.selected.length === 1
                        ? 'Add 1 ledger'
                        : `Add ${picker.selected.length} ledgers`}
                  </Button>
                </>
              ) : null}
            </div>
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>
    </div>
  );
}
