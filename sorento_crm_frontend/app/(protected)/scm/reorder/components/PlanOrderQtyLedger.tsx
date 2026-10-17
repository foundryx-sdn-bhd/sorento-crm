'use client';

import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { LiveNumberInput } from '@/components/ui/number-input';
import {
  EM_DASH,
  fmtDate,
  fmtDecimal,
  fmtInt,
  fmtMoney,
  fmtSigned,
  fmtSupplierCost,
} from '../../lib/format';
import { DrillHeader } from './PlanExplainDrills';
import { RecentPurchasesTable } from './PlanPurchaseTrendPopover';
import { useLocationStock, useRecommendationDemand } from '../hooks/useReorderRun';
import { describePurchaseTrend, type ProductPurchaseTrend } from '../lib/purchaseTrend';
import type { PlanLine } from '../lib/planLine';
import type { PlanDecision } from '../lib/planDecisions';
import {
  applySourceEdits,
  defaultSourceEdits,
  maxSourceEdit,
  type CoverProposal,
  type SourceEdits,
} from '../lib/coverPlan';
import { describePoBook, type PoReceipt } from '../lib/poCover';
import { moqGap, moqGapNote, type ProductEconomics } from '../lib/productHealth';
import {
  composeMixture,
  daysTerm,
  forecastAddOn,
  forecastQtyCap,
  ledgerDemandRows,
  levelLine,
  lineBreachStatus,
  roundBuyQty,
  type LedgerDemandRow,
} from '../lib/orderQtyLedger';
import type { TrajectoryEntry } from '../lib/trajectory';
import type { ReorderRecommendation } from '../types/reorder.types';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';

/**
 * The order-qty ledger, behind the "Explain order qty" trigger on the Suggested-qty column.
 *
 * Top to bottom (AC-R8, `PLAN-scm-reorder-per-product.md`): PROJECT DEMAND, RETAIL DEMAND,
 * NET NOW, THE BUY, HISTORY. Demand comes first because "what is being asked for" is the
 * first question; history comes last because it is context, not a reason.
 *
 * The cover block sits between Net now and The buy on the FORECAST basis only. On the
 * buyer's own level basis the plan is netted across every location of the product, so the
 * stock a transfer would move is already inside Net now - offering it as a deduction there
 * would subtract it twice. Moving it stays a decision on the fulfilment board.
 *
 * Where the cover block does render, it reads and writes the SAME decision state the
 * Decision cell and the Adjust dialog use (`coverForLine` + `composeMixture`, the one
 * formula) - there is no second model of what a line's buy is made of, and the toggle IS
 * the edit (UAC B4).
 */

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-t bg-muted/40 px-3 py-1.5 text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

function KVRow({
  label,
  value,
  note,
  muted,
}: {
  label: string;
  value: string;
  note?: string;
  muted?: boolean;
}) {
  return (
    <div className={`flex items-baseline justify-between gap-3 text-xs ${muted ? 'text-muted-foreground' : ''}`}>
      <span className="text-muted-foreground">{label}</span>
      <span className="text-end">
        <span className="tabular-nums">{value}</span>
        {note ? <span className="ms-1.5 text-2xs text-muted-foreground">{note}</span> : null}
      </span>
    </div>
  );
}

/** A cover part the buyer can turn off or back on - the ONE editing surface this ledger
 *  offers, and the reason a separate Adjust popup is not needed for this operation.
 *
 * Unticked reads as plain muted text, never strikethrough (user feedback, 2026-08-12) -
 * strikethrough is reserved for `disabled`, the one case where the line names something
 * the buyer cannot actually toggle (a trend that zeroed the add-on out). */
