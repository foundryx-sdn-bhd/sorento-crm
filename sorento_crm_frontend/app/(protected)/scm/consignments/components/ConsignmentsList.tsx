'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { RefreshCw, RotateCcw } from 'lucide-react';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useHasPermission } from '@/hooks/usePermissions';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import {
  isSearchInFlight,
  useDebouncedSearch,
} from '@/hooks/useDebouncedSearch';
import { useListingViewPreferences } from '@/lib/listing-column-preferences/useListingViewPreferences';
import { useSalesOrders } from '../../hooks/useSalesOrders';
import { fmtDate, fmtInt } from '../../lib/format';
import {
  consignmentAtCustomer,
  consignmentStatusLabel,
  consignmentStatusVariant,
} from '../../lib/consignmentStatus';
import type { SalesOrder } from '../../types/scm.types';
import {
  buildPlanActions,
  PLAN_PERMISSION,
} from '../../sales-orders/lib/planActions';
import { ResetPlanningDialog } from '../../sales-orders/components/ResetPlanningDialog';

const DEFAULT_SORTING: SortingState = [{ id: 'order_date', desc: true }];

/** The board's own bound, as on the Sales Orders list. */
const MAX_PLAN_SELECTION = 50;

/** What the backend gates Reset planning on. */
const RESET_PERMISSION = 'scm.reorder.run';

/** Sum of one quantity over the order's lines (the API sends no header total for returns). */
function sumLines(
  order: SalesOrder,
  key: 'qty_ordered' | 'qty_delivered' | 'qty_returned',
) {
  return (order.lines ?? []).reduce(
    (sum, line) => sum + Number(line[key] ?? 0),
    0,
  );
}

/**
 * The Consignment list (AC-CONSIGN-CRM UAC-20): consignment orders only. Built from the Sales
 * Orders list's own pieces (hook, DataGrid, toolbar, status badge, formatters, Plan selected,
 * Reset planning). There is no Add or upload because a consignment only arrives from
 * AutoCount; planning is offered now (CONSIGN-PLAN), the same as a sales order.
 */
