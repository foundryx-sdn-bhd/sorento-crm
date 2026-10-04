'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
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
import { Paperclip, Plus, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
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
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { useComplaintRootCausesSelect } from '../../complaint-root-causes/hooks/useComplaintRootCauses';
import { useComplaintResolutionsSelect } from '../../complaint-resolutions/hooks/useComplaintResolutions';
import { Skeleton } from '@/components/ui/skeleton';
import { getUserLookup } from '@/services/userSelectService';
import { useComplaints, useExportComplaintPdf } from '../hooks/useComplaints';
import { complaintStatusPillClass, complaintStatusLabel } from '@/lib/complaint-status';
import type { Complaint } from '../types/complaint.types';
import ComplaintBulkDeleteDialog from './ComplaintBulkDeleteDialog';
import ComplaintDeleteDialog from './ComplaintDeleteDialog';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { complaintActions } from '../actions';
import { EntityDownloadsButton } from '@/components/my-downloads/EntityDownloadsButton';
import { formatDate, formatDateTimeInMalaysia } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function ComplaintsList() {
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'created_at', desc: true },
  ]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const [assignedToFilter, setAssignedToFilter] = useState<string>('__all__');
  const [statusFilter, setStatusFilter] = useState<string>('__all__');
  const [rootCauseFilter, setRootCauseFilter] = useState<string[]>([]);
  const [resolutionFilter, setResolutionFilter] = useState<string[]>([]);

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearchQuery(state.searchQuery);
    setAssignedToFilter(state.filters.assigned_to ?? '__all__');
    setStatusFilter(state.filters.status ?? '__all__');
    setRootCauseFilter(state.filters.root_cause_ids ? state.filters.root_cause_ids.split(',') : []);
    setResolutionFilter(state.filters.resolution_ids ? state.filters.resolution_ids.split(',') : []);
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Complaint | null>(null);
  const exportPdfMutation = useExportComplaintPdf();

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  // Joined, not the arrays: a new array identity each render would read as a change.
  useResetPageOnFilterChange(setPagination, [
    searchQuery,
    assignedToFilter,
    statusFilter,
    rootCauseFilter.join(','),
    resolutionFilter.join(','),
  ]);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useComplaints({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    assigned_to:
      assignedToFilter && assignedToFilter !== '__all__'
        ? assignedToFilter
        : undefined,
    status:
      statusFilter && statusFilter !== '__all__' ? statusFilter : undefined,
    root_cause_ids: rootCauseFilter.length ? rootCauseFilter : undefined,
    resolution_ids: resolutionFilter.length ? resolutionFilter : undefined,
  });

  const {
    data: respondSyncedUsers = [],
    error: respondSyncedUsersError,
    refetch: refetchRespondSyncedUsers,
  } = useQuery({
    queryKey: ['user-lookup', 'respond-synced', 'with-inactive'],
    queryFn: () => getUserLookup({ respond_synced: true, include_inactive: true }),
    staleTime: 60_000,
  });
  const assigneeOptions = respondSyncedUsers.filter((u) => u.respond_user_id);
  // Active-only master data, the same source the complaint form's pickers use.
  const {
    data: rootCauseOptions = [],
    error: rootCauseError,
    refetch: refetchRootCauses,
  } = useComplaintRootCausesSelect();
  const {
    data: resolutionOptions = [],
    error: resolutionError,
    refetch: refetchResolutions,
  } = useComplaintResolutionsSelect();

  const filtersActiveCount =
    (assignedToFilter !== '__all__' ? 1 : 0) +
    (statusFilter !== '__all__' ? 1 : 0) +
    (rootCauseFilter.length ? 1 : 0) +
    (resolutionFilter.length ? 1 : 0);

  // The whole row opens the record, carrying the active list query so the
  // detail page's pager walks the same filtered+sorted page.
  const rowHref = (row: Complaint) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        assigned_to:
          assignedToFilter && assignedToFilter !== '__all__'
            ? assignedToFilter
            : undefined,
        status:
          statusFilter && statusFilter !== '__all__' ? statusFilter : undefined,
        root_cause_ids: rootCauseFilter.length
          ? rootCauseFilter.join(',')
          : undefined,
        resolution_ids: resolutionFilter.length
          ? resolutionFilter.join(',')
          : undefined,
      },
    );
    const qs = search ? `?${search}` : '';
    return `/complaint-management/complaints/${row.id}${qs}`;
  };

  const columns = useMemo<ColumnDef<Complaint>[]>(
    () => [
      buildSelectColumn<Complaint>(),
      {
        accessorKey: 'delivery_order_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="DO Number" column={column} />
        ),
        size: 150,
        cell: ({ row }) => row.original.delivery_order_number || '-',
        meta: { headerTitle: 'DO Number', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'complaint_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Complaint Number" column={column} />
        ),
        size: 170,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.complaint_number || undefined}>
            {row.original.complaint_number || '-'}
          </span>
        ),
        meta: { headerTitle: 'Complaint Number', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'complaint_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Complaint Date" column={column} />
        ),
        cell: ({ row }) =>
          row.original.complaint_date
            ? formatDate(new Date(row.original.complaint_date))
            : '-',
        size: 150,
        meta: { headerTitle: 'Complaint Date', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Created at" column={column} />
        ),
        cell: ({ row }) =>
          row.original.created_at
            ? formatDateTimeInMalaysia(row.original.created_at)
            : '-',
        size: 160,
        meta: { headerTitle: 'Created at', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Customer Name" column={column} />
        ),
        cell: ({ row }) => row.original.customer_name || '-',
        size: 200,
        meta: { headerTitle: 'Customer Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'product_code',
        header: ({ column }) => (
          <DataGridColumnHeader title="Product Code" column={column} />
        ),
        cell: ({ row }) => row.original.product_code || '-',
        size: 150,
        meta: { headerTitle: 'Product Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'complaint_type',
        header: ({ column }) => (
          <DataGridColumnHeader title="Complaint Type" column={column} />
        ),
        cell: ({ row }) => {
          const type = row.original.complaint_type;
          return type ? (
            <Badge variant="secondary">{type}</Badge>
          ) : (
            '-'
          );
        },
        size: 150,
        meta: { headerTitle: 'Complaint Type', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'project_title',
        header: ({ column }) => (
          <DataGridColumnHeader title="Project Title" column={column} />
        ),
        cell: ({ row }) => row.original.project_title || '-',
        size: 200,
        meta: { headerTitle: 'Project Title', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'salesperson',
        header: ({ column }) => (
          <DataGridColumnHeader title="Salesperson" column={column} />
        ),
        cell: ({ row }) => row.original.salesperson || '-',
        size: 160,
        meta: { headerTitle: 'Salesperson', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        size: 120,
        cell: ({ row }) => {
          const status = row.original.status;
          if (!status) return '-';
          return (
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${complaintStatusPillClass(status)}`}
            >
              {complaintStatusLabel(status)}
            </span>
          );
        },
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'assigned_to',
        header: ({ column }) => (
          <DataGridColumnHeader title="Assigned To" column={column} />
        ),
        cell: ({ row }) =>
          row.original.assigned_to_name ?? row.original.assigned_to ?? '-',
        size: 160,
        meta: { headerTitle: 'Assigned To', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'handled_by_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Handled By" column={column} />
        ),
        cell: ({ row }) => (
          <span className="truncate" title={row.original.handled_by_name ?? undefined}>
            {row.original.handled_by_name ?? '-'}
          </span>
        ),
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'Handled By', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'attachments',
        header: ({ column }) => (
          <DataGridColumnHeader title="Attachments" column={column} />
        ),
        cell: ({ row }) => {
          const attachmentCount = row.original.attachments?.length || 0;
          return attachmentCount > 0 ? (
            <div className="flex items-center gap-1">
              <Paperclip className="size-3 text-muted-foreground" />
              <span className="text-sm">{attachmentCount}</span>
            </div>
          ) : (
            '-'
          );
        },
        size: 100,
        meta: { headerTitle: 'Attachments', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'print_count',
        header: ({ column }) => (
          <DataGridColumnHeader title="Print Count" column={column} />
        ),
        cell: ({ row }) => (
          <EntityDownloadsButton
            entityType="complaint"
            entityId={row.original.id}
            label={row.original.complaint_number ?? undefined}
            count={row.original.print_count ?? 0}
          />
        ),
        size: 110,
        meta: { headerTitle: 'Print Count', skeleton: <Skeleton className="h-4 w-12" /> },
      },
      {
        id: 'actions',
        header: '',
        size: 56,
        enableSorting: false,
        enableHiding: false,
        cell: ({ row }) => (
          // The entity's own set, in the row's "..." (D15). Declared once in
          // `../actions`, so the record's gear renders the same items, in the same
          // order, behind the same delete gate.
          <RowActionsMenu
            actions={complaintActions(row.original, {
              isExporting: exportPdfMutation.isPending,
              onExport: (id) => exportPdfMutation.mutate(id),
              onDeleteRequested: () => setDeleteTarget(row.original),
            })}
            ariaLabel="complaint"
          />
        ),
      },
    ],
    // The mutation OBJECT is new on every render, so depending on it would rebuild
    // every column each time and remount the open menu out from under a click.
    // `mutate` is stable; `isPending` is the only part that changes.
    [exportPdfMutation.mutate, exportPdfMutation.isPending],
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
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() =>
        router.push('/complaint-management/complaints/new')
      }
    >
      <Plus />
      Create Complaint
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      error={error}
      onRetry={() => void refetch()}
      rowHref={rowHref}
      standardToolbar={false}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
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
                placeholder="Search complaints..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: filtersActiveCount > 0,
              activeCount: filtersActiveCount,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Assigned to</Label>
                    <SearchableSelect
                      value={assignedToFilter}
                      onChange={setAssignedToFilter}
                      loadError={respondSyncedUsersError}
                      onRetry={() => void refetchRespondSyncedUsers()}
                      options={[
                        { value: '__all__', label: 'All assignees' },
                        { value: '__unassigned__', label: 'Unassigned' },
                        ...assigneeOptions.map((u) => ({
                          value: u.respond_user_id!,
                          label: u.name || 'Unnamed user',
                        })),
                      ]}
                      placeholder="All assignees"
                      triggerClassName="mt-1"
                    />
                  </div>
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter}
                      onChange={setStatusFilter}
                      options={[
                        { value: '__all__', label: 'All statuses' },
                        { value: 'new', label: 'New' },
                        { value: 'updated', label: 'Updated' },
                        { value: 'responded', label: 'Responded' },
                        { value: 'approved', label: 'Approved' },
                        { value: 'rejected', label: 'Rejected' },
                        { value: 'settled_on_site', label: 'Settled on site' },
                      ]}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  <div>
                    <Label>Root cause</Label>
                    <SearchableMultiSelect
                      value={rootCauseFilter}
                      onChange={setRootCauseFilter}
                      loadError={rootCauseError}
                      onRetry={() => void refetchRootCauses()}
                      options={rootCauseOptions.map((rc) => ({
                        value: rc.id,
                        label: rc.name,
                      }))}
                      placeholder="All root causes"
                      triggerClassName="mt-1"
                    />
                  </div>
                  <div>
                    <Label>Resolution</Label>
                    <SearchableMultiSelect
                      value={resolutionFilter}
                      onChange={setResolutionFilter}
                      loadError={resolutionError}
                      onRetry={() => void refetchResolutions()}
                      options={resolutionOptions.map((r) => ({
                        value: r.id,
                        label: r.name,
                      }))}
                      placeholder="All resolutions"
                      triggerClassName="mt-1"
                    />
                  </div>
                  {filtersActiveCount > 0 && (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setAssignedToFilter('__all__');
                          setStatusFilter('__all__');
                          setRootCauseFilter([]);
                          setResolutionFilter([]);
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'complaints_export.xlsx' }}
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
      {deleteTarget && (
        <ComplaintDeleteDialog
          open
          closeDialog={() => setDeleteTarget(null)}
          complaint={deleteTarget}
          onSuccess={() => setDeleteTarget(null)}
        />
      )}

      <ComplaintBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={setBulkDeleteDialogOpen}
        complaintIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
    </DataGrid>
  );
}
