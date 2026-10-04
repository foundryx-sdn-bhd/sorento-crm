'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  PackageSearch,
} from 'lucide-react';
import { ColumnDef, RowSelectionState } from '@tanstack/react-table';
import { formatDateInMalaysia } from '@/lib/helpers';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { BoardScopeToggle, type BoardScope } from './BoardScopeToggle';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { DecisionTrailButton } from '../../_shared/components/DecisionTrailButton';
import { SoLineAttachmentsButton } from '../../_shared/components/SoLineAttachmentsButton';
import type { SoLineAttachmentsByLine } from '../../_shared/services/soLineAttachmentService';
import { BoardCellBreakdownDialog } from './BoardCellBreakdownDialog';
import { BoardDecidedMarker, decidedRevisions } from './BoardDecidedMarker';
import {
  BoardDecisionPill,
  VERDICT_FILTER_OPTIONS,
  VERDICT_SORT_RANK,
  verdictOf,
} from './BoardDecisionPill';
import { BoardVerdictActions } from './BoardVerdictActions';
import { BoardLineDecisionPanel } from './BoardLineDecisionPanel';
import { UnsavedDecisionPrompt, useDecisionRowExpansion } from './decisionRowExpansion';
import { BoardChangeTable } from './BoardChangeTable';
import { changedFieldsOf, lineKeyOf } from '../../_shared/lib/boardChangeAnnotations';
import type { BoardChangeAnnotation } from '../../_shared/lib/boardChangeAnnotations';
import { canDecide } from '../../_shared/lib/boardAmend';
import {
  contributionMatchesSearch,
  soLineLabel,
  soLineNoIsSynced,
  soLineNoText,
} from '../../_shared/lib/fulfilmentBoard';
import { BoardDecideControl } from './BoardDecideControl';
import {
  boardOrderInquiryWord,
  contributionDecision,
  contributionInquiryDecision,
  contributionSuggestion,
  // Aliased the way `SalesOrderDetail` aliases it: bare `describe` is vitest's, and a file
  // that imports both reads as though the test runner were writing the column.
  describe as describeSupply,
} from '../../_shared/lib/supplyVocabulary';
import type { SupplyPart } from '../../_shared/lib/supplyVocabulary';
import type {
  BoardCell,
  BoardContribution,
  BoardDecision,
  BoardDraft,
} from '../../_shared/types/fulfilmentPlanning.types';

/**
 * A single-line CELL, built from the contribution itself (`PLAN-oi-request-cs-reserve.md`
 * 3.9, AC-RS-42): the list view has no grid-axis cell to point at (the row IS the line), so
 * this wraps the line's own `locations` - the SAME per-location stock position the grid's
 * cell carries for it - in the shape `BoardCellBreakdownDialog` already renders, unchanged.
 * `bucket_key` is the contribution's own key: unique per row, and never read as a real date
 * bucket by the dialog (it only uses it as half of a remount key and a fallback label).
 */
function boardCellForContribution(contribution: BoardContribution): BoardCell {
  return {
    item_code: contribution.item_code,
    bucket_key: contribution.key,
    total_qty: contribution.qty_outstanding ?? contribution.qty,
    locations: contribution.locations ?? [],
    contributions: [contribution],
    unplannable_count: contribution.unplannable ? 1 : 0,
    contested_count: contribution.contested ? 1 : 0,
  };
}

/**
 * The board as a LIST, not a grid: one row per contributing line across every cell (D2,
 * PLAN-demo-followups-19aug-ladder-v2 "a list view of the board so Approve all can be seen
 * from an overview").
 *
 * The captain's ask was to see the whole draft at once - the grid answers "what does this
 * product owe by this date", the list answers "what is about to be committed, across every
 * order, in one scan". Same draft, same `onDecide`, same write path (there is none here
 * either - see `FulfilmentBoardPanel`'s own note): this is a second READING of the identical
 * data, never a second source of it.
 */
