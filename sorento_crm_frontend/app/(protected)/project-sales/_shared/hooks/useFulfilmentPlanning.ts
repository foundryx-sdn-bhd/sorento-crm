'use client';

import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  adoptSalesOrder,
  confirmMany,
  previewConfirmMany,
  deleteLineDraft,
  getClassificationEvidence,
  getPileQueue,
  getPlanningBoard,
  confirmSupply,
  getReconciliation,
  getStockDetail,
  getSupply,
  listFulfilmentPlanning,
  putLineDraft,
  rerunReconciliation,
} from '../services/fulfilmentPlanningService';
import { listPlans } from '../services/plansService';
import { failingLineText } from '../lib/fulfilmentBoard';
import type {
  AdoptSalesOrderResult,
  BoardCell,
  BoardContribution,
  BoardDecision,
  BoardGranularity,
  BoardLineDraft,
  BoardSource,
  ConfirmManyBody,
  ConfirmSupplyBody,
  FulfilmentPlanningListParams,
  PlanListParams,
  PlanningBoard,
} from '../types/fulfilmentPlanning.types';
import {
  ORDER_INQUIRY_ROWS_KEY,
  ORDER_INQUIRY_SUMMARY_KEY,
  ORDER_INQUIRY_WORKLIST_KEY,
  ORDER_INQUIRY_WORKLIST_SUMMARY_KEY,
} from './useOrderInquiry';
import { SALES_ORDERS_KEY, SALES_ORDER_KEY } from './useProjectSalesOrders';
// The confirmation is what RAISES a stock transfer, so the Transfers page and the two
// detail tabs reading it must not keep showing the previous revision's movements.
import { STOCK_TRANSFERS_KEY } from '@/app/(protected)/inventory-management/stock-transfers/hooks/useStockTransfers';
// The board's OWN transfers panel (D6) reads a different key from the transfers page, and it
// is the one on screen when Confirm is pressed: without this the movements the press just
// raised appear only after a reload, which is exactly the trip the panel exists to save.
import { BOARD_TRANSFERS_KEY } from './useBoardTransfers';
// A confirmation on a `?batch=` board APPLIES the planning change, so the batch's own record
// (applied_at, applied_by, the per-row applied state) is stale the moment it returns.
import { PLANNING_CHANGE_BATCH_KEY } from './usePlanningChanges';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

export const FULFILMENT_PLANNING_KEY = 'project-fulfilment-planning';
export const PLANNING_BOARD_KEY = 'project-fulfilment-board';
export const PILE_QUEUE_KEY = 'project-pile-queue';
export const CLASSIFICATION_EVIDENCE_KEY = 'project-classification-evidence';
export const STOCK_DETAIL_KEY = 'project-stock-detail';
export const RECONCILIATION_KEY = 'project-so-reconciliation';
export const SUPPLY_KEY = 'project-so-supply';
export const PLANS_KEY = 'project-supply-plans';

export const fulfilmentPlanningKey = (params: FulfilmentPlanningListParams) => [
  FULFILMENT_PLANNING_KEY,
  params,
];

export function useFulfilmentPlanning(params: FulfilmentPlanningListParams = {}) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: fulfilmentPlanningKey(params),
    queryFn: () => listFulfilmentPlanning(params),
    // The page and the search both change the key, so without this the grid empties to a
    // skeleton on every keystroke and every page turn. The previous page stays on screen
    // until the next one answers.
  });
}

/**
 * Start planning an outstanding core sales order.
 *
 * Separate from `useReconciliationMutations` because it is the only mutation on this screen
 * that a row can carry before any planning record exists, and it is the only one whose
 * success has to hand the caller an id it did not have (the sheet opens on the record that
 * was just created).
 *
 * No toast on the idempotent path beyond the truth: pressing Start planning on an order
 * somebody else already started is not an error and must not read like one.
 */
