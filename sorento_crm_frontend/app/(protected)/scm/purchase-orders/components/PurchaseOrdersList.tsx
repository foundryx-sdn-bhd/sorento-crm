'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { buildDetailSearch } from '@/lib/listNavQuery';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { toast } from '@/lib/toast';
import { LoaderCircle, RefreshCw, Upload } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useSearchParams } from 'next/navigation';
import { usePurchaseOrders } from '../../hooks/usePurchaseOrders';
import { usePurchaseOrderActions } from '../../hooks/usePurchaseOrderActions';
import { ConfirmActionDialog } from '../../components/ConfirmActionDialog';
import { BulkActionsMenu } from '../../components/BulkActionsMenu';
import { OutstandingUploadDialog } from '../../reorder/components/OutstandingUploadDialog';
import { buildPoBulkActions } from '../lib/poBulkActions';
import { EM_DASH, fmtDate, fmtInt, fmtSupplierCost } from '../../lib/format';
import {
  PURCHASE_ORDER_STATUS_FILTER_OPTIONS,
  isDraftPurchaseOrder,
  purchaseOrderStatusPill,
} from '../../lib/purchaseOrderStatus';
import type { PurchaseOrder } from '../../types/scm.types';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

/** All / Outstanding / Completed - the buyer's "at a glance" read (the captain, 20 Aug: "how
 *  do i know the open PO / outstanding PO"). Maps straight onto the list's `outstanding`
 *  query param: `true` / `false` / omitted. Default Outstanding, because that is the
 *  question this screen exists to answer.
 *
 *  Worded the way the Status column words the same fact, and the way the sales-order book
 *  words its own: a toggle reading "Closed" over a column of "Completed" is two names for
 *  one thing on one screen. */
type OutstandingFilter = 'all' | 'outstanding' | 'completed';

const OUTSTANDING_TO_PARAM: Record<OutstandingFilter, boolean | null> = {
  all: null,
  outstanding: true,
  completed: false,
};

/** Allocated = yes | no, or every order (AC-G4). A string rather than a tri-state boolean
 *  because that is what `SearchableSelect` reads and writes, the same as the Status filter
 *  beside it. */
type AllocatedFilter = '' | 'yes' | 'no';

const ALLOCATED_TO_PARAM: Record<AllocatedFilter, boolean | null> = {
  '': null,
  yes: true,
  no: false,
};

const ALLOCATED_FILTER_OPTIONS = [
  { value: 'yes', label: 'Allocated' },
  { value: 'no', label: 'Not allocated' },
];

/** Same tri-state shape as Allocated (G12, `PLAN-scm-reorder-oi-feedback-1sep.md` S6):
 *  a purchase order carrying an open line at a project-segment warehouse no claim names
 *  - the backfill Joey works from `FromSODocList` in AutoCount. */
type UnclaimedProjectBinFilter = '' | 'yes' | 'no';

const UNCLAIMED_PROJECT_BIN_TO_PARAM: Record<UnclaimedProjectBinFilter, boolean | null> = {
  '': null,
  yes: true,
  no: false,
};

const UNCLAIMED_PROJECT_BIN_FILTER_OPTIONS = [
  { value: 'yes', label: 'Unclaimed project bin' },
  { value: 'no', label: 'No unclaimed project bin' },
];

const isDraft = isDraftPurchaseOrder;