function ToggleLine({
  checked,
  onToggle,
  label,
  title,
  disabled,
}: {
  checked: boolean;
  onToggle: () => void;
  label: string;
  title?: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={`flex items-start gap-2 text-xs ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
      title={title}
    >
      <Checkbox
        size="sm"
        checked={checked}
        onCheckedChange={onToggle}
        disabled={disabled}
        className="mt-0.5"
      />
      <span
        className={
          checked
            ? 'font-medium'
            : disabled
              ? 'text-muted-foreground line-through'
              : 'text-muted-foreground'
        }
      >
        {label}
      </span>
    </label>
  );
}

/**
 * The forecast add-on's own row: a tick to opt in, plus the buyer's own editable figure
 * (user feedback, 2026-08-12: "what if I don't want to buy 1,200 more, what if 1,500?
 * editable here"). The number is not inside the checkbox's own `<label>` - a native label
 * click delegates to its first form control, which would toggle the checkbox every time
 * the buyer clicked into the input to edit it - so the checkbox and the input are two
 * independent controls, each with its own accessible name.
 */
function ForecastAddOnLine({
  checked,
  onToggle,
  value,
  onCommit,
  onValidityChange,
  showMessage,
  label,
  cap,
  detail,
}: {
  checked: boolean;
  onToggle: () => void;
  /** The figure in force (the last valid entry). */
  value: number;
  onCommit: (value: number) => void;
  onValidityChange?: (valid: boolean) => void;
  showMessage?: boolean;
  /** The tick's own accessible name, built from the last valid figure. */
  label: string;
  cap: number;
  detail: string;
}) {
  return (
    <div className="flex items-start gap-2 text-xs">
      <Checkbox
        size="sm"
        checked={checked}
        onCheckedChange={onToggle}
        className="mt-0.5"
        aria-label={label}
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div
          className={`flex flex-wrap items-baseline gap-1.5 ${checked ? 'font-medium' : 'text-muted-foreground'}`}
        >
          <span>{checked ? 'Added' : '+ Add'}</span>
          <LiveNumberInput
            variant="sm"
            min={0}
            max={cap}
            step={1}
            rules={{ min: 0, max: cap, integer: true }}
            value={value}
            onCommit={onCommit}
            onValidityChange={onValidityChange}
            showMessage={showMessage}
            errorClassName="basis-full"
            className="h-6 w-20 tabular-nums"
            aria-label="Forecast add-on quantity"
          />
        </div>
        <p className="text-2xs text-muted-foreground">{`(${detail})`}</p>
      </div>
    </div>
  );
}

/**
 * One offered location and how much of it to use.
 *
 * > "why am I allowed to use stock from other locations? ... use stock should behave like the
 * >  top-up purchase control - a toggle, and when on, editable per-location quantities
 * >  feeding the buy qty."
 *
 * The same two-control shape as `ForecastAddOnLine`, and for the same reason: the number is
 * NOT inside a `<label>` wrapping a checkbox, so clicking into the input to edit it cannot
 * toggle anything. What is free at that location sits beside the input rather than only in
 * the `max` attribute, because a cap the buyer cannot see reads as the field being broken.
 */
function CoverSourceLine({
  code,
  free,
  max,
  value,
  onCommit,
}: {
  code: string;
  /** What the location holds - a fact about the location, stated as such. */
  free: number;
  /** What this field may actually be set to: `free`, capped by what the row still needs. */
  max: number;
  /** The figure in force (the last valid entry). */
  value: number;
  onCommit: (value: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-2 text-xs">
      <span className="min-w-0 flex-1 truncate" title={code}>
        {code}
      </span>
      <span className="shrink-0 text-2xs tabular-nums text-muted-foreground">
        {`${fmtInt(free)} free`}
      </span>
      <LiveNumberInput
        variant="sm"
        min={0}
        max={max}
        step={1}
        rules={{ min: 0, max, integer: true }}
        value={value}
        onCommit={onCommit}
        errorClassName="basis-full text-end"
        className="h-6 w-20 shrink-0 tabular-nums"
        aria-label={`Use from ${code}`}
      />
    </div>
  );
}

/** THE LINE, auto mode: the ROP derivation, unchanged copy from the old order-qty drill -
 *  safety stock, reorder point, order-up-to, and where the daily rate comes from. MoQ /
 *  order multiple moved to THE BUY block (UAC B6) and no longer render here. */
function AutoDerivation({ line }: { line: PlanLine }) {
  const q = line.order_qty_inputs;
  const rec = line.rec;
  // A pooled buy is sized once for every location that shares stock, then split. Saying
  // so is the difference between "why 55" answering itself and the reader doing
  // arithmetic that cannot work (ported from the old order-qty drill, PlanExplainDrills'
  // `OrderQtyDrill`, when the S3 ledger replaced it - the ledger had dropped this half).
  const alloc = rec.allocation ?? [];
  const pooled = alloc.length > 1;
  const demandRate = line.forecast_daily_demand;
  const leadDays = line.supplier.lead_time_days;
  const reviewTerm = daysTerm(rec.review_days, 'review');
  const leadTerm = daysTerm(leadDays, 'lead');
  return (
    <>
      <div className="space-y-1 px-3 py-2">
        <KVRow label="Safety stock" value={q.safety_stock === null ? EM_DASH : fmtInt(q.safety_stock)} />
        <KVRow
          label={pooled ? 'Order-up-to level (whole pool)' : 'Order-up-to level'}
          value={q.order_up_to === null ? EM_DASH : fmtInt(q.order_up_to)}
        />
      </div>
      {pooled ? (
        <div className="border-t px-3 py-2">
          <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
            Bought for the whole pool
          </div>
          <p className="text-2xs text-muted-foreground">
            One purchase covers {alloc.length} locations that share stock. It is sized once
            against the pool, then placed where the shortage is.
          </p>
          <div className="mt-1 space-y-0.5">
            {alloc.map((a) => (
              <div key={a.warehouse_code ?? String(a.qty)} className="flex justify-between text-2xs">
                <span
                  className={
                    a.warehouse_code === rec.warehouse_code ? 'font-medium' : 'text-muted-foreground'
                  }
                >
                  {a.warehouse_code ?? EM_DASH}
                </span>
                <span className="tabular-nums">{fmtInt(a.qty)}</span>
              </div>
            ))}
            <div className="mt-1 flex justify-between border-t pt-1 text-2xs font-medium">
              <span>Pool total</span>
              <span className="tabular-nums">{fmtInt(alloc.reduce((t, a) => t + (a.qty ?? 0), 0))}</span>
            </div>
          </div>
        </div>
      ) : null}
      <div className="border-t px-3 py-2">
        <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
          Order-up-to level
        </div>
        <p className="text-2xs text-muted-foreground">S = reorder point + demand rate x review period</p>
        <div className="mt-1 flex items-baseline justify-between gap-2 text-xs">
          {reviewTerm ? (
            <span className="tabular-nums text-muted-foreground">
              {q.reorder_point === null ? EM_DASH : fmtInt(q.reorder_point)} +{' '}
              {demandRate == null ? EM_DASH : fmtDecimal(demandRate)}/day x {reviewTerm}
            </span>
          ) : (
            <span className="text-muted-foreground">no review period set</span>
          )}
          <span className="tabular-nums font-semibold">
            {q.order_up_to === null ? EM_DASH : fmtInt(q.order_up_to)}
          </span>
        </div>
        {rec.safety_days != null && rec.review_days != null ? (
          <p className="mt-1 text-2xs text-muted-foreground">
            {fmtInt((rec.safety_days ?? 0) + (leadDays ?? 0) + (rec.review_days ?? 0))} days of cover (
            {fmtInt(rec.safety_days)} safety + {fmtInt(leadDays)} lead + {fmtInt(rec.review_days)} review)
          </p>
        ) : null}
      </div>
      {demandRate != null && rec.demand_window_days ? (
        <div className="border-t px-3 py-2">
          <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
            Where the rate comes from
          </div>
          <p className="text-2xs text-muted-foreground">
            {fmtInt(demandRate * rec.demand_window_days)} units left this location over the last{' '}
            {fmtInt(rec.demand_window_days)} days, which averages {fmtDecimal(demandRate)} a day. Past
            deliveries, not the open orders.
          </p>
        </div>
      ) : null}
      <div className="border-t px-3 py-2">
        <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
          Reorder point
        </div>
        <p className="text-2xs text-muted-foreground">ROP = safety stock + demand rate x lead time</p>
        <div className="mt-1 flex items-baseline justify-between gap-2 text-xs">
          {leadTerm ? (
            <span className="tabular-nums text-muted-foreground">
              {q.safety_stock === null ? EM_DASH : fmtDecimal(q.safety_stock)} +{' '}
              {demandRate == null ? EM_DASH : fmtDecimal(demandRate)}/day x {leadTerm}
            </span>
          ) : (
            <span className="text-muted-foreground">no lead time on file</span>
          )}
          <span className="tabular-nums font-semibold">
            {q.reorder_point === null ? EM_DASH : fmtInt(q.reorder_point)}
          </span>
        </div>
      </div>
    </>
  );
}

/**
 * The orders behind one channel of a line: SO number, customer, when it is needed, how much
 * (AC-R8). The project block also states how much of each instruction is already placed, so
 * a remainder of 20 cannot be mistaken for an order that was always 20.
 *
 * A channel with nothing in it says so. Hiding the block would leave the reader to guess
 * whether there is no project demand or whether the panel failed to load.
 */
function DemandBlock({
  rows,
  total,
  isLoading,
  isError,
  emptyText,
  showLinked,
}: {
  rows: LedgerDemandRow[];
  total: number;
  isLoading: boolean;
  isError: boolean;
  emptyText: string;
  showLinked?: boolean;
}) {
  if (isLoading) {
    return <p className="px-3 py-2 text-xs text-muted-foreground">Loading orders...</p>;
  }
  if (isError) {
    return <p className="px-3 py-2 text-xs text-muted-foreground">Could not load the orders</p>;
  }
  if (!rows.length) {
    return <p className="px-3 py-2 text-xs text-muted-foreground">{emptyText}</p>;
  }
  return (
    <div className="px-3 py-2">
      <ScrollArea>
        <table className="w-auto min-w-full text-2xs">
          <thead>
            <tr className="text-muted-foreground/70">
              <th className="pb-0.5 text-left font-normal">Order</th>
              <th className="pb-0.5 text-left font-normal">Customer</th>
              <th className="pb-0.5 text-right font-normal">Needed</th>
              <th className="pb-0.5 text-right font-normal">Qty</th>
              {showLinked ? <th className="pb-0.5 text-right font-normal">Linked</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td className="max-w-24 truncate py-0.5" title={r.soNumber}>
                  {r.soNumber}
                </td>
                <td className="max-w-28 truncate py-0.5 text-muted-foreground" title={r.customer}>
                  {r.customer}
                </td>
                <td className="py-0.5 text-right tabular-nums text-muted-foreground">
                  {r.deliveryDate ? fmtDate(r.deliveryDate) : 'no date'}
                </td>
                <td className="py-0.5 text-right tabular-nums">{fmtInt(r.qty)}</td>
                {showLinked ? (
                  <td className="py-0.5 text-right tabular-nums text-muted-foreground">
                    {r.linked === null ? EM_DASH : fmtInt(r.linked)}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
      {total > rows.length ? (
        <p className="mt-1 text-2xs text-muted-foreground">
          {`Showing ${fmtInt(rows.length)} of ${fmtInt(total)} orders`}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Where the on-hand figure actually sits (AC-R8).
 *
 * The buy is one net across the product's site pools, so the total is the number that
 * decides - and the first question it raises is "where is it". Fetched only once the reader
 * asks, and LIVE rather than frozen, which is the same reading the On hand lightbox shows
 * (`useLocationStock`). Site pools only, like the figure it breaks down (R16, R19), which
 * is why the toggle says "by site pool" rather than "by location": a project bin is not
 * one of the places this number can be sitting in.
 */
function LocationStockRows({ productId }: { productId: string | null }) {
  const { data, isLoading, isError } = useLocationStock(productId, Boolean(productId));
  if (isLoading) {
    return <p className="ms-3 text-2xs text-muted-foreground">Loading locations...</p>;
  }
  if (isError) {
    return <p className="ms-3 text-2xs text-muted-foreground">Could not load the locations</p>;
  }
  // SITE POOLS ONLY (R16). This aside breaks down the figure ABOVE it, which counts the
  // pool and nothing else, so listing a project bin under it would print locations that
  // do not add up to the total they are explaining.
  const rows = (data?.locations ?? []).filter((l) => l.is_pool && (l.on_hand ?? 0) !== 0);
  if (!rows.length) {
    return <p className="ms-3 text-2xs text-muted-foreground">No stock in the pool</p>;
  }
  return (
    <div className="ms-3 space-y-0.5">
      {rows.map((l) => (
        <div key={l.warehouse_id} className="flex justify-between gap-2 text-2xs">
          <span className="truncate text-muted-foreground" title={l.warehouse_code ?? undefined}>
            {l.warehouse_code ?? EM_DASH}
          </span>
          <span className="tabular-nums">{fmtInt(l.on_hand)}</span>
        </div>
      ))}
    </div>
  );
}

/** THE LINE, manual mode: the level the buyer owns, its source, and (when the backend ever
 *  carries it) when it was set - not the ROP formula, which no longer decides anything on
 *  this basis. */
function ManualLevelLine({ rec }: { rec: ReorderRecommendation }) {
  const lvl = levelLine(rec);
  return (
    <div className="space-y-1 px-3 py-2">
      <KVRow label="Reorder level" value={lvl.level === null ? EM_DASH : fmtInt(lvl.level)} />
      <p className="text-2xs text-muted-foreground">
        {lvl.sourceLabel}
        {lvl.setAt ? `, set ${lvl.setAt}` : ''}
      </p>
    </div>
  );
}

export function OrderQtyLedger({
  line,
  decision,
  cover,
  poReceipts,
  economicsFor,
  healthThresholds,
  trend,
  onDecide,
  runId,
  purchaseTrend,
  purchaseWindowMonths = 3,
  purchaseTrendReady,
  onNeedPurchaseTrend,
}: {
  line: PlanLine;
  decision: PlanDecision | undefined;
  /** What the plan suggests: buy it, cover it from elsewhere, or both - the SAME proposal
   *  the Decision cell reads. */
  cover: CoverProposal;
  poReceipts: PoReceipt[];
  economicsFor?: (line: PlanLine) => ProductEconomics | undefined;
  healthThresholds: { margin_floor_pct: number; dead_turnover_months: number };
  /** The SAME trajectory entry that drives the row's own Trend pill, so the forecast
   *  add-on's rising/falling read never disagrees with it. */
  trend?: TrajectoryEntry;
  onDecide: (next: PlanDecision) => void;
  /** The run this row belongs to. Without it the two demand blocks have nothing to fetch
   *  and say so - they never invent a list. */
  runId?: string | null;
  /** What we have actually purchased, for the History block - the SAME payload the PO
   *  cell's own popover reads, so the two can never disagree about a product. */
  purchaseTrend?: ProductPurchaseTrend;
  purchaseWindowMonths?: number;
  /** Whether the lazy purchase-trend fetch has answered. Without it "we have never bought
   *  this" and "we have not looked yet" are the same absent payload. */
  purchaseTrendReady?: boolean;
  /** Starts that fetch, the same lazy contract the PO cell's own popover uses: opening
   *  this card is a reason to load it, mounting the whole grid is not. */
  onNeedPurchaseTrend?: () => void;
}) {
  const rec = line.rec;
  const q = line.order_qty_inputs;
  const manual = rec.policy_type === 'reorder_level';
  const poQty = poReceipts.reduce((t, r) => t + r.remaining, 0);
  // ONE FORMULA (PLAN-reorder-one-formula.md, AC-7). `recommended_qty` is the engine's raw
  // buy gap, already net of on-hand AND the open PO book (since #828), so the NEED it came
  // out of is reconstructed here once - `need = recommended_qty + on hand + PO` - and every
  // part below is measured against THAT. Measuring them against the already-net figure
  // instead is what read a genuine 196-unit gap as "Nothing to buy".
  //
  // `stockPart` is S: the row's own site-pool stock the need consumes. A FACT, never a
  // toggle and never persisted (the engine already spent it) - the toggles below own the
  // two parts a buyer really can change, a cross-location BORROW and how much of the open
  // PO book they trust.
  //
  // A `recommended_qty` of 0 or less (a covered row) still reveals a real buy the moment
  // the PO is toggled OFF ("pretend that supply is not there") - that IS the question the
  // toggle answers - while the default, everything on, still reads 0.
  const onHand = rec.on_hand ?? 0;
  const rawBuy = rec.recommended_qty ?? line.order_qty;
  const need = Math.max(0, rawBuy) + onHand + poQty;
  const stockPart = Math.min(onHand, need);

  // The orders behind this row, one fetch per channel - the same endpoint and the same
  // per-channel narrowing the SO cell's own drill uses. Fired when the ledger mounts,
  // which only happens when the popover is opened.
  const projectDemand = useRecommendationDemand(
    runId ?? null, rec.id, Boolean(runId), 'project',
  );
  const retailDemand = useRecommendationDemand(
    runId ?? null, rec.id, Boolean(runId), 'retail',
  );
  const projectRows = ledgerDemandRows(projectDemand.data?.lines);
  const retailRows = ledgerDemandRows(retailDemand.data?.lines);
  // Where the on-hand total sits. Closed by default: the total is what decides, and ten
  // locations opened on every row is the fatigue this card is being reordered to end.
  const [locationsOpen, setLocationsOpen] = useState(false);
  // The History block needs the run's purchase trend, which is fetched lazily. Asking on
  // mount means opening the card IS the request - the same thing the PO cell's popover
  // does, and it happens once per run because react-query caches the answer.
  useEffect(() => {
    onNeedPurchaseTrend?.();
    // Fire once per mount: the callback is a plain setState flip on the caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Which cover parts are "on": the committed decision when one exists, the engine's own
  // suggested mixture otherwise - never a third guess (UAC B4).
  //
  // `stockOn` is LOCAL state, seeded once, exactly as `forecastOn` below is: it is the
  // buyer's intention, not a reading of the numbers. Deriving it from the decision meant
  // that zeroing every location (a normal step while moving units between them) read as
  // "cover is off" and unmounted the inputs from under the buyer mid-edit. Cover 0 with the
  // toggle ON is a legitimate state: the whole gap is bought, and the rows stay there.
  const [stockOn, setStockOn] = useState(() =>
    decision ? (decision.stock?.qty ?? 0) > 0 : cover.coverQty > 0,
  );
  const poOn = decision ? (decision.po ?? 0) > 0 : poQty > 0;
  // How much to take from EACH offered location. Seeded from the decision already taken, or
  // from the engine's proposal (same-segment first) - so a buyer who never touches these
  // inputs gets exactly today's answer (UAC AC-3.5).
  const seedEdits = (): SourceEdits => {
    const taken = decision?.stock?.sources;
    if (taken?.length) {
      const base: Record<string, number> = Object.fromEntries(
        cover.offered.map((s) => [s.warehouse_id, 0]),
      );
      for (const s of taken) base[s.warehouse_id] = s.qty;
      return base;
    }
    return defaultSourceEdits(cover);
  };
  const [sourceEdits, setSourceEdits] = useState<SourceEdits>(seedEdits);
  // Re-seed when the OFFER itself changes - another row's decision can free a location up or
  // spend it down while this popover is open, and a newly offered location left out of the
  // edits map would silently render as 0 with no way to tell it from a deliberate zero. The
  // key deliberately excludes this row's own take (usePlanLines hands its own units back),
  // so committing an edit here never re-seeds over the buyer's typing.
  const offerKey = cover.offered.map((s) => `${s.warehouse_id}:${s.qty}`).join('|');
  const seededFor = useRef(offerKey);
  useEffect(() => {
    if (seededFor.current === offerKey) return;
    seededFor.current = offerKey;
    setSourceEdits(seedEdits());
    // `seedEdits` closes over the current cover/decision, which is what a re-seed wants.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offerKey]);
  const edited = applySourceEdits(cover, sourceEdits);
  // The forecast add-on has no home in `PlanDecision` beyond the buy total it grows, so its
  // own on/off state is re-derived from the reason a previous toggle left behind (best
  // effort - a manual Adjust afterwards can still overwrite that reason, which is fine: the
  // add-on is offered fresh, never assumed).
  const [forecastOn, setForecastOn] = useState(() => Boolean(decision?.reason?.startsWith('Forecast:')));

  const mixture = composeMixture({
    need,
    stockQty: stockPart,
    borrowedQty: stockOn ? edited.coverQty : 0,
    poQty: poOn ? poQty : 0,
  });
  // The SAME trajectory verdict that drives the row's own Trend pill - a rising line never
  // sees a flat proposal here while its pill says "consider more" (S3 follow-up).
  const addOn = forecastAddOn(rec, trend);
  // The buyer's own figure for the add-on, editable regardless of tick state (user
  // feedback, 2026-08-12: "what if I don't want to buy 1,200 more, what if 1,500?
  // editable here"). The trend-aware proposal is only ever the STARTING value - once the
  // popover mounts, the buyer's typed figure is the one that gets applied.
  // The figure in force (the last VALID entry); the box keeps what was typed and commits
  // nothing for a blank or out-of-range entry, so the recorded buy never rests on a figure
  // the buyer did not give.
  const [forecastQty, setForecastQty] = useState<number>(() => addOn?.qty ?? 0);
  const [forecastInvalid, setForecastInvalid] = useState(false);
  const [forecastRefused, setForecastRefused] = useState(false);
  const forecastCap = forecastQtyCap(addOn?.qty ?? 0);
  // `qty` can be 0 (a falling trend ate the whole flat proposal) - that renders a disabled
  // line, never a checkbox, so it can never actually be toggled on.
  const delta = forecastOn ? forecastQty : 0;
  const buyBeforeRounding = mixture.buy + delta;
  const buyRounded = roundBuyQty(buyBeforeRounding, q);
  const econ = economicsFor?.(line);
  const gap = moqGap(buyBeforeRounding, q.moq, buyRounded, econ, healthThresholds.dead_turnover_months);
  // Whether the row's own basis (level or ROP) is actually breached right now, computed
  // honestly off `net` - never trusted off `rec_type` alone (S3 follow-up bug: a covered
  // row well above its line showed a bogus "Gap to line").
  const breach = lineBreachStatus(rec, line.net);
  const showLineStatus = rec.type === 'covered' && !breach.breached && breach.basisLabel !== null;
  // The parenthetical shared between the toggle's own label and the reason recorded when
  // it is applied, so the two surfaces never drift (Fix2/Fix3: name the horizon's source,
  // and the trend read behind it, in one place). Describes WHY the system proposed a
  // figure, not the buyer's own typed number - that lives beside it, not inside it.
  const addOnDetail = (a: NonNullable<typeof addOn>) =>
    `next ${a.horizonDays}d demand at ${fmtDecimal(a.ratePerDay)}/day - ${a.sourceLabel}${
      a.trendNote ? `, ${a.trendNote}` : ''
    }`;

  const commit = (next: {
    stockOn: boolean;
    poOn: boolean;
    forecastOn: boolean;
    forecastQty: number;
    edits: SourceEdits;
  }) => {
    // The per-location figures ARE the split that gets recorded - the ledger never writes
    // the proposal's own sources back over an edit the buyer just made.
    const stock = applySourceEdits(cover, next.edits);
    const m = composeMixture({
      need,
      stockQty: stockPart,
      borrowedQty: next.stockOn ? stock.coverQty : 0,
      poQty: next.poOn ? poQty : 0,
    });
    const d = next.forecastOn ? next.forecastQty : 0;
    const before = m.buy + d;
    // The SAME helper the Accept button and the Adjust popup record through, so the three
    // surfaces cannot land on different quantities for one row (review finding 1, round 2).
    const rounded = roundBuyQty(before, q);
    onDecide({
      ...(rounded > 0 ? { buy: rounded } : {}),
      // Only the BORROW is persisted as `stock`. S is the row's own pool, already inside
      // the engine's net, so recording it would have the server net it a second time -
      // and, with no `stock_takes` to name it, be refused as a mixture with one part.
      ...(m.borrowedQty > 0
        ? {
            stock: {
              qty: m.borrowedQty,
              sources: stock.sources.map((s) => ({
                warehouse_id: s.warehouse_id,
                warehouse_code: s.warehouse_code,
                qty: s.qty,
              })),
            },
          }
        : {}),
      ...(m.usePo > 0 ? { po: m.usePo } : {}),
      ...(next.forecastOn && addOn
        ? { reason: `Forecast: +${fmtInt(next.forecastQty)} (${addOnDetail(addOn)})` }
        : {}),
    });
  };

  const toggleStock = () => {
    const next = !stockOn;
    setStockOn(next);
    commit({ stockOn: next, poOn, forecastOn, forecastQty, edits: sourceEdits });
  };
  const togglePo = () => commit({ stockOn, poOn: !poOn, forecastOn, forecastQty, edits: sourceEdits });
  const toggleForecast = () => {
    const next = !forecastOn;
    if (next && forecastInvalid) {
      setForecastRefused(true);
      return;
    }
    setForecastOn(next);
    commit({ stockOn, poOn, forecastOn: next, forecastQty, edits: sourceEdits });
  };
  // Editing a location re-applies immediately, exactly as the forecast input does: the
  // input IS the edit, there is no separate Record step in this ledger (UAC B4).
  const changeSource = (warehouseId: string, figure: number) => {
    const edits = { ...sourceEdits, [warehouseId]: figure };
    setSourceEdits(edits);
    if (stockOn) commit({ stockOn, poOn, forecastOn, forecastQty, edits });
  };
  // Editing while ticked re-applies immediately - the input IS the edit, there is no
  // separate "Record" step (mirrors the ledger's own UAC B4 non-goal for the toggles).
  const changeForecast = (value: number) => {
    setForecastQty(value);
    if (forecastOn) commit({ stockOn, poOn, forecastOn, forecastQty: value, edits: sourceEdits });
  };

  const noCoverAvailable = stockPart <= 0 && cover.coverQty <= 0 && poQty <= 0;
  const collapsed = buyBeforeRounding <= 0;
  const poLabel =
    poReceipts.length === 1 ? poReceipts[0].po_number : `${poReceipts.length} open orders`;

  return (
    <div>
      {/* Reads what the grid reads: a covered row suggests 0, its buy-anyway offer lives
          in The buy below (the captain, 27 Aug). */}
      <DrillHeader
        title={`Order qty = ${fmtInt(line.status === 'covered_by_stock' ? 0 : line.order_qty)}`}
      />
      {/* Scrolls INSIDE the popover rather than overflowing the viewport - the ledger has
          grown to three blocks, and a 375px screen has no room to spare (S3). */}
      <div className="max-h-[70vh] overflow-y-auto">
        <div className="pb-1">
          {/* The forecast basis still derives its own line, and the derivation is what
              explains the number Net now is measured against. The level basis has no
              derivation to show - the level IS the line - so it states it beside the net
              below instead. */}
          {manual ? null : (
            <>
              <SectionLabel>The line</SectionLabel>
              <AutoDerivation line={line} />
            </>
          )}

          <SectionLabel>Project demand</SectionLabel>
          <DemandBlock
            rows={projectRows}
            total={projectDemand.data?.total ?? projectRows.length}
            isLoading={projectDemand.isLoading}
            isError={projectDemand.isError}
            emptyText={
              runId ? 'No acknowledged project orders' : 'No project orders to show here'
            }
            showLinked
          />

          <SectionLabel>Retail demand</SectionLabel>
          <DemandBlock
            rows={retailRows}
            total={retailDemand.data?.total ?? retailRows.length}
            isLoading={retailDemand.isLoading}
            isError={retailDemand.isError}
            emptyText={runId ? 'No outstanding retail orders' : 'No retail orders to show here'}
          />

          <SectionLabel>Net now</SectionLabel>
          <div className="space-y-1 px-3 py-2">
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <button
                type="button"
                onClick={() => setLocationsOpen((open) => !open)}
                aria-expanded={locationsOpen}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {locationsOpen ? (
                  <ChevronDown className="size-3" aria-hidden />
                ) : (
                  <ChevronRight className="size-3" aria-hidden />
                )}
                <span>On hand</span>
                <span className="text-2xs">by site pool</span>
              </button>
              <span className="tabular-nums text-xs">{fmtInt(rec.on_hand ?? 0)}</span>
            </div>
            {locationsOpen ? <LocationStockRows productId={rec.product_id ?? null} /> : null}
            <KVRow label="+ SPO (arriving)" value={fmtInt(rec.incoming_spo ?? 0)} />
            {/* PO is counted here now (21 Aug fix) - the sizing engine nets the open PO
                book into the same `net` this row was decided against, so a leg reading
                "not counted" would be stating something that stopped being true. */}
            {(rec.outstanding_po ?? 0) > 0 ? (
              <KVRow label="+ PO (open)" value={fmtInt(rec.outstanding_po)} />
            ) : null}
            {/* The two channels the demand actually came from, when the run froze the
                split. A row that carries neither (a legacy run) states the one total it
                does have rather than two figures it never measured. */}
            {rec.project_committed == null && rec.retail_committed == null ? (
              <KVRow label="- SO (outstanding)" value={fmtInt(rec.outstanding_sales ?? 0)} />
            ) : (
              <>
                <KVRow label="- Project demand" value={fmtInt(rec.project_committed ?? 0)} />
                <KVRow label="- Retail demand" value={fmtInt(rec.retail_committed ?? 0)} />
              </>
            )}
            <div className="mt-1 flex justify-between border-t pt-1 text-xs font-medium">
              <span>Net</span>
              <span className="tabular-nums">{fmtSigned(line.net)}</span>
            </div>
          </div>
          {manual ? <ManualLevelLine rec={rec} /> : null}

          {showLineStatus ? (
            <>
              {/* The line reads ABOVE its own basis - the old "Gap to line" figure here was
                  never a shortfall, it was the engine's own covered committed demand, and
                  labelling it "gap" read as a bug (net 135, level 120, "gap" 15?). */}
              <div className="border-t px-3 py-2">
                <p className="text-xs text-muted-foreground">
                  {`Line not breached - net ${fmtSigned(line.net)} above ${breach.basisLabel} ${fmtInt(
                    breach.basisValue,
                  )}`}
                </p>
              </div>
              <div className="border-t px-3 py-2">
                <p className="text-xs text-muted-foreground">
                  {`Committed demand ${
                    rec.covered_committed == null ? EM_DASH : fmtInt(rec.covered_committed)
                  }, covered by stock`}
                </p>
              </div>
            </>
          ) : (
            <div className="border-t px-3 py-2">
              <KVRow
                label="Gap to line"
                value={rec.recommended_qty == null ? EM_DASH : fmtInt(rec.recommended_qty)}
              />
            </div>
          )}

          {manual ? null : (
            <>
          <SectionLabel>Cover before buying</SectionLabel>
          <div className="space-y-2 px-3 py-2">
            {noCoverAvailable ? (
              <p className="text-xs text-muted-foreground">
                No cover available - full quantity must be bought
              </p>
            ) : (
              <>
                {/* S - the row's own site pool, stated in the order the need consumes it
                    (own stock, then a borrow, then the open PO, then the buy). A FACT, not
                    a toggle: the engine already spent it inside the net, so switching it
                    off would be asking to buy what we are holding. */}
                {stockPart > 0 ? (
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="min-w-0 truncate text-muted-foreground">Own stock</span>
                    <span className="tabular-nums">{fmtInt(stockPart)}</span>
                  </div>
                ) : null}
                {cover.coverQty > 0 ? (
                  <div className="space-y-1.5">
                    <ToggleLine
                      checked={stockOn}
                      onToggle={toggleStock}
                      label={
                        stockOn
                          ? `Use stock ${fmtInt(edited.coverQty)}`
                          : `Use stock ${fmtInt(cover.coverQty)} (${cover.sources
                              .map((s) => s.warehouse_code)
                              .join(', ')})`
                      }
                    />
                    {/* On, the sources stop being a sentence and become the edit surface: one
                        row per location the policy allows this row to draw on. Off, they are
                        not offered at all - cover is 0 and the whole gap is bought. */}
                    {stockOn ? (
                      <div className="ms-6 space-y-1">
                        {cover.offered.map((s) => (
                          <CoverSourceLine
                            key={s.warehouse_id}
                            code={s.warehouse_code}
                            free={s.qty}
                            max={maxSourceEdit(cover, s.warehouse_id, sourceEdits)}
                            value={sourceEdits[s.warehouse_id] ?? 0}
                            onCommit={(v) => changeSource(s.warehouse_id, v)}
                          />
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {poQty > 0 ? (
                  <ToggleLine
                    checked={poOn}
                    onToggle={togglePo}
                    label={`Use PO ${poLabel} ${fmtInt(poQty)}`}
                    title={describePoBook(poReceipts).join('\n')}
                  />
                ) : null}
              </>
            )}
            {/* SPO is inside Net already (Block 1) - never re-offered as a choice, only
                restated so the buyer sees where it went (UAC B3). Shown regardless of
                whether there is any cover to toggle, since it is a fact, not a part. */}
            {(rec.incoming_spo ?? 0) > 0 ? (
              <p className="text-2xs text-muted-foreground">
                {`SPO arriving ${fmtInt(rec.incoming_spo ?? 0)} - already counted`}
              </p>
            ) : null}
          </div>
            </>
          )}

          <SectionLabel>The buy</SectionLabel>
          <div className="space-y-2 px-3 py-2">
            {addOn && addOn.qty > 0 ? (
              <ForecastAddOnLine
                checked={forecastOn}
                onToggle={toggleForecast}
                value={forecastQty}
                onCommit={changeForecast}
                onValidityChange={(valid) => setForecastInvalid(!valid)}
                showMessage={forecastRefused}
                label={forecastOn ? `Added ${fmtInt(forecastQty)}` : `Add ${fmtInt(forecastQty)}`}
                cap={forecastCap}
                detail={addOnDetail(addOn)}
              />
            ) : null}
            {/* A falling trend can eat the whole flat proposal (floored at 0) - stated
                rather than silently offering nothing, so the buyer sees WHY there is no
                add-on to opt into here (Fix3). */}
            {addOn && addOn.qty === 0 ? (
              <p className="text-xs text-muted-foreground">Orders falling - no add-on proposed</p>
            ) : null}
            {collapsed ? (
              <p className="text-xs text-muted-foreground">Nothing to buy - MoQ not relevant</p>
            ) : (
              <>
                <KVRow label="Buy before rounding" value={fmtInt(buyBeforeRounding)} />
                <div className="flex justify-between border-t pt-1 text-xs font-medium">
                  <span>MoQ / order multiple</span>
                  <span className="tabular-nums">{fmtInt(buyRounded)}</span>
                </div>
                {gap ? (
                  <p
                    className={`text-2xs ${gap.verdict === 'clears' ? 'text-muted-foreground' : 'text-scm-overstock'}`}
                  >
                    {moqGapNote(gap, fmtInt)}
                  </p>
                ) : null}
              </>
            )}
            {/* Who it comes from, what it costs, and what it draws from the budget - the
                three facts a buyer needs beside the quantity to place the order (AC-R8).
                The supplier's price and the cash figure are deliberately different money:
                the budget is one pot of ringgit. */}
            <div className="space-y-0.5 border-t pt-1.5 text-2xs text-muted-foreground">
              <div className="flex justify-between gap-3">
                <span>Supplier</span>
                <span
                  className="truncate text-end"
                  title={rec.supplier?.supplier_name ?? undefined}
                >
                  {rec.supplier?.supplier_name ?? rec.supplier?.supplier_code ?? EM_DASH}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span>Price</span>
                <span className="tabular-nums">
                  {rec.unit_cost == null ? EM_DASH : fmtSupplierCost(rec.unit_cost, rec.currency)}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span>Cash</span>
                <span className="tabular-nums">
                  {rec.cash_impact == null ? EM_DASH : fmtMoney(rec.cash_impact)}
                </span>
              </div>
            </div>
          </div>

          {/* Last, because it is context rather than a reason: what we have actually
              bought of this item lately, and from whom (AC-R8). */}
          <SectionLabel>History</SectionLabel>
          <div className="space-y-1 px-3 py-2 text-xs">
            {!purchaseTrend && purchaseTrendReady === false ? (
              <p className="text-muted-foreground">Loading purchases...</p>
            ) : (
              <>
                <p className="text-muted-foreground">
                  {describePurchaseTrend(purchaseTrend, purchaseWindowMonths)}
                </p>
                <RecentPurchasesTable lines={purchaseTrend?.lines ?? []} />
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
