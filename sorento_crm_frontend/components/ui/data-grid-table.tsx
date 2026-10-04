'use client';

import * as React from 'react';
import { CSSProperties, Fragment, ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import type { DragEndEvent } from '@dnd-kit/core';
import { arrayMove } from '@dnd-kit/sortable';
import { Checkbox } from '@/components/ui/checkbox';
import { ListLoadFailure } from '@/components/common/ListLoadFailure';
import { useDataGrid } from '@/components/ui/data-grid';
import { DataGridTableDnd } from '@/components/ui/data-grid-table-dnd';
import { Cell, Column, flexRender, Header, HeaderGroup, Row, Table } from '@tanstack/react-table';
import { cva } from 'class-variance-authority';
import { mergeColumnOrderWithLeafColumns } from '@/lib/listing-column-preferences/mergeColumnOrder';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { toAbsoluteUrl } from '@/lib/helpers';
import { useHorizontalOverflow } from '@/hooks/use-horizontal-overflow';
import { usePrefetchOnce } from '@/hooks/usePrefetchOnce';
import { cn } from '@/lib/utils';

/**
 * The id a row is named by for Back-to-list restore (M5-07, `from=`).
 *
 * ONE function for both directions - `appendListState` writes it into the
 * detail href, `DataGridTable` reads it back on mount to find the row again -
 * so read and write cannot silently name different things. Resolution order:
 *
 *   1. `row.id`, when the table itself resolves row identity (`getRowId` is
 *      set) - the caller's own stable key, so it wins over anything else.
 *   2. `row.original.id`, the record's own id field.
 *   3. the last path segment of the row's own href - what every `rowHref` in
 *      this app already embeds (`/module/entity/${id}`), for the rare row
 *      whose shape carries neither. Caveat (S6, M5 review run 1): this
 *      assumes the id is the LAST segment - a `rowHref` that ends in a
 *      static word instead (`/module/${id}/edit`) would tag every row with
 *      that same word, so this fallback only holds for the id-last shape
 *      every current `rowHref` in the app uses.
 *
 * Returns `undefined` when none of the three resolves anything, which makes
 * both callers no-ops rather than tagging a row with an empty string.
 */
function rowRestoreId<TData>(
  table: Table<TData>,
  row: Row<TData>,
  href?: string,
): string | undefined {
  if (typeof table.options.getRowId === 'function') return row.id;
  const original = row.original as { id?: unknown };
  if (typeof original?.id === 'string' && original.id) return original.id;
  if (typeof original?.id === 'number') return String(original.id);
  if (!href) return undefined;
  const path = href.split('?')[0].split('#')[0];
  const segments = path.split('/').filter(Boolean);
  return segments.length ? segments[segments.length - 1] : undefined;
}

/**
 * Splits a href into its path, query string (no leading `?`) and fragment
 * (leading `#` kept, or `''`).
 *
 * A fragment has to survive, and it sits AFTER the query string - splitting on
 * '?' alone turns `/orders/a1#lines` into a param named `a1#lines`. Shared by
 * `appendListState` and `LinkableBodyRow`'s history rewrite (M5-07) so both
 * parse a detail href the same way.
 */
function splitHref(href: string): { path: string; search: string; hash: string } {
  const hashAt = href.indexOf('#');
  const hash = hashAt === -1 ? '' : href.slice(hashAt);
  const [path, search = ''] = (hashAt === -1 ? href : href.slice(0, hashAt)).split('?');
  return { path, search, hash };
}

/**
 * The only params `LinkableBodyRow`'s history rewrite (M5-07/BL-2) is allowed
 * to touch on the LIST's own URL. Everything else already there (a filter the
 * list reads off its own URL but never echoes into `rowHref` - e.g. GRNList's
 * `spo_allocation_id`) survives untouched. `from` is the M5-07 restore id.
 */
const RESERVED_LIST_STATE_KEYS = ['page', 'limit', 'sort', 'dir', 'query', 'advFilter', 'from'];

/** The list's own page/sort/query state, in the param shape `appendListState` writes into a detail href. */
function listStateParams<TData>(table: Table<TData>): URLSearchParams {
  const state = table.getState();
  return new URLSearchParams(
    buildDetailSearch({
      pageIndex: state.pagination?.pageIndex ?? 0,
      pageSize: state.pagination?.pageSize ?? 50,
      sorting: state.sorting,
      searchQuery: typeof state.globalFilter === 'string' ? state.globalFilter : '',
    }),
  );
}

/**
 * Appends the list state the grid is showing to a row's detail href.
 *
 * The detail page's pager walks the page the user came FROM, so the URL has to
 * name that page. Param names come from `buildDetailSearch` (the same builder
 * the list GET uses), and anything the caller put in its own query string wins -
 * that is where a filter the list keeps outside TanStack rides along.
 *
 * Also appends `from=<row id>` (M5-07, `rowRestoreId`) so the list can scroll
 * this exact row back into view and highlight it when the reader returns.
 */
function appendListState<TData>(href: string, table: Table<TData>, row: Row<TData>): string {
  const { path, search: ownSearch, hash } = splitHref(href);
  const params = listStateParams(table);
  if (ownSearch) {
    for (const [key, value] of new URLSearchParams(ownSearch)) params.set(key, value);
  }
  const restoreId = rowRestoreId(table, row, href);
  if (restoreId) params.set('from', restoreId);
  return `${path}?${params.toString()}${hash}`;
}

const headerCellSpacingVariants = cva('', {
  variants: {
    size: {
      dense: 'px-2.5 h-8',
      default: 'px-4',
    },
  },
  defaultVariants: {
    size: 'default',
  },
});

const bodyCellSpacingVariants = cva('', {
  variants: {
    size: {
      dense: 'px-2.5 py-2',
      // Roomier default row spacing - every CRM list matches the Administrative
      // Users density. `h-11` (44px) acts as a row MIN-height in tables (cells are
      // align-middle), so single-line rows get the same breathing room as the
      // avatar rows without inflating the avatar rows themselves.
      default: 'px-4 py-2 h-11',
    },
  },
  defaultVariants: {
    size: 'default',
  },
});

function getPinningStyles<TData>(column: Column<TData>): CSSProperties {
  const isPinned = column.getIsPinned();

  return {
    left: isPinned === 'left' ? `${column.getStart('left')}px` : undefined,
    right: isPinned === 'right' ? `${column.getAfter('right')}px` : undefined,
    position: isPinned ? 'sticky' : 'relative',
    width: column.getSize(),
    // `--z-sticky-content` (css/config.reui.css), not a bare number: a pinned
    // column has to beat the columns scrolling under it, but stay below the
    // app shell's fixed header/sidebar, or the collapsed sidebar's hover
    // flyout renders under a frozen column instead of over it.
    zIndex: isPinned ? 'var(--z-sticky-content)' : 0,
  };
}

function DataGridTableBase({ children }: { children: ReactNode }) {
  const { props, table } = useDataGrid();

  // What stops a `table-fixed w-full` grid from squeezing six columns into a
  // phone: the table is at least as wide as its columns want to be, and the
  // scroller (data-grid-scroller) carries the overflow. Where the columns
  // already fit, `w-full` still wins.
  //
  // The grid has to be the ONLY scrollport on that axis. A list that wrapped it
  // in a Radix `ScrollArea` gave the table a `display: table` ancestor, which
  // shrink-fits: `data-grid-scroller` then measured scrollWidth === clientWidth,
  // never overflowed, and still swallowed the wheel gesture through
  // `overscroll-x-contain` - so 161 lists could not be scrolled sideways at all.
  // Those wrappers are gone; keep it that way.
  //
  // It has to be a DEFINITE length. `min-width: max-content` is meaningless on a
  // `table-layout: fixed` table - fixed layout ignores content by design - and
  // Chrome resolves it to its "infinite" sentinel of 1,000,000px, then the fixed
  // algorithm scales every column up to fill it. Measured on Products at
  // 1280x800: columns summing to 2367px laid out 1,000,000px wide, each column
  // 422x its size, the last header at x=962,282 and nothing but the checkbox on
  // screen. `getTotalSize()` is the sum of the visible leaf column sizes, so it
  // is that same width as a number the browser has nothing to resolve - and it
  // tracks a column the user has resized, or one restored from their saved
  // listing preferences.
  const minWidth = table.getTotalSize();

  return (
    <table
      data-slot="data-grid-table"
      style={minWidth > 0 ? { minWidth: `${minWidth}px` } : undefined}
      className={cn(
        // `tabular-nums` keeps figures aligned down a column.
        'w-full tabular-nums align-middle caption-bottom text-left rtl:text-right text-foreground font-normal text-sm',
        !props.tableLayout?.columnsDraggable && 'border-separate border-spacing-0',
        props.tableLayout?.width === 'fixed' ? 'table-fixed' : 'table-auto',
        props.tableClassNames?.base,
      )}
    >
      {children}
    </table>
  );
}

function DataGridTableHead({ children }: { children: ReactNode }) {
  const { props } = useDataGrid();

  return (
    <thead
      className={cn(
        props.tableClassNames?.header,
        props.tableLayout?.headerSticky && props.tableClassNames?.headerSticky,
      )}
    >
      {children}
    </thead>
  );
}

function DataGridTableHeadRow<TData>({
  children,
  headerGroup,
}: {
  children: ReactNode;
  headerGroup: HeaderGroup<TData>;
}) {
  const { props } = useDataGrid();

  return (
    <tr
      key={headerGroup.id}
      className={cn(
        'bg-muted/40',
        props.tableLayout?.headerBorder && '[&>th]:border-b',
        props.tableLayout?.cellBorder && '[&_>:last-child]:border-e-0',
        props.tableLayout?.stripped && 'bg-transparent',
        props.tableLayout?.headerBackground === false && 'bg-transparent',
        props.tableClassNames?.headerRow,
      )}
    >
      {children}
    </tr>
  );
}

/**
 * A grid with column GROUPS repeats every ungrouped column twice: TanStack puts a
 * placeholder in the group row and the real header in the leaf row underneath, which draws
 * an empty band above most of the header. The workbook the reports grid mirrors merges
 * those cells vertically, so here the PLACEHOLDER carries the header and spans the
 * remaining rows, and `skipMergedLeafHeader` drops the leaf it stands in for.
 *
 * A flat listing has one header row and therefore no placeholders at all, so both helpers
 * are no-ops on every other listing in the app.
 */
function headerRowSpan<TData>(header: Header<TData, unknown>, rowCount: number): number {
  // `header.depth` is 1-based: the first header row is depth 1.
  return header.isPlaceholder ? Math.max(rowCount - (header.depth - 1), 1) : 1;
}

function skipMergedLeafHeader<TData>(header: Header<TData, unknown>, rowCount: number): boolean {
  if (rowCount <= 1) return false; // a flat listing: nothing to merge
  if (header.column.parent) return false; // a grouped column keeps its own cell
  if (header.subHeaders.length > 0 && !header.isPlaceholder) return false; // a real group header
  // What is left is an ungrouped column below the first header row: the placeholder in
  // that first row already carries it, spanning down to here.
  return header.depth > 1;
}

function DataGridTableHeadRowCell<TData>({
  children,
  header,
  dndRef,
  dndStyle,
  dndDragging,
  rowSpan,
}: {
  children: ReactNode;
  header: Header<TData, unknown>;
  dndRef?: React.Ref<HTMLTableCellElement>;
  dndStyle?: CSSProperties;
  /** True while THIS column is the one being dragged (dnd-kit's `isDragging`). */
  dndDragging?: boolean;
  rowSpan?: number;
}) {
  const { props } = useDataGrid();

  const { column } = header;
  const isPinned = column.getIsPinned();
  const isLastLeftPinned = isPinned === 'left' && column.getIsLastColumn('left');
  const isFirstRightPinned = isPinned === 'right' && column.getIsFirstColumn('right');
  const headerCellSpacing = headerCellSpacingVariants({
    size: props.tableLayout?.dense ? 'dense' : 'default',
  });

  return (
    <th
      key={header.id}
      ref={dndRef}
      // 1 for every flat listing, so this is a no-op there. A grid whose columns are
      // grouped (the reports detail grid's "Expected year of delivery" ticks) needs the
      // group header to span its leaves, or the two header rows do not line up.
      colSpan={header.colSpan}
      // 1 on every flat listing. A single-level header in a GROUPED grid spans both header
      // rows instead of leaving an empty cell above itself (see headerRowSpan).
      rowSpan={rowSpan && rowSpan > 1 ? rowSpan : undefined}
      style={{
        ...(props.tableLayout?.width === 'fixed' && {
          width: `${header.getSize()}px`,
        }),
        // Pinning normally wins. Column drag-and-drop is on by default and
        // dnd-kit sets `position: relative` + `zIndex: 0` on every cell; spread
        // after the pinning styles it silently turned a pinned column back into
        // an ordinary one that scrolled away. The drag transform and transition
        // survive - only the stickiness wins.
        //
        // Driven by the pinned state itself, so it follows whatever the list
        // pinned rather than assuming a column.
        //
        // While THIS column is the one being dragged, the order flips: sticky
        // beats dnd-kit's `position: relative`, so the transform had no effect
        // and a pinned column could not be dragged at all.
        ...(dndDragging
          ? { ...(isPinned ? getPinningStyles(column) : null), ...dndStyle }
          : { ...dndStyle, ...(isPinned ? getPinningStyles(column) : null) }),
      }}
      data-pinned={isPinned || undefined}
      data-last-col={isLastLeftPinned ? 'left' : isFirstRightPinned ? 'right' : undefined}
      className={cn(
        'relative h-10 text-left rtl:text-right align-middle font-normal text-accent-foreground [&:has([role=checkbox])]:pe-0',
        headerCellSpacing,
        props.tableLayout?.cellBorder && 'border-e',
        props.tableLayout?.columnsResizable && column.getCanResize() && 'truncate',
        isPinned &&
          '[&:not([data-pinned]):has(+[data-pinned])_div.cursor-col-resize:last-child]:opacity-0 [&[data-last-col=left]_div.cursor-col-resize:last-child]:opacity-0 [&[data-pinned=left][data-last-col=left]]:border-e! [&[data-pinned=right]:last-child_div.cursor-col-resize:last-child]:opacity-0 [&[data-pinned=right][data-last-col=right]]:border-s! [&[data-pinned][data-last-col]]:border-border data-pinned:bg-muted/90 data-pinned:backdrop-blur-xs',
        header.column.columnDef.meta?.headerClassName,
        column.getIndex() === 0 || column.getIndex() === header.headerGroup.headers.length - 1
          ? props.tableClassNames?.edgeCell
          : '',
      )}
    >
      {children}
    </th>
  );
}

function DataGridTableHeadRowCellResize<TData>({ header }: { header: Header<TData, unknown> }) {
  const { column } = header;
  const resizeHandler = header.getResizeHandler();

  return (
    <div
      {...{
        onDoubleClick: () => column.resetSize(),
        onPointerDown: (e: React.PointerEvent) => {
          // Prevent dnd-kit from starting a column drag while the user is resizing.
          e.stopPropagation();
          // Capture the pointer so a fast drag that leaves the 16px handle - or
          // leaves the window - keeps resizing instead of silently stopping.
          (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        },
        onMouseDown: (e: React.MouseEvent) => {
          e.stopPropagation();
          resizeHandler(e);
        },
        onTouchStart: (e: React.TouchEvent) => {
          e.stopPropagation();
          resizeHandler(e);
        },
        className:
          'absolute top-0 h-full w-4 cursor-col-resize user-select-none touch-none -end-2 z-10 flex justify-center before:absolute before:w-px before:inset-y-0 before:bg-border before:-translate-x-px',
      }}
    />
  );
}

function DataGridTableRowSpacer() {
  return <tbody aria-hidden="true" className="h-2"></tbody>;
}

function DataGridTableBody({ children }: { children: ReactNode }) {
  const { props, table, isLoading } = useDataGrid();
  const showSkeleton = useBodySkeleton();

  // The rows on screen are the PREVIOUS page's while the next one loads
  // (M4 list latency) - dimmed rather than replaced by a skeleton.
  //
  // `isPlaceholderData` is the load-bearing clause: TanStack reports a
  // `keepPreviousData` window as `isLoading: false, isFetching: true`, so a
  // list that does not forward the flag never dims, however many rows it is
  // holding. `lib/list-query/options.inventory.test.ts` is what keeps every
  // list forwarding it. The `isLoading`-with-rows clause stays for the grids
  // whose call site feeds `isLoading || isFetching` instead.
  const holdingRows = Boolean(
    !showSkeleton && (props.isPlaceholderData || (isLoading && table.getRowModel().rows.length > 0)),
  );

  return (
    <tbody
      className={cn(
        '[&_tr:last-child]:border-0',
        props.tableLayout?.rowRounded && '[&_td:first-child]:rounded-s-lg [&_td:last-child]:rounded-e-lg',
        holdingRows && 'opacity-60 transition-opacity duration-(--duration-fast) ease-(--ease-standard)',
        props.tableClassNames?.body,
      )}
    >
      {children}
    </tbody>
  );
}

/**
 * True while the body should draw skeleton rows, i.e. while it has nothing
 * worth showing. Two ways for that to be true:
 *
 * - no rows yet - a FIRST load; or
 * - column preferences still resolving, which is why `DataGridProvider` merges
 *   `isColumnPreferencesLoading` into `isLoading` at all: painting the rows
 *   under the DEFAULT column layout and re-laying them out a tick later is a
 *   flash, and a reader who saw the wrong columns first does not trust the
 *   second answer either.
 *
 * With both settled, a reload holds the rows it has - dimmed by
 * `DataGridTableBody` - rather than throwing them away. `DataGridPagination`
 * calls this same hook to decide whether to draw its own two skeleton bars, so
 * the pager and the rows cannot disagree about what a first load is (M4-02,
 * M4-03). It lives here, not at the call sites, so all four body render paths
 * (plain, column-drag, row-drag, and the drive's own list body) and every grid
 * built on them behave the same.
 *
 * The `pageSize` clause only ever ruled out `0`/`undefined`; it does NOT make
 * `Array.from({ length: pagination.pageSize })` safe on its own, and a caller
 * that handed the table an oversized page size proved it - see
 * `skeletonRowCount`, which every body render path uses instead.
 */
function useBodySkeleton(): boolean {
  const { table, isLoading, isColumnPreferencesLoading, props } = useDataGrid();
  const hasRows = table.getRowModel().rows.length > 0;

  return Boolean(
    props.loadingMode === 'skeleton' &&
      isLoading &&
      table.getState().pagination?.pageSize &&
      (!hasRows || isColumnPreferencesLoading),
  );
}

/**
 * The most skeleton rows any grid draws, whatever its page size is.
 *
 * It is the LARGEST page size the DataGrid offers (`DEFAULT_PAGE_SIZES` in
 * `data-grid-pagination.tsx`, currently `[25, 50, 100]`), not the smallest -
 * most lists open at 50, and a cap below the real page size draws a short
 * skeleton that grows the moment the page lands, the layout jump M4 removed.
 * A hundred grey bars still reads as "rows are coming"; only an unbounded
 * page size (`paginate={false}`) gets clamped down to it. Not imported from
 * `data-grid-pagination.tsx` directly - that file already imports
 * `useBodySkeleton` from here, and importing back would cycle.
 */
export const SKELETON_ROWS_MAX = 100;

/**
 * How many skeleton rows to draw for a given page size.
 *
 * `Array.from({ length: n })` throws `RangeError: Invalid array length` for
 * anything that is not a valid array length, and a grid that renders every row
 * has no meaningful page size to draw a placeholder for. A skeleton is a
 * placeholder for what is arriving, not a faithful copy of it, so the count is
 * capped here - in ONE place, read by all four body render paths (plain,
 * column-drag, row-drag, and the drive's own list body).
 */
export function skeletonRowCount(pageSize: number | undefined): number {
  if (!pageSize || !Number.isFinite(pageSize) || pageSize < 1) return SKELETON_ROWS_MAX;
  return Math.min(Math.floor(pageSize), SKELETON_ROWS_MAX);
}

function DataGridTableBodyRowSkeleton({ children }: { children: ReactNode }) {
  const { table, props } = useDataGrid();

  return (
    <tr
      className={cn(
        'hover:bg-muted/40 data-[state=selected]:bg-muted/50',
        (props.rowHref || props.onRowClick) && 'cursor-pointer',
        !props.tableLayout?.stripped &&
          props.tableLayout?.rowBorder &&
          'border-b border-border [&:not(:last-child)>td]:border-b',
        props.tableLayout?.cellBorder && '[&_>:last-child]:border-e-0',
        props.tableLayout?.stripped && 'odd:bg-muted/90 hover:bg-transparent odd:hover:bg-muted',
        table.options.enableRowSelection && '[&_>:first-child]:relative',
        props.tableClassNames?.bodyRow,
      )}
    >
      {children}
    </tr>
  );
}

function DataGridTableBodyRowSkeletonCell<TData>({ children, column }: { children: ReactNode; column: Column<TData> }) {
  const { props, table } = useDataGrid();
  const bodyCellSpacing = bodyCellSpacingVariants({
    size: props.tableLayout?.dense ? 'dense' : 'default',
  });

  return (
    <td
      className={cn(
        'align-middle',
        bodyCellSpacing,
        props.tableLayout?.cellBorder && 'border-e',
        props.tableLayout?.columnsResizable && column.getCanResize() && 'truncate',
        column.columnDef.meta?.cellClassName,
        props.tableLayout?.columnsPinnable &&
          column.getCanPin() &&
          '[&[data-pinned=left][data-last-col=left]]:border-e! [&[data-pinned=right][data-last-col=right]]:border-s! [&[data-pinned][data-last-col]]:border-border data-pinned:bg-background/90 data-pinned:backdrop-blur-xs"',
        column.getIndex() === 0 || column.getIndex() === table.getVisibleFlatColumns().length - 1
          ? props.tableClassNames?.edgeCell
          : '',
      )}
    >
      {children}
    </td>
  );
}

/**
 * Anything inside a row that owns its own click.
 *
 * A row that opens a record must not also swallow the checkbox, the action
 * button, the inline editor or the menu item sitting in one of its cells. The
 * alternative is every one of those remembering `stopPropagation`, and the one
 * that forgets navigates away in the middle of an action.
 */
const ROW_INTERACTIVE_SELECTOR =
  'a,button,input,select,textarea,label,[role="checkbox"],[role="menuitem"],[role="combobox"]';

/**
 * Click, middle-click and keyboard handling for a row that opens something.
 *
 * Shared by BOTH row branches on purpose. The `rowHref` branch had all of this
 * and the `onRowClick` branch had a bare `onClick` on the `<tr>`, so a Brands
 * row carrying a "View products" link navigated to products AND set the edit
 * lightbox's state on the way out - which is why the row read as doing nothing.
 * The same hole put the row's open action out of reach of the keyboard and left
 * it with no role for assistive tech to announce.
 *
 * `opensUrl` is what separates the two: a row that opens a URL honours the
 * modifiers an anchor honours and can go to a new tab; a row that opens a
 * lightbox has no second tab, so a modified click just opens it here.
 *
 * No `role` on the `<tr>`. S1 put `role="link"` there and it looked right, but
 * an explicit role REPLACES the implicit one, so the row stopped being a `row`
 * to assistive tech and the table lost its grid semantics with it. The
 * fulfilment board's own tests caught it - `getAllByRole('row')` returned
 * nothing. `tabIndex` plus Enter and Space is what S1-06 and D3 actually ask
 * for ("the whole row is the target, keyboard included"), and it costs the
 * table nothing.
 */
function rowOpenProps({
  opensUrl,
  open,
}: {
  opensUrl: boolean;
  open: (newTab: boolean) => void;
}): React.ComponentProps<'tr'> {
  const fromOwnControl = (target: EventTarget | null) =>
    Boolean((target as Element | null)?.closest?.(ROW_INTERACTIVE_SELECTOR));

  return {
    tabIndex: 0,
    onClick: (event) => {
      // The PRIMARY button only. A real middle click fires auxclick alone, but a
      // synthetic dispatch, assistive tech and Firefox autoscroll also deliver a
      // `click` carrying button 1 - and React's onClick does not filter by
      // button. Without this the row opened the record in a new tab from
      // auxclick AND pushed the current tab to the same record from the click,
      // so the user lost the list they were reading. Opening on both would be
      // no better: two tabs. auxclick owns the new tab, click owns this one.
      if (event.button !== 0) return;
      // A control in a cell keeps its own click.
      if (fromOwnControl(event.target)) return;
      // Same modifiers an anchor honours, so the row behaves like the link it claims to be.
      open(opensUrl && (event.metaKey || event.ctrlKey || event.shiftKey));
    },
    onAuxClick: (event) => {
      if (!opensUrl) return;
      if (event.button !== 1) return;
      if (fromOwnControl(event.target)) return;
      event.preventDefault();
      open(true);
    },
    onKeyDown: (event) => {
      // Only the ROW's own keystrokes: Space in a cell's text input types a
      // space, and Space on the selection checkbox ticks the row.
      if (event.target !== event.currentTarget) return;
      if (event.key !== 'Enter' && event.key !== ' ') return;
      // Space scrolls the page otherwise, and Enter would submit a surrounding form.
      event.preventDefault();
      open(opensUrl && (event.metaKey || event.ctrlKey || event.shiftKey));
    },
  };
}

/**
 * The row when the list gave it a record to open.
 *
 * Split out so `useRouter` is only called by a grid that actually navigates -
 * Next throws "expected app router to be mounted" rather than returning null,
 * and a grid whose rows are not links must not require a router to render.
 */
function LinkableBodyRow({
  href,
  rowProps,
  children,
}: {
  href: string;
  rowProps: React.ComponentProps<'tr'>;
  children: ReactNode;
}) {
  const router = useRouter();
  const prefetchOnce = usePrefetchOnce();

  const openRecord = (newTab = false) => {
    if (newTab) {
      // `router.push` applies the deploy base path itself; `window.open` does not,
      // so a sub-path deploy would open a 404 in the new tab. A new tab leaves
      // this tab's own history alone, so nothing below runs for it either.
      window.open(toAbsoluteUrl(href), '_blank', 'noopener,noreferrer');
    } else {
      // M5-07 browser-Back gap (evidence run 1): `appendListState` only ever
      // wrote page/sort/query/`from` into the DETAIL href. The in-app Back
      // button (`useHrefWithListState`, `BackToList.tsx`) reads that href back
      // and works; the browser's OWN Back button does not replay a `push` -
      // it returns to whatever URL sits in the LIST's own history entry, which
      // without this stayed the bare URL from first mount (no page, no `from`),
      // so Back silently reset to page 1 with the row never highlighted.
      //
      // BL-2 (M5 run 3 review): a naive rewrite that just REPLACED the list's
      // own URL search with `href`'s search (the DETAIL page's query) had two
      // bugs. (a) it wiped any param the list reads off its own URL but never
      // echoes into `rowHref` - GRNList's `spo_allocation_id`, for one - so
      // Back landed on the unfiltered list. (b) it fired even when this grid
      // is not this route's own list at all (a `PanelDataGrid` embedded on a
      // detail page's own tab, e.g. `SeenInProductsTab` inside
      // `SpecKeyRecordDetail`), clobbering THAT page's own pager state with
      // the tab grid's page/limit.
      //
      // Two rules fix both, in one place:
      //   (a) start from the list's CURRENT URL params, not an empty object,
      //       and `.set()` only the reserved list-state keys
      //       (`RESERVED_LIST_STATE_KEYS`, read off the already-built detail
      //       href, which is where `appendListState` put them) plus `from` -
      //       every other existing param survives.
      //   (b) only rewrite when the detail href is a CHILD route of the
      //       current page (`<pathname>/<id>`); anywhere else, this is not
      //       "this page's list" and its row click must not touch this
      //       page's history entry at all.
      //
      // `history.replaceState` (not `router.replace`, which would re-render
      // and can refetch a list that is about to be left anyway) rewrites the
      // entry in place, BEFORE the push. `history.state` is passed through
      // unchanged so Next's own router state on this entry survives.
      const { path: detailPath, search: detailSearch } = splitHref(href);
      const isListsOwnRoute = detailPath.startsWith(`${window.location.pathname}/`);
      if (isListsOwnRoute) {
        const detailParams = new URLSearchParams(detailSearch);
        const nextParams = new URLSearchParams(window.location.search);
        for (const key of RESERVED_LIST_STATE_KEYS) {
          const value = detailParams.get(key);
          if (value !== null) nextParams.set(key, value);
          else nextParams.delete(key);
        }
        const nextSearch = nextParams.toString();
        window.history.replaceState(
          window.history.state,
          '',
          `${window.location.pathname}${nextSearch ? `?${nextSearch}` : ''}`,
        );
      }
      router.push(href);
    }
  };

  // Hover is the signal, not the viewport: 151 lists render up to 100 rows, and
  // a viewport-triggered prefetch would fetch a page's worth of detail chunks
  // the reader never opens (M4 list latency).
  //
  // Taken OUT of the spread and composed rather than written after it: a
  // draggable row carries its own `onPointerEnter`, and the last one written
  // wins. Same rule as `rowOpenProps` below - nothing here silently replaces a
  // handler the caller passed.
  const { onPointerEnter: rowPointerEnter, ...restRowProps } = rowProps;

  return (
    // `rowOpenProps` BEFORE the dnd listeners `rowProps` carries: a future
    // list that is both draggable and openable would otherwise have its
    // keyboard-drag onKeyDown replaced by this one, silently.
    <tr
      {...rowOpenProps({ opensUrl: true, open: openRecord })}
      onPointerEnter={(event) => {
        prefetchOnce(href);
        rowPointerEnter?.(event);
      }}
      {...restRowProps}
    >
      {children}
    </tr>
  );
}

function subscribeToNothing() {
  // No ongoing subscription: nothing in this feature listens for a LATER
  // `from=` change, so there is nothing to re-subscribe to. `getSnapshot`
  // (`currentLocationSearch` below) still re-reads `window.location.search`
  // on every render the way any `useSyncExternalStore` snapshot does -
  // "once" describes the URL param this feature acts on, not the read.
  return () => {};
}
function currentLocationSearch(): string {
  return window.location.search;
}
function emptyLocationSearch(): string {
  return '';
}

/**
 * The `from=` row id (M5-07), read once after hydration and cleared on the
 * reader's first pointer OR key interaction with the page - "until the next
 * pointer or key event", not for the life of the URL.
 *
 * Reads `window.location.search` through `useSyncExternalStore` rather than
 * `next/navigation`'s `useSearchParams()`. That hook needs a router context
 * every one of the grid's ~190 call sites (and their tests) would then have
 * to provide for a feature most never exercise, and a test that mocks
 * `next/navigation` for `usePathname`/`useRouter` without also stubbing it
 * throws on the call itself (vitest's mock proxy flags the missing export) -
 * real failures this file's own suite found
 * (`components/common/onboarding/PeopleGrid.test.tsx`,
 * `app/(protected)/project-sales/parties/components/PartiesClient.test.tsx`).
 * `useSyncExternalStore`'s `getServerSnapshot` (`emptyLocationSearch`) is the
 * React-native tool for exactly this shape: SSR, and the client's FIRST
 * (hydrating) render, both see `''` - no `from`, no mismatch - and React
 * re-renders with the REAL value (`getSnapshot`, which re-reads
 * `window.location.search` on every render, same as any other snapshot)
 * immediately after commit, entirely without a router dependency.
 *
 * Called ONCE per rendered grid (`DataGridTable`/`DataGridTableDnd`/
 * `DataGridTableDndRows` - whichever path a given grid renders through), not
 * per row (S5/S7, M5 review run 1): every row reads the same value off the
 * same query string and clears on the same document-wide event, so N rows
 * used to mean N listeners and N independent `cleared` states - a row that
 * mounted AFTER the first pointer or key event (a paginated list still
 * carries `from=` in the URL, so a later page's rows mount with the param
 * still set) had its own `cleared` still `false` and re-ran `scrollIntoView`
 * on mount. The caller now resolves this once and passes `returnedFromId`
 * down as a prop.
 */
function useReturnedRowId(): string | null {
  const search = React.useSyncExternalStore(
    subscribeToNothing,
    currentLocationSearch,
    emptyLocationSearch,
  );
  const fromParam = new URLSearchParams(search).get('from');
  const [cleared, setCleared] = React.useState(false);
  React.useEffect(() => {
    if (!fromParam || cleared) return;
    const clear = () => setCleared(true);
    document.addEventListener('pointerdown', clear, { once: true });
    document.addEventListener('keydown', clear, { once: true });
    return () => {
      document.removeEventListener('pointerdown', clear);
      document.removeEventListener('keydown', clear);
    };
  }, [fromParam, cleared]);
  return cleared ? null : fromParam;
}

function DataGridTableBodyRow<TData>({
  children,
  row,
  returnedFromId = null,
  dndRef,
  dndStyle,
  dndAttributes,
  dndListeners,
}: {
  children: ReactNode;
  row: Row<TData>;
  /**
   * The row id the reader's Back should restore to (M5-07), resolved ONCE by the
   * caller and passed down - see `useReturnedRowId`'s own doc for why (S5/S7, M5
   * review run 1). Defaults to `null` for the drag-overlay's floating row clone
   * (`data-grid-table-dnd-rows.tsx`), which is never the settled row to restore.
   */
  returnedFromId?: string | null;
  dndRef?: React.Ref<HTMLTableRowElement>;
  dndStyle?: CSSProperties;
  dndAttributes?: Record<string, unknown>;
  dndListeners?: Record<string, unknown>;
}) {
  const { props, table } = useDataGrid();

  // The whole row opens the record, from anywhere on it, by mouse or by keyboard.
  // 78 of 193 lists did this and 26 had a detail route with no way to reach it.
  //
  // Read ONCE (S6, M5 review run 1): `appendListState` and `rowRestoreId` below
  // both want the raw href, and calling `props.rowHref(row.original)` a second
  // time for the same row was wasted work at best - and a caller-supplied
  // `rowHref` is not guaranteed pure (it can read from a ref, a Date, etc.), so a
  // second call is not even guaranteed to return the same thing.
  const rawHref = props.rowHref?.(row.original);
  const href = rawHref ? appendListState(rawHref, table, row) : undefined;

  // A record on its way out stays visible and says so, rather than vanishing
  // before the reader can cancel (S6-07).
  const isPending = props.rowPending?.(row.original) ?? false;
  // S4: a picker opened from a schedule cell tints the rows whose date fell in the clicked
  // week, so the click reads as "these rows" once the dialog opens.
  const extraClassName = props.rowClassName?.(row.original);
  const extraAttributes = props.rowAttributes?.(row.original) ?? {};

  // M5-07: the row Back should restore, on mount only - `returnedFromId` is
  // read ONCE from `from=` (by the caller now, not this row - see
  // `useReturnedRowId`) and cleared on the reader's first pointer OR key
  // interaction with the page, so it highlights "until the next pointer or key
  // event" rather than for the life of the URL.
  const restoreId = href ? rowRestoreId(table, row, rawHref) : undefined;
  const isReturned = Boolean(restoreId && returnedFromId && restoreId === returnedFromId);
  const scrollRef = React.useRef<HTMLTableRowElement | null>(null);
  React.useEffect(() => {
    if (isReturned) scrollRef.current?.scrollIntoView({ block: 'center' });
  }, [isReturned]);

  const rowProps: React.ComponentProps<'tr'> = {
    ref: (node: HTMLTableRowElement | null) => {
      // Combines the caller-supplied ref (dnd-kit's, possibly) with our own.
      if (typeof dndRef === 'function') dndRef(node);
      else if (dndRef) (dndRef as React.MutableRefObject<HTMLTableRowElement | null>).current = node;
      scrollRef.current = node;
    },
    style: { ...(dndStyle ? dndStyle : null) },
    'data-state': table.options.enableRowSelection && row.getIsSelected() ? 'selected' : undefined,
    'data-pending': isPending ? 'true' : undefined,
    'data-returned': isReturned ? 'true' : undefined,
    ...extraAttributes,
    ...(dndAttributes ?? {}),
    ...(dndListeners ?? {}),
    className: cn(
      'hover:bg-muted/40 data-[state=selected]:bg-muted/50',
      isPending && 'opacity-50',
      // M5-07: the row Back restores, until the reader's next pointer event.
      'data-[returned=true]:bg-primary/5',
      extraClassName,
      (href || props.onRowClick) && 'cursor-pointer',
      // The press cue belongs to the rows that take a press. It is not on the
      // skeleton row (nothing to open yet), and not on a stripped grid, where
      // the odd row already paints itself bg-muted/90 - darker than the /60 the
      // press would set, so the press would read as a lift, not a push.
      (href || props.onRowClick) && !props.tableLayout?.stripped && 'active:bg-muted/60',
      !props.tableLayout?.stripped &&
        props.tableLayout?.rowBorder &&
        'border-b border-border [&:not(:last-child)>td]:border-b',
      props.tableLayout?.cellBorder && '[&_>:last-child]:border-e-0',
      props.tableLayout?.stripped && 'odd:bg-muted/90 hover:bg-transparent odd:hover:bg-muted',
      table.options.enableRowSelection && '[&_>:first-child]:relative',
      props.tableClassNames?.bodyRow,
    ),
  } as React.ComponentProps<'tr'>;

  if (href) {
    return (
      <LinkableBodyRow href={href} rowProps={rowProps}>
        {children}
      </LinkableBodyRow>
    );
  }

  if (props.onRowClick) {
    // A lightbox, not a URL: there is no second tab to open it in.
    return (
      <tr
        {...rowOpenProps({ opensUrl: false, open: () => props.onRowClick?.(row.original) })}
        {...rowProps}
      >
        {children}
      </tr>
    );
  }

  return <tr {...rowProps}>{children}</tr>;
}

/**
 * A totals row, INSIDE the table, so the number lines up under the column it sums.
 *
 * Rendered only when a column declares `footer` on its definition, so no existing grid gains a
 * stray empty row. A total placed beside the toolbar instead - "1 PO, RM 1,810,640.62" - reads as
 * a chip competing with the buttons, and nothing tells the reader WHICH column it totals; sitting
 * under its own column the number needs no label at all.
 */
/**
 * The footer rows worth drawing.
 *
 * A flat grid has exactly one, so this is `getFooterGroups()` unchanged. A grid with
 * COLUMN GROUPS gets a second, mirror row for the group headers, and no group declares a
 * `footer`, so it would render as an empty bordered strip under the totals.
 */
function footerGroupsWithContent<TData>(table: Table<TData>) {
  return table
    .getFooterGroups()
    .filter((group) => group.headers.some((h) => !h.isPlaceholder && Boolean(h.column.columnDef.footer)));
}

function DataGridTableFoot({ children }: { children: ReactNode }) {
  const { props } = useDataGrid();

  return (
    <tfoot
      className={cn(
        'border-t border-border bg-muted/30 font-semibold',
        props.tableClassNames?.footer,
      )}
    >
      {children}
    </tfoot>
  );
}

function DataGridTableFootRowCell<TData>({
  children,
  header,
}: {
  children: ReactNode;
  header: Header<TData, unknown>;
}) {
  const { props } = useDataGrid();
  const { column } = header;

  const bodyCellSpacing = bodyCellSpacingVariants({
    size: props.tableLayout?.dense ? 'dense' : 'default',
  });

  return (
    <td
      className={cn(
        'align-middle text-sm',
        bodyCellSpacing,
        // A footer carries one number, never an avatar, so it does not need the row min-height.
        'h-auto',
        props.tableLayout?.cellBorder && 'border-e',
        // Inherits the column's own alignment: a right-aligned money column totals right.
        column.columnDef.meta?.cellClassName,
      )}
      style={{
        ...(props.tableLayout?.columnsPinnable && column.getCanPin()
          ? getPinningStyles(column)
          : null),
        width: props.tableLayout?.width === 'fixed' ? `${header.getSize()}px` : undefined,
      }}
    >
      {children}
    </td>
  );
}

function DataGridTableBodyRowExpandded<TData>({ row }: { row: Row<TData> }) {
  const { props, table } = useDataGrid();

  return (
    <tr className={cn(props.tableLayout?.rowBorder && '[&:not(:last-child)>td]:border-b')}>
      <td colSpan={row.getVisibleCells().length}>
        {/* AC-RS-70 (`PLAN-oi-request-cs-reserve.md` section 6d G4a): a fixed-layout
            table this wide is as wide as the whole grid, so its own expanded content
            (a nested grid, a stock table with a slack column) can stretch past the
            scroll container's right edge with nothing to stop it. `sticky left-0`
            pins the wrapper to the viewport's own left edge and `max-w-[var(--dg-
            viewport-w)]` caps it at the scroller's own clientWidth - the CSS
            variable `DataGridScroller` keeps live off a `ResizeObserver` on itself,
            so every `expandedContent` site benefits with no change of its own.
            S2 (fix round 2): `overflow-x-auto` too - capping the width is not the
            same as making it reachable, and content still wider than that cap (a
            `SpoPlannerTable` `LocationSplitPanel` at `min-w-[30rem]` on a 375px
            viewport) needs its OWN scrollbar rather than being clipped silently. */}
        <div className="sticky left-0 max-w-[var(--dg-viewport-w)] overflow-x-auto">
          {table
            .getAllColumns()
            .find((column) => column.columnDef.meta?.expandedContent)
            ?.columnDef.meta?.expandedContent?.(row.original)}
        </div>
      </td>
    </tr>
  );
}

function DataGridTableBodyRowCell<TData>({
  children,
  cell,
  dndRef,
  dndStyle,
  dndDragging,
  dndSortableDisabled,
}: {
  children: ReactNode;
  cell: Cell<TData, unknown>;
  dndRef?: React.Ref<HTMLTableCellElement>;
  dndStyle?: CSSProperties;
  /** True while THIS column is the one being dragged (dnd-kit's `isDragging`). */
  dndDragging?: boolean;
  /** S1 (`PLAN-oi-request-cs-reserve.md` section 6d, fix round 2): true for a fixed-
   * utility / expanded-content column's own cell (`isFixedUtilityColumn`) - the SAME
   * value `useSortable`'s own `disabled` was handed, surfaced as `data-dnd-disabled`
   * (F3, fix round 3: the house convention next to `data-pinned`/`data-last-col` -
   * `aria-disabled` misdescribed a plain `<td>` as a disabled WIDGET to assistive
   * tech) so a plain column's cell and a fixed one's are told apart in the DOM the
   * way the header's grip already is. Undefined (no attribute) for an ordinary
   * sortable cell. */
  dndSortableDisabled?: boolean;
}) {
  const { props } = useDataGrid();

  const { column, row } = cell;
  const isPinned = column.getIsPinned();
  const isLastLeftPinned = isPinned === 'left' && column.getIsLastColumn('left');
  const isFirstRightPinned = isPinned === 'right' && column.getIsFirstColumn('right');
  const bodyCellSpacing = bodyCellSpacingVariants({
    size: props.tableLayout?.dense ? 'dense' : 'default',
  });

  return (
    <td
      key={cell.id}
      ref={dndRef}
      {...(props.tableLayout?.columnsDraggable && !isPinned ? { cell } : {})}
      style={{
        // Pinning last so it beats the drag style, except while this column is
        // the one being dragged - see the head cell.
        ...(dndDragging
          ? { ...(isPinned ? getPinningStyles(column) : null), ...dndStyle }
          : { ...dndStyle, ...(isPinned ? getPinningStyles(column) : null) }),
      }}
      data-pinned={isPinned || undefined}
      data-last-col={isLastLeftPinned ? 'left' : isFirstRightPinned ? 'right' : undefined}
      data-dnd-disabled={dndSortableDisabled || undefined}
      className={cn(
        'align-middle',
        bodyCellSpacing,
        props.tableLayout?.cellBorder && 'border-e',
        props.tableLayout?.columnsResizable && column.getCanResize() && 'truncate',
        cell.column.columnDef.meta?.cellClassName,
        isPinned &&
          '[&[data-pinned=left][data-last-col=left]]:border-e! [&[data-pinned=right][data-last-col=right]]:border-s! [&[data-pinned][data-last-col]]:border-border data-pinned:bg-background/90 data-pinned:backdrop-blur-xs"',
        column.getIndex() === 0 || column.getIndex() === row.getVisibleCells().length - 1
          ? props.tableClassNames?.edgeCell
          : '',
      )}
    >
      {children}
    </td>
  );
}

function DataGridTableEmpty() {
  const { table, props } = useDataGrid();
  const totalColumns = table.getAllColumns().length;
  const failed = props.error != null && props.error !== false;

  return (
    <tr>
      {/*
        The cell spans every column, so on a listing wider than its scroll container
        a CENTRED message is centred on the TABLE, not on what the user can see: at
        1280px the stock-inquiries grid is 2459px wide and its "No data available"
        landed ~600px past the right edge, so an empty result read as a blank band.
        That is worst exactly where it matters most - a sticky filter that matches
        nothing is otherwise indistinguishable from data loss.

        The message is start-aligned and sticky instead, so it sits under the first
        column at any table width and follows the horizontal scroll. `px-4` is the
        default cell padding, so it lines up with the first column's content rather
        than hugging the border.
      */}
      <td colSpan={totalColumns} className="p-0 text-muted-foreground">
        <div
          data-slot="data-grid-empty"
          className="sticky start-0 flex w-fit flex-col items-start gap-3 px-4 py-6 text-start"
        >
          {failed ? (
            <ListLoadFailure error={props.error} onRetry={props.onRetry} />
          ) : (
            <span>{props.emptyMessage || 'No data available'}</span>
          )}
        </div>
      </td>
    </tr>
  );
}

function DataGridTableLoader() {
  const { props } = useDataGrid();

  return (
    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
      <div className="text-muted-foreground bg-card  flex items-center gap-2 px-4 py-2 font-medium leading-none text-sm border shadow-xs rounded-md">
        <svg
          className="animate-spin -ml-1 h-5 w-5 text-muted-foreground"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3"></circle>
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
          ></path>
        </svg>
        {props.loadingMessage || 'Loading results'}
      </div>
    </div>
  );
}

function DataGridTableRowSelect<TData>({ row, size }: { row: Row<TData>; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <div
      className="inline-flex"
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div
        className={cn('hidden absolute top-0 bottom-0 start-0 w-[2px] bg-primary', row.getIsSelected() && 'block')}
      ></div>
      <Checkbox
        checked={row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(!!value)}
        aria-label="Select row"
        size={size ?? 'sm'}
        className="align-[inherit]"
      />
    </div>
  );
}

function DataGridTableRowSelectAll({ size }: { size?: 'sm' | 'md' | 'lg' }) {
  const { table, recordCount } = useDataGrid();

  return (
    <Checkbox
      checked={table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && 'indeterminate')}
      // Not `isLoading`: a refetch with rows on screen is not a reason to take
      // the control away (M4-05). An empty list still has nothing to select.
      disabled={recordCount === 0}
      onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
      aria-label="Select all"
      size={size}
      className="align-[inherit]"
    />
  );
}

/**
 * A drag, with a column GROUP's members kept contiguous.
 *
 * TanStack draws a group header once per RUN of its members, so a foreign column dropped
 * into the middle of a group renders that header twice - and the split order is then saved
 * as the user's own preference. Two rules keep the group whole: a column from outside lands
 * at the group's nearest EDGE, and a member cannot be dragged out of its group (the group
 * is one choice, the same reason a saved view names the group and not its columns).
 *
 * `groups` is empty on every flat listing, where this is a plain `arrayMove` as before.
 */
export function moveColumnKeepingGroups(
  order: string[],
  activeId: string,
  overId: string,
  groups: string[][],
): string[] {
  const oldIndex = order.indexOf(activeId);
  const overIndex = order.indexOf(overId);
  if (oldIndex === -1 || overIndex === -1 || activeId === overId) return order;

  const groupOf = (id: string) => groups.find((members) => members.includes(id));
  const activeGroup = groupOf(activeId);
  const overGroup = groupOf(overId);

  if (activeGroup) {
    // Inside its own group it reorders; anywhere else the drop is a no-op.
    return overGroup === activeGroup ? arrayMove(order, oldIndex, overIndex) : order;
  }

  if (overGroup) {
    const without = order.filter((id) => id !== activeId);
    const indexes = overGroup.map((id) => without.indexOf(id)).filter((index) => index >= 0);
    if (!indexes.length) return arrayMove(order, oldIndex, overIndex);
    const first = Math.min(...indexes);
    const last = Math.max(...indexes);
    const dropped = without.indexOf(overId);
    const insertAt = dropped - first <= last - dropped ? first : last + 1;
    return [...without.slice(0, insertAt), activeId, ...without.slice(insertAt)];
  }

  return arrayMove(order, oldIndex, overIndex);
}

/**
 * The grid's own horizontal scroll container.
 *
 * Without one, a `table-fixed w-full` grid squeezed its columns into whatever
 * width it was given - Stock showed one column at 375, Categories crushed six -
 * or, where it did overflow, pushed the whole PAGE sideways. The scrollbar is
 * the only affordance a mouse user gets, so a right-edge fade marks that there
 * is more to see and disappears once the end is reached.
 *
 * `min-w-0` on both divs is what makes the scroller scroll at all. `CardTable`
 * is a `grid`, `Card` is a `flex` column, and an item of either has
 * `min-width: auto` by default, which resolves to its MIN-CONTENT width. The
 * table asks for `min-width: 2178px`, so the item refused to be narrower than
 * that and the track blew out with it: measured on Orders at 1280, the scroller
 * reported clientWidth 2178 === scrollWidth 2178, i.e. "nothing to scroll",
 * inside a 950px card. `min-width: 0` lets the item take the width it is given
 * and the overflow lands where it belongs.
 */
function DataGridScroller({ children }: { children: ReactNode }) {
  const { ref, isFading } = useHorizontalOverflow<HTMLDivElement>();
  const { props } = useDataGrid();

  // AC-RS-70 (round 3 G4a): `--dg-viewport-w` is this scroller's own `clientWidth`,
  // kept live off a `ResizeObserver` the same way `useHorizontalOverflow` above
  // tracks its own state - every `expandedContent` site (`DataGridTableBodyRow
  // Expandded`) reads it through plain CSS inheritance, so a wrapper further down
  // the tree never has to be handed the number explicitly.
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const setViewportWidth = () => {
      el.style.setProperty('--dg-viewport-w', `${el.clientWidth}px`);
    };
    setViewportWidth();
    const observer = new ResizeObserver(setViewportWidth);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);

  // The default keeps `headerSticky` observable: a sticky header needs a
  // bounded ancestor to stick inside, and this scroller is the ONE scrollport
  // for both axes (S1-05), so the bound has to live here rather than on a
  // second wrapper. `false` opts a list out entirely (it already lives inside
  // a bounded viewport of its own); a string swaps in the caller's class.
  const scrollerMaxHeight = props.tableLayout?.scrollerMaxHeight;
  const verticalScrollClasses =
    scrollerMaxHeight === false
      ? ''
      : typeof scrollerMaxHeight === 'string'
        ? scrollerMaxHeight
        : 'max-h-(--grid-max-h) overflow-y-auto';

  return (
    <div className="relative min-w-0">
      <div
        ref={ref}
        data-slot="data-grid-scroller"
        data-fade={isFading}
        className={cn('min-w-0 overflow-x-auto overscroll-x-contain', verticalScrollClasses)}
      >
        {children}
      </div>
      {isFading && (
        <div
          aria-hidden="true"
          data-slot="data-grid-fade"
          className="pointer-events-none absolute inset-y-0 end-0 w-8 bg-gradient-to-l from-background to-transparent"
        />
      )}
    </div>
  );
}

