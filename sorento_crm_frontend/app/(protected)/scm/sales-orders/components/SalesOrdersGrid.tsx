'use client';

import { useCallback, useMemo, useState } from 'react';
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
import { useQueryClient } from '@tanstack/react-query';
import {
  ChevronDown,
  Plus,
  RefreshCw,
  RotateCcw,
  Upload,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { useSalesOrderActions } from '../actions';
import { useRowPending } from '@/hooks/useDeferredRowAction';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatMyrExact } from '@/app/(protected)/project-sales/_shared/lib/money';
import { buildPlanActions, PLAN_PERMISSION } from '../lib/planActions';
import { formatStatusLabel } from '@/lib/status-badge';
import { demandClassBadge } from '../../lib/demandClass';
import { TRANSFERABLE_FILTER_OPTIONS, transferableBadge } from '../../lib/transferable';
import { salesOrderPlannedBadge } from '../../lib/salesOrderPlanned';
import {
  SALES_ORDER_STATUS_FILTER_OPTIONS,
  salesOrderPriorityVariant,
  salesOrderStatusLabel,
  salesOrderStatusVariant,
} from '../../lib/salesOrderStatus';
import { useCustomerOptions } from '../../hooks/useScmOptions';
import { usePathname, useRouter } from 'next/navigation';
import {
  useCreateSalesOrder,
  useResetSalesOrderPlanning,
  useSalesOrders,
} from '../../hooks/useSalesOrders';
import { useSalesAgentOptions } from '../hooks/useSalesAgentOptions';
import { SOURCE_FILTER_OPTIONS, SOURCE_LABELS } from '../lib/sourceLabels';
import { fmtDate, fmtInt } from '../../lib/format';
import type { SalesOrder, SalesOrderFormData } from '../../types/scm.types';
import { SalesOrderFormModal } from './SalesOrderFormModal';
// The order book upload lives on Reorder planning - the whole plan is computed from it, so
// it is a planning action there. This list is the other place someone reasonably looks for
// it, so the same dialog (never forked) is reused here too.
import { OutstandingUploadDialog } from '../../reorder/components/OutstandingUploadDialog';
import { runHistoryKey, todayRunKey } from '../../reorder/hooks/useReorderRun';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useListingViewPreferences } from '@/lib/listing-column-preferences/useListingViewPreferences';

/**
 * THE sales-order table. One component, two places: the Sales Orders list (`SalesOrdersList`,
 * which is this with no props) and the Sales orders tab on a sales agent's record.
 *
 * Extracted rather than copied. A second, smaller table of the same rows is how one screen
 * comes to word a status differently from the other, or to print a total the first one
 * computes another way - and the reader has no way to tell a deliberate difference from an
 * accidental one. What the agent tab changes is stated as props (see `SalesOrdersGridProps`)
 * and amounts to hiding what that context already answers; the columns, the cell renderers,
 * the toolbar, the row click and the footer are the list's own.
 */

/** How many purchase orders to name in the cell before collapsing the rest into a count. */
const WAITING_ON_LIMIT = 2;

/** Same cap for the inquiry numbers, for the same reason. */
const ORDER_INQUIRY_LIMIT = 2;

/**
 * How many orders may be planned together, matching the board's own bound
 * (`FulfilmentPlanningClient.MAX_BOARD_SELECTION`). The whole book is 862 products across
 * 349 dates, so a board of everything is roughly 300,000 cells and is not a screen. Stated
 * here so the action can say why it is refusing BEFORE the board is opened.
 */
const MAX_PLAN_SELECTION = 50;

/** What the backend gates Reset planning on: the buyer's own write permission. */
const RESET_PERMISSION = 'scm.reorder.run';

/** The planning class - what the classification agents actually resolved, as distinct from
 *  the rarely-stated `order_type_label`. `unclassified` reads `demand_class IS NULL`. */
const DEMAND_CLASS_FILTER_OPTIONS = [
  { value: '', label: 'All types' },
  { value: 'project', label: 'Project' },
  { value: 'retail', label: 'Retail' },
  { value: 'unclassified', label: 'Unclassified' },
];

const PRIORITY_FILTER_OPTIONS = [
  { value: '', label: 'All priorities' },
  { value: 'urgent', label: 'Urgent' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'normal', label: 'Normal' },
  { value: 'low', label: 'Low' },
];

/**
 * When an order's lines are due: the earliest date, and the rest behind a "+N".
 *
 * Every distinct date, never a range. An order due on 12 January and on 10 March is due on
 * two DAYS; printing "12/01/2026 - 10/03/2026" claims the eight weeks in between, which
 * nothing in the data says and which a planner reads as a delivery window.
 *
 * The expander is a popover rather than more text in the cell, so a row keeps its height
 * and the column keeps its width whether the order is due on one day or on nine.
 */
function DeliveryDatesCell({ dates }: { dates: string[] }) {
  if (!dates.length) return <span className="text-muted-foreground">-</span>;
  const [first, ...rest] = dates;
  const all = dates.map(fmtDate).join(', ');
  if (!rest.length) {
    return (
      <span className="truncate text-muted-foreground" title={all}>
        {fmtDate(first)}
      </span>
    );
  }
  return (
    <div className="flex min-w-0 items-center gap-1">
      <span className="truncate text-muted-foreground" title={all}>
        {fmtDate(first)}
      </span>
      <Popover>
        <PopoverTrigger asChild>
          <Button
            variant="dim"
            size="sm"
            className="h-6 shrink-0 px-1.5 text-xs"
            // The row underneath opens the order; this opens the list of dates.
            onClick={(e) => e.stopPropagation()}
            aria-label={`Show all ${dates.length} delivery dates`}
          >
            +{rest.length}
            <ChevronDown className="size-3" />
          </Button>
        </PopoverTrigger>
        {/* Portalled (owner, 29 Sep 2026): at the document root the popover sits above
            the rows beneath it instead of painting inside the table. */}
        <PopoverPortal>
          <PopoverContent
            align="start"
            collisionPadding={8}
            className="w-44 p-2"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex flex-col gap-1">
              {dates.map((d) => (
                <span key={d} className="text-sm tabular-nums">
                  {fmtDate(d)}
                </span>
              ))}
            </div>
          </PopoverContent>
        </PopoverPortal>
      </Popover>
    </div>
  );
}

