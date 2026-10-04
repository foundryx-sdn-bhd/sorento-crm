'use client';

import * as React from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { toast } from '@/lib/toast';
import {
  ArrowLeft,
  LayoutGrid,
  List,
  PackageSearch,
  Settings,
  Undo2,
} from 'lucide-react';
import {
  Alert,
  AlertContent,
  AlertDescription,
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
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
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import DeferredActionButton from '@/components/common/DeferredActionButton';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useHasPermission } from '@/hooks/usePermissions';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import {
  PLANNING_BOARD_KEY,
  useConfirmManyMutation,
  usePreviewConfirmManyMutation,
  useFulfilmentPlanningMutations,
  useLineDraftMutation,
  usePlanningBoard,
} from '../../_shared/hooks/useFulfilmentPlanning';
import { usePlanningChangeBatchesByIds } from '../../_shared/hooks/usePlanningChanges';
import { useSoLineAttachmentLookup } from '../../_shared/hooks/useSoLineAttachments';
import { canQuickSave, suggestedDecisionFor } from '../../_shared/lib/boardAmend';
import {
  annotationsByCell,
  annotationsByLine,
  preMarkedKeys,
  uncoverChangedLines,
} from '../../_shared/lib/boardChangeAnnotations';
import {
  boardAxis,
  bucketLabelText,
  pressPostsContribution,
  contributionMatchesSearch,
  confirmSummaryFor,
  decisionHeaderText,
  orderListRows,
  rowMatchesSearch,
  confirmLinesFor,
  rejectedCoveredLineIdsFor,
  confirmNoticeLines,
  failingLineText,
  shiftedDayWindow,
  soLineLabel,
  unpostableDecidedFor,
  type UnpostableLine,
  type UnpostableReason,
} from '../../_shared/lib/fulfilmentBoard';
import { unpostableNotices } from '../../_shared/lib/unpostableNotices';
import type {
  BoardCell,
  BoardContribution,
  BoardDecision,
  BoardDraft,
  BoardGranularity,
  BoardRowAxis,
  ConfirmManyOrderResult,
} from '../../_shared/types/fulfilmentPlanning.types';
import type {
  PlanningChangeBatch,
  PlanningChangeOrder,
} from '../../_shared/types/planningChange.types';
import { BoardCellBreakdownDialog } from './BoardCellBreakdownDialog';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { BoardScopeToggle, type BoardScope } from './BoardScopeToggle';
import { isPreMarkOnly, VERDICT_FILTER_OPTIONS, verdictOf } from './BoardDecisionPill';
import { BoardTransfersPanel } from './BoardTransfersPanel';
import { FulfilmentBoardListView } from './FulfilmentBoardListView';
import { FulfilmentBoardMatrix } from './FulfilmentBoardMatrix';
import { DecisionStrip } from './DecisionStrip';
import { cellCarriesKind, contributionCarriesKind } from '../../_shared/lib/decisionStrip';
import type { SupplyKind } from '../../_shared/lib/supplyVocabulary';

/**
 * Persisted in the URL as `?view=grid` (S6, PLAN-scm-oi-worklist-excel-parity.md R-J).
 * List is the default now - the plan opens on the overview "Approve all" reads, and Grid
 * is the deliberate switch into the pivoted matrix.
 */
type BoardView = 'grid' | 'list';

/**
 * One line of the results block: a server outcome, or an order THIS press could not send.
 *
 * `so_number` is the panel's own addition and is never posted anywhere: an order the press
 * left out has no `pso_id` to look a name up by, and the results block must not fall back to
 * an id nobody can read. A server result carries none and is named off the board as before.
 *
 * `heldBack` (N2, fix round, `PLAN-board-reject-on-confirmed-line.md`): a batch-blocked
 * withdrawal (`heldBackByBatch` below) is NOT a refusal - the order's own lines still went
 * through, or there simply was nothing else to post for it, and the withdrawal itself is
 * still staged, just waiting on the pending change. Renders neutral rather than destructive,
 * and is excluded from the header's ok/total denominator so an order that confirmed
 * everything else does not read as a failure alongside its own success.
 */
type BoardBatchResult = ConfirmManyOrderResult & { so_number?: string; heldBack?: boolean };

/**
 * S6 (R-J): List is the default view; only an explicit `?view=grid` opens Grid. Exported
 * so `OrderInquiriesClient.planner.test.tsx` can pin AC-P1 as a pure function rather than
 * mounting the whole board for a one-line contract.
 */
export function boardViewFrom(value: string | null): BoardView {
  return value === 'grid' ? 'grid' : 'list';
}

/** The reason a disabled "Undo confirm" gear entry states, visibly, inside the item
 * itself (AC-UC-03, review round: a `title` on a `data-disabled` item never renders -
 * `pointer-events-none` kills the tooltip on both a screen reader and a touch device). */
const UNDO_REFUSAL_TITLES: Record<string, string> = {
  linked: 'Purchasing linked a PO line',
  actioned: 'Purchasing marked a row actioned',
  changed: 'A row changed since this confirm',
};

/** The second line a RECONSTRUCTED entry states, unrefused, so the admin who presses it knows
 * what a best-effort undo of a journal-less revision does not bring back (AC-R2-F02,
 * `PLAN-scm-oi-handover-r2-undo.md` S5). A refused entry shows its refusal reason instead -
 * there is room for one second line, and the refusal is the more urgent of the two. */
const RECONSTRUCTED_UNDO_NOTE = 'Saved drafts and row notes are not restored';

/**
 * The calendar control (PLAN 13.3, `date` added S1 PLAN-board-oi-mechanical-22sep.md, owner
 * ruling 22 Sep 2026): exact date is the default, day/week/month stay available exactly as
 * before.
 */
const GRANULARITY_OPTIONS = [
  { value: 'date', label: 'By date' },
  { value: 'day', label: 'By day' },
  { value: 'week', label: 'By week' },
  { value: 'month', label: 'By month' },
];

/**
 * The granularity a URL may name, and what an unknown one becomes.
 *
 * Guarded the way the server guards it: a link carrying `granularity=fortnightly` opens the
 * date board rather than asking for a cut nothing can produce. A hand-edited or stale link is
 * the normal case for a shareable URL, not an attack. `date` is also what an ABSENT param
 * resolves to (AC-B1-1) - a link that already names a granularity keeps meaning what it said
 * (AC-B1-5).
 */
const GRANULARITIES: BoardGranularity[] = ['date', 'day', 'week', 'month'];

/**
 * What the vertical axis can be, and what the reader calls each one.
 *
 * Product first because it is the default and the shape the board shipped as; the other three
 * are the captain's own list, in the order they asked for them.
 */
const ROW_AXIS_OPTIONS = [
  { value: 'product', label: 'Product' },
  { value: 'sales_order', label: 'Sales order' },
  { value: 'customer', label: 'Customer' },
  { value: 'project', label: 'Project' },
];

/** Singular and plural for the "N of M" line, so it names what the rows actually are. */
const ROW_AXIS_NOUNS: Record<BoardRowAxis, string> = {
  product: 'products',
  sales_order: 'sales orders',
  customer: 'customers',
  project: 'projects',
};

const ROW_AXES: BoardRowAxis[] = ['product', 'sales_order', 'customer', 'project'];

function rowAxisFrom(value: string | null): BoardRowAxis {
  return ROW_AXES.includes(value as BoardRowAxis) ? (value as BoardRowAxis) : 'product';
}

function granularityFrom(value: string | null): BoardGranularity {
  return GRANULARITIES.includes(value as BoardGranularity)
    ? (value as BoardGranularity)
    : 'date';
}

/**
 * `source` with every bare pre-mark dropped, as though the line had never been touched (S5,
 * owner ruling 25 Sep 2026, issue #1245, `PLAN-esb-change-row-refresh.md`): after SO419122,
 * "Confirm (119)" read a count that included every `Change proposed` line nobody had actually
 * looked at, and one press handed 49 of them to purchasing. `{ verdict: 'approved', preMarked:
 * true }` is the board's OWN suggestion for such a line, seeded so the pill, the per-row
 * proposal and the change icons keep reading right (`FulfilmentBoardPanel`'s own pre-mark
 * effect, unchanged) - it is never something a person saved. Confirm's own count, its payload,
 * and every toast that echoes the count back all read the draft through this first, so a
 * pre-marked line counts, and posts, only once a real `decide()` write - Save, an explicit
 * reject, or "Save all suggested" itself - has overwritten it with a decision that carries no
 * `preMarked` flag. `canQuickSave`'s own `!draft[key]` rule is what makes "Save all suggested"
 * pick a pre-marked line back up once it reads `undefined` for it here, exactly as it already
 * would for a line nobody had touched at all.
 */
function draftWithoutPreMarks(source: BoardDraft): BoardDraft {
  const out: BoardDraft = {};
  for (const [key, decision] of Object.entries(source)) {
    if (decision?.preMarked) continue;
    out[key] = decision;
  }
  return out;
}

/**
 * Planning several sales orders at once (PLAN section 13).
 *
 * The board is a LENS. It reads across the selection and writes nothing of its own: approve /
 * amend / reject go into a draft held here, and the thing that commits is still the existing
 * per-order confirmation, one call per order, atomic across that order's lines (13.4, 13.6).
 *
 * Confirm is NOT gated on an order being fully decided (13.4, the captain overruling this plan's
 * own recommendation): a planner commits the lines they are sure about precisely so the undecided
 * ones keep flowing to reorder planning. So the rail's "4 of 12 lines decided" is INFORMATION, not
 * a gate, and beside it the screen states plainly what each Confirm would leave behind and where
 * that demand goes. A button that silently committed four lines and dropped eight would be the
 * same lie in the other direction.
 */