function DataGridTable<TData>() {
  const { table, props } = useDataGrid();
  const pagination = table.getState().pagination;
  const showBodySkeleton = useBodySkeleton();
  // ONCE per grid (S5/S7, M5 review run 1), unconditionally - before the branch
  // below - so it runs exactly once regardless of which render path this grid
  // takes. See `useReturnedRowId`'s own doc for why this used to be one call PER
  // ROW instead.
  const returnedFromId = useReturnedRowId();
  // A phone does NOT pin the identifier column. S1 pinned it under `sm` so the
  // row stayed labelled while the grid scrolled sideways; the user tried it and
  // found a column that refuses to move with the rest weirder than losing sight
  // of the name (ruling 2026-08-30). The whole row scrolls as one. Explicit
  // pinning still works for the lists that ask for it.

  if (props.tableLayout?.columnsDraggable) {
    const handleDragEnd = (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over) return;

      const columnOrderState = table.getState().columnOrder as string[] | undefined;
      const leafIds = table.getAllLeafColumns().map((c) => c.id);
      const rawOrder =
        Array.isArray(columnOrderState) && columnOrderState.length > 0 ? columnOrderState : leafIds;
      const effectiveOrder = mergeColumnOrderWithLeafColumns(rawOrder, leafIds);
      if (!Array.isArray(effectiveOrder) || effectiveOrder.length === 0) return;

      const groups = table
        .getAllColumns()
        .filter((column) => column.columns?.length)
        .map((column) => column.getLeafColumns().map((leaf) => leaf.id));

      const next = moveColumnKeepingGroups(
        effectiveOrder,
        String(active.id),
        String(over.id),
        groups,
      );
      if (next === effectiveOrder) return;
      table.setColumnOrder(next);
    };

    return (
      <DataGridScroller>
        <DataGridTableDnd<TData> handleDragEnd={handleDragEnd} returnedFromId={returnedFromId} />
      </DataGridScroller>
    );
  }

  return (
    <DataGridScroller>
      <DataGridTableBase>
        <DataGridTableHead>
          {table.getHeaderGroups().map((headerGroup: HeaderGroup<TData>, index) => {
            const rowCount = table.getHeaderGroups().length;
            return (
              <DataGridTableHeadRow headerGroup={headerGroup} key={index}>
                {headerGroup.headers
                  .filter((header) => !skipMergedLeafHeader(header, rowCount))
                  .map((header, index) => {
                    const { column } = header;

                    return (
                      <DataGridTableHeadRowCell
                        header={header}
                        key={index}
                        rowSpan={headerRowSpan(header, rowCount)}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {props.tableLayout?.columnsResizable && column.getCanResize() && (
                          <DataGridTableHeadRowCellResize header={header} />
                        )}
                      </DataGridTableHeadRowCell>
                    );
                  })}
              </DataGridTableHeadRow>
            );
          })}
        </DataGridTableHead>

        {(props.tableLayout?.stripped || !props.tableLayout?.rowBorder) && <DataGridTableRowSpacer />}

        <DataGridTableBody>
          {showBodySkeleton ? (
            Array.from({ length: skeletonRowCount(pagination.pageSize) }).map((_, rowIndex) => (
              <DataGridTableBodyRowSkeleton key={rowIndex}>
                {/* LEAF columns: the flat list includes a group PARENT, which is not a
                    cell, so every skeleton row came out one td wider than the table. */}
                {table.getVisibleLeafColumns().map((column, colIndex) => {
                  return (
                    <DataGridTableBodyRowSkeletonCell column={column} key={colIndex}>
                      {column.columnDef.meta?.skeleton}
                    </DataGridTableBodyRowSkeletonCell>
                  );
                })}
              </DataGridTableBodyRowSkeleton>
            ))
          ) : table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row: Row<TData>, index) => {
              // Optional grouping. `renderGroupHeader` returns a label when this
              // row starts a new group, or null otherwise - so a grid that does
              // not opt in behaves exactly as before.
              //
              // The caller decides what a group is; the grid only draws the
              // divider. Group members must already be contiguous (order them
              // server-side), or a group will appear more than once.
              const groupHeader = props.renderGroupHeader?.(
                row.original as TData,
                index === 0 ? null : (table.getRowModel().rows[index - 1].original as TData),
              );
              return (
                <Fragment key={row.id}>
                  {groupHeader != null && (
                    <tr
                      className="bg-muted/50"
                      data-testid="data-grid-group-header"
                      data-group-label={typeof groupHeader === 'string' ? groupHeader : undefined}
                    >
                      <td
                        colSpan={row.getVisibleCells().length}
                        className="px-4 py-2 text-xs font-medium text-muted-foreground"
                      >
                        {groupHeader}
                      </td>
                    </tr>
                  )}
                  <DataGridTableBodyRow row={row} returnedFromId={returnedFromId} key={index}>
                    {row.getVisibleCells().map((cell: Cell<TData, unknown>, colIndex) => {
                      return (
                        <DataGridTableBodyRowCell cell={cell} key={colIndex}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </DataGridTableBodyRowCell>
                      );
                    })}
                  </DataGridTableBodyRow>
                  {row.getIsExpanded() && <DataGridTableBodyRowExpandded row={row} />}
                </Fragment>
              );
            })
          ) : (
            <DataGridTableEmpty />
          )}
        </DataGridTableBody>

        {table.getVisibleFlatColumns().some((column) => Boolean(column.columnDef.footer)) && (
          <DataGridTableFoot>
            {footerGroupsWithContent(table).map((footerGroup) => (
              <tr key={footerGroup.id}>
                {footerGroup.headers.map((header) => (
                  <DataGridTableFootRowCell key={header.id} header={header}>
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.footer, header.getContext())}
                  </DataGridTableFootRowCell>
                ))}
              </tr>
            ))}
          </DataGridTableFoot>
        )}
      </DataGridTableBase>
    </DataGridScroller>
  );
}

export {
  footerGroupsWithContent,
  DataGridScroller,
  headerRowSpan,
  skipMergedLeafHeader,
  DataGridTable,
  DataGridTableBase,
  DataGridTableBody,
  DataGridTableBodyRow,
  DataGridTableBodyRowCell,
  DataGridTableBodyRowExpandded,
  DataGridTableBodyRowSkeleton,
  DataGridTableBodyRowSkeletonCell,
  DataGridTableEmpty,
  DataGridTableFoot,
  DataGridTableFootRowCell,
  DataGridTableHead,
  DataGridTableHeadRow,
  DataGridTableHeadRowCell,
  DataGridTableHeadRowCellResize,
  DataGridTableLoader,
  DataGridTableRowSelect,
  DataGridTableRowSelectAll,
  DataGridTableRowSpacer,
  useBodySkeleton,
  useReturnedRowId,
};