/**
 * The listing's shipped default, until the user has left one behind: latest document date
 * first, so the list opens on what came in most recently rather than the row that happened
 * to be inserted last (the captain, 3 Sep - rolled out from the Stock Inquiries pilot).
 */
const DEFAULT_SORTING: SortingState = [{ id: 'order_date', desc: true }];

/**
 * Shape of what this page stores in the opaque `filters` blob (PLAN-listing-view-memory).
 * BUMP whenever this shape changes, so a blob written by the old shape is discarded rather
 * than applied (AC-B4). `outstanding` is `true` or absent - there is no "explicitly off"
 * state to store, an absent key already means off.
 *
 * v2: the customer axis is keyed `customer_code`, not `customer_id` - the value the
 * Customer select actually holds is a customer code, and `customer_id` was never read by
 * anything on the wire (the Customer filter itself was a no-op, fixed alongside this bump).
 */
const FILTERS_VERSION = 2;

type SalesOrdersFilters = {
  status?: string;
  priority?: string;
  source?: string;
  date_from?: string;
  date_to?: string;
  customer_code?: string;
  sales_agent_id?: string;
  demand_class?: string;
  transferable?: string;
  outstanding?: true;
};

/** Looks a stored/selected value up in a `{value,label}` option list for the chip's words. */
function optionLabel(
  options: { value: string; label: string }[],
  value: string,
): string | undefined {
  return options.find((o) => o.value === value)?.label;
}

export interface SalesOrdersGridProps {
  /**
   * Pin the grid to ONE agent's orders.
   *
   * Set by the sales-agent detail page's Sales orders tab. With it, the Agent column and
   * the Agent filter come off (every row would say the same thing), and so do the two
   * writes that belong to the book rather than to one agent - Add sales order and Upload
   * sales orders. Everything else is the list, unchanged: the same columns, the same cell
   * renderers, the same toolbar, the same footer. There is no second sales-order table in
   * this product, so the two surfaces cannot drift apart.
   */
  salesAgentId?: string;
  /**
   * Where the reader's column order/visibility is stored. Left unset, `DataGrid` keys off
   * the route, which is right for the list and wrong inside a record page - the path
   * carries the agent's id, so every agent would get their own saved layout.
   */
  listingKey?: string;
}

/**
 * The row's "..." (D15): the same set the record's gear renders. Its own
 * component because the action set is a hook.
 */
function SalesOrderRowActions({ order }: { order: SalesOrder }) {
  const { actions } = useSalesOrderActions(order, { surface: 'toast' });
  return <RowActionsMenu ariaLabel="sales order" actions={actions} />;
}

