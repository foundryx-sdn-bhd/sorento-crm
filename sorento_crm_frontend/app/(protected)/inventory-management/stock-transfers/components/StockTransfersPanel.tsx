'use client';

import * as React from 'react';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Check, Truck } from 'lucide-react';
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
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';

import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { getWarehouses } from '@/app/(protected)/inventory-management/warehouses/services/warehouseService';
import { getProductsForLineSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import { useStockTransferMutations, useStockTransfers } from '../hooks/useStockTransfers';
import {
  TRANSFER_KIND_LABEL,
  TRANSFER_STATE_LABEL,
  type StockTransfer,
  type StockTransferKind,
  type StockTransferState,
} from '../types/stockTransfer.types';
import {
  StockTransferActionDialogs,
  availableActions,
  type TransferAction,
} from './StockTransferActions';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { stockTransferActions } from '../actions';
import { distinctName } from '@/lib/product-display';

// Both option lists are derived from the label maps rather than retyped, so a word can only
// ever be changed in one place. The filter and the badge cannot say `moved` two ways.
const STATE_OPTIONS: { value: StockTransferState; label: string }[] = (
  ['proposed', 'approved', 'moved', 'cancelled'] as StockTransferState[]
).map((state) => ({ value: state, label: TRANSFER_STATE_LABEL[state] }));

const KIND_OPTIONS: { value: StockTransferKind; label: string }[] = (
  ['own_group', 'pool', 'borrow'] as StockTransferKind[]
).map((kind) => ({ value: kind, label: TRANSFER_KIND_LABEL[kind] }));

/** Warehouses, server-searched. A code the box does not hold is one the filter cannot set. */
async function fetchWarehouseOptions(query: string) {
  const response = await getWarehouses({
    pageIndex: 0,
    pageSize: 50,
    sorting: [{ id: 'warehouse_code', desc: false }],
    searchQuery: query,
    is_active: true,
  });
  return (response.data ?? []).map((warehouse) => ({
    value: warehouse.id,
    label: warehouse.warehouse_name
      ? `${warehouse.warehouse_code} - ${warehouse.warehouse_name}`
      : warehouse.warehouse_code,
    searchText: `${warehouse.warehouse_code} ${warehouse.warehouse_name ?? ''}`,
  }));
}

/**
 * Products, SERVER-searched, never a capped client-side list.
 *
 * The catalogue runs to tens of thousands of codes, so a select filled from one page would
 * silently hide most of them and the filter would read as broken for anything outside it.
 * `/master-data/products/select` is the endpoint built for exactly this.
 */
async function fetchProductOptions(query: string) {
  const rows = await getProductsForLineSelect(query);
  return rows.map((product) => ({
    value: product.id,
    label: product.product_code,
    description: product.product_name ?? undefined,
    searchText: `${product.product_code} ${product.product_name ?? ''}`,
  }));
}

export function TransferStatePill({ state }: { state: StockTransferState }) {
  const variant =
    state === 'moved'
      ? 'success'
      : state === 'cancelled'
        ? 'secondary'
        : state === 'approved'
          ? 'primary'
          : 'warning';
  const label = TRANSFER_STATE_LABEL[state];
  return (
    // `title` and `truncate`, because "Moved, awaiting stock upload" is longer than a
    // 190px column and a badge cut mid-word reads as a different state.
    <Badge variant={variant} appearance="light" size="sm" title={label}>
      <span className="block truncate">{label}</span>
    </Badge>
  );
}

export interface StockTransfersPanelProps {
  /** Pin the grid to one CORE sales order (the SO detail page's Transfers tab). */
  salesOrderId?: string;
  /** Pin it to one sales agent (the agent detail page's Transfers tab). */
  salesAgentId?: string;
  /** Saved column layout key. A real key, never the route: see `SalesAgentDetail`. */
  listingKey: string;
  /** The full filter bar + bulk approve. Off inside a detail tab, which is already scoped. */
  showFilters?: boolean;
}

/**
 * Every movement a supply decision has asked for (`PLAN-scm-cs-planning-uat.md` section E).
 *
 * ONE grid for the Transfers page, the SO detail tab and the sales-agent detail tab
 * (AC-E6), because a transfer says the same thing wherever it is read. The pinned surfaces
 * pass their filter and drop the filter bar; nothing else differs.
 */
export function StockTransfersPanel({
  salesOrderId,
  salesAgentId,
  listingKey,
  showFilters = true,
}: StockTransfersPanelProps) {
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [state, setState] = React.useState('');
  const [kind, setKind] = React.useState('');
  const [fromWarehouseId, setFromWarehouseId] = React.useState('');
  const [toWarehouseId, setToWarehouseId] = React.useState('');
  const [productId, setProductId] = React.useState('');

  const [sorting, setSorting] = React.useState<SortingState>([
    { id: 'proposed_at', desc: true },
  ]);
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  //
  // Below the state it writes, not above it: the hook applies DURING the render,
  // and a `const` read before its own line throws rather than reading undefined.
  // Declared first, this threw on every arrival from a record and never from the
  // sidebar, where the query string is empty and the callback never runs.
  useListStateFromUrl((urlState) => {
    setPagination({ pageIndex: urlState.pageIndex, pageSize: urlState.pageSize });
    setSorting(urlState.sorting);
    resetSearch(urlState.searchQuery);
    setState(urlState.filters.state ?? '');
    setKind(urlState.filters.kind ?? '');
    setFromWarehouseId(urlState.filters.from_warehouse_id ?? '');
    setToWarehouseId(urlState.filters.to_warehouse_id ?? '');
    setProductId(urlState.filters.product_id ?? '');
  });
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});
  const [acting, setActing] = React.useState<{
    transfer: StockTransfer;
    action: TransferAction;
  } | null>(null);
  const [confirmingBulk, setConfirmingBulk] = React.useState(false);

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [
    debounced,
    state,
    kind,
    fromWarehouseId,
    toWarehouseId,
    productId,
  ]);

  /**
   * A selection belongs to the rows that were on screen when it was made.
   *
   * Cleared whenever the set under it moves - a filter, the search, a sort or a page -
   * because `rowSelection` is keyed by id and survives a refetch: tick three rows, filter
   * to something else, press Approve, and three rows nobody could see would have been
   * approved. `bulkApprove` below also submits only the ids on the CURRENT page, so a
   * stale key that outlived this effect still cannot be acted on.
   */
  React.useEffect(() => {
    setRowSelection({});
  }, [
    debounced,
    state,
    kind,
    fromWarehouseId,
    toWarehouseId,
    productId,
    sorting,
    pagination.pageIndex,
    pagination.pageSize,
  ]);

  const params = React.useMemo(
    () => ({
      query: debounced || undefined,
      state: (state || undefined) as StockTransferState | undefined,
      kind: (kind || undefined) as StockTransferKind | undefined,
      from_warehouse_id: fromWarehouseId || undefined,
      to_warehouse_id: toWarehouseId || undefined,
      product_id: productId || undefined,
      sales_order_id: salesOrderId,
      sales_agent_id: salesAgentId,
      sort: sorting[0]?.id,
      dir: (sorting[0]?.desc ? 'desc' : 'asc') as 'asc' | 'desc',
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
    }),
    [
      debounced,
      state,
      kind,
      fromWarehouseId,
      toWarehouseId,
      productId,
      salesOrderId,
      salesAgentId,
      sorting,
      pagination,
    ],
  );

  const list = useStockTransfers(params);
  const rows = React.useMemo(() => list.data?.data ?? [], [list.data]);
  const total = list.data?.pagination.total ?? 0;
  const filtered = Boolean(debounced || state || kind || fromWarehouseId || toWarehouseId || productId);
  const { bulkApprove } = useStockTransferMutations();

  /**
   * Carried into the record URL so the detail page's prev/next pager walks the SAME
   * searched, sorted, FILTERED page the reader was on.
   *
   * The filters ride along as well as the sort and the search: a reader who narrowed to
   * "proposed, from BRW" and opened the third row expects the chevrons to walk that set,
   * and a pager rebuilt from page and sort alone would step through the unfiltered book
   * instead. Same param names as the list GET, so the detail page forwards them verbatim.
   */
  const detailSearch = React.useMemo(
    () =>
      buildDetailSearch(
        {
          pageIndex: pagination.pageIndex,
          pageSize: pagination.pageSize,
          sorting,
          searchQuery: debounced,
        },
        {
          state,
          kind,
          from_warehouse_id: fromWarehouseId,
          to_warehouse_id: toWarehouseId,
          product_id: productId,
          sales_order_id: salesOrderId,
          sales_agent_id: salesAgentId,
        },
      ),
    [
      pagination.pageIndex,
      pagination.pageSize,
      sorting,
      debounced,
      state,
      kind,
      fromWarehouseId,
      toWarehouseId,
      productId,
      salesOrderId,
      salesAgentId,
    ],
  );

  const columns = React.useMemo<ColumnDef<StockTransfer>[]>(() => {
    const base: ColumnDef<StockTransfer>[] = [];
    if (showFilters) {
      base.push(
        buildSelectColumn<StockTransfer>({
          enableRow: (row) => availableActions(row.original.state).approve,
          disabledReason: (row) =>
            availableActions(row.original.state).approve
              ? undefined
              : `${TRANSFER_STATE_LABEL[row.original.state]} - nothing to approve.`,
          rowLabel: (row) => `Select ${row.original.transfer_no}`,
        }),
      );
    }
    base.push(
      {
        accessorKey: 'transfer_no',
        header: ({ column }) => <DataGridColumnHeader title="Transfer" column={column} />,
        size: 130,
        meta: { headerTitle: 'Transfer' },
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.transfer_no}>
            {row.original.transfer_no}
          </span>
        ),
      },
      {
        accessorKey: 'item_code',
        header: ({ column }) => <DataGridColumnHeader title="Item" column={column} />,
        size: 170,
        meta: { headerTitle: 'Item' },
        cell: ({ row }) => (
          <span
            className="block truncate"
            title={
              distinctName(row.original.item_code, row.original.product_name) ??
              row.original.item_code ??
              undefined
            }
          >
            {row.original.item_code ?? '-'}
          </span>
        ),
      },
      {
        id: 'movement',
        header: ({ column }) => <DataGridColumnHeader title="Move" column={column} />,
        size: 210,
        enableSorting: false,
        meta: { headerTitle: 'Move' },
        cell: ({ row }) => {
          const text = `${row.original.qty} ${row.original.from_location ?? '?'} -> ${
            row.original.to_location ?? '?'
          }`;
          return (
            <span className="block truncate tabular-nums" title={text}>
              {text}
            </span>
          );
        },
      },
      {
        accessorKey: 'kind',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        size: 160,
        meta: { headerTitle: 'Source' },
        cell: ({ row }) => (
          <span className="block truncate" title={TRANSFER_KIND_LABEL[row.original.kind]}>
            {TRANSFER_KIND_LABEL[row.original.kind]}
          </span>
        ),
      },
      {
        accessorKey: 'so_number',
        header: ({ column }) => <DataGridColumnHeader title="Sales order" column={column} />,
        size: 150,
        meta: { headerTitle: 'Sales order' },
        cell: ({ row }) => {
          const text = row.original.so_number
            ? row.original.so_line_no != null
              ? `${row.original.so_number} L${row.original.so_line_no}`
              : row.original.so_number
            : '-';
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
      },
      {
        id: 'customer',
        header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
        size: 190,
        enableSorting: false,
        meta: { headerTitle: 'Customer' },
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_name ?? undefined}>
            {row.original.customer_name ?? '-'}
          </span>
        ),
      },
      {
        id: 'agent',
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        size: 130,
        enableSorting: false,
        meta: { headerTitle: 'Agent' },
        cell: ({ row }) => {
          const text = row.original.agent_name
            ? `${row.original.agent_code ?? ''} · ${row.original.agent_name}`
            : (row.original.agent_code ?? '-');
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
      },
      {
        accessorKey: 'state',
        header: ({ column }) => <DataGridColumnHeader title="State" column={column} />,
        size: 190,
        meta: { headerTitle: 'State' },
        cell: ({ row }) => <TransferStatePill state={row.original.state} />,
      },
      {
        accessorKey: 'proposed_at',
        header: ({ column }) => <DataGridColumnHeader title="Proposed" column={column} />,
        size: 150,
        meta: { headerTitle: 'Proposed' },
        cell: ({ row }) => (
          <span className="block truncate whitespace-nowrap tabular-nums">
            {row.original.proposed_at
              ? formatDateTimeInMalaysia(row.original.proposed_at)
              : '-'}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        size: 56,
        enableSorting: false,
        enableHiding: false,
        cell: ({ row }) => {
          // The record's own set, in the row's "..." (D15). A row has no primary
          // slot, so Approve leads the menu here and is a button on the record.
          const set = stockTransferActions(row.original, (action) =>
            setActing({ transfer: row.original, action }),
          );
          const actions = [...(set.approve ? [set.approve] : []), ...set.actions];
          if (actions.length === 0) return null;
          return <RowActionsMenu actions={actions} ariaLabel="stock transfer" />;
        },
      },
    );
    return base;
  }, [showFilters]);

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: (row) => availableActions(row.original.state).approve,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    manualPagination: true,
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  /** Ticked AND on the page in front of the reader. Never a key left over from another set. */
  const selectedIds = React.useMemo(() => {
    const onPage = new Set(rows.map((row) => row.id));
    return Object.keys(rowSelection).filter((key) => rowSelection[key] && onPage.has(key));
  }, [rowSelection, rows]);

  return (
    <>
      <div className="space-y-4">
        {showFilters ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end">
            <div className="w-full sm:w-44">
              <SearchableSelect
                value={state}
                onChange={setState}
                options={STATE_OPTIONS}
                placeholder="Any state"
                clearable
                size="sm"
                id="transfer-filter-state"
              />
            </div>
            <div className="w-full sm:w-52">
              <SearchableSelect
                value={kind}
                onChange={setKind}
                options={KIND_OPTIONS}
                placeholder="Any source"
                clearable
                size="sm"
                id="transfer-filter-kind"
              />
            </div>
            <div className="w-full sm:w-56">
              <SearchableSelect
                value={fromWarehouseId}
                onChange={setFromWarehouseId}
                fetchOptions={fetchWarehouseOptions}
                placeholder="From any location"
                clearable
                size="sm"
                id="transfer-filter-from"
              />
            </div>
            <div className="w-full sm:w-56">
              <SearchableSelect
                value={toWarehouseId}
                onChange={setToWarehouseId}
                fetchOptions={fetchWarehouseOptions}
                placeholder="To any location"
                clearable
                size="sm"
                id="transfer-filter-to"
              />
            </div>
            <div className="w-full sm:w-60">
              <SearchableSelect
                value={productId}
                onChange={setProductId}
                fetchOptions={fetchProductOptions}
                placeholder="Any item"
                clearable
                size="sm"
                id="transfer-filter-product"
              />
            </div>
          </div>
        ) : null}

        <DataGrid
          table={table}
          recordCount={total}
          isLoading={list.isLoading}
          isPlaceholderData={list.isPlaceholderData}
          listingKey={listingKey}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          rowHref={(row) =>
            `/inventory-management/stock-transfers/${row.id}${
              detailSearch ? `?${detailSearch}` : ''
            }`
          }
          emptyMessage={
            <div className="px-6 py-10 text-center">
              <Truck className="mx-auto size-6 text-muted-foreground" aria-hidden />
              <h3 className="mt-2 text-sm font-semibold">
                {filtered ? 'No transfers match' : 'No stock transfers yet'}
              </h3>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                {filtered
                  ? 'Clear the filters to see every transfer.'
                  : 'Confirming supply from another location raises the movement here.'}
              </p>
            </div>
          }
        >
          <Card>
            <CardHeader className="block">
              <DataGridListToolbar
                table={table}
                exportConfig={false}
                searchSlot={
                  <ListSearchInput
                    value={search}
                    onChange={setSearch}
                    isSettling={isSearchInFlight(debouncedSettling, list.isFetching, debounced)}
                    placeholder="Search by transfer, item, order or location"
                    aria-label="Search stock transfers"
                    className="w-full"
                  />
                }
                bulkActions={
                  showFilters
                    ? [
                        {
                          key: 'approve',
                          label: bulkApprove.isPending ? 'Approving…' : 'Approve',
                          icon: Check,
                          disabled: bulkApprove.isPending || selectedIds.length === 0,
                          // Confirmed first, exactly like the single Approve: telling a
                          // warehouse to carry eleven loads of stock across the country is
                          // not a thing to do on one click.
                          onClick: () => setConfirmingBulk(true),
                        },
                      ]
                    : []
                }
                onRefresh={async () => {
                  await list.refetch();
                }}
                isRefreshing={list.isFetching && !list.isLoading}
              />
            </CardHeader>

            <CardTable>
              {list.isError ? (
                <div className="px-6 py-10 text-center">
                  <h3 className="text-sm font-semibold text-destructive">
                    Stock transfers could not be loaded
                  </h3>
                  <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                    {list.error instanceof Error
                      ? list.error.message
                      : 'Try again in a moment.'}
                  </p>
                </div>
              ) : (
                <DataGridTable />
              )}
            </CardTable>

            {total > pagination.pageSize && (
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            )}
          </Card>
        </DataGrid>
      </div>

      <StockTransferActionDialogs
        transfer={acting?.transfer ?? null}
        action={acting?.action ?? null}
        onClose={() => setActing(null)}
      />

      <AlertDialog
        open={confirmingBulk && selectedIds.length > 0}
        onOpenChange={(next) => !next && setConfirmingBulk(false)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {`Approve ${selectedIds.length} transfer${selectedIds.length === 1 ? '' : 's'}?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {/* The count is in the copy, per the bulk-action standard. */}
              {`${selectedIds.length} stock movement${
                selectedIds.length === 1 ? '' : 's'
              } will be approved for the warehouse to carry out.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={bulkApprove.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={bulkApprove.isPending}
              onClick={(event) => {
                event.preventDefault();
                bulkApprove.mutate(selectedIds, {
                  onSuccess: () => {
                    setRowSelection({});
                    setConfirmingBulk(false);
                  },
                });
              }}
            >
              {bulkApprove.isPending ? 'Approving…' : 'Approve'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export default StockTransfersPanel;
