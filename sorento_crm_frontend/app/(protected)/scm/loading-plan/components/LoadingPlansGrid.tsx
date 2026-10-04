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
import { Upload } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useRowPending } from '@/hooks/useDeferredRowAction';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { EM_DASH, fmtInt, fmtOpens, fmtTrimmedDecimal } from '../../lib/format';
import { describeWindow } from '../../reorder/lib/runListing';
import { useLoadingPlanList } from '../../hooks/useFulfilment';
import {
  type LoadingPlanRecord,
  type LoadingPlanStatus,
} from '../../services/fulfilmentService';
import { LoadingPlanRowActions } from '../actions';
import { PlanContainerDialog } from './PlanContainerDialog';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

/**
 * The loading plans list (R3).
 *
 * A container plan is a record now, not a browser tab's state, so this screen is the same
 * shape every other listing in the system has: a DataGrid whose whole row opens the record,
 * server-side paging and search, and ONE primary action.
 *
 * That one action is **Upload** (R4, AC-A3). Starting a plan and handing over the supplier's
 * document are the same errand - "plan this supplier's next container, here is what they sent
 * me" - so there is one button for it, and the two Upload buttons the old single page carried
 * (header card and empty state) are gone with the page.
 */

const STATUS_LABEL: Record<LoadingPlanStatus, string> = {
  planning: 'Planning',
  sent: 'Sent',
  cancelled: 'Cancelled',
};

const STATUS_VARIANT: Record<LoadingPlanStatus, 'warning' | 'primary' | 'secondary'> = {
  planning: 'warning',
  sent: 'primary',
  cancelled: 'secondary',
};

/** The filter chip's own vocabulary. "Active" is the default because a cancelled plan is a
 *  decision already made, and a list that opens on it hides the work in front of somebody. */
const STATUS_OPTIONS = [
  { value: 'active', label: 'Active (planning and sent)' },
  { value: 'planning', label: 'Planning' },
  { value: 'sent', label: 'Sent' },
  { value: 'cancelled', label: 'Cancelled' },
];