export function useFulfilmentPlanningMutations() {
  const queryClient = useQueryClient();

  const adopt = useMutation({
    mutationFn: (salesOrderId: string) => adoptSalesOrder(salesOrderId),
    onSuccess: (result: AdoptSalesOrderResult) => {
      queryClient.invalidateQueries({ queryKey: [FULFILMENT_PLANNING_KEY] });
      queryClient.invalidateQueries({ queryKey: [SALES_ORDERS_KEY] });
      toast.success(
        result.already_adopted
          ? `${result.so_number} is already being planned.`
          : `${result.so_number} is ready to plan.`,
      );
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { adopt };
}

/**
 * The reconciliation for one order, fetched when its sheet is open.
 *
 * `enabled` rather than an always-on query: the list carries five columns of the same
 * answer already, and pre-fetching a summary per row would run the mapping for orders
 * nobody opened.
 */
export function useReconciliation(psoId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: [RECONCILIATION_KEY, psoId ?? ''],
    queryFn: () => getReconciliation(psoId as string),
    enabled: Boolean(psoId) && enabled,
    // One retry, not the default three: the sheet is open in front of somebody, and three
    // backed-off attempts leave them looking at a skeleton for ten seconds before the
    // failure is admitted.
    retry: 1,
  });
}

/**
 * The composition for one order, fetched with the sheet for the same reason the
 * reconciliation is: it reads live stock, claims and incoming per line, and running that
 * for every row of the worklist would price the whole list on nobody's behalf.
 */
export function useSupply(psoId: string | undefined, enabled = true) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: [SUPPLY_KEY, psoId ?? ''],
    queryFn: () => getSupply(psoId as string),
    enabled: Boolean(psoId) && enabled,
    retry: 1,
    // A focus refetch mid-composition churns the proposal under CS for no decision
    // change; the confirm rechecks live facts anyway, so the sheet does not need to.
    staleTime: 15_000,
  });

  // The supply read is the one that notices an out-of-band change (it rechecks live
  // facts and may flip the decision to challenged), so when its review_state disagrees
  // with the cached reconciliation the pill sources are stale, not the sheet: refetch
  // them rather than letting "Confirmed" sit over a composition that says otherwise.
  const freshState = query.data?.review_state;
  useEffect(() => {
    if (!psoId || freshState === undefined) return;
    const summary = queryClient.getQueryData<{ review_state?: string | null }>([
      RECONCILIATION_KEY,
      psoId,
    ]);
    if (summary && (summary.review_state ?? null) !== (freshState ?? null)) {
      void queryClient.invalidateQueries({ queryKey: [RECONCILIATION_KEY, psoId] });
      void queryClient.invalidateQueries({ queryKey: [FULFILMENT_PLANNING_KEY] });
    }
  }, [psoId, freshState, queryClient]);

  return query;
}

/**
 * Re-running writes the links it can prove, so it invalidates the sales-order queries too:
 * the review state is shown on the project's SO list and on the SO detail header, and a
 * stale one there says an order still needs reconciling after it has been cleared.
 */
