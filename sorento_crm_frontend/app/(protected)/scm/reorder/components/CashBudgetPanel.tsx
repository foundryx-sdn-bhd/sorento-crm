'use client';

import { CircleDollarSign, Wallet } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Slider, SliderThumb } from '@/components/ui/slider';
import { cn } from '@/lib/utils';
import { fmtInt, fmtMoney } from '../../lib/format';
import { LiveNumberInput } from '@/components/ui/number-input';
import type { FundingResult } from '../lib/reorderCashAllocation';

/** One compact readout figure (label above, value below). */
function Readout({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="truncate text-xs font-medium text-muted-foreground">{label}</div>
      <div
        title={value}
        className={cn(
          'mt-0.5 truncate whitespace-nowrap text-lg font-semibold tabular-nums tracking-tight',
          valueClass,
        )}
      >
        {value}
      </div>
      {sub ? <div className="truncate text-2xs text-muted-foreground">{sub}</div> : null}
    </div>
  );
}

export function CashBudgetPanel({
  budget,
  onBudgetChange,
  sliderMax,
  step,
  funding,
}: {
  budget: number;
  onBudgetChange: (value: number) => void;
  sliderMax: number;
  step: number;
  funding: FundingResult;
}) {
  const { funded, deferred, needsCost, fundedCash, deferredCash, remaining } = funding;
  // Fill bar: how much of the budget the funded set consumes (capped at 100%).
  const fillPct = budget > 0 ? Math.min(100, Math.round((fundedCash / budget) * 100)) : 0;

  const clamp = (n: number) => Math.max(0, Math.min(sliderMax, Math.round(n)));

  return (
    <Card className="space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Wallet className="size-4.5" aria-hidden />
          </span>
          <div>
            <div className="text-sm font-semibold">Cash budget</div>
            <div className="text-xs text-muted-foreground">
              Slide to fund the highest-ranked buys that fit - the rest defer with their
              stockout risk.
            </div>
          </div>
        </div>
        <div>
          <Label htmlFor="cash-budget" className="mb-1 block text-xs">
            Budget (RM)
          </Label>
          <LiveNumberInput
            id="cash-budget"
            inputMode="numeric"
            min={0}
            max={sliderMax}
            step={step}
            rules={{ min: 0, max: sliderMax }}
            value={budget}
            onCommit={onBudgetChange}
            className="w-36 tabular-nums"
          />
        </div>
      </div>

      {/* The one bold element - the slider + funded-vs-budget fill bar. */}
      <div className="space-y-2">
        <Slider
          value={[budget]}
          min={0}
          max={sliderMax}
          step={step}
          onValueChange={([v]) => onBudgetChange(clamp(v))}
          aria-label="Cash budget"
        >
          <SliderThumb aria-label="Budget amount" />
        </Slider>
        <div className="flex items-center justify-between text-2xs text-muted-foreground tabular-nums">
          <span>{fmtMoney(0)}</span>
          <span>{fmtMoney(sliderMax)}</span>
        </div>
        {/* Fill bar: funded cash against the budget envelope. */}
        <div>
          <div
            className="relative h-2.5 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuenow={fillPct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Funded cash against budget"
          >
            <div
              className="h-full origin-left rounded-full bg-scm-incoming transition-transform duration-(--duration-base) ease-linear motion-reduce:transition-none"
              style={{ transform: `scaleX(${fillPct / 100})` }}
            />
          </div>
          <div className="mt-1 flex items-center justify-between text-2xs text-muted-foreground">
            <span>
              <span className="font-medium text-foreground tabular-nums">{fmtMoney(fundedCash)}</span>{' '}
              funded ({fillPct}% of budget)
            </span>
            <span className="tabular-nums">{fmtMoney(remaining)} left</span>
          </div>
        </div>
      </div>

      {/* Live readout row. */}
      <div className="grid grid-cols-2 gap-4 border-t pt-3 sm:grid-cols-4">
        <Readout label="Budget" value={fmtMoney(budget)} />
        <Readout
          label="Funded"
          value={fmtInt(funded.length)}
          sub={fmtMoney(fundedCash)}
          valueClass="text-scm-incoming"
        />
        <Readout
          label="Deferred"
          value={fmtInt(deferred.length)}
          sub={fmtMoney(deferredCash)}
          valueClass="text-scm-overstock"
        />
        <Readout label="Remaining" value={fmtMoney(remaining)} />
      </div>

      {/* Uncosted buys can't be cash-ranked (M4-D16) - kept visible, not hidden. */}
      {needsCost.length > 0 ? (
        <div className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <CircleDollarSign className="size-3.5 shrink-0" aria-hidden />
          <span>
            <span className="font-medium text-foreground tabular-nums">
              {fmtInt(needsCost.length)}
            </span>{' '}
            {needsCost.length === 1 ? 'buy needs' : 'buys need'} a supplier cost before they can be
            cash-ranked - shown in the Needs cost section below.
          </span>
        </div>
      ) : null}
    </Card>
  );
}
