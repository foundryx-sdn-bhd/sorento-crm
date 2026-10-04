'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  type Column,
  type ColumnDef,
  type Row,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { BarChart3, Plus, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn, selectedRowIds } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import LookupBoundLabel from '@/components/common/LookupBoundLabel';
import { useQuery } from '@tanstack/react-query';
import { useHasPermission } from '@/hooks/usePermissions';
import { usePurchaseRequests } from '../hooks/usePurchaseRequests';
import { getUserLookup } from '@/services/userSelectService';
import type { PurchaseRequest } from '../types/purchaseRequest.types';
import { formatDate, formatDateTimeInMalaysia } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { revisionBadgeLabel, withRevisionSuffix } from '@/lib/document-number';
import PurchaseRequestBulkDeleteDialog from './PurchaseRequestBulkDeleteDialog';
import { statusPillClass, STATUS_PILL_BASE } from '@/lib/status-pill';
import { purchaseRequestNumberFieldLabel } from '../lib/purchase-request-field-labels';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';

const REQUEST_TYPE_LABELS: Record<string, string> = {
  purchase_request: 'Purchase Request',
  sponsorship_form: 'Sponsorship Form',
};

/** API value -> display label for status filter */
const STATUS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'pending', label: 'Pending approval' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
];

/** Display status: draft → pending_approval → approved (or rejected). Resend sets back to pending_approval. */
function getDisplayStatus(row: PurchaseRequest): string {
  // Terminal CS lifecycle states win over the approval decision so the list
  // tallies with the detail page and the portal (an approved-then-processed
  // request reads "Processed by CS", not "Approved").
  const s = (row.status ?? '').trim().toLowerCase();
  if (s === 'processed_by_cs') return 'Processed by CS';
  if (s === 'closed') return 'Closed';
  const a = row.approval_status;
  if (a === 'pending') return 'Pending approval';
  if (a === 'approved') return 'Approved';
  if (a === 'rejected') return 'Rejected';
  // No approval decision yet - reflect the lifecycle status so a portal-submitted
  // request shows "Submitted" (tallies with the portal), not "Draft".
  if (s === 'submitted') return 'Submitted';
  return 'Draft';
}

const DEFAULT_BASE_PATH = '/procurement-management/purchase-requests';

/** The single "variable" column between Project Title and Requested By:
 *  Sponsor Subject for sponsorship forms, Purpose for purchase requests
 *  (and the combined/unfiltered list). Honors the fixed-layout rules
 *  (explicit size, truncate + title). */
export function purposeOrSponsorSubjectColumn(
  requestType: 'purchase_request' | 'sponsorship_form' | undefined,
): ColumnDef<PurchaseRequest> {
  if (requestType === 'sponsorship_form') {
    return {
      accessorKey: 'sponsor_subject',
      header: ({ column }: { column: Column<PurchaseRequest> }) => (
        <DataGridColumnHeader title="Sponsor Subject" column={column} />
      ),
      size: 160,
      cell: ({ row }: { row: Row<PurchaseRequest> }) => {
        const value = row.original.sponsor_subject;
        if (!value) return '-';
        const other = row.original.sponsor_subject_other;
        const suffix = value === 'others' && other ? `: ${other}` : '';
        return (
          <span className="truncate" title={`${value}${suffix}`}>
            <LookupBoundLabel
              table="purchase_requests"
              column="sponsor_subject"
              value={value}
              fallback="-"
            />
            {suffix}
          </span>
        );
      },
      meta: { skeleton: <Skeleton className="h-4 w-24" /> },
    };
  }
  return {
    accessorKey: 'purpose',
    header: ({ column }: { column: Column<PurchaseRequest> }) => (
      <DataGridColumnHeader title="Purpose" column={column} />
    ),
    size: 120,
    cell: ({ row }: { row: Row<PurchaseRequest> }) => (
      <span className="truncate" title={row.original.purpose ?? undefined}>
        {row.original.purpose || '-'}
      </span>
    ),
    meta: { skeleton: <Skeleton className="h-4 w-20" /> },
  };
}

interface PurchaseRequestsListProps {
  /** When set, only this type is shown and type filter is hidden (single-type page). */
  requestType?: 'purchase_request' | 'sponsorship_form';
  /** Base path for list, new, and detail links. Defaults to purchase-requests path. */
  basePath?: string;
  /**
   * Permission slug that unlocks a Report action in the toolbar, linking to
   * `<basePath>/report`. Omit and no Report button is rendered at all: a listing whose
   * type has no registered report has nothing to link to (PLAN-reporting-foundation AC-E1).
   */
  reportPermission?: string;
}

