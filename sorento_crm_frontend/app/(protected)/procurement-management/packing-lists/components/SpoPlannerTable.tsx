'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  ExpandedState,
  Row,
  getCoreRowModel,
  getExpandedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import {
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Download,
  Info,
  LayoutGrid,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
  Table2,
  Trash2,
  X,
} from 'lucide-react';
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
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import {
  DrillTable,
  OnHandTable,
  PlanRowDialog,
  PoTakesPicker,
  RIGHT,
  SoCoveragePicker,
  SpoTabs,
  type PlanRowDialogKind,
} from '@/app/(protected)/scm/components/PlanRowDialog';
import { PlanNumberButton } from '@/app/(protected)/scm/components/PlanNumberButton';
import {
  useCreateSpo,
  useDeleteSpo,
  useDownloadSpoWorksheet,
  useReviseSpo,
  useSpoPlannerState,
  useSpoSuggestion,
} from '@/app/(protected)/scm/hooks/useFulfilment';
import { purchaseOrderStatusPill } from '@/app/(protected)/scm/lib/purchaseOrderStatus';
import { formatDateInMalaysia } from '@/lib/helpers';
import { spoDetailHref } from '@/lib/spo-detail';
import type {
  SpoConfirmLine,
  SpoCoverageLine,
  SpoDemandLine,
  SpoLocationOption,
  SpoLocationSplit,
  SpoPoTake,
  SpoRef,
  SpoSuggestionLine,
} from '@/app/(protected)/scm/services/fulfilmentService';
import {
  cascadeTake,
  buildSpoScheduleMatrix,
  bucketKeyFor,
  type SpoMatrixBucket,
  type SpoMatrixEntry,
} from './spoScheduleMatrix';
import { SpoScheduleMatrixTable } from './SpoScheduleMatrixTable';

/**
 * The packing-list-to-SPO planner. One row per PACKED product, the loading-plan-style ranked
 * TABLE `ContainerRequestSection` renders for the container request, not a checkbox list. No
 * checkbox column, on purpose, mirroring `ContainerRequestSection.renderQtyCell`'s own rule:
 * the SPO-qty input IS the include/exclude decision, edited to 0 drops the line off the
 * confirm without hiding the row.
 *
 * DOCTRINE CORRECTION (captain, 21 Aug): "when there is PO, then we only can do SPO... it is
 * when we got PO, then we only can pull from the PO to form SPO." `suggested_qty` is
 * `po_covered_qty` itself - what an open PO PULLS this SPO up to. On hand / incoming SPO are
 * CONTEXT columns only - they never feed the qty. See `fulfilmentService.ts`'s own contract
 * doc for the full story.
 *
 * R2 (captain's ruling, 3 Sep): the PO cap is REMOVED. `po_covered_qty` / `suggested_qty` is
 * only the DEFAULT the qty input starts at now, never a ceiling - the input takes any whole
 * number, no live clamp. `cannot_convert` (input disabled) is true ONLY for a line with no
 * supplier; a line with a supplier and no open PO is fully editable, simply unbacked. Nothing
 * about the quantity - past packed, past what a PO covers, an over-tick - blocks Create SPO.
 * The server caps every line at its own remainder and writes the uncovered part without a PO
 * pull, so there is nothing to confirm: Create SPO creates on the click (captain, 4 Sep - the
 * "Review before creating" dialog this used to open is gone, no replacement dialog either).
 *
 * Four asks from the same doctrine-correction message, all on this table:
 *   1. The PO-takes drill names the PO's own date and supplier, and opens as the shared
 *      lightbox (`PlanRowDialog kind="po_takes"`, R21).
 *   2. A "SO covered" drill answers "what SO am I covering", in the same lightbox.
 *   3. Table/Schedule toggle, two schedule views (PO coverage, SO coverage) - product rows x
 *      weekly buckets, the loading plan's own visual precedent.
 *   4. The location control is a per-line SPLIT editor (`LocationSplitPanel`) - a line's SPO
 *      can land at more than one warehouse, each split writing its own allocation. It lives in
 *      the row's EXPANDED area (R22, part 4): a list of editable rows does not fit a cell, and
 *      a popover put it behind a click, clipped at the viewport edge, away from its own row.
 */

const EM_DASH = '-';
const intFmt = new Intl.NumberFormat('en-MY', { maximumFractionDigits: 0 });
function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH;
  return intFmt.format(value);
}
function fmtSigned(value: number): string {
  const s = intFmt.format(Math.abs(value));
  return value < 0 ? `-${s}` : s;
}

/** `qtyText` is set while the box holds something that is not a usable quantity (blank or
 *  negative): the figure then counts as 0 on screen, the box keeps what was typed, and the
 *  buttons that send refuse it with a message. */
type SplitState = { warehouseId: string; qty: number; qtyText?: string };
type LineState = {
  qty: number;
  /** See `SplitState.qtyText`. */
  qtyText?: string;
  /** What the operator TYPED, held apart from what is currently sendable. Null until they
   *  type one, and the quantity is then a derived default that follows the ticked takes.
   *  Kept separate because the sendable figure is clamped to what is ticked, and writing
   *  that clamp back over the typed one is how unticking the only take stored 0 and the
   *  re-tick had nothing left to give back. */
  typedQty: number | null;
  splits: SplitState[];
  /** Which PO takes this line draws from. Every one, until somebody unticks one (AC-G1). */
  poTakeIds: string[];
  /** Which demand this SPO is pointed at - `SpoCoverageLine.key`s (Q4, AC-G3). */
  soKeys: string[];
  /** The user's own Take number, by `key` (R19, AC-I2) - present only for a row she typed
   *  into; every other ticked row still follows the cascade default. Editing a row clears
   *  every override AFTER it in the coverage list's own order ("re-runs the remainder"),
   *  never the ones before it - see `setSoTake`. */
  soTakeOverrides: Record<string, number>;
  /** The order the SO-covered picker is currently SHOWING this line's rows in (S7, review
   *  round 1) - project tab then retail tab, each in its own active sort. The Take cascade
   *  walks this, so "the rows after the one she edited" means the rows after it on screen.
   *  Empty until the picker has been opened, and the server's own walk stands until then. */
  soOrder: string[];
};
type ScheduleView = 'po' | 'so';

/** The sentence for a quantity box that holds something unusable; nothing for a good one. */
function quantityMessage(text: string | undefined): string | undefined {
  return text === undefined ? undefined : numberFieldError(text, { min: 0 });
}

/** A `po_takes` row occupied by ANOTHER SPO entirely (S5) - `qty: 0` is never this
 *  cascade's own take, so the row can never be ticked. */
function isTakenPo(t: SpoPoTake): boolean {
  return t.qty === 0 && t.taken_qty > 0;
}

/** Every `po_line_id` this line's take set could TICK - a taken row is never one of them
 *  (S5). The initial-tick default (every take, AC-G1) and a re-tick from the picker both
 *  read through this so neither can hand a taken row's id back into state. */
function tickablePoTakeIds(ln: SpoSuggestionLine): string[] {
  return ln.po_takes.filter((t) => !isTakenPo(t)).map((t) => t.po_line_id);
}

/** A `so_coverage` row occupied by ANOTHER SPO entirely (S5) - `qty: 0` is never tickable
 *  here either; `default_ticked` is already `false` for it server-side, so this only guards
 *  a re-tick handed back by the picker. */
function isTakenCoverage(c: SpoCoverageLine): boolean {
  return c.qty === 0 && c.taken_qty > 0;
}

/**
 * What the ticked takes cover - the ceiling on this line's SPO quantity (AC-G2).
 *
 * The cascade RE-RUN over the ticked lines, not the sum of the slices it took while every
 * line was ticked: with 100 packed against a 60-open PO and a 150-open one, the walk takes
 * 60 and 40, and unticking the first must ask the second for the whole 100 - it has it.
 * Summing the remaining slice answered 40 and quietly lost 60 pieces of cover that exist.
 * `open_qty` is what makes that computable here; the server does the same walk on Create.
 */
function poCoveredFor(ln: SpoSuggestionLine, takeIds: string[]): number {
  const available = ln.po_takes
    .filter((t) => takeIds.includes(t.po_line_id))
    .reduce((sum, t) => sum + (t.open_qty ?? t.qty), 0);
  // Capped at what is actually LEFT to convert on this line (R1), not the raw packed
  // figure - a prior `create` run already claimed the rest.
  return Math.min(available, ln.remaining_qty);
}

/**
 * What the ticked demand actually GETS from this SPO - the one walk every figure on the
 * row is read off (browser pass 2, finding 3).
 *
 * There used to be two: the cell showed `min(sum of ticked need, qty)` and the banner
 * compared the raw `sum of ticked need` against the packed quantity. On a container packed
 * with 100 whose POs cover 19, the cell read 19 and the banner read "302 ticked against 100
 * packed" and disabled Create SPO - two answers to one question, and the wrong one was the
 * one holding the button.
 *
 * The walk: each ticked entry, in the server's own order, takes what it needs out of the
 * SPO quantity until there is none left. An entry that gets nothing is one this container
 * cannot serve at all, which is the only thing worth stopping the operator for.
 */