export default function SalesOrdersGrid({ salesAgentId, listingKey }: SalesOrdersGridProps = {}) {
  // One agent's orders, inside that agent's record. What it turns off is listed on the prop.
  const pinnedToAgent = !!salesAgentId;
  // A row whose delete is counting down stays visible and dims (S6-07).
  const rowPending = useRowPending<SalesOrder>('scm_sales_order');
  const pathname = usePathname();
  // The SAME key `DataGrid` derives for column prefs (`listingKey ?? pathname`), so the
  // sort/filter row and the column row are one row (PLAN-listing-view-memory §3.2).
  const effectiveListingKey = listingKey ?? pathname;
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();

  // The sort and every filter are remembered per user, per listing. Page number and search
  // text are deliberately NOT remembered (AC-D1/D2).
  const {
    sorting,
    setSorting,
    filters: viewFilters,
    setFilters: setViewFilters,
    isLoading: isViewPrefsLoading,
  } = useListingViewPreferences<SalesOrdersFilters>({
    listingKey: effectiveListingKey,
    defaultSorting: DEFAULT_SORTING,
    filtersVersion: FILTERS_VERSION,
  });

  const statusFilter = viewFilters?.status ?? '';
  const priorityFilter = viewFilters?.priority ?? '';
  // "Show me the orders the Order Inquiry sheet created" is a filter on this list rather
  // than a screen of its own: a second list of the same entity is how two screens start
  // disagreeing about the same order.
  const sourceFilter = viewFilters?.source ?? '';
  // The three questions this screen is actually asked: what came in over these dates, whose
  // orders are these, and what is still owed.
  const dateFrom = viewFilters?.date_from ?? '';
  const dateTo = viewFilters?.date_to ?? '';
  const customerFilter = viewFilters?.customer_code ?? '';
  // The planning class the classification agents resolved - `order_type_label` is the ERP
  // document type and is blank on almost every row, so it never answered this question.
  const demandClassFilter = viewFilters?.demand_class ?? '';
  // AutoCount's Transferable flag (SO-TRANSFERABLE): yes | no | unknown.
  const transferableFilter = viewFilters?.transferable ?? '';
  // Never surfaced while pinned: the pin already wins in `effectiveAgentId`, and a stored
  // agent filter from the unpinned list has nothing to say inside one agent's own record.
  const agentFilter = pinnedToAgent ? '' : (viewFilters?.sales_agent_id ?? '');
  const outstandingOnly = viewFilters?.outstanding === true;

  // Merges a change over the current filters, drops empties, resets to page 1. Every
  // filter control below goes through this ONE path, so the chip's Clear and the popover's
  // own "Clear filters" cannot disagree about what clearing means.
  const applyFilters = useCallback(
    (partial: Partial<SalesOrdersFilters>) => {
      const merged: SalesOrdersFilters = { ...(viewFilters ?? {}), ...partial };
      const cleaned: SalesOrdersFilters = {};
      if (merged.status) cleaned.status = merged.status;
      if (merged.priority) cleaned.priority = merged.priority;
      if (merged.source) cleaned.source = merged.source;
      if (merged.date_from) cleaned.date_from = merged.date_from;
      if (merged.date_to) cleaned.date_to = merged.date_to;
      if (merged.customer_code) cleaned.customer_code = merged.customer_code;
      if (merged.sales_agent_id) cleaned.sales_agent_id = merged.sales_agent_id;
      if (merged.demand_class) cleaned.demand_class = merged.demand_class;
      if (merged.transferable) cleaned.transferable = merged.transferable;
      if (merged.outstanding) cleaned.outstanding = true;
      setViewFilters(Object.keys(cleaned).length ? cleaned : null);
      setPagination((p) => ({ ...p, pageIndex: 0 }));
    },
    [viewFilters, setViewFilters],
  );

  const clearFilters = useCallback(() => {
    setViewFilters(null);
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [setViewFilters]);

  // Back hands the list its own query string back, and the pager keeps rewriting it (S3-01).
  // Only pagination and search are restored from it now: sort and filter come from the
  // remembered view, which already holds what was active when the row was clicked.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    resetSearchQuery(state.searchQuery);
  });

  // Create-only: editing happens on the detail page in place (A5), the same shape as the
  // project sales order screen, and the row click is the way there.
  const [formOpen, setFormOpen] = useState(false);
  // Reset planning (the captain, 27 Aug): a UAT walk has to be repeatable from the screen.
  const [resetOpen, setResetOpen] = useState(false);
  const [rewindBook, setRewindBook] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  // Which orders to plan together. The board is a URL, not a stored plan, so the selection
  // is the whole state there is: nothing is saved by picking rows here.
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const canPlan = useHasPermission(PLAN_PERMISSION);
  const canReset = useHasPermission(RESET_PERMISSION);

  const queryClient = useQueryClient();

  // A pinned agent wins over the filter, which is not offered while it is pinned.
  const effectiveAgentId = salesAgentId ?? (agentFilter || null);

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = useSalesOrders({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter || null,
    priority: priorityFilter || null,
    source: sourceFilter || null,
    dateFrom: dateFrom || null,
    dateTo: dateTo || null,
    customerCode: customerFilter || null,
    outstanding: outstandingOnly,
    salesAgentId: effectiveAgentId,
    demandClass: demandClassFilter || null,
    transferable: transferableFilter || null,
    // One fetch, with the remembered view already applied (AC-B3).
    enabled: !isViewPrefsLoading,
  });

  const customerOptions = useCustomerOptions();
  const agentOptions = useSalesAgentOptions();
  const router = useRouter();

  const createMut = useCreateSalesOrder();
  const resetMut = useResetSalesOrderPlanning();

  // A search brings the reader back to page 0 to see the matches - but not on the FIRST
  // run, or this would wipe the page `useListStateFromUrl` just restored from Back's own
  // query string, landing every "Back to sales orders" on page 1. Filter changes reset the
  // page themselves (`applyFilters`/`clearFilters`); search is the only thing left that
  // needs its own reset here.
  useResetPageOnFilterChange(setPagination, [searchQuery]);

  const rows = useMemo<SalesOrder[]>(() => data?.data ?? [], [data]);

  // Carried into the detail URL so its prev/next pager walks the SAME filtered, sorted page
  // the user was reading (same param names as the list GET). Mirrors the purchase-order list.
  const detailSearch = useMemo(
    () =>
      buildDetailSearch(
        { pageIndex: pagination.pageIndex, pageSize: pagination.pageSize, sorting, searchQuery },
        {
          status: statusFilter || undefined,
          priority: priorityFilter || undefined,
          source: sourceFilter || undefined,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          customer_code: customerFilter || undefined,
          outstanding: outstandingOnly ? 'true' : undefined,
          sales_agent_id: effectiveAgentId || undefined,
          demand_class: demandClassFilter || undefined,
          transferable: transferableFilter || undefined,
        },
      ),
    [
      pagination.pageIndex,
      pagination.pageSize,
      sorting,
      searchQuery,
      statusFilter,
      priorityFilter,
      sourceFilter,
      dateFrom,
      dateTo,
      customerFilter,
      effectiveAgentId,
      outstandingOnly,
      demandClassFilter,
      transferableFilter,
    ],
  );

  const detailHref = (so: SalesOrder) =>
    `/scm/sales-orders/${so.id}${detailSearch ? `?${detailSearch}` : ''}`;

  const handleSubmit = async (formData: SalesOrderFormData) => {
    await createMut.mutateAsync(formData);
    setFormOpen(false);
  };

  // The dialog itself toasts and links to the job page (Confirm -> apply -> onApplied); this
  // list only has to refresh once the write lands. The reorder plan is computed from the same
  // order book, so its two queries are invalidated alongside this list's own, the same as
  // Reorder planning's own `uploadQueued`.
  const handleUploadQueued = () => {
    void queryClient.invalidateQueries({ queryKey: ['scm', 'sales-orders'] });
    void queryClient.invalidateQueries({ queryKey: todayRunKey });
    void queryClient.invalidateQueries({ queryKey: runHistoryKey });
  };

  const columns = useMemo<ColumnDef<SalesOrder>[]>(
    () => [
      // Picking orders to plan together is what this list is for, beside reading it: the
      // fulfilment board takes a set of sales orders and nothing else. Each box says WHICH
      // order it ticks - a grid of "Select row" tells a screen reader nothing.
      buildSelectColumn<SalesOrder>({
        rowLabel: (row) => `Select ${row.original.so_number}`,
      }),
      {
        accessorKey: 'so_number',
        header: ({ column }) => <DataGridColumnHeader title="SO number" column={column} />,
        cell: ({ row }) => (
          <div className="flex flex-col">
            {/* The document number IS the way in, the same as the purchase-order list. The
                list query rides along so the detail page's prev/next walks the page the
                user was actually reading. */}
            <Link
              href={detailHref(row.original)}
              onClick={(e) => e.stopPropagation()}
              className="font-medium text-primary hover:underline"
            >
              {row.original.so_number}
            </Link>
            {/* The document date is a COLUMN of its own now (the captain, 27 Aug): as a grey
                sub-line here it could not be sorted on, could not be compared down the page
                against the Delivery date column beside it, and read as an attribute of the
                number rather than as a date the order carries. */}
            {/* The book moved a planned line on this order and nobody has applied the change
                yet (AC-P3-1). The badge IS the way in: it opens the board on this order and
                that batch, which is where the change is decided. */}
            {row.original.planning_change_batch_id ? (
              <Link
                data-testid={`so-changed-${row.original.so_number}`}
                href={`/project-sales/fulfilment-planning?orders=${encodeURIComponent(
                  row.original.so_number,
                )}&batch=${encodeURIComponent(row.original.planning_change_batch_id)}`}
                onClick={(e) => e.stopPropagation()}
                className="mt-0.5 w-fit rounded bg-amber-100 px-1 text-[10px] font-medium text-amber-800 hover:underline"
              >
                Changed
              </Link>
            ) : null}
          </div>
        ),
        size: 160,
        meta: { headerTitle: 'SO number', skeleton: <Skeleton className="h-8 w-28" /> },
      },
      {
        // Immediately after the number, because that is where it was read from until now.
        // A saved layout that predates this column places it beside its definition-order
        // neighbour (`mergeColumnOrder`), so it lands here rather than at the far right.
        accessorKey: 'order_date',
        header: ({ column }) => <DataGridColumnHeader title="Document date" column={column} />,
        cell: ({ row }) => (
          <span className="truncate tabular-nums" title={fmtDate(row.original.order_date)}>
            {fmtDate(row.original.order_date)}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'Document date' },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
        // The name and nothing else. The market segment used to ride underneath it, and it
        // is the same answer the Type column already carries as a pill (the captain, 27 Aug):
        // one fact stated twice in one row is a row that reads as two.
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_name}>
            {row.original.customer_name}
          </span>
        ),
        size: 200,
        meta: { headerTitle: 'Customer' },
      },
      {
        accessorKey: 'project_label',
        header: ({ column }) => <DataGridColumnHeader title="Project" column={column} />,
        // Resolved from whichever source ranked highest (the Order Inquiry sheet, the
        // AutoCount note, its `Ref`, or a delivery address) - the source word itself is a
        // detail-page fact, not a list one, so the cell states only the name.
        cell: ({ row }) => {
          const label = row.original.project_label;
          if (!label) return <span className="text-muted-foreground">-</span>;
          return (
            <span className="block truncate" title={label}>
              {label}
            </span>
          );
        },
        size: 220,
        meta: { headerTitle: 'Project' },
      },
      // Off inside an agent's own record: a column whose every cell repeats the name at the
      // top of the page is a column that answers nothing.
      ...(pinnedToAgent
        ? []
        : [
            {
              accessorKey: 'sales_agent_code',
              header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
              cell: ({ row }) => {
                const code = row.original.sales_agent_code;
                if (!code) return <span className="text-muted-foreground">-</span>;
                return (
                  <span className="truncate" title={row.original.sales_agent_label || code}>
                    {code}
                  </span>
                );
              },
              size: 120,
              enableSorting: false,
              meta: { headerTitle: 'Agent' },
            } as ColumnDef<SalesOrder>,
          ]),
      {
        accessorKey: 'demand_class',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        // `order_type_label` is the ERP document type, and it is EMPTY on nearly every row
        // in this book - the AutoCount export rarely states one. `demand_class` is what the
        // classification agents actually resolved, so it is the primary answer here; the
        // document type rides along as a subline only when the order carries one AND it
        // says something different from Project/Retail.
        cell: ({ row }) => {
          const cls = demandClassBadge(row.original.demand_class);
          const label = row.original.order_type_label;
          const showLabel = label && label !== cls.label;
          return (
            <div className="flex flex-col gap-0.5">
              {/* The pill shape the sales-agents master uses, which is the page the captain
                  pointed at as the reference: `appearance="light" size="md"` for an enum. */}
              <Badge variant={cls.variant} appearance="light" size="md">
                {cls.label}
              </Badge>
              {showLabel ? (
                <span className="truncate text-2xs text-muted-foreground" title={label}>
                  {label}
                </span>
              ) : null}
            </div>
          );
        },
        size: 140,
        meta: { headerTitle: 'Type' },
      },
      {
        // AutoCount's Transferable flag (SO-TRANSFERABLE). Read-only everywhere: AutoCount
        // owns it. Not sortable - the route has no sort key for it, and the filter is how a
        // buyer finds the F orders.
        accessorKey: 'is_transferable',
        header: ({ column }) => <DataGridColumnHeader title="Transferable" column={column} />,
        cell: ({ row }) => {
          const flag = transferableBadge(row.original.is_transferable);
          return (
            <Badge variant={flag.variant} appearance="light" size="md">
              {flag.label}
            </Badge>
          );
        },
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Transferable' },
      },
      {
        accessorKey: 'priority',
        header: ({ column }) => <DataGridColumnHeader title="Priority" column={column} />,
        cell: ({ row }) => (
          <Badge
            variant={salesOrderPriorityVariant(row.original.priority)}
            appearance="light"
            size="md"
          >
            {formatStatusLabel(row.original.priority)}
          </Badge>
        ),
        size: 110,
        meta: { headerTitle: 'Priority' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        // The same light chip as every other pill in this table. It was a ghost chip with a
        // dot, on the theory that a STATE is a different kind of thing from an enum; the
        // captain's verdict on a bare green dot beside a word was that it reads as an
        // unfinished control, so the colour is carried by the chip itself. Worded the way
        // AutoCount words it: `open` is Outstanding, `closed` is Completed.
        cell: ({ row }) => (
          <Badge
            variant={salesOrderStatusVariant(row.original.status)}
            appearance="light"
            size="md"
          >
            {salesOrderStatusLabel(row.original.status)}
          </Badge>
        ),
        size: 160,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'planned',
        // A PLAIN TITLE, not `DataGridColumnHeader` (AC-S4-8). That primitive renders a
        // button whatever `enableSorting` says, so the header read as a control that does
        // nothing when clicked.
        header: () => <span>Planned</span>,
        // BESIDE STATUS, because it is the question Status keeps being read as answering and
        // does not: Completed says the book shipped it, this says whether anybody ever decided
        // where the stock came from. SO421404 read Completed, 3 of 3 delivered, and had never
        // been planned - which is exactly the order somebody has to go and plan.
        //
        // NOT SORTABLE AND NOT FILTERABLE in this lane: the counts are already on the row and
        // nobody has asked to slice by them. Trigger for adding either: the owner asks.
        cell: ({ row }) => {
          const badge = salesOrderPlannedBadge(
            row.original.planned_lines,
            row.original.plannable_lines,
          );
          return (
            <Badge variant={badge.variant} appearance="light" size="md">
              {badge.label}
            </Badge>
          );
        },
        enableSorting: false,
        size: 130,
        meta: { headerTitle: 'Planned' },
      },
      {
        accessorKey: 'order_inquiries',
        header: ({ column }) => <DataGridColumnHeader title="Order inquiries" column={column} />,
        // What purchasing has been told to do about this order, by NUMBER. There is no
        // planning record to show and no "Planning" column: the business sees sales orders
        // and order inquiries, so the order names its inquiries and links to them.
        cell: ({ row }) => {
          const inquiries = row.original.order_inquiries ?? [];
          if (!inquiries.length) return <span className="text-muted-foreground">-</span>;
          const shown = inquiries.slice(0, ORDER_INQUIRY_LIMIT);
          const hidden = inquiries.length - shown.length;
          // One line per inquiry on the tooltip: who raised it, when, and how far
          // purchasing has got with it. The cell itself stays a row of numbers.
          const title = inquiries
            .map(
              (i) =>
                `${i.inquiry_no ?? 'Unnumbered'}: raised ${fmtDate(i.raised_at)}` +
                `${i.raised_by_name ? ` by ${i.raised_by_name}` : ''}` +
                `, ${i.rows_placed}/${i.rows_total} placed`,
            )
            .join('\n');
          return (
            <span className="truncate" title={title}>
              {shown.map((inquiry, index) => (
                <span key={inquiry.inquiry_no ?? index}>
                  {index > 0 ? ', ' : null}
                  <Link
                    // S3, AC-LK-01: the OI detail page, by header id, when the payload
                    // carries one; the old filtered-list search only for a payload from
                    // before that column existed.
                    href={
                      inquiry.id
                        ? `/project-sales/order-inquiries/${inquiry.id}`
                        : `/project-sales/order-inquiries?query=${encodeURIComponent(
                            row.original.so_number,
                          )}`
                    }
                    onClick={(e) => e.stopPropagation()}
                    className="text-primary hover:underline"
                  >
                    {inquiry.inquiry_no ?? 'Unnumbered'}
                  </Link>
                </span>
              ))}
              {hidden > 0 ? <span className="text-muted-foreground"> +{hidden} more</span> : null}
            </span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Order inquiries' },
      },
      {
        accessorKey: 'total_qty',
        header: ({ column }) => <DataGridColumnHeader title="Total qty" column={column} />,
        cell: ({ row }) => fmtInt(row.original.total_qty),
        size: 100,
        meta: { headerTitle: 'Total qty', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        accessorKey: 'committed_qty',
        header: ({ column }) => <DataGridColumnHeader title="Committed" column={column} />,
        cell: ({ row }) => (
          <span className={row.original.committed_qty > 0 ? 'font-medium' : 'text-muted-foreground'}>
            {fmtInt(row.original.committed_qty)}
          </span>
        ),
        size: 110,
        meta: { headerTitle: 'Committed', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        accessorKey: 'total_amount',
        header: ({ column }) => <DataGridColumnHeader title="Total amount" column={column} />,
        // The same figure the detail page's Totals card prints, through the same formatter,
        // so the list and the order cannot disagree about what it is worth. `-` and not
        // `RM 0` when nobody priced it: 15,000 of the absorbed rows carry no money at all,
        // and an order nobody priced is not an order worth nothing.
        cell: ({ row }) =>
          row.original.total_amount ? formatMyrExact(row.original.total_amount) : '-',
        size: 140,
        // Not sortable: the total is summed from the LINES (each line's stated total, or
        // the arithmetic its parts support), so there is no column for Postgres to order
        // by and a SQL rewrite of that rule could disagree with the figure on screen.
        enableSorting: false,
        meta: {
          headerTitle: 'Total amount',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        // The id stays `delivery_date_from` though the value is now a LIST: it is the sort
        // key the backend knows (`min(required_date)`) and the id every saved column layout
        // names, so renaming it would silently drop both.
        id: 'delivery_date_from',
        accessorFn: (so) => so.delivery_dates?.[0],
        header: ({ column }) => <DataGridColumnHeader title="Delivery date" column={column} />,
        // The LINE dates, not the header's `requested_delivery_date`: one order routinely
        // ships on several days, and the header figure is blank on most of this book. Every
        // DISTINCT date, never a range - an order due on 12 January and 10 March is due on
        // two days, and "12/01/2026 - 10/03/2026" claims the eight weeks between them.
        // The cell prints the earliest and puts the rest behind a "+N", so the column keeps
        // its width whether the order is due on one day or on nine. Sorted by the earliest,
        // which is the question the column is scanned with: what is due first.
        cell: ({ row }) => <DeliveryDatesCell dates={row.original.delivery_dates ?? []} />,
        size: 190,
        meta: { headerTitle: 'Delivery date' },
      },
      // No Location column here. A location is a property of a LINE - one order routinely
      // lands in two - so it lives on the detail page's lines grid, where it belongs to the
      // row it describes rather than being flattened into a list on the header.
      {
        accessorKey: 'linked_purchase_orders',
        header: ({ column }) => <DataGridColumnHeader title="Waiting on" column={column} />,
        cell: ({ row }) => {
          // The UNRESOLVED ones only. "Which of my orders is stuck behind a purchase order
          // we have not received" is the question this column exists to answer, and listing
          // the matched ones alongside would bury it.
          const waiting = (row.original.linked_purchase_orders ?? []).filter(
            (l) => !l.resolved,
          );
          if (!waiting.length) return <span className="text-muted-foreground">-</span>;
          // Capped, because a real order waits on 23 purchase orders and the full list
          // renders as a wall of text that says less than the first two plus a count. The
          // whole list is still on the title attribute for anyone who needs it.
          const numbers = waiting.map((l) => l.po_number);
          const shown = numbers.slice(0, WAITING_ON_LIMIT).join(', ');
          const hidden = numbers.length - Math.min(numbers.length, WAITING_ON_LIMIT);
          return (
            <span className="truncate" title={numbers.join(', ')}>
              {shown}
              {hidden > 0 ? (
                <span className="text-muted-foreground"> +{hidden} more</span>
              ) : null}
            </span>
          );
        },
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Waiting on' },
      },
      {
        accessorKey: 'source',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        cell: ({ row }) => (
          <Badge
            variant={row.original.source === 'inquiry' ? 'primary' : 'secondary'}
            appearance="light"
            size="md"
          >
            {SOURCE_LABELS[row.original.source ?? 'manual'] ?? 'Manual'}
          </Badge>
        ),
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Source' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        // The same set the record's gear renders (D15). Editing is the detail page's
        // job - the row already opens it, and a pencil beside a clickable row is a
        // second door to the same screen.
        cell: ({ row }) => <SalesOrderRowActions order={row.original} />,
        size: 60,
        enableHiding: false,
        enableSorting: false,
      },
    ],
    // `detailSearch` is read by the SO-number link and the row-click handler. Left out of
    // the deps, the columns kept the query from the FIRST render, so every row linked to
    // page 1 of an unfiltered list and the detail pager walked a set the user never chose.
    [detailSearch, pinnedToAgent],
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

  // The orders the board would be opened on, in the order the list shows them. Document
  // NUMBERS, not ids: the board's URL is the plan (`?orders=SO1,SO2`), and it is a link a
  // person can read, keep and send.
  const selectedSoNumbers = table
    .getSelectedRowModel()
    .rows.map((r) => r.original.so_number);

  const planActions = buildPlanActions(
    { selectedCount: selectedSoNumbers.length, canPlan, max: MAX_PLAN_SELECTION },
    {
      onPlan: () =>
        router.push(
          `/project-sales/fulfilment-planning?orders=${encodeURIComponent(
            selectedSoNumbers.join(','),
          )}`,
        ),
    },
  );
  // Exactly one action, or none without the board's own permission (`buildPlanActions`) -
  // the toolbar's primary CTA, not a menu to map over.
  const planAction = planActions[0];
  const PlanIcon = planAction?.icon;

  const selectedOrders = table.getSelectedRowModel().rows.map((r) => r.original);
  const resetActions = canReset
    ? [
        {
          key: 'reset-planning',
          label: `Reset planning (${selectedOrders.length})`,
          icon: RotateCcw,
          onClick: () => setResetOpen(true),
          disabled: selectedOrders.length < 1,
          disabledReason:
            selectedOrders.length < 1 ? 'Tick the sales orders to reset first.' : undefined,
        },
      ]
    : [];

  const filtersActive =
    (statusFilter ? 1 : 0) +
    (priorityFilter ? 1 : 0) +
    (sourceFilter ? 1 : 0) +
    (dateFrom ? 1 : 0) +
    (dateTo ? 1 : 0) +
    (customerFilter ? 1 : 0) +
    (agentFilter ? 1 : 0) +
    (outstandingOnly ? 1 : 0) +
    (demandClassFilter ? 1 : 0) +
    (transferableFilter ? 1 : 0);

  // The chip's plain-words label (PLAN-listing-view-memory). Every axis states a NAME, never
  // a raw code or id; an axis whose name has not resolved yet (the customer/agent list still
  // loading) states its axis word (Customer, Agent) until the name arrives, so the chip is
  // never blank.
  const activeFilterLabel = useMemo(() => {
    const parts: string[] = [];
    if (statusFilter) parts.push(salesOrderStatusLabel(statusFilter));
    if (priorityFilter) parts.push(formatStatusLabel(priorityFilter));
    if (sourceFilter) {
      const label = SOURCE_LABELS[sourceFilter];
      if (label) parts.push(label);
    }
    if (demandClassFilter) {
      const label = optionLabel(DEMAND_CLASS_FILTER_OPTIONS, demandClassFilter);
      if (label) parts.push(label);
    }
    if (transferableFilter) {
      const label = optionLabel(TRANSFERABLE_FILTER_OPTIONS, transferableFilter);
      if (label) parts.push(`Transferable: ${label}`);
    }
    if (dateFrom && dateTo) parts.push(`Dates ${fmtDate(dateFrom)} to ${fmtDate(dateTo)}`);
    else if (dateFrom) parts.push(`Dates from ${fmtDate(dateFrom)}`);
    else if (dateTo) parts.push(`Dates to ${fmtDate(dateTo)}`);
    // Customer/agent options are a multi-thousand-row master that loads slower than the
    // grid's own first paint, so "not resolved yet" is the NORMAL first render for a user
    // who has only a customer or agent filter remembered - not a rare edge. The axis word
    // stands in until the name arrives, so the chip is never blank.
    if (customerFilter) {
      const name = optionLabel(customerOptions.data ?? [], customerFilter);
      parts.push(name ?? 'Customer');
    }
    if (!pinnedToAgent && agentFilter) {
      const name = optionLabel(agentOptions.options ?? [], agentFilter);
      parts.push(name ?? 'Agent');
    }
    if (outstandingOnly) parts.push('Outstanding qty');
    return parts.join(', ');
  }, [
    statusFilter,
    priorityFilter,
    sourceFilter,
    demandClassFilter,
    transferableFilter,
    dateFrom,
    dateTo,
    customerFilter,
    agentFilter,
    outstandingOnly,
    pinnedToAgent,
    customerOptions.data,
    agentOptions.options,
  ]);

  // An empty book and an over-filtered one look identical in the grid, so they say different
  // things: one is a dead end the user can clear, the other is the step they have not done yet.
  // Inside an agent's record the third answer is neither: the book is full, this agent simply
  // has none of it, and pointing them at an upload would be pointing at the wrong screen.
  const emptyMessage =
    filtersActive || searchQuery ? (
      'No sales order matches this search and filter.'
    ) : pinnedToAgent ? (
      'No sales orders for this agent.'
    ) : (
      <span>
        No sales orders yet. Upload the Order Inquiry sheet from{' '}
        <Link href="/scm/reorder" className="text-primary underline underline-offset-2">
          Reorder planning
        </Link>{' '}
        to create them, or add one with Add sales order.
      </span>
    );

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading || isViewPrefsLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        emptyMessage={emptyMessage}
        // The whole row opens the order. The SO-number link stays a real anchor so
        // middle-click and copy-link still work, and stops its own click propagating.
        rowHref={(row) => detailHref(row)}
        rowPending={rowPending}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        listingKey={listingKey}
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
                  // Four things, because those are the four a person holds when they come
                  // looking: the document number, who it is for, what is on it, and who
                  // sold it. The backend's `query` matches all four (`sales_order_service
                  // .list`), so the placeholder is not a promise the search cannot keep.
                  placeholder="Search SO, customer, product or agent..."
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: filtersActive > 0,
                activeCount: filtersActive,
                // The chip states what is active in plain words, and its Clear is the SAME
                // function the popover's own "Clear filters" button calls below - one clear
                // path, not two (PLAN-listing-view-memory). Only passed once there is a word
                // to show - an empty label would otherwise render a blank chip with an
                // `aria-label="Clear filter: "`.
                activeSummary: activeFilterLabel
                  ? { label: activeFilterLabel, onClear: clearFilters }
                  : undefined,
                content: (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between gap-3 rounded-md border p-2.5">
                      {/* The same words the detail page's Totals card and the lines grid's
                          own column use. "Still owed" was a third phrasing of one figure. */}
                      <Label htmlFor="so-outstanding-only" className="cursor-pointer">
                        Outstanding qty
                      </Label>
                      <Switch
                        id="so-outstanding-only"
                        checked={outstandingOnly}
                        onCheckedChange={(checked) =>
                          applyFilters({ outstanding: checked ? true : undefined })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor="so-customer" className="mb-1 block">
                        Customer
                      </Label>
                      <SearchableSelect
                        id="so-customer"
                        value={customerFilter}
                        onChange={(v) => applyFilters({ customer_code: v || undefined })}
                        options={customerOptions.data ?? []}
                        placeholder="All customers"
                        clearable
                      />
                    </div>
                    {/* Not offered while the grid is pinned to one agent: the answer is
                        already the page the reader is on. */}
                    {pinnedToAgent ? null : (
                      <div>
                        <Label htmlFor="so-agent" className="mb-1 block">
                          Agent
                        </Label>
                        <SearchableSelect
                          id="so-agent"
                          value={agentFilter}
                          onChange={(v) => applyFilters({ sales_agent_id: v || undefined })}
                          options={agentOptions.options}
                          placeholder="All agents"
                          clearable
                        />
                      </div>
                    )}
                    <div>
                      <Label htmlFor="so-date-range" className="mb-1 block">
                        Ordered
                      </Label>
                      <DateRangePicker
                        id="so-date-range"
                        from={dateFrom || null}
                        to={dateTo || null}
                        onChange={({ from, to }) =>
                          applyFilters({ date_from: from ?? undefined, date_to: to ?? undefined })
                        }
                        placeholder="All dates"
                      />
                    </div>
                    <div>
                      <Label htmlFor="so-type" className="mb-1 block">
                        Type
                      </Label>
                      <SearchableSelect
                        id="so-type"
                        value={demandClassFilter}
                        onChange={(v) => applyFilters({ demand_class: v || undefined })}
                        options={DEMAND_CLASS_FILTER_OPTIONS}
                        placeholder="All types"
                      />
                    </div>
                    <div>
                      <Label htmlFor="so-transferable" className="mb-1 block">
                        Transferable
                      </Label>
                      <SearchableSelect
                        id="so-transferable"
                        value={transferableFilter}
                        onChange={(v) => applyFilters({ transferable: v || undefined })}
                        options={TRANSFERABLE_FILTER_OPTIONS}
                        placeholder="All"
                        clearable
                      />
                    </div>
                    <div>
                      <Label htmlFor="so-status" className="mb-1 block">
                        Status
                      </Label>
                      <SearchableSelect
                        id="so-status"
                        value={statusFilter}
                        onChange={(v) => applyFilters({ status: v || undefined })}
                        options={SALES_ORDER_STATUS_FILTER_OPTIONS}
                        placeholder="All statuses"
                      />
                    </div>
                    <div>
                      <Label htmlFor="so-priority" className="mb-1 block">
                        Priority
                      </Label>
                      <SearchableSelect
                        id="so-priority"
                        value={priorityFilter}
                        onChange={(v) => applyFilters({ priority: v || undefined })}
                        options={PRIORITY_FILTER_OPTIONS}
                        placeholder="All priorities"
                      />
                    </div>
                    <div>
                      <Label htmlFor="so-source" className="mb-1 block">
                        Source
                      </Label>
                      <SearchableSelect
                        id="so-source"
                        value={sourceFilter}
                        onChange={(v) => applyFilters({ source: v || undefined })}
                        options={SOURCE_FILTER_OPTIONS}
                        placeholder="All sources"
                        clearable
                      />
                    </div>
                    {filtersActive > 0 ? (
                      <div className="flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={clearFilters}
                        >
                          Clear filters
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ),
              }}
              // No `bulkActions`: the strip keeps its count, its Export and Clear, and
              // nothing else. Plan selected is the toolbar's own primary button (the owner's
              // ruling, 22 Sep 2026) rather than a strip action, so the strip does not need to
              // carry it too - and a strip action only exists once rows are ticked, so it
              // could not be found by anyone who had not already guessed it was there.
              exportConfig={{ filename: 'sales_orders_export.xlsx' }}
              // Two secondary actions is what makes the shared toolbar collapse them into
              // an "Actions" dropdown (data-grid-list-toolbar.tsx) instead of a single loose
              // button, matching Delivery Orders (OrdersList.tsx).
              // Actions is the housekeeping menu: put the order book in, add one order by
              // hand, walk one back to never-planned, re-read the page. Planning is the one
              // thing this list STARTS, so it is the primary button instead, not a menu item.
              secondaryActions={[
                // The upload carries the WHOLE book, so it belongs to the book's own screen.
                // Offering it inside one agent's record would read as "upload this agent's
                // orders", which is not what the file is.
                ...(pinnedToAgent
                  ? []
                  : [
                      {
                        key: 'upload-sales-orders',
                        label: 'Upload sales orders',
                        icon: Upload,
                        onClick: () => setUploadOpen(true),
                      },
                    ]),
                // A new order created from inside one agent's record would carry no agent,
                // so the record it was added from would not list it.
                ...(pinnedToAgent
                  ? []
                  : [
                      {
                        key: 'add-sales-order',
                        label: 'Add sales order',
                        icon: Plus,
                        onClick: () => setFormOpen(true),
                      },
                    ]),
                ...resetActions,
                {
                  key: 'refresh',
                  label: 'Refresh',
                  icon: RefreshCw,
                  onClick: () => void refetch(),
                },
              ]}
              primaryAction={
                // Plan selected is the CTA (the owner's ruling, 22 Sep 2026, on a screenshot
                // of this list): the one thing the list starts is taking a set of orders to
                // the fulfilment board, so it gets the button rather than a menu item behind
                // a second click. `buildPlanActions` returns nothing without the board's own
                // permission, so the button itself disappears rather than opening a door that
                // answers 403.
                planAction ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      {/* A `title` on a disabled Button never reaches a real browser's
                          hover - the primitive sets `disabled:pointer-events-none`, so no
                          pointer event ever lands on it. The refusal travels in a Radix
                          Tooltip on a focusable wrapper instead, the same pattern
                          BoardLineDecisionPanel uses for its own disabled Save/Reject. */}
                      <span
                        tabIndex={0}
                        className="inline-flex"
                        data-testid="plan-selected-trigger"
                      >
                        <Button disabled={planAction.disabled} onClick={planAction.onClick}>
                          {PlanIcon ? <PlanIcon className="size-4" aria-hidden /> : null}
                          {planAction.label}
                        </Button>
                      </span>
                    </TooltipTrigger>
                    {planAction.disabled && planAction.disabledReason ? (
                      <TooltipContent>{planAction.disabledReason}</TooltipContent>
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

      {/* Mounted only while open, the same as `UploadDataMenu` on Reorder planning: a closed
          dialog starts from a clean flow rather than whatever the last upload left behind. */}
      {uploadOpen ? (
        <OutstandingUploadDialog
          open
          onOpenChange={setUploadOpen}
          kind="sales-orders"
          onQueued={handleUploadQueued}
        />
      ) : null}

      {/* No Add action while pinned to an agent, so no form to mount either. */}
      {pinnedToAgent ? null : (
        <SalesOrderFormModal
          open={formOpen}
          onOpenChange={setFormOpen}
          onSubmit={handleSubmit}
          isPending={createMut.isPending}
        />
      )}


      {/* KEPT as a dialog where S6b turned single-record deletes into grace windows
          (D7). Two reasons, either of them enough: it acts on a SELECTION of orders,
          and one countdown cannot speak for a set; and it collects an answer
          ("also rewind the book"), which a countdown has nowhere to put. */}
      <ConfirmDeleteDialog
        open={resetOpen}
        onOpenChange={(o) => {
          setResetOpen(o);
          if (!o) setRewindBook(false);
        }}
        title="Reset planning"
        confirmLabel="Reset planning"
        description={
          <span className="flex flex-col gap-3">
            <span>
              Put{' '}
              <span className="font-medium">
                {selectedOrders.map((o) => o.so_number).join(', ')}
              </span>{' '}
              back to never planned? Every order inquiry, link, allocation, stock transfer and
              confirmed revision on {selectedOrders.length === 1 ? 'it' : 'them'} is removed.
              The sales order and its lines stay. This cannot be undone.
            </span>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={rewindBook}
                onChange={(e) => setRewindBook(e.target.checked)}
              />
              <span>
                Also put the lines a sales-order upload changed back to before the first upload
              </span>
            </label>
          </span>
        }
        onDelete={async () => {
          for (const o of selectedOrders) {
            await resetMut.mutateAsync({ id: o.id, rewindBook });
          }
        }}
        successMessage="Planning reset"
        onSuccess={() => {
          setRowSelection({});
          void refetch();
        }}
      />
    </>
  );
}
