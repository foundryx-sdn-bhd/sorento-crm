'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { FormDialogScaffold } from '@/components/common/FormDialogScaffold';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { formatDate } from '@/lib/helpers';
import { useCreateCustomerGroup, useCustomerGroups } from '../hooks/useCustomerGroups';
import { formatAccountLevels } from '../lib/accounts';
import type { CustomerGroup } from '../types/customerGroup.types';

export default function CustomerGroupsList() {
  const router = useRouter();
  const canEdit = useHasPermission('order_management.customers.edit');
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'name', desc: false }]);
  // 'mixed' or '' (all); the URL carries it as agent_mixed=true so back/next keep the filter.
  const [agentFilter, setAgentFilter] = useState('');
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();

  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setAgentFilter(state.filters.agent_mixed === 'true' ? 'mixed' : '');
  });
  useResetPageOnFilterChange(setPagination, [searchQuery, agentFilter]);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useCustomerGroups({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    agent_mixed: agentFilter === 'mixed',
  });

  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const create = useCreateCustomerGroup();

  const closeAdd = (open: boolean) => {
    setAddOpen(open);
    if (!open) {
      setName('');
      setNameError(null);
      setServerError(null);
    }
  };

  const submitAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError('Name is required');
      return;
    }
    setNameError(null);
    setServerError(null);
    try {
      const group = await create.mutateAsync({ name: trimmed });
      closeAdd(false);
      router.push(`/order-management/customer-groups/${group.id}`);
    } catch (err) {
      // Stays open with the reason inline (a duplicate name is the usual one).
      setServerError(err instanceof Error ? err.message : 'Failed to create customer group');
    }
  };

  const rowHref = (row: CustomerGroup) => {
    const search = buildDetailSearch({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
    }, { agent_mixed: agentFilter === 'mixed' ? 'true' : undefined });
    return `/order-management/customer-groups/${row.id}${search ? `?${search}` : ''}`;
  };

  const columns = useMemo<ColumnDef<CustomerGroup>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Group" column={column} />,
        cell: ({ row }) => (
          <Link
            href={`/order-management/customer-groups/${row.original.id}`}
            onClick={(e) => e.stopPropagation()}
            className="block truncate font-medium text-primary hover:underline"
            title={row.original.name}
          >
            {row.original.name}
          </Link>
        ),
        size: 320,
        meta: { headerTitle: 'Group', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'ledger_count',
        header: ({ column }) => <DataGridColumnHeader title="Ledgers" column={column} />,
        size: 110,
        meta: { headerTitle: 'Ledgers', skeleton: <Skeleton className="h-4 w-8" /> },
      },
      {
        accessorKey: 'account_levels',
        header: ({ column }) => <DataGridColumnHeader title="Accounts" column={column} />,
        enableSorting: false,
        cell: ({ row }) => {
          const label = formatAccountLevels(row.original.account_levels);
          return label ? (
            <span className="block truncate" title={label}>
              {label}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          );
        },
        size: 160,
        meta: { headerTitle: 'Accounts', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'sales_agent',
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        enableSorting: false,
        cell: ({ row }) => {
          const { sales_agent_mixed, sales_agent_label } = row.original;
          if (sales_agent_mixed) {
            return (
              <Badge variant="warning" appearance="light">
                Mixed
              </Badge>
            );
          }
          return sales_agent_label ? (
            <span className="block truncate" title={sales_agent_label}>
              {sales_agent_label}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated" column={column} />,
        cell: ({ row }) =>
          row.original.updated_at ? formatDate(new Date(row.original.updated_at)) : '-',
        size: 140,
        meta: { headerTitle: 'Updated', skeleton: <Skeleton className="h-4 w-20" /> },
      },
    ],
    [],
  );

  const total = data?.pagination.total ?? 0;
  const table = useReactTable({
    columns,
    data: data?.data ?? [],
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
    <>
      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        listingKey="order_management.customers.view::customer_groups"
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        emptyMessage={
          <div className="py-4 text-center">
            <p className="text-sm font-medium text-foreground">No customer groups</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Try a different search, or add a group
            </p>
          </div>
        }
        rowHref={rowHref}
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
                  placeholder="Search groups..."
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: !!agentFilter,
                activeCount: agentFilter ? 1 : 0,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label htmlFor="customer-group-agent-filter">Agent</Label>
                      <SearchableSelect
                        id="customer-group-agent-filter"
                        value={agentFilter}
                        onChange={setAgentFilter}
                        options={[{ value: 'mixed', label: 'Mixed agents only' }]}
                        placeholder="All agents"
                        triggerClassName="mt-1"
                        clearable
                      />
                    </div>
                  </div>
                ),
              }}
              onRefresh={() => void refetch()}
              isRefreshing={isFetching && !isLoading}
              primaryAction={
                canEdit ? (
                  <Button onClick={() => setAddOpen(true)}>
                    <Plus />
                    Add group
                  </Button>
                ) : undefined
              }
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

      <FormDialogScaffold
        open={addOpen}
        onOpenChange={closeAdd}
        title="Add customer group"
        onSubmit={submitAdd}
        submitLabel="Create group"
        isPending={create.isPending}
        error={serverError}
      >
        <div className="space-y-1.5">
          <Label htmlFor="customer-group-name">Name</Label>
          <Input
            id="customer-group-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={255}
            autoFocus
          />
          {nameError ? <p className="text-sm text-destructive">{nameError}</p> : null}
        </div>
      </FormDialogScaffold>
    </>
  );
}