export function ConsignmentsList() {
  const pathname = usePathname();
  const router = useRouter();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [resetOpen, setResetOpen] = useState(false);
  const canPlan = useHasPermission(PLAN_PERMISSION);
  const canReset = useHasPermission(RESET_PERMISSION);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const {
    sorting,
    setSorting,
    isLoading: isViewPrefsLoading,
  } = useListingViewPreferences<Record<string, never>>({
    listingKey: pathname,
    defaultSorting: DEFAULT_SORTING,
    filtersVersion: 1,
  });

  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    resetSearchQuery(state.searchQuery);
  });
  useResetPageOnFilterChange(setPagination, [searchQuery]);

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } =
    useSalesOrders({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      // The full shape `consignmentsPagerQuery.listQueryKey` rebuilds from the URL, so the
      // detail pager reads the page this list fetched.
      status: null,
      priority: null,
      source: null,
      dateFrom: null,
      dateTo: null,
      customerCode: null,
      outstanding: false,
      salesAgentId: null,
      demandClass: null,
      transferable: null,
      docType: 'consignment',
      enabled: !isViewPrefsLoading,
    });
  const rows = useMemo<SalesOrder[]>(() => data?.data ?? [], [data]);

  const detailSearch = useMemo(
    () =>
      buildDetailSearch(
        {
          pageIndex: pagination.pageIndex,
          pageSize: pagination.pageSize,
          sorting,
          searchQuery,
        },
        {},
      ),
    [pagination.pageIndex, pagination.pageSize, sorting, searchQuery],
  );
  const detailHref = (order: SalesOrder) =>
    `/scm/consignments/${order.id}${detailSearch ? `?${detailSearch}` : ''}`;

  const columns = useMemo<ColumnDef<SalesOrder>[]>(
    () => [
      buildSelectColumn<SalesOrder>({
        rowLabel: (row) => `Select ${row.original.so_number}`,
      }),
      {
        accessorKey: 'so_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Consignment" column={column} />
        ),
        cell: ({ row }) => (
          <Link
            href={detailHref(row.original)}
            onClick={(e) => e.stopPropagation()}
            className="block truncate font-medium text-primary hover:underline"
            title={row.original.so_number}
          >
            {row.original.so_number}
          </Link>
        ),
        size: 160,
        meta: { headerTitle: 'Consignment' },
      },
      {
        accessorKey: 'order_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Document date" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className="truncate tabular-nums"
            title={fmtDate(row.original.order_date)}
          >
            {fmtDate(row.original.order_date)}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'Document date' },
      },
      {
        accessorKey: 'customer_name',
        // A plain title: the backend has no customer_name sort key, and
        // `DataGridColumnHeader` draws a sort button whatever `enableSorting` says.
        header: () => <span>Customer</span>,
        enableSorting: false,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_name}>
            {row.original.customer_name}
          </span>
        ),
        size: 220,
        meta: { headerTitle: 'Customer' },
      },
      {
        accessorKey: 'status',
        // Worded from the lines (At customer / Fully returned), not the stored status, so the
        // server cannot sort it.
        enableSorting: false,
        header: () => <span>Status</span>,
        cell: ({ row }) => (
          <Badge
            variant={consignmentStatusVariant(row.original)}
            appearance="light"
            size="md"
          >
            {consignmentStatusLabel(row.original)}
          </Badge>
        ),
        size: 150,
        meta: { headerTitle: 'Status' },
      },
      ...(['qty_ordered', 'qty_delivered', 'qty_returned'] as const).map(
        (key): ColumnDef<SalesOrder> => {
          const title =
            key === 'qty_ordered'
              ? 'Qty ordered'
              : key === 'qty_delivered'
                ? 'Qty delivered'
                : 'Qty returned';
          return {
            id: key,
            // Summed from the lines, so the column is shown but not sortable on the server.
            enableSorting: false,
            header: () => <span>{title}</span>,
            cell: ({ row }) => fmtInt(sumLines(row.original, key)),
            size: 130,
            meta: {
              headerTitle: title,
              headerClassName: 'text-right',
              cellClassName: 'text-right tabular-nums',
            },
          };
        },
      ),
      {
        id: 'at_customer',
        enableSorting: false,
        header: () => <span>At customer</span>,
        cell: ({ row }) => fmtInt(consignmentAtCustomer(row.original)),
        size: 130,
        meta: {
          headerTitle: 'At customer',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [detailSearch],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const selectedOrders = table
    .getSelectedRowModel()
    .rows.map((r) => r.original);
  const selectedSoNumbers = selectedOrders.map((o) => o.so_number);
  const planAction = buildPlanActions(
    {
      selectedCount: selectedSoNumbers.length,
      canPlan,
      max: MAX_PLAN_SELECTION,
      noun: 'consignments',
    },
    {
      onPlan: () =>
        router.push(
          `/project-sales/fulfilment-planning?orders=${encodeURIComponent(
            selectedSoNumbers.join(','),
          )}`,
        ),
    },
  )[0];
  const PlanIcon = planAction?.icon;
  const resetActions = canReset
    ? [
        {
          key: 'reset-planning',
          label: `Reset planning (${selectedOrders.length})`,
          icon: RotateCcw,
          onClick: () => setResetOpen(true),
          disabled: selectedOrders.length < 1,
          disabledReason:
            selectedOrders.length < 1
              ? 'Tick the consignments to reset first.'
              : undefined,
        },
      ]
    : [];

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading || isViewPrefsLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        emptyMessage={
          searchQuery
            ? 'No consignment matches this search.'
            : 'No consignments yet.'
        }
        rowHref={(row) => detailHref(row)}
        tableLayout={{
          width: 'fixed',
          columnsResizable: true,
          columnsVisibility: true,
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
                  placeholder="Search number, customer or product..."
                  className="w-64"
                />
              }
              // Kept as the Actions menu even when only Refresh is left (no reset permission).
              secondaryActionsAsMenu
              secondaryActions={[
                ...resetActions,
                {
                  key: 'refresh',
                  label: 'Refresh',
                  icon: RefreshCw,
                  onClick: () => void refetch(),
                },
              ]}
              primaryAction={
                planAction ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      {/* Tooltip on a focusable wrapper: a disabled Button receives no pointer
                        events, the same pattern the Sales Orders list uses. */}
                      <span
                        tabIndex={0}
                        className="inline-flex"
                        data-testid="plan-selected-trigger"
                      >
                        <Button
                          disabled={planAction.disabled}
                          onClick={planAction.onClick}
                        >
                          {PlanIcon ? (
                            <PlanIcon className="size-4" aria-hidden />
                          ) : null}
                          {planAction.label}
                        </Button>
                      </span>
                    </TooltipTrigger>
                    {planAction.disabled && planAction.disabledReason ? (
                      <TooltipContent>
                        {planAction.disabledReason}
                      </TooltipContent>
                    ) : null}
                  </Tooltip>
                ) : null
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
      <ResetPlanningDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        orders={selectedOrders}
        docLabel="consignment"
        offerRewind={false}
        onDone={() => {
          setRowSelection({});
          void refetch();
        }}
      />
    </>
  );
}