function coverageTakes(
  ln: SpoSuggestionLine,
  soKeys: string[],
  qty: number,
): { entry: SpoCoverageLine; take: number }[] {
  const out: { entry: SpoCoverageLine; take: number }[] = [];
  let left = Math.max(qty, 0);
  for (const entry of ln.so_coverage ?? []) {
    if (!soKeys.includes(entry.key)) continue;
    const take = Math.min(entry.qty, left);
    out.push({ entry, take: Math.max(take, 0) });
    left -= Math.max(take, 0);
  }
  return out;
}

/**
 * `coverageTakes`, with the operator's own edits layered on top (R19, AC-I2).
 *
 * A ticked row with no override still follows the waterfall (`coverageTakes`'s own walk,
 * against whatever the ROW BEFORE it left over); a row the operator typed a number into
 * takes exactly that instead, whether or not it fits. This is why `setSoTake` clears every
 * override AFTER the row she just edited (`resolvedSoTakes` never bounds the OVERRIDE
 * itself, only the rows still following the cascade) - a stale override further down the
 * list would otherwise disagree with a total that just changed above it.
 */
function orderedCoverage(ln: SpoSuggestionLine, order?: string[]): SpoCoverageLine[] {
  const rows = ln.so_coverage ?? [];
  if (!order?.length) return rows;
  const rank = new Map(order.map((key, i) => [key, i] as const));
  return rows
    .map((entry, i) => ({ entry, i }))
    .sort((x, y) => {
      // A row the picker is not currently showing (filtered out of both tabs) keeps the
      // server's own place, at the end - it is still ticked, and dropping it from the walk
      // would change what a search box is allowed to do.
      const rx = rank.get(x.entry.key) ?? Number.MAX_SAFE_INTEGER;
      const ry = rank.get(y.entry.key) ?? Number.MAX_SAFE_INTEGER;
      return rx - ry || x.i - y.i;
    })
    .map((x) => x.entry);
}

function resolvedSoTakes(
  ln: SpoSuggestionLine,
  soKeys: string[],
  overrides: Record<string, number>,
  qty: number,
  order?: string[],
): { entry: SpoCoverageLine; take: number }[] {
  const out: { entry: SpoCoverageLine; take: number }[] = [];
  let left = Math.max(qty, 0);
  for (const entry of orderedCoverage(ln, order)) {
    if (!soKeys.includes(entry.key)) continue;
    const override = overrides[entry.key];
    const take = override !== undefined ? override : Math.max(Math.min(entry.qty, left), 0);
    out.push({ entry, take });
    left -= take;
  }
  return out;
}

/** What the ticks cover between them - what the SO-covered cell states. */
function tickedQty(
  ln: SpoSuggestionLine,
  soKeys: string[],
  overrides: Record<string, number>,
  qty: number,
  order?: string[],
): number {
  return resolvedSoTakes(ln, soKeys, overrides, qty, order).reduce((sum, t) => sum + t.take, 0);
}

/**
 * The ticks the server proposed, narrowed to what this SPO can reach.
 *
 * The server walks its list against the PACKED quantity; the SPO is capped at what open
 * POs cover, which is often less. Ticking an order the container cannot serve on the very
 * first render is how the banner fired on a screen nobody had touched.
 */
function defaultSoKeys(ln: SpoSuggestionLine, qty: number): string[] {
  const proposed = (ln.so_coverage ?? []).filter((c) => c.default_ticked).map((c) => c.key);
  return coverageTakes(ln, proposed, qty)
    .filter((t) => t.take > 0)
    .map((t) => t.entry.key);
}

/**
 * Where the quantity lands, derived from the same walk (AC-G4).
 *
 * Each ticked piece of demand pulls what it needs, at ITS warehouse. What no tick claims is
 * free stock, and it goes to the suggested warehouse - labelled Unassigned on screen,
 * because nobody has said who it is for and pretending otherwise is how a container arrives
 * against the wrong order.
 */
function splitsFromTicks(
  ln: SpoSuggestionLine,
  soKeys: string[],
  overrides: Record<string, number>,
  qty: number,
  order?: string[],
): SplitState[] {
  if (ln.cannot_convert || qty <= 0) return [];
  const byWarehouse = new Map<string, number>();
  let claimed = 0;
  for (const { entry, take } of resolvedSoTakes(ln, soKeys, overrides, qty, order)) {
    if (take <= 0) continue;
    const warehouse = entry.warehouse_id ?? ln.suggested_warehouse_id;
    if (!warehouse) continue;
    byWarehouse.set(warehouse, (byWarehouse.get(warehouse) ?? 0) + take);
    claimed += take;
  }
  const unassigned = qty - claimed;
  if (unassigned > 0 && ln.suggested_warehouse_id) {
    byWarehouse.set(
      ln.suggested_warehouse_id,
      (byWarehouse.get(ln.suggested_warehouse_id) ?? 0) + unassigned,
    );
  }
  return [...byWarehouse.entries()].map(([warehouseId, splitQty]) => ({
    warehouseId,
    qty: splitQty,
  }));
}

function splitsTotal(splits: SplitState[]): number {
  return splits.reduce((sum, s) => sum + (s.qty || 0), 0);
}

