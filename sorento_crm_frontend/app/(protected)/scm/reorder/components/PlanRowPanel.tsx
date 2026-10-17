'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import type { ApexOptions } from 'apexcharts';
import { TrendingDown } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { LiveNumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { EM_DASH, fmtDecimal, fmtInt, fmtSupplierCost } from '../../lib/format';
import { applySourceEdits, sourceEditsForTotal, type CoverProposal } from '../lib/coverPlan';
import { lineCost, type LineCostMoney } from '../lib/lineCost';
import { composeMixture, roundBuyQty } from '../lib/orderQtyLedger';
import type { PlanLine } from '../lib/planLine';
import type { PlanDecision } from '../lib/planDecisions';
import type { PlanRowEdit } from '../lib/planEdits';
import {
  describeCheaper,
  describeLastPurchase,
  humanAge,
  type CheaperAlternative,
  type PriceAdvice,
} from '../lib/priceAdvice';
import {
  effectiveLevel,
  levelActionLabel,
  levelTerms,
  type LevelSuggestion,
} from '../lib/levelSuggestion';
import { healthVerdict, suggestedLifecycle, type ProductEconomics } from '../lib/productHealth';
import type { PoReceipt } from '../lib/poCover';
import type { PlanRowPriceMode } from '../types/decisions.types';
import { distinctName } from '@/lib/product-display';

const ApexChart = dynamic(() => import('react-apexcharts').then((mod) => mod.default), {
  ssr: false,
});

/**
 * The whole decision for one product, in the row that asks it (plan 4.4).
 *
 * Four zones on one strip: how the shortage is covered, what it costs and from whom, the
 * AutoCount level and quantity to key back, and whether the product should still be sold.
 * They used to be five separate cells with five separate popovers, each writing to the
 * backend the moment it closed - so a buyer changing their mind produced four requests and
 * the row's own numbers moved under them mid-thought.
 *
 * Every control calls `onEdit`, which lands in the page's draft map; the pill turns
 * Unsaved. The toolbar's Save (N) persists every drafted row at once; `onSave` (S12,
 * round 2, 9 Sep - was "Use suggestion", which reset the draft to the engine's own
 * mixture) persists just THIS row's draft the moment the buyer is done with it, without
 * waiting on the rest of the plan. The per-location stock table that used to live here
 * moved into the On hand lightbox (R12) and SPO is stated as a fact rather than offered as
 * an input (R2) - it is already inside the net, so a "take SPO" quantity would count it
 * twice.
 */
export function PlanRowPanel({
  line,
  edit,
  decision,
  cover,
  poReceipts = [],
  price,
  cheaper = null,
  levelSuggestion,
  economics,
  healthWindows,
  disabled = false,
  saving = false,
  lockReason = null,
  onEdit,
  onSave,
}: {
  line: PlanLine;
  /** The unsaved draft on this row, when there is one. */
  edit: PlanRowEdit | undefined;
  /** What is already persisted for it. */
  decision: PlanDecision | undefined;
  cover: CoverProposal;
  poReceipts?: PoReceipt[];
  price: PriceAdvice | undefined;
  cheaper?: CheaperAlternative | null;
  levelSuggestion: LevelSuggestion | undefined;
  economics: ProductEconomics | undefined;
  healthWindows?: { sold_window_months?: number; bought_window_months?: number };
  /** A legacy run: the panel still renders, every input is dead (D8). */
  disabled?: boolean;
  /** THIS row's own Save is in flight (review fix round 2, 9 Sep) - disables just the
   *  Save button, never the rest of the panel, and stops a second click from firing a
   *  second PUT while the first is still on the wire. */
  saving?: boolean;
  lockReason?: string | null;
  onEdit: (patch: PlanRowEdit) => void;
  /** Persist THIS row's current draft now, rather than waiting for the toolbar's Save.
   *  `pendingPatch` (AC-S12.5) carries an un-blurred Buy value straight into the save
   *  that is about to fire - see `pendingBuyPatch` below for why this cannot instead go
   *  through `onEdit` first and `onSave` second as two separate calls. */
  onSave: (pendingPatch?: PlanRowEdit) => void;
}) {
  const [chartOpen, setChartOpen] = useState(false);

  // ONE FORMULA (PLAN-reorder-one-formula.md, AC-8). The need is reconstructed off the
  // line's own frozen fields - `need = recommended_qty + on hand + PO`, exactly as
  // `suggestedDecisionFor` does - and the parts are measured against THAT, never against
  // `order_qty`, which the engine has already netted once. A `rawBuy` of 0 or less (a
  // covered row) reads no mixture at all, matching "Nothing" everywhere else.
  //
  // Three different things, kept apart:
  //
  // - S (`stockPart`) is the row's OWN site-pool stock the need consumes. A FACT, shown
  //   read-only and NEVER persisted: the engine already spent it inside the net, and a
  //   `stock` part with no `stock_takes` to name it is refused by the server anyway
  //   ("a mixture needs more than one part").
  // - the BORROW (`cover`) is cross-location stock this row's net does NOT contain. Only
  //   a warehouse-grain row has any (`coverForLine` offers a product-grain row none), and
  //   it IS a decision - it is what `PlanDecision.stock` means.
  // - P is the open PO the buyer TRUSTS. Prefilled at what the need consumes and editable
  //   down ("do not trust that PO"), which raises Buy by the same amount.
  //
  // PO is read off the panel's own LIVE receipts fetch rather than the frozen
  // `outstanding_po` snapshot - the receipts drill this same panel opens is the fresher
  // figure, and the two parts still sum back to the need either way.
  const onHand = line.rec.on_hand ?? 0;
  const poReceiptsQty = poReceipts.reduce((t, r) => t + r.remaining, 0);
  const rawBuy = line.rec.recommended_qty ?? line.order_qty;
  const need = rawBuy > 0 ? rawBuy + onHand + poReceiptsQty : 0;
  const stockPart = Math.min(onHand, need);
  const rounding = line.order_qty_inputs;
  const suggestedMix = composeMixture({
    need,
    stockQty: stockPart,
    borrowedQty: cover.coverQty,
    poQty: poReceiptsQty,
  });
  const suggested: PlanDecision = {
    ...(roundBuyQty(suggestedMix.buy, rounding) > 0
      ? { buy: roundBuyQty(suggestedMix.buy, rounding) }
      : {}),
    ...(suggestedMix.borrowedQty > 0
      ? {
          stock: {
            qty: suggestedMix.borrowedQty,
            sources: cover.sources.map((s) => ({
              warehouse_id: s.warehouse_id,
              warehouse_code: s.warehouse_code,
              qty: s.qty,
            })),
          },
        }
      : {}),
    ...(suggestedMix.usePo > 0 ? { po: suggestedMix.usePo } : {}),
  };
  // What the inputs READ: the draft first, then what is persisted, then the engine.
  const current: PlanDecision = edit?.decision ?? decision ?? suggested;
  const borrowMax = cover.coverQty + cover.buyQty > 0 ? cover.coverQty : 0;
  const poMax = poReceiptsQty;
  const skipped = Boolean(current.skip);

  /** The BORROW the draft currently holds, and where from (R18). */
  const borrowQty = current.stock?.qty ?? 0;
  const borrowSources = current.stock?.sources ?? [];
  /** Which location the borrow comes out of, named rather than labelled "stock": one
   *  offered pool is the normal case, and the per-source split is stated below it when
   *  there is more than one. */
  const borrowLabel =
    cover.offered.length === 1 ? cover.offered[0].warehouse_code : 'Borrow';
  const poQty = current.po ?? 0;
  const buyQty = current.buy ?? 0;

  const moq = line.order_qty_inputs.moq;
  const masterMoq = line.order_qty_inputs.master_moq;
  // S13 (round 2, 9 Sep): when the row carries neither a buyer override nor a frozen
  // master figure, fall back to the remembered product-supplier link (`rec.supplier.moq`)
  // before giving up - a just-remembered MOQ (AC-S13.1) shows here immediately, on the
  // SAME plan, rather than waiting on the next run to freeze it onto `master_moq`.
  const moqValue =
    edit?.moq !== undefined ? edit.moq : (moq ?? line.rec.supplier?.moq ?? null);

  const hasPriceOnFile = Boolean(price?.last) || (line.unit_cost ?? 0) > 0;
  // Never purchased means there is no last price to use, so the row starts on the only
  // honest answer: go and get one (D4). Anything the buyer or a previous save said still
  // wins over it.
  const priceMode: PlanRowPriceMode =
    edit?.priceMode ??
    current.priceMode ??
    decision?.priceMode ??
    (hasPriceOnFile ? 'use_last' : 'ask_new');
  // Clearing the select is "no override", which now falls back to the LAST PURCHASE
  // supplier when this row's history names one (S11, round 2, 9 Sep) - not the engine's
  // default link, which is only a candidate ranking and can go stale (measured:
  // SRTSS8710's default link priced at RM 121.80 while the last purchase actually paid
  // CNY 48.00 to a different supplier). Absent that, the engine's own pick is still the
  // fallback, and an empty value sends no `supplier_code` on save either way.
  const supplierCode =
    edit?.supplierCode ||
    current.supplierCode ||
    line.rec.last_purchase_supplier_code ||
    line.supplier?.code ||
    null;
  const picked = (line.alternatives ?? []).find((a) => a.value === supplierCode) ?? null;

  // S11 (finding 4, review fix round 2, 9 Sep): the row's TRUE last purchase, whoever it
  // was bought from - the SAME fact the "Last price" line below prints and the figure
  // "at last price" actually names. ONE source, so the two never disagree: the loaded
  // price-advice purchase (`price.last`, keyed to the currently chosen supplier) when it
  // has arrived, else the frozen rec's own `last_purchase` fields (the pool/segment-
  // attributed fact every row is built from before the price-advice fetch resolves).
  const lastPurchase: LineCostMoney = price?.last
    ? { unit_cost: price.last.unit_cost, currency: price.last.currency }
    : { unit_cost: line.rec.last_purchase_cost, currency: line.rec.last_purchase_currency };
  const hasLastPurchase = lastPurchase.unit_cost !== null && lastPurchase.unit_cost !== undefined;
  // The chosen supplier's own quote, in ITS OWN currency - the "at supplier price" basis,
  // used only when there is no last price to cost against or a fresh quote was asked for.
  const supplierQuote: LineCostMoney =
    picked && picked.value !== line.supplier?.code
      ? { unit_cost: picked.unit_cost, currency: picked.currency }
      : { unit_cost: line.unit_cost, currency: line.currency };
  const cost = lineCost({ buyQty, priceMode, last: lastPurchase, supplier: supplierQuote });

  /** Write a whole mixture back to the draft, keeping the buyer's price call with it. */
  const setDecision = (next: PlanDecision) => onEdit({ decision: { ...next } });

  const num = (v: string) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  };

  /** Re-run the one formula for a changed part, and write the Buy it leaves behind. The
   *  buyer moves a part, the remainder moves with it - they never have to do the
   *  subtraction themselves, and the row can never record parts that do not sum. */
  const recompose = (next: { borrowedQty: number; poQty: number }, sources: PlanDecision['stock']) => {
    const m = composeMixture({ need, stockQty: stockPart, ...next });
    setDecision({
      ...current,
      skip: undefined,
      stock: m.borrowedQty > 0 ? sources : undefined,
      po: m.usePo,
      buy: roundBuyQty(m.buy, rounding),
    });
  };

  const setStock = (raw: string) => {
    // The per-bin split is scaled from the FRONT: the nearest bins ranked first, so they are
    // the ones kept when the buyer takes less than offered. Same helper the ledger's own
    // per-location inputs use, so a total typed here and quantities typed there agree.
    const edited = applySourceEdits(
      cover,
      sourceEditsForTotal(cover, Math.min(num(raw), borrowMax)),
    );
    recompose(
      { borrowedQty: edited.coverQty, poQty },
      edited.coverQty > 0
        ? {
            qty: edited.coverQty,
            sources: edited.sources.map((s) => ({
              warehouse_id: s.warehouse_id,
              warehouse_code: s.warehouse_code,
              qty: s.qty,
            })),
          }
        : undefined,
    );
  };

  const setPo = (raw: string) =>
    recompose({ borrowedQty: borrowQty, poQty: Math.min(num(raw), poMax) }, current.stock);

  const [buyDraft, setBuyDraft] = useState<string | null>(null);
  // A typed Buy that is blank or not a whole number is told, never read as 0: it is not
  // applied and Save waits for a figure.
  const [buyTried, setBuyTried] = useState(false);
  const buyError =
    buyDraft === null ? undefined : numberFieldError(buyDraft, { min: 0, integer: true });
  /** The Buy patch `commitBuy` would apply, computed but NOT applied - Save reads this
   *  to flush an un-blurred typed value into the very save it is about to fire (AC-S12.5,
   *  review fix round 3), rather than waiting on `onEdit`'s own state update, which would
   *  not be visible to `usePlanEdits.saveRow` until the NEXT render. */
  const pendingBuyPatch = (): PlanRowEdit | undefined =>
    buyDraft === null
      ? undefined
      : {
          // The supplier's MoQ and order multiple do not stop applying because the
          // figure was typed by hand - a buy is rounded wherever it is recorded.
          decision: { ...current, skip: undefined, buy: roundBuyQty(num(buyDraft), rounding) },
        };
  const commitBuy = () => {
    const patch = pendingBuyPatch();
    if (!patch) return;
    if (buyError) {
      setBuyTried(true);
      return;
    }
    setBuyDraft(null);
    setBuyTried(false);
    onEdit(patch);
  };

  const level = levelSuggestion;
  const levelAction = level ? levelActionLabel(level) : null;
  const levelValue =
    edit?.level !== undefined
      ? edit.level
      : level
        ? effectiveLevel(level)
        : (line.rec.reorder_level ?? line.rec.master_reorder_level ?? null);
  // The draft, then what the buyer last SAVED (R5), then AutoCount's own master figure.
  // Without the middle term a saved quantity vanished on the next refetch, because the
  // master column is only rewritten by an AutoCount upload.
  const reorderQtyValue =
    edit?.reorderQty !== undefined
      ? edit.reorderQty
      : (level?.reorder_qty ??
         level?.master_reorder_quantity ??
         line.rec.master_reorder_quantity ??
         null);

  const health = healthVerdict(economics, healthWindows);
  // AC-S8.1 (G4 ruling, 9 Sep 2026): a stored decision always wins; short of one, the
  // radio preselects from the health class rather than sitting with neither option
  // checked - Dead suggests Discontinue, everything else suggests Keep selling.
  const lifecycle =
    edit?.lifecycle ?? economics?.lifecycle_decision ?? suggestedLifecycle(economics?.movement_class);

  const months = level?.basis.months ?? [];

  const pin = useVisibleWidth();

  return (
    // The expanded cell spans the whole TABLE, which is wider than the viewport whenever
    // the grid scrolls - so the fourth zone (Product health) sat past the right edge and
    // reading it meant scrolling the grid sideways and losing the row it belongs to.
    // Pinning the panel to the scroll container's own visible width puts all four zones on
    // screen at once at 1280 and gives 375 a panel that stays put while the columns move.
    <div
      ref={pin.ref}
      className="sticky left-0 border-t bg-muted px-5 py-4"
      style={pin.width ? { width: pin.width } : undefined}
    >
      {lockReason ? (
        <p className="mb-3 text-xs font-medium text-muted-foreground">{lockReason}</p>
      ) : null}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-4">
        {/* ---- 1. Cover ------------------------------------------------------ */}
        <section className="min-w-0 space-y-2">
          <ZoneTitle>Cover</ZoneTitle>

          {/* S - a FACT, never an input. The row's own pool is already inside the net the
              engine sized against, so a quantity here would net it a second time. */}
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate text-muted-foreground">BRW</span>
            <span className="tabular-nums">{fmtInt(stockPart)}</span>
          </div>
          {/* The BORROW: cross-location stock this row's own net does NOT contain, named
              by where it comes from. A product-grain row is offered none (its on hand
              already sums every in-scope pool), so the row is simply absent there. */}
          {borrowMax > 0 ? (
            <NumberField
              label={borrowLabel}
              value={borrowQty}
              max={borrowMax}
              disabled={disabled}
              onChange={setStock}
            />
          ) : null}
          {/* WHICH pools the units come out of, once there is more than one (R18). One
              source needs no split: the input's own max already says how many. Only site
              pools reach here - a project bin is never a source. */}
          {borrowSources.length > 1 ? (
            <p className="text-2xs text-muted-foreground">
              {borrowSources
                .map((src) => `${src.warehouse_code} ${fmtInt(src.qty)}`)
                .join(' + ')}
            </p>
          ) : null}
          <NumberField
            label="PO"
            value={poQty}
            max={poMax}
            disabled={disabled || poMax <= 0}
            onChange={setPo}
          />
          <label className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate text-muted-foreground">Buy</span>
            <NumberInput
              min={0}
              inputMode="numeric"
              aria-label="Units to buy"
              className="h-7 w-24 text-right tabular-nums"
              errorClassName="basis-full text-end"
              error={buyTried ? buyError : undefined}
              disabled={disabled}
              value={buyDraft ?? String(buyQty)}
              onChange={(e) => setBuyDraft(e.target.value)}
              onBlur={commitBuy}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur();
              }}
            />
          </label>

          {/* R2: a fact, never an input. It is already inside the net and Available. */}
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate text-muted-foreground">SPO</span>
            <span className="tabular-nums">{fmtInt(line.rec.incoming_spo ?? 0)}</span>
          </div>

          <label className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate text-muted-foreground">
              MOQ
              {masterMoq === null || masterMoq === undefined ? null : (
                <span className="ms-1 text-2xs">{`master ${fmtInt(masterMoq)}`}</span>
              )}
            </span>
            <LiveNumberInput
              min={0}
              inputMode="numeric"
              aria-label="MOQ"
              className="h-7 w-24 text-right tabular-nums"
              errorClassName="basis-full text-end"
              disabled={disabled}
              placeholder={masterMoq === null ? undefined : String(masterMoq)}
              rules={{ min: 0, integer: true, optional: true }}
              value={moqValue ?? null}
              onCommit={(figure) => onEdit({ moq: figure })}
              onClear={() => onEdit({ moq: null })}
            />
          </label>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button
              size="sm"
              variant="outline"
              className="h-7"
              disabled={disabled || saving}
              onClick={() => {
                // AC-S12.5: an un-blurred Buy value is flushed straight into this save
                // rather than left stranded in local state - `commitBuy`'s own onBlur
                // still runs too on whichever browser event order gets there first, but
                // Save must not depend on blur having already happened.
                if (buyError) {
                  setBuyTried(true);
                  return;
                }
                setBuyDraft(null);
                onSave(pendingBuyPatch());
              }}
            >
              {saving ? 'Saving...' : 'Save'}
            </Button>
            <Button
              size="sm"
              variant={skipped ? 'primary' : 'ghost'}
              className="h-7"
              disabled={disabled}
              onClick={() => setDecision({ skip: true })}
            >
              {skipped ? 'Skipped' : 'Skip'}
            </Button>
          </div>
        </section>

        {/* ---- 2. Price and supplier ---------------------------------------- */}
        <section className="min-w-0 space-y-2">
          <ZoneTitle>Price and supplier</ZoneTitle>

          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="text-muted-foreground">Last price</span>
            <span className="min-w-0 text-end">
              {hasLastPurchase ? (
                <span className="tabular-nums font-medium">
                  {/* AC-S2.3/AC-S11.2 (finding 4): the SAME `lastPurchase` fact the line
                      cost below costs against - never the chosen supplier's own quote,
                      which is a different fact and, for an item never purchased, used to
                      print here mislabelled as a price we had actually paid. */}
                  {fmtSupplierCost(lastPurchase.unit_cost, lastPurchase.currency)}
                </span>
              ) : (
                <span className="text-muted-foreground">No price on file</span>
              )}
            </span>
          </div>
          {price?.last ? (
            <p className="text-2xs text-muted-foreground">
              {describeLastPurchase(price.last)}
              {price.age_days != null ? ` (${humanAge(price.age_days)})` : ''}
            </p>
          ) : null}

          <div className="space-y-1">
            <Label className="text-2xs text-muted-foreground">Last supplier</Label>
            <SearchableSelect
              size="sm"
              value={supplierCode ?? ''}
              onChange={(code) => onEdit({ supplierCode: code })}
              options={(line.alternatives ?? []).map((a) => ({
                value: a.value,
                label: a.label,
                description: `${fmtSupplierCost(a.unit_cost, a.currency)}${
                  a.lead_time_days > 0 ? `, ${a.lead_time_days} day lead` : ''
                }`,
              }))}
              placeholder="Choose a supplier"
              emptyMessage="No supplier is linked to this product."
              clearable
              disabled={disabled || (line.alternatives ?? []).length === 0}
              wrapOptions
              triggerClassName="h-7 text-2xs"
            />
          </div>

          {/* The shortlist ranking is demoted to this one line (R6): the price the row is
              costed at is always what we last paid, and a cheaper name is advice, not a swap. */}
          {cheaper ? (
            <p className="flex items-start gap-1 text-2xs text-amber-600">
              <TrendingDown className="mt-0.5 size-3 shrink-0" aria-hidden />
              <span>{`Cheaper on file: ${describeCheaper(cheaper)}`}</span>
            </p>
          ) : null}

          <RadioGroup
            className="flex flex-wrap gap-3 pt-1"
            value={priceMode}
            disabled={disabled}
            onValueChange={(v) => onEdit({ priceMode: v as PlanRowPriceMode })}
          >
            <label className="flex items-center gap-1.5 text-xs">
              <RadioGroupItem value="use_last" id={`price-last-${line.id}`} />
              <span>Use last price</span>
            </label>
            <label className="flex items-center gap-1.5 text-xs">
              <RadioGroupItem value="ask_new" id={`price-new-${line.id}`} />
              <span>Get new price</span>
            </label>
          </RadioGroup>

          {/* S11 (round 2, 9 Sep): read in the purchase's OWN currency, never converted -
              "at last price" names the row's true last purchase, "at supplier price" the
              chosen supplier's own quote, and nothing is claimed when neither exists. The
              "No MYR rate" hint this replaced is retired (AC-S11.3): most buying is CNY,
              and there was never a market rate to ask for in the first place. */}
          <p className="text-xs">
            <span className="text-muted-foreground">Line cost </span>
            <span className="tabular-nums font-medium">
              {cost ? fmtSupplierCost(cost.amount, cost.currency) : EM_DASH}
            </span>
            {cost ? (
              <span className="text-2xs text-muted-foreground">
                {cost.basis === 'last' ? ' at last price' : ' at supplier price'}
              </span>
            ) : null}
          </p>
        </section>

        {/* ---- 3. AutoCount level + qty ------------------------------------- */}
        <section className="min-w-0 space-y-2">
          <ZoneTitle>AutoCount level + qty</ZoneTitle>

          {levelAction ? (
            <div className="space-y-0.5">
              <Badge variant={levelAction.changed ? 'info' : 'success'} appearance="light" size="sm">
                {levelAction.label}
              </Badge>
              <p className="truncate text-2xs text-muted-foreground" title={levelAction.detail}>
                {levelAction.detail}
              </p>
            </div>
          ) : (
            <p className="text-2xs text-muted-foreground">No level suggestion for this item.</p>
          )}

          {/* A level is AMENDED, never set from nothing: the backend refuses an amendment
              with no suggestion to amend ("There is no suggestion to amend for this
              item"), and a live input whose save can only 422 is worse than a dead one.
              The reorder quantity beside it has no such rule and stays editable. */}
          <label className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="text-muted-foreground">Level</span>
            <LiveNumberInput
              min={0}
              inputMode="numeric"
              aria-label="AutoCount level"
              className="h-7 w-24 text-right tabular-nums"
              errorClassName="basis-full text-end"
              disabled={disabled || !level}
              rules={{ min: 0, integer: true, optional: true }}
              value={levelValue ?? null}
              onCommit={(figure) => onEdit({ level: figure })}
              onClear={() => onEdit({ level: null })}
            />
          </label>
          <label className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="text-muted-foreground">Reorder qty</span>
            <LiveNumberInput
              min={0}
              inputMode="numeric"
              aria-label="AutoCount reorder qty"
              className="h-7 w-24 text-right tabular-nums"
              errorClassName="basis-full text-end"
              disabled={disabled}
              rules={{ min: 0, integer: true, optional: true }}
              value={reorderQtyValue ?? null}
              onCommit={(figure) => onEdit({ reorderQty: figure })}
              onClear={() => onEdit({ reorderQty: null })}
            />
          </label>

          {level ? (
            <p className="text-2xs text-muted-foreground">
              {levelTerms(level).map((t) => `${t.label} ${t.value}`).join(' · ')}
            </p>
          ) : null}
          {months.length ? (
            <button
              type="button"
              className="text-2xs font-medium text-primary underline-offset-2 hover:underline"
              onClick={() => setChartOpen(true)}
            >
              {`${fmtInt(months.length)}-month chart`}
            </button>
          ) : null}
        </section>

        {/* ---- 4. Product health -------------------------------------------- */}
        <section className="min-w-0 space-y-2">
          <ZoneTitle>Product health</ZoneTitle>

          {health ? (
            <>
              <Badge variant={health.tone} appearance="light" size="sm">
                {health.label}
              </Badge>
              <RadioGroup
                className="flex flex-wrap gap-3"
                value={lifecycle ?? ''}
                disabled={disabled || !economics}
                onValueChange={(v) => onEdit({ lifecycle: v as 'keep' | 'discontinue' })}
              >
                <label className="flex items-center gap-1.5 text-xs">
                  <RadioGroupItem value="keep" id={`health-keep-${line.id}`} />
                  <span>Keep selling</span>
                </label>
                <label className="flex items-center gap-1.5 text-xs">
                  <RadioGroupItem value="discontinue" id={`health-stop-${line.id}`} />
                  <span>Discontinue</span>
                </label>
              </RadioGroup>
              <ul className="space-y-0.5 text-2xs text-muted-foreground">
                {health.factors.map((f) => (
                  <li key={f} className="truncate" title={f}>
                    {f}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-2xs text-muted-foreground">
              No movement on file for this product.
            </p>
          )}
        </section>
      </div>

      {/* The evidence behind the level, moved behind a link so the panel stays one screen
          tall at 1280 (plan 4.4). */}
      <Dialog open={chartOpen} onOpenChange={setChartOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{`What left ${line.sku} each month`}</DialogTitle>
            <DialogDescription className="truncate" title={distinctName(line.sku, line.product_name) ?? undefined}>
              {distinctName(line.sku, line.product_name)}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {months.length ? (
              <ApexChart
                options={{
                  chart: { type: 'bar', toolbar: { show: false } },
                  plotOptions: { bar: { columnWidth: '55%', borderRadius: 2 } },
                  colors: ['var(--color-primary, #2563eb)'],
                  dataLabels: { enabled: true, style: { fontSize: '10px' } },
                  xaxis: { categories: months.map((m) => m.month) },
                  yaxis: { labels: { show: false } },
                  grid: { show: false },
                  tooltip: { enabled: false },
                } satisfies ApexOptions}
                series={[{ name: 'Left this location', data: months.map((m) => m.qty) }]}
                type="bar"
                height={240}
              />
            ) : (
              <p className="text-sm text-muted-foreground">
                No monthly movement behind this suggestion.
              </p>
            )}
            {level ? (
              <p className="mt-3 border-t pt-2 text-2xs text-muted-foreground">
                {`Average ${fmtDecimal(level.basis.adu, 3)} a day over ${fmtInt(level.basis.window_days)} days. Applying the level happens in AutoCount.`}
              </p>
            ) : null}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ZoneTitle({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </h4>
  );
}

/** One capped quantity input. The cap is SHOWN, never only enforced: an input that silently
 *  clamps reads as broken. */
function NumberField({
  label,
  hint,
  value,
  max,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  value: number;
  max: number;
  disabled?: boolean;
  onChange: (raw: string) => void;
}) {
  return (
    <label className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <span
        className="min-w-0 truncate text-muted-foreground"
        title={hint ? `${label} (${hint})` : label}
      >
        {label}
        {hint ? <span className="ms-1 text-2xs">{`(${hint})`}</span> : null}
      </span>
      <LiveNumberInput
        min={0}
        max={max}
        inputMode="numeric"
        aria-label={label}
        className="h-7 w-24 text-right tabular-nums"
        errorClassName="basis-full text-end"
        disabled={disabled}
        rules={{ min: 0, max, integer: true }}
        value={value}
        onCommit={(figure) => onChange(String(figure))}
      />
    </label>
  );
}


/**
 * The width of the nearest horizontally-scrolling ancestor, so a full-width row can be
 * pinned to what the reader can actually SEE rather than to the table's own width.
 *
 * A `colSpan` cell is as wide as every column together, and this grid is `width: fixed`
 * with eleven of them - so at any viewport narrower than the table, content at the right
 * of the panel is simply off screen. `position: sticky; left: 0` keeps it in place while
 * the columns scroll under it, and the measured width is what stops it stretching to the
 * table's width again.
 *
 * Measured rather than assumed: the sidebar collapses, the window resizes, and a hard-coded
 * breakpoint would be wrong in exactly the cases this exists for.
 */
function useVisibleWidth(): { ref: (node: HTMLDivElement | null) => void; width: number | null } {
  const [width, setWidth] = useState<number | null>(null);
  const observed = useRef<ResizeObserver | null>(null);

  const ref = useCallback((node: HTMLDivElement | null) => {
    observed.current?.disconnect();
    observed.current = null;
    if (!node) return;
    let parent: HTMLElement | null = node.parentElement;
    while (parent) {
      const overflow = getComputedStyle(parent).overflowX;
      if (overflow === 'auto' || overflow === 'scroll') break;
      parent = parent.parentElement;
    }
    if (!parent) return;
    const measure = () => setWidth(parent.clientWidth || null);
    measure();
    // jsdom has no ResizeObserver; the measurement above still runs, which is all a
    // component test needs.
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    observed.current = observer;
  }, []);

  useEffect(() => () => observed.current?.disconnect(), []);

  return { ref, width };
}