export default function PurchaseOrdersList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState('');
  // Default Outstanding: the buyer's "at a glance" question, answered without opening
  // the advanced Filters popover.
  const [outstandingFilter, setOutstandingFilter] = useState<OutstandingFilter>('outstanding');
  // "Have we ever bought this item, and for how much." The plan now takes its cost from
  // this book, so when a plan line shows no cost, this is where the buyer finds out why.
  const [productFilter, setProductFilter] = useState('');

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearchQuery(state.searchQuery);
    setStatusFilter(state.filters.status ?? '');
    setProductFilter(state.filters.product_code ?? '');
    setOutstandingFilter(
      state.filters.outstanding === 'true'
        ? 'outstanding'
        : state.filters.outstanding === 'false'
          ? 'completed'
          : 'all',
    );
  });
  // Committed on Enter or blur rather than per keystroke: this filter hits the whole order
  // book by line, and firing it on every character is a query per letter typed.
  const [productDraft, setProductDraft] = useState('');
  // "Is this purchase order already spoken for" (section 3.G, AC-G4). '' = every order,
  // 'yes' = something is linked to a line of it, 'no' = nothing is.
  const [allocatedFilter, setAllocatedFilter] = useState<AllocatedFilter>('');
  // G12's own filter (AC-6.11): '' = every order, 'yes' = carries an unclaimed
  // project-bin line, 'no' = none of its open lines do.
  const [unclaimedProjectBinFilter, setUnclaimedProjectBinFilter] =
    useState<UnclaimedProjectBinFilter>('');
  // `?documents=a,b,c` - the exact orders ONE upload wrote, which is how the Order
  // Inquiries page sends the buyer here to look at the book they just uploaded (AC-H13).
  // Read from the URL and clearable like any other filter; absent on every other visit.
  const searchParams = useSearchParams();
  const [documentsFilter, setDocumentsFilter] = useState<string[]>(() =>
    (searchParams.get('documents') ?? '').split(',').filter(Boolean),
  );
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // Confirm-flow dialog state.
  const [confirmIds, setConfirmIds] = useState<string[] | null>(null);
  // Bulk delete (captain, 20 Aug: "give me an option to bulk delete purchase orders").
  const [deleteIds, setDeleteIds] = useState<string[] | null>(null);
  // The outstanding PURCHASE-ORDER book is loaded here, on the screen whose actor owns
  // it, until AutoCount is integrated.
  const [uploadOpen, setUploadOpen] = useState(false);

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = usePurchaseOrders({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter || null,
    supplier: null,
    productCode: productFilter || null,
    outstanding: OUTSTANDING_TO_PARAM[outstandingFilter],
    allocated: ALLOCATED_TO_PARAM[allocatedFilter],
    unclaimedProjectBin: UNCLAIMED_PROJECT_BIN_TO_PARAM[unclaimedProjectBinFilter],
    documents: documentsFilter.length ? documentsFilter : null,
  });

  // `createGr` is deliberately not taken: recording what arrived is a receiving decision
  // made against the delivery in hand, not a button on a list of 13,000 orders - the same
  // reason "Create DO" came off the sales-order list.
  const { confirm, bulkDelete } = usePurchaseOrderActions();

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [
    searchQuery,
    statusFilter,
    productFilter,
    outstandingFilter,
    allocatedFilter,
    unclaimedProjectBinFilter,
    documentsFilter,
  ]);
  // A tick on a row the new filter excludes is not a selection any more.
  useEffect(() => {
    setRowSelection({});
  }, [
    searchQuery,
    statusFilter,
    productFilter,
    outstandingFilter,
    allocatedFilter,
    unclaimedProjectBinFilter,
    documentsFilter,
  ]);

  const rows = useMemo<PurchaseOrder[]>(() => data?.data ?? [], [data]);

  // Carried into the detail URL so its prev/next pager walks the SAME filtered, sorted page
  // the user was reading (same param names as the list GET).
  const detailSearch = useMemo(
    () =>
      buildDetailSearch(
        { pageIndex: pagination.pageIndex, pageSize: pagination.pageSize, sorting, searchQuery },
        {
          status: statusFilter || undefined,
          product_code: productFilter || undefined,
          outstanding: OUTSTANDING_TO_PARAM[outstandingFilter] ?? undefined,
          allocated: ALLOCATED_TO_PARAM[allocatedFilter] ?? undefined,
          unclaimed_project_bin:
            UNCLAIMED_PROJECT_BIN_TO_PARAM[unclaimedProjectBinFilter] ?? undefined,
        },
      ),
    [
      pagination.pageIndex,
      pagination.pageSize,
      sorting,
      searchQuery,
      statusFilter,
      productFilter,
      outstandingFilter,
      allocatedFilter,
      unclaimedProjectBinFilter,
    ],
  );

  const detailHref = (po: PurchaseOrder) =>
    `/scm/purchase-orders/${po.id}${detailSearch ? `?${detailSearch}` : ''}`;

  const columns = useMemo<ColumnDef<PurchaseOrder>[]>(
    () => [
      // Select-all means all rows (the user unticks what they don't want); the Confirm
      // action then applies to the draft subset of the selection (see bulkActions).
      buildSelectColumn<PurchaseOrder>(),
      {
        accessorKey: 'po_number',
        header: ({ column }) => <DataGridColumnHeader title="PO number" column={column} />,
        cell: ({ row }) => (
          <div className="flex flex-col">
            {/* The document number IS the way in, and the whole row opens it too. The
                anchor stays real so middle-click and copy-link still work, and stops its
                own click propagating. */}
            <Link
              href={detailHref(row.original)}
              onClick={(e) => e.stopPropagation()}
              className="font-medium text-primary hover:underline"
              title={`Open ${row.original.po_number}`}
            >
              {row.original.po_number}
            </Link>
            <span className="text-xs text-muted-foreground">{fmtDate(row.original.order_date)}</span>
          </div>
        ),
        size: 160,
        meta: { headerTitle: 'PO number', skeleton: <Skeleton className="h-8 w-28" /> },
      },
      {
        accessorKey: 'supplier_name',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        cell: ({ row }) =>
          row.original.supplier_name ? (
            <span className="truncate" title={row.original.supplier_name}>
              {row.original.supplier_name}
            </span>
          ) : (
            // An imported historical PO can carry no supplier at all. Blank read as the
            // warehouse underneath it standing in for the supplier - the captain's "why
            // is BRW under supplier?" - so this is an explicit dash, never the location.
            <span
              className="truncate text-muted-foreground"
              title="No supplier on this imported PO"
            >
              {EM_DASH}
            </span>
          ),
        size: 180,
        meta: { headerTitle: 'Supplier' },
      },
      // No header-level Location column (captain, 20 Aug: "PO's location is at line
      // level ... at header level we don't need location actually") - the per-line
      // warehouse lives on the detail page's lines grid, where the fact is real.
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        // The same light chip every other enum column on this screen wears, worded the way
        // AutoCount words it: still expecting goods reads Outstanding, an order that
        // finished reads Completed. It used to be a ghost chip with a dot, on the theory
        // that a STATE is a different kind of thing from an enum; the captain's verdict on
        // a bare green dot beside a word was that it reads as an unfinished control, so the
        // colour is carried by the chip itself. The separate "On order" column that used to
        // sit beside it is gone - it said the same thing twice, in different words, in a
        // column whose only two values were the two this pill now carries.
        cell: ({ row }) => {
          const pill = purchaseOrderStatusPill(row.original);
          return (
            <Badge variant={pill.variant} appearance="light" size="md">
              {pill.label}
            </Badge>
          );
        },
        size: 150,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'expected_date',
        // "Delivery date" is what the buyer calls it and what AutoCount prints; "Expected
        // date" was our word for the same column. The stored field is untouched - only the
        // heading is - so the sort key, the listing preference and the API all still say
        // `expected_date`.
        header: ({ column }) => <DataGridColumnHeader title="Delivery date" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{fmtDate(row.original.expected_date)}</span>
        ),
        size: 140,
        meta: { headerTitle: 'Delivery date' },
      },
      {
        accessorKey: 'total_qty',
        header: ({ column }) => <DataGridColumnHeader title="Total qty" column={column} />,
        cell: ({ row }) => fmtInt(row.original.total_qty),
        size: 100,
        meta: { headerTitle: 'Total qty', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        accessorKey: 'line_count',
        header: ({ column }) => <DataGridColumnHeader title="Lines" column={column} />,
        cell: ({ row }) => fmtInt(row.original.line_count),
        size: 80,
        meta: { headerTitle: 'Lines', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        accessorKey: 'allocated_qty',
        // How much of this order an order inquiry has already occupied (section 3.G,
        // AC-G4). WHO is on it lives on the detail page's Allocated to panel - a list of
        // 13,000 orders answers "is this one spoken for", not "by whom".
        header: ({ column }) => <DataGridColumnHeader title="Allocated" column={column} />,
        cell: ({ row }) =>
          row.original.allocated_qty ? (
            fmtInt(row.original.allocated_qty)
          ) : (
            // 0 and "nothing is linked to it" are the same answer here, and the dash is
            // what stops a column of zeros reading as a figure somebody has to check.
            <span className="text-muted-foreground">{EM_DASH}</span>
          ),
        // Not sortable: the figure is computed off the links table per page rather than
        // being a column of `purchase_orders`, so an ORDER BY on it would need a join the
        // list does not make. The filter beside it is what narrows on this fact.
        enableSorting: false,
        size: 110,
        meta: {
          headerTitle: 'Allocated',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      // No per-row action column. "Create GR" lived here and is gone, the same day and for
      // the same reason "Create DO" came off the sales-order list: recording what arrived
      // is a receiving decision made against the delivery in hand, not a button beside a
      // row on a list of 13,000 orders. Confirm and Delete stay, as bulk actions, because
      // they ARE list decisions - the buyer picks the rows and acts on the set.
    ],
    // `detailSearch` is read by the PO-number link and the row-click handler. Left out of
    // the deps, the columns would keep the query from the FIRST render, so every row would
    // link to page 1 of an unfiltered list.
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

  const filtersActive =
    (statusFilter ? 1 : 0) +
    (productFilter ? 1 : 0) +
    (allocatedFilter ? 1 : 0) +
    (unclaimedProjectBinFilter ? 1 : 0) +
    (documentsFilter.length ? 1 : 0);
  const lastCost = data?.product_cost ?? null;

  // Confirm applies to the DRAFT subset of the selection (select-all can include actives);
  // Delete applies to the WHOLE selection regardless of status.
  const selectedIds = table.getSelectedRowModel().rows.map((r) => r.original.id);
  const selectedDraftIds = table
    .getSelectedRowModel()
    .rows.filter((r) => isDraft(r.original.status))
    .map((r) => r.original.id);

  const runConfirm = async () => {
    if (!confirmIds) return;
    try {
      const res = await confirm.mutateAsync(confirmIds);
      table.resetRowSelection();
      toast.success(
        `Confirmed ${res.confirmed_count} purchase order${res.confirmed_count === 1 ? '' : 's'} - now counted as incoming stock`,
      );
      setConfirmIds(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to confirm purchase orders');
    }
  };

  /**
   * The upload was QUEUED, so there is nothing yet to report: it writes on the worker and
   * the drawer is already following the job. Refetching is still worth doing - the list
   * re-reads as soon as the job lands rather than showing yesterday's book until a reload -
   * and the dialog has already told the user it is queued.
   */
  const bookQueued = () => {
    void refetch();
  };

  const runBulkDelete = async () => {
    if (!deleteIds) return;
    try {
      const res = await bulkDelete.mutateAsync(deleteIds);
      table.resetRowSelection();
      const deletedMsg = `Deleted ${res.deleted} purchase order${res.deleted === 1 ? '' : 's'}`;
      // The side effect the buyer has to know about, not just the count they asked for:
      // any row that was placed on one of these POs is back on the board unplaced.
      toast.success(
        res.unplaced_rows > 0
          ? `${deletedMsg} - ${res.unplaced_rows} order-inquiry row${res.unplaced_rows === 1 ? '' : 's'} placed on them ${res.unplaced_rows === 1 ? 'was' : 'were'} put back on the board`
          : deletedMsg,
      );
      setDeleteIds(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete purchase orders');
    }
  };

  // An empty book and an over-filtered one look identical in the grid, so they say different
  // things: one is a dead end the user can clear, the other is the step they have not done
  // yet. The default toggle IS a filter, so it counts - otherwise a book with nothing
  // outstanding would tell the buyer he has never uploaded one.
  const emptyMessage =
    filtersActive || searchQuery || outstandingFilter !== 'all' ? (
      'No purchase order matches this search and filter.'
    ) : (
      <span>
        No purchase orders yet. Upload the purchase-order book from the Actions menu, or
        accept a funded reorder recommendation to draft one.
      </span>
    );

  return (
    <div className="space-y-3">
      {/* The draft-vs-active explainer banner lived here until the captain removed it
          (20 Aug) - teaching prose belongs in the user guide, not the UI. */}
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        emptyMessage={emptyMessage}
        // The whole row opens the order, the same as the sales-order list. The PO-number
        // link stays a real anchor and stops its own click propagating.
        rowHref={(row) => detailHref(row)}
      >
        <Card>
          {documentsFilter.length ? (
            // A list narrowed to somebody else's link is not a state to leave unexplained:
            // it says how many orders it is showing and gives them all back in one press.
            <div className="flex items-center justify-between gap-3 border-b px-5 py-2.5 text-sm">
              <span>
                Showing the {documentsFilter.length} purchase order
                {documentsFilter.length === 1 ? '' : 's'} from one upload.
              </span>
              <Button variant="ghost" size="sm" onClick={() => setDocumentsFilter([])}>
                Show all
              </Button>
            </div>
          ) : null}
          {productFilter ? (
            <div
              className="border-b px-5 py-2.5 text-sm"
              role="status"
              aria-label="Last purchase price"
            >
              {lastCost ? (
                <span>
                  Last paid{' '}
                  <span className="font-medium tabular-nums">
                    {/* In the currency the order was written in. The book is 8438 lines
                        USD against 4186 MYR, so "RM 45" against a USD purchase order is a
                        wrong number, not a formatting detail. */}
                    {fmtSupplierCost(lastCost.unit_cost, lastCost.currency)}
                  </span>{' '}
                  for <span className="font-medium">{productFilter}</span>
                  {lastCost.supplier_name ? ` from ${lastCost.supplier_name}` : ''} on{' '}
                  {lastCost.po_number}
                  {lastCost.issue_date ? ` (${fmtDate(lastCost.issue_date)})` : ''}.
                </span>
              ) : (
                // Never bought is a different answer from bought for nothing, and this is
                // the screen where the buyer tells them apart: a plan line with no cost is
                // explained by this sentence.
                <span className="text-muted-foreground">
                  No purchase order records a price for{' '}
                  <span className="font-medium text-foreground">{productFilter}</span>, so the
                  plan has no cost to work from.
                </span>
              )}
            </div>
          ) : null}
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <>
                  <ListSearchInput
                    value={searchInput}
                    onChange={setSearchInput}
                    isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                    placeholder="Search PO or supplier..."
                    className="w-64"
                  />
                  {/* "How do i know the open PO / outstanding PO" (the captain, 20 Aug) -
                      a glance-able control, not buried inside the Filters popover. */}
                  <ToggleGroup
                    type="single"
                    variant="outline"
                    value={outstandingFilter}
                    onValueChange={(v) => v && setOutstandingFilter(v as OutstandingFilter)}
                  >
                    <ToggleGroupItem value="all" className="px-3">
                      All
                    </ToggleGroupItem>
                    <ToggleGroupItem value="outstanding" className="px-3">
                      Outstanding
                    </ToggleGroupItem>
                    <ToggleGroupItem value="completed" className="px-3">
                      Completed
                    </ToggleGroupItem>
                  </ToggleGroup>
                </>
              }
              filters={{
                kind: 'custom',
                active: filtersActive > 0,
                activeCount: filtersActive,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label htmlFor="po-product-code" className="mb-1 block">
                        Product code
                      </Label>
                      <Input
                        id="po-product-code"
                        placeholder="e.g. MWC7624-RL-S10"
                        value={productDraft}
                        onChange={(e) => setProductDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') setProductFilter(productDraft.trim());
                        }}
                        onBlur={() => setProductFilter(productDraft.trim())}
                      />
                    </div>
                    <div>
                      <Label htmlFor="po-status" className="mb-1 block">
                        Status
                      </Label>
                      {/* Only Draft and Cancelled: the toggle above already answers
                          Outstanding versus Completed, and offering "Received" or "Closed"
                          here would put words in the filter that the Status column never
                          prints. */}
                      <SearchableSelect
                        id="po-status"
                        value={statusFilter}
                        onChange={setStatusFilter}
                        options={PURCHASE_ORDER_STATUS_FILTER_OPTIONS}
                        placeholder="All statuses"
                      />
                    </div>
                    <div>
                      <Label htmlFor="po-allocated" className="mb-1 block">
                        Allocated
                      </Label>
                      {/* Clearable, like every other optional select: the buyer has to be
                          able to unset it, not only change it. */}
                      <SearchableSelect
                        id="po-allocated"
                        value={allocatedFilter}
                        onChange={(v) => setAllocatedFilter((v || '') as AllocatedFilter)}
                        options={ALLOCATED_FILTER_OPTIONS}
                        placeholder="Any"
                        clearable
                      />
                    </div>
                    <div>
                      <Label htmlFor="po-unclaimed-project-bin" className="mb-1 block">
                        Project bin
                      </Label>
                      <SearchableSelect
                        id="po-unclaimed-project-bin"
                        value={unclaimedProjectBinFilter}
                        onChange={(v) =>
                          setUnclaimedProjectBinFilter((v || '') as UnclaimedProjectBinFilter)
                        }
                        options={UNCLAIMED_PROJECT_BIN_FILTER_OPTIONS}
                        placeholder="Any"
                        clearable
                      />
                    </div>
                    {filtersActive > 0 ? (
                      <div className="flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setStatusFilter('');
                            setProductFilter('');
                            setProductDraft('');
                            setAllocatedFilter('');
                            setUnclaimedProjectBinFilter('');
                            setDocumentsFilter([]);
                          }}
                        >
                          Clear filters
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ),
              }}
              bulkActionsSlot={
                // Unified "Actions" dropdown (same pattern as the reorder results grid).
                // Confirm surfaces when the selection contains ≥1 draft; Delete surfaces
                // whenever anything at all is selected. BulkActionsMenu renders nothing
                // (button hidden) when neither applies.
                <BulkActionsMenu
                  actions={buildPoBulkActions(
                    { draftCount: selectedDraftIds.length, selectedCount: selectedIds.length },
                    {
                      onConfirm: () => setConfirmIds(selectedDraftIds),
                      onDelete: () => setDeleteIds(selectedIds),
                    },
                  )}
                />
              }
              exportConfig={{ filename: 'purchase_orders_export.xlsx' }}
              // Two secondary actions is what makes the shared toolbar collapse them into
              // an "Actions" dropdown (data-grid-list-toolbar.tsx) instead of a loose button
              // beside a standalone refresh icon, which wrapped this toolbar onto a second
              // row at 1280px. Same shape as the sales-order list.
              secondaryActions={[
                {
                  key: 'refresh',
                  label: 'Refresh',
                  icon: RefreshCw,
                  onClick: () => void refetch(),
                },
                {
                  key: 'upload-purchase-orders',
                  // The file carries the whole book - orders still outstanding and orders
                  // already completed - so naming the action after half of it described a
                  // scope the export never had. Same wording as the sales-order side.
                  label: 'Upload purchase orders',
                  icon: Upload,
                  onClick: () => setUploadOpen(true),
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

      <ConfirmActionDialog
        open={!!confirmIds}
        onOpenChange={(o) => !o && setConfirmIds(null)}
        title="Confirm purchase orders?"
        description={
          confirmIds
            ? `Confirm ${fmtInt(confirmIds.length)} draft purchase order${
                confirmIds.length === 1 ? '' : 's'
              }? Confirming makes these count as incoming stock (on-order) in the next reorder run.`
            : ''
        }
        confirmLabel="Confirm POs"
        onConfirm={runConfirm}
        isBusy={confirm.isPending}
      />

      {/* Mounted only while open, the same as the sales-order list: a closed dialog starts
          from a clean flow rather than whatever the last upload left behind. */}
      {uploadOpen ? (
        <OutstandingUploadDialog
          open
          onOpenChange={setUploadOpen}
          kind="purchase-orders"
          onQueued={bookQueued}
        />
      ) : null}

      {/* Destructive - AlertDialog + destructive button per ADR-PRODUCT-STANDARDS, not
          ConfirmActionDialog (that one is reserved for non-destructive confirms). */}
      <AlertDialog open={!!deleteIds} onOpenChange={(o) => !o && setDeleteIds(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm delete</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteIds
                ? `Delete ${fmtInt(deleteIds.length)} purchase order${
                    deleteIds.length === 1 ? '' : 's'
                  }? This action cannot be undone.`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteIds(null)}
              disabled={bulkDelete.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={runBulkDelete}
              disabled={bulkDelete.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {bulkDelete.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