export function LoadingPlansGrid() {
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'started_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInputRaw,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const setSearchInput = (next: string) => {
    setSearchInputRaw(next);
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };
  const [status, setStatus] = useState<LoadingPlanStatus | 'active'>('active');
  const [uploadOpen, setUploadOpen] = useState(false);
  const rowPending = useRowPending<LoadingPlanRecord>('loading_plan');

  const list = useLoadingPlanList({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status,
  });

  const columns = useMemo<ColumnDef<LoadingPlanRecord>[]>(
    () => [
      {
        id: 'started_at',
        accessorFn: (row) => row.started_at,
        header: ({ column }) => <DataGridColumnHeader title="Started" visibility column={column} />,
        cell: ({ row }) => (
          <span className="tabular-nums">
            {formatDateTimeInMalaysia(row.original.started_at)}
          </span>
        ),
        size: 170,
        enableSorting: true,
        meta: { headerTitle: 'Started', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'supplier_name',
        accessorFn: (row) => row.supplier_name ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Supplier" visibility column={column} />
        ),
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.supplier_name ?? ''}>
            {row.original.supplier_name ?? EM_DASH}
          </span>
        ),
        size: 260,
        enableSorting: true,
        meta: { headerTitle: 'Supplier', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        // AC-N7: both ends of the window, the same wording the record's own header and
        // subtitle use (`describeWindow`), so this list never states the end alone while a
        // plan also carries a start.
        id: 'plan_horizon_date',
        // Sorts on the END date only - same shape as ReorderRunsGrid's own window column;
        // the start has no independent order worth a second sort key.
        accessorFn: (row) => row.plan_horizon_date ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Sales orders needed" visibility column={column} />
        ),
        cell: ({ row }) => {
          const text = describeWindow(
            row.original.plan_horizon_start, row.original.plan_horizon_date,
          );
          return text === 'every open order' ? (
            <span className="text-muted-foreground" title="Every open order counted">
              {text}
            </span>
          ) : (
            <span className="tabular-nums">{text}</span>
          );
        },
        size: 190,
        enableSorting: true,
        meta: { headerTitle: 'Sales orders needed', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'document_label',
        accessorFn: (row) => row.document_label,
        header: ({ column }) => <DataGridColumnHeader title="Document" visibility column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.document_label}>
            {row.original.document_label}
          </span>
        ),
        size: 180,
        enableSorting: false,
        meta: { headerTitle: 'Document', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'to_request_qty',
        accessorFn: (row) => row.to_request_qty ?? -1,
        header: ({ column }) => (
          <DataGridColumnHeader title="To request" visibility column={column} />
        ),
        // Two lines, because the buyer asks two questions of one figure: how many pieces, and
        // whether it fits a container. A plan nobody has opened yet has neither, and says so
        // rather than printing a zero that reads as "nothing to ask for".
        cell: ({ row }) =>
          row.original.to_request_qty === null ? (
            <span className="text-muted-foreground" title="Not built yet">
              {EM_DASH}
            </span>
          ) : (
            <div className="flex flex-col text-end">
              <span className="tabular-nums">{fmtInt(row.original.to_request_qty)}</span>
              {row.original.to_request_cbm !== null ? (
                <span className="text-2xs text-muted-foreground tabular-nums">
                  est. {fmtTrimmedDecimal(row.original.to_request_cbm, 1)} cbm
                </span>
              ) : null}
            </div>
          ),
        size: 130,
        enableSorting: true,
        meta: { headerTitle: 'To request', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        id: 'sent_at',
        accessorFn: (row) => row.sent_at ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Sent" visibility column={column} />,
        cell: ({ row }) =>
          row.original.sent_at ? (
            <span className="block truncate tabular-nums" title={row.original.sent_channel ?? ''}>
              {row.original.sent_channel === 'chat' ? 'Chat' : 'Email'}{' '}
              {formatDateTimeInMalaysia(row.original.sent_at)}
            </span>
          ) : (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ),
        size: 170,
        enableSorting: true,
        meta: { headerTitle: 'Sent', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'opened_at',
        accessorFn: (row) => row.last_opened_at ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Opened" visibility column={column} />,
        // Two facts, one cell (AC-C8): how many times the supplier opened the link, and when
        // they last did. A plan they have never opened says so in words - a dash there reads
        // as "we do not know", and since S3 we do.
        cell: ({ row }) =>
          row.original.open_count > 0 ? (
            <div className="flex flex-col">
              <span className="truncate tabular-nums" title={fmtOpens(row.original.open_count, row.original.last_opened_at)}>
                {row.original.last_opened_at
                  ? formatDateTimeInMalaysia(row.original.last_opened_at)
                  : EM_DASH}
              </span>
              <span className="text-2xs text-muted-foreground">
                {fmtInt(row.original.open_count)}{' '}
                {row.original.open_count === 1 ? 'time' : 'times'}
              </span>
            </div>
          ) : (
            <span className="text-2xs text-muted-foreground">Not opened yet</span>
          ),
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Opened', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'status',
        accessorFn: (row) => row.status,
        header: ({ column }) => <DataGridColumnHeader title="Status" visibility column={column} />,
        cell: ({ row }) => (
          <Badge variant={STATUS_VARIANT[row.original.status]} appearance="light" size="sm">
            {STATUS_LABEL[row.original.status]}
          </Badge>
        ),
        size: 110,
        enableSorting: true,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-5 w-16" /> },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => <LoadingPlanRowActions plan={row.original} />,
        size: 60,
        enableHiding: false,
        enableSorting: false,
      },
    ],
    [],
  );

  const rows = list.data?.data ?? [];
  const total = list.data?.total ?? 0;

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(total / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => setUploadOpen(true)} data-testid="open-plan-container">
      <Upload className="size-4" />
      Upload
    </Button>
  );

  return (
    <>
      <DataGrid
        table={table}
        recordCount={total}
        isLoading={list.isLoading}
        error={list.error}
        onRetry={() => void list.refetch()}
        isPlaceholderData={list.isPlaceholderData}
        emptyMessage={
          searchQuery || status !== 'active'
            ? 'No plan matches this search and filter.'
            : 'No container plans yet. Upload a supplier stock list or proforma invoice to start one.'
        }
        onRowClick={(row) => router.push(`/scm/loading-plan/${row.id}`)}
        rowPending={rowPending}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        listingKey="scm.dashboard.view::loading-plans"
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              primaryAction={listPrimaryAction}
              filters={{
                kind: 'custom',
                active: status !== 'active',
                activeCount: status !== 'active' ? 1 : 0,
                activeSummary:
                  status !== 'active'
                    ? {
                        label: `Status: ${STATUS_LABEL[status]}`,
                        onClear: () => setStatus('active'),
                      }
                    : undefined,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label htmlFor="plan-status-filter" className="mb-1 block">
                        Status
                      </Label>
                      <SearchableSelect
                        id="plan-status-filter"
                        value={status}
                        onChange={(v) => {
                          setStatus((v || 'active') as LoadingPlanStatus | 'active');
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
                        }}
                        options={STATUS_OPTIONS}
                        placeholder="Active (planning and sent)"
                      />
                    </div>
                  </div>
                ),
              }}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  isSettling={isSearchInFlight(searchSettling, list.isFetching, searchQuery)}
                  placeholder="Search plans by supplier"
                  className="w-full sm:w-64"
                />
              }
              onRefresh={() => void list.refetch()}
              isRefreshing={list.isFetching}
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

      <PlanContainerDialog open={uploadOpen} onOpenChange={setUploadOpen} />
    </>
  );
}

export default LoadingPlansGrid;
