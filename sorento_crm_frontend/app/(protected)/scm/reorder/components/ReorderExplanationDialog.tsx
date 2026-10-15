'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Popover as PopoverPrimitive } from 'radix-ui';
import {
  AlertTriangle,
  ArrowRightLeft,
  Ban,
  ChevronDown,
  ExternalLink,
  Layers,
  PackageX,
  SendHorizontal,
  ShoppingCart,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
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
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import RecordNavigation from '@/components/common/RecordNavigation';
import { cn } from '@/lib/utils';
import {
  useAskRecommendation,
  useRecommendationAdvisory,
  useRecommendationExplanation,
} from '../hooks/useExplainer';
import { REFUSAL } from '../lib/explainerMockStore';
import { CoverageTimelinePanel } from './CoverageTimelinePanel';
import type { AskTurn } from '../types/explainer.types';
import { xyzLabel } from '../../lib/health';
import type { XyzClass } from '../../types/scm.types';
import {
  BASE_CURRENCY,
  EM_DASH,
  fmtDate,
  fmtDecimal,
  fmtInt,
  fmtMoney,
  fmtPct,
  fmtSupplierCost,
  fmtTrimmedDecimal,
} from '../../lib/format';
import { ConfidenceBadge } from '../../components/HealthIndicators';
import type {
  RankFactor,
  ReorderRecommendation,
  SupplierChoice,
} from '../types/reorder.types';
import { distinctName } from '@/lib/product-display';

/**
 * Row-click explanation popup - the headline "make it fool-proof" surface.
 *
 * Turns a recommendation's FROZEN inputs (AC-M3.11) into a plain-language,
 * step-by-step derivation a supply-chain novice can follow: it never recomputes
 * on the client, only presents the numbers the deterministic engine already
 * stored (this is NOT the M5 LLM narrator). Every metric is spelled out, the
 * arithmetic is made explicit (ROP = demand × lead time + safety stock, order
 * qty = order-up-to − net), and the modal is mobile-scrollable.
 */

/** Compact number for the arithmetic - trims trailing zeros so 11.0 reads "11". */
function dec(v: number | null | undefined): string {
  return fmtTrimmedDecimal(v, 2);
}

const REASON_NOUN: Record<string, string> = {
  reorder_point: 'reorder point',
  min_max: 'minimum level',
  periodic_review: 'order-up-to target',
  dead: 'dead-stock window',
  overstock: 'days-of-cover ceiling',
};

const LEAD_SOURCE_WHY: Record<string, string> = {
  measured: 'measured from past receipts',
  declared: 'declared by the supplier',
  default: 'default (no history on file)',
};

const SS_METHOD_WHY: Record<string, (r: ReorderRecommendation) => string> = {
  fixed_days: (r) => `${dec(r.safety_days)}-day buffer of demand`,
  statistical: (r) =>
    `statistical buffer at ${r.service_level != null ? `${Math.round(r.service_level * 100)}%` : ''} service level`,
  manual: () => 'manual override set on the policy',
};

const POLICY_TYPE_LABEL: Record<string, string> = {
  reorder_point: 'Reorder point',
  min_max: 'Min / Max',
  periodic_review: 'Periodic review',
  // S1's global toggle (or a per-product override) can freeze this basis onto a row too.
  reorder_level: 'Reorder level (manual)',
};

const SELECTION_LABEL: Record<string, string> = {
  primary: 'primary supplier',
  best_score: 'best performance score',
  lowest_cost: 'lowest cost',
  last_purchase: 'last purchase',
};

/** One derivation line: a label, its value, and a short plain-language "why". */
function Step({
  label,
  value,
  why,
  emphasis,
}: {
  label: string;
  value: React.ReactNode;
  why?: React.ReactNode;
  /** Formula steps (ROP, order qty) render boxed so the arithmetic stands out. */
  emphasis?: boolean;
}) {
  return (
    <div
      className={cn(
        'flex flex-col gap-0.5 border-b border-border/60 py-2 last:border-b-0 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4',
        emphasis && 'rounded-md border-b-0 bg-muted/50 px-3',
      )}
    >
      <div className="min-w-0">
        <div className={cn('text-sm', emphasis ? 'font-semibold' : 'font-medium')}>{label}</div>
        {why ? <div className="text-xs text-muted-foreground">{why}</div> : null}
      </div>
      <div
        className={cn(
          'shrink-0 whitespace-nowrap text-sm tabular-nums',
          emphasis ? 'font-semibold' : 'text-foreground',
        )}
      >
        {value}
      </div>
    </div>
  );
}

const RANK_FACTOR_LABEL: Record<RankFactor['key'], string> = {
  urgency: 'Urgency',
  margin: 'Margin',
  abc: 'Value',
  priority: 'SO priority',
  committed: 'Committed vs forecast',
  market: 'Market trend',
};

/** M4 "why this rank" block - which cash-ranking factors were present, and which
 *  dominated. Margin is DROPPED (not zeroed) for uncosted SKUs (M4-D14), shown
 *  greyed so a novice sees exactly why urgency ended up dominant. */
function RankExplanation({ rec }: { rec: ReorderRecommendation }) {
  const factors = rec.rank_factors ?? [];
  const present = factors.filter((f) => f.present && f.value !== null);
  const dominant = present.reduce<RankFactor | null>(
    (top, f) => (top === null || f.weight * (f.value ?? 0) > top.weight * (top.value ?? 0) ? f : top),
    null,
  );
  const isNeedsCost = rec.funding_status === 'needs_cost';
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {isNeedsCost ? (
          <Badge variant="secondary" appearance="light" size="md">
            No supplier cost
          </Badge>
        ) : (
          <Badge variant="primary" appearance="light" size="md">
            Rank {rec.rank}
          </Badge>
        )}
        <span className="text-muted-foreground">
          {isNeedsCost
            ? "Can't be cash-ranked or funded until a supplier cost is added."
            : `Score ${fmtDecimal(rec.rank_score, 2)} · cash impact ${
                rec.cash_impact != null ? fmtMoney(rec.cash_impact) : 'unknown (uncosted)'
              }`}
        </span>
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        {factors.map((f) => {
          const isDominant = dominant?.key === f.key;
          return (
            <div
              key={f.key}
              className={cn(
                'flex items-center justify-between gap-3 border-b px-3 py-1.5 text-sm last:border-b-0',
                !f.present && 'opacity-55',
              )}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate">{RANK_FACTOR_LABEL[f.key]}</span>
                {isDominant ? (
                  <Badge variant="primary" appearance="light" size="xs">
                    top driver
                  </Badge>
                ) : null}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {f.present && f.value !== null ? (
                  <>
                    {fmtPct(f.value)} <span className="text-2xs">· weight {f.weight}</span>
                  </>
                ) : (
                  <span className="text-2xs italic">dropped - no cost on file</span>
                )}
              </span>
            </div>
          );
        })}
      </div>
      {rec.market_signal ? (
        <div className="flex items-start gap-2 rounded-lg border border-scm-incoming/40 bg-scm-incoming-soft p-2.5 text-xs">
          <TrendingUp className="mt-0.5 size-3.5 shrink-0 text-scm-incoming" aria-hidden />
          <span>
            <span className="font-medium">Market trend applied:</span> {rec.market_signal} - this
            lifted the funding priority (rank), not the order quantity.
          </span>
        </div>
      ) : null}
      <p className="text-xs text-muted-foreground">
        {isNeedsCost
          ? 'Without a supplier cost the margin factor drops out and there is no cash impact to weigh against the budget, so this buy stays in Needs cost. It is still a real must-buy - add a cost to fund it.'
          : 'Rank orders the buys for funding: higher-ranked buys are funded first when the budget is tight. Factors without data are dropped so they never dilute the score.'}
      </p>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

function TypeChip({ rec }: { rec: ReorderRecommendation }) {
  if (rec.type === 'buy') {
    return (
      <Badge variant="info" appearance="light" size="md">
        <ShoppingCart className="size-3" /> Buy
      </Badge>
    );
  }
  if (rec.type === 'exception') {
    return (
      <Badge variant="warning" appearance="light" size="md">
        <AlertTriangle className="size-3" /> Exception
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" appearance="light" size="md">
      <Layers className="size-3" /> Disposition
    </Badge>
  );
}

const CONFIDENCE_LABEL: Record<string, string> = { high: 'High', medium: 'Medium', low: 'Low' };

/** One label/value line inside the supplier score popover. */
function DetailLine({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-end">
        <span className="font-medium tabular-nums">{value}</span>
        {sub ? <span className="ms-1 text-2xs text-muted-foreground">{sub}</span> : null}
      </span>
    </div>
  );
}

/** Click-to-reveal supplier scorecard - kept OUT of the always-on table so the row
 *  stays scannable; a planner who wants "why this supplier" clicks the chevron and
 *  gets the composite score breakdown, reliability, cost + ordering constraints,
 *  plus how it ranked vs the chosen supplier. Frozen numbers only (M5 boundary). */
function SupplierScoreDetail({
  s,
  selected,
  chosen,
}: {
  s: SupplierChoice;
  selected: boolean;
  chosen: SupplierChoice | null;
}) {
  // Compared in base currency, because subtracting a USD price from a ringgit one
  // produces a number that looks like money and is not.
  const costDelta =
    !selected && chosen?.unit_cost_base != null && s.unit_cost_base != null
      ? s.unit_cost_base - chosen.unit_cost_base
      : null;
  const leadDelta =
    !selected && chosen?.lead_time_days != null && s.lead_time_days != null
      ? s.lead_time_days - chosen.lead_time_days
      : null;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center gap-0.5 rounded-sm px-1 text-2xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          title="Supplier score detail"
          aria-label={`Score detail for ${s.supplier_name ?? s.supplier_code}`}
        >
          Details
          <ChevronDown className="size-3" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverPrimitive.Portal>
        <PopoverContent align="end" collisionPadding={8} className="w-72 p-0">
          <div className="flex items-center gap-2 border-b px-3 py-2 text-xs font-medium">
            <span className="truncate">{s.supplier_name ?? s.supplier_code}</span>
            <Badge variant={selected ? 'primary' : 'secondary'} appearance="light" size="xs">
              {selected ? 'chosen' : 'alternative'}
            </Badge>
            {s.is_primary ? (
              <Badge variant="secondary" appearance="light" size="xs">
                primary
              </Badge>
            ) : null}
          </div>
          <div className="divide-y">
            <DetailLine
              label="Performance score"
              value={s.composite_score != null ? fmtInt(s.composite_score) : EM_DASH}
              sub={
                s.confidence
                  ? `${CONFIDENCE_LABEL[s.confidence] ?? s.confidence} · ${fmtInt(s.sample_size ?? 0)} obs`
                  : s.sample_size != null
                    ? `${fmtInt(s.sample_size)} obs`
                    : undefined
              }
            />
            <DetailLine
              label="Lead time"
              value={s.lead_time_days != null ? `${fmtInt(s.lead_time_days)}d` : EM_DASH}
              sub={
                [
                  s.lead_time_source ? LEAD_SOURCE_WHY[s.lead_time_source] ?? s.lead_time_source : null,
                  s.lead_time_variance != null ? `±${dec(Math.sqrt(Math.max(s.lead_time_variance, 0)))}d` : null,
                ]
                  .filter(Boolean)
                  .join(' · ') || undefined
              }
            />
            <DetailLine
              label="Unit cost"
              value={fmtSupplierCost(s.unit_cost, s.currency)}
              sub={convertedNote(s)}
            />
            {s.moq != null && s.moq > 0 ? (
              <DetailLine label="Min order" value={fmtInt(s.moq)} />
            ) : null}
            {s.order_multiple != null && s.order_multiple > 0 ? (
              <DetailLine label="Pack multiple" value={fmtInt(s.order_multiple)} />
            ) : null}
            {!selected && (costDelta != null || leadDelta != null) ? (
              <div className="px-3 py-1.5 text-2xs text-muted-foreground">
                vs chosen:{' '}
                {costDelta != null ? (
                  <span className={cn(costDelta > 0 ? 'text-scm-stockout' : 'text-scm-incoming')}>
                    {costDelta > 0 ? '+' : ''}
                    {fmtSupplierCost(costDelta, chosen?.base_currency)}
                  </span>
                ) : null}
                {costDelta != null && leadDelta != null ? ', ' : ''}
                {leadDelta != null ? (
                  <span className={cn(leadDelta > 0 ? 'text-scm-stockout' : 'text-scm-incoming')}>
                    {leadDelta > 0 ? '+' : ''}
                    {fmtInt(leadDelta)}d lead
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        </PopoverContent>
      </PopoverPrimitive.Portal>
    </Popover>
  );
}


/**
 * "and that is RM X at the rate we used" - said only when the supplier prices in something
 * other than the base currency, because on a ringgit price it would be noise.
 *
 * Without it, "USD 45 beat MYR 210" reads as an arithmetic mistake rather than a
 * conversion, and a buyer cannot check the decision they are being asked to approve.
 */
function convertedNote(s: SupplierChoice): string | undefined {
  if (s.missing_rate_currency) {
    return `No exchange rate on file for ${s.missing_rate_currency}, so this price could not be compared.`;
  }
  const base = s.base_currency ?? BASE_CURRENCY;
  const code = (s.currency || '').trim().toUpperCase();
  if (!code || code === base || s.unit_cost_base == null) return undefined;
  const rate = s.rate_to_base != null ? ` at ${s.rate_to_base}` : '';
  const asOf = s.rate_as_of ? ` (${fmtDate(s.rate_as_of)})` : '';
  return `${fmtSupplierCost(s.unit_cost_base, base)}${rate}${asOf}`;
}

/** Why this supplier, in words, from the reason frozen at run time.
 *
 * "Chosen by lowest cost" named the RULE and stopped there. What a buyer checks is which
 * supplier was beaten and by how much, so that is what this says - and only when the run
 * actually knew it. A saving needs BOTH costs; a lone supplier is not "cheapest".
 */
function SupplierReason({ rec }: { rec: ReorderRecommendation }) {
  const reason = rec.supplier_reason;
  const chosen = rec.supplier;
  const paid =
    chosen?.unit_cost_source === 'last_po' && chosen.unit_cost_ref
      ? `Cost is what we last paid, on ${chosen.unit_cost_ref}${
          chosen.unit_cost_at ? ` (${fmtDate(chosen.unit_cost_at)})` : ''
        }.`
      : chosen?.unit_cost_source === 'contract'
        ? 'Cost is the contract price on the product record, not a purchase we have made.'
        : null;

  const comparedIn = reason?.compared_in ?? BASE_CURRENCY;
  const missing = reason?.missing_rates ?? [];

  let why: string | null = null;
  if (reason?.basis === 'only_supplier') {
    why = 'The only supplier linked to this item.';
  } else if (reason?.basis === 'no_comparable_cost') {
    // Every candidate priced in money we hold no rate for. Ranking them by cost would be
    // a claim we cannot support, so the popup says what happened instead of inventing it.
    why = 'No supplier price could be compared, so this one was not chosen on cost.';
  } else if (reason?.basis === 'lowest_cost' && reason.runner_up) {
    // "RM 10.47 per unit under X" read as "it costs RM 10.47 at X" - the saving was taken
    // for the price and the beaten supplier for the chosen one, which is the exact opposite
    // of what happened. Name the gap as a gap, name the supplier it is measured against, and
    // give that supplier's own comparable price so the subtraction is checkable on screen.
    const runnerUpCost =
      reason.runner_up_cost_base != null
        ? ` at ${fmtSupplierCost(reason.runner_up_cost_base, comparedIn)}`
        : reason.runner_up_cost != null
          ? ` at ${fmtSupplierCost(reason.runner_up_cost, reason.runner_up_currency)}`
          : '';
    why =
      reason.saving_per_unit != null
        ? `Cheapest of the linked suppliers: ${fmtSupplierCost(
            reason.saving_per_unit,
            comparedIn,
          )} per unit less than the next best, ${reason.runner_up}${runnerUpCost}.`
        : `Cheapest of the priced suppliers. ${reason.runner_up} has no cost we could compare, so the gap is unknown.`;
  } else if (rec.supplier_selection) {
    why = `Chosen by ${SELECTION_LABEL[rec.supplier_selection] ?? rec.supplier_selection}.`;
  }

  // Named, not implied: a buyer cannot add a rate they were never told was missing.
  const rateGap = missing.length
    ? `No exchange rate on file for ${missing.join(', ')}, so ${
        missing.length > 1 ? 'those prices were' : 'that price was'
      } left out of the comparison.`
    : null;

  if (!why && !paid && !rateGap) return null;
  return (
    <div className="space-y-0.5 text-xs text-muted-foreground">
      {why ? <div>{why}</div> : null}
      {paid ? <div>{paid}</div> : null}
      {rateGap ? <div>{rateGap}</div> : null}
    </div>
  );
}

/** Supplier block: chosen supplier + ranked alternatives (cost / lead / score). */
function SupplierBlock({ rec }: { rec: ReorderRecommendation }) {
  if (rec.is_exception || !rec.supplier) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-scm-stockout/40 bg-scm-stockout-soft p-3 text-sm">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-scm-stockout" aria-hidden />
        <span>
          No supplier is linked to this SKU, so the reorder can&apos;t be sourced. Link a supplier in
          the product record to turn this into a buy.
        </span>
      </div>
    );
  }
  const others = rec.alternatives.filter((a) => a.supplier_code !== rec.supplier?.supplier_code);
  const ranked: SupplierChoice[] = [rec.supplier, ...others];
  return (
    <div className="space-y-2">
      <SupplierReason rec={rec} />
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-x-3 border-b bg-muted/40 px-3 py-1.5 text-2xs font-medium text-muted-foreground">
          <span>Supplier</span>
          <span className="text-right">Cost</span>
          <span className="text-right">Lead</span>
          <span className="text-right">Score</span>
          <span className="text-right sr-only">Detail</span>
        </div>
        {ranked.map((s) => {
          const selected = s.supplier_code === rec.supplier?.supplier_code;
          return (
            <div
              key={s.supplier_code}
              className={cn(
                'grid grid-cols-[1fr_auto_auto_auto_auto] items-center gap-x-3 px-3 py-1.5 text-sm',
                selected && 'bg-muted/40',
              )}
            >
              <span className="flex min-w-0 items-center gap-1">
                <span className="truncate" title={s.supplier_name ?? undefined}>
                  {s.supplier_name ?? EM_DASH}
                </span>
                {selected ? (
                  <Badge variant="primary" appearance="light" size="xs">
                    chosen
                  </Badge>
                ) : null}
              </span>
              <span className="text-right tabular-nums">
                <span className="block">{fmtSupplierCost(s.unit_cost, s.currency)}</span>
                {/* The converted figure sits under the price rather than inside a popover:
                    it is the number the ranking used, so "USD 45 beat MYR 210" has to be
                    checkable without a second click. */}
                {convertedNote(s) ? (
                  <span className="block text-2xs font-normal text-muted-foreground">
                    {convertedNote(s)}
                  </span>
                ) : null}
              </span>
              <span className="text-right tabular-nums">
                {s.lead_time_days != null ? `${fmtInt(s.lead_time_days)}d` : EM_DASH}
              </span>
              <span className="text-right tabular-nums">
                {s.composite_score != null ? fmtInt(s.composite_score) : EM_DASH}
              </span>
              <span className="text-right">
                <SupplierScoreDetail s={s} selected={selected} chosen={rec.supplier} />
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Forecast-demand derivation row - the daily usage that drives ROP and
 *  days-of-cover. Shared by the buy and disposition variants so the demand that
 *  PRODUCED the days-of-cover is always spelled out (value + /day + pattern). */
function ForecastDemandStep({ rec }: { rec: ReorderRecommendation }) {
  const pattern = xyzLabel(rec.xyz_class as XyzClass | null);
  return (
    <Step
      label="Forecast demand"
      value={`${dec(rec.forecast_daily_demand)} /day`}
      why={
        <>
          Average daily usage over recent sales history. Demand pattern:{' '}
          <span className="font-medium text-foreground">{pattern}</span>.
        </>
      }
    />
  );
}

/** Buy / exception derivation - the arithmetic laid out step by step. */
function BuyDerivation({ rec }: { rec: ReorderRecommendation }) {
  const ssWhy = rec.safety_stock_method
    ? SS_METHOD_WHY[rec.safety_stock_method]?.(rec)
    : undefined;
  const roundingNote: string[] = [];
  if (rec.moq != null && rec.moq > 0) roundingNote.push(`min order ${dec(rec.moq)}`);
  if (rec.order_multiple != null && rec.order_multiple > 0)
    roundingNote.push(`pack multiple ${dec(rec.order_multiple)}`);

  // "-" glued straight onto a unit ("- days") reads as a formatting bug rather than "no
  // lead time / review period is on file for this row" (21 Aug fix) - name it in words
  // instead, same rule the order-qty ledger applies to its own "-d review"/"-d lead".
  const hasReview = rec.review_days != null;
  const netPositionWhy =
    (rec.outstanding_po ?? 0) > 0
      ? 'On hand + on order + open PO − committed'
      : 'On hand + on order − committed';

  return (
    <div>
      <ForecastDemandStep rec={rec} />
      <Step
        label="Lead time"
        value={rec.lead_time_days != null ? `${dec(rec.lead_time_days)} days` : 'no lead time on file'}
        why={
          rec.lead_time_source
            ? `Source: ${LEAD_SOURCE_WHY[rec.lead_time_source] ?? rec.lead_time_source}.`
            : undefined
        }
      />
      <Step
        label="Safety stock"
        value={`${dec(rec.safety_stock)} units`}
        why={
          <>
            {ssWhy ? `Method: ${ssWhy}.` : null}
            {rec.safety_stock_fallback ? (
              <span className="mt-0.5 block text-amber-600">{rec.safety_stock_fallback}</span>
            ) : null}
          </>
        }
      />
      <Step
        emphasis
        label="Reorder point"
        why="Demand × lead time + safety stock"
        value={
          <>
            {dec(rec.forecast_daily_demand)} × {dec(rec.lead_time_days)} +{' '}
            {dec(rec.safety_stock)} = <span className="text-foreground">{dec(rec.reorder_point)}</span>
          </>
        }
      />
      <Step
        label="Net position"
        value={dec(rec.net_position)}
        why={netPositionWhy}
      />
      <Step
        label="Runway"
        value={rec.days_of_cover != null ? `${fmtInt(rec.days_of_cover)} days` : EM_DASH}
        why="How long the net position lasts at the forecast demand"
      />
      <Step
        emphasis
        label="Order-up-to target"
        why={
          hasReview
            ? `Reorder point + one review period (${dec(rec.review_days)} days) of demand`
            : 'Reorder point (no review period set for this policy)'
        }
        value={
          hasReview ? (
            <>
              {dec(rec.reorder_point)} + {dec(rec.forecast_daily_demand)} × {dec(rec.review_days)} ={' '}
              <span className="text-foreground">{dec(rec.order_up_to)}</span>
            </>
          ) : (
            <span className="text-foreground">{dec(rec.order_up_to)}</span>
          )
        }
      />
      {rec.type === 'buy' ? (
        <Step
          emphasis
          label="Order quantity"
          why={
            roundingNote.length
              ? `Order-up-to − net, rounded to ${roundingNote.join(' / ')}`
              : 'Order-up-to − net position'
          }
          value={
            <>
              {dec(rec.order_up_to)} − {dec(rec.net_position)}
              {rec.recommended_qty != null ? <> = {dec(rec.recommended_qty)}</> : null}
              {' → '}
              <span className="text-foreground">{fmtInt(rec.order_qty)}</span>
            </>
          }
        />
      ) : (
        <Step
          label="Order quantity"
          value={EM_DASH}
          why="Can't be sized until a supplier (with min-order / pack rules) is linked."
        />
      )}
    </div>
  );
}

/** Disposition explanation - why (dead / overstock) + suggested action. No buy maths. */
function DispositionExplanation({ rec }: { rec: ReorderRecommendation }) {
  const isDead = rec.reason === 'dead';
  return (
    <div>
      <Step
        label={isDead ? 'Dead stock' : 'Overstock'}
        value={
          <Badge variant="secondary" appearance="light" size="md">
            {isDead ? <Ban className="size-3" /> : <PackageX className="size-3" />}
            {isDead ? 'No recent movement' : 'Excess cover'}
          </Badge>
        }
        why={
          isDead
            ? 'No consumption recorded within the dead-stock window.'
            : `Runway exceeds the ${REASON_NOUN.overstock}.`
        }
      />
      {/* Surface the demand that PRODUCED the days-of-cover so the "why" is
          traceable, not just asserted (mirrors the buy variant's row). */}
      <ForecastDemandStep rec={rec} />
      {rec.days_of_cover != null ? (
        <Step
          emphasis
          label="Runway"
          why="Net position ÷ forecast demand"
          value={
            <>
              {dec(rec.net_position)} ÷ {dec(rec.forecast_daily_demand)} /day ={' '}
              <span className="text-foreground">{fmtInt(rec.days_of_cover)} days</span>
            </>
          }
        />
      ) : null}
      <Step label="Net position" value={dec(rec.net_position)} why="On hand + on order − committed" />
      <Step
        label="Suggested action"
        value={
          <span className="font-medium">
            {rec.disposition_action === 'discontinue'
              ? 'Discontinue'
              : rec.disposition_action === 'promo'
                ? 'Promote'
                : 'Hold'}
          </span>
        }
        why={rec.reason_label ?? undefined}
      />
      {rec.transfer_flag ? (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-scm-incoming/40 bg-scm-incoming-soft p-3 text-sm">
          <ArrowRightLeft className="mt-0.5 size-4 shrink-0 text-scm-incoming" aria-hidden />
          <span>{rec.transfer_flag}</span>
        </div>
      ) : null}
    </div>
  );
}

/**
 * M5 semantic layer - the LLM narrator that sits ON TOP of the deterministic derivation
 * below. Clearly badged "AI" so it is never mistaken for the frozen arithmetic.
 *
 * ON DEMAND, not on open. It used to fire two model calls (explanation + market advisory)
 * every single time somebody opened a row, and the drawer is opened to READ THE NUMBERS -
 * stepping through 230 recommendations spent 460 calls on a sentence that restates the
 * figures printed underneath it. The button costs one click and nothing until then.
 */
function AiSummaryBlock({ rec, enabled }: { rec: ReorderRecommendation; enabled: boolean }) {
  const [asked, setAsked] = useState(false);
  // `enabled` still gates on the dialog being open, so closing it cannot leave a request
  // running; `asked` is what actually authorises the spend.
  const active = enabled && asked;
  const explanation = useRecommendationExplanation(rec, active);
  const advisory = useRecommendationAdvisory(rec, active);

  // A new rec means a new question: stepping to the next row must not show the previous
  // row's sentence, and must not silently re-buy an explanation nobody asked for.
  useEffect(() => {
    setAsked(false);
  }, [rec.id]);

  if (!asked) {
    return (
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => setAsked(true)}
        data-testid="explain-in-words"
      >
        <Sparkles className="size-3.5 text-primary" aria-hidden />
        Explain in plain language
      </Button>
    );
  }

  return (
    <div className="space-y-2">
      <div className="rounded-lg border border-primary/25 bg-primary/5 p-3">
        <div className="mb-1.5 flex items-center gap-1.5">
          <Sparkles className="size-3.5 text-primary" aria-hidden />
          <span className="text-2xs font-semibold uppercase tracking-wide text-primary">
            AI summary
          </span>
        </div>
        {explanation.isLoading ? (
          <div className="space-y-1.5" aria-label="Generating explanation">
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-4/5" />
          </div>
        ) : explanation.isError ? (
          <p className="text-sm text-muted-foreground">
            Couldn&apos;t generate a plain-language summary - the derivation below still applies.
          </p>
        ) : (
          <p className="text-sm">{explanation.data?.explanation}</p>
        )}
      </div>

      {/* Market advisory - subtle info callout, only when a signal matched. */}
      {advisory.data?.advisory ? (
        <div className="flex items-start gap-2 rounded-lg border border-scm-incoming/40 bg-scm-incoming-soft p-3 text-sm">
          <TrendingUp className="mt-0.5 size-4 shrink-0 text-scm-incoming" aria-hidden />
          <div className="min-w-0 space-y-1">
            <Badge variant="info" appearance="light" size="xs">
              Market signal
            </Badge>
            <p>{advisory.data.advisory}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * M5 Ask box - a bounded Q&A grounded in THIS rec's frozen numbers. Keeps a short
 * transcript in local state (reset when the dialog steps to another rec via the
 * `key` on DialogBody). A question that needs a number the rec doesn't carry
 * comes back as the exact refusal string, which we style as a muted turn so the
 * user sees the model declining rather than guessing.
 */
function AskSection({ rec }: { rec: ReorderRecommendation }) {
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const ask = useAskRecommendation(rec);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || ask.isPending) return;
    setQuestion('');
    try {
      const res = await ask.mutateAsync(q);
      setTurns((prev) => [...prev, { question: q, answer: res.answer, refused: res.answer === REFUSAL }]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to answer question';
      setTurns((prev) => [...prev, { question: q, answer: msg, refused: true }]);
    }
  };

  return (
    <div>
      <SectionTitle>Ask about this recommendation</SectionTitle>
      {turns.length ? (
        <div className="mb-3 space-y-3">
          {turns.map((t, i) => (
            <div key={i} className="space-y-1">
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-lg bg-primary/10 px-3 py-1.5 text-sm">
                  {t.question}
                </div>
              </div>
              <div className="flex justify-start">
                <div
                  className={cn(
                    'flex max-w-[85%] items-start gap-1.5 rounded-lg px-3 py-1.5 text-sm',
                    t.refused ? 'bg-muted text-muted-foreground italic' : 'bg-muted',
                  )}
                >
                  <Sparkles className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
                  <span>{t.answer}</span>
                </div>
              </div>
            </div>
          ))}
          {ask.isPending ? (
            <div className="flex justify-start">
              <div className="flex max-w-[85%] items-center gap-1.5 rounded-lg bg-muted px-3 py-1.5">
                <Skeleton className="h-3.5 w-32" />
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      <form onSubmit={submit} className="flex items-center gap-2">
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. What lead time was used?"
          aria-label="Ask about this recommendation"
        />
        <Button type="submit" disabled={!question.trim() || ask.isPending}>
          <SendHorizontal className="size-4" aria-hidden />
          Ask
        </Button>
      </form>
    </div>
  );
}

export function ReorderExplanationDialog({
  rec,
  open,
  onOpenChange,
  recs,
  totalCount,
  pageItemOffset,
  onNavigate,
  poLink,
}: {
  rec: ReorderRecommendation | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Current grid-page rows, in the active sort/filter order - powers the
   *  in-place prev/next pager so the user can step recommendations without
   *  closing the popup. Optional so the dialog still renders standalone. */
  recs?: ReorderRecommendation[];
  /** Full filtered total (all pages) - the pager's denominator. */
  totalCount?: number;
  /** 0-based index of the first `recs` row within the full filtered list. */
  pageItemOffset?: number;
  /** Step to a neighbouring recommendation in place (no close). */
  onNavigate?: (rec: ReorderRecommendation) => void;
  /** The draft PO this rec was confirmed into (M8-F8) - drives the "View PO"
   *  button that deep-links to the PO detail page. null when not yet confirmed. */
  poLink?: { po_number: string; po_id: string | null } | null;
}) {
  const isBuyLike = rec?.type === 'buy' || rec?.type === 'exception';
  const canPage = !!recs && recs.length > 1 && !!rec && !!onNavigate;
  // Where this recommendation sits in the page the dialog was opened from.
  const recIndex = recs && rec ? recs.findIndex((r) => r.id === rec.id) : -1;

  // Arrow-key stepping between recommendations while the popup is open. Ignore
  // arrows fired from a text field (the M5 Ask input lives in this DialogContent),
  // so moving the caret mid-question never jumps to another recommendation.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!canPage || !recs || !rec || !onNavigate) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      )
        return;
      const idx = recs.findIndex((r) => r.id === rec.id);
      if (idx < 0) return;
      const nextIdx = e.key === 'ArrowRight' ? idx + 1 : idx - 1;
      if (nextIdx < 0 || nextIdx >= recs.length) return;
      e.preventDefault();
      onNavigate(recs[nextIdx]);
    },
    [canPage, recs, rec, onNavigate],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange} modal>
      {/* `overflow-y-hidden` overrides the base `overflow-y-auto` (same
          tailwind-merge group) so only the body scrolls - the header + pager
          stay pinned. `max-h-[85dvh]` keeps the tallest derivation on-screen,
          reachable at ~375px mobile width. */}
      <DialogContent
        className="max-h-[85dvh] max-w-xl overflow-y-hidden sm:max-w-2xl"
        onKeyDown={handleKeyDown}
      >
        <DialogHeader className="shrink-0">
          <div className="flex items-start justify-between gap-3 pe-8">
            <DialogTitle>How this recommendation was reached</DialogTitle>
            {canPage ? (
              <RecordNavigation
                index={(pageItemOffset ?? 0) + recIndex + 1}
                total={totalCount ?? recs!.length}
                hasPrevious={recIndex > 0}
                hasNext={recIndex >= 0 && recIndex < recs!.length - 1}
                onPrevious={() => onNavigate!(recs![recIndex - 1])}
                onNext={() => onNavigate!(recs![recIndex + 1])}
                ariaLabel="recommendation"
                className="shrink-0"
              />
            ) : null}
          </div>
          <DialogDescription>
            {rec ? (
              <span className="flex flex-wrap items-center gap-2">
                <TypeChip rec={rec} />
                <span className="font-medium text-foreground">{rec.sku}</span>
                {distinctName(rec.sku, rec.product_name) ? <span>- {rec.product_name}</span> : null}
                {/* View PO (M8-F8): once the line is confirmed into a draft PO, jump
                    straight to that PO's detail page. */}
                {poLink?.po_id ? (
                  <Button asChild variant="outline" size="sm" className="ms-auto h-7 shrink-0 px-2 text-xs">
                    <Link href={`/scm/purchase-orders/${poLink.po_id}`} title={`View ${poLink.po_number}`}>
                      <ExternalLink className="size-3.5" aria-hidden />
                      View PO
                    </Link>
                  </Button>
                ) : null}
              </span>
            ) : null}
          </DialogDescription>
        </DialogHeader>

        {rec ? (
          // `key` resets scroll to top when stepping to a neighbouring rec.
          <DialogBody key={rec.id} className="grow space-y-5 overflow-y-auto min-h-0 -mx-6 px-6">
            {/* M5 AI narration + market advisory IS the headline for EVERY rec type
                (buy / exception / disposition) - the old deterministic one-liner was
                redundant with it. */}
            <AiSummaryBlock rec={rec} enabled={open} />

            {/* Coverage Timeline (UAC Group B) - the dated evidence for the number
                above, sitting directly under it because the planner's first question
                on opening a row is "do I believe this?", and the answer is a list of
                documents she recognises. Rendered for EVERY rec type: a disposition
                row's excess is as much a coverage question as a buy row's shortage.
                The pool is resolved from the row's own location code (a code with no
                pool pointer is its own pool), and the floor is the row's reorder
                point - 0 when it has none, which is the project-demand case. */}
            <div>
              <SectionTitle>Coverage timeline</SectionTitle>
              <CoverageTimelinePanel
                productCode={rec.sku}
                productName={rec.product_name}
                poolCode={rec.warehouse_code}
                floor={rec.reorder_point ?? 0}
                enabled={open}
              />
            </div>

            {/* Step-by-step derivation */}
            <div>
              <SectionTitle>{isBuyLike ? 'How the numbers were reached' : "Why it's flagged"}</SectionTitle>
              {isBuyLike ? <BuyDerivation rec={rec} /> : <DispositionExplanation rec={rec} />}
            </div>

            {/* Cash ranking (M4 buy recs only) */}
            {rec.type === 'buy' && rec.rank != null ? (
              <div>
                <SectionTitle>
                  {rec.funding_status === 'needs_cost' ? 'Why it needs a cost' : 'Why this rank'}
                </SectionTitle>
                <RankExplanation rec={rec} />
              </div>
            ) : null}

            {/* Policy + trigger */}
            <div>
              <SectionTitle>Policy &amp; trigger</SectionTitle>
              <Step
                label="Policy applied"
                value={
                  rec.policy_type ? (POLICY_TYPE_LABEL[rec.policy_type] ?? rec.policy_type) : EM_DASH
                }
                why={rec.is_network ? 'Planned across the network (aggregated).' : undefined}
              />
              <Step
                label="Trigger"
                value={<span className="text-end">{rec.reason_label ?? EM_DASH}</span>}
              />
            </div>

            {/* Confidence */}
            <div>
              <SectionTitle>Confidence</SectionTitle>
              <div className="flex flex-wrap items-center gap-2">
                {rec.confidence ? (
                  <ConfidenceBadge confidence={rec.confidence} sampleSize={rec.sample_size} />
                ) : (
                  <span className="text-sm text-muted-foreground">{EM_DASH}</span>
                )}
                <span className="text-xs text-muted-foreground">
                  Reflects data sufficiency (demand pattern{' '}
                  <span className="font-medium">{xyzLabel(rec.xyz_class as XyzClass | null)}</span> ·{' '}
                  {fmtInt(rec.sample_size)} sample), not that the number is guaranteed correct.
                </span>
              </div>
            </div>

            {/* Supplier (buy / exception) */}
            {isBuyLike ? (
              <div>
                <SectionTitle>Supplier</SectionTitle>
                <SupplierBlock rec={rec} />
              </div>
            ) : null}

            {/* Network allocation */}
            {rec.is_network && rec.allocation && rec.allocation.length ? (
              <div>
                <SectionTitle>Per-warehouse allocation</SectionTitle>
                <div className="overflow-hidden rounded-lg border border-border">
                  {rec.allocation.map((a) => (
                    <div
                      key={a.warehouse_code}
                      className="flex items-center justify-between border-b px-3 py-1.5 text-sm last:border-b-0"
                    >
                      <span className="truncate" title={a.warehouse_name}>
                        <span className="font-medium">{a.warehouse_code}</span>{' '}
                        <span className="text-xs text-muted-foreground">{a.warehouse_name}</span>
                      </span>
                      <span className="tabular-nums">{fmtInt(a.qty)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {/* M5 Ask box - bounded Q&A over this rec's frozen numbers (buy /
                exception only). Lives at the bottom so the derivation is read
                first; the dialog body is already mobile-scrollable. */}
            <AskSection rec={rec} />
          </DialogBody>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
