'use client';

import * as React from 'react';
import {
  ColumnDef,
  ExpandedState,
  OnChangeFn,
  PaginationState,
  Row,
  RowSelectionState,
  SortingState,
  Table,
  getCoreRowModel,
  getExpandedRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { ListLoadFailure } from '@/components/common/ListLoadFailure';

/**
 * The system list, inside a detail tab.
 *
 * Every tab on a project or lead was showing its rows a different way: a `ul` with a
 * divider here, a grid of bordered cards there, a bespoke timeline somewhere else. The
 * client's words: "use our system design principle for the list" and "don't use this out
 * of system design punya list". A record read as a card in one tab and a row in the next
 * teaches nothing that carries over.
 *
 * So all of them render THIS, which is the same `DataGrid` the top-level listings use, with
 * the contract from ARCHITECTURE-RULES already applied: fixed table layout, resizable
 * columns, `onChange` resize mode, and the same pagination bar as the user list -- "1 - 1 of 1",
 * a page picker and a rows-per-page selector, shown WHENEVER there are rows. It was previously
 * hidden below one page, which is exactly what made a short list look like a different component
 * from the long one. Callers supply columns with explicit sizes, and nothing else.
 *
 * The row is the way in (ADR 1d), so pass `onRowClick` rather than adding an action column.
 */
export function PanelDataGrid<TRow extends object>({
  title,
  toolbar,
  columns,
  rows,
  getRowId,
  listingKey,
  isLoading = false,
  error,
  onRetry,
  emptyTitle,
  emptyBody,
  onRowClick,
  rowClassName,
  rowAttributes,
  searchPlaceholder,
  searchOf,
  renderGroupHeader,
  sortable = false,
  onSortedRowsChange,
  rowSelection,
  onRowSelectionChange,
  enableRowSelection,
  expanded,
  onExpandedChange,
  pageSize = 10,
  paginate = true,
  scrollerMaxHeight,
  pageResetKey,
  focusRowId,
  focusRequestKey,
  initialColumnVisibility,
}: {
  /**
   * A plain heading, or a heading with an embedded link (e.g. the record's own number).
   *
   * OPTIONAL, for a grid already titled by what it opened from: the stock drill expands
   * under a location row that names the product and the bin, and repeating that above the
   * columns pushed the headers a line and a half away from the row they explain (captain,
   * 30 August 2026). With no title, no toolbar and no search the card header is not
   * rendered at all, so the column headers sit directly under that row.
   */
  title?: React.ReactNode;
  /**
   * Filters, view switches and the Add button. Sits in the card header beside the title. Or a
   * function receiving the grid's own `table`, for a caller that mounts the app's list toolbar
   * (`DataGridListToolbar`), which needs it for Columns and the bulk strip.
   */
  toolbar?: React.ReactNode | ((slots: { table: Table<TRow> }) => React.ReactNode);
  columns: ColumnDef<TRow>[];
  rows: TRow[];
  getRowId?: (row: TRow) => string;
  /** Drives per-user column order and visibility. See docs/LISTING-COLUMN-PREFERENCES.md. */
  listingKey: string;
  isLoading?: boolean;
  /** The read's error. With no rows it renders no-access or message + Retry (lever L2). */
  error?: unknown;
  /** Refetch the rows; shown as Retry beside a non-refusal `error`. */
  onRetry?: () => void;
  emptyTitle: string;
  /** One short line at most. A tab is not the place to explain the feature (ADR 1e). */
  emptyBody?: string;
  /**
   * No slot: an empty state is heading and hint only, and the page's one
   * primary CTA lives in its header (owner ruling, PR #1336, CRM-wide).
   */
  emptyAction?: never;
  onRowClick?: (row: TRow) => void;
  /**
   * Extra classes layered onto a row (the same idiom `DataGrid`'s own `rowClassName`
   * already gives the top-level listings, e.g. a picker tinting the rows a clicked
   * week fell in) - the PO lightbox uses it to highlight the line an opening row's
   * link actually sits on.
   */
  rowClassName?: (row: TRow) => string | undefined;
  /** Extra DOM attributes for a row, alongside `rowClassName` - kept separate so a test
   * asserting a row's identity does not have to assert a CSS class string to do it. */
  rowAttributes?: (row: TRow) => Record<string, string | undefined>;
  /** Shown in the search box. Omit both search props for a list too short to need one. */
  searchPlaceholder?: string;
  /**
   * The text a search matches against, per row. Client-side on purpose: a detail tab holds
   * one project's rows, so there is nothing to page through on the server, and filtering in
   * the browser answers instantly.
   */
  searchOf?: (row: TRow) => string;
  /**
   * A band heading rendered above a row when it opens a new section - the quotation's
   * "BILL NO 3 PAGE 15/4". Return null for a row that continues the band it is already in.
   * Passed straight to the shared grid, so bands are a rendering of the row order rather than
   * a second list living beside it.
   */
  renderGroupHeader?: (row: TRow, previousRow: TRow | null) => React.ReactNode | null;
  /**
   * Let the reader sort the rows, client-side.
   *
   * OPT-IN, and deliberately not the default: a panel holds one record's rows in a meaningful
   * order (the allocation ranking, the quotation's bill order), and turning sorting on
   * everywhere would put a live control on every header of fifteen existing panels that never
   * asked for one. The initial order is always the order the caller passed.
   */
  sortable?: boolean;
  /**
   * The rows in the grid's own live order - sorted, then filtered, never paginated - each time
   * that order changes (Should fix 3, review round 1, PR #1218 S3). OPTIONAL: a caller whose
   * own bulk action has to claim contested stock in the order the reader currently SEES has no
   * other way to read that order back, since `sorting` is this grid's own state. Omitted by
   * every other panel, which never asked for it.
   */
  onSortedRowsChange?: (rows: TRow[]) => void;
  /**
   * Row selection, for a panel that offers bulk actions.
   *
   * The STATE is the caller's, because the actions are: a panel that owns a selection but not
   * the verbs that act on it can only hand the selection back, which is the same thing with an
   * extra step. Pass `buildSelectColumn(...)` as the first column and render the strip in
   * `toolbar`, exactly as the users list does.
   */
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: React.Dispatch<React.SetStateAction<RowSelectionState>>;
  /** Which rows may be ticked. TanStack reads this from the TABLE, never from the column. */
  enableRowSelection?: (row: Row<TRow>) => boolean;
  /**
   * Which row is open in place, and the setter for it.
   *
   * The STATE is the caller's for the same reason the selection is: what the drawer holds
   * belongs to the screen that opened it, and a panel that owned the expansion but not the
   * editor inside it could only hand the row back. Pair it with `meta.expandedContent` on
   * one column - the shared `DataGridTable` renders that full-width under any expanded row -
   * and toggle it from `onRowClick`, the way reorder planning's `PlanLinesGrid` does.
   *
   * Opt-in: without it the expanded row model is never built, so fifteen existing panels
   * keep the exact table they have today.
   */
  expanded?: ExpandedState;
  onExpandedChange?: OnChangeFn<ExpandedState>;
  pageSize?: number;
  /**
   * SF-8 (M5 run 3 review): `false` renders every row and hides the pager - the
   * ruling for a line table ON A DOCUMENT (an order's own lines, a GRN's own
   * picking lines), where a page-2 hides rows the reader expects to see in one
   * scroll. Default `true` (the 10-row page every other panel keeps) is
   * unaffected - this is opt-in per caller, not a change to the shared default.
   */
  paginate?: boolean;
  /**
   * Pass `false` when the caller already renders this inside a `DialogBody` or
   * `SheetBody` that owns its own `overflow-y-auto` viewport (B2, M5 review run 1) -
   * without it the grid's own M5-05 bounded scroller nests a second scrollport
   * inside the first. Omit it for a plain detail-page tab panel, which wants the
   * default bounded, sticky-header scroller like every other list.
   */
  scrollerMaxHeight?: string | false;
  /**
   * Reset the page to 1 when this value changes.
   *
   * OPTIONAL, for a caller that filters `rows` itself before handing them
   * here (no `searchOf`): this grid's own search box resets the page on
   * every keystroke, but a parent-side filter changes `rows`' CONTENTS, not
   * a value this component can see, so typing on page 3 would otherwise show
   * whatever landed on page 3 of the new matches instead of the top ones.
   * Pass the parent's own filter key (its search text, its dropdown value,
   * or both joined) and a change resets the page the same way. Omit it for a
   * grid with no external filter, or one that filters through `searchOf`,
   * which already resets itself.
   */
  pageResetKey?: string;
  /**
   * Jump to the PAGE holding this row id (`getRowId`'s own id), in the row order paging
   * itself already reads - sorted, then filtered, before pagination slices it. A caller
   * whose own link names a row rather than a page (`FulfilmentBoardPanel`'s left-out banner,
   * board-confirm-left-out AC-5) sets this instead of computing a page index by hand, which
   * would need to duplicate whatever sort this grid is currently under.
   */
  focusRowId?: string | null;
  /**
   * Review round 2 Should fix 1 (R16): re-fire the jump above for the SAME
   * `focusRowId`, on demand. `jumpedFocusToken` below only skips a jump it has
   * already made for a given id - correct for a `focusRowId` that changes, but a
   * "Go to" button always names the same highlighted line, so a second press
   * after the reader had paged away did nothing at all (the id had not changed,
   * so the guard fired). Bump a counter (or any other value) on every press and
   * pass it here; a change clears the guard so the jump effect runs again even
   * though `focusRowId` itself is unchanged. Omit it for a caller with nothing
   * to re-press, which keeps today's once-per-id behaviour exactly as it is.
   */
  focusRequestKey?: string | number;
  /** Columns hidden until the reader turns them on (a saved column preference still wins). */
  initialColumnVisibility?: Record<string, boolean>;
}) {
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize,
  });
  const [search, setSearch] = React.useState('');
  const [sorting, setSorting] = React.useState<SortingState>([]);

  const filtered = React.useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle || !searchOf) return rows;
    return rows.filter((row) => searchOf(row).toLowerCase().includes(needle));
  }, [rows, search, searchOf]);

  const pageCount = paginate ? Math.ceil(filtered.length / pagination.pageSize) || 0 : 1;
  // Clamped for THIS render, not in an effect: a row removal (a save, a
  // Confirm refetch) can leave `pageIndex` past the last page that still
  // exists, and an effect would still commit one paint at the stale index
  // first - showing "No data available" before it lands.
  const lastPageIndex = Math.max(0, pageCount - 1);
  const tablePagination: PaginationState = paginate
    ? { ...pagination, pageIndex: Math.min(pagination.pageIndex, lastPageIndex) }
    : pagination;

  // The render clamp above is only a PAINT: `pagination` (the real state
  // `table.previousPage()`/`nextPage()` step from) is still left at the
  // stale index, so a previous-page press after a shrink computes its new
  // page from the wrong number and looks like a dead click. Persist the
  // same clamp into state once the render settles, so they agree again.
  React.useEffect(() => {
    if (!paginate) return;
    if (pagination.pageIndex > lastPageIndex) {
      setPagination((current) => ({ ...current, pageIndex: lastPageIndex }));
    }
  }, [paginate, lastPageIndex, pagination.pageIndex]);

  // A parent-side filter (see `pageResetKey`'s doc) resets the page by
  // changing this value instead of `rows`. Skips the render that mounts it,
  // since page 0 is already where a fresh mount starts.
  const isFirstPageResetRender = React.useRef(true);
  React.useEffect(() => {
    if (isFirstPageResetRender.current) {
      isFirstPageResetRender.current = false;
      return;
    }
    setPagination((current) => ({ ...current, pageIndex: 0 }));
  }, [pageResetKey]);

  const table = useReactTable({
    columns: columns as ColumnDef<TRow, unknown>[],
    data: filtered,
    pageCount,
    getRowId,
    // TanStack's own default resets `pageIndex` to 0 on every new `data`
    // reference (a draft save, a Confirm refetch) even though the reader
    // never touched the page - see PLAN-panel-datagrid-keep-page.md. The
    // render-time clamp above is the only reset this grid wants for that.
    autoResetPageIndex: false,
    ...(initialColumnVisibility
      ? { initialState: { columnVisibility: initialColumnVisibility } }
      : {}),
    state: {
      pagination: tablePagination,
      ...(sortable ? { sorting } : {}),
      ...(rowSelection ? { rowSelection } : {}),
      ...(expanded === undefined ? {} : { expanded }),
    },
    ...(rowSelection
      ? {
          onRowSelectionChange,
          enableRowSelection: enableRowSelection ?? true,
        }
      : {}),
    onPaginationChange: setPagination,
    ...(sortable
      ? { onSortingChange: setSorting, getSortedRowModel: getSortedRowModel() }
      : { enableSorting: false }),
    ...(expanded === undefined
      ? {}
      : { onExpandedChange, getExpandedRowModel: getExpandedRowModel() }),
    getCoreRowModel: getCoreRowModel(),
    // `paginate={false}` renders EVERY row by leaving the pagination row model
    // off entirely: without it TanStack's `getRowModel()` is the pre-pagination
    // model, so nothing is sliced and the page size is never read. This used to
    // be an oversized `pageSize` instead, and that is exactly what made the
    // shared loading skeleton - which draws `pageSize` rows - throw
    // `RangeError: Invalid array length` on every mount that still had
    // preferences resolving.
    ...(paginate ? { getPaginationRowModel: getPaginationRowModel() } : {}),
    columnResizeMode: 'onChange',
  });

  // Should fix 3 (review round 1): sorted, then filtered, never paginated - the same order
  // `focusRowId` above already reads paging by. Fires only when a caller asked for it.
  // `getSortedRowModel()`'s own ROWS are TanStack's memoized reference (stable while nothing
  // sorting-relevant moved), so deriving off THAT rather than re-mapping on every render is
  // what keeps this from re-firing, and re-rendering the caller, every single paint.
  const sortedRows = sortable ? table.getSortedRowModel().rows : null;
  const sortedOriginals = React.useMemo(
    () => (sortedRows ? sortedRows.map((row) => row.original) : filtered),
    [sortedRows, filtered],
  );
  React.useEffect(() => {
    onSortedRowsChange?.(sortedOriginals);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortedOriginals]);

  /**
   * Which `focusRowId` (paired with the `focusRequestKey` it fired under) the jump
   * below has already fired for, so a re-render (or a later arrival, S5 just below)
   * does not repeat it - the same ref shape the list's own scroll effect uses
   * (`FulfilmentBoardListView`, S3). Review round 2 Should fix 1 (R16): the token
   * carries `focusRequestKey` alongside the id, not the id alone - a "Go to" button
   * always names the SAME `focusRowId`, so without the request key a second press
   * after the reader had paged away matched this guard and did nothing at all.
   */
  const jumpedFocusToken = React.useRef<string | null>(null);

  // `getPrePaginationRowModel` is filtered-then-sorted, exactly what paging itself slices -
  // no second implementation of sorting here, and it stays right if a column is later sorted
  // ascending/descending while a jump is pending.
  //
  // DECLARED AFTER the `pageResetKey` effect above (nit, fix round 2 review): React runs one
  // component's own `useEffect`s in DECLARATION order on a commit where both fire - `focusKey`
  // and `pageResetKey` (a parent-side search clearing, `FulfilmentBoardListView`'s own B1 fix)
  // can change in the SAME commit, and the pageReset effect's `setPagination(... pageIndex: 0)`
  // would otherwise win the race and strand the jump on page 1. Declared after it, THIS one's
  // `setPagination` is the later call in that flush, so its target page is what survives.
  //
  // S5 (fix round 3, reviewer): `filtered` is an explicit dependency, not `focusRowId` alone -
  // a row named before the CALLER'S OWN data caught up to it (a fresher board read landing a
  // beat after the click that asked for it) used to leave the page at 0 forever: `focusRowId`
  // itself never changed again, so nothing re-ran this once the row actually arrived.
  // `index === -1` returns WITHOUT touching the ref, so the NEXT render that changes
  // `filtered` gets another try; the ref is only set once a page is actually chosen, so a row
  // already on the right page from the start does not re-jump on every unrelated data refresh.
  React.useEffect(() => {
    if (!focusRowId) {
      jumpedFocusToken.current = null;
      return;
    }
    // Should fix 1 (R16): `focusRequestKey` joins the token, so bumping it alone -
    // `focusRowId` unchanged - is itself a reason to re-run this effect and jump
    // again, the way a genuinely new `focusRowId` already did.
    const token = `${focusRowId}::${focusRequestKey ?? ''}`;
    if (!paginate || jumpedFocusToken.current === token) return;
    const rows = table.getPrePaginationRowModel().rows;
    const index = rows.findIndex((row) => row.id === focusRowId);
    if (index === -1) return;
    jumpedFocusToken.current = token;
    const targetPage = Math.floor(index / pagination.pageSize);
    setPagination((current) =>
      current.pageIndex === targetPage ? current : { ...current, pageIndex: targetPage },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRowId, filtered, focusRequestKey]);

  return (
    <DataGrid
      table={table}
      recordCount={filtered.length}
      isLoading={isLoading}
      listingKey={listingKey}
      tableLayout={{
        width: 'fixed',
        columnsResizable: true,
        // Most callers are a plain detail-page tab and want M5-05's own bounded,
        // sticky-header scroller. A caller embedded in a `DialogBody`/`SheetBody`
        // that already owns the scroll viewport passes `false` here instead, or
        // that ancestor's own `overflow-y-auto` would double-bound it.
        scrollerMaxHeight,
      }}
      onRowClick={onRowClick}
      rowClassName={rowClassName}
      rowAttributes={rowAttributes}
      renderGroupHeader={renderGroupHeader as never}
    >
      <Card>
        {/* flex-col until sm so a title and a toolbar never overlap at phone width. Not
            rendered at all when there is nothing to put in it, so a grid titled by the row
            it expanded from starts at its own column headers. */}
        {(title || toolbar || searchOf) && (
          <CardHeader className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            {title ? (
              <CardTitle className="min-w-0 break-words text-sm">{title}</CardTitle>
            ) : null}
            <div
              className={`flex w-full flex-wrap items-center gap-2 ${
                typeof toolbar === 'function' ? 'min-w-0 sm:grow' : 'sm:w-auto'
              }`}
            >
              {searchOf && (
                <Input
                  type="search"
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    // Back to page one: filtering while on page three shows an empty table.
                    setPagination((current) => ({ ...current, pageIndex: 0 }));
                  }}
                  placeholder={searchPlaceholder ?? 'Search…'}
                  aria-label={searchPlaceholder ?? 'Search'}
                  className="h-8 w-full sm:w-56"
                />
              )}
              {typeof toolbar === 'function' ? toolbar({ table }) : toolbar}
            </div>
          </CardHeader>
        )}

        <CardTable>
          {error && rows.length === 0 && !isLoading ? (
            <div className="px-5 py-6 text-sm">
              <ListLoadFailure error={error} onRetry={onRetry} />
            </div>
          ) : isLoading ? (
            <div className="space-y-2 p-5">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : filtered.length === 0 ? (
            // Rendered rather than hidden, per the CRUD standard: a tab that vanishes when
            // empty makes the feature look absent instead of unused.
            <div className="px-6 py-10 text-center">
              <h3 className="text-sm font-semibold">
                {rows.length > 0 ? 'Nothing matches that search' : emptyTitle}
              </h3>
              {emptyBody && (
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {emptyBody}
                </p>
              )}
            </div>
          ) : (
            <DataGridTable />
          )}
        </CardTable>

        {paginate && filtered.length > 0 && (
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        )}
      </Card>
    </DataGrid>
  );
}
