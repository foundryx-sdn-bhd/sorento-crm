'use client';

import { useEffect, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardHeading, CardTitle } from '@/components/ui/card';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { numberFieldError, parseNumberDraft, type NumberFieldRules } from '@/lib/number-field';
import { cn } from '@/lib/utils';
import { useSaveSupplierScoring, useSupplierScoring } from '../hooks/usePolicies';

const WEIGHT_RULES: NumberFieldRules = { min: 0, max: 100 };
const GRACE_RULES: NumberFieldRules = { min: 0 };
const SAMPLE_RULES: NumberFieldRules = { min: 1, integer: true };

/**
 * Global supplier-scoring weights (single row). Delivery + Quality weights blend
 * into each supplier's composite score and MUST sum to 100%. Consumed by the
 * ANALYTICS job, so edits take effect on the next analytics run.
 */
export function SupplierScoringPanel() {
  const { data, isLoading, isError } = useSupplierScoring();
  const save = useSaveSupplierScoring();

  const [delivery, setDelivery] = useState('');
  const [quality, setQuality] = useState('');
  const [grace, setGrace] = useState('');
  const [minSample, setMinSample] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  // Set once Save found a bad number: each field's sentence then follows its value live.
  const [showErrors, setShowErrors] = useState(false);
  const fieldError = (raw: string, rules: NumberFieldRules) =>
    showErrors ? numberFieldError(raw, rules) : undefined;

  useEffect(() => {
    if (!data) return;
    setDelivery(String(Math.round(data.delivery_weight * 100)));
    setQuality(String(Math.round(data.quality_weight * 100)));
    setGrace(String(data.grace_days));
    setMinSample(String(data.min_sample_size));
  }, [data]);

  const deliveryNum = parseNumberDraft(delivery) ?? NaN;
  const qualityNum = parseNumberDraft(quality) ?? NaN;
  const weightSum =
    Number.isFinite(deliveryNum) && Number.isFinite(qualityNum) ? deliveryNum + qualityNum : NaN;
  const sumOk = Math.abs(weightSum - 100) < 0.1;
  const weightsValid =
    !numberFieldError(delivery, WEIGHT_RULES) && !numberFieldError(quality, WEIGHT_RULES);

  const onSave = async () => {
    if (!weightsValid) {
      setShowErrors(true);
      setFormError('Each weight must be between 0 and 100%.');
      return;
    }
    if (!sumOk) {
      setFormError('Delivery and quality weights must add up to 100%.');
      return;
    }
    const graceNum = Number(grace);
    const minSampleNum = Number(minSample);
    if (numberFieldError(grace, GRACE_RULES)) {
      setShowErrors(true);
      setFormError('On-time grace must be 0 or more days.');
      return;
    }
    if (numberFieldError(minSample, SAMPLE_RULES)) {
      setShowErrors(true);
      setFormError('Minimum sample size must be a whole number of 1 or more.');
      return;
    }
    setFormError(null);
    await save.mutateAsync({
      delivery_weight: deliveryNum / 100,
      quality_weight: qualityNum / 100,
      grace_days: graceNum,
      min_sample_size: minSampleNum,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardHeading>
          <CardTitle>Supplier scoring</CardTitle>
        </CardHeading>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">
          Each supplier gets a composite performance score that blends how reliably they deliver{' '}
          <strong>on time</strong> with their <strong>quality</strong> (accepted vs rejected
          receipts). The two weights set how much each matters and must total 100%.
        </p>

        {!data?.exists && !isLoading ? (
          <Alert>
            <AlertDescription>
              No scoring policy saved yet - the seeded defaults below are shown. Save to store them.
            </AlertDescription>
          </Alert>
        ) : null}

        {isError ? (
          <Alert variant="destructive">
            <AlertDescription>Failed to load supplier scoring.</AlertDescription>
          </Alert>
        ) : null}

        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="scoring-delivery" className="mb-1 block">Delivery weight (%)</Label>
                <NumberInput id="scoring-delivery" min={0} max={100} inputMode="numeric" value={delivery} error={fieldError(delivery, WEIGHT_RULES)} onChange={(e) => setDelivery(e.target.value)} />
                <p className="mt-1 text-2xs text-muted-foreground">How much on-time delivery counts.</p>
              </div>
              <div>
                <Label htmlFor="scoring-quality" className="mb-1 block">Quality weight (%)</Label>
                <NumberInput id="scoring-quality" min={0} max={100} inputMode="numeric" value={quality} error={fieldError(quality, WEIGHT_RULES)} onChange={(e) => setQuality(e.target.value)} />
                <p className="mt-1 text-2xs text-muted-foreground">How much receipt quality counts.</p>
              </div>
            </div>

            <div
              className={cn(
                'flex items-center justify-between rounded-md border px-3 py-2 text-sm',
                sumOk ? 'border-border bg-muted/30 text-muted-foreground' : 'border-destructive/40 bg-destructive/5 text-destructive',
              )}
            >
              <span>Weights total</span>
              <span className="font-semibold tabular-nums">
                {Number.isFinite(weightSum) ? `${weightSum}%` : '-'}
                {!sumOk ? ' (must be 100%)' : ''}
              </span>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="scoring-grace" className="mb-1 block">On-time grace (days)</Label>
                <NumberInput id="scoring-grace" min={0} inputMode="numeric" value={grace} error={fieldError(grace, GRACE_RULES)} onChange={(e) => setGrace(e.target.value)} />
                <p className="mt-1 text-2xs text-muted-foreground">
                  A delivery this many days late still counts as on time.
                </p>
              </div>
              <div>
                <Label htmlFor="scoring-sample" className="mb-1 block">Minimum sample size</Label>
                <NumberInput id="scoring-sample" min={1} inputMode="numeric" value={minSample} error={fieldError(minSample, SAMPLE_RULES)} onChange={(e) => setMinSample(e.target.value)} />
                <p className="mt-1 text-2xs text-muted-foreground">
                  How many receipts a supplier needs before their score is trusted.
                </p>
              </div>
            </div>
          </>
        )}

        <p className="rounded-md bg-muted/40 px-3 py-2 text-2xs text-muted-foreground">
          Takes effect on the next analytics run, which recomputes supplier scores.
        </p>
      </CardContent>
      <CardFooter className="justify-end">
        <Button onClick={() => void onSave()} disabled={isLoading || save.isPending || (weightsValid && !sumOk)}>
          {save.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
          Save scoring
        </Button>
      </CardFooter>
    </Card>
  );
}