export function SpoPlannerTable({
  shipmentId,
  initialEditPurchaseOrderId = null,
}: {
  shipmentId: string;
  /** `?edit=<purchase_order_id>` on the URL (R24, AC-K5) - how the SPO document's own
   *  "Edit in planner" action lands here already in edit mode. Read in the page, not here,
   *  so this component stays free of `useSearchParams` and testable on its own. */
  initialEditPurchaseOrderId?: string | null;
}) {
  const suggestion = useSpoSuggestion(shipmentId);
  const create = useCreateSpo(shipmentId);
  const deleteSpo = useDeleteSpo(shipmentId);
  const worksheet = useDownloadSpoWorksheet(shipmentId);
  /**
   * Which SPO is being EDITED (R24, AC-K1), or null in the ordinary create-mode planner.
   *
   * One flag, not a second component: every cell, every walk and the whole confirm payload
   * are the same in both modes - the difference is only WHICH lines the grid holds and what
   * seeds their state. A parallel edit table would be the same 600 lines with one field
   * changed, and the two would drift the first time a cell was fixed in only one of them.
   */
  const [editPoId, setEditPoId] = useState<string | null>(initialEditPurchaseOrderId ?? null);
  const plannerState = useSpoPlannerState(shipmentId, editPoId);
  const revise = useReviseSpo(shipmentId, editPoId);
  /** Which row of the Created SPOs grid Delete was pressed on (R1: one row per SPO, not one
   *  confirm for the whole shipment). */
  const [deleteTarget, setDeleteTarget] = useState<SpoRef | null>(null);
  const [view, setView] = useState<'table' | 'schedule'>('table');
  const [scheduleView, setScheduleView] = useState<ScheduleView>('po');

  const [state, setState] = useState<Record<string, LineState>>({});
  /**
   * The live per-line state, as the grid CELLS read it.
   *
   * `columns` below has to keep a STABLE identity: `flexRender` renders a column's `cell`
   * function as a React component TYPE (`createElement(columnDef.cell, ctx)`), so a columns
   * array rebuilt on every keystroke hands React a new type for every cell - which unmounts
   * the SPO qty input mid-word and takes the caret with it (captain, live, 4 Sep: the
   * operator had to click back into the field for each digit). The cells therefore close
   * over this ref rather than over `state`, and every helper they call bottoms out at
   * `stateFor`, which reads it - so the closure the columns memo captured on its FIRST
   * render still gives the current answer on every later one.
   */
  const stateRef = useRef(state);
  stateRef.current = state;
  /** A send (Create SPO / Save changes) was refused on a quantity box: from then on the boxes
   *  show their sentence, live, until every figure is usable. Read by the cells through a ref
   *  for the same reason as `stateRef`. */
  const [qtyRefused, setQtyRefused] = useState(false);
  const qtyRefusedRef = useRef(qtyRefused);
  qtyRefusedRef.current = qtyRefused;
  /** Which lines have their destinations open (R22). Closed to start with: the table is a
   *  ranked reading first, and every row open turns it into a form. */
  const [expanded, setExpanded] = useState<ExpandedState>({});
  /** The ONE lightbox this grid opens (R21): which figure was clicked, and on which line.
   *  Four dialogs mounted per row would be four Radix roots per row; the grid holds one and
   *  the cells only say what to put in it. */
  const [dialog, setDialog] = useState<
    { kind: PlanRowDialogKind; line: SpoSuggestionLine; bucket?: SpoMatrixBucket } | null
  >(null);

  useEffect(() => {
    // EDIT MODE (R24, AC-K1): the row is seeded from what was PERSISTED, never from the
    // suggestion walk - the operator is looking at what this SPO already is, and a default
    // recomputed over it would silently propose a different SPO the moment the screen opened.
    if (!editPoId) return;
    const next: Record<string, LineState> = {};
    for (const ln of plannerState.data?.lines ?? []) {
      next[ln.shipment_line_id] = {
        qty: ln.spo_qty,
        // Typed, so unticking a PO take can never pull the SPO's own persisted quantity
        // down underneath the operator - the figure on file is a decision somebody made.
        typedQty: ln.spo_qty,
        poTakeIds: ln.po_take_ids,
        soKeys: ln.so_takes.map((t) => t.key),
        soTakeOverrides: Object.fromEntries(ln.so_takes.map((t) => [t.key, t.qty])),
        soOrder: [],
        splits: ln.location_splits
          .filter((s) => s.warehouse_id)
          .map((s) => ({ warehouseId: s.warehouse_id, qty: s.qty })),
      };
    }
    setState(next);
  }, [plannerState.data, editPoId]);

  useEffect(() => {
    // A fresh suggestion replaces whatever she had edited - "refresh" looks again, it does
    // not keep edits made against a now-stale suggestion (same rule ContainerRequestSection
    // applies to its own qty overrides).
    if (editPoId) return; // edit mode seeds off `plannerState` above, never the suggestion
    const next: Record<string, LineState> = {};
    for (const ln of suggestion.data?.lines ?? []) {
      // Every take ticked and the server's own demand walk pre-ticked: the default IS the
      // answer for most containers, and the ticks exist for the ones where it is not - a
      // TAKEN row (S5) is never one of them, no matter how it landed on `po_takes`.
      const poTakeIds = tickablePoTakeIds(ln);
      const soKeys = defaultSoKeys(ln, ln.suggested_qty);
      next[ln.shipment_line_id] = {
        qty: ln.suggested_qty,
        typedQty: null,
        poTakeIds,
        soKeys,
        soTakeOverrides: {},
        soOrder: [],
        splits: splitsFromTicks(ln, soKeys, {}, ln.suggested_qty),
      };
    }
    setState(next);
  }, [suggestion.data, editPoId]);

  /** The grid's rows: the shipment's REMAINDER in create mode, and ONLY the SPO being
   *  edited in edit mode (R24, AC-K1). */
  const lines: SpoSuggestionLine[] = useMemo(
    () => (editPoId ? plannerState.data?.lines ?? [] : suggestion.data?.lines ?? []),
    [editPoId, plannerState.data, suggestion.data],
  );
  // R1: every SPO this shipment has ever produced, oldest first - always populated, never
  // gating the planner below it (a container is routinely converted in more than one pass).
  const existingSpos = useMemo(() => suggestion.data?.existing_spos ?? [], [suggestion.data]);

  /** What each line of the SPO being edited has already RECEIVED (AC-K3), by shipment line.
   *  Empty in create mode - nothing has arrived against an SPO that does not exist yet. */
  const receivedByLine = useMemo(() => {
    const out: Record<string, number> = {};
    if (!editPoId) return out;
    for (const ln of plannerState.data?.lines ?? []) out[ln.shipment_line_id] = ln.received_qty;
    return out;
  }, [editPoId, plannerState.data]);
  /** Read inside the identity-stable `columns` memo - see `stateRef` for why cells cannot
   *  close over state directly. */
  const receivedRef = useRef(receivedByLine);
  receivedRef.current = receivedByLine;
  const editingRef = useRef(!!editPoId);
  editingRef.current = !!editPoId;

  const stateFor = (ln: SpoSuggestionLine): LineState => {
    const held = stateRef.current[ln.shipment_line_id];
    if (held) return held;
    const soKeys = defaultSoKeys(ln, ln.suggested_qty);
    return {
      qty: ln.suggested_qty,
      typedQty: null,
      poTakeIds: tickablePoTakeIds(ln),
      soKeys,
      soTakeOverrides: {},
      soOrder: [],
      splits: splitsFromTicks(ln, soKeys, {}, ln.suggested_qty),
    };
  };
  const qtyFor = (ln: SpoSuggestionLine) => stateFor(ln).qty;
  const splitsFor = (ln: SpoSuggestionLine) => stateFor(ln).splits;
  const takeIdsFor = (ln: SpoSuggestionLine) => stateFor(ln).poTakeIds;
  const soKeysFor = (ln: SpoSuggestionLine) => stateFor(ln).soKeys;
  const soTakeOverridesFor = (ln: SpoSuggestionLine) => stateFor(ln).soTakeOverrides;
  /** The order the picker is showing this line in (S7) - the cascade's walk order. */
  const soOrderFor = (ln: SpoSuggestionLine) => stateFor(ln).soOrder;

  const patch = (ln: SpoSuggestionLine, next: Partial<LineState>) =>
    setState((prev) => ({
      ...prev,
      [ln.shipment_line_id]: { ...stateFor(ln), ...next },
    }));

  const setQty = (ln: SpoSuggestionLine, qty: number, qtyText?: string) => {
    const current = stateFor(ln);
    // The split follows the quantity, because it is derived from the ticks against it -
    // leaving a stale split behind is how Create SPO refuses with "the split has to add up"
    // on a screen where nothing looks wrong. The SO-take overrides are cleared too: they
    // were typed against the OLD total, and re-running the cascade fresh against the new
    // one is less surprising than a stale number nobody chose for this total.
    patch(ln, {
      qty,
      qtyText,
      typedQty: qty,
      soTakeOverrides: {},
      splits: splitsFromTicks(ln, current.soKeys, {}, qty, current.soOrder),
    });
  };

  const setSplits = (ln: SpoSuggestionLine, splits: SplitState[]) => patch(ln, { splits });

  /**
   * Untick a take and the SPO falls to what the rest of them cover; tick it back and it
   * returns (AC-G2).
   *
   * ONE recompute for both directions. It used to take `min(current qty, covered)`, which
   * cannot go back up: unticking lowered the quantity, and re-ticking then held it at the
   * lowered figure while the PO-covers cell alone recovered - so the SPO qty, the SO
   * covered, the split and the Create gate all sat at the unticked values until a reload.
   *
   * A quantity the operator TYPED is theirs and survives, clamped to what is ticked. One
   * they never touched is a derived default and follows the takes.
   */
  const setTakeIds = (ln: SpoSuggestionLine, poTakeIds: string[]) => {
    // A taken row's checkbox is disabled in the picker, but its id is never trusted from the
    // caller either way (S5) - the same defensiveness `_keep` uses server-side.
    const tickable = new Set(tickablePoTakeIds(ln));
    const nextIds = poTakeIds.filter((id) => tickable.has(id));
    const current = stateFor(ln);
    const covered = poCoveredFor(ln, nextIds);
    // The PO cap is removed (captain's ruling, 3 Sep, R2): a typed quantity is the buyer's
    // own decision and is never clamped down to what the ticked takes cover - the SPO line
    // simply pulls less and writes the rest without a pull. Only an UNTYPED (still default)
    // quantity follows the takes.
    const qty = current.typedQty === null ? covered : current.typedQty;
    // The PO takes changing moves the total (`qty`) underneath the SO-take overrides, the
    // same reason `setQty` clears them - a number typed against the old total has nothing
    // to say about the new one.
    patch(ln, {
      poTakeIds: nextIds,
      qty,
      soKeys: current.soKeys,
      soTakeOverrides: {},
      splits: splitsFromTicks(ln, current.soKeys, {}, qty, current.soOrder),
    });
  };

  /** Every override at or after this key's position in the order the picker is SHOWING
   *  (S7, review round 1) - cleared by `setSoKeys`/`setSoTake`, both of which are "a
   *  change AT this row", per R19's "re-runs the remainder for rows after it" (the row
   *  itself never keeps a STALE override once it has just been ticked, unticked or
   *  retyped). Walks the same order the cascade does, or the two disagree about which
   *  rows come "after" the edited one. */
  function clearOverridesFrom(
    ln: SpoSuggestionLine,
    key: string,
    overrides: Record<string, number>,
  ): Record<string, number> {
    const coverage = orderedCoverage(ln, soOrderFor(ln));
    const idx = coverage.findIndex((c) => c.key === key);
    if (idx < 0) return overrides;
    const next = { ...overrides };
    for (let i = idx; i < coverage.length; i++) delete next[coverage[i].key];
    return next;
  }

  /** Tick the demand this SPO is for; the split follows (AC-G3, AC-G4). */
  const setSoKeys = (ln: SpoSuggestionLine, soKeys: string[]) => {
    // Same defensiveness as `setTakeIds` (S5): a taken row's key is never trusted, however
    // it arrived.
    const takenKeys = new Set(
      (ln.so_coverage ?? []).filter(isTakenCoverage).map((c) => c.key),
    );
    const nextKeys = soKeys.filter((key) => !takenKeys.has(key));
    const current = stateFor(ln);
    // The picker toggles exactly one row per call - its own key is the one whose override
    // (if unticking) or whose seeded default (if ticking) must not survive the change, and
    // R19 asks the SAME of every row after it.
    const changedKey =
      nextKeys.find((key) => !current.soKeys.includes(key)) ??
      current.soKeys.find((key) => !nextKeys.includes(key));
    const nextOverrides = changedKey
      ? clearOverridesFrom(ln, changedKey, current.soTakeOverrides)
      : current.soTakeOverrides;
    patch(ln, {
      soKeys: nextKeys,
      soTakeOverrides: nextOverrides,
      splits: splitsFromTicks(ln, nextKeys, nextOverrides, current.qty, current.soOrder),
    });
  };

  /**
   * The operator types a Take number (R19, AC-I2/AC-I3): the row she edited keeps exactly
   * that number, and every row AFTER it in the coverage list's own order re-runs the
   * cascade against whatever is left - never the rows before it, which already answered
   * for a total this edit does not change.
   */
  const setSoTake = (ln: SpoSuggestionLine, key: string, qty: number) => {
    const current = stateFor(ln);
    const nextOverrides = clearOverridesFrom(ln, key, current.soTakeOverrides);
    nextOverrides[key] = Math.max(qty, 0);
    patch(ln, {
      soTakeOverrides: nextOverrides,
      splits: splitsFromTicks(ln, current.soKeys, nextOverrides, current.qty, current.soOrder),
    });
  };

  /** The picker reports what it is SHOWING (S7): the cascade follows the screen. */
  const setSoOrder = (ln: SpoSuggestionLine, soOrder: string[]) => {
    if (soOrder.join('|') === stateFor(ln).soOrder.join('|')) return;
    patch(ln, { soOrder });
  };

  const confirmLines: SpoConfirmLine[] = useMemo(
    () =>
      lines.map((ln) => {
        const qty = Math.max(qtyFor(ln), 0);
        const include = !ln.cannot_convert && qty > 0;
        const splits = include ? splitsFor(ln).filter((s) => s.warehouseId && s.qty > 0) : [];
        const location_splits: SpoLocationSplit[] = splits.map((s) => ({
          warehouse_id: s.warehouseId,
          qty: s.qty,
        }));
        // R19: every TICKED key, at the take `resolvedSoTakes` gives it - the operator's
        // own override where she typed one, the cascade default everywhere else.
        const so_takes = resolvedSoTakes(
          ln, soKeysFor(ln), soTakeOverridesFor(ln), qty, soOrderFor(ln),
        ).map(
          (t) => ({ key: t.entry.key, qty: t.take }),
        );
        return {
          shipment_line_id: ln.shipment_line_id,
          qty: include ? qty : 0,
          include,
          location_splits,
          po_take_ids: takeIdsFor(ln),
          so_takes,
        };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lines, state],
  );
  const includedCount = confirmLines.filter((l) => l.include).length;
  /** Any quantity box holding a blank or negative figure. Sending is refused until fixed. */
  const hasUnusableQty = lines.some((ln) => {
    const st = stateFor(ln);
    return !ln.cannot_convert && (st.qtyText !== undefined || st.splits.some((sp) => sp.qtyText !== undefined));
  });
  const sendIfUsable = (send: () => void) => {
    // A refused send is what makes the boxes tell their sentence; nothing is said while typing.
    if (hasUnusableQty) {
      setQtyRefused(true);
      return;
    }
    setQtyRefused(false);
    send();
  };

  // A split must sum to exactly the line's SPO qty (the backend's own hard rule, mirroring
  // `allocation_suggestion_service.approve`) - checked here too so Create SPO is disabled
  // before the round-trip rather than after a 422.
  const splitMismatch = useMemo(() => {
    const bad = new Set<string>();
    for (const ln of lines) {
      const item = confirmLines.find((c) => c.shipment_line_id === ln.shipment_line_id);
      if (!item?.include) continue;
      const splits = item.location_splits ?? [];
      if (splits.length === 0) continue; // no location chosen - allowed, just no allocation
      const total = splits.reduce((sum, s) => sum + s.qty, 0);
      if (Math.abs(total - item.qty) > 1e-6) bad.add(ln.shipment_line_id);
    }
    return bad;
  }, [lines, confirmLines]);
  /** Read by the Location cell and its expanded panel, which live inside the identity-stable
   *  `columns` memo - see `stateRef`. */
  const splitMismatchRef = useRef(splitMismatch);
  splitMismatchRef.current = splitMismatch;

  // AC-I2: a Take above its own row's outstanding, or the total above the SPO qty, disables
  // Create SPO exactly like `splitMismatch` does - checked here so the round-trip never has
  // to refuse what the screen could have said itself.
  const soTakeMismatch = useMemo(() => {
    const bad = new Set<string>();
    for (const ln of lines) {
      if (!(ln.so_coverage ?? []).length) continue;
      const takes = resolvedSoTakes(
        ln, soKeysFor(ln), soTakeOverridesFor(ln), qtyFor(ln), soOrderFor(ln),
      );
      const total = takes.reduce((sum, t) => sum + t.take, 0);
      const overRow = takes.some((t) => t.take > t.entry.qty);
      if (overRow || total > qtyFor(ln)) bad.add(ln.shipment_line_id);
    }
    return bad;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines, state]);

  /**
   * B4 (review round 1): a line ticked against sales orders with no location chosen. Every
   * link the confirm writes hangs off an allocation, and a line with no split writes none -
   * so the server refuses it (422), and the screen says so first, in the same words.
   */
  const takesWithoutLocation = useMemo(() => {
    const bad = new Set<string>();
    for (const item of confirmLines) {
      if (!item.include) continue;
      if (!(item.so_takes ?? []).some((t) => t.qty > 0)) continue;
      if ((item.location_splits ?? []).some((s) => s.qty > 0)) continue;
      bad.add(item.shipment_line_id);
    }
    return bad;
  }, [confirmLines]);

  /**
   * A line of the SPO being edited whose new quantity is below what its allocations have
   * already RECEIVED (AC-K3) - the one thing the save cannot do, so it is stated here as
   * well as refused server-side, rather than only after the round trip.
   *
   * Only in edit mode: `receivedByLine` is empty otherwise, because nothing has arrived
   * against an SPO that has not been created yet.
   */
  const receivedMismatch = useMemo(() => {
    const bad = new Set<string>();
    for (const ln of lines) {
      const received = receivedByLine[ln.shipment_line_id] ?? 0;
      if (received > 0 && qtyFor(ln) < received - 1e-6) bad.add(ln.shipment_line_id);
    }
    return bad;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines, receivedByLine, state]);

  const renderQtyCell = (ln: SpoSuggestionLine) => {
    const received = receivedRef.current[ln.shipment_line_id] ?? 0;
    const short = received > 0 && qtyFor(ln) < received - 1e-6;
    return (
      <div className="flex min-w-0 flex-col gap-0.5">
        {renderQtyInput(ln, short)}
        {received > 0 ? (
          <span
            className={short ? 'text-2xs font-medium text-destructive' : 'text-2xs text-muted-foreground'}
          >
            {fmtInt(received)} received
          </span>
        ) : null}
      </div>
    );
  };

  const renderQtyInput = (ln: SpoSuggestionLine, short: boolean) => (
    <NumberInput
      min={0}
      step={1}
      className={
        short
          ? 'h-8 w-24 border-destructive tabular-nums focus-visible:ring-destructive/40'
          : 'h-8 w-24 tabular-nums'
      }
      value={stateFor(ln).qtyText ?? qtyFor(ln)}
      error={qtyRefusedRef.current ? quantityMessage(stateFor(ln).qtyText) : undefined}
      disabled={ln.cannot_convert}
      aria-label="SPO qty"
      // A test hook, not a live tooltip (the `title` this used to carry was the explanatory
      // clamp text R2 removed) - the drills open a MODAL that marks the rest of the grid
      // `aria-hidden`, which takes this input out of the accessibility tree `getByRole`
      // reads, so a test asserting the figure WHILE a drill is open needs a query that does
      // not depend on that tree.
      data-testid="spo-qty-input"
      onChange={(e) => {
        const text = e.target.value;
        const typed = parseNumberDraft(text);
        // A usable figure is kept exactly; a blank or negative one is held as typed.
        if (typed !== null && typed >= 0) setQty(ln, typed);
        else setQty(ln, 0, text);
      }}
    />
  );

  /**
   * How much of this line's quantity no tick claims (AC-G4, Q4).
   *
   * It rides at the suggested warehouse, which is often the SAME warehouse a ticked order
   * needs - so the split reads "BRW 70" whether 40 of it is spoken for or none of it is,
   * and unticking an order moved no number on the screen at all. The figure has to be
   * stated, because it is the difference between goods promised to an order and free stock.
   */
  const unassignedFor = (ln: SpoSuggestionLine) =>
    Math.max(
      qtyFor(ln) -
        tickedQty(ln, soKeysFor(ln), soTakeOverridesFor(ln), qtyFor(ln), soOrderFor(ln)),
      0,
    );

  /**
   * What goes INSIDE the one lightbox, by which figure was clicked (R21).
   *
   * The two pickers are controlled and fetch-free: this table already holds `po_takes` and
   * `so_coverage`, and owns the walk that re-reads them when a tick changes, so the dialog
   * cannot hold a second opinion about the same rows. On hand and Incoming SPO are the
   * SHARED reorder / loading-plan bodies, which fetch their own rows on open.
   */
  const dialogBody = (kind: PlanRowDialogKind, ln: SpoSuggestionLine, bucket?: SpoMatrixBucket) => {
    switch (kind) {
      case 'po_takes': {
        // S4, AC-D3: opened from a schedule cell, the rows whose OWN date fell in that
        // clicked week are marked - the same week function the matrix bucketed them into,
        // so the picker cannot disagree with the cell that opened it.
        const bucketHits = bucket
          ? new Set(
              ln.po_takes
                .filter((t) => bucketKeyFor(t.expected_date) === bucket.key)
                .map((t) => t.po_line_id),
            )
          : undefined;
        return (
          <PoTakesPicker
            takes={ln.po_takes}
            tickedIds={takeIdsFor(ln)}
            onChange={(ids) => setTakeIds(ln, ids)}
            coveredQty={poCoveredFor(ln, takeIdsFor(ln))}
            packedQty={ln.packed_qty}
            bucketHits={bucketHits}
          />
        );
      }
      case 'so_coverage': {
        const bucketHits = bucket
          ? new Set(
              (ln.so_coverage ?? [])
                .filter((c) => bucketKeyFor(c.required_date) === bucket.key)
                .map((c) => c.key),
            )
          : undefined;
        return (
          <SoCoveragePicker
            coverage={ln.so_coverage ?? []}
            tickedKeys={soKeysFor(ln)}
            onChange={(keys) => setSoKeys(ln, keys)}
            unassigned={unassignedFor(ln)}
            // Take per row off the SAME walk the cell, the split and the banner read, so
            // the dialog cannot say one thing while the row says another (R19).
            takes={Object.fromEntries(
              resolvedSoTakes(
                ln, soKeysFor(ln), soTakeOverridesFor(ln), qtyFor(ln), soOrderFor(ln),
              ).map((t) => [
                t.entry.key,
                t.take,
              ]),
            )}
            onTakeChange={(key, qty) => setSoTake(ln, key, qty)}
            onOrderChange={(keys) => setSoOrder(ln, keys)}
            spoQty={qtyFor(ln)}
            bucketHits={bucketHits}
          />
        );
      }
      case 'on_hand':
        // R7 (captain 8 Sep 2026): this planner's own On hand cell counts every active
        // location, so the lightbox behind it must too, or the total stops footing.
        return <OnHandTable productId={ln.product_id} scope="all" />;
      case 'spo':
        return <SpoTabs supplierId={ln.supplier_id ?? ''} productId={ln.product_id} />;
      default:
        return null;
    }
  };

  /** A line nothing can be sent for has nowhere to send it, so it does not open (R22). */
  const cannotSplit = (ln: SpoSuggestionLine) =>
    ln.cannot_convert || qtyFor(ln) <= 0 || ln.location_options.length === 0;

  /**
   * The Location cell is now a chevron, not an editor (R22, AC-G4).
   *
   * The destinations are an editable LIST of rows, and a DataGrid cell has no room for one -
   * it lived in a popover, which put an editing surface behind a click, clipped it at the
   * viewport edge and could not be read beside the row it belongs to. The row expands
   * instead: same rows, full width, and the cell keeps the reading it always had (where it
   * lands, and how much of it nobody has claimed).
   */
  const renderLocationCell = (ln: SpoSuggestionLine, row: Row<SpoSuggestionLine>) => {
    const splits = splitsFor(ln);
    const unassigned = unassignedFor(ln);
    const disabled = cannotSplit(ln);
    const label =
      splits.length === 0
        ? 'No location'
        : splits.length === 1
          ? ln.location_options.find((o) => o.warehouse_id === splits[0].warehouseId)?.warehouse_code ??
            'Choose'
          : `${splits.length} locations`;
    const mismatch = splitMismatchRef.current.has(ln.shipment_line_id);
    const expanded = row.getIsExpanded();
    return (
      <div className="flex min-w-0 flex-col gap-0.5">
        {disabled ? (
          <span className="truncate text-muted-foreground">{label}</span>
        ) : (
          <button
            type="button"
            aria-expanded={expanded}
            title="Where this SPO lands"
            onClick={() => row.toggleExpanded()}
            className={
              mismatch
                ? 'inline-flex min-w-0 items-center gap-1 rounded-sm font-medium text-destructive underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40'
                : 'inline-flex min-w-0 items-center gap-1 rounded-sm underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40'
            }
          >
            {expanded ? (
              <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            ) : (
              <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            )}
            <span className="truncate">{label}</span>
          </button>
        )}
        {unassigned > 0 && !ln.cannot_convert ? (
          <span className="text-2xs text-muted-foreground">
            {fmtInt(unassigned)} unassigned
          </span>
        ) : null}
      </div>
    );
  };

  /** The Created SPOs grid (R1, AC-H6) - every SPO this shipment has ever produced, each a
   *  link to its own SPO allocation view (R22, purchasing consolidation batch 6 Sep 2026:
   *  the SPO document, never the PO) and its own Delete. A row with no `po_number` yet has
   *  nothing to link to, so it prints plain text instead. */
  const createdSpoColumns = useMemo<ColumnDef<SpoRef>[]>(
    () => [
      {
        id: 'po_number',
        header: 'SPO',
        cell: ({ row }) =>
          row.original.po_number ? (
            <Link
              href={spoDetailHref(row.original.po_number)}
              className="font-medium text-primary hover:underline"
              title={`Open ${row.original.po_number}`}
            >
              {row.original.po_number}
            </Link>
          ) : (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ),
        size: 160,
        enableSorting: false,
      },
      {
        id: 'supplier',
        header: 'Supplier',
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.supplier_name ?? undefined}>
            {row.original.supplier_name ?? EM_DASH}
          </span>
        ),
        size: 170,
        enableSorting: false,
      },
      {
        id: 'line_count',
        header: 'Lines',
        cell: ({ row }) => fmtInt(row.original.line_count),
        size: 70,
        enableSorting: false,
        meta: RIGHT,
      },
      {
        id: 'total_qty',
        header: 'Qty',
        cell: ({ row }) => fmtInt(row.original.total_qty),
        size: 90,
        enableSorting: false,
        meta: RIGHT,
      },
      {
        id: 'created_at',
        header: 'Created',
        // F2 (review round): `created_at` is a naive UTC ISO string - `fmtDate`'s
        // `new Date(iso)` + `toLocaleDateString` reads it in the BROWSER's own timezone,
        // which prints the wrong civil date for anyone not on UTC. Malaysia is the one
        // timezone this product runs in, so `formatDateInMalaysia` is the correct read
        // everywhere else this column's own sibling dates are shown.
        cell: ({ row }) =>
          row.original.created_at ? formatDateInMalaysia(row.original.created_at) : EM_DASH,
        size: 110,
        enableSorting: false,
      },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => {
          const pill = purchaseOrderStatusPill({ status: row.original.status });
          return (
            <Badge variant={pill.variant} size="sm">
              {pill.label}
            </Badge>
          );
        },
        size: 110,
        enableSorting: false,
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-0.5">
            {/* R24, AC-K1: the same planner, loaded with what this SPO already is - not a
                second editor. Beside Delete, because "change it" and "scrap it" are the two
                things one can do to an SPO that exists. */}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground hover:text-foreground"
              aria-label={`Edit ${row.original.po_number ?? 'this SPO'} in planner`}
              title="Edit in planner"
              onClick={() => setEditPoId(row.original.purchase_order_id)}
            >
              <Pencil className="size-4" aria-hidden />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground hover:text-destructive"
              aria-label={`Delete ${row.original.po_number ?? 'this SPO'}`}
              onClick={() => setDeleteTarget(row.original)}
            >
              <Trash2 className="size-4" aria-hidden />
            </Button>
          </div>
        ),
        size: 96,
        enableSorting: false,
      },
    ],
    [],
  );

  const columns = useMemo<ColumnDef<SpoSuggestionLine>[]>(
    () => [
      {
        id: 'product',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          const ln = row.original;
          return (
            <div className={ln.cannot_convert ? 'flex min-w-0 items-center gap-1 opacity-60' : 'flex min-w-0 items-center gap-1'}>
              <span className="truncate font-medium" title={ln.item_code ?? ''}>
                {ln.item_code ?? EM_DASH}
              </span>
              {ln.reason ? (
                <span className="shrink-0" title={ln.reason} aria-label={ln.reason}>
                  <Info className="size-3.5 text-muted-foreground" aria-hidden />
                </span>
              ) : null}
            </div>
          );
        },
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'packed_qty',
        header: ({ column }) => <DataGridColumnHeader title="Packed" column={column} />,
        cell: ({ row }) => <span className="tabular-nums">{fmtInt(row.original.packed_qty)}</span>,
        size: 85,
        enableSorting: false,
        meta: { headerTitle: 'Packed' },
      },
      {
        id: 'po_covered',
        header: ({ column }) => <DataGridColumnHeader title="PO covers" column={column} />,
        cell: ({ row }) => {
          const ln = row.original;
          // R1: nothing left on this line for THIS shipment to convert - a prior `create`
          // run already claimed it all, so there is no tick to make any more. It never
          // fires in EDIT mode (R24): the SPO whose takes are on screen IS the run that
          // claimed them, and locking its own row would make the edit unreachable.
          if (!editingRef.current && ln.remaining_qty <= 0) {
            return <span className="text-2xs text-muted-foreground">Done</span>;
          }
          if (!ln.po_takes.length) {
            return <span className="tabular-nums text-muted-foreground">{fmtInt(0)}</span>;
          }
          const ticked = takeIdsFor(ln);
          return (
            <PlanNumberButton
              value={fmtInt(poCoveredFor(ln, ticked))}
              label="Which PO covers this, oldest purchase order first"
              onClick={() => setDialog({ kind: 'po_takes', line: ln })}
            />
          );
        },
        size: 130,
        enableSorting: false,
        meta: { headerTitle: 'PO covers' },
      },
      {
        id: 'on_hand',
        header: ({ column }) => <DataGridColumnHeader title="On hand" column={column} />,
        cell: ({ row }) => {
          const ln = row.original;
          return (
            <PlanNumberButton
              value={fmtInt(ln.on_hand)}
              label="Where this product is on hand right now"
              onClick={() => setDialog({ kind: 'on_hand', line: ln })}
              disabled={!ln.product_id}
            />
          );
        },
        size: 85,
        enableSorting: false,
        meta: { headerTitle: 'On hand (context)' },
      },
      {
        id: 'incoming_spo',
        header: ({ column }) => <DataGridColumnHeader title="Incoming SPO" column={column} />,
        cell: ({ row }) => {
          const ln = row.original;
          return (
            <PlanNumberButton
              value={fmtInt(ln.incoming_spo)}
              label="What is already on the water, every active location"
              onClick={() => setDialog({ kind: 'spo', line: ln })}
              // The drill reads by supplier AND product; without both there is nothing to
              // ask for, so the figure stays plain text rather than opening an empty table.
              disabled={!ln.product_id || !ln.supplier_id}
            />
          );
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Incoming SPO (context)' },
      },
      {
        id: 'suggested_qty',
        header: ({ column }) => <DataGridColumnHeader title="SPO qty" column={column} />,
        cell: ({ row }) => renderQtyCell(row.original),
        size: 130,
        enableSorting: false,
        meta: { headerTitle: 'SPO qty' },
      },
      {
        id: 'so_covered',
        header: ({ column }) => <DataGridColumnHeader title="SO covered" column={column} />,
        cell: ({ row }) => {
          const ln = row.original;
          if (!editingRef.current && ln.remaining_qty <= 0) {
            return <span className="text-2xs text-muted-foreground">Done</span>;
          }
          if (ln.cannot_convert || !(ln.so_coverage ?? []).length) {
            return <span className="tabular-nums text-muted-foreground">{EM_DASH}</span>;
          }
          const keys = soKeysFor(ln);
          return (
            <PlanNumberButton
              value={fmtInt(tickedQty(ln, keys, soTakeOverridesFor(ln), qtyFor(ln), soOrderFor(ln)))}
              label="Which demand this SPO is for - project by need-by date, then retail"
              onClick={() => setDialog({ kind: 'so_coverage', line: ln })}
            />
          );
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'SO covered' },
      },
      {
        id: 'location',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        cell: ({ row }) => renderLocationCell(row.original, row),
        size: 220,
        enableSorting: false,
        meta: {
          headerTitle: 'Location',
          // `DataGridTable` renders this full width under any row whose `getIsExpanded()`
          // is true, the same mechanism `PlanLinesGrid` uses for its per-warehouse panel.
          // Only ONE column may carry it (the table takes the first it finds), and this is
          // the one the chevron sits in.
          expandedContent: (ln: SpoSuggestionLine) => (
            <LocationSplitPanel
              line={ln}
              splits={splitsFor(ln)}
              qty={qtyFor(ln)}
              disabled={cannotSplit(ln)}
              mismatch={splitMismatchRef.current.has(ln.shipment_line_id)}
              showQtyMessage={qtyRefusedRef.current}
              onChange={(next) => setSplits(ln, next)}
            />
          ),
        },
      },
    ],
    // The deps are EMPTY on purpose and must stay that way: a rebuilt columns array
    // remounts every cell (see `stateRef`), which is what made the SPO qty input lose focus
    // after each keystroke. The cells read live state through `stateRef` / `splitMismatchRef`
    // instead, and re-run on every render of this component because `DataGridTable` builds a
    // fresh element for each cell.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const table = useReactTable({
    columns,
    data: lines,
    getRowId: (row) => row.shipment_line_id,
    state: { expanded },
    onExpandedChange: setExpanded,
    getRowCanExpand: (row) => !cannotSplit(row.original),
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // Every line covered, or nothing packed for one: the chevrons are all disabled, so the
  // two toolbar buttons have nothing to open. A control that does nothing when pressed
  // reads as a broken screen, so it says why instead (R22, AC-G5).
  const nothingToExpand = table.getRowModel().rows.every((r) => !r.getCanExpand());

  const poMatrix = useMemo(() => {
    const entries: SpoMatrixEntry<SpoPoTake & { item_code: string | null }>[] = [];
    for (const ln of lines) {
      const rowKey = ln.item_code ? `item:${ln.item_code}` : `line:${ln.shipment_line_id}`;
      const rowLabel = ln.item_code ?? ln.product_name ?? 'Unresolved';
      const takes = cascadeTake(ln.po_takes, qtyFor(ln));
      for (const t of takes) {
        entries.push({
          row_key: rowKey,
          row_label: rowLabel,
          row_description: null,
          shipment_line_id: ln.shipment_line_id,
          date: t.expected_date,
          qty: t.takenQty,
          taken_qty: 0,
          detail: { ...t, item_code: ln.item_code },
        });
      }
      // S5 (AC-E8): every candidate line ANOTHER SPO already pulled from, on its own entry
      // (`qty: 0`) - whether or not THIS cascade also took from the same line, so a mixed
      // cell (both figures) and a taken-only cell (grey) both fall out of the same sum.
      for (const t of ln.po_takes) {
        if (!(t.taken_qty > 0)) continue;
        entries.push({
          row_key: rowKey,
          row_label: rowLabel,
          row_description: null,
          shipment_line_id: ln.shipment_line_id,
          date: t.expected_date,
          qty: 0,
          taken_qty: t.taken_qty,
          taken_by: t.taken_by,
          detail: { ...t, item_code: ln.item_code },
        });
      }
    }
    return buildSpoScheduleMatrix(entries);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines, state]);

  // Bucketed by the TICKS, not by a cascade over the chosen locations (AC-G8): the ticks
  // are what the operator actually decided this SPO is for, and a schedule computed from
  // anything else would show a different answer to the one the drill above states.
  const soMatrix = useMemo(() => {
    const entries: SpoMatrixEntry<SpoDemandLine & { item_code: string | null }>[] = [];
    for (const ln of lines) {
      if (ln.cannot_convert) continue;
      const rowKey = ln.item_code ? `item:${ln.item_code}` : `line:${ln.shipment_line_id}`;
      const rowLabel = ln.item_code ?? ln.product_name ?? 'Unresolved';
      const keys = soKeysFor(ln);
      // R19: the SAME resolved walk the cell, the dialog and the confirm payload read - a
      // row the operator retyped shows up here at HER number, not the raw cascade.
      for (const { entry, take } of resolvedSoTakes(
        ln, keys, soTakeOverridesFor(ln), qtyFor(ln), soOrderFor(ln),
      )) {
        if (take <= 0) continue;
        entries.push({
          row_key: rowKey,
          row_label: rowLabel,
          row_description: null,
          shipment_line_id: ln.shipment_line_id,
          date: entry.required_date,
          qty: take,
          taken_qty: 0,
          detail: {
            so_number: entry.document,
            customer_name: entry.customer_name,
            agent_name: null,
            required_date: entry.required_date,
            order_date: null,
            qty: take,
            item_code: ln.item_code,
          },
        });
      }
      // S5 (AC-E8): every piece of demand ANOTHER SPO already covers, on its own entry
      // (`qty: 0`) - see the PO loop above for why this is separate from the take loop.
      for (const entry of ln.so_coverage ?? []) {
        if (!(entry.taken_qty > 0)) continue;
        entries.push({
          row_key: rowKey,
          row_label: rowLabel,
          row_description: null,
          shipment_line_id: ln.shipment_line_id,
          date: entry.required_date,
          qty: 0,
          taken_qty: entry.taken_qty,
          taken_by: entry.taken_by,
          detail: {
            so_number: entry.document,
            customer_name: entry.customer_name,
            agent_name: null,
            required_date: entry.required_date,
            order_date: null,
            qty: 0,
            item_code: ln.item_code,
          },
        });
      }
    }
    return buildSpoScheduleMatrix(entries);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines, state]);

  if (suggestion.isLoading || (editPoId && plannerState.isLoading)) {
    return (
      <Card className="p-4">
        <Skeleton className="h-6 w-64" />
        <Skeleton className="mt-3 h-40 w-full rounded-lg" />
      </Card>
    );
  }

  // An SPO that cannot be opened for editing (deleted since the link was made, or a
  // purchase order Create SPO never minted) says so and offers the way back to the
  // planner, rather than an empty grid with a Save button over it.
  if (editPoId && plannerState.isError) {
    return (
      <Card className="flex flex-col items-center gap-3 p-8 text-center">
        <p className="text-sm font-medium text-destructive">
          {plannerState.error instanceof Error
            ? plannerState.error.message
            : 'Failed to open this SPO in the planner.'}
        </p>
        <Button variant="outline" size="sm" onClick={() => setEditPoId(null)}>
          Back to the planner
        </Button>
      </Card>
    );
  }

  if (suggestion.isError) {
    return (
      <Card className="flex flex-col items-center gap-3 p-8 text-center">
        <p className="text-sm font-medium text-destructive">
          {suggestion.error instanceof Error
            ? suggestion.error.message
            : 'Failed to load the SPO planner.'}
        </p>
        <Button variant="outline" size="sm" onClick={() => suggestion.refetch()}>
          <RefreshCw className="size-4" />
          Try again
        </Button>
      </Card>
    );
  }

  if (!lines.length && !existingSpos.length) {
    return (
      <Card className="p-8 text-center">
        <p className="text-sm font-medium">This container has no line we hold a product for.</p>
      </Card>
    );
  }

  const deleteTargetName = deleteTarget?.po_number ?? 'this SPO';
  const editingName = plannerState.data?.po_number ?? 'this SPO';
  const cancelEdit = () => setEditPoId(null);
  const receivedRowNames = lines
    .filter((ln) => receivedMismatch.has(ln.shipment_line_id))
    .map((ln) => ln.item_code ?? ln.product_name ?? 'One line')
    .join(', ');
  // B4: the server's own sentence, said before the round trip.
  const noLocationRowNames = lines
    .filter((ln) => takesWithoutLocation.has(ln.shipment_line_id))
    .map((ln) => ln.item_code ?? ln.product_name ?? 'One line')
    .join(', ');

  return (
    <div className="flex flex-col gap-4">
      {suggestion.data?.self_heal_note ? (
        <Alert variant="warning" size="sm">
          <AlertDescription>{suggestion.data.self_heal_note}</AlertDescription>
        </Alert>
      ) : null}

      {/* R1 (AC-H6): every SPO this container has ever produced, oldest first - a container is
          routinely converted in more than one pass, so this sits above the remainder planner
          rather than replacing it. Gone while one of them is being EDITED (R24): the screen
          is about that one SPO then, and leaving the grid up offers a second Edit and a
          Delete for work already open below it. */}
      {existingSpos.length && !editPoId ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between py-3">
            <h3 className="text-sm font-semibold">Created SPOs</h3>
            <Button
              size="sm"
              variant="outline"
              onClick={() => worksheet.mutate(suggestion.data?.shipment_number ?? 'container')}
              disabled={worksheet.isPending}
            >
              {worksheet.isPending ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              ) : (
                <Download className="size-4" aria-hidden />
              )}
              Download worksheet
            </Button>
          </CardHeader>
          <CardTable>
            <DrillTable
              columns={createdSpoColumns}
              rows={existingSpos}
              getRowId={(spo) => spo.purchase_order_id}
              emptyMessage="No SPO has been created from this container yet."
            />
          </CardTable>
        </Card>
      ) : null}

      {lines.length ? (
        <Card>
        <CardHeader className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold">
            {editPoId ? `Editing ${editingName}` : 'SPO planner'}
          </h3>
        </div>
        {editPoId ? (
          /* R24, AC-K1: the same payload Create SPO builds, sent as a revision of the SPO
             already on file - the number and its header row are untouched. */
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={cancelEdit} disabled={revise.isPending}>
              <X className="size-4" aria-hidden />
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() =>
                sendIfUsable(() =>
                  revise.mutate(confirmLines, { onSuccess: () => setEditPoId(null) }),
                )
              }
              disabled={
                // S5: an SPO cannot be emptied by a revision - Delete is the action for
                // that, and the server refuses it (422) for the same reason.
                (!includedCount && !hasUnusableQty) ||
                splitMismatch.size > 0 ||
                soTakeMismatch.size > 0 ||
                takesWithoutLocation.size > 0 ||
                receivedMismatch.size > 0 ||
                revise.isPending
              }
            >
              {revise.isPending ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              ) : (
                <Check className="size-4" aria-hidden />
              )}
              Save changes
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            // Creates on the click (captain, 4 Sep): the server caps every line at its own
            // remainder and writes the uncovered part without a PO pull, so there is nothing
            // to confirm - no review dialog.
            onClick={() => sendIfUsable(() => create.mutate(confirmLines))}
            disabled={
              (!includedCount && !hasUnusableQty) ||
              splitMismatch.size > 0 ||
              soTakeMismatch.size > 0 ||
              takesWithoutLocation.size > 0 ||
              create.isPending
            }
          >
            {create.isPending ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
            ) : (
              <Check className="size-4" aria-hidden />
            )}
            Create SPO
          </Button>
        )}
      </CardHeader>

      <div className="flex flex-col gap-3 border-t border-border px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-md border border-input" role="group" aria-label="Planner view">
            <Button
              type="button"
              size="sm"
              variant={view === 'table' ? 'primary' : 'ghost'}
              className="rounded-e-none"
              aria-pressed={view === 'table'}
              onClick={() => setView('table')}
            >
              <Table2 className="size-4" aria-hidden />
              Table
            </Button>
            <Button
              type="button"
              size="sm"
              variant={view === 'schedule' ? 'primary' : 'ghost'}
              className="rounded-s-none border-s border-input"
              aria-pressed={view === 'schedule'}
              onClick={() => setView('schedule')}
            >
              <LayoutGrid className="size-4" aria-hidden />
              Schedule
            </Button>
          </div>
          {view === 'table' ? (
            <div className="flex items-center gap-1">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={nothingToExpand}
                title={nothingToExpand ? 'No line can be split yet' : undefined}
                // Only the lines that CAN be split. `toggleAllRowsExpanded(true)` ignores
                // `getRowCanExpand`, so it opened a split panel under every covered line too -
                // a panel with a disabled editor and nothing to do in it.
                onClick={() =>
                  table.setExpanded(
                    Object.fromEntries(
                      table
                        .getRowModel()
                        .rows.filter((r) => r.getCanExpand())
                        .map((r) => [r.id, true]),
                    ),
                  )
                }
              >
                <ChevronsUpDown className="size-4" aria-hidden />
                Expand all
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={nothingToExpand}
                title={nothingToExpand ? 'No line can be split yet' : undefined}
                onClick={() => table.setExpanded({})}
              >
                <ChevronsDownUp className="size-4" aria-hidden />
                Collapse all
              </Button>
            </div>
          ) : null}
        </div>
        <div className="flex min-w-0 items-center justify-end gap-2 sm:ms-auto">
          {/* S3 (review): this gates Create SPO in EITHER view, so it renders in both - a
              disabled Create SPO with no reason on screen reads as broken. It sits before
              the schedule View select so the reason is read first. */}
          {splitMismatch.size > 0 ? (
            <span className="text-2xs text-end font-medium text-destructive">
              {splitMismatch.size} line{splitMismatch.size === 1 ? '' : 's'} - location split does not add up
              to the SPO qty
            </span>
          ) : null}
          {soTakeMismatch.size > 0 ? (
            <span className="text-2xs text-end font-medium text-destructive">
              {soTakeMismatch.size} line{soTakeMismatch.size === 1 ? '' : 's'} - a Take exceeds
              what is left, or the total exceeds the SPO qty
            </span>
          ) : null}
          {/* B4: the same sentence the server refuses with, naming the row. */}
          {takesWithoutLocation.size > 0 ? (
            <span className="text-2xs text-end font-medium text-destructive">
              {noLocationRowNames} needs a location before it can cover sales orders
            </span>
          ) : null}
          {/* S5: an SPO with no line left is a document that says nothing - the planner
              says which action that is instead of refusing after the round trip. */}
          {editPoId && !includedCount ? (
            <span className="text-2xs text-end font-medium text-destructive">
              An SPO has to keep at least one line - delete it instead
            </span>
          ) : null}
          {/* AC-K3: names the ROW, because "some line is short" is not something the
              operator can act on across a container's worth of them. */}
          {receivedMismatch.size > 0 ? (
            <span className="text-2xs text-end font-medium text-destructive">
              {receivedRowNames} - the qty is below what has already been received
            </span>
          ) : null}
          {view === 'schedule' ? (
            <div className="flex items-center gap-2">
              <Label htmlFor="spo-schedule-mode" className="text-xs text-muted-foreground">
                View
              </Label>
              <div className="w-44">
                <SearchableSelect
                  id="spo-schedule-mode"
                  value={scheduleView}
                  onChange={(v) => setScheduleView(v as ScheduleView)}
                  options={[
                    { value: 'po', label: 'Purchase order' },
                    { value: 'so', label: 'Sales order' },
                  ]}
                />
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {view === 'table' ? (
        <DataGrid
          table={table}
          recordCount={lines.length}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          emptyMessage="This container has no line we hold a product for."
        >
          <CardTable>
            <DataGridTable />
          </CardTable>
        </DataGrid>
      ) : scheduleView === 'po' ? (
        poMatrix.rows.length === 0 ? (
          <div className="p-6 text-center">
            <p className="text-sm font-medium">Nothing is pulled from an open PO yet.</p>
          </div>
        ) : (
          <div className="p-4">
            <SpoScheduleMatrixTable
              rowHeader="Product"
              rows={poMatrix.rows}
              buckets={poMatrix.buckets}
              cells={poMatrix.cells}
              onCellClick={(cell, bucket) => {
                // The clicked CELL's own first entry, not the row's: a row groups by item
                // code and can hold more than one shipment line, and the row's own
                // `shipment_line_id` names whichever line first CREATED the row - not
                // necessarily the one behind THIS cell (S4, AC-D2).
                const ln = lines.find((l) => l.shipment_line_id === cell.entries[0]?.shipment_line_id);
                if (ln) setDialog({ kind: 'po_takes', line: ln, bucket });
              }}
            />
          </div>
        )
      ) : soMatrix.rows.length === 0 ? (
        <div className="p-6 text-center">
          <p className="text-sm font-medium">No location has been chosen for any line yet.</p>
        </div>
      ) : (
        <div className="p-4">
          <SpoScheduleMatrixTable
            rowHeader="Product"
            rows={soMatrix.rows}
            buckets={soMatrix.buckets}
            cells={soMatrix.cells}
            onCellClick={(cell, bucket) => {
              const ln = lines.find((l) => l.shipment_line_id === cell.entries[0]?.shipment_line_id);
              if (ln) setDialog({ kind: 'so_coverage', line: ln, bucket });
            }}
          />
        </div>
      )}
      <CardFooter className="justify-end text-2xs text-muted-foreground">
        {editPoId
          ? `${includedCount} of ${lines.length} line${lines.length === 1 ? '' : 's'} stay on this SPO`
          : `${includedCount} of ${lines.length} line${lines.length === 1 ? '' : 's'} will create an SPO`}
      </CardFooter>

      {dialog ? (
        <PlanRowDialog
          kind={dialog.kind}
          productCode={dialog.line.item_code ?? dialog.line.product_name ?? EM_DASH}
          productName={dialog.line.product_name}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        >
          {dialogBody(dialog.kind, dialog.line, dialog.bucket)}
        </PlanRowDialog>
      ) : null}

        </Card>
      ) : null}

      {/* Delete on a row of the Created SPOs grid above (R1) - confirms by the SPO's own
          number, never a bare `confirm()`, and scopes the unwind to just that one header. */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm delete</AlertDialogTitle>
            <AlertDialogDescription>
              This deletes {deleteTargetName}. The planner can create another, but the number
              is minted fresh - this one is gone. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteSpo.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (!deleteTarget) return;
                deleteSpo.mutate(deleteTarget.purchase_order_id, {
                  onSuccess: () => setDeleteTarget(null),
                });
              }}
              disabled={deleteSpo.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleteSpo.isPending ? 'Deleting...' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * Where a line's SPO lands (fourth doctrine-correction ask, "I can create SPO to multiple
 * locations"): zero, one or several destination rows, each an independent warehouse + qty,
 * validated to sum to the line's SPO qty.
 *
 * In the EXPANDED ROW, full width, not a popover (R22, AC-G4). An editable list of rows in a
 * popover is an editing surface behind a click: it clips at the viewport edge, it cannot be
 * read beside the row it belongs to, and it hid the one figure that decides whether Create
 * SPO is allowed. The row itself has the width for it.
 *
 * What it does NOT carry is the coverage list. Which orders this SPO is for is a decision of
 * its own and belongs in the SO covered lightbox (captain, 27 Aug); repeating it here made
 * two places to read the same walk, and they disagreed the moment one of them was stale.
 */
function LocationSplitPanel({
  line,
  splits,
  qty,
  disabled,
  mismatch,
  showQtyMessage,
  onChange,
}: {
  line: SpoSuggestionLine;
  splits: SplitState[];
  qty: number;
  disabled: boolean;
  mismatch: boolean;
  /** A send was refused on a quantity: the boxes now show their message. */
  showQtyMessage: boolean;
  onChange: (splits: SplitState[]) => void;
}) {
  const options = line.location_options.map((o: SpoLocationOption) => ({
    value: o.warehouse_id,
    label: o.warehouse_code ?? o.warehouse_id,
  }));
  const total = splitsTotal(splits);
  /** The remainder, which is what the SPO has not been given a destination for. Negative
   *  means the rows ask for more than the SPO holds - the one state that has to shout. */
  const unassigned = qty - total;

  const updateRow = (index: number, patch: Partial<SplitState>) =>
    onChange(splits.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  const removeRow = (index: number) => onChange(splits.filter((_, i) => i !== index));
  const addRow = () => {
    const remaining = Math.max(qty - total, 0);
    const used = new Set(splits.map((s) => s.warehouseId));
    const next = line.location_options.find((o) => !used.has(o.warehouse_id));
    onChange([...splits, { warehouseId: next?.warehouse_id ?? '', qty: remaining || 0 }]);
  };

  if (disabled) {
    return (
      <div className="px-4 py-3 text-2xs text-muted-foreground">
        No destination can be chosen for this line.
      </div>
    );
  }

  return (
    <div className="bg-muted/30 px-4 py-3">
      {/* Its own scroll container, so the select + qty + remove row stays usable at 375px
          without widening the grid it sits inside. */}
      <ScrollArea>
        <div className="min-w-[30rem] space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium">
              {line.item_code ?? line.product_name ?? 'This product'} - destinations
            </p>
            <span
              className={
                mismatch ? 'text-2xs font-medium text-destructive' : 'text-2xs text-muted-foreground'
              }
            >
              {fmtInt(total)} / {fmtInt(qty)} split
            </span>
          </div>

          <div className="space-y-2">
            {splits.map((split, i) => {
              const location = line.location_options.find((o) => o.warehouse_id === split.warehouseId);
              return (
                <div key={i} className="space-y-1 rounded-md border bg-background p-2">
                  <div className="flex items-center gap-1.5">
                    <div className="min-w-0 flex-1">
                      <SearchableSelect
                        size="sm"
                        value={split.warehouseId}
                        options={options}
                        onChange={(v) => updateRow(i, { warehouseId: v || '' })}
                        placeholder="Choose location"
                        clearable
                      />
                    </div>
                    <div>
                      <NumberInput
                        min={0}
                        step={1}
                        className="h-8 w-20 tabular-nums"
                        aria-label={`Quantity for destination ${i + 1}`}
                        value={split.qtyText ?? split.qty}
                        error={showQtyMessage ? quantityMessage(split.qtyText) : undefined}
                        errorClassName="w-24 whitespace-normal"
                        onChange={(e) => {
                          const text = e.target.value;
                          const typed = parseNumberDraft(text);
                          if (typed !== null && typed >= 0) updateRow(i, { qty: typed, qtyText: undefined });
                          else updateRow(i, { qty: 0, qtyText: text });
                        }}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
                      aria-label="Remove destination"
                      onClick={() => removeRow(i)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                  {location ? (
                    <div className="grid grid-cols-4 gap-1 text-2xs text-muted-foreground">
                      <span>SO {fmtInt(location.outstanding_so)}</span>
                      <span>On hand {fmtInt(location.on_hand)}</span>
                      <span>SPO {fmtInt(location.incoming_spo)}</span>
                      <span title="available after this SPO">
                        After {fmtSigned(location.available + (split.qty || 0))}
                      </span>
                    </div>
                  ) : null}
                </div>
              );
            })}

            <div
              className={
                unassigned < 0
                  ? 'flex items-center justify-between gap-2 rounded-md border border-destructive px-2 py-1.5 text-xs font-medium text-destructive'
                  : 'flex items-center justify-between gap-2 rounded-md border border-dashed px-2 py-1.5 text-xs text-muted-foreground'
              }
            >
              <span>Unassigned</span>
              <span className="tabular-nums">{fmtInt(unassigned)}</span>
            </div>
          </div>

          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-full"
            onClick={addRow}
            disabled={splits.length >= line.location_options.length}
          >
            <Plus className="size-4" aria-hidden />
            Add location
          </Button>
        </div>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </div>
  );
}

export default SpoPlannerTable;