export interface FulfilmentBoardListViewProps {
  contributions: BoardContribution[];
  draft: BoardDraft;
  onDecide: (key: string, decision: BoardDecision | null) => Promise<boolean> | void;
  /**
   * D15: the quiet-bulk path the board-wide "Save all suggested" and this view's own header
   * button both post through, so several rows saved together toast once ("N lines saved · M
   * to confirm") rather than the N separate "Line N saved" toasts D14 shipped with.
   */
  onDecideMany: (keys: string[]) => Promise<{ saved: number; failed: number }>;
  /**
   * S3 (D1): the Decide strip's own save - one PUT per row, a decision per key already
   * composed by `BoardDecideControl` (`decideComposition`), through the SAME chunked-of-5
   * loop `onDecideMany` runs. No bulk endpoint (AC-18); this is the loop's own generalisation
   * to a decision per row rather than always the engine's suggestion.
   */
  onDecideBatch: (
    entries: { key: string; decision: BoardDecision }[],
  ) => Promise<{ savedKeys: string[]; failed: { key: string; why: string }[] }>;
  /**
   * What the re-uploaded book did to each line, keyed by planning line (AC-C9). The row
   * shows it as a hazard icon in the column that moved, and the lightbox behind the icon
   * says the rest - the same component the grid cell uses.
   */
  annotations?: Map<string, BoardChangeAnnotation[]>;
  /**
   * S6 (PLAN-scm-oi-worklist-excel-parity.md R-J): the board's ONE search box, beside the
   * title, drives Grid and List alike now - this filters `contributions` with it BEFORE
   * handing them to `PanelDataGrid`, and `PanelDataGrid` is never given its own `searchOf`
   * any more, so there is no second box to type into.
   */
  externalSearch?: string;
  /**
   * Resets `PanelDataGrid`'s page on a change - `externalSearch` on its own, unless the
   * caller narrows `contributions` by something else too (`FulfilmentBoardPanel`'s kind
   * filter, on top of its own search): that caller passes its OWN composite key instead,
   * since a `contributions` array that changed only because of THAT filter would otherwise
   * leave the reader on whatever page 3 now shows instead of the top of the new list.
   */
  pageResetKey?: string;
  /**
   * Board-confirm-left-out AC-5: the row the left-out banner's own link asked to see, opened
   * here and scrolled into view. The BOARD says which row; this view is the one that already
   * owns the expansion state, so it is the one that acts on it.
   */
  focusKey?: string | null;
  /** Fired once `focusKey` has been opened, so the caller can clear it for the next click. */
  onFocusHandled?: () => void;
  /**
   * The board's own `pool_share_pct` (LADDER v8, R-K), threaded through to the per-line
   * Stock dialog exactly as the grid's own cell dialog receives it - a line's `locations`
   * can carry a site-pool row as readily as a cell's can.
   */
  poolSharePct?: number;
  /**
   * #1312: whether the signed-in reader may upload/remove a line's own attachments
   * (`projects.projects.edit`) - resolved by the caller (`FulfilmentBoardPanel`, which
   * already owns every other permission check on this screen) rather than read here,
   * so this view stays a pure renderer over its props like every other reading of the
   * board. Defaults to false: a caller that forgets to pass it gets a read-only lightbox,
   * never an upload surface nobody checked.
   */
  canEditAttachments?: boolean;
  /**
   * #1312 (AC-U2, fix round 1 should-fix 3): every visible line's own files, keyed
   * by core line id - ONE lookup call `FulfilmentBoardPanel` makes over the WHOLE
   * selection (`allContributions`, never this view's own search-filtered subset),
   * under the app's real `QueryClientProvider`. This view stays a pure renderer
   * over the result, like every other prop here - no hook call of its own. Optional,
   * defaulting to `{}` (empty), only for a caller that has not wired the lookup
   * (an older unit test rendering this view standalone) - the real caller always
   * supplies it.
   */
  attachmentsByLine?: SoLineAttachmentsByLine;
  /**
   * The board's ONE search box, drawn in this list's toolbar (`searchSlot`) instead of the page
   * header, exactly as the Products list draws its own. Omitted, the list shows no box.
   */
  search?: { value: string; onChange: (next: string) => void; placeholder: string };
  /** The Status filter's state, when the board owns it (shared with the grid view). */
  status?: { value: string[]; onChange: (next: string[]) => void };
  /**
   * The Saved | All toggle (owner hand test, 1 Oct 2026), drawn after the search box. Saved = the
   * lines Confirm sends, All = every line. Omitted, the list
   * shows every line and draws no toggle.
   */
  scope?: {
    value: BoardScope;
    onChange: (next: BoardScope) => void;
    savedCount: number;
    allCount: number;
    /** Whether Confirm sends this line: the board's ONE `pressPostsContribution` predicate. */
    isSaved: (contribution: BoardContribution) => boolean;
  };
}

