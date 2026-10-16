'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { LayoutGrid, PackageSearch, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ConsignmentBadge } from '@/app/(protected)/scm/lib/docType';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateInMalaysia } from '@/lib/helpers';
import {
  useFulfilmentPlanning,
  useFulfilmentPlanningMutations,
} from '../../_shared/hooks/useFulfilmentPlanning';
import { ReviewStatePill } from '../../_shared/components/ReviewStatePill';
import type {
  FulfilmentPlanningRow,
  ReviewState,
} from '../../_shared/types/fulfilmentPlanning.types';
import {
  FULFILMENT_PLANNING_SORT_FIELDS,
  REVIEW_STATE_LABELS,
} from '../../_shared/types/fulfilmentPlanning.types';
import {
  formatOutstandingQty,
  isNotStarted,
  planningRowKey,
  planningRowProjectLabel,
  planningRowReference,
  planningRowSalesOrderHref,
} from '../../_shared/lib/fulfilmentPlanningRows';
import { InfoHint } from '../../[projectId]/components/InfoHint';
import { FulfilmentBoardPanel } from './FulfilmentBoardPanel';
import { FulfilmentPlanningSheet } from './FulfilmentPlanningSheet';
import { PageHeader } from '@/components/common/PageHeader';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

/**
 * How many orders may be planned together (PLAN 13.2).
 *
 * Not arbitrary: the whole book is 862 distinct products across 349 distinct required dates,
 * so a board of everything is roughly 300,000 cells and is not a screen. The bound is part of
 * the design rather than a guard bolted on afterwards.
 */
const MAX_BOARD_SELECTION = 50;

/**
 * Where the board's own "Back" goes. The sales-order list, because that is the screen the
 * Plan action opens a board FROM; the worklist stays exactly where it is and is simply no
 * longer the way back out of a board.
 */
const SALES_ORDERS_PATH = '/scm/sales-orders';

/** What the server can order by, and so the only columns that offer a sort. */
const SORTABLE_COLUMNS = new Set<string>(FULFILMENT_PLANNING_SORT_FIELDS);

/**
 * The order the server ships by default, stated here so the header shows which column is
 * carrying it rather than leaving the sort invisible until somebody presses something.
 */
const DEFAULT_SORTING: SortingState = [{ id: 'earliest_required_date', desc: false }];

const REVIEW_STATE_OPTIONS = [
  { value: 'all', label: 'Any' },
  { value: 'not_started', label: REVIEW_STATE_LABELS.not_started },
  {
    value: 'awaiting_reconciliation',
    label: REVIEW_STATE_LABELS.awaiting_reconciliation,
  },
  { value: 'needs_cs_review', label: REVIEW_STATE_LABELS.needs_cs_review },
  { value: 'confirmed', label: REVIEW_STATE_LABELS.confirmed },
];

/**
 * One worklist of everything that needs planning, one row each (journey step 1).
 *
 * Two kinds of subject share the list: an outstanding project-class CORE sales order out of
 * the AutoCount book, which reads **Not started** until somebody plans it, and a Project SO
 * authored here that has no core order yet. Nobody has to publish anything for the first
 * kind to appear, and no row was written to make it appear.
 *
 * The reading order of the columns is the order the work is judged in: which sales order,
 * for whom, on what project, when it is due, how much is owed. Due date first is why the
 * default order is earliest outstanding required date - the top of the list is the work
 * that is due, not the row that was touched last.
 *
 * The screen is a worklist rather than a report: the review state is what a CS reads down
 * the page, and the row's one action is the way into the order. Nothing here is per line -
 * the whole sales order carries one state (AC-A03), so no column can say "3 of 4 lines
 * confirmed" and "Not started" is a whole-order value.
 */