export function FulfilmentBoardPanel({
  soNumbers,
  batchId,
  onBack,
}: {
  soNumbers: string[];
  /**
   * The planning-change batch the board was opened ON (`?batch=<id>`, AC-P3-1).
   *
   * Everything it changes is additive: the changed lines' cells carry a Was / Now table and
   * arrive pre-marked, and Confirm applies the batch instead of writing an ordinary revision.
   * A board opened without one is untouched.
   */
  batchId?: string | null;
  onBack: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // #1312 (Q6): the paperclip's own upload/remove is gated on the SAME grant every
  // write on this board already is - resolved here, once, and handed to the list
  // view as a prop rather than read there, so that view stays a pure renderer.
  const canEditAttachments = useHasPermission('projects.projects.edit');
  const [granularity, setGranularity] = React.useState<BoardGranularity>(() =>
    granularityFrom(searchParams.get('granularity')),
  );
  /**
   * Narrowing the PRODUCT ROWS (the captain: "i need the search here also btw").
   *
   * A filter over one already-fetched payload, never a refetch and never a change to the
   * selection: the board is a single response, and asking the server again for a subset of
   * rows it already sent would be slower and could disagree with the cells beside it.
   */
  const {
    value: productSearchInput,
    setValue: setProductSearchInput,
    debouncedValue: productSearch,
    reset: resetProductSearch,
  } = useDebouncedSearch(searchParams.get('product') ?? '');
  const [rowAxis, setRowAxis] = React.useState<BoardRowAxis>(() =>
    rowAxisFrom(searchParams.get('rows')),
  );
  /**
   * Grid | List (D2, PLAN-demo-followups-19aug-ladder-v2 "a list view of the board so
   * Approve all can be seen from an overview"). Persisted in the URL the same way the other
   * dials are, so a link to the list view is shareable.
   */
  const [view, setView] = React.useState<BoardView>(() => boardViewFrom(searchParams.get('view')));
  const [draft, setDraft] = React.useState<BoardDraft>({});
  /**
   * The decision-strip card currently narrowing the grid, or null (AC-D2).
   *
   * Deliberately NOT in the URL, unlike the dials above: it is a way of reading the board in
   * front of you while you work, not a state of the board worth sending to somebody else, and
   * a shared link that arrived pre-filtered would hide the rest of the plan without saying so.
   */
  const [kindFilter, setKindFilter] = React.useState<SupplyKind | null>(null);
  /** The Status filter, shared by the list's Filters control and the grid's filter strip. */
  const [statusFilter, setStatusFilter] = React.useState<string[]>([]);
  /** Saved | All (owner hand test, 1 Oct 2026): All by default, kept across a Confirm press. */
  const [scope, setScope] = React.useState<BoardScope>(() =>
    searchParams.get('scope') === 'saved' ? 'saved' : 'all',
  );
  const [openCell, setOpenCell] = React.useState<BoardCell | null>(null);
  /** Which 30-day window the day view is showing. Undefined lets the server choose the first. */
  const [dayWindow, setDayWindow] = React.useState<string | undefined>(undefined);
  /**
   * The line a click on the left-out banner (AC-5) asked to see, or null. Handed to
   * `FulfilmentBoardListView`, which owns the row expansion this opens - the board only says
   * WHICH row, never how the list gets there.
   */
  const [focusKey, setFocusKey] = React.useState<string | null>(null);
  /**
   * Board-confirm-left-out AC-5: the banner's own link. Switches to List (where every
   * contributing line has its own row, unlike the grid's per-cell/per-product pivot), clears
   * whatever would otherwise hide the row - the kind-strip filter and the product search box -
   * and hands the key to the list, which expands that row and scrolls it into view.
   *
   * B1 (fix round 2, reviewer): `reset('')`, never `setProductSearchInput('')`. `setValue`
   * alone only moves the BOX; `debouncedValue` (what `productSearch` actually reads, and what
   * `visibleListContributions` filters by) still lags it by 200ms
   * (`hooks/useDebouncedSearch.ts`), so the row this press is trying to reach was still
   * filtered out at the moment `PanelDataGrid` went looking for it - the jump found no such
   * row, the scroll no-op'd, and the box only caught up a beat later, too late to help.
   * `reset` sets both halves synchronously, the same escape hatch the hook exists for.
   */
  const focusLeftOutLine = React.useCallback(
    (contribution: BoardContribution) => {
      setView('list');
      setKindFilter(null);
      setStatusFilter([]);
      // A left-out line is one Confirm will not post, so it is not under Saved; All is what
      // guarantees the link reaches it.
      setScope('all');
      resetProductSearch('');
      setFocusKey(contribution.key);
    },
    [resetProductSearch],
  );
  /**
   * Fired by `FulfilmentBoardListView` once it has actually scrolled to `focusKey` (S3) - a
   * stable function (nit, fix round 3 review), the same reason `dirtySetterFor` is cached per
   * key one file over: an inline arrow here is a new prop identity on every render, which
   * would needlessly re-fire any effect keyed on it.
   */
  const handleLeftOutLineFocused = React.useCallback(() => setFocusKey(null), []);

  // The granularity and the product filter travel in the URL, beside the selection the
  // worklist put there, so the WHOLE board is one link (PLAN 13.2, 13.3). `replace`, not
  // `push`: turning a dial is not a place in history to go back to.
  React.useEffect(() => {
    const next = new URLSearchParams(searchParams.toString());
    if (granularity === 'date') next.delete('granularity');
    else next.set('granularity', granularity);
    // Absent when it is the default, the same idiom as the granularity, so a link carries only
    // what the sender actually changed.
    if (rowAxis === 'product') next.delete('rows');
    else next.set('rows', rowAxis);
    if (productSearch.trim()) next.set('product', productSearch.trim());
    else next.delete('product');
    if (view === 'list') next.delete('view');
    else next.set('view', view);
    const query = next.toString();
    if (query === searchParams.toString()) return;
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [granularity, rowAxis, productSearch, view, pathname, router, searchParams]);

  const rawBoard = usePlanningBoard(
    soNumbers,
    granularity,
    // Always the LIVE policy. The preview was a what-if for showing a fair weighting before one
    // was switched on; the fair policy is now the live one, the offer was retired with it, and
    // the banner that carried the way back went with the banner itself.
    false,
    dayWindow ? { dayWindow } : {},
  );

  /**
   * The batch ids this board needs (`PLAN-scm-board-picks-up-pending-change.md`, AC-B1/AC-B2):
   * every order's OWN `pending_change_batch_id` the board response names, unioned with the
   * URL `batchId` - a deep link still wins (AC-B4) even when the board names none for that
   * order, e.g. an APPLIED batch, which the pending-only helper behind `pending_change_batch_id`
   * never names.
   */
  const boardBatchIds = React.useMemo(() => {
    const ids = new Set<string>();
    for (const order of rawBoard.data?.orders ?? []) {
      if (order.pending_change_batch_id) ids.add(order.pending_change_batch_id);
    }
    if (batchId) ids.add(batchId);
    return Array.from(ids);
  }, [rawBoard.data, batchId]);
  const loadedBatches = usePlanningChangeBatchesByIds(boardBatchIds);

  /**
   * One entry PER SO NUMBER, deduped across every loaded batch (S1, reviewer's pass on
   * 39a5d8b07): a deep link's URL `?batch=` can name an APPLIED batch for an order the
   * board's own `pending_change_batch_id` union ALSO names a PENDING one for (the
   * planning-changes list still links the applied batch after a newer change was raised
   * for the same order) - unioning both ids in `boardBatchIds` then loads both, and the
   * order would otherwise appear TWICE: once from each batch. The PENDING batch wins,
   * because that is the one with something left to decide; between two pending (or two
   * applied) batches on the same order, the NEWEST `created_at` wins. The other batch's
   * row for that order is dropped entirely - not merged - so the cell renders once.
   * Newest-wins is safe here rather than a real conflict: the backend's own
   * `pending_batch_id_by_sales_order` already returns only the SINGLE newest pending batch
   * per order, so the only way an OLDER pending batch reaches this map at all is via the
   * URL deep link, never via the board's own union.
   */
  const bySoNumber = React.useMemo(() => {
    const out = new Map<string, { batch: PlanningChangeBatch; order: PlanningChangeOrder }>();
    for (const batch of loadedBatches) {
      for (const order of batch.orders ?? []) {
        const existing = out.get(order.so_number);
        if (!existing) {
          out.set(order.so_number, { batch, order });
          continue;
        }
        const existingPending = !existing.batch.applied_at;
        const candidatePending = !batch.applied_at;
        if (existingPending !== candidatePending) {
          // Exactly one is pending: it wins outright, whichever `created_at` is newer.
          if (candidatePending) out.set(order.so_number, { batch, order });
          continue;
        }
        // Both pending or both applied: the newer batch is the one still worth reading.
        if (new Date(batch.created_at).getTime() > new Date(existing.batch.created_at).getTime()) {
          out.set(order.so_number, { batch, order });
        }
      }
    }
    return out;
  }, [loadedBatches]);

  /** The one batch this order belongs to, or null - a `replan`/applied lookup never guesses. */
  const batchIdBySoNumber = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const [soNumber, entry] of bySoNumber) map.set(soNumber, entry.batch.id);
    return map;
  }, [bySoNumber]);

  /**
   * The SURVIVING rows, grouped back by the batch that survived for them - the pre-mark
   * effect below must read THIS, never the raw `loadedBatches`. An order the dedup above
   * dropped (an applied batch a pending one superseded for the SAME so_number) still sits
   * in `loadedBatches`, and seeding a draft from its rows would pre-mark a line the board
   * no longer has anything pending to decide for.
   */
  const survivingOrdersByBatchId = React.useMemo(() => {
    const map = new Map<string, PlanningChangeOrder[]>();
    for (const { batch, order } of bySoNumber.values()) {
      const list = map.get(batch.id);
      if (list) list.push(order);
      else map.set(batch.id, [order]);
    }
    return map;
  }, [bySoNumber]);

  /**
   * The board as the CHANGED lines make it: a line the book has moved is no longer covered
   * by the decision taken for it, so it arrives undecided carrying the batch's own fresh
   * proposal (`uncoverChangedLines`). Identity on every board with no batch loaded.
   *
   * Merged across EVERY loaded batch (AC-B3), one row per so_number after the dedup above -
   * `uncoverChangedLines`, `preMarkedKeys` and `annotationsByCell` all take
   * `Pick<PlanningChangeBatch, 'orders'>` and never read the batch's own id, so this
   * flattened `orders[]` reads exactly like one bigger batch to all three.
   */
  const changeBatchData: Pick<PlanningChangeBatch, 'orders'> | null = React.useMemo(
    () =>
      bySoNumber.size > 0
        ? { orders: Array.from(bySoNumber.values(), (entry) => entry.order) }
        : null,
    [bySoNumber],
  );
  /**
   * The UNCOVER step's own input, review round 2 (AC-F6): an APPLIED batch is history - the
   * line's decision already carries what Apply did to it - and uncovering it would offer a
   * Save the server refuses outright (R1). Filtered out here only: `appliedSoNumbers`, the
   * Confirm-blocked banner and the change-icon annotations still read `changeBatchData` above
   * unfiltered, because a deep link to an applied batch still has to SAY what it applied.
   */
  const openChangeBatchData: Pick<PlanningChangeBatch, 'orders'> | null = React.useMemo(() => {
    const orders = Array.from(bySoNumber.values())
      .filter((entry) => !entry.batch.applied_at)
      .map((entry) => entry.order);
    return orders.length > 0 ? { orders } : null;
  }, [bySoNumber]);
  const board = React.useMemo(
    () => ({
      ...rawBoard,
      data: rawBoard.data
        ? uncoverChangedLines(rawBoard.data, openChangeBatchData)
        : rawBoard.data,
    }),
    [rawBoard, openChangeBatchData],
  );

  /**
   * A Confirm's own refetch is on the wire, on the SAME selection/granularity/window - a
   * draft save/undo never lands here any more (D16: `useLineDraftMutation` patches the cache
   * in place and asks for no refetch at all). React Query already keeps the last successful
   * `data` on screen through a same-key refetch with no extra wiring; the only gap was that
   * `FulfilmentBoardListView` was fed this flag AS `PanelDataGrid`'s `isLoading`, which
   * skeleton-wipes a grid that already had every row it needed - the flicker itself (the
   * captain, 3 September 2026: "very choppy"). Read here instead to DIM the board rather than
   * blank it.
   *
   * Deliberately NOT extended to a granularity/day-window turn (that IS a key change, so it
   * would need `placeholderData` to keep the old rows up) - measured, not theoretical: turning
   * that on made a cell opened moments after a granularity switch close itself, because the
   * click landed on the OLD granularity's cell before the fresh read replaced it and
   * `liveCell` then matched nothing. See `usePlanningBoard`'s own note.
   */
  const boardRefreshing = board.isFetching;

  /**
   * Orders a PENDING planning-change batch currently covers (S4, fix round,
   * `PLAN-board-reject-on-confirmed-line.md`) - the SAME predicate `runConfirmAll`
   * resolves each order's own `orderBatchId` with below, read here so the "Confirm
   * (N)" counter (and the per-decision toast, which reads the identical function)
   * never promises a covered-rejected line's withdrawal a press cannot actually
   * carry out: AC-B12 refuses `rejected_line_ids` alongside `batch_id` outright, so
   * that line rides along as nothing and a counter that still named it read
   * "Confirm (1)" for a press that then confirmed nothing at all.
   *
   * N3 (nit, fix round): reads `board.data`, while `runConfirmAll`'s own loop below acts
   * on `liveBoard` - the FRESH read after an adoption round, when one ran. The two can
   * disagree for exactly the span between that refetch and this memo's own re-render off
   * the same query cache; the counter self-corrects on the next render once React catches
   * up, so this is a display lag, not a body the server sees wrong.
   */
  const pendingBatchSalesOrderIds = React.useMemo(() => {
    const out = new Set<string>();
    for (const order of board.data?.orders ?? []) {
      const batchId =
        (order.so_number ? batchIdBySoNumber.get(order.so_number) : undefined) ??
        order.pending_change_batch_id ??
        null;
      if (batchId) out.add(order.sales_order_id);
    }
    return out;
  }, [board.data, batchIdBySoNumber]);

  // Orders with no planning record yet: the press adopts them first, so only there does a
  // line with no mirror still count as postable.
  const unadoptedSalesOrderIds = React.useMemo(
    () =>
      new Set(
        (board.data?.orders ?? [])
          .filter((order) => !order.project_sales_order_id)
          .map((order) => order.sales_order_id),
      ),
    [board.data?.orders],
  );

  /**
   * Move the day window by a whole window at a time.
   *
   * The FIRST window is the server's: it opens on the earliest date still to come, falling
   * back to the earliest owed when everything is past. Once the planner has moved it, the
   * window THEY asked for is the anchor, and the step is the contract's thirty days - never
   * the columns that happened to come back, so a stretch nobody owes anything in is still a
   * page and no day is skipped or shown twice.
   *
   * Day is the only granularity with a window. Week and month need none: only periods actually
   * owed become columns, so the 50-order cap tops out around 57 week or 24 month columns, and
   * a control to page through them would be a knob for a problem nobody has.
   */
  const shiftWindow = React.useCallback(
    (direction: 1 | -1) => {
      const anchor =
        dayWindow ??
        board.data?.dateBuckets.find((bucket) => bucket.kind === 'dated' && bucket.start)?.start ??
        board.data?.as_of;
      if (!anchor) return;
      setDayWindow(shiftedDayWindow(anchor, direction));
    },
    [board.data, dayWindow],
  );

  const { adopt } = useFulfilmentPlanningMutations();
  const { save: saveLineDraft, remove: removeLineDraft } = useLineDraftMutation();

  // NO PER-ORDER CONFIRM (R11). The board used to carry one Confirm per sales order in a
  // Commit section under the matrix, each with its own busy flag and its own refusal list.
  // A planner reading a board of nine orders had nine buttons to press to say one thing, and
  // the refusals were three screens below the rows that caused them. There is ONE Confirm
  // now, in the header bar, and it posts every order in one call.

  // NO PER-ORDER LEDGER. It accumulated which order each contribution key belonged to across
  // every day window the planner had scrolled through, so the per-order "N of M lines decided"
  // counter would not fall as they moved. That counter went with the Commit section (R13), and
  // the one counter left is summed over the whole selection, unwindowed, from `contributions`.

  /**
   * Every contributing line of the WHOLE selection, unwindowed - the same population "Approve
   * all" and the List view (D2) act on. The server's own top-level `contributions`, never
   * `cells[].contributions`: a cell only exists for a bucket that made it onto screen, and at
   * day granularity that is the 30-day window (`DAY_WINDOW_COLUMNS`), not the whole selection -
   * flattening the cells silently dropped every line outside it, so "Approve all" and the
   * confirm-all dialog undercounted (13.5's own reason `standings` reads `board.orders` instead
   * of the cells).
   */
  const allContributions = React.useMemo<BoardContribution[]>(
    () => board.data?.contributions ?? [],
    [board.data],
  );

  /**
   * #1312 (AC-U2, fix round 1 should-fix 3): ONE lookup call for the WHOLE
   * selection's own line ids - `allContributions`, never the list view's own
   * search-filtered subset, so a paperclip's count does not flicker away when a
   * search term temporarily narrows the rows the list view itself renders. Lives
   * here (under this screen's own `QueryClientProvider`, from wherever mounts
   * `FulfilmentBoardPanel`) rather than inside `FulfilmentBoardListView`, which is
   * unit-tested standalone with no provider in scope.
   */
  const attachmentLineIds = React.useMemo(
    () => allContributions.map((contribution) => contribution.line_id),
    [allContributions],
  );
  const { data: attachmentsByLine } = useSoLineAttachmentLookup(attachmentLineIds);

  /**
   * Every changed line of EVERY loaded batch arrives PRE-MARKED (AC-P3-3, AC-B2, AC-B3) -
   * no `batchId` prop required any more: a batch the board named for an order pre-marks its
   * suggestion exactly as a `?batch=` deep link always has.
   *
   * Seeded into the board's own DRAFT, not into a second state: the cell then colours, counts
   * and confirms exactly as a line the planner ticked themselves, and un-ticking one is the
   * same gesture it always was. Once PER BATCH, keyed by batch id rather than one board-wide
   * flag: two orders on two different batches load at different times (`useQueries`), and a
   * batch that arrives on a later render must still seed its own suggestion once, without
   * re-seeding a sibling batch's lines the planner has already touched.
   *
   * A verdict the planner has already given is never overwritten.
   *
   * Reads `survivingOrdersByBatchId`, never the raw `loadedBatches`: a batch the S1 dedup
   * dropped for a given order (an applied batch a pending one superseded on the SAME
   * so_number) must not still seed an approved draft for that order's rows.
   *
   * Waits for EVERY id in `boardBatchIds` to have loaded before seeding anything, rather
   * than reacting to each one as it arrives: two batches for the same so_number can settle
   * on different renders (`useQueries`, one query per id), and while only one has loaded
   * it is the sole, unopposed "survivor" the S1 dedup above ever sees - so seeding from
   * that partial state can mark the STALE batch's rows a moment before the real survivor
   * is known, and the once-per-batch-id guard below has no way to take an already-seeded
   * key back once the true survivor turns out to be a different batch.
   */
  const preMarkedBatchIds = React.useRef<Set<string>>(new Set());
  /**
   * WHICH keys were pre-marked, not merely which batches did the marking (AC-B13, owner
   * finding 22 Sep: "it becomes suggested instead of change proposed").
   *
   * `decide(key, null)` used to delete the key outright, and the effect above never seeds a
   * second time (it is guarded per batch id), so undoing a save on a line the open batch
   * NAMES dropped it back to plain `Suggested` - a line the book had moved reading as though
   * the book had not. This is the set `decide` puts the pre-mark back over, and the
   * board-wide discard with it (SF-5).
   *
   * IT IS NEVER EMPTIED IN-SESSION, deliberately (N-1): a batch that is applied while this
   * board is open leaves its lines genuinely changed by the book, so an Undo on one of them
   * still belongs at `Change proposed` until the board is re-opened on a batch that no
   * longer names it. Clearing the set on apply would make the SAME Undo mean two different
   * things depending on when it was pressed.
   */
  const preMarkedKeySet = React.useRef<Set<string>>(new Set());
  React.useEffect(() => {
    if (allContributions.length === 0) return;
    if (loadedBatches.length < boardBatchIds.length) return;
    for (const [batchId, orders] of survivingOrdersByBatchId) {
      if (preMarkedBatchIds.current.has(batchId)) continue;
      const keys = preMarkedKeys({ orders }, allContributions);
      if (keys.length === 0) continue;
      preMarkedBatchIds.current.add(batchId);
      setDraft((current) => {
        const next = { ...current };
        // `preMarked: true` (PLAN-board-change-proposed-pill, owner ruling 18 Sep 2026): what
        // tells the pill and the Verdict column this entry is the board's OWN pre-mark, not a
        // decision anybody has actually saved - `decide()` always writes a fresh object over
        // this key, so the flag drops itself the moment a person acts on the line.
        for (const key of keys) {
          preMarkedKeySet.current.add(key);
          if (!next[key]) next[key] = { verdict: 'approved', preMarked: true };
        }
        return next;
      });
    }
  }, [survivingOrdersByBatchId, allContributions, loadedBatches, boardBatchIds]);

  /**
   * Keys whose DELETE is on the wire right now (C1, code review round 3 batch 2): added
   * before `removeLineDraft` fires, cleared once it settles either way. A ref, not state -
   * nothing here should ever cause its own render.
   *
   * The seeding effect below reads this to skip a key mid-delete: a save on a DIFFERENT
   * line invalidates the board the same way this line's own Undo does, and the refetch that
   * lands can still carry THIS key's server draft if the DELETE has not committed yet - the
   * `!next[key]` guard alone only protects a key that is missing from `draft` because a
   * decision was never taken here, not one that is missing because a click just removed it
   * and is still waiting on the network to agree.
   */
  const pendingDeletes = React.useRef<Set<string>>(new Set());

  /**
   * Keys whose SAVE is on the wire right now (R3, AC-F5): added before `saveLineDraft`
   * fires in `decide` below, cleared once it settles either way. The covered-line drop a
   * few lines down has to skip a key here - a board read that races an in-flight Save can
   * land BEFORE the write it is racing, and dropping the local entry on that read would
   * flicker the pill back to Confirmed for a frame ahead of the very save that is about to
   * make it Saved again.
   */
  const pendingSaves = React.useRef<Set<string>>(new Set());

  /**
   * A line SAVED elsewhere - another device, another planner, or this one before a reload -
   * arrives ON THE BOARD ITSELF (S4, R-F): `contribution.draft` is the server's own row, and
   * this seeds it into the SAME `draft` map a click here would write, so a Saved pill, the
   * header counter and Confirm all read the one state whichever way the line got there.
   *
   * Seeded on EVERY board read, not once like `preMarked` above: AC-4.5 ("a second planner
   * sees the first planner's saved lines") needs a later fetch to bring in a save nobody
   * here made. `!next[key]` is what keeps this from clobbering THIS session's own edit - the
   * same guard `preMarked` uses, and for the same reason: a verdict already given here is
   * never overwritten by what the server happened to say a moment before. `pendingDeletes`
   * is the other half of that guard: a key whose Undo is still in flight is ALSO missing
   * from `draft`, and without the skip a same-tick refetch racing that DELETE puts the pill
   * straight back to Saved a moment after the click cleared it.
   *
   * R3 (SO314595, 17 Sep 2026): the SAME read also drops a local entry whose contribution is
   * now `covered` and carries no server `draft`. A covered line with no draft means the
   * server has already PROMOTED it (Confirm deletes the draft it promotes) or had it
   * REMOVED some other way - either way this tab's own local copy is stale, and it is what
   * printed Saved/Rejected over eleven lines a lost Confirm response had actually already
   * confirmed. `pendingDeletes` and `pendingSaves` are both checked, because a key mid-write
   * either way is not yet the state this board read is describing.
   */
  React.useEffect(() => {
    const serverDrafts = allContributions.filter((contribution) => contribution.draft);
    const coveredWithNoDraft = allContributions.filter(
      (contribution) => contribution.covered && !contribution.draft,
    );
    if (serverDrafts.length === 0 && coveredWithNoDraft.length === 0) return;
    setDraft((current) => {
      let changed = false;
      const next = { ...current };
      for (const contribution of serverDrafts) {
        if (pendingDeletes.current.has(contribution.key)) continue;
        if (!next[contribution.key] && contribution.draft) {
          next[contribution.key] = contribution.draft.decision;
          changed = true;
        }
      }
      for (const contribution of coveredWithNoDraft) {
        if (pendingDeletes.current.has(contribution.key)) continue;
        if (pendingSaves.current.has(contribution.key)) continue;
        if (next[contribution.key]) {
          delete next[contribution.key];
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [allContributions]);

  /**
   * The one place a draft is actually deleted (C1): `decide(key, null)` and "Undo all" both
   * go through this, so `pendingDeletes` is tracked once rather than twice.
   */
  const removeDraftKey = React.useCallback(
    async (key: string): Promise<void> => {
      pendingDeletes.current.add(key);
      try {
        await removeLineDraft(key);
      } finally {
        pendingDeletes.current.delete(key);
      }
    },
    [removeLineDraft],
  );

  /**
   * Save decision / Undo (S4, R-F): local first, then the server write, so the pill answers
   * the click before any network round-trip - and reverted, with the mutation's own error
   * toast, if the write fails. A rejection is a decision too (owner, 22 Sep 2026): it gets
   * the same per-line toast a Save does, worded for what it is rather than skipped outright.
   *
   * THE UPDATER FORM, both ways (B1, code review round 3): a plain `setDraft(next)` computed
   * `next` off the `draft` CLOSURE, so several `decide()` calls fired together (Undo all used
   * to loop this) each dropped only THEIR OWN key off the same stale snapshot - the last call
   * to actually run won, and every earlier call's drop was overwritten straight back in.
   * `setDraft((current) => ...)` instead applies each call against whatever the PREVIOUS one
   * left, so N calls in flight together compose rather than race. The revert on a failed write
   * is the same shape: only THIS key goes back to what it held before this call touched it,
   * never the whole map (a DELETE failing on line 2 must not resurrect line 1's own successful
   * discard the same press just made).
   *
   * `appliedNext` is captured out of the updater for the toast below: React calls a `useState`
   * updater synchronously on dispatch (the "eager state" check), and the toast only runs after
   * `await`ing the network write, well past that point - so it is always the map this decision
   * actually produced, never a value computed before a concurrent call's own delta landed.
   *
   * `options.quiet` (D15) skips this per-line toast without touching anything else about the
   * write: `decideMany` and `undoMany` below post every key through this SAME function, one at
   * a time, so a bulk press still gets the identical local-first / revert-on-failure behaviour
   * and reports its own outcome with a single toast of its own instead of N of these.
   */
  const decide = React.useCallback(
    async (
      key: string,
      decision: BoardDecision | null,
      options?: {
        quiet?: boolean;
        // Review round 1, Should fix 1: `decideBatch` folds every failed row into its own
        // lenient toast (R10) - a second, per-row toast off THIS mutation's own `onError`
        // would be the "too many errors" the owner asked Decide to stop doing. `onFailure`
        // is how the caller still gets the server's own sentence for its skip, without it.
        // Nit (review round 2): named `silent`, not `silentError` - the same name
        // `saveLineDraft`'s own option carries, since this is nothing but a pass-through to it.
        silent?: boolean;
        onFailure?: (message: string) => void;
      },
    ): Promise<boolean> => {
      let hadPrevious = false;
      let previousForKey: BoardDecision | undefined;
      let appliedNext: BoardDraft = {};
      // Read once, for both the write below and the toast at the end - the same
      // contribution either way, and `allContributions` does not move mid-call.
      const contribution = allContributions.find((entry) => entry.key === key);
      setDraft((current) => {
        hadPrevious = Object.prototype.hasOwnProperty.call(current, key);
        previousForKey = current[key];
        const next = { ...current };
        if (decision) next[key] = decision;
        // AC-B13: a line the open change batch NAMED goes back to the board's own pre-mark
        // rather than to nothing, so its pill reads `Change proposed` again and it is still
        // counted toward Confirm. The server draft is deleted either way (below); what
        // differs is only the shape the local entry is left in.
        else if (preMarkedKeySet.current.has(key))
          next[key] = { verdict: 'approved', preMarked: true };
        else delete next[key];
        appliedNext = next;
        return next;
      });
      try {
        if (decision) {
          // S1 (code review round 3) still holds: staleness is judged server-side on the
          // LINE's own facts (outstanding qty, required date), never on a proposal. D12
          // (#573) adds the contribution's own `sources` as `proposed` for a DIFFERENT
          // reason: the Sales Order page's Suggested column reads it back on this line
          // until Confirm freezes a revision.
          pendingSaves.current.add(key);
          try {
            await saveLineDraft(key, decision, contribution?.sources, {
              silent: options?.silent,
            });
          } finally {
            pendingSaves.current.delete(key);
          }
        } else {
          await removeDraftKey(key);
        }
      } catch (error) {
        // The mutation's own `onError` already toasted the message unless `silent`
        // asked it not to (Should fix 1) - either way, `onFailure` is the caller's own way
        // to read it, and this key still goes back the way the click found it.
        options?.onFailure?.(error instanceof Error ? error.message : 'could not be saved');
        setDraft((current) => {
          const reverted = { ...current };
          if (hadPrevious && previousForKey) reverted[key] = previousForKey;
          else delete reverted[key];
          return reverted;
        });
        return false;
      }
      if (!options?.quiet && decision) {
        // S5: the toast's own count reads the same pre-mark-free view Confirm itself does -
        // `appliedNext` still carries every OTHER line's still-unsaved pre-mark verbatim.
        const { toConfirm, rejected } = confirmSummaryFor(
          allContributions,
          draftWithoutPreMarks(appliedNext),
          pendingBatchSalesOrderIds,
          { unadoptedSalesOrderIds },
        );
        toast.success(
          decision.verdict === 'rejected'
            ? `${contribution ? soLineLabel(contribution) : 'Line'} rejected · ${toConfirm} to confirm · ${rejected} rejected`
            : `${contribution ? soLineLabel(contribution) : 'Line'} saved · ${toConfirm} to confirm`,
        );
      }
      return true;
    },
    [allContributions, saveLineDraft, removeDraftKey, pendingBatchSalesOrderIds, unadoptedSalesOrderIds],
  );

  /**
   * Every still-eligible line saved with the engine's own composition, in one press (D15: the
   * board-wide "Save all suggested" button, the list's and dialog's own bulk buttons, and a
   * grid cell's own save icon all post through this rather than looping `onDecide` themselves,
   * which is what left D14's bulk verbs toasting once PER LINE - "Line 4 saved", "Line 7
   * saved", ... - on a press that was meant to be one action.
   *
   * FIVE AT A TIME: `Promise.all` per chunk, chunks run one after another, so a board-wide
   * press of forty lines does not open forty simultaneous writes against the same draft
   * endpoint.
   *
   * The toast's own "M to confirm" is read off `appliedNext` - THIS call's own delta over the
   * `draft` this component held when the press started, not a fresh read of React state after
   * the fact: every key here was eligible precisely because it carried no draft yet, so the
   * composition `decide()` is about to save for each one is exactly what `appliedNext` adds.
   */
  const decideMany = React.useCallback(
    async (keys: string[]): Promise<{ saved: number; failed: number }> => {
      let saved = 0;
      let failed = 0;
      const appliedNext: BoardDraft = { ...draft };
      const CHUNK_SIZE = 5;
      for (let i = 0; i < keys.length; i += CHUNK_SIZE) {
        const chunk = keys.slice(i, i + CHUNK_SIZE);
        await Promise.all(
          chunk.map(async (key) => {
            const contribution = allContributions.find((entry) => entry.key === key);
            if (!contribution) {
              failed += 1;
              return;
            }
            const decision = suggestedDecisionFor(contribution);
            const ok = await decide(key, decision, { quiet: true });
            if (ok) {
              saved += 1;
              appliedNext[key] = decision;
            } else {
              failed += 1;
            }
          }),
        );
      }
      if (saved > 0) {
        // S5: same pre-mark-free reading as every other count on this board - a line outside
        // THIS press's own population (filtered off screen) can still carry a stale pre-mark
        // in `appliedNext`.
        const { toConfirm } = confirmSummaryFor(
          allContributions,
          draftWithoutPreMarks(appliedNext),
          pendingBatchSalesOrderIds,
          { unadoptedSalesOrderIds },
        );
        toast.success(
          `${saved} line${saved === 1 ? '' : 's'} saved · ${toConfirm} to confirm`,
        );
      }
      return { saved, failed };
    },
    [allContributions, decide, draft, pendingBatchSalesOrderIds, unadoptedSalesOrderIds],
  );

  /**
   * S3 (D1): the Decide strip's own save - one PUT per row through the identical `decide()`
   * chunked-of-5 loop `decideMany` already runs, but with a DECISION PER KEY the caller has
   * already composed (`decideComposition`), rather than always the engine's own suggestion.
   * No toast here (AC-16/AC-17): the caller (`BoardDecideControl`) already knows which rows
   * it skipped WITHOUT a PUT (a pile a pick could not cover in full) and folds them into the
   * SAME lenient toast beside whichever of these fail on the wire - two toasts for one press
   * would be the "too many errors" the owner asked Decide to stop doing (R10).
   */
  const decideBatch = React.useCallback(
    async (
      entries: { key: string; decision: BoardDecision }[],
    ): Promise<{ savedKeys: string[]; failed: { key: string; why: string }[] }> => {
      const savedKeys: string[] = [];
      const failed: { key: string; why: string }[] = [];
      const CHUNK_SIZE = 5;
      for (let i = 0; i < entries.length; i += CHUNK_SIZE) {
        const chunk = entries.slice(i, i + CHUNK_SIZE);
        await Promise.all(
          chunk.map(async ({ key, decision }) => {
            let why = 'could not be saved';
            const ok = await decide(key, decision, {
              quiet: true,
              silent: true,
              onFailure: (message) => {
                why = message;
              },
            });
            if (ok) savedKeys.push(key);
            else failed.push({ key, why });
          }),
        );
      }
      return { savedKeys, failed };
    },
    [decide],
  );

  /**
   * The cell's own Undo and the list's per-row Undo already act on ONE line without a toast
   * (S4's per-line Undo carries none, on the reading that one line's undo is reversible with
   * another quick save). A GRID CELL's own undo icon can carry several lines at once, so it
   * gets the one toast its own press deserves - the same "one action, one toast" rule
   * `decideMany` follows above, in the other direction.
   */
  const undoMany = React.useCallback(
    async (keys: string[]): Promise<{ saved: number; failed: number }> => {
      let saved = 0;
      let failed = 0;
      const CHUNK_SIZE = 5;
      for (let i = 0; i < keys.length; i += CHUNK_SIZE) {
        const chunk = keys.slice(i, i + CHUNK_SIZE);
        await Promise.all(
          chunk.map(async (key) => {
            const ok = await decide(key, null, { quiet: true });
            if (ok) saved += 1;
            else failed += 1;
          }),
        );
      }
      if (saved > 0) {
        // "UNDONE", not "back to suggested" (SF-5, reviewer, fix round 2): a line the open
        // change batch named goes back to `Change proposed`, not to `Suggested`, so naming
        // one of the two outcomes described the other half of the press wrongly.
        toast.success(`${saved} line${saved === 1 ? '' : 's'} undone`);
      }
      return { saved, failed };
    },
    [decide],
  );

  /**
   * The same lines, in AutoCount's own order (S4, owner ruling 21 Sep 2026, fix round
   * #1076) - sales order, then the line number AutoCount itself sent.
   *
   * SUPERSEDED `orderByProductRows` (retired, fix round #1076 delta review), which used to
   * order THIS list to match the grid's product axis - it had no production caller left
   * once the list moved here. The grid's own axis was never built from it either - it is
   * `boardAxis` over `board.data.productRows` below, untouched by this change - so a
   * planner comparing this list against the source document now reads it top to bottom the
   * way AutoCount does, not product by product, while the grid keeps its own
   * product-by-product axis for the cross-order view. The two are no longer
   * position-aligned - the toggle now genuinely shows two different readings of one
   * payload, which is the owner's call, not a defect. Applied here rather than inside the
   * list so `allContributions` - which Approve-all and the confirm dialog also read - keeps
   * the order the server served in.
   */
  const listContributions = React.useMemo<BoardContribution[]>(
    () => orderListRows(allContributions),
    [allContributions],
  );

  /**
   * `draft` as Confirm reads it (S5, owner ruling 25 Sep 2026, issue #1245): every bare
   * pre-mark dropped, so a `Change proposed` line nobody has saved counts, and posts, exactly
   * like a line nobody has touched at all - `draftWithoutPreMarks`'s own doc has the reasoning.
   * The pre-mark seeding effects above still write `preMarked: true` straight into `draft`
   * itself; this is a read-side view of it, never a second copy of the state.
   */
  const draftWithoutPreMark = React.useMemo(() => draftWithoutPreMarks(draft), [draft]);

  /**
   * What one press of Confirm would do: "N to confirm · M rejected" (D1/D3).
   *
   * Counted over exactly the population `confirmLinesFor` posts, so the sentence beside the
   * button and what the button does can never disagree. `confirmSummaryFor` is the shared
   * implementation (`_shared/lib/fulfilmentBoard.ts`) - `decide()`'s own S4 save toast needs
   * the SAME count read off the draft it just wrote, before this `useMemo` has re-run with
   * it, so the reduction lives in one place rather than being kept in step by hand in two.
   *
   * Reads `draftWithoutPreMark`, not `draft` (S5): a `Change proposed` line nobody has saved
   * is not counted, and the Confirm button disables at 0 exactly as it already does for a
   * board with nothing decided at all.
   */
  const confirmSummary = React.useMemo(
    () =>
      confirmSummaryFor(allContributions, draftWithoutPreMark, pendingBatchSalesOrderIds, {
        unadoptedSalesOrderIds,
      }),
    [allContributions, draftWithoutPreMark, pendingBatchSalesOrderIds, unadoptedSalesOrderIds],
  );

  /**
   * AC-DT-1/AC-DT-4 (`PLAN-oi-decision-trail-ui.md`): "Revision N, confirmed by <name>,
   * N lines" beside the confirm summary, or "No decision yet" - one segment per order
   * when several are planned together. Read off `board.data.orders` (never recomputed):
   * `decision` is the server's own read of the active `so_supply_decisions` row.
   */
  const decisionHeader = React.useMemo(
    () => decisionHeaderText(board.data?.orders ?? []),
    [board.data?.orders],
  );

  /**
   * Decided lines this confirmation cannot carry, across the WHOLE board, each with why.
   *
   * It used to sit inside the per-order commit card that R13 removed. Named rather than
   * dropped in silence: the fix is somewhere else (another screen, or the row's own editor),
   * so a planner told nothing would have no way to find out they had not committed it.
   */
  const unpostable = React.useMemo<UnpostableLine[]>(() => {
    if (!board.data) return [];
    const contributions = board.data.contributions;
    // S5: a bare pre-mark is not a decision to flag a REASON for - it is not being posted at
    // all, the same as a line nobody has touched (`draftWithoutPreMark`'s own doc). Left on
    // raw `draft` this banner would name a `buy_reason_missing`-style refusal for a `Change
    // proposed` line nobody had looked at yet.
    return board.data.orders.flatMap((order) =>
      unpostableDecidedFor(
        contributions,
        order.sales_order_id,
        draftWithoutPreMark,
        Boolean(order.project_sales_order_id),
      ),
    );
  }, [board.data, draftWithoutPreMark]);

  /**
   * The sales orders whose OWN batch rows have all been applied already (AC-B6).
   *
   * One upload moves many orders and the batch itself only reads applied once the last of
   * them is written, so an order that has already had its change applied would otherwise be
   * posted a second time by the next press - writing another revision of a change that is
   * already in the plan. It is left out of the body and said so in the result, rather than
   * blocking the whole board: the other orders on it still have a change nobody has decided.
   * Reads `changeBatchData`, the flattened `orders[]` of EVERY loaded batch, so a two-batch
   * board skips exactly the order whose OWN batch is applied and no other.
   */
  const appliedSoNumbers = React.useMemo(() => {
    const out = new Set<string>();
    for (const order of changeBatchData?.orders ?? []) {
      if (order.rows.length > 0 && order.rows.every((row) => row.applied_state === 'applied')) {
        out.add(order.so_number);
      }
    }
    return out;
  }, [changeBatchData]);
  /**
   * Why Confirm is off, board-wide, when it is (AC-P3-4).
   *
   * Only makes sense with ONE batch on the board: "this planning change was applied" names A
   * change, and a board with two orders on two different batches has no single change to
   * name (AC-B6 - the OTHER order still has something to confirm, so the button must stay
   * live). The per-order skip in `appliedSoNumbers` above is what actually keeps a
   * already-applied order out of the body on a multi-batch board; this banner is the single-
   * batch case's up-front statement of the same fact, unchanged from before this slice.
   */
  const singleBatch = loadedBatches.length === 1 ? loadedBatches[0] : null;
  const confirmBlockedReason = React.useMemo<string | null>(() => {
    if (!singleBatch?.applied_at) return null;
    return `This planning change was applied ${formatDateTimeInMalaysia(singleBatch.applied_at)}${
      singleBatch.applied_by_name ? ` by ${singleBatch.applied_by_name}` : ''
    }.`;
  }, [singleBatch]);

  /**
   * Undo last confirm (`PLAN-board-undo-last-confirm.md`, S2/#979).
   *
   * One `useDeferredAction` for the whole board: `undoTarget` names whichever order the
   * planner just picked from the gear, and the effect below fires `start()` once the hook
   * has re-rendered against that order's id - `start()` reads `entityId`/`payload` from
   * THIS render's closure, so it must run after the state that produced them has landed,
   * never in the same tick as the click that set it.
   */
  const [undoTarget, setUndoTarget] = React.useState<{
    orderId: string;
    soNumber: string;
    decisionId: string;
    /** Carried into the payload (S5, AC-R2-34): the server 409s a `mode` that does not match
     * the decision's own journal state, so a stale gear entry can never replay the wrong
     * path. */
    mode: 'journal' | 'reconstructed';
  } | null>(null);
  const undoAction = useDeferredAction({
    actionKey: 'project_sales_order.undo_confirm',
    entityType: 'project_sales_order',
    entityId: undoTarget?.orderId ?? null,
    verb: 'Undoing',
    subject: undoTarget?.soNumber ?? '',
    surface: 'inline',
    successMessage: `${undoTarget?.soNumber ?? 'Order'} confirm undone`,
    payload: undoTarget
      ? { decision_id: undoTarget.decisionId, mode: undoTarget.mode }
      : undefined,
    invalidateKeys: [[PLANNING_BOARD_KEY]],
  });
  React.useEffect(() => {
    if (!undoTarget) return;
    if (undoAction.pending || undoAction.isPending) return;
    undoAction.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [undoTarget]);

  const undoableOrders = React.useMemo(
    () => (board.data?.orders ?? []).filter((order) => order.undo),
    [board.data],
  );

  const confirmMany = useConfirmManyMutation();
  const previewConfirmMany = usePreviewConfirmManyMutation();
  // The lines each order's press posted, by planning record, so the results block can set
  // them beside what the server says it wrote.
  const [postedLines, setPostedLines] = React.useState<
    Record<string, { project_line_id: string; label: string }[]>
  >({});


  /**
   * Undo all throws away every decision taken since the board was opened, and there is no way
   * back to them: it is destructive in the only sense a client draft can be, so it is
   * confirmed with the count first, like every other destructive verb in this product.
   */
  const [undoAllOpen, setUndoAllOpen] = React.useState(false);
  const [confirmingAll, setConfirmingAll] = React.useState(false);
  const [batchResults, setBatchResults] = React.useState<BoardBatchResult[] | null>(null);

  /**
   * The body of ONE press, built once for both Preview and Confirm so the two can never drift:
   * adopts any order not yet adopted (the body needs the mirror ids), re-reads the board once
   * afterwards, and returns the per-order `orders` plus the orders left out with a reason.
   * `excludeKeys` are the lines the planner unticked in the preview; Preview itself passes none.
   */
  const buildConfirmOrders = React.useCallback(
    async (excludeKeys: ReadonlySet<string>) => {
      if (!board.data) return null;
      let liveBoard = board.data;
      let contributions = allContributions;

      const wantedOrders = new Set(
        contributions
          .filter((contribution) => {
            if (contribution.unplannable) return false;
            if (excludeKeys.has(contribution.key)) return false;
            const decision = draftWithoutPreMark[contribution.key];
            // A COVERED reject is a WITHDRAWAL this press carries out (owner ruling 23 Sep
            // 2026, `PLAN-board-reject-on-confirmed-line.md`: "we should confirm the
            // rejection") - its order belongs in the batch on that account alone, same as an
            // amendment does. An UNCOVERED reject has nothing active to withdraw and is left
            // out, exactly as before.
            if (decision?.verdict === 'rejected') return Boolean(contribution.covered);
            // 8 Sep 2026 ruling (reverses R11): an untouched, uncovered line is undecided,
            // not agreed - it does not put its order in the batch on its account alone.
            if (!contribution.covered && !decision) return false;
            // Covered and untouched: the server carries it, so this press has nothing to
            // post for it and its order is not put in the batch on its account alone.
            // An Approved draft on a covered line IS posted (`lineFor`), so it counts too.
            if (
              contribution.covered &&
              decision?.verdict !== 'amended' &&
              decision?.verdict !== 'approved'
            ) {
              return false;
            }
            return true;
          })
          .map((contribution) => contribution.sales_order_id),
      );
      if (wantedOrders.size === 0) return null;

      let adoptedAny = false;
      // WHAT ADOPT ITSELF ANSWERED WITH, kept rather than discarded. The id is in the
      // response body (that is why the mutation resolves with one), and the refetch below is
      // a second, slower read of the same fact that can still come back `null`: the board
      // derives an order's planning record from its MIRROR LINES, so an order whose core
      // lines were re-ingested under new ids reports "not adopted" however many times it has
      // been adopted (SO419851, 13 Sep walk). Reading the press's own answer is what stops a
      // Confirm (1) posting nothing at all.
      const adoptedPsoIds = new Map<string, string>();
      for (const order of liveBoard.orders) {
        if (!wantedOrders.has(order.sales_order_id) || order.project_sales_order_id) continue;
        try {
          const adopted = await adopt.mutateAsync(order.sales_order_id);
          adoptedAny = true;
          if (adopted?.project_sales_order_id) {
            adoptedPsoIds.set(order.sales_order_id, adopted.project_sales_order_id);
          }
        } catch {
          // Named in the results block below rather than swallowed: with no pso_id there is
          // nothing to post for this order, and a press that ends having said nothing reads
          // as a press that did nothing.
        }
      }
      if (adoptedAny) {
        const fresh = await board.refetch();
        if (fresh.data) {
          liveBoard = fresh.data;
          contributions = liveBoard.contributions;
        }
      }

      const psoIdBySalesOrder = new Map(
        liveBoard.orders
          .filter((order) => order.project_sales_order_id)
          .map((order) => [order.sales_order_id, order.project_sales_order_id as string]),
      );
      // The adopt answer WINS over the refetched board: both name the same record when the
      // board is in step, and only one of them is the press's own.
      for (const [salesOrderId, psoId] of adoptedPsoIds) {
        psoIdBySalesOrder.set(salesOrderId, psoId);
      }

      const orders: {
        pso_id: string;
        lines: ReturnType<typeof confirmLinesFor>;
        batch_id: string | null;
        rejected_line_ids: string[];
      }[] = [];
      // An order whose planning change is already applied is NOT sent again (AC-P3-4). It is
      // reported instead, in the same place a server refusal is reported, so a press that
      // deliberately skipped it does not read as a press that did nothing.
      const skipped: BoardBatchResult[] = [];
      for (const salesOrderId of wantedOrders) {
        const psoId = psoIdBySalesOrder.get(salesOrderId);
        const standing = liveBoard.orders.find(
          (order) => order.sales_order_id === salesOrderId,
        );
        const soNumber = standing?.so_number;
        // NO PLANNING RECORD, AND ADOPTING IT DID NOT PRODUCE ONE (it was refused, or it
        // answered without an id). There is nothing to post against, so the order is left
        // out - but it is left out OUT LOUD, beside every other order's outcome, because the
        // silent `continue` here ended the whole press with an empty screen.
        if (!psoId) {
          skipped.push({
            pso_id: '',
            so_number: soNumber,
            ok: false,
            error:
              'is not being planned yet, so there is nothing to confirm it against. Press Start planning on it, then confirm again.',
          } as BoardBatchResult);
          continue;
        }
        if (soNumber && appliedSoNumbers.has(soNumber)) {
          skipped.push({
            pso_id: psoId,
            ok: false,
            error: 'This planning change was already applied to this sales order.',
          } as ConfirmManyOrderResult);
          continue;
        }
        const lines = confirmLinesFor(contributions, salesOrderId, draftWithoutPreMark, {
          excludeKeys: excludeKeys,
        });
        // AC-B3/AC-B5: THIS order's own batch, not the board-wide `batchId` - two orders on
        // two different pending batches each answer their own. The batches the screen LOADED
        // first (it was opened on one), and the BOARD'S own statement of the newest pending
        // batch for this order second (AC-B1). Same fact, two sources: a board reached
        // without `?batch=` loads no batch rows at all, and sending `null` there confirmed
        // the lines while leaving the change Pending.
        const orderBatchId =
          (soNumber ? batchIdBySoNumber.get(soNumber) : undefined) ??
          standing?.pending_change_batch_id ??
          null;
        // A pending planning change has no shape for a withdrawal alongside it (server
        // refuses the combination, 422 - `PLAN-board-reject-on-confirmed-line.md`, "Not in
        // scope") - an order on one never carries `rejected_line_ids` from here.
        const rejectedLineIds = orderBatchId
          ? []
          : rejectedCoveredLineIdsFor(contributions, salesOrderId, draftWithoutPreMark, {
              excludeKeys: excludeKeys,
            });
        // S4 (fix round, review): the covered-rejected lines THIS order's own batch just
        // zeroed out of `rejectedLineIds` above, so the planner is told what did not ride
        // along rather than the counter simply promising it and the press posting nothing
        // for it (`plannedLineCount`'s own batch exclusion is the OTHER half of this fix -
        // see `pendingBatchSalesOrderIds`). Computed straight off the contributions, never
        // off `rejectedCoveredLineIdsFor`'s own (deliberately empty, on a batched order)
        // result.
        const heldBackByBatch = orderBatchId
          ? contributions.filter(
              (contribution) =>
                contribution.sales_order_id === salesOrderId &&
                contribution.covered &&
                !excludeKeys.has(contribution.key) &&
                draftWithoutPreMark[contribution.key]?.verdict === 'rejected',
            )
          : [];
        // S4: said OUT LOUD, named per line THROUGH `failing_lines` (the same shape a
        // server refusal already renders), rather than the counter simply promising a
        // withdrawal this press cannot carry out and the planner finding out only when
        // nothing changed. Reported even when the order ALSO has other lines going
        // through below (a second `batchResults` entry beside its own `ok: true` one,
        // told apart by `so_number` - a server result never carries one) - this order's
        // withdrawal not riding along is worth saying regardless of what else it did.
        //
        // N2 (fix round): held back is NOT a refusal (`heldBack: true`, above), so it
        // carries its own `error` sentence rather than falling through to the generic
        // "refused" the results panel prints for an entry with none - worded by whether
        // this SAME press is also posting something else for the order (`lines` or
        // `rejectedLineIds`, both already computed above).
        if (heldBackByBatch.length > 0) {
          const postingSomethingElse = lines.length > 0 || rejectedLineIds.length > 0;
          skipped.push({
            pso_id: psoId,
            so_number: soNumber,
            ok: false,
            heldBack: true,
            error: postingSomethingElse
              ? 'was confirmed, but its staged rejection could not ride along with the pending change; it commits after the change is applied.'
              : 'has a staged rejection that commits after the pending change is applied.',
            failing_lines: heldBackByBatch.map((contribution) => ({
              line_no: contribution.line_no,
              reason: 'rejection is staged; it commits after the pending change is applied.',
            })),
          } as BoardBatchResult);
        }
        // DECIDED, AND NOT ONE LINE OF IT COULD BE BUILT, AND NOTHING TO WITHDRAW EITHER.
        // Every line was left out for a reason `unpostableDecidedFor` already knows (no
        // mirror on the planning record, a Reserve at a warehouse the board cannot address),
        // so the order sends nothing - and said nothing,
        // because a press whose `orders` came out empty with an empty `skipped` never set
        // `batchResults` at all. It is reported beside every other order's outcome instead,
        // in the wording the notice above the block already uses for the lines themselves.
        if (lines.length === 0 && rejectedLineIds.length === 0) {
          // The held-back note above already said why, when that is the whole reason -
          // the generic "had no line..." sentence would only repeat it more vaguely.
          if (heldBackByBatch.length > 0) continue;
          const blocked = unpostableDecidedFor(contributions, salesOrderId, draftWithoutPreMark);
          const everyLineOffTheRecord =
            blocked.length > 0 && blocked.every((entry) => entry.reason === 'no_mirror');
          skipped.push({
            pso_id: psoId,
            so_number: soNumber,
            ok: false,
            error: everyLineOffTheRecord
              ? 'is not on the planning record yet, so nothing was posted for it. Re-sync the sales order, then confirm again.'
              : 'had no line this confirmation could post, so nothing was posted for it. The notices above name each line and why.',
          } as BoardBatchResult);
          continue;
        }
        orders.push({ pso_id: psoId, lines, batch_id: orderBatchId, rejected_line_ids: rejectedLineIds });
      }
      // Per-order `batch_id` above answers AC-P3-4/AC-B5 on its own. The body-level
      // `batch_id` is kept ONLY when EVERY order in THIS press carries that SAME id
      // (backward compatible with a server that has not deployed the per-order field
      // yet) - reviewer finding B1 (39a5d8b07): `singleBatch` (how many batches the BOARD
      // loaded) is the wrong question here, because a board can load exactly one batch
      // while still sending an order that batch names NOTHING for (that order's own
      // `orderBatchId` is `null`), and a body-level id there would contradict the very
      // order carrying `null` right beside it. Checked on THIS press's `orders`, not on
      // `loadedBatches`.
      const firstOrderBatchId = orders[0]?.batch_id ?? null;
      const bodyBatchId =
        firstOrderBatchId && orders.every((order) => order.batch_id === firstOrderBatchId)
          ? firstOrderBatchId
          : null;
      return {
        orders,
        skipped,
        contributions,
        body: bodyBatchId ? { orders, batch_id: bodyBatchId } : { orders },
      };
    },
    [
      board,
      allContributions,
      draftWithoutPreMark,
      adopt,
      appliedSoNumbers,
      batchIdBySoNumber,
    ],
  );

  /**
   * CONFIRM: one call, grouped per order, each order writing in its OWN transaction
   * server-side (`confirm_many`) - so one order's refusal never takes the others down. Any
   * order that has not been adopted yet is adopted first; the board is re-read once
   * afterwards so the fresh mirror lines can be named in the payload (adoption fills
   * `project_line_id`, which is null until then).
   *
   * The population is `confirmLinesFor`'s own: CONFIRM POSTS SAVED LINES ONLY (8 Sep 2026
   * ruling, reverses R11). An uncovered line nobody saved a decision for is left undecided,
   * not confirmed as the engine's suggestion; "Save all suggested" is the bulk way to agree
   * with it before this press.
   *
   * S5 (owner ruling 25 Sep 2026, issue #1245): reads `draftWithoutPreMark`, never `draft`,
   * throughout - a `Change proposed` line nobody has saved is the SAME "nothing to post" case
   * as a line nobody has touched, so its order never enters the batch on its account alone and
   * its batch row stays pending server-side.
   */
  const runConfirmAll = React.useCallback(async () => {
    if (!board.data) return;
    setConfirmingAll(true);
    setBatchResults(null);
    // AC-6: the count as the banner above stated it the moment Confirm was pressed - the
    // owner's own words were "confirming silently is dangerous", so a press that leaves lines
    // out says so in the SAME toast that says what it did commit, not only in a banner that
    // stays on screen after the fact.
    const leftOutAtConfirm = unpostable.length;
    try {
      const built = await buildConfirmOrders(new Set());
      if (!built) return;
      const { skipped, contributions } = built;
      if (built.orders.length === 0) {
        if (skipped.length > 0) setBatchResults(skipped);
        return;
      }
      // DRY RUN FIRST (FULFIL-CONFIRM-SCOPE): the server runs this very body and rolls it back,
      // answering per order which lines it would confirm or withdraw. The press then posts the
      // same body narrowed to exactly those lines, so hold-back and the recheck still protect
      // and "Posted P" is what the server said it would write. No step for the planner.
      const dry = await previewConfirmMany.mutateAsync(built.body);
      const answer = new Map(dry.results.map((entry) => [entry.pso_id, entry]));
      const refusals: BoardBatchResult[] = [];
      const orders = built.orders
        .filter((order) => {
          const dryRun = answer.get(order.pso_id);
          if (dryRun && !dryRun.ok) {
            refusals.push({
              ...dryRun,
              preview: null,
              so_number: board.data?.orders.find(
                (standing) => standing.project_sales_order_id === order.pso_id,
              )?.so_number,
            } as BoardBatchResult);
            return false;
          }
          const ids = [...(dryRun?.lines_confirmed ?? []), ...(dryRun?.lines_withdrawn ?? [])];
          // An order whose named lines are all already fulfilled confirms nothing new, but the REAL
          // press clears their stale decisions (the dry run rolled that back), so it is still posted.
          if (ids.length > 0 || (dryRun?.lines_fulfilled_skipped ?? 0) > 0 || Boolean(order.batch_id)) {
            return true;
          }
          refusals.push({
            pso_id: order.pso_id,
            ok: false,
            heldBack: true,
            error: 'has nothing to confirm',
            so_number: board.data?.orders.find(
              (standing) => standing.project_sales_order_id === order.pso_id,
            )?.so_number,
          } as BoardBatchResult);
          return false;
        })
        .map((order) => {
          const dryRun = answer.get(order.pso_id);
          const only = [...(dryRun?.lines_confirmed ?? []), ...(dryRun?.lines_withdrawn ?? [])].map(
            (entry) => entry.project_line_id,
          );
          if (only.length === 0 || order.batch_id) return order;
          const keep = new Set(only);
          return {
            ...order,
            lines: order.lines.filter((line) => keep.has(line.project_line_id)),
            rejected_line_ids: order.rejected_line_ids.filter((id) => keep.has(id)),
            only_line_ids: only,
          };
        });
      const body = built.body.batch_id ? { orders, batch_id: built.body.batch_id } : { orders };
      if (orders.length === 0) {
        setBatchResults([...skipped, ...refusals]);
        return;
      }

      const posted = await confirmMany.mutateAsync(body);
      // Lines the dry run held back were never posted, so the real press cannot name them:
      // carry them into the displayed entry (union by line no) so the scope is never silent.
      const result = {
        ...posted,
        results: posted.results.map((entry) => {
          const dryRun = answer.get(entry.pso_id);
          if (!entry.ok || !dryRun) return entry;
          const seen = new Set((entry.lines_held_back ?? []).map((line) => line.line_no));
          const heldBack = [
            ...(entry.lines_held_back ?? []),
            ...(dryRun.lines_held_back ?? []).filter((line) => !seen.has(line.line_no)),
          ];
          return {
            ...entry,
            lines_held_back: heldBack.length > 0 ? heldBack : entry.lines_held_back,
            lines_fulfilled_skipped:
              entry.lines_fulfilled_skipped ?? dryRun.lines_fulfilled_skipped ?? null,
          };
        }),
      };
      const labelOf = new Map(
        contributions.map((entry) => [
          entry.project_line_id ?? '',
          `line ${entry.line_no} ${entry.item_code}`,
        ]),
      );
      setPostedLines(
        Object.fromEntries(
          orders.map((order) => [
            order.pso_id,
            order.lines.map((line) => ({
              project_line_id: line.project_line_id,
              label: labelOf.get(line.project_line_id) ?? 'a line',
            })),
          ]),
        ),
      );
      setBatchResults([...skipped, ...refusals, ...result.results]);

      // What the press produced, in the three numbers a planner is about to act on (D3):
      // the promises made, the movements somebody now has to approve (the panel below lists
      // them), and the rows purchasing has been handed.
      const ok = result.results.filter((entry) => entry.ok);
      // Named lines the server skipped because nothing was open on them any more, their
      // stale saved decision cleared (#1362). Posted, but not confirmed.
      const fulfilled = ok.reduce(
        (total, entry) => total + (entry.lines_fulfilled_skipped ?? 0),
        0,
      );
      // #1362: lines the recheck held back so the rest of their order confirmed, and Buys
      // kept over goods that landed for their line. Both are named in the panel below.
      const heldBack = ok.reduce(
        (total, entry) => total + (entry.lines_held_back?.length ?? 0),
        0,
      );
      const landedBuys = ok.reduce(
        (total, entry) => total + (entry.landed_buy_notices?.length ?? 0),
        0,
      );
      // The server's own echo of the lines it wrote; only a server that predates the field
      // falls back to what this press posted.
      const linesConfirmed = ok.reduce((total, entry) => {
        if (entry.lines_confirmed) return total + entry.lines_confirmed.length;
        const posted = orders.find((order) => order.pso_id === entry.pso_id)?.lines.length ?? 0;
        return total + posted - (entry.lines_fulfilled_skipped ?? 0) - (entry.lines_held_back?.length ?? 0);
      }, 0);
      const transfers = ok.reduce((total, entry) => total + (entry.transfers_written ?? 0), 0);
      // What was already on a warehouse's list and stayed there (R16). Said only when there
      // IS one: on a first confirmation it is always zero, and a zero in the sentence would
      // be a number the reader has to decide to ignore.
      const kept = ok.reduce((total, entry) => total + (entry.transfers_kept ?? 0), 0);
      const inquiries = ok.reduce((total, entry) => total + (entry.inquiry_rows_created ?? 0), 0);
      // Covered lines Confirm just took OUT of their confirmation (owner ruling 23 Sep 2026,
      // `PLAN-board-reject-on-confirmed-line.md`) - said only when there IS one, the same
      // "zero is not worth a reader's attention" rule `kept` already follows.
      const withdrawn = ok.reduce((total, entry) => total + (entry.rejected_count ?? 0), 0);
      if (ok.length > 0) {
        const summary =
          `${linesConfirmed} line${linesConfirmed === 1 ? '' : 's'} confirmed · ` +
          `${transfers} transfer${transfers === 1 ? '' : 's'} proposed · ` +
          (kept > 0 ? `${kept} kept · ` : '') +
          `${inquiries} inquiry row${inquiries === 1 ? '' : 's'}` +
          (withdrawn > 0 ? ` · ${withdrawn} withdrawn` : '') +
          (fulfilled > 0
            ? ` · ${fulfilled} line${fulfilled === 1 ? '' : 's'} already fulfilled, decision cleared`
            : '') +
          (leftOutAtConfirm > 0 ? ` · ${leftOutAtConfirm} left out` : '') +
          (heldBack > 0 ? ` · ${heldBack} held back` : '') +
          (landedBuys > 0
            ? ` · ${landedBuys} Buy${landedBuys === 1 ? '' : 's'} kept over landed goods`
            : '');
        // S4 (fix round 2, reviewer): a press that left something out is not an unqualified
        // success, the owner's own words on SO420745 were "confirming silently is dangerous" -
        // so the toast that SAYS so reads amber, not the plain green every other Confirm gets.
        if (leftOutAtConfirm > 0 || heldBack > 0 || landedBuys > 0) {
          toast.warning(summary);
        } else {
          toast.success(summary);
        }
      }

      const committedPsoIds = new Set(
        result.results.filter((entry) => entry.ok).map((entry) => entry.pso_id),
      );
      // Only the lines the server says it wrote lose their local draft; a posted line it did
      // not name keeps its Saved pill. A server with no echo keeps the all-posted behaviour.
      const echoedByPso = new Map(
        result.results
          .filter((entry) => entry.ok && entry.lines_confirmed)
          .map((entry) => [
            entry.pso_id,
            new Set((entry.lines_confirmed ?? []).map((line) => line.project_line_id)),
          ]),
      );
      const committedLineIds = new Set(
        orders
          .filter((order) => committedPsoIds.has(order.pso_id))
          .flatMap((order) =>
            order.lines
              .map((line) => line.project_line_id)
              .filter((id) => echoedByPso.get(order.pso_id)?.has(id) ?? true),
          ),
      );
      setDraft((current) => {
        const next = { ...current };
        for (const contribution of contributions) {
          if (
            contribution.project_line_id &&
            committedLineIds.has(contribution.project_line_id) &&
            next[contribution.key]
          ) {
            delete next[contribution.key];
          }
        }
        return next;
      });
    } catch {
      // The mutation's own `onError` already toasted the message; nothing here is left to say.
      // Caught only so the rejection does not float unhandled past this async click handler.
    } finally {
      setConfirmingAll(false);
    }
  }, [
    board,
    buildConfirmOrders,
    confirmMany,
    unpostable,
    previewConfirmMany,
  ]);

  /**
   * The rows on screen, and the rows the selection holds.
   *
   * Matching on the code AND the name, because a planner knows a product by either. The counts
   * this produces are about the FILTER; every headline number on this screen stays
   * selection-scoped, exactly as it does under the day window.
   */
  /**
   * The rows and cells for the chosen axis.
   *
   * On the PRODUCT axis these are the server's own, untouched: its cells carry the stock
   * position per product and location, which no client-side regrouping could reproduce. The
   * pivoted axes are the same contributions grouped differently - one payload, one idea of what
   * a line is.
   */
  const axis = React.useMemo(() => {
    const cells = board.data?.cells ?? [];
    if (rowAxis === 'product') {
      return {
        rows: (board.data?.productRows ?? []).map((row) => ({
          key: row.item_code,
          label: row.item_code,
          description: row.description,
        })),
        cells,
      };
    }
    return boardAxis(rowAxis, cells);
  }, [board.data, rowAxis]);

  /**
   * What the re-uploaded book did to each cell's lines (AC-P3-2), keyed as the matrix keys
   * its cells. Empty on every board opened without a batch.
   */
  const changeAnnotations = React.useMemo(
    () => annotationsByCell(changeBatchData, axis.cells),
    [changeBatchData, axis],
  );

  /**
   * The same annotations keyed by LINE, for the list view: a list row is one line, so it
   * does not have to know which cell that line landed in to say what moved (AC-C9).
   */
  const changeAnnotationsByLine = React.useMemo(
    () => annotationsByLine(changeBatchData),
    [changeBatchData],
  );

  /**
   * What the decision strip is summed over: THE LINES THE CURRENT VIEW CAN SHOW.
   *
   * The grid renders cells, and at day granularity those are a 30-day window; the list renders
   * the whole selection. Summing the strip over the selection while filtering the grid over
   * its cells let a card read "Shared 71" off lines three months out and then empty the board
   * when it was pressed - a figure the view cannot produce, acted on. So the population
   * follows the view, and the card and the figures above it can never disagree.
   */
  const stripContributions = React.useMemo<BoardContribution[]>(() => {
    if (view === 'list') return listContributions;
    // Keyed, because a pivoted axis regroups the same cells and a line must not be counted
    // twice for landing in two of them.
    const seen = new Map<string, BoardContribution>();
    for (const cell of axis.cells) {
      for (const contribution of cell.contributions) seen.set(contribution.key, contribution);
    }
    return [...seen.values()];
  }, [view, listContributions, axis]);

  /**
   * The cells a decision-strip card leaves on screen (AC-D2): the ones carrying that kind on
   * EITHER side, suggested or decided.
   *
   * A filter over the axis's own cells, so the rows follow: a row every one of whose cells is
   * filtered out drops out with them, and the "N of M" fraction below counts it.
   */
  const visibleCells = React.useMemo(
    () =>
      kindFilter
        ? axis.cells.filter((cell) => cellCarriesKind(cell, draft, kindFilter))
        : axis.cells,
    [axis, draft, kindFilter],
  );

  /** The same card, obeyed by the list. Both views answer to one press or neither should. */
  const visibleListContributions = React.useMemo(
    () =>
      kindFilter
        ? listContributions.filter((contribution) =>
            contributionCarriesKind(contribution, draft[contribution.key] ?? null, kindFilter),
          )
        : listContributions,
    [listContributions, draft, kindFilter],
  );

  /** Lines per row, so the search can ask whether ANY of a row's lines matches. */
  const linesByRow = React.useMemo(() => {
    const map = new Map<string, BoardContribution[]>();
    for (const cell of visibleCells) {
      const key = cell.row_key ?? cell.item_code;
      const held = map.get(key);
      if (held) held.push(...cell.contributions);
      else map.set(key, [...cell.contributions]);
    }
    return map;
  }, [visibleCells]);

  // The Status filter, shared by the list (its Filters control) and the grid (the strip below the
  // cards). In the grid a row stays when ANY of its lines has one of the chosen states, read off
  // `verdictOf` - the same state the pill shows - never off "has a draft".
  // "Saved" is exactly what Confirm posts: the ONE `pressPostsContribution` predicate, read over the
  // same draft Confirm reads (`draftWithoutPreMark`), the orders about to be adopted and the
  // orders a pending planning change holds back.
  const isSavedLine = React.useCallback(
    (line: BoardContribution) =>
      pressPostsContribution(line, draftWithoutPreMark[line.key], {
        batchBlocked: pendingBatchSalesOrderIds.has(line.sales_order_id),
        unadopted: unadoptedSalesOrderIds.has(line.sales_order_id),
      }),
    [draftWithoutPreMark, pendingBatchSalesOrderIds, unadoptedSalesOrderIds],
  );
  // The Saved | All counts: the board's lines under the product search (and the day window,
  // through the list's whole-selection population), never under the Status filter.
  const scopeCounts = React.useMemo(() => {
    let saved = 0;
    let all = 0;
    for (const line of listContributions) {
      if (!contributionMatchesSearch(line, productSearch)) continue;
      all += 1;
      if (isSavedLine(line)) saved += 1;
    }
    return { saved, all };
  }, [listContributions, productSearch, isSavedLine]);
  const visibleProductRows = React.useMemo(
    () =>
      axis.rows.filter(
        (row) =>
          (!kindFilter || linesByRow.has(row.key)) &&
          rowMatchesSearch(row, linesByRow.get(row.key) ?? [], productSearch) &&
          (linesByRow.get(row.key) ?? []).some((line) => {
            return (
              (scope === 'all' || isSavedLine(line) === (scope === 'saved')) &&
              (statusFilter.length === 0 ||
                statusFilter.includes(verdictOf(line, draft[line.key] ?? null)))
            );
          }),
      ),
    [axis, linesByRow, productSearch, kindFilter, statusFilter, scope, draft, isSavedLine],
  );

  const otherFilters =
    productSearch.trim().length > 0 || kindFilter !== null || statusFilter.length > 0;
  // The Saved segment narrows the rows too, so the board says so ("N of M").
  const filtering = otherFilters || scope === 'saved';

  /**
   * Every key the board-wide "Save all suggested" button would post (D15): whichever lines the
   * planner is ACTUALLY LOOKING AT right now, filtered by whatever this screen's own dials
   * (view, the decision-strip card, product search) currently leave standing - never
   * `allContributions`. A board-wide press acting on lines a filter had hidden would save
   * something the button's own count never claimed to.
   *
   * The list already carries its own "whole selection, kind-filtered" population
   * (`visibleListContributions`); the grid additionally narrows by the product search, which
   * only ever touches the rows, never the list.
   *
   * `canQuickSave` reads `draftWithoutPreMark`, not `draft` (S5, owner ruling 25 Sep 2026,
   * issue #1245): its own `!draft[key]` rule already treats a line with no entry as eligible,
   * and a `Change proposed` line is exactly that - a proposal nobody has saved. Reads as
   * `undefined` here, so it counts into "Save all suggested (M)" and `decideMany` (below)
   * overwrites the pre-mark with a real decision, dropping the flag for good.
   */
  const quickSaveKeys = React.useMemo(() => {
    const population =
      view === 'list'
        ? visibleListContributions
        : (() => {
            const shownRowKeys = new Set(visibleProductRows.map((row) => row.key));
            const seen = new Map<string, BoardContribution>();
            for (const cell of visibleCells) {
              if (!shownRowKeys.has(cell.row_key ?? cell.item_code)) continue;
              for (const contribution of cell.contributions) {
                seen.set(contribution.key, contribution);
              }
            }
            return [...seen.values()];
          })();
    return population
      .filter((contribution) => canQuickSave(contribution, draftWithoutPreMark))
      .map((contribution) => contribution.key);
  }, [view, visibleListContributions, visibleCells, visibleProductRows, draftWithoutPreMark]);

  /**
   * Orders the link asked for that the board came back without.
   *
   * A shared link can name an order that has since been delivered or closed, or one that was
   * mistyped. Opening a board of four when the link asked for five, and saying nothing, is the
   * quiet subtraction that makes a shared link untrustworthy. The message states what is
   * observable and does not guess which of the two happened.
   */
  const missingOrders = React.useMemo(() => {
    if (!board.data) return [];
    const present = new Set(board.data.orders.map((order) => order.so_number));
    return soNumbers.filter((soNumber) => !present.has(soNumber));
  }, [board.data, soNumbers]);

  const bucketLabel = React.useMemo(() => {
    const map = new Map<string, string>();
    // Through the same de-jargoning the column headers go through: the dialog title reads the
    // same label, and it was still saying "w/c 24 Nov 2025" after the headers had stopped.
    for (const bucket of board.data?.dateBuckets ?? []) {
      map.set(bucket.key, bucketLabelText(bucket.label));
    }
    return map;
  }, [board.data]);

  // The cell the dialog is showing has to be re-read from the board on every render, or the
  // decision pills inside it would keep the shape they had when it was opened.
  const liveCell = React.useMemo(() => {
    if (!openCell || !board.data) return null;
    // Re-read from the cells of the CURRENT axis, keyed the way that axis keys them. Looking it
    // up in the server's product cells found nothing on a pivoted board, so the dialog simply
    // did not open.
    const openKey = openCell.row_key ?? openCell.item_code;
    return (
      axis.cells.find(
        (cell) =>
          (cell.row_key ?? cell.item_code) === openKey &&
          cell.bucket_key === openCell.bucket_key,
      ) ?? null
    );
  }, [openCell, board.data, axis]);

  return (
    <div className="space-y-4">
      {/* Title left, actions right, and the row WRAPS. A plain `items-center justify-between`
          does not, so at narrow widths the controls landed on top of the title and pushed the
          page sideways - which is what the captain screenshotted. */}
      <div
        data-testid="board-header"
        className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
      >
        <h2
          data-testid="board-header-title"
          className="min-w-0 text-lg font-semibold break-words"
        >
          {`Planning ${soNumbers.length} sales orders together`}
        </h2>
        {/* No search in the header: the list draws the box in its toolbar, and the grid in the
            filter strip under the summary cards (same box, same URL `product=` param). */}

        <div
          data-testid="board-header-actions"
          className="flex w-full flex-wrap items-center gap-2 sm:w-auto"
        >
          {granularity === 'day' && (
            <>
              <Button type="button" variant="outline" size="sm" onClick={() => shiftWindow(-1)}>
                Earlier days
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={() => shiftWindow(1)}>
                Later days
              </Button>
            </>
          )}
          {/* Labelled in words, because "Product / Sales order / Customer / Project" in a bare
              select says nothing about what it does to the grid. The captain wrote it as
              "Rows: Product | Sales order | Customer | Project", so that is what it reads. */}
          <div className="flex w-full items-center gap-2 sm:w-auto">
            <label htmlFor="rows" className="text-sm text-muted-foreground">
              Rows
            </label>
            <div className="w-full sm:w-40">
              <SearchableSelect
                id="rows"
                value={rowAxis}
                onChange={(value) => setRowAxis(value as BoardRowAxis)}
                options={ROW_AXIS_OPTIONS}
              />
            </div>
          </div>
          <div className="w-full sm:w-44">
            <SearchableSelect
              value={granularity}
              onChange={(value) => {
                // A window belongs to the view that scrolled it; carrying it into week or month
                // would silently pin those to a date the planner never chose.
                setDayWindow(undefined);
                setGranularity(value as BoardGranularity);
              }}
              options={GRANULARITY_OPTIONS}
            />
          </div>
          {/* Grid | List (D2): "how do I review it" - the grid answers what a product owes by
              date, the list answers what is about to be committed, across every order, in one
              scan. A toggle, not two screens, because it is the same draft either way. */}
          <div className="inline-flex rounded-md border border-input" role="group" aria-label="Board view">
            <Button
              type="button"
              size="sm"
              variant={view === 'grid' ? 'primary' : 'ghost'}
              className="rounded-e-none"
              aria-pressed={view === 'grid'}
              onClick={() => setView('grid')}
            >
              <LayoutGrid className="size-4" aria-hidden />
              Grid
            </Button>
            <Button
              type="button"
              size="sm"
              variant={view === 'list' ? 'primary' : 'ghost'}
              className="rounded-s-none border-s border-input"
              aria-pressed={view === 'list'}
              onClick={() => setView('list')}
            >
              <List className="size-4" aria-hidden />
              List
            </Button>
          </div>
          {/* NO "Back to sales orders" HERE. It lives under the gear on the bar below (R12):
              this row is the controls that decide what the board SHOWS, and a way off the
              screen sitting among them competed with them for the same glance. */}
        </div>
      </div>

      {/* THE ONE ACTION BAR (D1). Its own row above the grid/list so it is visible whichever
          view is on screen. What it says on the left is what the button on the right will
          do, counted over the same population, and the order is fixed: the gear (the rare
          things) then Confirm, last on the right, where a primary action belongs.
          The gear renders EVEN WHILE THE BOARD IS STILL LOADING or has come back empty: it
          is this screen's only way off it, since the header row deliberately carries none
          (the comment above it) - a board slow to load, or with nothing to plan, still needs
          an exit. Confirm and its counter stay gated on real data: there is nothing to
          confirm before there is a board. */}
      <div
        data-testid="board-action-bar"
        className="flex flex-col gap-2 rounded-lg border border-border px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between"
      >
        {board.data && board.data.cells.length > 0 ? (
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span
              data-testid="board-confirm-summary"
              className="text-sm text-muted-foreground tabular-nums"
            >
              {`${confirmSummary.toConfirm} to confirm · ${confirmSummary.rejected} rejected`}
              {/* C4 (code review round 3 batch 2): a saved line the engine has re-suggested
                  is dropped from Confirm with no trace beyond the pill itself - stated here
                  too, and only while it applies, the same rule the two figures beside it
                  follow. */}
              {confirmSummary.changed > 0 ? ` · ${confirmSummary.changed} changed` : ''}
            </span>
            {/* AC-DT-1/AC-DT-4 (`PLAN-oi-decision-trail-ui.md`): "Revision N, confirmed by
                <name>, N lines" or "No decision yet" - wraps rather than truncates
                (AC-DT-7), since a multi-order board's own text can run long. */}
            <span
              data-testid="board-decision-header"
              className="whitespace-normal break-words text-sm text-muted-foreground"
            >
              {decisionHeader}
            </span>
          </span>
        ) : (
          <span />
        )}
        <div className="flex flex-wrap items-center gap-2">
          {/* D15: the board-wide quick save, left of the gear - every line shown right now
              that a quick save could still touch, in one press. No confirmation: it is
              reversible the same way a single quick save is, through Undo all or a line's
              own Undo. */}
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={quickSaveKeys.length === 0}
            onClick={() => void decideMany(quickSaveKeys)}
          >
            {`Save all suggested (${quickSaveKeys.length})`}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                size="sm"
                variant="outline"
                mode="icon"
                aria-label="Board actions"
              >
                <Settings className="size-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            {/* Bounded so a long undo label (AC-R2-F06: ", reconstructed" plus its own
                second line) truncates INSIDE the menu instead of growing the menu wider
                than a 375px viewport - `DropdownMenuContent` carries no width cap of its
                own (only `min-w-[8rem]`), so an unbounded flex item would otherwise just
                grow to fit its untruncated text. `sm:max-w-md` (review round 1, S7), not
                `sm:max-w-80`: the reconstructed label plus its own "Saved drafts and row
                notes are not restored" second line needs more room at 1280 than the
                narrower cap left, and was itself getting truncated. */}
            <DropdownMenuContent align="end" className="max-w-[calc(100vw-2rem)] sm:max-w-md">
              {/* Every decision taken on this board since it was opened, or since the
                  last confirm, goes back to the suggestion - on the SERVER too (S4): each
                  key is deleted through `decide(key, null)`, or the next board read would
                  seed the discarded lines straight back in. Nothing CONFIRMED moves. */}
              <DropdownMenuItem
                disabled={Object.keys(draft).length === 0}
                onSelect={
                  Object.keys(draft).length === 0 ? undefined : () => setUndoAllOpen(true)
                }
              >
                <Undo2 className="size-4" aria-hidden />
                Undo all
              </DropdownMenuItem>
              {/* Undo last confirm, one entry per order the newest revision can be undone
                  for (R6): below Undo all, same permission as Confirm, hidden entirely when
                  nothing on the board is undoable (AC-UC-04). A refused order keeps its
                  entry so purchasing's reason is where the planner is already looking,
                  rather than a control that silently is not there (AC-UC-03). */}
              {undoableOrders.length > 0 && (
                <>
                  <DropdownMenuSeparator />
                  {undoableOrders.map((order) => {
                    const mode = order.undo?.mode ?? 'journal';
                    // AC-R2-F02: a reconstructed entry names itself so the admin knows,
                    // before pressing, that this is a best-effort undo rather than a
                    // journal replay.
                    const label = `Undo ${order.so_number} confirm (rev ${order.undo?.revision_no})${
                      mode === 'reconstructed' ? ', reconstructed' : ''
                    }`;
                    const reason = UNDO_REFUSAL_TITLES[order.undo?.refusal ?? ''];
                    // A refusal is the more urgent of the two possible second lines
                    // (AC-R2-F03); unrefused, a reconstructed entry states what it will
                    // not bring back (AC-R2-F02).
                    const secondLine =
                      reason ?? (mode === 'reconstructed' ? RECONSTRUCTED_UNDO_NOTE : null);
                    const disabled = Boolean(order.undo?.refusal);
                    return (
                      <DropdownMenuItem
                        key={order.sales_order_id}
                        // `min-w-0`: a flex item's default `min-width: auto` would let its
                        // content dictate the item's width and defeat the `truncate` below,
                        // so the menu would grow past the viewport instead (AC-R2-F06).
                        className="min-w-0"
                        disabled={disabled}
                        onSelect={
                          disabled
                            ? undefined
                            : () =>
                                setUndoTarget({
                                  orderId: order.project_sales_order_id as string,
                                  soNumber: order.so_number,
                                  decisionId: order.undo?.decision_id as string,
                                  mode,
                                })
                        }
                      >
                        <Undo2 className="size-4" aria-hidden />
                        {/* Exactly one `title` owner in this item, the label span - a
                            disabled item's own `title` never renders (AC-UC-03), so the
                            reason is plain visible text underneath instead. `min-w-0` on
                            both this wrapper and the DropdownMenuContent's own width cap
                            keep a long label truncating INSIDE the menu rather than
                            forcing it wider than the viewport (AC-R2-F06). */}
                        <span className="flex min-w-0 flex-col">
                          <span className="truncate" title={label}>
                            {label}
                          </span>
                          {secondLine ? (
                            // `text-foreground/70`, not `text-muted-foreground`
                            // (review round follow-up): the disabled item's own
                            // reduced opacity stacks with a muted foreground and
                            // drops this line below the contrast floor.
                            <span
                              className="truncate text-xs text-foreground/70"
                              title={secondLine}
                            >
                              {secondLine}
                            </span>
                          ) : null}
                        </span>
                      </DropdownMenuItem>
                    );
                  })}
                </>
              )}
              <DropdownMenuItem onSelect={onBack}>
                <ArrowLeft className="size-4" aria-hidden />
                Back to sales orders
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {/* An undo countdown must keep showing even once its own commit has cleared the
              board down to zero cells (review round) - the Confirm slot this occupies is
              not gated on there being anything left to confirm while it is counting down. */}
          {board.data && (board.data.cells.length > 0 || undoAction.pending) ? (
            <DeferredActionButton
              pending={undoAction.pending}
              verb="Undoing"
              subject={undoTarget?.soNumber}
              onCancel={undoAction.cancel}
              idle={
                <>
                  <Button
                    type="button"
                    size="sm"
                    data-testid="board-confirm"
                    disabled={
                      confirmSummary.toConfirm === 0 ||
                      confirmingAll ||
                      Boolean(confirmBlockedReason)
                    }
                    title={confirmBlockedReason ?? undefined}
                    onClick={() => void runConfirmAll()}
                  >
                    {`Confirm (${confirmSummary.toConfirm})`}
                  </Button>
                </>
              }
            />
          ) : null}
        </div>
      </div>

      {/* Why Confirm is off, when it is - stated, never a dead button. */}
      {confirmBlockedReason ? (
        <p data-testid="confirm-blocked" className="text-sm text-muted-foreground break-words">
          {confirmBlockedReason}
        </p>
      ) : null}

      {/* A line the planner decided that this confirmation cannot carry - LOUD, per the
          owner's own words on SO420745 ("confirming silently is dangerous"), REACHABLE
          ("a hyperlink to go to that line directly") and, once its reason is fixed,
          FIXABLE (AC-1/AC-2 - see the panel's own approving save). One notice per reason,
          so the count on the button and this banner always describe the same lines. */}
      {unpostable.length > 0 && (
        <Alert
          variant="warning"
          appearance="light"
          data-testid="board-left-out-banner"
        >
          <AlertIcon>
            <AlertTriangle />
          </AlertIcon>
          <AlertContent className="space-y-2">
            {UNPOSTABLE_REASONS.flatMap((reason) => {
              const lines = unpostable.filter((entry) => entry.reason === reason);
              if (lines.length === 0) return [];
              return unpostableNotices(reason, lines).map((notice, index) => (
                <AlertDescription
                  key={`${reason}-${index}`}
                  className="break-words"
                >
                  {notice.named.map((name, nameIndex) => (
                    <React.Fragment key={name.line.contribution.key}>
                      {nameIndex > 0 ? ', ' : ''}
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        // `Button`'s own base class carries `whitespace-nowrap`
                        // (`components/ui/button.tsx`) - fine for a short label, but a long
                        // item code has nowhere to wrap at 375px without overriding it back
                        // (design nit, fix round 2 review).
                        className="h-auto min-h-0 whitespace-normal break-words p-0 text-left align-baseline text-sm"
                        onClick={() => focusLeftOutLine(name.line.contribution)}
                      >
                        {name.label}
                      </Button>
                    </React.Fragment>
                  ))}
                  {notice.moreCount > 0 ? ` and ${notice.moreCount} more` : ''}
                  {` ${notice.clause}`}
                </AlertDescription>
              ));
            })}
          </AlertContent>
        </Alert>
      )}

      {batchResults && (
        <div
          data-testid="board-confirm-results"
          className="space-y-1 rounded-lg border border-border px-3 py-2.5"
        >
          <p className="text-sm font-medium">
            {/* N2 (fix round): a HELD-BACK withdrawal is not a refusal (its own order may
                have confirmed everything else this same press), so it is excluded from
                BOTH sides of this count - a reader must never see "1 of 2" for an order
                that in fact confirmed everything it could. */}
            {(() => {
              const countable = batchResults.filter((r) => !r.heldBack);
              return `${countable.filter((r) => r.ok).length} of ${countable.length} orders confirmed`;
            })()}
          </p>
          <ul className="space-y-1">
            {batchResults.map((result) => {
              const order = board.data?.orders.find(
                (candidate) => candidate.project_sales_order_id === result.pso_id,
              );
              const label = order?.so_number ?? result.so_number ?? result.pso_id;
              // A refusal names the LINES it refused, not just the order: the fix is on one
              // row, and "SO404352: refused" sends a planner to read thirty of them.
              const failing = result.failing_lines ?? [];
              // #1362: a confirmed order can still name lines - held back, or a Buy kept
              // over landed goods - and each one is something to act on.
              const notices = result.ok ? confirmNoticeLines(result) : [];
              return (
                <li key={`${result.pso_id}-${result.so_number ?? ''}`} className="space-y-0.5">
                  <span
                    className={`block text-sm break-words ${
                      result.ok
                        ? 'text-emerald-700'
                        : result.heldBack
                          ? 'text-muted-foreground'
                          : 'text-destructive'
                    }`}
                  >
                    {result.ok
                      ? `${label}: confirmed as revision ${result.decision_revision} (${result.inquiry_rows_created ?? 0} purchase row${(result.inquiry_rows_created ?? 0) === 1 ? '' : 's'} handed over)`
                      : `${label}: ${result.error ?? 'refused'}`}
                  </span>
                  {result.ok && result.lines_confirmed && result.lines_confirmed.length > 0 && (
                    <ul
                      data-testid={`board-confirm-lines-${result.pso_id}`}
                      className="space-y-0.5"
                    >
                      <li className="text-sm break-words">
                        {`Confirmed: ${result.lines_confirmed
                          .map((line) => `line ${line.line_no ?? '?'} ${line.item_code ?? ''}`.trim())
                          .join(' · ')}`}
                      </li>
                      {(result.lines_carried ?? 0) > 0 && (
                        <li className="text-sm text-muted-foreground">
                          {`${result.lines_carried} carried forward unchanged`}
                        </li>
                      )}
                    </ul>
                  )}
                  {(() => {
                    if (!result.ok) return null;
                    if (!result.lines_confirmed) {
                      return (
                        <p className="text-sm text-muted-foreground">
                          The server did not name the lines it confirmed.
                        </p>
                      );
                    }
                    const posted = postedLines[result.pso_id] ?? [];
                    const echoed = new Set(result.lines_confirmed.map((line) => line.project_line_id));
                    // A posted line the server did not name. Comparing lengths would flag a
                    // planning-change echo that carries lines the board never posted.
                    const missing = posted.filter((line) => !echoed.has(line.project_line_id));
                    if (missing.length === 0) return null;
                    return (
                      <p
                        data-testid={`board-confirm-mismatch-${result.pso_id}`}
                        className="rounded-md bg-amber-50 px-2 py-1.5 text-sm break-words text-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
                      >
                        {`Posted ${posted.length}, server confirmed ${result.lines_confirmed.length}: ${missing
                          .map((line) => line.label)
                          .join(' · ')}`}
                      </p>
                    );
                  })()}
                  {failing.length > 0 && (
                    <ul
                      className={`space-y-0.5 rounded-md px-2 py-1.5 ${
                        result.heldBack ? 'bg-muted/50' : 'bg-destructive/5'
                      }`}
                    >
                      {failing.map((line, index) => (
                        <li
                          key={`${line.line_no ?? 'order'}-${line.item_code ?? ''}-${index}`}
                          className={`text-sm break-words ${
                            result.heldBack ? 'text-muted-foreground' : 'text-destructive'
                          }`}
                        >
                          {failingLineText(line)}
                        </li>
                      ))}
                    </ul>
                  )}
                  {result.ok && (result.lines_fulfilled_skipped ?? 0) > 0 && (
                    <p data-testid={`board-confirm-fulfilled-${result.pso_id}`} className="text-sm break-words text-muted-foreground">
                      {`${result.lines_fulfilled_skipped} line${
                        result.lines_fulfilled_skipped === 1 ? '' : 's'
                      } already fulfilled, decision cleared`}
                      {(() => {
                        const echoed = new Set((result.lines_confirmed ?? []).map((line) => line.project_line_id));
                        const held = new Set((result.lines_held_back ?? []).map((line) => line.line_no));
                        const names = (postedLines[result.pso_id] ?? [])
                          .filter((line) => !echoed.has(line.project_line_id))
                          .filter((line) => !held.has(Number(line.label.split(' ')[1])))
                          .map((line) => line.label);
                        return names.length > 0 ? `: ${names.join(' \u00b7 ')}` : '';
                      })()}
                    </p>
                  )}
                  {notices.length > 0 && (
                    <ul
                      data-testid="board-confirm-notices"
                      className="space-y-0.5 rounded-md bg-amber-50 px-2 py-1.5 dark:bg-amber-950/30"
                    >
                      {notices.map((text, index) => (
                        <li
                          key={`${result.pso_id}-notice-${index}`}
                          className="text-sm break-words text-amber-800 dark:text-amber-300"
                        >
                          {text}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {board.isError ? (
        <Alert variant="destructive" appearance="light">
          <AlertIcon>
            <AlertTriangle />
          </AlertIcon>
          <AlertContent>
            <AlertTitle>The planning board could not be loaded</AlertTitle>
            <AlertDescription>
              {board.error instanceof Error ? board.error.message : 'Try again in a moment.'}
              <div className="mt-3">
                <Button type="button" size="sm" variant="outline" onClick={() => board.refetch()}>
                  Try again
                </Button>
              </div>
            </AlertDescription>
          </AlertContent>
        </Alert>
      ) : board.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-72 w-full" />
        </div>
      ) : !board.data || board.data.cells.length === 0 ? (
        <Card>
          <CardContent className="px-6 py-10 text-center">
            <PackageSearch className="mx-auto size-6 text-muted-foreground" aria-hidden />
            {/* No cells does NOT mean the selection is empty. A day window scrolled to a
                stretch with nothing due has no cells while the selection still holds every
                one of its lines, and "these orders have nothing to plan" would flatly
                contradict them. The selection-scoped total is the only thing that can tell
                the two apart.

                NEITHER EMPTY STATE SPEAKS ABOUT DELIVERY ANY MORE (14 Sep 2026 ruling). The
                board asks who decided where a line's stock comes from, not whether delivery
                is still outstanding - a delivered line nobody decided is on this board - so
                "Nothing is outstanding ..." named the wrong question and told a planner
                looking at an unplanned completed order that there was nothing to do. */}
            {(board.data?.line_count ?? 0) > 0 ? (
              <>
                <h3 className="mt-2 text-sm font-semibold">No lines in these dates</h3>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {`The selection holds ${board.data?.line_count} lines on other dates.`}
                </p>
              </>
            ) : (
              <>
                <h3 className="mt-2 text-sm font-semibold">
                  No lines to plan on these sales orders
                </h3>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  Every line is cancelled or marked no purchase needed.
                </p>
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          {missingOrders.length > 0 && (
            <Alert appearance="light">
              <AlertIcon>
                <AlertTriangle />
              </AlertIcon>
              <AlertContent>
                <AlertTitle>
                  {`${missingOrders.join(', ')} ${
                    missingOrders.length === 1 ? 'has' : 'have'
                  } nothing to plan on this board.`}
                </AlertTitle>
              </AlertContent>
            </Alert>
          )}

          {/* NO "N of M lines are already past their delivery date" BANNER (retired 26
              August 2026, AC-C5). The column headers already say "Already past" over the
              periods it is talking about, so the banner restated the screen in words and
              pushed the grid down a row to do it. */}

          {/* NO POLICY BANNER. It named the rule and listed its weights across the top of the
              board, and the captain's verdict on it was "this text is not needed at the top":
              it is not what anybody opens this screen to read, and it was there whether or not
              a question about ranking had been asked. The information is not lost - the rank
              popover on a row names the policy above its factor table, which is where somebody
              IS asking - see `BoardRankPopover` and PLAN 13.10. */}

          {/* NO LEGEND ROW (retired 26 August 2026, AC-C5). The decision strip below carries
              every label in its own colour, so a legend was the same six words twice - and
              the one a reader meets first should be the one with the numbers on it. */}

          {/* Dims rather than blanks (D16): Confirm's own refetch of this SAME selection
              leaves every row mounted, so a card's own open/closed state and scroll position
              survive it - a skeleton in their place would not. `boardRefreshing` above. A
              draft save/undo never lands here at all any more; it patches the cache without
              asking for a refetch. A granularity/day-window turn is a DIFFERENT selection and
              still shows the true skeleton (`board.isLoading` above) - see the note on
              `usePlanningBoard`. */}
          <div
            data-testid="board-content"
            className={`space-y-4 transition-opacity ${
              boardRefreshing ? 'opacity-60' : 'opacity-100'
            }`}
          >
            {/* Suggested vs decided across the selection, card per kind (AC-D2). */}
            <DecisionStrip
              contributions={stripContributions}
              draft={draft}
              active={kindFilter}
              onToggle={(kind) =>
                setKindFilter((current) => (current === kind ? null : kind))
              }
            />

            {/* The movements this board's confirmations raised, ABOVE the matrix (R13). They
                used to be reachable only from the transfers screen, so the promise was made
                here and the movement it implied was approved by somebody who had not seen the
                order it was for. */}
            <BoardTransfersPanel
              soNumbers={soNumbers}
              justConfirmed={batchResults !== null}
              inquiryRows={(batchResults ?? [])
                .filter((result) => result.ok)
                .reduce((total, result) => total + (result.inquiry_rows_created ?? 0), 0)}
            />

            {view === 'list' ? (
              /* D2: one row per contributing line across every cell of the WHOLE selection, not
                 the pivoted/windowed rows the grid shows - the point is an overview, so the row
                 axis and product search that shape the grid do not narrow it.

                 NO `isLoading` HERE (D16): this only ever mounts once `board.data` already has
                 cells, so passing the query's `isFetching` skeleton-wiped a list that already
                 had every row it needed - the flicker itself. The dim wrapper above says the
                 same thing without discarding what is on screen. */
              <FulfilmentBoardListView
                contributions={visibleListContributions}
                draft={draft}
                onDecide={decide}
                onDecideMany={decideMany}
                onDecideBatch={decideBatch}
                annotations={changeAnnotationsByLine}
                // The ONE board search (S6, R-J) drives Grid and List alike: this list draws it in
                // its own toolbar (`search` below), the grid in its filter strip.
                externalSearch={productSearch}
                status={{ value: statusFilter, onChange: setStatusFilter }}
                scope={{
                  value: scope,
                  onChange: setScope,
                  savedCount: scopeCounts.saved,
                  allCount: scopeCounts.all,
                  isSaved: isSavedLine,
                }}
                search={{
                  value: productSearchInput,
                  onChange: setProductSearchInput,
                  placeholder: 'Search sales order, customer, project or product',
                }}
                // `visibleListContributions` also narrows by `kindFilter` (above), which
                // is not part of `externalSearch` - so the reset key carries both, or
                // toggling a kind card while on page 3 would leave the list showing
                // whatever landed there instead of the top of the narrowed set.
                pageResetKey={`${productSearch}|${kindFilter ?? ''}|${scope}`}
                // AC-5: which row the left-out banner asked to see, opened and scrolled to
                // once. Cleared once handled so a second click on the SAME line still fires
                // the effect the list reads it with.
                focusKey={focusKey}
                onFocusHandled={handleLeftOutLineFocused}
                // AC-RS-42: the SAME pool-share policy the grid's own cell dialog passes,
                // so a line's Stock button reads the site-pool subtotal the tenant actually
                // runs rather than the component's own constant default.
                poolSharePct={board.data?.pool_share_pct}
                // #1312: gates the paperclip's own upload/remove (Q6).
                canEditAttachments={canEditAttachments}
                // #1312: the ONE lookup call's own result, computed above over
                // every contribution the whole selection carries.
                attachmentsByLine={attachmentsByLine}
              />
            ) : (
              <>
                {/* The grid's filter strip: the same search box, Saved | All toggle and Status filter
                    the list's toolbar carries, inside the same Card > CardHeader shell so the box
                    keeps its left edge between views. No Columns, Expand/Collapse or Decide. */}
                <Card>
                  <CardHeader className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div
                      data-testid="board-grid-filter-strip"
                      className="flex w-full flex-wrap items-center gap-2 py-5"
                    >
                      <ListSearchInput
                        value={productSearchInput}
                        onChange={setProductSearchInput}
                        placeholder="Search sales order, customer, project or product"
                        aria-label="Search sales order, customer, project or product"
                        className="w-64"
                      />
                      <BoardScopeToggle
                        value={scope}
                        onChange={setScope}
                        savedCount={scopeCounts.saved}
                        allCount={scopeCounts.all}
                      />
                      <SearchableMultiSelect
                        value={statusFilter}
                        onChange={setStatusFilter}
                        options={VERDICT_FILTER_OPTIONS}
                        placeholder="Status"
                        size="sm"
                        triggerClassName="w-40 shrink-0"
                      />
                    </div>
                  </CardHeader>
                </Card>
                {/* How much of the board is on screen. Only while a filter is on, and stated as
                    a fraction, so a narrowed board is never mistaken for the whole one. */}
                {filtering && (
                  <p className="text-sm text-muted-foreground tabular-nums">
                    {`${visibleProductRows.length} of ${axis.rows.length} ${ROW_AXIS_NOUNS[rowAxis]}`}
                  </p>
                )}

                {visibleProductRows.length === 0 ? (
                  <Card>
                    <CardContent className="px-6 py-10 text-center">
                      <PackageSearch className="mx-auto size-6 text-muted-foreground" aria-hidden />
                      {/* NOT the "owes nothing" copy: the selection owes plenty, the filter
                          simply matched none of it. */}
                      <h3 className="mt-2 text-sm font-semibold">
                        {scope === 'saved'
                          ? otherFilters
                            ? 'No saved decisions match the filter'
                            : 'Nothing to confirm yet'
                          : 'No products match'}
                      </h3>
                    </CardContent>
                  </Card>
                ) : (
                  <FulfilmentBoardMatrix
                    dateBuckets={board.data.dateBuckets}
                    rows={visibleProductRows}
                    rowHeader={
                      ROW_AXIS_OPTIONS.find((option) => option.value === rowAxis)?.label ?? 'Product'
                    }
                    cells={visibleCells}
                    draft={draft}
                    annotations={changeAnnotations}
                    onOpenCell={(cell) => setOpenCell(cell)}
                    onDecideMany={decideMany}
                    onUndoMany={undoMany}
                  />
                )}
              </>
            )}
          </div>

          {/* NO COMMIT SECTION (R13). It was one card per sales order carrying a Confirm,
              a "N of M lines decided" counter and a paragraph explaining where Buy rows and
              stock transfers go. The counter is the bar at the top, the Confirm is the one
              button beside it, and the two destinations are a panel of real transfers above
              and a link under it - facts rather than a description of them. */}
        </>
      )}

      {liveCell && (
        <BoardCellBreakdownDialog
          cell={liveCell}
          bucketLabel={bucketLabel.get(liveCell.bucket_key) ?? liveCell.bucket_key}
          draft={draft}
          poolSharePct={board.data?.pool_share_pct}
          onDecide={decide}
          onDecideMany={decideMany}
          onClose={() => setOpenCell(null)}
        />
      )}

      {/* Undo all discards work nobody can get back. Same rule, same component. */}
      <AlertDialog open={undoAllOpen} onOpenChange={setUndoAllOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {`Discard ${Object.keys(draft).length} draft decision${
                Object.keys(draft).length === 1 ? '' : 's'
              }?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {/* N-8 (reviewer, fix round 3): "Every line goes back to the suggestion"
                  was true of one kind of line only - a line the open change batch names
                  goes back to its PROPOSED CHANGE (AC-B13), which is still counted
                  toward Confirm. */}
              Every saved decision is discarded, and a line the book changed goes back to
              its proposed change. Nothing already confirmed changes.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep them</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const keys = Object.keys(draft);
                // A BARE PRE-MARK HAS NOTHING ON THE SERVER (browser pass, 22 Sep 2026:
                // 17 DELETEs went out and 16 came back 404). It is the board's own
                // suggestion seeded into this session's draft, never a saved decision, so
                // there is nothing to delete for it - and it comes straight back as a
                // pre-mark through `preMarksFor` below either way.
                const onServer = keys.filter((key) => {
                  const contribution = allContributions.find(
                    (entry) => entry.key === key,
                  );
                  return (
                    !contribution || !isPreMarkOnly(contribution, draft[key] ?? null)
                  );
                });
                setUndoAllOpen(false);
                // S4/AC-4.3: a saved line's draft lives on the server now, so discarding it
                // has to reach the server too, or it re-seeds right back in off the next
                // board refetch. STRAIGHT to `removeDraftKey`, not through N `decide(key,
                // null)` calls (B1, code review round 3): those each read the same stale
                // `draft` closure before any of them had committed, so the last call to
                // actually run undid only its own key and put every earlier one back. One
                // batch, one commit: on full success every key clears in the SAME
                // `setDraft({})`; on a partial failure only the keys whose DELETE actually
                // failed are kept, never the whole map. `removeDraftKey` (not the bare
                // mutation) so `pendingDeletes` tracks these the same way it tracks a
                // single-line Undo (C1, code review round 3 batch 2).
                void (async () => {
                  const outcomes = await Promise.all(
                    onServer.map(async (key) => {
                      try {
                        await removeDraftKey(key);
                        return { key, ok: true as const };
                      } catch {
                        return { key, ok: false as const };
                      }
                    }),
                  );
                  const failedKeys = outcomes
                    .filter((entry) => !entry.ok)
                    .map((entry) => entry.key);
                  // AC-B13 applies to the board-wide discard too (SF-5): a key the open
                  // batch pre-marked comes back as the pre-mark rather than disappearing,
                  // so a line the book moved still reads `Change proposed` and is still
                  // counted toward Confirm. `setDraft({})` dropped every one of them.
                  const preMarksFor = (undone: string[]): BoardDraft => {
                    const back: BoardDraft = {};
                    for (const key of undone) {
                      if (preMarkedKeySet.current.has(key)) {
                        back[key] = { verdict: 'approved', preMarked: true };
                      }
                    }
                    return back;
                  };
                  if (failedKeys.length === 0) {
                    setDraft(preMarksFor(keys));
                    // AC-B14 (browser pass, 22 Sep 2026): this path discarded every draft
                    // on the board and said nothing, while the grid cell's own undo of two
                    // lines toasted. Counted over the lines that HAD a decision to
                    // discard - a pre-mark was never saved, so undoing the board did not
                    // undo it.
                    if (onServer.length > 0) {
                      toast.success(
                        `${onServer.length} line${onServer.length === 1 ? '' : 's'} undone`,
                      );
                    }
                    return;
                  }
                  setDraft((current) => {
                    const kept: BoardDraft = preMarksFor(
                      keys.filter((key) => !failedKeys.includes(key)),
                    );
                    // Whatever failed keeps whatever it held; everything else has already
                    // been answered by `preMarksFor` above.

                    for (const key of failedKeys) {
                      if (current[key]) kept[key] = current[key];
                    }
                    return kept;
                  });
                  // Named by LINE, never by the contribution key: that key carries the core
                  // order id, which is a UUID and has no business on screen.
                  const failedLines = failedKeys
                    .map(
                      (key) =>
                        allContributions.find((entry) => entry.key === key)?.line_no,
                    )
                    .filter((lineNo): lineNo is number => lineNo != null);
                  toast.error(
                    `${failedKeys.length} line${failedKeys.length === 1 ? '' : 's'} could not be discarded` +
                      (failedLines.length ? ` (line ${failedLines.join(', ')})` : ''),
                  );
                })();
              }}
            >
              Discard
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

const UNPOSTABLE_REASONS: UnpostableReason[] = ['no_mirror', 'no_reserve_warehouse'];