export function FulfilmentBoardListView({
  contributions,
  draft,
  onDecide,
  onDecideMany,
  onDecideBatch,
  annotations,
  externalSearch,
  pageResetKey,
  focusKey,
  onFocusHandled,
  poolSharePct,
  canEditAttachments = false,
  attachmentsByLine = {},
  search,
  status,
  scope,
}: FulfilmentBoardListViewProps) {
  /**
   * AC-RS-42: the Stock button and the "To plan" figure both open the SAME dialog the grid
   * view's cell strip does, scoped to this one line - never a second table reinventing what
   * `BoardCellBreakdownDialog` already draws.
   */
  const [openContribution, setOpenContribution] = React.useState<BoardContribution | null>(
    null,
  );
  const openCell = React.useMemo(
    () => (openContribution ? boardCellForContribution(openContribution) : null),
    [openContribution],
  );

  /**
   * Which rows are open - the same STATE the cell breakdown keeps, and the same panel inside
   * it, opened as MANY at a time here (AC-C12: Expand all would mean nothing on a list that
   * closes each row as the next one opens). The list used to carry Approve / Amend / Reject buttons in its Verdict
   * column and open the amend MODAL over the board; a decision is taken in the row on both
   * readings now, or the two would teach different gestures for one act - including the
   * question asked before an unsaved composition is thrown away (C5).
   */
  // S6: the row filter itself, off the board's own search box - `contributionMatchesSearch`
  // is the SAME matcher `rowMatchesSearch` reads for the grid's rows, so the two views can
  // never disagree about what one search term narrows to.
  // The Status filter (owner, 30 Sep 2026): the SAME state the Verdict pill and its sort read
  // (`verdictOf`), so a status can never be filtered by one rule and shown by another.
  // Controlled by the board when it hands `status` in (so the grid view shares it), else local.
  const [localStatusFilter, setLocalStatusFilter] = React.useState<string[]>([]);
  const statusFilter = status?.value ?? localStatusFilter;
  const setStatusFilter = status?.onChange ?? setLocalStatusFilter;
  const filteredContributions = React.useMemo(
    () =>
      contributions.filter(
        (contribution) =>
          contributionMatchesSearch(contribution, externalSearch ?? '') &&
          (!scope ||
            scope.value === 'all' ||
            scope.isSaved(contribution) === (scope.value === 'saved')) &&
          (statusFilter.length === 0 ||
            statusFilter.includes(verdictOf(contribution, draft[contribution.key] ?? null))),
      ),
    [contributions, externalSearch, statusFilter, draft, scope],
  );

  // Should fix 3 (review round 1, fixed again round 2): AC-10's "the order follows the list's
  // current sort" means the grid's OWN sort, not the incoming prop order - `PanelDataGrid`
  // holds that state, so this mirrors it back via `onSortedRowsChange` rather than re-deriving
  // it here. Seeded from `filteredContributions` so Decide has an order to claim by before the
  // grid's first effect runs.
  //
  // NO reset effect on `filteredContributions` any more (review round 2, Should fix 1): React
  // runs a child's effects before its parent's, so `PanelDataGrid`'s own `onSortedRowsChange`
  // effect (which fires on every rows change too, since the sort recomputes against the new
  // rows) landed first and a parent reset right after it threw the sorted order away again -
  // AC-10's claim order fell back to prop order on the very save it was supposed to survive.
  // The child effect alone is enough: it already re-fires whenever `filteredContributions`
  // changes, because the sort it reads is derived from those same rows.
  const [sortedContributions, setSortedContributions] = React.useState(filteredContributions);

  const expansion = useDecisionRowExpansion({ multiple: true });
  const {
    expanded,
    setExpanded,
    openKeys,
    dirtySetterFor,
    requestRow,
    expandAll,
    requestCollapseAll,
  } = expansion;

  /** Which row the scroll below has already fired for, so a re-render does not repeat it. */
  const lastScrolledFocusKey = React.useRef<string | null>(null);

  /**
   * Board-confirm-left-out AC-5: opens `focusKey`'s row alongside whatever is already open
   * (the multi-open reading, so nothing already on screen is thrown away).
   */
  React.useEffect(() => {
    if (!focusKey) {
      lastScrolledFocusKey.current = null;
      return;
    }
    setExpanded((current) => {
      const record = typeof current === 'boolean' ? {} : current;
      if (record[focusKey]) return current;
      return { ...record, [focusKey]: true };
    });
  }, [focusKey, setExpanded]);

  /**
   * The scroll itself, once the row `focusKey` named is actually open (`openKeys`, not
   * `focusKey` alone): the effect above ASKS for it to expand, and the row does not exist in
   * the DOM until the render that follows that state change lands - this effect re-fires on
   * exactly that render, because `openKeys` is what changed. Guarded by the ref above, not by
   * clearing `focusKey` on the caller's side alone: a second click naming the SAME already-
   * open row has to scroll again, and this is what tells "still the same request" apart from
   * "asked for again".
   *
   * S3 (fix round 2, reviewer): the ref is set and `onFocusHandled` fired only once the QUERY
   * ACTUALLY FOUND A NODE, not merely once the row is "open" - `openKeys` says the row is
   * expanded, not that it is currently RENDERED, and a search still narrowing the list to
   * something else (B1 above) or a page still on its way to the right one both leave `open`
   * true with nothing in the DOM yet. Marking it done regardless left later renders - the
   * search settling, the page landing - with no signal left to scroll on. `filteredContributions`
   * is an explicit dependency for the same reason: `openKeys` alone does not change when the
   * ROWS do (a search settling, a fresher board read), so a miss on THAT account would
   * otherwise never get a second attempt.
   */
  React.useEffect(() => {
    if (!focusKey || !openKeys.includes(focusKey)) return;
    if (lastScrolledFocusKey.current === focusKey) return;
    const node = document.querySelector(`[data-testid="line-decision-${focusKey}"]`);
    if (!node) return;
    lastScrolledFocusKey.current = focusKey;
    node.scrollIntoView({ block: 'center' });
    onFocusHandled?.();
  }, [focusKey, openKeys, onFocusHandled, filteredContributions]);

  /**
   * D14 (the captain: a quick save for the lines that need nothing amended). Selection is
   * the SAME `RowSelectionState` `BoardCellBreakdownDialog` keeps for its own bulk verbs -
   * ticked here, applied with `suggestedDecisionFor`, cleared once applied. A row that is
   * already covered or already carries a draft is not offered a box at all (`buildSelectColumn`
   * below): there is nothing a quick save would change on either.
   */
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});
  // Only rows on screen: a row ticked and then hidden by the Status filter is not part of what
  // Decide would act on, so it is not counted or saved (its tick is kept for when it returns).
  const selectedKeys = React.useMemo(() => {
    const visible = new Set(filteredContributions.map((entry) => entry.key));
    return Object.keys(rowSelection).filter((key) => rowSelection[key] && visible.has(key));
  }, [rowSelection, filteredContributions]);
  // A tick on a row the segment, the Status filter or the search has since hidden is dropped, so
  // the strip's "N selected" is exactly the rows Decide would act on.
  React.useEffect(() => {
    const visible = new Set(filteredContributions.map((entry) => entry.key));
    setRowSelection((current) => {
      const next: RowSelectionState = {};
      let dropped = false;
      for (const key of Object.keys(current)) {
        if (!current[key]) continue;
        if (visible.has(key)) next[key] = true;
        else dropped = true;
      }
      return dropped ? next : current;
    });
  }, [filteredContributions]);
  // S3 (D1, R9): saved rows untick, skipped rows stay ticked - so a Decide press narrows the
  // selection to exactly what it could not cover, ready for a second pick.
  const untickSaved = React.useCallback((savedKeys: string[]) => {
    if (savedKeys.length === 0) return;
    setRowSelection((current) => {
      const next = { ...current };
      for (const key of savedKeys) delete next[key];
      return next;
    });
  }, []);

  /**
   * Open this line's decision panel, and only open it (AC-B10): the pencil is not a toggle -
   * a planner who presses it on a row that is already open asked to see the panel, and
   * closing it under them would read as the press having missed. `requestRow` still runs the
   * unsaved-work question on the way (the list opens many at once, so opening a second row
   * throws nothing away and asks nothing).
   */
  const openRow = React.useCallback(
    (key: string) => {
      if (openKeys.includes(key)) return;
      requestRow(key);
    },
    [openKeys, requestRow],
  );

  /**
   * The change icon this row shows in this column, or nothing (AC-C9).
   *
   * The column is read off what MOVED, through the same `changedFieldsOf` the lightbox
   * prints, so a date icon can never sit on a row whose date did not move. A line the book
   * touched without moving its quantity or its date still has a composed suggestion to
   * read, and that is what the Suggested column's icon is for.
   */
  const changeIcons = React.useCallback(
    (
      contribution: BoardContribution,
      column: 'required_date' | 'outstanding' | 'suggested',
    ) => {
      // The planning line first, then the sales order and line number - the address a row on
      // an order nobody has adopted carries instead (R3).
      const lineId = contribution.project_line_id;
      const forLine =
        (lineId ? annotations?.get(lineId) : undefined) ??
        annotations?.get(lineKeyOf(contribution.so_number, contribution.line_no)) ??
        [];
      const filtered = forLine.filter((annotation) => {
        const keys = changedFieldsOf(annotation).map((field) => field.key);
        if (column === 'required_date') return keys.includes('date');
        if (column === 'outstanding') return keys.includes('qty');
        return !keys.includes('date') && !keys.includes('qty');
      });
      if (filtered.length === 0) return null;
      // ONE icon per column, however many pending batch rows moved it (AC-D5, owner finding
      // 22 Sep: "why so many warning signs"). Two rows that each moved this line's date used
      // to draw two identical triangles side by side in one cell, which reads as two
      // problems; the lightbox behind the single icon lists them all instead.
      return (
        <BoardChangeTable
          key={`${filtered[0].rowId}-${column}`}
          annotations={filtered}
          column={column}
          compact
        />
      );
    },
    [annotations],
  );

  const columns = React.useMemo<ColumnDef<BoardContribution>[]>(
    () => [
      // The repo's own select column (the users list uses the same one), so a quick save is
      // a bulk action like any other rather than a second selection mechanism.
      buildSelectColumn<BoardContribution>({
        selectAllRows: true,
        // S3 (D1): widened from `canQuickSave` - a Confirmed or already-saved row is
        // tickable too (R3), so only an unplannable or a cancelled line is refused.
        enableRow: (row) => canDecide(row.original),
        disabledReason: (row) =>
          row.original.unplannable
            ? 'This line cannot be decided here: its sales order states no fulfilment location.'
            : row.original.cancelled
              ? 'This line was removed from the sales order.'
              : undefined,
        rowLabel: (row) =>
          `Select ${row.original.so_number} ${soLineLabel(row.original).toLowerCase()}`,
      }),
      // S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-13): the leftmost column, split out
      // of the "Sales order" cell's own `(Line N)` suffix below - AutoCount's own line
      // number, sortable on its own. The rows already ARRIVE in this order (`orderListRows`,
      // `FulfilmentBoardPanel`'s own `listContributions`), so no `sorting` state is seeded
      // here - the caller's own order IS the default (`PanelDataGrid`'s own contract).
      {
        id: 'line',
        meta: { headerTitle: 'Line' },
        // #1362 item 5: AutoCount's own line number, never the planning row index;
        // "row N" only where AutoCount gave the line none.
        accessorFn: (row) => row.so_line_no ?? row.line_no,
        header: ({ column }) => <DataGridColumnHeader title="Line" column={column} />,
        cell: ({ row }) => (
          <span
            className="block tabular-nums"
            title={
              soLineNoIsSynced(row.original)
                ? undefined
                : 'AutoCount line number not synced for this order'
            }
          >
            {soLineNoText(row.original)}
          </span>
        ),
        size: 70,
        minSize: 60,
        enableSorting: true,
      },
      {
        id: 'so_number',
        accessorFn: (row) => row.so_number,
        header: ({ column }) => <DataGridColumnHeader title="Sales order" column={column} />,
        cell: ({ row }) => {
          const contribution = row.original;
          const body = (
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-1.5">
                {/* A state indicator, not a control: the whole row opens the decision. */}
                {row.getIsExpanded() ? (
                  <ChevronDown
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                ) : (
                  <ChevronRight
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                )}
                {/* S6 (AC-B6-13/AC-B6-14): the number ONLY now - the line it carried
                    (AC-C13) moved into its own leftmost column above, so repeating it here
                    would say it twice on the same row. */}
                <span
                  className="truncate text-sm font-medium tabular-nums"
                  title={contribution.so_number}
                >
                  {contribution.so_number}
                </span>
                {/* The same tick the grid puts on a fully-decided cell, here per row: one
                    row IS one contribution, so it is decided or it is not. */}
                <BoardDecidedMarker
                  revisions={decidedRevisions([contribution])}
                />
              </div>
            </div>
          );
          // S6 (AC-B6-3/AC-B6-14): lands on this exact line of the sales order's own Lines
          // tab. `line_id` is already the CORE sales-order line id (its own doc comment,
          // `fulfilmentPlanning.types.ts`) - no backend change needed for this one.
          const href = contribution.sales_order_id
            ? `/scm/sales-orders/${contribution.sales_order_id}${
                contribution.line_id ? `?tab=lines&line=${contribution.line_id}` : ''
              }`
            : null;
          return href ? (
            <Link
              href={href}
              onClick={(event) => event.stopPropagation()}
              className="block min-w-0 hover:underline"
            >
              {body}
            </Link>
          ) : (
            body
          );
        },
        size: 150,
        minSize: 120,
        // Owner ruling, 22 Sep 2026: every column on this list sorts.
        enableSorting: true,
        meta: {
          headerTitle: 'Sales order',
          // The SAME editor the cell breakdown expands, so a decision reads and is taken
          // identically whichever way the planner came at the line - the per-location
          // Available included (C4). The figures ride on the CONTRIBUTION, netted of this
          // line's own quantity, so the list does not have to know which cell the line sits
          // in to quote the right pile.
          expandedContent: (contribution: BoardContribution) =>
            // A cancelled line has no decision to take (R3): the book removed it, and
            // Confirm retires it. The row still opens, and says that rather than offering
            // controls that would compose supply for a quantity nobody is owed.
            contribution.cancelled ? (
              <p className="px-4 py-3 text-sm text-muted-foreground">
                This line was removed from the sales order. Confirm retires it; there is
                nothing left to decide for it.
              </p>
            ) : (
              <BoardLineDecisionPanel
                contribution={contribution}
                decision={draft[contribution.key] ?? null}
                locations={contribution.locations ?? []}
                onDecide={(next) => onDecide(contribution.key, next)}
                onDirtyChange={dirtySetterFor(contribution.key)}
              />
            ),
        },
      },
      {
        id: 'agent',
        meta: { headerTitle: 'Agent' },
        accessorFn: (row) => row.agent_code ?? '',
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
        enableSorting: true,
      },
      {
        id: 'customer',
        meta: { headerTitle: 'Customer' },
        accessorFn: (row) => row.customer_name ?? '',
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
        minSize: 130,
        enableSorting: true,
      },
      {
        id: 'product',
        meta: { headerTitle: 'Product' },
        accessorFn: (row) => row.item_code,
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          // AC-RL-06 amended (17 Sep, "beside the PRODUCT"), and the whole ladder since
          // AC-A1 to AC-A7: ONE word for how far this line's own inquiry has got, on every
          // line regardless of verdict, draft or decision - see `boardOrderInquiryWord`'s
          // own note for why this reads the inquiry directly rather than through the
          // Decided cell's gated `contributionInquiryDecision`.
          const word = boardOrderInquiryWord(row.original.order_inquiry);
          return (
            <span className="flex min-w-0 items-center gap-1.5">
              <span
                className="block min-w-0 truncate tabular-nums"
                title={row.original.item_code}
              >
                {row.original.item_code}
              </span>
              {word ? (
                <Badge
                  size="sm"
                  appearance="light"
                  variant="secondary"
                  className="shrink-0"
                  // AC-A3 to AC-A5, R1: the chip is a WORD, and the document number(s) or
                  // the inquiry's own number ride in the tooltip - there is no room for
                  // either beside a product code at 375px.
                  title={word.title}
                >
                  {word.word}
                </Badge>
              ) : null}
            </span>
          );
        },
        size: 140,
        minSize: 110,
        enableSorting: true,
      },
      // S6 (AC-B6-15): the live OI row this line raised, linking straight to it. A line
      // with no live row - nothing raised, or the row it raised has since settled/gone -
      // reads a plain dash, never a guess.
      {
        id: 'order_inquiry',
        meta: { headerTitle: 'OI' },
        accessorFn: (row) => row.order_inquiry?.inquiry_no ?? '',
        header: ({ column }) => <DataGridColumnHeader title="OI" column={column} />,
        cell: ({ row }) => {
          const inquiry = row.original.order_inquiry;
          if (!inquiry?.inquiry_no) return <span className="text-muted-foreground">-</span>;
          // A row whose payload carries neither id - one raised before inquiries were
          // numbered, or a header the join could not reach - is plain text rather than a
          // link that lands nowhere in particular.
          const href =
            inquiry.inquiry_id && inquiry.row_id
              ? `/project-sales/order-inquiries/${inquiry.inquiry_id}?row=${inquiry.row_id}`
              : null;
          if (!href) {
            return (
              <span className="block truncate tabular-nums" title={inquiry.inquiry_no}>
                {inquiry.inquiry_no}
              </span>
            );
          }
          return (
            <Link
              href={href}
              onClick={(event) => event.stopPropagation()}
              className="block truncate tabular-nums text-primary hover:underline"
              title={inquiry.inquiry_no}
            >
              {inquiry.inquiry_no}
            </Link>
          );
        },
        size: 120,
        minSize: 100,
        enableSorting: true,
      },
      {
        id: 'required_date',
        meta: { headerTitle: 'Required date' },
        accessorFn: (row) => row.required_date ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Required date" column={column} />,
        cell: ({ row }) => (
          <span className="flex min-w-0 items-center gap-1">
            {row.original.required_date ? (
              <span className="block min-w-0 truncate tabular-nums">
                {formatDateInMalaysia(row.original.required_date)}
              </span>
            ) : (
              <span className="text-muted-foreground">No date</span>
            )}
            {changeIcons(row.original, 'required_date')}
          </span>
        ),
        size: 130,
        minSize: 110,
        enableSorting: true,
      },
      {
        id: 'owed_qty',
        meta: { headerTitle: 'Outstanding qty' },
        // Numeric, not the raw string `qty_outstanding`/`qty` ride on - a lexicographic
        // sort would put "20" ahead of "9" (owner ruling, 22 Sep 2026).
        accessorFn: (row) => Number(row.qty_outstanding ?? row.qty ?? 0),
        // #1119 (owner ruling, 22 Sep 2026): the sortable `DataGridColumnHeader`, titled
        // "Outstanding qty" - every column header carries a sort control, this one included.
        // AC-RS-42 names the clickable figure inside the cell "the To plan figure"; that is
        // the qty button below, not the column's own title.
        header: ({ column }) => <DataGridColumnHeader title="Outstanding qty" column={column} />,
        cell: ({ row }) => {
          const contribution = row.original;
          return (
            <span className="flex min-w-0 items-center gap-1">
              <button
                type="button"
                className="block min-w-0 truncate text-start tabular-nums hover:underline"
                data-testid={`board-list-to-plan-${contribution.key}`}
                onClick={(event) => {
                  event.stopPropagation();
                  setOpenContribution(contribution);
                }}
              >
                {contribution.qty_outstanding ?? contribution.qty}
              </button>
              {changeIcons(contribution, 'outstanding')}
              {/* AC-RS-42: a Stock icon-button per row, opening the SAME dialog the grid
                  view's cell strip does - purchasing used to leave this screen and open
                  the grid just to check one line's own stock. Icon only, ghost variant
                  (round 2, owner ruling after hand-testing round 1) - matches the
                  verdict-row pencil in `BoardVerdictActions.tsx`, never the labelled
                  outline button this used to be. */}
              <Button
                type="button"
                mode="icon"
                variant="ghost"
                size="sm"
                className="shrink-0"
                aria-label="Stock"
                title="Stock"
                data-testid={`board-list-stock-${contribution.key}`}
                onClick={(event) => {
                  event.stopPropagation();
                  setOpenContribution(contribution);
                }}
              >
                <PackageSearch className="size-3.5" aria-hidden />
              </Button>
            </span>
          );
        },
        size: 160,
        minSize: 140,
        enableSorting: true,
      },
      {
        // AC-D4: what the ENGINE said, in PLAN section 2's own words. Split off the old
        // single "Proposal" column, which showed the decision on a decided line and the
        // proposal on an undecided one - so the two could never be compared, which is the
        // one thing the planner opens this view to do.
        id: 'suggested',
        meta: { headerTitle: 'Suggested' },
        // Owner ruling, 22 Sep 2026: sorts on the SAME text the cell prints
        // (`suggestedSortText` below), so the sort order and the words on screen can never
        // disagree.
        accessorFn: (row) => suggestedSortText(row),
        header: ({ column }) => <DataGridColumnHeader title="Suggested" column={column} />,
        cell: ({ row }) => {
          const contribution = row.original;
          if (contribution.unplannable) {
            // The ladder was never walked for it (AC-FP16), so there is nothing to suggest -
            // and the reason is the one thing worth saying in its place.
            return (
              <span className="text-muted-foreground">Needs a location</span>
            );
          }
          const parts = contributionSuggestion(contribution);
          if (!parts) {
            // A decision frozen before the proposal was recorded. Not "nothing suggested".
            return <span className="text-muted-foreground">Not recorded</span>;
          }
          const text = describeSupply(parts, contribution.fulfilment_location);
          // NO BAR (AC-C13). The composition is already written out beside it in words, and
          // the bar cost the row a second text line to say the same thing less precisely.
          return (
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="block min-w-0 truncate" title={text}>
                {text || (
                  <span className="text-muted-foreground">Nothing proposed</span>
                )}
              </span>
              {contribution.buy_origin === 'local' && hasBuy(parts) && (
                <Badge variant="secondary">Local</Badge>
              )}
              {changeIcons(contribution, 'suggested')}
            </span>
          );
        },
        size: 240,
        minSize: 170,
        enableSorting: true,
      },
      {
        id: 'decided',
        meta: { headerTitle: 'Decided' },
        // Owner ruling, 22 Sep 2026: sorts on the SAME text the cell prints
        // (`decidedSortText` below).
        accessorFn: (row) => decidedSortText(row, draft[row.key] ?? null),
        header: ({ column }) => <DataGridColumnHeader title="Decided" column={column} />,
        cell: ({ row }) => {
          const contribution = row.original;
          const drafted = draft[contribution.key] ?? null;
          const parts = contributionDecision(contribution, drafted);
          if (!parts) {
            // DECIDED BY THE BOOK, not by a board (14 Sep 2026 ruling): a line carrying a
            // live order inquiry row has no composition to print, so the slot names the
            // instruction purchasing already holds. "Not decided" over it would invite a
            // second Buy for a line somebody has already been told to buy.
            const inquiry = contributionInquiryDecision(contribution);
            if (inquiry) {
              const text = inquiry.inquiry_no ?? 'Unnumbered inquiry';
              // AC-RL-06 amended (17 Sep review round), widened by AC-A7: the stage word -
              // `received` / `used` / `SPO` / `PO` / `OI` - sits beside the PRODUCT (this
              // row's own `product` column, driven by `boardOrderInquiryWord`) so it reads
              // on every line, not only the ones that land in this branch; printing it here
              // too would say it twice on a line that does.
              return (
                <span className="flex min-w-0 items-center gap-1 tabular-nums">
                  <span className="block min-w-0 truncate" title={text}>
                    {text}
                  </span>
                </span>
              );
            }
            return <span className="text-muted-foreground">Not decided</span>;
          }
          // The composition alone, in section 2's words. NOT "Confirmed rev 1 · Buy 43":
          // the revision is already on the Verdict column and on the row's tick, and
          // repeating it here would cost the width the composition needs. No bar either
          // (AC-C13) - the words carry it, and the grid still draws one where a cell has
          // the room.
          const text = describeSupply(parts, contribution.fulfilment_location);
          return (
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="block min-w-0 truncate" title={text}>
                {text}
              </span>
              {contribution.buy_origin === 'local' && hasBuy(parts) && (
                <Badge variant="secondary">Local</Badge>
              )}
            </span>
          );
        },
        size: 240,
        minSize: 170,
        enableSorting: true,
      },
      {
        id: 'rank',
        meta: { headerTitle: 'Rank' },
        accessorFn: (row) => row.rank_score,
        header: ({ column }) => <DataGridColumnHeader title="Rank" column={column} />,
        cell: ({ row }) =>
          row.original.covered || row.original.unplannable ? (
            <span className="text-muted-foreground">-</span>
          ) : (
            <span className="tabular-nums">
              {row.original.rank_score.toFixed(2)}
            </span>
          ),
        size: 80,
        minSize: 70,
        enableSorting: true,
      },
      {
        id: 'verdict',
        meta: { headerTitle: 'Verdict' },
        // AC-7: sorted by the SAME state the pill renders (`verdictOf`, `BoardDecisionPill`) -
        // never a second reading of "how far this line has got", which is exactly how the
        // column and the pill it sorts could come to disagree.
        accessorFn: (row) => VERDICT_SORT_RANK[verdictOf(row, draft[row.key] ?? null)],
        enableSorting: true,
        header: ({ column }) => <DataGridColumnHeader title="Verdict" column={column} />,
        // A PILL, and the row's own actions beside it (`BoardVerdictActions`, AC-B11): the
        // trio on a line the engine or the book still proposes, Undo on one something has
        // been written for, and Change decision on every line that has one to take. Shared
        // with the cell breakdown's Decision column so the two cannot drift.
        cell: ({ row }) => {
          const contribution = row.original;
          const key = contribution.key;
          return (
            <div className="flex min-w-0 items-center gap-1">
              <BoardDecisionPill contribution={contribution} decision={draft[key] ?? null} />
              {/* AC-DT-5 (`PLAN-oi-decision-trail-ui.md`, round 2): the History icon,
                  right after the verdict chip - shown only once there is a trail worth
                  opening (a decision, a draft, an OI row, OR the line simply being
                  covered - N1, round 3: a fully-reserved / local-buy covered line can
                  carry `covered: true` with no `decision` object of its own and no OI
                  row, and still have a confirmed revision behind it worth tracing); a
                  bare suggested line has none of the four and stays hidden. */}
              {contribution.decision ||
              contribution.draft ||
              contribution.order_inquiry ||
              contribution.covered ? (
                <DecisionTrailButton
                  coreLineId={contribution.line_id ?? null}
                  itemCode={contribution.item_code}
                  className="shrink-0"
                />
              ) : null}
              <BoardVerdictActions
                contribution={contribution}
                decision={draft[key] ?? null}
                onDecide={(next) => onDecide(key, next)}
                onChange={() => openRow(key)}
              />
              {/* #1312 (AC-U1): a row with no core line id (never mirrored yet) gets
                  no paperclip - there is nowhere to file a clarification against. */}
              {contribution.line_id ? (
                <SoLineAttachmentsButton
                  lineId={contribution.line_id}
                  label={`${contribution.so_number} ${soLineLabel(contribution)} ${contribution.item_code}`}
                  attachments={attachmentsByLine[contribution.line_id] ?? []}
                  canEdit={canEditAttachments}
                />
              ) : null}
            </div>
          );
        },
        // AC-C1/AC-C2: wide enough for the pill and the three icons any one state offers
        // (Accept + Reject + Change decision, or Undo + Change decision), and RESIZABLE -
        // the column carried `enableResizing: false` while every other one on this grid
        // could be dragged, which read as the grid being broken on the one column a planner
        // acts in.
        size: 240,
        minSize: 170,
      },
    ],
    [
      changeIcons,
      dirtySetterFor,
      draft,
      onDecide,
      openRow,
      setOpenContribution,
      attachmentsByLine,
      canEditAttachments,
    ],
  );

  return (
    <>
    <PanelDataGrid
      columns={columns}
      rows={filteredContributions}
      getRowId={(row) => row.key}
      pageResetKey={`${pageResetKey ?? externalSearch ?? ''}|${statusFilter.join(',')}|${scope?.value ?? ''}`}
      // Rank is a planner's tiebreak, not something to read on every row: hidden until asked.
      initialColumnVisibility={{ rank: false }}
      listingKey="projects.projects.view::project-fulfilment-board-list-v2"
      emptyTitle={
        scope?.value === 'saved'
          ? statusFilter.length > 0
            ? 'No saved decisions match the filter'
            : 'Nothing to confirm yet'
          : 'Nothing is outstanding on this board'
      }
      rowSelection={rowSelection}
      onRowSelectionChange={setRowSelection}
      enableRowSelection={(row) => canDecide(row.original)}
      // The app's own list toolbar (`DataGridListToolbar`, PLAN-unified-list-toolbar-UAC.md D2/D3):
      // search, the Saved | All toggle, Filters (Status) and Columns on the left, Expand and
      // Collapse as `leftActions` (the component's own slot for a grid whose rows expand, since
      // they change what the table shows), Decide as the primary action. While rows are ticked
      // its bulk strip ("N selected", Clear) replaces the left cluster, so Decide keeps its place.
      toolbar={({ table }) => (
        <DataGridListToolbar
          table={table}
          exportConfig={false}
          showColumns
          keepSearchWhileSelected
          searchSlot={
            search || scope ? (
              <>
                {search && (
                  <ListSearchInput
                    value={search.value}
                    onChange={search.onChange}
                    placeholder={search.placeholder}
                    aria-label={search.placeholder}
                    className="w-64"
                  />
                )}
                {scope && (
                  <BoardScopeToggle
                    value={scope.value}
                    onChange={scope.onChange}
                    savedCount={scope.savedCount}
                    allCount={scope.allCount}
                  />
                )}
              </>
            ) : undefined
          }
          filters={{
            kind: 'custom',
            active: statusFilter.length > 0,
            activeCount: statusFilter.length,
            activeSummary:
              statusFilter.length > 0
                ? {
                    label: `Status: ${statusFilter
                      .map(
                        (value) =>
                          VERDICT_FILTER_OPTIONS.find((option) => option.value === value)?.label ??
                          value,
                      )
                      .join(', ')}`,
                    onClear: () => setStatusFilter([]),
                  }
                : undefined,
            content: (
              <SearchableMultiSelect
                value={statusFilter}
                onChange={setStatusFilter}
                options={VERDICT_FILTER_OPTIONS}
                placeholder="Status"
                size="sm"
              />
            ),
          }}
          leftActions={
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                mode="icon"
                data-testid="board-list-expand-all"
                title="Expand all"
                aria-label="Expand all"
                disabled={openKeys.length >= filteredContributions.length}
                onClick={() => expandAll(filteredContributions.map((row) => row.key))}
              >
                <ChevronsUpDown className="size-4" aria-hidden />
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                mode="icon"
                data-testid="board-list-collapse-all"
                title="Collapse all"
                aria-label="Collapse all"
                disabled={openKeys.length === 0}
                onClick={requestCollapseAll}
              >
                <ChevronsDownUp className="size-4" aria-hidden />
              </Button>
            </>
          }
          // S3 (D1, R1, R4): Decide replaces the old "Save as suggested" button - As suggested
          // is its first menu item, and Decide itself is ALWAYS rendered, disabled with a
          // tooltip while nothing is ticked.
          primaryAction={
            <BoardDecideControl
              contributions={sortedContributions}
              selectedKeys={selectedKeys}
              draft={draft}
              onSave={onDecideBatch}
              onSaved={untickSaved}
              onClear={() => setRowSelection({})}
              embedded
            />
          }
        />
      )}
      expanded={expanded}
      onExpandedChange={setExpanded}
      onRowClick={(row) => requestRow(row.key)}
      pageSize={25}
      // Owner ruling, 22 Sep 2026: every column on this list sorts.
      sortable
      onSortedRowsChange={setSortedContributions}
      // AC-5, fix round 1: the banner's link names a ROW, not a page - `PanelDataGrid` jumps
      // to whichever page currently holds it, in its own sorted order, so a left-out line
      // beyond page 1 is reachable rather than a dead link.
      focusRowId={focusKey}
    />
    <UnsavedDecisionPrompt state={expansion} />
    {/* AC-RS-42: the grid view's OWN dialog, unchanged, opened here for one line at a time -
        the expanded decision panel above is untouched. */}
    {openCell && (
      <BoardCellBreakdownDialog
        cell={openCell}
        bucketLabel={
          openContribution?.required_date
            ? formatDateInMalaysia(openContribution.required_date)
            : 'No date'
        }
        draft={draft}
        poolSharePct={poolSharePct}
        onDecide={onDecide}
        onDecideMany={onDecideMany}
        onClose={() => setOpenContribution(null)}
      />
    )}
    </>
  );
}

/** Whether a Buy actually contributed to this composition (S3, R-Local). */
function hasBuy(parts: SupplyPart[] | null): boolean {
  return Boolean(parts?.some((part) => part.kind === 'buy' && Number(part.qty) > 0));
}

/**
 * The plain text the Suggested column's cell prints, shared with its own `accessorFn`
 * (owner ruling, 22 Sep 2026: every column sorts) so the sort order can never disagree with
 * the words on screen.
 */
function suggestedSortText(contribution: BoardContribution): string {
  if (contribution.unplannable) return 'Needs a location';
  const parts = contributionSuggestion(contribution);
  if (!parts) return 'Not recorded';
  return describeSupply(parts, contribution.fulfilment_location) || 'Nothing proposed';
}

/** The Decided column's own equivalent of `suggestedSortText` above. */
function decidedSortText(contribution: BoardContribution, drafted: BoardDecision | null): string {
  const parts = contributionDecision(contribution, drafted);
  if (!parts) {
    const inquiry = contributionInquiryDecision(contribution);
    if (inquiry) return inquiry.inquiry_no ?? 'Unnumbered inquiry';
    return 'Not decided';
  }
  return describeSupply(parts, contribution.fulfilment_location);
}
