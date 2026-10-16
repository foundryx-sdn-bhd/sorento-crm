'use client';

import { useEffect, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardHeading,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import {
  FULFILMENT_CLASS_LABEL,
  FULFILMENT_CLASS_ORDER,
  FULFILMENT_FACTOR_ORDER,
  fulfilmentFactorLabel,
} from '../lib/labels';
import { numberFieldError, type NumberFieldRules } from '@/lib/number-field';
import { DEFAULT_TBA_DATE_FROM } from '../types/policy.types';
import {
  useFulfilmentPriority,
  useSaveFulfilmentPriority,
} from '../hooks/usePolicies';

/** Local calendar day as `YYYY-MM-DD`, so the mirror of the backend check reads the same
 *  date the user sees in the picker rather than a UTC-shifted one. */
function todayIso(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10);
}

// The rules each number is held to on Save. The weights carry no upper bound the backend
// enforces, so none is invented here (the `max` on the input only steers the spinner).
const WEIGHT_RULES: NumberFieldRules = { min: 0 };
const TRANSFER_DAYS_RULES: NumberFieldRules = { min: 0, integer: true };
const WINDOW_DAYS_RULES: NumberFieldRules = { min: 0, max: 365, integer: true };
const POOL_SHARE_RULES: NumberFieldRules = { min: 0, max: 100, integer: true };

/**
 * The ranking that decides both what goes in a container and which purchase-order line
 * arriving stock is assigned to (AC-H5) - one policy, tuned here. Ranking factors weigh a
 * demand row against its competitors; demand-class weights say how much a project order
 * outranks a retail one; the last two settings are the ladder's two calendar dates.
 *
 * `reorder_coverage_until` is a CALENDAR DATE (19 Aug follow-up), not a rolling day count -
 * the captain's own framing was "purchasing reorders until October". A line required after
 * this date is proposed as Buy now, untouched; clearing it means no coverage limit is set.
 *
 * `tba_date_from` is the other end of the same idea (borrow ladder v7.1, R20): demand dated
 * on or after it is TBA - it takes no supply, is never covered and never donates. It is NOT
 * NULL, so the button beside it resets to the column default rather than emptying the field.
 * The cross-group borrow caps that used to sit here are gone with R5: any ownership group may
 * donate now, so there is nothing left for a cap to cap.
 *
 * `overdue_grace_days` / `overdue_dead_days` (R-O, 3 Sep ruling) are the other pair on that
 * row: a document whose arrival has passed with nothing received is planned against
 * `today + grace`, and one later than the dead line counts as nothing at all.
 *
 * `immediate_window_days` / `pool_share_pct` (fulfilment feedback batch, S1, 2 Sep ruling
 * R-B) tune the site pool's share step: a line due within the window may take up to
 * `pool_share_pct`'s complement of the pool's free pile now, bounded by the five-pool net;
 * beyond the window a line takes the whole allowance or nothing. Nobody planning an order is
 * asked for either number - they live here, once.
 *
 * Read and edit are the same layout - every value is always an input, the way the other
 * policy panels on this page already work; Save writes a NEW policy revision and activates
 * it, so the ranking history a planner was judged against is never rewritten in place.
 */