export function FulfilmentPlanningClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [reviewState, setReviewState] = React.useState('all');
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
  } = useDebouncedSearch();
  const [openRow, setOpenRow] = React.useState<FulfilmentPlanningRow | null>(null);
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});
  /**
   * The board's selection, by sales-order NUMBER, hydrated from the URL (PLAN 13.2).
   *
   * A link past the cap opens NOTHING rather than the first fifty: a set the sender did not
   * choose is not the set they meant to share, which is the same argument that made the cap a
   * stated refusal on the worklist instead of a silent trim.
   */
  const urlOrders = React.useMemo(
    () =>
      (searchParams.get('orders') ?? '')
        .split(',')
        .map((entry) => entry.trim())
        .filter(Boolean),
    [searchParams],
  );
  /**
   * The planning-change batch the board was opened ON (AC-P3-1), from the same URL as the
   * selection: `?orders=SO381895&batch=<id>`. The Planning changes list and the sales-order
   * list's Changed badge both address the board this way, so a board on a change is one link
   * and can be sent to somebody.
   */
  const batchId = React.useMemo(
    () => searchParams.get('batch')?.trim() || null,
    [searchParams],
  );
  /**
   * The board the URL is asking for, or null.
   *
   * THE CAP DOES NOT APPLY TO A BATCH. It exists because a selection the sender did not
   * choose is not the set they meant to share - but a batch IS a chosen set: one book
   * upload, the orders it moved, and the only screen those changes can be decided on. A
   * 60-order upload dead-ended on "too many orders" with nowhere else to go, because the
   * separate batch page is retired.
   */
  const urlBoardOrders = React.useMemo(
    () =>
      urlOrders.length > 0 && (batchId !== null || urlOrders.length <= MAX_BOARD_SELECTION)
        ? urlOrders
        : null,
    [urlOrders, batchId],
  );
  const [boardOrders, setBoardOrders] = React.useState<string[] | null>(urlBoardOrders);
  /**
   * A SECOND deep link, on the route this screen is already on, opens the board it names
   * (R3, the SO400884 walk).
   *
   * `?orders=`/`?batch=` used to be read by a lazy `useState` initialiser, which runs once,
   * at mount - so clicking a "Changed" badge while this screen was open changed the query
   * string and nothing else, and the worklist sat there until somebody reloaded the page.
   * An effect on what the URL asks for follows it instead. It fires only when that ASK
   * changes, so a board opened by a tick (whose own `router.replace` may land a moment
   * later) is not torn down and rebuilt underneath the planner.
   */
  const urlBoardKey = `${urlBoardOrders?.join(',') ?? ''}|${batchId ?? ''}`;
  const lastUrlBoardKey = React.useRef(urlBoardKey);
  React.useEffect(() => {
    if (urlBoardKey === lastUrlBoardKey.current) return;
    lastUrlBoardKey.current = urlBoardKey;
    if (urlBoardOrders) setBoardOrders(urlBoardOrders);
  }, [urlBoardKey, urlBoardOrders]);
  /** A link that asked for too many, so the refusal can be stated on the worklist. */
  const [refusedLink] = React.useState(() =>
    urlOrders.length > MAX_BOARD_SELECTION && batchId === null ? urlOrders.length : 0,
  );
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  // Opened from the URL so a worklist view is shareable, and validated against the closed set:
  // a `sort` the server cannot honour would leave the header showing an order it is not in.
  const [sorting, setSorting] = React.useState<SortingState>(() => {
    const field = searchParams.get('sort');
    if (!field || !SORTABLE_COLUMNS.has(field)) return DEFAULT_SORTING;
    return [{ id: field, desc: searchParams.get('dir') === 'desc' }];
  });

  // Narrowing the set, or re-ordering it, changes which rows are on which page, so page 3 of
  // the old set is a page of nothing in the new one.
  React.useEffect(() => {
    setPagination((current) => ({ ...current, pageIndex: 0 }));
  }, [debounced, reviewState, sorting]);

  // The sort travels in the URL beside whatever else is there, so the view can be linked and
  // reloaded. `replace`, not `push`: sorting a list is not a place in history to go back to.
  React.useEffect(() => {
    const next = new URLSearchParams(searchParams.toString());
    if (sorting[0]) {
      next.set('sort', sorting[0].id);
      next.set('dir', sorting[0].desc ? 'desc' : 'asc');
    } else {
      next.delete('sort');
      next.delete('dir');
    }
    const query = next.toString();
    if (query === searchParams.toString()) return;
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [sorting, pathname, router, searchParams]);

  const planning = useFulfilmentPlanning({
    page: pagination.pageIndex + 1,
    limit: pagination.pageSize,
    query: debounced || undefined,
    review_state: reviewState === 'all' ? undefined : (reviewState as ReviewState),
    sort: sorting[0]?.id,
    dir: sorting[0] ? (sorting[0].desc ? 'desc' : 'asc') : undefined,
  });

  const rows = React.useMemo(() => planning.data?.data ?? [], [planning.data]);
  const { adopt } = useFulfilmentPlanningMutations();

  /**
   * Start planning, then open the order on the record it just created. One press, no form
   * and no confirmation: adoption writes a planning record and its mirror lines and asks
   * nothing else, and it is idempotent, so a second press lands on the same record.
   */
  const startPlanning = React.useCallback(
    async (row: FulfilmentPlanningRow) => {
      if (!row.sales_order_id) return;
      const result = await adopt.mutateAsync(row.sales_order_id).catch(() => null);
      if (!result) return;
      setOpenRow({
        ...row,
        id: result.project_sales_order_id,
        origin: 'adopted',
        provisional_ref: result.so_number,
        autocount_doc_no: result.so_number,
        status: 'adopted',
        review_state: result.review_state,
      });
    },
    [adopt],
  );

  // Column order is the reading order of the worklist: which sales order, for whom, on what
  // project, when it is due, how much is owed, what state it is in, and the one action. The
  // rest is context and can sit past the fold, because a CS scanning the page is answering
  // "what is due and what still needs work".
  const columns = React.useMemo<ColumnDef<FulfilmentPlanningRow>[]>(() => {
    const defs: ColumnDef<FulfilmentPlanningRow>[] = [
      // The repo's own select column, so the header's select-all and its indeterminate state
      // are the ones every other bulk-action list uses rather than a second implementation
      // (the captain: "need to have select all option also at the top left of table").
      {
        ...buildSelectColumn<FulfilmentPlanningRow>({
          // Only a real, outstanding sales order can go on the board: an authored Project SO
          // with no core order yet has no core lines to aggregate.
          enableRow: (row) => Boolean(row.original.so_number),
          disabledReason: () =>
            'Only a sales order from the AutoCount book can go on the board.',
          // A row with no sales order is named generically rather than as "Select null for
          // planning": it is the same rule as never rendering an id, applied to a label.
          rowLabel: (row) =>
            row.original.so_number
              ? `Select ${row.original.so_number} for planning`
              : 'Select row',
        }),
        meta: { headerTitle: 'Select', skeleton: <Skeleton className="h-4 w-4" /> },
      },
      {
        id: 'so_number',
        header: ({ column }) => <DataGridColumnHeader title="Sales order" column={column} />,
        cell: ({ row }) => {
          const reference = planningRowReference(row.original);
          if (!reference) return <span className="text-muted-foreground">Not linked yet</span>;
          const href = planningRowSalesOrderHref(row.original);
          // The identity column is the way to the sales order (the repo's listing idiom), and
          // the row keeps its own meaning: `stopPropagation` so following the link does not
          // also open the planning sheet behind it. A row with nothing to open renders plain
          // text rather than a link that 404s.
          const { so_number: soNumber, planning_change_batch_id: pendingChangeBatchId } =
            row.original;
          return (
            <div className="flex flex-col">
              {href ? (
                <Link
                  href={href}
                  onClick={(event) => event.stopPropagation()}
                  title={reference}
                  className="block truncate font-medium tabular-nums text-primary hover:underline"
                >
                  {reference}
                </Link>
              ) : (
                <span className="block truncate font-medium tabular-nums" title={reference}>
                  {reference}
                </span>
              )}
              <ConsignmentBadge docType={row.original.so_doc_type} size="xs" />
              {/* AC-B7 (`PLAN-scm-board-picks-up-pending-change.md`): the same `Changed` pill
                  the SCM Sales Orders list shows (`SalesOrdersGrid.tsx`), off the same
                  `planning_change_batch_id` - it opens the board on this order and that
                  batch, which is where the change is decided. */}
              {soNumber && pendingChangeBatchId ? (
                <Link
                  data-testid={`so-changed-${soNumber}`}
                  href={`/project-sales/fulfilment-planning?orders=${encodeURIComponent(
                    soNumber,
                  )}&batch=${encodeURIComponent(pendingChangeBatchId)}`}
                  onClick={(event) => event.stopPropagation()}
                  className="mt-0.5 w-fit rounded bg-amber-100 px-1 text-2xs font-medium text-amber-800 hover:underline"
                >
                  Changed
                </Link>
              ) : null}
            </div>
          );
        },
        size: 140,
        minSize: 110,
        meta: { headerTitle: 'Sales order', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'customer_name',
        header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
        cell: ({ row }) =>
          row.original.customer_name ? (
            <span className="block truncate" title={row.original.customer_name}>
              {row.original.customer_name}
            </span>
          ) : (
            <span className="text-muted-foreground">Not recorded</span>
          ),
        size: 180,
        minSize: 140,
        meta: { headerTitle: 'Customer', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        // Who sold it. Not sortable - `agent_code` is not in `FULFILMENT_PLANNING_SORT_FIELDS`
        // - so the map below leaves it a plain header rather than offering a control the
        // server cannot honour.
        id: 'agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        cell: ({ row }) =>
          row.original.agent_code ? (
            <span
              className="block truncate tabular-nums"
              title={row.original.agent_label ?? row.original.agent_code}
            >
              {row.original.agent_code}
            </span>
          ) : (
            <span className="text-muted-foreground">Not stated</span>
          ),
        size: 110,
        minSize: 90,
        meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-14" /> },
      },
      {
        // Named for the field the server sorts on, so the grid's column id, the sort key and
        // the wire field are one word rather than three that have to be kept in step.
        id: 'project_label',
        header: ({ column }) => <DataGridColumnHeader title="Project" column={column} />,
        cell: ({ row }) => {
          const label = planningRowProjectLabel(row.original);
          return (
            <div className="flex min-w-0 flex-col gap-0.5">
              {label ? (
                <span className="block truncate" title={label}>
                  {label}
                </span>
              ) : (
                // An adopted order has no project registration, and the AutoCount book did
                // not always name one either. Say which it is rather than showing a dash.
                <span className="text-muted-foreground">Not named on the order</span>
              )}
              {row.original.project_code ? (
                <span
                  className="block truncate text-xs text-muted-foreground"
                  title={row.original.project_code}
                >
                  {row.original.project_code}
                </span>
              ) : null}
            </div>
          );
        },
        size: 220,
        minSize: 150,
        meta: { headerTitle: 'Project', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'earliest_required_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Earliest delivery" column={column} />
        ),
        cell: ({ row }) =>
          row.original.earliest_required_date ? (
            <span className="block truncate tabular-nums">
              {formatDateInMalaysia(row.original.earliest_required_date)}
            </span>
          ) : (
            <span className="text-muted-foreground">No date</span>
          ),
        size: 140,
        minSize: 120,
        meta: { headerTitle: 'Earliest delivery', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'outstanding_qty',
        header: ({ column }) => <DataGridColumnHeader title="Outstanding" column={column} />,
        cell: ({ row }) => {
          const qty = formatOutstandingQty(row.original.outstanding_qty);
          return qty ? (
            <span className="block truncate tabular-nums" title={qty}>
              {qty}
            </span>
          ) : (
            <span className="text-muted-foreground">Unknown</span>
          );
        },
        size: 120,
        minSize: 100,
        meta: { headerTitle: 'Outstanding', skeleton: <Skeleton className="h-4 w-14" /> },
      },
      {
        id: 'line_count',
        header: ({ column }) => <DataGridColumnHeader title="Lines" column={column} />,
        cell: ({ row }) => {
          // "0 linked / 40" on an order nobody has planned would report a failure that has
          // not happened: there is no mirror to link yet, so the count is the whole story.
          const text = isNotStarted(row.original)
            ? String(row.original.line_count)
            : `${row.original.lines_linked ?? 0} linked / ${row.original.line_count}`;
          return (
            <span className="block truncate tabular-nums" title={text}>
              {text}
            </span>
          );
        },
        size: 120,
        minSize: 105,
        meta: { headerTitle: 'Lines', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        id: 'review_state',
        header: ({ column }) => <DataGridColumnHeader title="Review state" column={column} />,
        cell: ({ row }) => (
          <ReviewStatePill
            state={row.original.review_state}
            exceptionCount={row.original.exception_count}
          />
        ),
        // Wide enough for the longest pill it can hold, "Awaiting reconciliation · 3
        // exceptions": the count is the instruction, and a truncated one is worse than none.
        size: 275,
        minSize: 200,
        meta: { headerTitle: 'Review state', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) =>
          isNotStarted(row.original) ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={adopt.isPending}
              onClick={(event) => {
                // The row itself opens the sheet; this cell is the other decision.
                event.stopPropagation();
                void startPlanning(row.original);
              }}
            >
              <Play className="size-4" aria-hidden />
              Start planning
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={(event) => {
                event.stopPropagation();
                setOpenRow(row.original);
              }}
            >
              Open
            </Button>
          ),
        size: 150,
        minSize: 130,
        enableResizing: false,
        meta: { headerTitle: 'Action', skeleton: <Skeleton className="h-7 w-24" /> },
      },
      {
        id: 'provisional_ref',
        header: ({ column }) => <DataGridColumnHeader title="Project SO" column={column} />,
        cell: ({ row }) =>
          row.original.provisional_ref ? (
            <span className="block truncate tabular-nums" title={row.original.provisional_ref}>
              {row.original.provisional_ref}
            </span>
          ) : (
            <span className="text-muted-foreground">Not authored here</span>
          ),
        size: 150,
        minSize: 120,
        meta: { headerTitle: 'Project SO', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'po_number',
        header: ({ column }) => <DataGridColumnHeader title="Customer PO" column={column} />,
        cell: ({ row }) =>
          row.original.po_number ? (
            <span className="block truncate" title={row.original.po_number}>
              {row.original.po_number}
            </span>
          ) : (
            <span className="text-muted-foreground">None</span>
          ),
        size: 130,
        minSize: 110,
        meta: { headerTitle: 'Customer PO', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'area_group',
        header: ({ column }) => <DataGridColumnHeader title="Area group" column={column} />,
        cell: ({ row }) =>
          row.original.area_group ? (
            <span className="block truncate" title={row.original.area_group}>
              {row.original.area_group}
            </span>
          ) : (
            <span className="text-muted-foreground">No area group</span>
          ),
        size: 130,
        minSize: 110,
        meta: { headerTitle: 'Area group', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated" column={column} />,
        cell: ({ row }) =>
          row.original.updated_at ? (
            <span className="block truncate tabular-nums">
              {formatDateInMalaysia(row.original.updated_at)}
            </span>
          ) : (
            <span className="text-muted-foreground">Unknown</span>
          ),
        size: 130,
        minSize: 100,
        meta: { headerTitle: 'Updated', skeleton: <Skeleton className="h-4 w-20" /> },
      },
    ];

    // Sortability comes from ONE list, applied here, so a column can never offer a sort the
    // server does not do or hide one it does. The selection and action columns carry no
    // server field and so render a plain label instead of a header button.
    return defs.map((column) => {
      const id = String(column.id);
      if (!SORTABLE_COLUMNS.has(id)) return { ...column, enableSorting: false };
      return {
        ...column,
        enableSorting: true,
        // TanStack refuses to sort a column with no accessor, and these are display columns
        // that read `row.original` in their cell. Every sortable id IS a field of the wire
        // row, so the accessor is the honest one rather than a stub - nothing sorts on it
        // here (`manualSorting`), it exists so the header offers the control.
        accessorFn: (row: FulfilmentPlanningRow) =>
          (row as unknown as Record<string, unknown>)[id],
      };
    });
  }, [adopt.isPending, startPlanning]);

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((planning.data?.total ?? 0) / pagination.pageSize) || 0,
    getRowId: planningRowKey,
    state: { pagination, sorting, rowSelection },
    // The PREDICATE lives on the table, which is where TanStack reads `getCanSelect` from -
    // a column-level `enableRowSelection` is silently ignored, and every row would tick.
    enableRowSelection: (row) => Boolean(row.original.so_number),
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    manualPagination: true,
    // The sort is the SERVER's and there is deliberately no `getSortedRowModel`: re-sorting
    // the page here would reorder 25 rows the server picked for page 1 and leave row 26 where
    // it was, so the screen would disagree with its own pagination.
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  const filtersActive = reviewState !== 'all' ? 1 : 0;

  /**
   * Every sales-order number this session has seen, by row key.
   *
   * A board is assembled ACROSS searches: tick SO403765, search for SO396351, tick that too.
   * The tick itself survives, because `rowSelection` is keyed by `planningRowKey` and nothing
   * resets it, but the row it was made on is gone from the page, and the NUMBER only ever
   * arrived with that row. Remembering the pairs as they go past is what lets a tick still
   * name its order once the page has moved on. It only ever grows by rows actually rendered.
   */
  const seenOrderNumbers = React.useRef(new Map<string, string>());

  /**
   * The ticked orders, by NUMBER, which is what a board is addressed with (13.2, never ids).
   *
   * Read off the table's own selection state rather than a parallel array, so ticking a row
   * and ticking the header mean exactly the same thing - but off `rowSelection` itself, NOT
   * `table.getSelectedRowModel()`, which is page-scoped and would forget an order the moment
   * a new search replaced the rows it was ticked on.
   */
  const selected = React.useMemo(() => {
    // Idempotent, and derived only from `rows`: re-running it can add nothing new.
    for (const row of rows) {
      if (row.so_number) seenOrderNumbers.current.set(planningRowKey(row), row.so_number);
    }
    return Object.entries(rowSelection)
      .filter(([, ticked]) => ticked)
      .map(([key]) => seenOrderNumbers.current.get(key))
      .filter((soNumber): soNumber is string => Boolean(soNumber));
  }, [rows, rowSelection]);

  const overCap = selected.length > MAX_BOARD_SELECTION;
  /** How many were asked for, whether by tick or by link. */
  const overCapCount = refusedLink || selected.length;

  /** Open the board and put the selection in the URL, so the view can be sent to somebody. */
  const openBoard = React.useCallback(
    (orders: string[]) => {
      setBoardOrders(orders);
      const next = new URLSearchParams(searchParams.toString());
      next.set('orders', orders.join(','));
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  /**
   * Back to the SALES ORDER LIST, which is where a board is opened from.
   *
   * It used to drop the board out of this URL and land on the worklist underneath it. The
   * board is reached from the sales-order list now (its Plan action), so returning to a
   * different screen than the one the reader left is a step they did not ask for - and the
   * worklist is not going anywhere, it is simply no longer the way back.
   *
   * A board opened FROM the sales-order list carries that list's query, and it rides along
   * (minus the board's own `orders` / `granularity` / `product`) so the reader lands back on
   * the page and filter they left. A board opened from the worklist carries the WORKLIST's
   * query instead - `sort=earliest_required_date` and the rest - which means something else
   * on the sales-order screen, so it is dropped rather than forwarded into a URL where it is
   * a different fact under the same name.
   *
   * `page` / `limit` are what tell the two apart: the sales-order list always writes them
   * (`buildDetailSearch`) and the worklist never does.
   */
  const closeBoard = React.useCallback(() => {
    const next = new URLSearchParams(searchParams.toString());
    const fromSalesOrders = next.has('page') || next.has('limit');
    next.delete('orders');
    next.delete('batch');
    next.delete('granularity');
    next.delete('product');
    const query = fromSalesOrders ? next.toString() : '';
    router.push(query ? `${SALES_ORDERS_PATH}?${query}` : SALES_ORDERS_PATH);
  }, [router, searchParams]);

  // The board replaces the worklist in place rather than sitting under it: they answer the
  // same question at two grains, and showing both at once would ask the reader which one is
  // the current subject. Phase 2 puts the selection in the URL so a board can be linked.
  if (boardOrders) {
    return (
      <FulfilmentBoardPanel soNumbers={boardOrders} batchId={batchId} onBack={closeBoard} />
    );
  }

  return (
    <>
      <PageHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Fulfilment planning
            <InfoHint label="About fulfilment planning">
              Outstanding project sales orders, earliest delivery date first.
            </InfoHint>
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {/* Over the cap, the count is STATED rather than the selection quietly trimmed to
                50: a planner who ticked everything and got a board of the first fifty would be
                planning a set they did not choose, which is the thing 13.2 exists to prevent. */}
            {(overCap || refusedLink > 0) && (
              <span className="text-sm text-destructive break-words">
                {`${overCapCount} ticked. A board takes at most ${MAX_BOARD_SELECTION} sales orders.`}
              </span>
            )}
            {selected.length > 0 && (
              <Button type="button" variant="ghost" size="sm" onClick={() => setRowSelection({})}>
                Clear the selection
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              disabled={selected.length < 1 || overCap}
              title={
                overCap
                  ? `Untick some: a board takes at most ${MAX_BOARD_SELECTION} sales orders`
                  : selected.length < 1
                    ? 'Tick one or more sales orders to plan them'
                    : undefined
              }
              onClick={() => openBoard(selected)}
            >
              <LayoutGrid className="size-4" aria-hidden />
              {selected.length === 1 ? `Plan ${selected[0]}` : `Plan together (${selected.length})`}
            </Button>
          </div>
        }
      />

      <DataGrid
        table={table}
        recordCount={planning.data?.total ?? 0}
        isLoading={planning.isLoading}
        isPlaceholderData={planning.isPlaceholderData}
        // The stable id is bumped because this is not the same listing it was: four columns
        // are new and one is gone, so a config saved against the old set interleaves them
        // into an order nobody chose (verified in the browser: Sales order, Project SO,
        // Review state, action, Lines, ... with Customer and the dates pushed off). A user
        // would have to find Columns -> Reset to get the intended reading order. Bumping
        // the id gives everyone the new defaults once, which is the honest answer to "the
        // columns changed"; anyone who had resized this grid sets it again.
        // v3: two column IDS changed (`project` -> `project_label`, `lines` -> `line_count`)
        // so that a column's id, its sort key and the wire field are one word. A config saved
        // against the old ids would order the grid by names that no longer exist, so everyone
        // takes the new defaults once rather than finding two columns adrift.
        listingKey="projects.projects.view::project-fulfilment-planning-v3"
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        // A not-started row has no planning record to open, and clicking a row must never
        // be the thing that writes one: Start planning is an explicit press.
        onRowClick={(row) => {
          if (row.id) setOpenRow(row);
        }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              exportConfig={false}
              // The selection here is BUILT by searching: two orders worth planning together
              // are rarely on one page, so the box has to survive the first tick.
              keepSearchWhileSelected
              searchSlot={
                <ListSearchInput
                  value={search}
                  onChange={setSearch}
                  isSettling={isSearchInFlight(debouncedSettling, planning.isFetching, debounced)}
                  placeholder="Search sales order, customer, project or product"
                  // The one surprise, as a hint on the box rather than prose on the page: a
                  // product needle matches the ORDER, so the row still counts the whole
                  // order, and only an outstanding line counts as a match.
                  title="A product match lists the whole order that has an outstanding line for it."
                  className="w-full"
                />
              }
              filters={{
                kind: 'custom',
                active: filtersActive > 0,
                activeCount: filtersActive,
                content: (
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">Review state</p>
                    <SearchableSelect
                      value={reviewState}
                      onChange={(value) => setReviewState(value)}
                      options={REVIEW_STATE_OPTIONS}
                    />
                  </div>
                ),
              }}
              onRefresh={async () => {
                await planning.refetch();
              }}
              isRefreshing={planning.isFetching}
            />
          </CardHeader>

          <CardTable>
            {planning.isError ? (
              <div className="px-6 py-10 text-center">
                <h3 className="text-sm font-semibold text-destructive">
                  The planning list could not be loaded
                </h3>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {planning.error instanceof Error
                    ? planning.error.message
                    : 'Try again in a moment.'}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  className="mt-4"
                  onClick={() => planning.refetch()}
                >
                  Try again
                </Button>
              </div>
            ) : !planning.isLoading && rows.length === 0 ? (
              <div className="px-6 py-10 text-center">
                <PackageSearch className="mx-auto size-6 text-muted-foreground" aria-hidden />
                <h3 className="mt-2 text-sm font-semibold">
                  {reviewState === 'not_started'
                    ? 'Every outstanding sales order is being planned'
                    : reviewState === 'needs_cs_review'
                      ? 'No sales order is waiting on a decision'
                      : reviewState === 'awaiting_reconciliation'
                        ? 'Every sales order here is reconciled'
                        : reviewState === 'confirmed'
                          ? 'No sales order has been confirmed yet'
                          : 'Nothing is outstanding'}
                </h3>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {reviewState === 'all'
                    ? 'An outstanding project sales order appears here on its own, with no upload and nothing to publish.'
                    : 'Clear the review state filter to see the rest of the sales orders.'}
                </p>
                <Button asChild variant="outline" className="mt-4">
                  <Link href="/scm/sales-orders">Open the sales order book</Link>
                </Button>
              </div>
            ) : (
              <DataGridTable />
            )}
          </CardTable>

          {(planning.data?.total ?? 0) > pagination.pageSize && (
            <CardFooter>
              <DataGridPagination />
            </CardFooter>
          )}
        </Card>
      </DataGrid>

      <FulfilmentPlanningSheet
        row={openRow}
        open={Boolean(openRow)}
        onOpenChange={(next) => {
          if (!next) setOpenRow(null);
        }}
      />
    </>
  );
}