export function useReconciliationMutations() {
  const queryClient = useQueryClient();

  const rerun = useMutation({
    mutationFn: (psoId: string) => rerunReconciliation(psoId),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: [FULFILMENT_PLANNING_KEY] });
      queryClient.invalidateQueries({ queryKey: [RECONCILIATION_KEY] });
      queryClient.invalidateQueries({ queryKey: [SALES_ORDERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [SALES_ORDER_KEY] });
      const open = summary.exceptions.length;
      if (open === 0) {
        toast.success('Reconciled. This sales order is ready for CS review.');
      } else {
        toast.warning(
          `${open} exception${open === 1 ? '' : 's'} still to clear on this sales order.`,
        );
      }
    },
    onError: (error: Error) => toast.error(error.message),
  });

  /**
   * One press confirms every line (AC-C01). It invalidates the same four key families the
   * re-run does, plus the order inquiry (per project and the cross-project worklist): the
   * confirmed Buy residual is what purchasing is handed, so a stale inquiry list is the one
   * place the decision would look like it had not happened. The pile queue and the stock
   * detail show the holds the confirmation placed, so they go too.
   *
   * The error toast is deliberately short. A refusal names each failing line, and that
   * list belongs on the sheet beside the lines, not in a toast that scrolls away.
   */
  const confirm = useMutation({
    mutationFn: ({ psoId, body }: { psoId: string; body: ConfirmSupplyBody }) =>
      confirmSupply(psoId, body),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: [FULFILMENT_PLANNING_KEY] });
      // The board reads live stock and live holds, so a confirmation changes what every OTHER
      // order on the same board can still be promised. Leaving it stale would show a planner a
      // Reserve that their own previous press had just consumed.
      queryClient.invalidateQueries({ queryKey: [PLANNING_BOARD_KEY] });
      queryClient.invalidateQueries({ queryKey: [RECONCILIATION_KEY] });
      queryClient.invalidateQueries({ queryKey: [SUPPLY_KEY] });
      queryClient.invalidateQueries({ queryKey: [SALES_ORDERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [SALES_ORDER_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_ROWS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [PILE_QUEUE_KEY] });
      queryClient.invalidateQueries({ queryKey: [STOCK_DETAIL_KEY] });
      queryClient.invalidateQueries({ queryKey: [STOCK_TRANSFERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [BOARD_TRANSFERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [PLANNING_CHANGE_BATCH_KEY] });
      const rows = result.inquiry_rows_created;
      // #1362: lines the server skipped because nothing was open on them any more.
      const fulfilled = result.lines_fulfilled_skipped ?? 0;
      const fulfilledText =
        fulfilled > 0
          ? ` ${fulfilled} line${fulfilled === 1 ? '' : 's'} already fulfilled, decision cleared.`
          : '';
      toast.success(
        result.revision_no === null
          ? `Nothing to confirm.${fulfilledText}`
          : `Confirmed as revision ${result.revision_no}. ${rows} purchase row${
              rows === 1 ? '' : 's'
            } handed over.${fulfilledText}`,
      );
      // #1362 (owner, 29 Sep 2026): a Buy kept over goods that landed for its line is
      // confirmed as decided; say it, the way purchasing reads it on the Buy's row.
      for (const notice of result.landed_buy_notices ?? []) {
        toast.warning(failingLineText(notice));
      }
      // Only when something went wrong. The successful count is on the Transfers page and
      // does not need saying twice; an unwritten movement has no other way to be noticed.
      const failed = result.transfers_failed ?? 0;
      if (failed > 0) toast.warning(`Transfers not written: ${failed}`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { rerun, confirm };
}

/**
 * The multi-order board for a selection of sales orders.
 *
 * `enabled` on a non-empty selection: the board is meaningless for nothing, and firing a
 * request for an empty `orders=` would ask the server to aggregate the whole book, which is
 * the one thing section 13.2 says it must never be asked to do.
 *
 * No `placeholderData`: changing the selection or the granularity changes what the grid MEANS,
 * and holding the previous board on screen under a new header would state the old answer as
 * though it were the new one - measured, not theoretical (D16): turning it on made a cell
 * opened moments after a granularity switch close itself, because the click landed on the
 * OLD granularity's cell (same accessible name, same qty, different `bucket_key`) before the
 * new read replaced it, and `liveCell` then found no match for it in the fresh board.
 *
 * A Confirm's own refetch (D16) needs none of this: it is the SAME key, and react-query already
 * keeps the last successful `data` on screen through a same-key refetch with no help from
 * `placeholderData` - only `isFetching` flips, which `FulfilmentBoardPanel` reads to DIM the
 * board rather than blank it (see `boardRefreshing`). A draft save/undo does not even reach
 * this any more: `useLineDraftMutation` patches the cache in place and asks for no refetch at
 * all, which is the actual fix for the flicker the captain called "very choppy" 3 September
 * 2026 - the granularity/window transition above was never the complaint.
 */
export function usePlanningBoard(
  soNumbers: string[],
  granularity: BoardGranularity = 'date',
  previewPolicy: boolean | string = false,
  options: { dayWindow?: string; asOf?: string } = {},
  enabled = true,
) {
  return useQuery({
    // The window is part of the key: scrolling the day view asks for a DIFFERENT board, and
    // sharing a cache entry between two windows would show yesterday's columns under today's
    // header.
    queryKey: [
      PLANNING_BOARD_KEY,
      [...soNumbers].sort().join(','),
      granularity,
      typeof previewPolicy === 'string' ? previewPolicy : previewPolicy ? 'preview' : 'live',
      options.dayWindow ?? '',
      options.asOf ?? '',
    ],
    queryFn: () => getPlanningBoard(soNumbers, granularity, previewPolicy, options),
    enabled: enabled && soNumbers.length > 0,
    retry: 1,
  });
}

/**
 * The whole queue at one pile, on behalf of one line.
 *
 * `enabled` only once the dialog is open and the pile is addressable: a line whose sales order
 * states no location has no pile to queue at, and asking for one would 404 behind a dialog that
 * has nothing to show anyway.
 */
export function usePileQueue(
  productId?: string | null,
  warehouseId?: string | null,
  lineId?: string | null,
  enabled = true,
) {
  return useQuery({
    // The asking line is part of the key: the same pile read on behalf of two different lines
    // marks a different row and answers a different `leading_factor` per row.
    queryKey: [PILE_QUEUE_KEY, productId ?? '', warehouseId ?? '', lineId ?? ''],
    queryFn: () => getPileQueue(productId as string, warehouseId as string, lineId),
    enabled: enabled && Boolean(productId) && Boolean(warehouseId),
    retry: 1,
  });
}

/**
 * The Proof button: the ranked evidence behind one product's hot/cold verdict.
 *
 * `enabled` only once the popover is open - the ranking is a live window-function read over
 * the whole network, and nobody has asked for it until the button is pressed.
 */
export function useClassificationEvidence(productId?: string | null, enabled = true) {
  return useQuery({
    queryKey: [CLASSIFICATION_EVIDENCE_KEY, productId ?? ''],
    queryFn: () => getClassificationEvidence(productId as string),
    enabled: enabled && Boolean(productId),
    retry: 1,
  });
}

/**
 * The documents behind one location row - AutoCount's "Stock Status with Detail".
 *
 * Addressed by ids: two products on the live book share the item code `B2155-NL-BLUE`, so a
 * lookup by code would answer confidently about the wrong one.
 *
 * `lineIds` are the cell's own contributing lines. Their rows come back marked, so a planner
 * can find themselves in a list that is otherwise all other people's documents. Part of the
 * key, because a different asker is a different answer.
 *
 * `group` reads the whole ownership group instead of one bin, which is the pile step 1 of the
 * ladder actually draws.
 */
export function useStockDetail(
  productId: string,
  warehouseId: string | null,
  lineIds: string[] = [],
  /** A whole SET instead of one bin: the group suffix (`IB`), or `pools`. */
  group?: string | null,
  /**
   * Off while a caller is still resolving WHICH bin or group to ask for
   * (`OrderInquiryStockGrid`, a bare pool code resolved to its warehouse id first) - the
   * route 422s when asked for neither, and firing that request every render while nothing
   * is resolved yet would be a failed fetch nobody asked for. Defaults on: every other
   * caller already knows its target before this hook is called.
   */
  enabled = true,
) {
  const key = lineIds.join(',');
  return useQuery({
    queryKey: [STOCK_DETAIL_KEY, productId, group ?? warehouseId, key],
    queryFn: () => getStockDetail(productId, warehouseId, lineIds, group),
    enabled: enabled && Boolean(warehouseId || group),
    retry: 1,
  });
}

/**
 * The Plans page (D1): every supply decision, one row per revision, cross-order.
 *
 * `LIST_QUERY_OPTIONS` for the same reason the worklist spreads it: page and filter changes
 * both change the query key, and without it the grid empties to a skeleton on every one.
 */
export function usePlans(params: PlanListParams = {}) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [PLANS_KEY, params],
    queryFn: () => listPlans(params),
  });
}

/**
 * The board's ONE Confirm (R11/D2): one call, several orders, one result per order.
 *
 * Invalidates the same query families the per-order `confirm` does - the board, the
 * worklist, the plans list itself (a newly confirmed decision belongs there now), the SO
 * detail, the order-inquiry lists a confirmed Buy hands off to, the transfers a confirmed
 * move raises (both the page's list and the board's own panel) and the planning-change batch
 * the press may have applied.
 */
export function useConfirmManyMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: ConfirmManyBody) => confirmMany(body),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: [FULFILMENT_PLANNING_KEY] });
      queryClient.invalidateQueries({ queryKey: [PLANNING_BOARD_KEY] });
      queryClient.invalidateQueries({ queryKey: [PLANS_KEY] });
      queryClient.invalidateQueries({ queryKey: [RECONCILIATION_KEY] });
      queryClient.invalidateQueries({ queryKey: [SUPPLY_KEY] });
      queryClient.invalidateQueries({ queryKey: [SALES_ORDERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [SALES_ORDER_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_ROWS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [PILE_QUEUE_KEY] });
      queryClient.invalidateQueries({ queryKey: [STOCK_DETAIL_KEY] });
      queryClient.invalidateQueries({ queryKey: [STOCK_TRANSFERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [BOARD_TRANSFERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [PLANNING_CHANGE_BATCH_KEY] });
      // NO SUCCESS TOAST HERE (D6). The board says what the press produced in the three
      // numbers a planner acts on - "37 lines confirmed - 1 transfer proposed - 0 inquiry
      // rows" - and a second toast counting ORDERS beside it made the screen shout twice and
      // answer a question nobody asked. Only the partial refusal speaks, because the count of
      // orders that would not go through is not on the panel's sentence.
      const succeeded = result.results.filter((entry) => entry.ok).length;
      const failed = result.results.length - succeeded;
      if (failed > 0) {
        toast.warning(
          `Confirmed ${succeeded} order${succeeded === 1 ? '' : 's'}; ${failed} refused - see the results below.`,
        );
      }
    },
    onError: (error: Error) => {
      // R3 (SO314595): a Confirm whose response never reached this tab has still committed
      // on the server, so the board has to be re-read either way - a refetch is what turns
      // the pill from Saved/Rejected back to Confirmed once the real state arrives.
      queryClient.invalidateQueries({ queryKey: [PLANNING_BOARD_KEY] });
      queryClient.invalidateQueries({ queryKey: [FULFILMENT_PLANNING_KEY] });
      toast.error(error.message);
    },
  });
}