export default function PurchaseRequestsList({
  requestType,
  basePath = DEFAULT_BASE_PATH,
  reportPermission,
}: PurchaseRequestsListProps = {}) {
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
    reset: resetSearch,
  } = useDebouncedSearch();
  const [requestTypeFilter, setRequestTypeFilter] = useState<string>(
    requestType ?? 'all',
  );
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [assignedToFilter, setAssignedToFilter] = useState<string>('__all__');

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatusFilter(state.filters.approval_status ?? 'all');
    setAssignedToFilter(state.filters.assigned_to ?? '__all__');
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data: assigneeOptions = [] } = useQuery({
    queryKey: ['user-lookup', 'with-inactive'],
    queryFn: () => getUserLookup({ include_inactive: true }),
    staleTime: 5 * 60_000,
  });
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const canViewReport = useHasPermission(reportPermission ?? '') && Boolean(reportPermission);

  const effectiveRequestType =
    requestType ?? (requestTypeFilter && requestTypeFilter !== 'all' ? requestTypeFilter : undefined);
  const effectiveStatusFilter =
    statusFilter && statusFilter !== 'all' ? statusFilter : undefined;

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = usePurchaseRequests({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    requestType: effectiveRequestType,
    approvalStatus: effectiveStatusFilter,
    assignedTo: assignedToFilter !== '__all__' ? assignedToFilter : undefined,
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [statusFilter, assignedToFilter, searchQuery]);

  // The whole row opens the record, carrying the list query the pager rebuilds its
  // key from. request_type rides along so a PR pager stays in PRs and an SF one in SFs.
  const rowHref = (row: PurchaseRequest) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        request_type: effectiveRequestType,
        approval_status: effectiveStatusFilter,
        assigned_to:
          assignedToFilter && assignedToFilter !== '__all__'
            ? assignedToFilter
            : undefined,
      },
    );
    const qs = search ? `?${search}` : '';
    return `${basePath}/${row.id}${qs}`;
  };

  const bulkDeleteEntityLabel =
    requestType === 'purchase_request'
      ? 'Purchase Request'
      : requestType === 'sponsorship_form'
        ? 'Sponsorship Form'
        : 'record';

  const requestNumberColumnTitle = purchaseRequestNumberFieldLabel(requestType);

  const columns = useMemo<ColumnDef<PurchaseRequest>[]>(
    () => [
      buildSelectColumn<PurchaseRequest>(),
      ...(!requestType
        ? [
            {
              accessorKey: 'request_type',
              header: ({ column }: { column: Column<PurchaseRequest> }) => (
                <DataGridColumnHeader title="Type" column={column} />
              ),
              size: 140,
              cell: ({ row }: { row: Row<PurchaseRequest> }) => {
                const type_ = row.original.request_type;
                const label = REQUEST_TYPE_LABELS[type_] ?? type_;
                return (
                  <Badge variant="secondary" className="capitalize">
                    {label}
                  </Badge>
                );
              },
              meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-24" /> },
            },
          ]
        : []),
      {
        accessorKey: 'request_number',
        header: ({ column }) => (
          <DataGridColumnHeader title={requestNumberColumnTitle} column={column} />
        ),
        cell: ({ row }) => {
          // The `-R{n}` suffix is derived from the denormalized counter, never
          // stored (UAC N2/N3).
          const number = withRevisionSuffix(
            row.original.request_number,
            row.original.revision_no,
          );
          return (
            <span className="truncate font-medium tabular-nums" title={number ?? undefined}>
              {number ?? '-'}
            </span>
          );
        },
        size: 150,
        meta: { headerTitle: requestNumberColumnTitle, skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'revision_no',
        header: ({ column }) => <DataGridColumnHeader title="Rev" column={column} />,
        size: 80,
        enableSorting: false,
        // Denormalized on the row - no per-row query (UAC H4).
        cell: ({ row }) => {
          const label = revisionBadgeLabel(row.original.revision_no);
          if (!label) return <span className="text-muted-foreground"> - </span>;
          return (
            <Badge variant="secondary" title={label}>
              {label}
            </Badge>
          );
        },
        meta: { headerTitle: 'Rev', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      {
        accessorKey: 'submitted_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Submitted date" column={column} />
        ),
        cell: ({ row }) =>
          row.original.submitted_at
            ? formatDate(new Date(row.original.submitted_at))
            : '-',
        size: 140,
        meta: { headerTitle: 'Submitted date', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Created At" column={column} />
        ),
        cell: ({ row }) =>
          row.original.created_at
            ? formatDateTimeInMalaysia(row.original.created_at)
            : '-',
        size: 160,
        meta: { headerTitle: 'Created At', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'approval_status',
        id: 'status',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        size: 140,
        cell: ({ row }) => {
          const status = getDisplayStatus(row.original);
          return (
            <span className={`${STATUS_PILL_BASE} ${statusPillClass(status)}`}>
              {status}
            </span>
          );
        },
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Customer" column={column} />
        ),
        size: 160,
        cell: ({ row }) => row.original.customer_name || '-',
        meta: { headerTitle: 'Customer', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'project_title',
        header: ({ column }) => (
          <DataGridColumnHeader title="Project Title" column={column} />
        ),
        size: 200,
        cell: ({ row }) => row.original.project_title || '-',
        meta: { headerTitle: 'Project Title', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      purposeOrSponsorSubjectColumn(requestType),
      // Sales Type applies to purchase requests only (not sponsorship forms).
      ...(requestType !== 'sponsorship_form'
        ? [
            {
              accessorKey: 'sales_type',
              header: ({ column }: { column: Column<PurchaseRequest> }) => (
                <DataGridColumnHeader title="Sales Type" column={column} />
              ),
              size: 120,
              cell: ({ row }: { row: Row<PurchaseRequest> }) => {
                const value = row.original.sales_type;
                if (!value) return '-';
                return (
                  <span className="truncate" title={value}>
                    <LookupBoundLabel
                      table="purchase_requests"
                      column="sales_type"
                      value={value}
                      fallback="-"
                    />
                  </span>
                );
              },
              meta: { headerTitle: 'Sales Type', skeleton: <Skeleton className="h-4 w-20" /> },
            } as ColumnDef<PurchaseRequest>,
          ]
        : []),
      {
        accessorKey: 'requested_by',
        header: ({ column }) => (
          <DataGridColumnHeader title="Requested By" column={column} />
        ),
        size: 120,
        cell: ({ row }) => row.original.requested_by || '-',
        meta: { headerTitle: 'Requested By', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'assigned_to_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Assigned To" column={column} />
        ),
        size: 140,
        enableSorting: false,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.assigned_to_name ?? undefined}>
            {row.original.assigned_to_name ?? '-'}
          </span>
        ),
        meta: { headerTitle: 'Assigned To', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'handled_by_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Handled By" column={column} />
        ),
        size: 140,
        enableSorting: false,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.handled_by_name ?? undefined}>
            {row.original.handled_by_name ?? '-'}
          </span>
        ),
        meta: { headerTitle: 'Handled By', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: () => null,
        size: 40,
        enableHiding: false,
      },
    ],
    [requestType, requestNumberColumnTitle],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination?.total ?? 0) / pagination.pageSize),
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
  });

  const filtersActiveCount =
    (statusFilter !== 'all' ? 1 : 0) + (assignedToFilter !== '__all__' ? 1 : 0);

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push(`${basePath}/new`)}>
      <Plus />
      Create
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination?.total ?? 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      standardToolbar={false}
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
                placeholder="Search..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: filtersActiveCount > 0,
              activeCount: filtersActiveCount,
              content: (
                <div className="space-y-4">
                  {!requestType && (
                    <div>
                      <Label>Type</Label>
                      <SearchableSelect
                        value={requestTypeFilter}
                        onChange={setRequestTypeFilter}
                        placeholder="Type"
                        triggerClassName="mt-1 w-full"
                        options={[
                          { value: 'all', label: 'All types' },
                          { value: 'purchase_request', label: 'Purchase Request' },
                          { value: 'sponsorship_form', label: 'Sponsorship Form' },
                        ]}
                      />
                    </div>
                  )}
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter}
                      onChange={setStatusFilter}
                      placeholder="Status"
                      triggerClassName="mt-1 w-full"
                      options={STATUS_FILTER_OPTIONS.map((opt) => ({
                        value: opt.value,
                        label: opt.label,
                      }))}
                    />
                  </div>
                  <div>
                    <Label>Assigned to</Label>
                    <SearchableSelect
                      value={assignedToFilter}
                      onChange={setAssignedToFilter}
                      placeholder="Assigned to"
                      triggerClassName="mt-1 w-full"
                      options={[
                        { value: '__all__', label: 'All assignees' },
                        { value: '__unassigned__', label: 'Unassigned' },
                        ...assigneeOptions.map((u) => ({
                          value: u.id,
                          label: u.name?.trim() || 'Unnamed user',
                          searchText: u.name ?? '',
                        })),
                      ]}
                    />
                  </div>
                  {filtersActiveCount > 0 && (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setStatusFilter('all');
                          setAssignedToFilter('__all__');
                          if (!requestType) setRequestTypeFilter('all');
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'purchase_requests_export.xlsx' }}
            secondaryActions={
              canViewReport
                ? [
                    {
                      key: 'report',
                      label: 'Report',
                      icon: BarChart3,
                      href: `${basePath}/report`,
                    },
                  ]
                : []
            }
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
            bulkActions={[
              {
                key: 'delete',
                label: 'Delete',
                icon: Trash2,
                destructive: true,
                onClick: () => setBulkDeleteOpen(true),
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
      <PurchaseRequestBulkDeleteDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => {
          setBulkDeleteOpen(open);
          if (!open) setRowSelection({});
        }}
        ids={selectedRowIds(table)}
        entityLabel={bulkDeleteEntityLabel}
        onSuccess={() => setRowSelection({})}
      />
    </DataGrid>
  );
}