export function FulfilmentPriorityPanel() {
  const { data, isLoading, isError } = useFulfilmentPriority();
  const save = useSaveFulfilmentPriority();

  const [factors, setFactors] = useState<Record<string, string>>({});
  const [classWeights, setClassWeights] = useState<Record<string, string>>({});
  const [reorderCoverageUntil, setReorderCoverageUntil] = useState('');
  const [tbaDateFrom, setTbaDateFrom] = useState('');
  const [transferDays, setTransferDays] = useState('0');
  const [immediateWindowDays, setImmediateWindowDays] = useState('30');
  const [poolSharePct, setPoolSharePct] = useState('50');
  const [overdueGraceDays, setOverdueGraceDays] = useState('0');
  const [overdueDeadDays, setOverdueDeadDays] = useState('0');
  const [formError, setFormError] = useState<string | null>(null);
  // Set once Save found a bad number: every field's sentence then follows its value live.
  const [showErrors, setShowErrors] = useState(false);
  const fieldError = (raw: string, rules: NumberFieldRules) =>
    showErrors ? numberFieldError(raw, rules) : undefined;

  useEffect(() => {
    if (!data) return;
    // Seeded from the FULL stored record, not just the keys this screen renders: the
    // backend keeps `factors` / `demand_class_weights` as open JSONB precisely so a
    // policy can carry a factor this UI does not yet know about, and a draft that only
    // remembered the rendered keys would drop it the moment it was saved again.
    const nextFactors: Record<string, string> = {};
    for (const [key, value] of Object.entries(data.factors)) {
      nextFactors[key] = String(value);
    }
    for (const key of FULFILMENT_FACTOR_ORDER) {
      if (!(key in nextFactors)) nextFactors[key] = '0';
    }
    setFactors(nextFactors);

    const nextClasses: Record<string, string> = {};
    for (const [key, value] of Object.entries(data.demand_class_weights)) {
      nextClasses[key] = String(value);
    }
    for (const key of FULFILMENT_CLASS_ORDER) {
      if (!(key in nextClasses)) nextClasses[key] = '0';
    }
    setClassWeights(nextClasses);

    setReorderCoverageUntil(data.reorder_coverage_until ?? '');
    setTbaDateFrom(data.tba_date_from);
    setTransferDays(String(data.transfer_days ?? 0));
    // `?? 30` / `?? 50` ahead of the S1 migration: a GET from a database that has never
    // set these reads the column defaults, the same "seeded defaults shown, save to
    // activate" contract every other field on this row already has (AC-1.1).
    setImmediateWindowDays(String(data.immediate_window_days ?? 30));
    setPoolSharePct(String(data.pool_share_pct ?? 50));
    // R-O's pair, same contract: a GET from a database that predates migration 464 omits
    // both keys, so the panel seeds the column's own SHIPPED default (0 / 0 - captain's
    // ruling, 3 Sep 2026) itself.
    setOverdueGraceDays(String(data.overdue_grace_days ?? 0));
    setOverdueDeadDays(String(data.overdue_dead_days ?? 0));
  }, [data]);

  const onSave = async () => {
    const parsedFactors: Record<string, number> = {};
    for (const key of FULFILMENT_FACTOR_ORDER) {
      const value = Number(factors[key]);
      if (numberFieldError(factors[key], WEIGHT_RULES)) {
        setShowErrors(true);
        setFormError(`${fulfilmentFactorLabel(key)} weight must be 0 or more.`);
        return;
      }
      parsedFactors[key] = value;
    }
    // Any factor the stored policy carries beyond what this screen renders travels
    // through untouched, merged over the edited set rather than replacing it.
    for (const [key, raw] of Object.entries(factors)) {
      if (key in parsedFactors) continue;
      const value = Number(raw);
      if (Number.isFinite(value)) parsedFactors[key] = value;
    }

    const parsedClasses: Record<string, number> = {};
    for (const key of FULFILMENT_CLASS_ORDER) {
      const value = Number(classWeights[key]);
      if (numberFieldError(classWeights[key], WEIGHT_RULES)) {
        setShowErrors(true);
        setFormError(
          `${FULFILMENT_CLASS_LABEL[key]} weight must be 0 or more.`,
        );
        return;
      }
      parsedClasses[key] = value;
    }
    for (const [key, raw] of Object.entries(classWeights)) {
      if (key in parsedClasses) continue;
      const value = Number(raw);
      if (Number.isFinite(value)) parsedClasses[key] = value;
    }

    // A native date input hands back '' when the user clears it, and the column is NOT
    // NULL - so an emptied field saves the default rather than nothing. This is the ONE
    // fallback on this screen: what the backend sends is rendered as it comes.
    const tba = tbaDateFrom || DEFAULT_TBA_DATE_FROM;
    // The backend rejects a CHANGE to a past TBA date with 422; mirrored here so the
    // message arrives beside the field instead of as a toast after a round trip.
    //
    // Only when the field is DIRTY. Every save sends the whole record, so once a
    // configured date had quietly passed this check refused the panel's own unchanged
    // value - and with it every weight, coverage date and class weight on the screen. The
    // rule is about moving the TBA line back, not about the age of a date nobody touched.
    if (tba !== data?.tba_date_from && tba < todayIso()) {
      setFormError('TBA date from must be today or later.');
      return;
    }

    // R-B (31 Aug ruling): 0 by default, and never negative - the backend's own
    // `transfer_days_negative` coded 422 mirrored here so the message sits beside the
    // field instead of arriving as a toast after a round trip.
    const parsedTransferDays = Number(transferDays);
    if (numberFieldError(transferDays, TRANSFER_DAYS_RULES)) {
      setShowErrors(true);
      setFormError('Transfer days between bins must be 0 or more.');
      return;
    }

    // R-B (2 Sep ruling, AC-1.2): 0 is valid for both (0 days = nothing is immediate,
    // 0 % = the pool never gives a share) - only out-of-range is refused.
    const parsedImmediateWindowDays = Number(immediateWindowDays);
    if (numberFieldError(immediateWindowDays, WINDOW_DAYS_RULES)) {
      setShowErrors(true);
      setFormError('Immediate window (days) must be between 0 and 365.');
      return;
    }

    const parsedPoolSharePct = Number(poolSharePct);
    if (numberFieldError(poolSharePct, POOL_SHARE_RULES)) {
      setShowErrors(true);
      setFormError('Pool share (%) must be between 0 and 100.');
      return;
    }

    // R-O (3 Sep ruling): 0 is valid for both (no grace at all, and every late document
    // dead on the day), and out-of-range is refused with the backend's own 0-365 bound.
    const parsedOverdueGraceDays = Number(overdueGraceDays);
    if (numberFieldError(overdueGraceDays, WINDOW_DAYS_RULES)) {
      setShowErrors(true);
      setFormError('Overdue grace (days) must be between 0 and 365.');
      return;
    }

    const parsedOverdueDeadDays = Number(overdueDeadDays);
    if (numberFieldError(overdueDeadDays, WINDOW_DAYS_RULES)) {
      setShowErrors(true);
      setFormError('Overdue dead after (days) must be between 0 and 365.');
      return;
    }

    setFormError(null);
    try {
      await save.mutateAsync({
        factors: parsedFactors,
        demand_class_weights: parsedClasses,
        reorder_coverage_until: reorderCoverageUntil || null,
        tba_date_from: tba,
        transfer_days: Math.trunc(parsedTransferDays),
        immediate_window_days: Math.trunc(parsedImmediateWindowDays),
        pool_share_pct: Math.trunc(parsedPoolSharePct),
        overdue_grace_days: Math.trunc(parsedOverdueGraceDays),
        overdue_dead_days: Math.trunc(parsedOverdueDeadDays),
      });
    } catch {
      // Already toasted by the mutation's own onError; this only stops the rejection
      // `mutateAsync` rethrows from reaching the click handler unhandled.
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardHeading>
          <CardTitle>Fulfilment priority</CardTitle>
        </CardHeading>
      </CardHeader>
      <CardContent className="space-y-6">
        {!data?.exists && !isLoading ? (
          <Alert>
            <AlertDescription>
              No fulfilment priority has been activated yet - the seeded
              defaults below are shown. Save to activate them.
            </AlertDescription>
          </Alert>
        ) : null}

        {isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              Failed to load the fulfilment priority policy.
            </AlertDescription>
          </Alert>
        ) : null}

        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : (
          <>
            <div>
              <h4 className="mb-3 text-sm font-medium">Ranking factors</h4>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {FULFILMENT_FACTOR_ORDER.map((key) => (
                  <div key={key}>
                    <Label
                      htmlFor={`fulfilment-factor-${key}`}
                      className="mb-1 block"
                    >
                      {fulfilmentFactorLabel(key)}
                    </Label>
                    <NumberInput
                      id={`fulfilment-factor-${key}`}
                      min={0}
                      max={10}
                      step={0.5}
                      inputMode="decimal"
                      value={factors[key] ?? ''}
                      error={fieldError(factors[key] ?? '', WEIGHT_RULES)}
                      onChange={(e) =>
                        setFactors((prev) => ({
                          ...prev,
                          [key]: e.target.value,
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            </div>

            <div>
              <h4 className="mb-3 text-sm font-medium">Demand class weight</h4>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {FULFILMENT_CLASS_ORDER.map((key) => (
                  <div key={key}>
                    <Label
                      htmlFor={`fulfilment-class-${key}`}
                      className="mb-1 block"
                    >
                      {FULFILMENT_CLASS_LABEL[key]}
                    </Label>
                    <NumberInput
                      id={`fulfilment-class-${key}`}
                      min={0}
                      max={10}
                      step={0.5}
                      inputMode="decimal"
                      value={classWeights[key] ?? ''}
                      error={fieldError(classWeights[key] ?? '', WEIGHT_RULES)}
                      onChange={(e) =>
                        setClassWeights((prev) => ({
                          ...prev,
                          [key]: e.target.value,
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            </div>

            <div>
              <h4 className="mb-3 text-sm font-medium">Coverage dates</h4>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label
                    htmlFor="fulfilment-coverage-until"
                    className="mb-1 block"
                  >
                    Purchasing covers demand until
                  </Label>
                  <div className="flex items-center gap-2">
                    <Input
                      id="fulfilment-coverage-until"
                      type="date"
                      value={reorderCoverageUntil}
                      onChange={(e) => setReorderCoverageUntil(e.target.value)}
                      className="w-full"
                    />
                    {reorderCoverageUntil ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setReorderCoverageUntil('')}
                      >
                        Clear
                      </Button>
                    ) : null}
                  </div>
                  <p className="mt-1 text-2xs text-muted-foreground">
                    Lines required after this date are proposed as Buy now.
                  </p>
                </div>
                <div>
                  <Label
                    htmlFor="fulfilment-tba-date-from"
                    className="mb-1 block"
                  >
                    TBA date from
                  </Label>
                  <div className="flex items-center gap-2">
                    <Input
                      id="fulfilment-tba-date-from"
                      type="date"
                      value={tbaDateFrom}
                      onChange={(e) => setTbaDateFrom(e.target.value)}
                      className="w-full"
                    />
                    {tbaDateFrom !== DEFAULT_TBA_DATE_FROM ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setTbaDateFrom(DEFAULT_TBA_DATE_FROM)}
                      >
                        Reset
                      </Button>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>

            <div>
              <h4 className="mb-3 text-sm font-medium">
                Transfer cost &amp; pool share
              </h4>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label
                    htmlFor="fulfilment-transfer-days"
                    className="mb-1 block"
                  >
                    Transfer days between bins
                  </Label>
                  <NumberInput
                    id="fulfilment-transfer-days"
                    min={0}
                    step={1}
                    inputMode="numeric"
                    value={transferDays}
                    error={fieldError(transferDays, TRANSFER_DAYS_RULES)}
                    onChange={(e) => setTransferDays(e.target.value)}
                    className="w-full"
                  />
                  <p className="mt-1 text-2xs text-muted-foreground">
                    Added to a non-own-location option's fulfil date. 0 charges
                    nothing.
                  </p>
                </div>
                <div>
                  <Label
                    htmlFor="fulfilment-immediate-window-days"
                    className="mb-1 block"
                  >
                    Immediate window (days)
                  </Label>
                  <NumberInput
                    id="fulfilment-immediate-window-days"
                    min={0}
                    max={365}
                    step={1}
                    inputMode="numeric"
                    value={immediateWindowDays}
                    error={fieldError(immediateWindowDays, WINDOW_DAYS_RULES)}
                    onChange={(e) => setImmediateWindowDays(e.target.value)}
                    className="w-full"
                  />
                  <p className="mt-1 text-2xs text-muted-foreground">
                    A line due within this many days may take a share of the
                    site pool now; beyond it, the pool gives the whole line or
                    nothing.
                  </p>
                </div>
                <div>
                  <Label
                    htmlFor="fulfilment-pool-share-pct"
                    className="mb-1 block"
                  >
                    Pool share (%)
                  </Label>
                  <NumberInput
                    id="fulfilment-pool-share-pct"
                    min={0}
                    max={100}
                    step={1}
                    inputMode="numeric"
                    value={poolSharePct}
                    error={fieldError(poolSharePct, POOL_SHARE_RULES)}
                    onChange={(e) => setPoolSharePct(e.target.value)}
                    className="w-full"
                  />
                  <p className="mt-1 text-2xs text-muted-foreground">
                    Kept back from the site pool for dealers; a project line
                    may take up to the rest.
                  </p>
                </div>
                <div>
                  <Label
                    htmlFor="fulfilment-overdue-grace-days"
                    className="mb-1 block"
                  >
                    Overdue grace (days)
                  </Label>
                  <NumberInput
                    id="fulfilment-overdue-grace-days"
                    min={0}
                    max={365}
                    step={1}
                    inputMode="numeric"
                    value={overdueGraceDays}
                    error={fieldError(overdueGraceDays, WINDOW_DAYS_RULES)}
                    onChange={(e) => setOverdueGraceDays(e.target.value)}
                    className="w-full"
                  />
                  <p className="mt-1 text-2xs text-muted-foreground">
                    A document whose arrival has passed is planned against this
                    many days from today.
                  </p>
                </div>
                <div>
                  <Label
                    htmlFor="fulfilment-overdue-dead-days"
                    className="mb-1 block"
                  >
                    Overdue dead after (days)
                  </Label>
                  <NumberInput
                    id="fulfilment-overdue-dead-days"
                    min={0}
                    max={365}
                    step={1}
                    inputMode="numeric"
                    value={overdueDeadDays}
                    error={fieldError(overdueDeadDays, WINDOW_DAYS_RULES)}
                    onChange={(e) => setOverdueDeadDays(e.target.value)}
                    className="w-full"
                  />
                  <p className="mt-1 text-2xs text-muted-foreground">
                    Past this much lateness a document counts as nothing at
                    all.
                  </p>
                </div>
              </div>
            </div>
          </>
        )}
      </CardContent>
      <CardFooter className="justify-end">
        <Button
          onClick={() => void onSave()}
          disabled={isLoading || save.isPending}
        >
          {save.isPending ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : null}
          Save fulfilment priority
        </Button>
      </CardFooter>
    </Card>
  );
}
