'use client';

import * as React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { PillOverflow, type PillItem } from '@/components/common/PillOverflow';
import { fromMinor, toMinor } from '../../_shared/lib/supplyComposition';
import {
  availableForProject,
  availableForProjectOf,
  DEFAULT_POOL_SHARE_PCT,
  POOLS_SET,
} from '../../_shared/lib/poolShare';
import {
  PILL_TONE,
  SHORT_LABELS,
  type SupplyKind,
} from '../../_shared/lib/supplyVocabulary';
import { StockDocumentsPanel } from './StockDocumentsPanel';
import type {
  BoardCellLocation,
  BoardLocationWhere,
  CellStockTableHandle,
  StockDocumentMatch,
  StockDonorMatch,
  StockJumpTarget,
} from '../../_shared/types/fulfilmentPlanning.types';

const CHEVRON_COL = 'w-[36px] min-w-[36px] max-w-[36px]';
/**
 * The column that takes the SLACK. `w-full` on one cell of an auto-layout table is what makes
 * that column absorb whatever the fixed ones did not, so the table reaches the edge of the
 * dialog instead of stopping two thirds across and leaving a blank band beside it. The floor
 * keeps a short code readable when the dialog is narrow.
 */
const LOCATION_COL = 'w-full min-w-[120px]';
/** A floor, not a fixed width: the numbers keep their room and never overlap. */
const NUMBER_COL = 'min-w-[100px]';

/**
 * AC-RS-71 (`PLAN-oi-request-cs-reserve.md` section 6d G5, round 3): the Location
 * column is resizable, remembered per browser - one column, one `localStorage` key, no
 * column-config API row. Floor matches `LOCATION_COL`'s own `min-w-[120px]`.
 */
const LOCATION_WIDTH_STORAGE_KEY = 'cellStockTable.locationWidth';
const LOCATION_WIDTH_FLOOR = 120;

/** Every `localStorage` touch is wrapped: a browser with storage disabled (or a test
 * that makes it throw) still renders the table, just with today's slack column. */