/**
 * Preview of a Confirm: writes nothing, so it invalidates nothing and toasts nothing on
 * success (the panel shows the answer). A failure speaks like every other mutation here.
 */
export function usePreviewConfirmManyMutation() {
  return useMutation({
    mutationFn: (body: ConfirmManyBody) => previewConfirmMany(body),
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

/**
 * Patches ONE line's `draft` inside a cached board - the top-level `contributions` AND every
 * `cells[].contributions` entry that carries the same key, because Confirm-all, the List view
 * and the grid each read a different one of those two (D16).
 *
 * A save/undo never changes the engine's suggestion, so re-running the whole board query for
 * it was waste - and worse than waste: it was the cause of the flicker (the captain, 3
 * September 2026, "very choppy"). This patches the ONE row instead of asking the server for
 * the whole board again.
 *
 * Every UNTOUCHED contribution keeps its EXACT reference (the early `return contribution`
 * below): `useLineDraftMutation`'s two writers are the only callers, and the seeding effect in
 * `FulfilmentBoardPanel` (which reads `contribution.draft` off every one of them on every
 * render) must not see a new object for a row this write did not touch, or an unrelated
 * card re-renders for no reason.
 *
 * Exported and unit-tested on its own (`useFulfilmentPlanning.test.tsx`) rather than folded
 * inline into the mutation - the reference-identity guarantee above is exactly the kind of
 * thing that quietly breaks under a later edit if nothing pins it down.
 */
export function patchContributionDraft<
  T extends { cells: BoardCell[]; contributions: BoardContribution[] },
>(board: T, key: string, draft: BoardLineDraft | null): T {
  const patch = (contribution: BoardContribution): BoardContribution =>
    contribution.key === key ? { ...contribution, draft } : contribution;
  return {
    ...board,
    cells: board.cells.map((cell) => ({ ...cell, contributions: cell.contributions.map(patch) })),
    contributions: board.contributions.map(patch),
  };
}

/**
 * Save decision / Undo (S4, R-F): a line's decision on the SERVER, surviving a reload,
 * another device, another planner.
 *
 * PATCHES the cached board in place (D16) rather than invalidating it - `patchContributionDraft`
 * above, run against every cached `PlanningBoard` this key might be sitting in
 * (`setQueriesData` over the whole `PLANNING_BOARD_KEY` family, since the board is keyed by
 * selection/granularity/window and a line can be showing on more than one open board). Neither
 * write changes a decision's COUNT the way Confirm does (a draft is never `active`), so the
 * worklist, the plans list and the rest of `useConfirmManyMutation`'s long invalidation list
 * have nothing to learn from either one - and now neither does the board query itself, since
 * nothing about the ENGINE's suggestion moved.
 *
 * REWORKED (owner ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`, hand-test
 * feedback: "we should confirm the rejection"): a `rejected` save on a covered line used to
 * reach `uncover_lines` on the server, which needed a matching invalidation here (S3, fix
 * round 3) - that call is gone. Every save, `rejected` included, is a STAGED draft now, same
 * as any other verdict: nothing about the active confirmation moves, so the plain patch below
 * is the whole story again. Confirm is what invalidates the wider list (`useConfirmManyMutation`
 * above), the same press that actually withdraws the line.
 *
 * NO SUCCESS TOAST HERE (D6, matching `useConfirmManyMutation`'s own note): the sentence
 * "Line 3 saved - 4 to confirm" (AC-4.1) needs the FRESH board-wide confirm count, which
 * this hook does not have - only `FulfilmentBoardPanel`'s own `decide()`, which already
 * computed the optimistic local update, can say it without a second, stale-by-one-render
 * toast. `onError` still speaks here, the same as every other mutation on this screen.
 */
export function useLineDraftMutation() {
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: ({
      key,
      decision,
      proposed,
    }: {
      key: string;
      decision: BoardDecision;
      proposed?: BoardSource[];
      // Review round 1, Should fix 1: read only by `onError` below, never by the write
      // itself - `BoardDecideControl`'s own lenient toast (R10) already names a failed row,
      // so a Decide save asks this mutation to stay quiet rather than toasting it a second
      // time.
      silent?: boolean;
    }) => (proposed ? putLineDraft(key, decision, proposed) : putLineDraft(key, decision)),
    onSuccess: (saved, { key }) => {
      queryClient.setQueriesData<PlanningBoard>({ queryKey: [PLANNING_BOARD_KEY] }, (current) =>
        current ? patchContributionDraft(current, key, saved) : current,
      );
    },
    onError: (error: Error, variables) => {
      if (!variables.silent) toast.error(error.message);
    },
  });

  const remove = useMutation({
    mutationFn: (key: string) => deleteLineDraft(key),
    onSuccess: (_result, key) => {
      queryClient.setQueriesData<PlanningBoard>({ queryKey: [PLANNING_BOARD_KEY] }, (current) =>
        current ? patchContributionDraft(current, key, null) : current,
      );
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return {
    /**
     * `proposed` is OPTIONAL and NEVER what staleness is judged against (S1, code review
     * round 3 still holds: the server snapshots the LINE's own facts at save time for
     * that) - see `putLineDraft`'s own note. It exists so `FulfilmentBoardPanel.decide()`
     * can pass the contribution's own `sources` (D12, #573), which the Sales Order page
     * reads back on a saved-but-unconfirmed line. The SAVER comes off the caller's own
     * JWT and is never sent.
     */
    save: (
      key: string,
      decision: BoardDecision,
      proposed?: BoardSource[],
      options?: { silent?: boolean },
    ): Promise<BoardLineDraft> =>
      save.mutateAsync({ key, decision, proposed, silent: options?.silent }),
    remove: (key: string): Promise<void> => remove.mutateAsync(key),
  };
}
