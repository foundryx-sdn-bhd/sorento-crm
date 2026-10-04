'use client';

import { useEffect, useMemo, useState } from 'react';
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
import { FolderMinus, FolderPlus, Plus, Upload, UserCog } from 'lucide-react';
import { Badge, BadgeDot } from '@/components/ui/badge';
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
import { useImportJobDrawer } from '@/components/upload-activity';
import { useCustomers } from '../hooks/useCustomers';
import { searchCustomerGroupsSelect } from '../../customer-groups/services/customerGroupService';
import { buildDetailSearch } from '@/lib/listNavQuery';
import type { Customer } from '../types/customer.types';
import { CustomerRowActions } from '../actions';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { useHasPermission } from '@/hooks/usePermissions';
import { BulkSetCustomerGroupDialog } from './BulkSetCustomerGroupDialog';
import { BulkSetSalesAgentDialog } from './BulkSetSalesAgentDialog';
import { CustomerImportDialog } from './CustomerImportDialog';
import {
  importCustomers,
  validateCustomerImport,
} from '../services/customerImportService';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function CustomersList() {
  const router = useRouter();
  const { notifyImportQueued } = useImportJobDrawer();
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [groupFilter, setGroupFilter] = useState<{ id: string; name: string } | null>(null);

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(state.filters.status ?? 'all');
    // The URL carries the id only; the name is re-read once the filter popover opens.
    setGroupFilter((prev) =>
      state.filters.customer_group_id
        ? { id: state.filters.customer_group_id, name: prev?.name ?? 'Selected group' }
        : null,
    );
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const canSetSalesAgent = useHasPermission('master_data.sales_agents.edit');
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  const canEditCustomers = useHasPermission('order_management.customers.edit');
  const [groupDialogOpen, setGroupDialogOpen] = useState(false);
  // Remove from group asks nothing (D7): a countdown with Cancel, the selection clears
  // once the removals have settled.
  const removeFromGroup = useDeferredBulkAction({
    actionKey: 'customer.remove_from_group',
    entityType: 'customer',
    verb: 'Removing',
    pastVerb: 'removed from their group',
    describe: (count) => `${count} customer${count === 1 ? '' : 's'}`,
    invalidateKeys: [['customers'], ['customer-groups']],
    onFinished: () => setRowSelection({}),
  });

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useCustomers({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter === 'all' ? undefined : statusFilter,
    customer_group_id: groupFilter?.id,
  });

  // Reset selection whenever the result set changes.
  useEffect(() => {
    setRowSelection({});
  }, [searchQuery, statusFilter, groupFilter, pagination.pageIndex, pagination.pageSize, sorting]);

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [searchQuery]);

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from - the status filter included, or the pager would page a wider
  // set than the user was looking at.
  const rowHref = (row: Customer) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        status: statusFilter === 'all' ? undefined : statusFilter,
        customer_group_id: groupFilter?.id,
      },
    );
    const qs = search ? `?${search}` : '';
    return `/order-management/customers/${row.id}${qs}`;
  };

  const columns = useMemo<ColumnDef<Customer>[]>(
    () => [
      buildSelectColumn<Customer>(),
      {
        accessorKey: 'customer_code',
        header: ({ column }) => <DataGridColumnHeader title="Customer Code" column={column} />,
        size: 150,
        meta: { headerTitle: 'Customer Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => <DataGridColumnHeader title="Customer Name" column={column} />,
        size: 250,
        meta: { headerTitle: 'Customer Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'email',
        header: ({ column }) => <DataGridColumnHeader title="Email" column={column} />,
        size: 200,
        meta: { headerTitle: 'Email', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'phone_number',
        header: ({ column }) => <DataGridColumnHeader title="Phone" column={column} />,
        size: 150,
        meta: { headerTitle: 'Phone', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'sales_agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Sales Agent" column={column} />,
        // Not in `_CUSTOMER_SORT_MAP` (backend) - it is a joined column and no other joined
        // column on this list is sortable either, so this one is not the first to be.
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
        meta: { headerTitle: 'Sales Agent', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'customer_group_name',
        header: ({ column }) => <DataGridColumnHeader title="Group" column={column} />,
        // A joined column: not in the backend sort map, so not sortable.
        enableSorting: false,
        cell: ({ row }) =>
          row.original.customer_group_name ? (
            <span className="block truncate" title={row.original.customer_group_name}>
              {row.original.customer_group_name}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 200,
        meta: { headerTitle: 'Group', skeleton: <Skeleton className="h-4 w-28" /> },
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
        size: 100,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => <CustomerRowActions customer={row.original} />,
        size: 60,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
      },
    ],
    [],
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
    // DataGrid rule (CLAUDE.md): a resizable grid needs its column resize mode set, or a
    // drag never commits. This grid predates the rule; the column this PR adds is what
    // surfaces it.
    columnResizeMode: 'onChange',
  });

  // Remove only touches the selected customers that sit in a group, so that is its N.
  const groupedCount = table
    .getSelectedRowModel()
    .rows.filter((r) => r.original.customer_group_id).length;

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/order-management/customers/new')}>
      <Plus />
      Create Customer
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
      tableLayout={{ columnsVisibility: true }}
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
                placeholder="Search customers..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: statusFilter !== 'all' || groupFilter !== null,
              activeCount: (statusFilter !== 'all' ? 1 : 0) + (groupFilter ? 1 : 0),
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter}
                      onChange={(v) => {
                        setStatusFilter(v);
                        setPagination((p) => ({ ...p, pageIndex: 0 }));
                      }}
                      options={[
                        { value: 'all', label: 'All statuses' },
                        { value: 'active', label: 'Active' },
                        { value: 'inactive', label: 'Inactive' },
                      ]}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  <div>
                    <Label>Group</Label>
                    <SearchableSelect
                      aria-label="Group"
                      value={groupFilter?.id ?? ''}
                      onChange={(v) => {
                        if (!v) setGroupFilter(null);
                        setPagination((p) => ({ ...p, pageIndex: 0 }));
                      }}
                      onOptionChange={(opt) =>
                        setGroupFilter(opt ? { id: opt.value, name: opt.label } : null)
                      }
                      fetchOptions={async (query) =>
                        (await searchCustomerGroupsSelect(query)).map((g) => ({
                          value: g.id,
                          label: g.name,
                          description: `${g.ledger_count} ${g.ledger_count === 1 ? 'ledger' : 'ledgers'}`,
                        }))
                      }
                      selectedOption={
                        groupFilter ? { value: groupFilter.id, label: groupFilter.name } : undefined
                      }
                      placeholder="All groups"
                      emptyMessage="No groups match."
                      triggerClassName="mt-1"
                      clearable
                    />
                  </div>
                  {(statusFilter !== 'all' || groupFilter) && (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setStatusFilter('all');
                          setGroupFilter(null);
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'customers_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            secondaryActions={[
              {
                key: 'import',
                label: 'Import',
                icon: Upload,
                onClick: () => setImportDialogOpen(true),
              },
            ]}
            primaryAction={listPrimaryAction}
            bulkActions={[
              ...(canSetSalesAgent
                ? [
                    {
                      key: 'set-sales-agent',
                      label: `Set sales agent (${selectedRowIds(table).length})`,
                      icon: UserCog,
                      onClick: () => setAgentDialogOpen(true),
                    },
                  ]
                : []),
              ...(canEditCustomers
                ? [
                    {
                      key: 'set-customer-group',
                      label: `Set customer group (${selectedRowIds(table).length})`,
                      icon: FolderPlus,
                      onClick: () => setGroupDialogOpen(true),
                    },
                    {
                      key: 'remove-from-group',
                      label: `Remove from group (${groupedCount})`,
                      icon: FolderMinus,
                      disabled: removeFromGroup.isStarting || groupedCount === 0,
                      onClick: () =>
                        removeFromGroup.run(
                          table
                            .getSelectedRowModel()
                            .rows.filter((r) => r.original.customer_group_id)
                            .map((r) => ({
                              id: r.id,
                              payload: { customer_group_id: r.original.customer_group_id },
                            })),
                        ),
                    },
                  ]
                : []),
            ]}
          />
        </CardHeader>
        <BulkSetSalesAgentDialog
          open={agentDialogOpen}
          onOpenChange={setAgentDialogOpen}
          customerIds={selectedRowIds(table)}
          onDone={() => setRowSelection({})}
        />
        <BulkSetCustomerGroupDialog
          open={groupDialogOpen}
          onOpenChange={setGroupDialogOpen}
          customerIds={selectedRowIds(table)}
          onDone={() => setRowSelection({})}
        />
        <CustomerImportDialog
          open={importDialogOpen}
          onOpenChange={setImportDialogOpen}
          onTest={validateCustomerImport}
          onUpload={async (file) => {
            const queued = await importCustomers(file);
            // Opens the upload drawer and refetches the feed; the job then drives its
            // own 5s polling until terminal, so the user can leave the page.
            notifyImportQueued();
            void refetch();
            return queued;
          }}
        />
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}