function readStoredLocationWidth(): number | null {
  try {
    const raw = window.localStorage.getItem(LOCATION_WIDTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? Math.max(LOCATION_WIDTH_FLOOR, parsed) : null;
  } catch {
    return null;
  }
}

function writeStoredLocationWidth(px: number) {
  try {
    window.localStorage.setItem(LOCATION_WIDTH_STORAGE_KEY, String(px));
  } catch {
    // localStorage disabled - the width still applies for this session.
  }
}
/** Wide enough for "Own location", which is the longest of the four tags. */
const WHERE_COL = 'min-w-[104px]';

/**
 * What a figure the server did not state renders as, which after AC-B2 is ONE row: the line
 * whose sales order names no location. A zero there would read as "that location is empty",
 * and there is no location to be empty.
 */
const BLANK = '-';
const NOT_STATED_TITLE =
  'No location on the sales order line, so nothing to count';

/** Nothing drawn: every row reads 0. Module-level, so it is not a new object per render. */
const EMPTY_TAKEN: Map<string, string> = new Map();
/** Nothing drawn, kept apart by kind: every Taken cell renders the number alone. */
const EMPTY_TAKEN_KINDS: Map<string, { kind: SupplyKind; qty: string }[]> =
  new Map();

const WHERE_LABELS: Record<BoardLocationWhere, string> = {
  own: 'Own location',
  group: 'Group',
  site_pool: 'Site pool',
  other_group: 'Other group',
};

/**
 * What is AT each location behind a cell, tabulated (captain, 18 August 2026).
 *
 * > "the representation of the BRW-BB on hand quantity, so quantity, PO quantity etc can be more
 * > tabulated and structured like AutoCount, with expandable details instead of clicking in"
 *
 * What this replaces was a pill per location reading
 * `BRW-BB · 316 owed · On hand 478 · SO qty 47009 · SPO qty 0 · Available -46531` - one long
 * sentence per location, which two locations turned into two sentences a reader had to parse
 * word by word to compare. The same facts as a row each, under AutoCount's own headers, are read
 * by running an eye down a column.
 *
 * The incoming legs were in a tooltip once, and they are SPO rows in the row's own expansion
 * now, which is where the document that carries them already lives.
 *
 * NOT a DataGrid, and this is the carve-out `FulfilmentBoardMatrix` documents and PLAN 13.10
 * states: this is a fixed matrix of eight named figures, not a listing - no column config, sort,
 * resize or pagination applies to it, and its expansion is the point rather than a row action.
 * The three obligations of that carve-out are met here as they are there: the table scrolls
 * INSIDE its own container so the dialog never scrolls sideways, it is `w-full` with a min-width
 * FLOOR per column and the Location column taking the slack (never `table-fixed`, which overlaps
 * its columns the moment content exceeds the declared width), and long text truncates with a
 * `title`.
 *
 * A LOCATION WITH NO STOCK ROW READS 0 (AC-B2). It used to read "Not stated", and that was the
 * answer to a different question: an absent `stock` row means the last upload counted none
 * there, which is a fact, while "nobody has said where to look" is true only of a line whose
 * sales order names no location at all. That one row keeps its blanks, and it is now the only
 * row that can have any - the server states a figure for every location it names.
 */
/**
 * A location row, with two OPTIONAL client-only fields a donor picker sets (S4/review B2,
 * `PLAN-local-supplier-oi-routing.md`): `rowKey`, so two donors sharing one warehouse (a
 * same-agent Borrow off two different sales-order lines at one bin) render as two rows
 * and select independently, and `label`, the donor's own identity ("SO371334 line 2",
 * "Another project") shown under the location code. Absent on every ordinary Grid
 * Location table row, which keys and labels itself off the location alone exactly as
 * before.
 */
export type DonorLocationRow = BoardCellLocation & { rowKey?: string; label?: string };

export interface CellStockTableProps {
  locations: DonorLocationRow[];
  /**
   * Why this table is showing the line's own location and nothing else, when that is all there
   * is (`BoardCell.location_group_note`). The rows are normally the sales agent's whole
   * ownership group, so a single row with no explanation reads as "this product lives in
   * exactly one place" - which is the belief the group listing exists to correct.
   */
  groupNote?: string | null;
  /**
   * How much this cell draws from each location, by warehouse code
   * (`supplyVocabulary.takenByLocation`). A location with no entry reads 0.
   *
   * Passed in rather than derived here: it is the SAME switch between the decision and the
   * suggestion that colours the cell on the grid, and computing it twice is how the bar and
   * the table come to disagree about a line the planner has just amended.
   */
  taken?: Map<string, string>;
  /**
   * `taken`'s own breakdown by kind (S3b, R-J: `supplyVocabulary.takenKindsByLocation`) - one
   * pill per part under the Taken figure, when a location was drawn on by more than one kind.
   * A location with no entry, or one entry, renders the number alone; the number never moves.
   */
  takenKinds?: Map<string, { kind: SupplyKind; qty: string }[]>;
  /**
   * The cell's own contributing lines, passed down to the expanded documents panel so their
   * rows are tagged there. The table itself does not read them.
   */
  lineIds?: string[];
  /**
   * WHOSE position this is, when the cell holds more than one line (R1).
   *
   * Every figure below is netted of one line's own quantity - a line does not compete with
   * itself - so on a cell of several lines the table is one of several true answers, and
   * which one it is has to be said. Undefined on a cell of one line, where there is nothing
   * to distinguish.
   */
  forLine?: string;
  /**
   * Every donor the active suggestion names, if any (AC-3.3/3.13) - a LIST, because a
   * step-2 combine can draw from several donors on one line (R35) and each of their rows
   * needs its own "Donor" badge, not only the first one's (review round, S3).
   */
  donor?: StockDonorMatch[] | null;
  /** The SPO document the active suggestion names, if any (AC-3.4). */
  documentInfo?: StockDocumentMatch | null;
  /**
   * The sticky toolbar's search (AC-3.5), filtering the documents inside whichever sections
   * are already expanded. Never triggers an expansion by itself - a search answers "what is
   * in what I opened", not "open something for me".
   */
  filterText?: string;
  /**
   * AC-3.1: land on "This line" - own-location expanded, scrolled, flashed - the moment this
   * table mounts, with no click. OPT-IN and off by default: this component's own test suite
   * (and any other reader of a stock position) opens closed until asked, and only the cell
   * dialog wants the auto-land its own default-landing contract promises.
   */
  landOnMount?: boolean;
  /**
   * How much of a site pool is kept back for dealers, off the board's own policy
   * (`PlanningBoard.pool_share_pct`, LADDER v8 R-K). Every site-pool ROW already arrives
   * with `available_for_project` computed by the server; this is only what the pool
   * SUBTOTAL applies the same rule with, over the pool's own net - a figure that belongs to
   * the set rather than to any bin, so no row carries it. Defaulted, so a caller with no
   * board in hand (this component's own tests) still renders the documented rule.
   */
  poolSharePct?: number;
  /**
   * Radio selection for a donor picker (S4/review B2, `PLAN-local-supplier-oi-routing.md`):
   * the currently chosen row's KEY, and the setter, rendered as a radio in the Location cell
   * before the code. Keyed by `rowKey` when a row carries one, else `warehouse_id` - a plain
   * Grid Location table row (no `rowKey`) still keys itself by warehouse, but a donor picker
   * needs the finer key so two donors at one bin (a same-agent Borrow off two different
   * sales-order lines) are two independently selectable rows rather than one. Absent renders
   * no radio - this table's ordinary caller, the cell dialog, never needs one.
   */
  selectable?: {
    value: string;
    onChange: (rowKey: string) => void;
  };
  /**
   * Badge labels per row, keyed the SAME way `selectable` is (`rowKey` else `warehouse_id`)
   * (S4): `Recommended` on the ranked donor, `Same agent` on one sharing the asking line's
   * own sales agent. Absent renders no badge - only `BorrowAddDialog` has anything to say
   * here.
   */
  badges?: Record<string, ReadonlyArray<'Recommended' | 'Same agent'>>;
  /**
   * Hides the section subtotal rows and the grand-total footer (S4). Defaults to `true`,
   * the table's ordinary behaviour - a donor list is not a group, and summing donors that
   * were never going to be combined would print a number nobody asked for.
   */
  showGroupSubtotal?: boolean;
  /**
   * The stock position alone, for a reader with no order in hand (the quotation's stock chip):
   * On hand, SO qty, SPO qty and Available, with no document chevrons. PO qty, Taken and
   * Available for Project only mean something for an order being planned, and outside one they
   * read 0 or repeat Available.
   */
  stockOnly?: boolean;
}

/** The columns `stockOnly` keeps. */
const STOCK_ONLY_COLUMNS = new Set(['on-hand', 'so', 'spo', 'available']);

/**
 * See the module doc above. Forwards a small imperative handle (`CellStockTableHandle`) so
 * the dialog's sticky toolbar and its suggestion-sentence links can drive a jump without this
 * table's own open/closed state leaking upward - the same shape `StockDocumentsPanel`'s "My
 * line" button already used locally, generalised to a target this component chooses.
 */
export const CellStockTable = React.forwardRef<
  CellStockTableHandle,
  CellStockTableProps
>(function CellStockTable(
  {
    locations,
    groupNote,
    taken,
    takenKinds,
    lineIds,
    forLine,
    donor,
    documentInfo,
    filterText,
    landOnMount,
    poolSharePct = DEFAULT_POOL_SHARE_PCT,
    selectable,
    badges,
    showGroupSubtotal = true,
    stockOnly = false,
  },
  ref,
) {
  const numericColumns = stockOnly
    ? NUMERIC_COLUMNS.filter((column) => STOCK_ONLY_COLUMNS.has(column.key))
    : NUMERIC_COLUMNS;
  const spanAll = (stockOnly ? 2 : 3) + numericColumns.length;
  const drawn = taken ?? EMPTY_TAKEN;
  const drawnKinds = takenKinds ?? EMPTY_TAKEN_KINDS;
  /**
   * AC-RS-71: the Location column's own width, in px - `null` while nothing is stored
   * (today's `w-full` slack behaviour). Read once, synchronously, off `localStorage` so
   * the very first render already carries a remembered width rather than flashing the
   * default first.
   */
  const [locationWidth, setLocationWidth] = React.useState<number | null>(
    readStoredLocationWidth,
  );
  const locationThRef = React.useRef<HTMLTableCellElement | null>(null);
  const locationDragRef = React.useRef<{
    startX: number;
    baseline: number;
    lastWidth: number;
  } | null>(null);

  const locationColStyle: React.CSSProperties | undefined =
    locationWidth != null
      ? { width: locationWidth, minWidth: locationWidth, maxWidth: locationWidth }
      : undefined;
  // Today's slack class only while nothing is remembered - a stored width drives the
  // column through `style` instead, so the two never fight over which one wins.
  const locationColClassName = locationWidth != null ? 'min-w-[120px]' : LOCATION_COL;

  function startLocationResize(clientX: number) {
    const baseline = locationWidth ?? locationThRef.current?.offsetWidth ?? LOCATION_WIDTH_FLOOR;
    locationDragRef.current = { startX: clientX, baseline, lastWidth: baseline };
  }
  function moveLocationResize(clientX: number) {
    const drag = locationDragRef.current;
    if (!drag) return;
    const next = Math.max(LOCATION_WIDTH_FLOOR, drag.baseline + (clientX - drag.startX));
    drag.lastWidth = next;
    setLocationWidth(next);
  }
  function endLocationResize() {
    const drag = locationDragRef.current;
    locationDragRef.current = null;
    if (!drag) return;
    writeStoredLocationWidth(drag.lastWidth);
  }

  /**
   * Which locations stand open. Several at once on purpose: a cell that draws on its own
   * location and on the shared pool is opened precisely to compare the two, and an accordion
   * that closes one to open the other makes that comparison a memory test.
   */
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});
  /**
   * Which SETS stand open. The group is the pile the ladder's first step actually draws -
   * a `BRW-IB` line is fed by `MWH-IB` stock - so the running balance a planner needs is
   * read here, under the subtotal, and never under one bin.
   */
  const [expandedSets, setExpandedSets] = React.useState<
    Record<string, boolean>
  >({});
  /**
   * The active jump (AC-3.1/3.2/3.3/3.4): which section to open, and the signal every
   * mounted `StockDocumentsPanel` reads to know whether it holds the row that should scroll
   * and flash. Broadcast to every open panel rather than addressed to one, because the panel
   * that owns the match is the only thing that can find it - the row lives inside a query
   * this component never reads (`useStockDetail`).
   */
  const [activeJump, setActiveJump] = React.useState<StockJumpTarget | null>(
    null,
  );
  /**
   * `nonce`'s only job is to be a value that DIFFERS from the last jump so the effect that
   * reads it re-fires on a repeat press of the same jump - `Date.now()` collided within the
   * same millisecond on a fast repeat click (review round, S3) and a second "My line" press
   * inside that millisecond silently did nothing. A ref-held counter can never repeat.
   */
  const jumpNonceRef = React.useRef(0);

  /**
   * The rows cut into the SETS availability is actually counted over (ladder v4), computed
   * once so both the render below and the imperative jump handles agree on where a location
   * sits - a second, ad hoc scan here would risk answering the two questions differently.
   */
  const sections = React.useMemo(() => sectionsOf(locations), [locations]);

  /**
   * Opens whichever section a jump names - the GROUP subtotal when the set nets one (the
   * mockup's "Balance after" reading), or ONE bin's own row when it does not (N2, fix round
   * 5) - and raises the signal every open `StockDocumentsPanel` checks itself against.
   *
   * N2: this used to open EVERY row of the section, and every open ledger's own section row
   * is sticky under the table's header (D3) - several open at once stacked their sticky
   * rows on top of one another. `location`, when the caller has one (a donor / document
   * jump names the bin it points at), picks that row; without one (`jumpToThisLine`, whose
   * section is a single own-location row anyway) the section's first addressable row opens,
   * so a bare jump never opens more than one ledger.
   */
  const openSectionAndJump = React.useCallback(
    (
      section: StockSection | undefined,
      kind: StockJumpTarget['kind'],
      location?: string | null,
    ) => {
      if (!section) return;
      if (section.netOf && sectionProductId(section)) {
        setExpandedSets((current) => ({ ...current, [section.key]: true }));
      } else {
        const addressable = (row: StockSection['rows'][number]) =>
          Boolean(row.location && row.product_id && row.warehouse_id);
        const target =
          section.rows.find(
            (row) => row.location === location && addressable(row),
          ) ?? section.rows.find(addressable);
        if (target?.location) {
          const key = target.location;
          setExpanded((current) => ({ ...current, [key]: true }));
        }
      }
      jumpNonceRef.current += 1;
      setActiveJump({ kind, nonce: jumpNonceRef.current });
    },
    [],
  );

  const ownSection = React.useCallback(
    () =>
      sections.find((section) =>
        section.rows.some((row) => (row.where ?? 'own') === 'own'),
      ),
    [sections],
  );
  const sectionAt = React.useCallback(
    (location?: string | null) =>
      location
        ? sections.find((section) =>
            section.rows.some((row) => row.location === location),
          )
        : undefined,
    [sections],
  );

  React.useImperativeHandle(
    ref,
    () => ({
      jumpToThisLine: () => openSectionAndJump(ownSection(), 'this-line'),
      // Explicit ARGUMENT wins over the component's own `donor` prop - the suggestion
      // sentence passes the SOURCE'S OWN donor (a step-2 combine can name several, R35), and
      // the toolbar's single "Donor" button, which has no one source to point at, falls back
      // to the first.
      jumpToDonor: (target) =>
        openSectionAndJump(
          sectionAt((target ?? donor?.[0])?.location) ?? ownSection(),
          'donor',
          (target ?? donor?.[0])?.location,
        ),
      jumpToDocument: (target) =>
        openSectionAndJump(
          sectionAt((target ?? documentInfo)?.location) ?? ownSection(),
          'document',
          (target ?? documentInfo)?.location,
        ),
    }),
    [openSectionAndJump, ownSection, sectionAt, donor, documentInfo],
  );

  /**
   * AC-3.1: the default landing, raised on this table's OWN mount rather than waited for
   * through the ref from outside. The dialog's `<Tabs>` needs a commit of its own before this
   * "stock" panel's content actually exists in the tree, so a jump fired from ABOVE (keyed on
   * the cell) would run against a ref that is still null on that first commit and never get a
   * second chance - the cell's own key does not change between it and the commit that
   * follows. This component remounting per cell (the dialog keys it by
   * `cell.row_key|cell.bucket_key`) is what makes "once per mount" the same thing as "once
   * per cell". `landOnMount` is OPT-IN (see the prop doc) - only the dialog asks for it.
   *
   * Fires once on mount only, deliberately: a fresh mount is guaranteed per cell by the
   * caller's own remount key, not by this dependency array.
   */
  React.useEffect(() => {
    if (!landOnMount) return;
    openSectionAndJump(ownSection(), 'this-line');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (locations.length === 0) {
    // Rendered rather than hidden, per the CRUD standard. A pivoted cell holds several products,
    // so no single stock position is true of it - and saying nothing at all would read as a
    // position of zero.
    return (
      <div className="space-y-1">
        <div
          data-testid="cell-stock-table-empty"
          className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground"
        >
          No stock position for this cell
        </div>
        {groupNote ? (
          <p
            data-testid="cell-stock-group-note"
            className="text-xs text-muted-foreground"
          >
            {groupNote}
          </p>
        ) : null}
      </div>
    );
  }

  const showTotals = locations.length > 1;
  /**
   * A section carries a subtotal when it holds several rows, or when it states a net - the
   * net is the number the ladder obeyed and it is over the whole set, including members this
   * cell never listed, so it says something no sum of these rows can. `sections` itself is
   * the memo above, shared with the imperative jump handles.
   */
  const showSubtotals =
    sections.length > 1 || sections.some((section) => section.net !== null);

  return (
    <div className="space-y-1">
      {forLine ? (
        <p
          data-testid="cell-stock-for-line"
          className="text-xs text-muted-foreground"
        >
          {`Available for ${forLine}`}
        </p>
      ) : null}
      {/* NO vertical bound, and the Stock tab has exactly ONE scroll region: the dialog body
        above this, which already owns a vertical viewport. This wrapper used to cap itself at
        half the window height and scroll, which made it one of FOUR nested vertical scrollports
        on this tab - the dialog body, this wrapper, the documents panel's own cap below it, and
        the documents grid's scroller. Measured in a browser at 1440x900 with the Site pool
        subtotal expanded, all four had `scrollHeight > clientHeight` at once, and the documents
        table's header sat 33px ABOVE its parent panel's visible top, i.e. clipped out of sight,
        while the wheel moved whichever region the pointer happened to be over rather than the
        one the reader was reading. Horizontal scroll stays: the table is wider than the dialog
        and has always scrolled sideways.

        AC-RS-70 (round 3 G4b): NO `overscroll-x-contain` here any more. An `overflow:
        auto` box is a scroll container even with nothing to scroll, and `overscroll-
        behavior: contain` on it stops a shift-wheel / two-finger swipe from chaining
        to the parent OI Lines grid's own scroller once this table sits inside that
        grid's own expanded row - `data-grid-table.tsx`'s own doc records the identical
        trap for the grid itself. */}
      <div
        data-testid="cell-stock-table"
        className="w-full overflow-x-auto rounded-lg border border-border"
      >
        {/* `w-full`, never `table-fixed`: the table fills the dialog (the captain's screenshot
          had it stopping at two thirds with an empty band on the right), the numeric columns
          hold a min-width floor, and the Location column above carries the slack. */}
        <table className="w-full border-separate border-spacing-0 text-xs">
          <thead>
            <tr>
              {!stockOnly && <th scope="col" className={cn(CHEVRON_COL, HEAD_CELL)} />}
              <th
                ref={locationThRef}
                scope="col"
                className={cn(locationColClassName, HEAD_CELL, 'relative')}
                style={locationColStyle}
              >
                Location
                {/* AC-RS-71: mirrors `DataGridTableHeadRowCellResize`'s own affordance -
                    drag to set a width, remembered in `localStorage` on release. */}
                <span
                  data-testid="cell-stock-location-resize"
                  className="absolute top-0 -end-2 z-10 h-full w-4 cursor-col-resize select-none"
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
                    startLocationResize(event.clientX);
                  }}
                  onPointerMove={(event) => moveLocationResize(event.clientX)}
                  onPointerUp={(event) => {
                    (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
                    endLocationResize();
                  }}
                  // Nit (fix round 2): a drag that never reaches `pointerup` - the
                  // browser cancels the gesture (an OS/browser drag-and-drop kicking
                  // in, a touch interrupted by a system gesture) or capture is lost
                  // some other way - used to leave `locationDragRef` armed, so the
                  // NEXT pointer move anywhere on the header would resume resizing
                  // from a stale baseline. Both end the drag the same way `pointerup`
                  // does.
                  onPointerCancel={() => endLocationResize()}
                  onLostPointerCapture={() => endLocationResize()}
                />
              </th>
              <th scope="col" className={cn(WHERE_COL, HEAD_CELL)}>
                Where
              </th>
              {numericColumns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={cn(NUMBER_COL, HEAD_CELL, 'text-end')}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {sections.flatMap((section) => [
              ...section.rows.map((entry) => {
                // S4/review B2: a donor picker's own `rowKey` wins when a row carries one,
                // so two donors sharing one warehouse (a same-agent Borrow off two
                // different sales-order lines at one bin) are two rows that select and
                // expand independently, not one merged by location - every OTHER caller
                // sets no `rowKey` and keys exactly as before.
                const key = entry.rowKey ?? entry.location ?? '__none__';
                // The row's DOM ADDRESS stays the location it is about, `rowKey` or not:
                // it is what a reader (and every test) names a row by, and a composite
                // candidate key in the attribute would name nothing anyone can point at.
                const testKey = entry.location ?? 'none';
                // Only a position the server ADDRESSED can be opened: two products share the code
                // B2155-NL-BLUE on the live book, so resolving one from the code would answer
                // confidently about the wrong product.
                const addressable = Boolean(
                  entry.product_id && entry.warehouse_id,
                );
                const isOpen = Boolean(expanded[key]);
                return (
                  <React.Fragment key={key}>
                    <tr data-testid={`cell-location-${testKey}`}>
                      {!stockOnly && (
                      <td className={cn(CHEVRON_COL, BODY_CELL, 'px-1')}>
                        {addressable ? (
                          <Button
                            type="button"
                            mode="icon"
                            variant="ghost"
                            size="sm"
                            className="size-5"
                            data-testid={`stock-expand-${testKey}`}
                            aria-label={`${isOpen ? 'Hide' : 'Show'} documents behind ${
                              entry.location ?? 'this location'
                            }`}
                            aria-expanded={isOpen}
                            onClick={() =>
                              setExpanded((current) => ({
                                ...current,
                                [key]: !current[key],
                              }))
                            }
                          >
                            {isOpen ? (
                              <ChevronDown className="size-3.5" aria-hidden />
                            ) : (
                              <ChevronRight className="size-3.5" aria-hidden />
                            )}
                          </Button>
                        ) : (
                          // The sales order named no warehouse, so there is no position to open.
                          <span
                            className="inline-block size-4"
                            title="Not addressable"
                          />
                        )}
                      </td>
                      )}
                      <td className={cn(locationColClassName, BODY_CELL)} style={locationColStyle}>
                        <div className="flex items-start gap-2">
                          {selectable && entry.warehouse_id ? (
                            <input
                              type="radio"
                              className="mt-0.5"
                              aria-label={`Choose ${entry.label ?? entry.location ?? 'this location'}`}
                              data-testid={`stock-select-${testKey}`}
                              checked={selectable.value === key}
                              onChange={() => selectable.onChange(key)}
                            />
                          ) : null}
                          <span className="min-w-0">
                            <span
                              className={cn(
                                'block truncate font-medium',
                                !entry.location && 'text-destructive',
                              )}
                              title={entry.location ?? 'No location'}
                            >
                              {entry.location ?? 'No location'}
                            </span>
                            {/* S4/review B2: the donor's own identity - "SO371334 line 2",
                                "Another project" - under the location code, so two donors at
                                one bin (same code, different candidate) are told apart. */}
                            {entry.label ? (
                              <span
                                className="block truncate text-muted-foreground"
                                title={entry.label}
                                data-testid={`stock-donor-label-${testKey}`}
                              >
                                {entry.label}
                              </span>
                            ) : null}
                            {badges?.[key]?.length ? (
                              <span className="mt-0.5 flex flex-wrap gap-1">
                                {badges[key]?.map((label) => (
                                  <Badge
                                    key={label}
                                    variant={label === 'Recommended' ? 'secondary' : 'warning'}
                                    size="sm"
                                  >
                                    {label}
                                  </Badge>
                                ))}
                              </span>
                            ) : null}
                          </span>
                        </div>
                      </td>
                      <td className={cn(WHERE_COL, BODY_CELL)}>
                        <span
                          className="block truncate text-muted-foreground"
                          title={WHERE_LABELS[entry.where ?? 'own']}
                        >
                          {WHERE_LABELS[entry.where ?? 'own']}
                        </span>
                      </td>
                      {numericColumns.map((column) => {
                        const value = valueOf(column, entry, drawn);
                        // Signed and never clamped: a negative Available IS the shortfall, and the
                        // colour is what makes it the number the eye lands on.
                        const negative = column.signed && isNegative(value);
                        // The Taken figure's own breakdown by kind (S3b, R-J): a location
                        // this cell draws on from more than one kind - a Reserve off the
                        // floor AND a borrow, say - gets a pill per part under the number.
                        // One kind or none renders the number alone; the number never moves.
                        const kinds =
                          column.key === 'taken' && entry.location
                            ? (drawnKinds.get(entry.location) ?? [])
                            : [];
                        return (
                          <td
                            key={column.key}
                            className={cn(NUMBER_COL, BODY_CELL)}
                          >
                            <span
                              data-testid={`stock-${column.key}-${testKey}`}
                              className={cn(
                                'block truncate text-end tabular-nums',
                                value === null && 'text-muted-foreground',
                                negative && 'text-destructive',
                              )}
                              title={value ?? NOT_STATED_TITLE}
                            >
                              {value ?? BLANK}
                            </span>
                            {kinds.length > 1 && (
                              <PillOverflow
                                items={kinds.map((part, index) => ({
                                  key: `${entry.location}-${part.kind}-${index}`,
                                  label: `${SHORT_LABELS[part.kind]} ${part.qty}`,
                                  tone: PILL_TONE[part.kind],
                                }))}
                                ariaLabel="Taken from"
                                testId={`stock-taken-pills-${testKey}`}
                                className="mt-0.5"
                                renderPopover={(items: PillItem[]) => (
                                  <div className="space-y-1">
                                    {items.map((item) => (
                                      <p key={item.key}>{item.label}</p>
                                    ))}
                                  </div>
                                )}
                              />
                            )}
                          </td>
                        );
                      })}
                    </tr>

                    {isOpen && addressable && !stockOnly && (
                      <tr data-testid={`stock-expansion-${testKey}`}>
                        {/* Under the row it belongs to, spanning the whole table: the documents are
                        this location's evidence, not a column of it. */}
                        <td
                          colSpan={spanAll}
                          className="border-b border-border p-0"
                        >
                          <StockDocumentsPanel
                            productId={entry.product_id as string}
                            warehouseId={entry.warehouse_id as string}
                            lineIds={lineIds}
                            donor={donor}
                            documentInfo={documentInfo}
                            filterText={filterText}
                            jumpTarget={activeJump}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              }),
              // What this section holds, added up - and for Available, what the SET NETS, which
              // is the figure the ladder actually drew on. The two differ on purpose: the net
              // covers every location of the group, and this table lists the ones this cell
              // consulted.
              ...(showGroupSubtotal &&
              showSubtotals &&
              (section.rows.length > 1 || section.net !== null)
                ? [
                    <tr
                      key={`subtotal-${section.key}`}
                      data-testid={`stock-subtotal-${section.key}`}
                    >
                      {!stockOnly && (
                      <td className={cn(CHEVRON_COL, FOOT_CELL, 'px-1')}>
                        {section.netOf && sectionProductId(section) ? (
                          <Button
                            type="button"
                            mode="icon"
                            variant="ghost"
                            size="sm"
                            className="size-5"
                            data-testid={`stock-set-expand-${section.key}`}
                            aria-label={`${
                              expandedSets[section.key] ? 'Hide' : 'Show'
                            } documents behind ${section.label}`}
                            aria-expanded={Boolean(expandedSets[section.key])}
                            onClick={() =>
                              setExpandedSets((current) => ({
                                ...current,
                                [section.key]: !current[section.key],
                              }))
                            }
                          >
                            {expandedSets[section.key] ? (
                              <ChevronDown className="size-3.5" aria-hidden />
                            ) : (
                              <ChevronRight className="size-3.5" aria-hidden />
                            )}
                          </Button>
                        ) : null}
                      </td>
                      )}
                      <td
                        className={cn(
                          locationColClassName,
                          FOOT_CELL,
                          'text-muted-foreground',
                        )}
                        style={locationColStyle}
                      >
                        {section.label}
                      </td>
                      <td className={cn(WHERE_COL, FOOT_CELL)} />
                      {numericColumns.map((column) => {
                        if (!column.total) {
                          return (
                            <td
                              key={column.key}
                              className={cn(NUMBER_COL, FOOT_CELL)}
                            />
                          );
                        }
                        const isNet =
                          column.key === 'available' && section.net !== null;
                        // R-K: the SUBTOTAL's own Available-for-Project is not a sum of the
                        // rows above it, same reason `isNet` is not for Available - on a SITE
                        // POOL it is the share of the pool's own net, which every pool row
                        // already agrees on (`_net_fields`, the same value each row's `net`
                        // carries). On every other section it IS that section's Available
                        // (D2, captain 3 Sep): nothing is kept back outside a pool, and the
                        // "-" it used to print read as missing data.
                        const total =
                          column.key === 'available-for-project'
                            ? projectShareSubtotalOf(
                                section,
                                drawn,
                                poolSharePct,
                              )
                            : isNet
                              ? section.net
                              : sumOf(section.rows, (entry) =>
                                  valueOf(column, entry, drawn),
                                );
                        return (
                          <td
                            key={column.key}
                            className={cn(NUMBER_COL, FOOT_CELL)}
                          >
                            <span
                              data-testid={`stock-subtotal-${column.key}-${section.key}`}
                              className={cn(
                                'block truncate text-end tabular-nums',
                                total === null &&
                                  'font-normal text-muted-foreground',
                                column.signed &&
                                  isNegative(total) &&
                                  'text-destructive',
                              )}
                              // Why this one figure is not the column above it added up: the
                              // net covers EVERY location of the set, and the table lists the
                              // ones this cell consulted.
                              title={isNet ? netTitle(section) : undefined}
                            >
                              {total ?? BLANK}
                            </span>
                          </td>
                        );
                      })}
                    </tr>,
                    ...(!stockOnly &&
                    expandedSets[section.key] &&
                    section.netOf &&
                    sectionProductId(section)
                      ? [
                          // The SET's own documents, merged across its bins and walked in date
                          // order: the pile the ladder's first step draws is the group's, so
                          // this is the level at which "what was left when my line came round"
                          // is a true question.
                          <tr
                            key={`set-expansion-${section.key}`}
                            data-testid={`stock-set-expansion-${section.key}`}
                          >
                            <td
                              colSpan={spanAll}
                              className="border-b border-border p-0"
                            >
                              <StockDocumentsPanel
                                productId={sectionProductId(section) as string}
                                group={section.netOf}
                                lineIds={lineIds}
                                donor={donor}
                                documentInfo={documentInfo}
                                filterText={filterText}
                                jumpTarget={activeJump}
                              />
                            </td>
                          </tr>,
                        ]
                      : []),
                  ]
                : []),
            ])}
          </tbody>

          {showGroupSubtotal && showTotals && (
            // Only when there is something to add up. One location IS its own total, and a totals
            // row repeating it is a row that says nothing.
            <tfoot>
              <tr>
                {!stockOnly && <td className={cn(CHEVRON_COL, FOOT_CELL)} />}
                <td
                  className={cn(
                    locationColClassName,
                    FOOT_CELL,
                    'text-muted-foreground',
                  )}
                  style={locationColStyle}
                >
                  Total
                </td>
                <td className={cn(WHERE_COL, FOOT_CELL)} />
                {numericColumns.map((column) => {
                  if (!column.total) {
                    return (
                      <td
                        key={column.key}
                        className={cn(NUMBER_COL, FOOT_CELL)}
                      />
                    );
                  }
                  // Nit (code review round 3 batch 2), amended by D2, and S4 (fix round 5):
                  // neither Available nor Available for Project is a sum of the ROWS, the
                  // same reason the site pool SUBTOTAL isn't (R-K) - a plain `sumOf
                  // (locations, ...)` added every pool row's own figure together, which
                  // undercounts the SET's true net whenever the cell lists fewer pool rows
                  // than the five the net covers, and the two Total columns disagreed
                  // outside a pool (Available for Project already summed subtotals;
                  // Available did not). Both are the SUBTOTALS added up now, which is how
                  // each column's own total reads once every section has stated its own.
                  const total =
                    column.key === 'available-for-project'
                      ? sumValues(
                          sections.map((section) =>
                            projectShareSubtotalOf(
                              section,
                              drawn,
                              poolSharePct,
                            ),
                          ),
                        )
                      : column.key === 'available'
                        ? sumValues(
                            sections.map((section) =>
                              availableSubtotalOf(section, drawn),
                            ),
                          )
                        : sumOf(locations, (entry) =>
                            valueOf(column, entry, drawn),
                          );
                  return (
                    <td key={column.key} className={cn(NUMBER_COL, FOOT_CELL)}>
                      <span
                        data-testid={`stock-total-${column.key}`}
                        className={cn(
                          'block truncate text-end tabular-nums',
                          total === null && 'font-normal text-muted-foreground',
                          column.signed &&
                            isNegative(total) &&
                            'text-destructive',
                        )}
                      >
                        {total ?? BLANK}
                      </span>
                    </td>
                  );
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      {groupNote ? (
        // Muted and one line, under the table it explains. Stated rather than left silent: a
        // single row with nothing said about it reads as the whole answer.
        <p
          data-testid="cell-stock-group-note"
          className="text-xs text-muted-foreground"
        >
          {groupNote}
        </p>
      ) : null}
    </div>
  );
});

type StockSection = {
  /** Stable per table, and what the subtotal row is addressed by in a test. */
  key: string;
  /** The SET this run belongs to, compared exactly - a prefix test would merge `IB` into `IB2`. */
  setKey: string;
  /** The set's own name (`IB`, `pools`), for the subtotal's label and its tooltip. */
  netOf: string | null;
  label: string;
  /** What the SET nets, when the server states one. `null` for a set with no net. */
  net: string | null;
  rows: DonorLocationRow[];
};

/**
 * The rows cut into the sets availability is actually counted over (ladder v4).
 *
 * The line's own location and its group's other warehouses are ONE ownership group and net
 * as one, so they share a subtotal even though they carry different Where tags - the tag says
 * where a row stands relative to this cell, the net says which pile it is part of, and after
 * 26 August those are two different questions. Every site pool is one pile too. A donor group
 * outside this line's own gets its own subtotal, because each donor group nets separately.
 *
 * Cut on CONTIGUOUS RUNS, never by re-collecting rows with the same key: the server sends the
 * rows in the order the reader walks them, and quietly moving one up beside an earlier row of
 * the same set would rearrange a table somebody is comparing against AutoCount.
 */
function sectionsOf(locations: DonorLocationRow[]): StockSection[] {
  const sections: StockSection[] = [];
  const used = new Set<string>();
  locations.forEach((entry) => {
    const where = entry.where ?? 'own';
    const setKey = entry.net_of ? `set:${entry.net_of}` : `where:${where}`;
    const last = sections[sections.length - 1];
    if (last && last.setKey === setKey) {
      last.rows.push(entry);
      return;
    }
    // The set's own name is the test id and the anchor: `group`, `pools`, `IB`. Suffixed
    // only if one set is ever split into two runs, which is what keeps the id unique
    // without putting an index on the ordinary case.
    let key = entry.net_of ?? where;
    for (let n = 2; used.has(key); n += 1)
      key = `${entry.net_of ?? where}-${n}`;
    used.add(key);
    sections.push({
      key,
      setKey,
      netOf: entry.net_of ?? null,
      label: labelOf(where, entry.net_of ?? null),
      net: entry.net ?? null,
      rows: [entry],
    });
  });
  return sections;
}

/**
 * The product a set's drill is opened by. Addressed by ID, never resolved from the item code:
 * two products share `B2155-NL-BLUE` on the live book. Null for a set whose rows the server
 * did not address, which is the one row a sales order gave no location.
 */
function sectionProductId(section: StockSection): string | null {
  return section.rows.find((entry) => entry.product_id)?.product_id ?? null;
}

/**
 * What the Available subtotal means, for the one cell whose figure is not a sum of the rows
 * above it. A tooltip rather than a line of copy: the number is the point, and a table that
 * explains itself in prose is a table nobody reads twice.
 */
function netTitle(section: StockSection): string {
  const what =
    section.netOf === POOLS_SET
      ? 'every site pool'
      : section.netOf
        ? `every ${section.netOf} location`
        : 'every location of this set';
  return `${section.net} across ${what}, including any this table does not list`;
}

function labelOf(where: BoardLocationWhere, netOf: string | null): string {
  if (netOf === POOLS_SET) return 'Site pool subtotal';
  if (netOf) return `${netOf} group subtotal`;
  return `${WHERE_LABELS[where]} subtotal`;
}

// NOT sticky. The header used to stick to the wrapper's own bounded scrollport, and with that
// scrollport gone the only thing left to stick to is `DialogBody` - where the dialog's own search-and-jump
// toolbar is already pinned at `top-0`. Two bands competing for the same offset needs a hard-coded
// height for the first one, and that toolbar wraps to two lines below ~900px, so the number would
// be wrong exactly where it matters. Inside a dialog the header scrolling away with its rows is
// the cheaper trade.
const HEAD_CELL =
  'border-b border-e border-border bg-muted px-2 py-1.5 text-start align-bottom font-medium';
const BODY_CELL = 'border-b border-e border-border px-2 py-1.5 align-middle';
const FOOT_CELL =
  'border-b border-e border-border bg-muted/50 px-2 py-1.5 font-medium';

/**
 * The columns, in AutoCount's order, because this is the order the planner reads over there and
 * then comes here to reconcile.
 *
 * There is no demand column. It used to lead the row as "Owed here", and it said what the
 * Contributing lines table below already says line by line under Outstanding - the same figure
 * twice, in a word this screen no longer uses. What this table is for is what is AT each
 * location, which is the one thing that table cannot say.
 *
 * EVERY column totals. The rows are a whole ownership group and every site pool rather than the
 * one warehouse a line named, and "what does the group hold" is the question they were listed to
 * answer. Totals were left out when a "total" could only ever add a location to itself.
 */
const NUMERIC_COLUMNS: {
  key: string;
  label: string;
  of: (entry: BoardCellLocation, taken: Map<string, string>) => string | null;
  /** Summed in the totals row. */
  total?: boolean;
  /** May legitimately be negative, and is coloured when it is. */
  signed?: boolean;
}[] = [
  {
    key: 'on-hand',
    label: 'On hand',
    of: (entry) => entry.qty_on_hand ?? null,
    total: true,
  },
  // No Reserved column, and no Free one. Free was `On hand - Reserved`, and Reserved itself is
  // read by nothing on this screen: `Available` is `On hand - SO + SPO` and does not use it.
  // Both went to make room for the two columns below, which answer questions no other number
  // here does. Reserved is still on the wire and still in the row's own expansion.
  {
    key: 'so',
    label: 'SO qty',
    of: (entry) => entry.so_qty ?? null,
    total: true,
  },
  {
    key: 'spo',
    label: 'SPO qty',
    of: (entry) => entry.spo_qty ?? null,
    total: true,
  },
  {
    key: 'available',
    label: 'Available',
    of: (entry) => entry.available_qty ?? null,
    total: true,
    signed: true,
  },
  // R-K, S2: what the pool may still give a PROJECT line once its own dealer share is kept
  // back (`min(floor(available x (100 - share) / 100), max(five-pool net, 0))`). Every site
  // pool row states it, `0` included.
  //
  // OUTSIDE A SITE POOL IT IS SIMPLY `Available` (D2, captain 3 Sep): no dealer share is
  // kept back at an own bin, a group sibling or another group, so everything Available
  // there is available to a project - the same signed figure, negative included. It used to
  // print "-", and a dash in a numeric column reads as missing data rather than as a rule
  // that does not apply.
  {
    key: 'available-for-project',
    label: 'Available for Project',
    of: (entry) => availableForProjectOf(entry),
    total: true,
    signed: true,
  },
  // Information, deliberately NOT folded into Available: a purchase order reaches a project
  // line through a link, never by sitting at the location. "500 already on order at DC1" is
  // what decides between a Buy and a transfer, and until now the planner had to leave the
  // dialog to find it.
  {
    key: 'po',
    label: 'PO qty',
    of: (entry) => entry.po_open_qty ?? null,
    total: true,
  },
  // What the cell actually draws from this row. A location nothing was needed from reads 0,
  // which is the answer to "why not MWH" - it was listed, it had stock, nothing was taken.
  {
    key: 'taken',
    label: 'Taken',
    of: (entry, taken) =>
      entry.location ? (taken.get(entry.location) ?? '0') : null,
    total: true,
  },
];

/**
 * One column's figure on one row, with AC-B2's rule applied HERE and nowhere else: a row that
 * NAMES a location and carries no figure reads 0.
 *
 * An absent `stock` row means the last upload counted none there, which is a fact. The row
 * whose sales order names no location keeps its blank, because there is no location whose
 * stock could be counted and a 0 would read as "that location is empty".
 *
 * Stated once, so the cells and the two totals rows cannot come to disagree about a blank.
 */
function valueOf(
  column: {
    of: (entry: BoardCellLocation, taken: Map<string, string>) => string | null;
  },
  entry: BoardCellLocation,
  taken: Map<string, string>,
): string | null {
  const value = column.of(entry, taken);
  if (value !== null) return value;
  return entry.location ? '0' : null;
}

/** Absent stays absent: a column no location stated has no total, never a total of 0. */
function sumOf(
  locations: BoardCellLocation[],
  pick: (entry: BoardCellLocation) => string | null,
): string | null {
  return sumValues(locations.map(pick));
}

/** The same rule over figures already picked, for a total summed across SECTIONS (D2). */
function sumValues(values: (string | null)[]): string | null {
  const stated = values.filter((value): value is string => value !== null);
  if (stated.length === 0) return null;
  return fromMinor(stated.reduce((total, value) => total + toMinor(value), 0));
}

/**
 * What one section's Available subtotal prints: the SET's own net when the server states one
 * (the figure covers every location of the set, listed here or not), and otherwise the rows
 * added up.
 */
function availableSubtotalOf(
  section: StockSection,
  drawn: Map<string, string>,
): string | null {
  if (section.net !== null) return section.net;
  const column = NUMERIC_COLUMNS.find((entry) => entry.key === 'available');
  return column
    ? sumOf(section.rows, (entry) => valueOf(column, entry, drawn))
    : null;
}

/**
 * What one section's "Available for Project" subtotal prints (R-K, amended by D2).
 *
 * A SITE POOL keeps a share back for dealers, so its figure is the share of its own net. No
 * other set keeps anything back, so its figure IS its Available - the same signed number,
 * read off the same place the Available cell reads it, so the two can never disagree.
 */
function projectShareSubtotalOf(
  section: StockSection,
  drawn: Map<string, string>,
  poolSharePct: number,
): string | null {
  return section.netOf === POOLS_SET
    ? availableForProject(section.net, section.net, poolSharePct)
    : availableSubtotalOf(section, drawn);
}

function isNegative(value: string | null): boolean {
  return value !== null && Number(value) < 0;
}
