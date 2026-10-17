'use client';

import { useEffect, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardHeading, CardTitle } from '@/components/ui/card';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { numberFieldError } from '@/lib/number-field';
import { useClassification, useSaveClassification } from '../hooks/usePolicies';

const PCT_RULES = { min: 0, minExclusive: true, max: 100 };
const CV_RULES = { min: 0, minExclusive: true };

/**
 * Global ABC-XYZ classification thresholds (single row). ABC splits items by
 * cumulative value; XYZ splits by demand variability (coefficient of variation).
 * Consumed by the ANALYTICS job, so edits take effect on the next analytics run.
 */
export function ClassificationThresholdsPanel() {
  const { data, isLoading, isError } = useClassification();
  const save = useSaveClassification();

  const [abcA, setAbcA] = useState('');
  const [abcB, setAbcB] = useState('');
  const [xyzX, setXyzX] = useState('');
  const [xyzY, setXyzY] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  // Set by a Save that found a wrong number; the sentences then follow the typing.
  const [checked, setChecked] = useState(false);
  const errors = {
    abcA: numberFieldError(abcA, PCT_RULES),
    abcB: numberFieldError(abcB, PCT_RULES),
    xyzX: numberFieldError(xyzX, CV_RULES),
    xyzY: numberFieldError(xyzY, CV_RULES),
  };

  useEffect(() => {
    if (!data) return;
    setAbcA(String(Math.round(data.abc_a_pct * 100)));
    setAbcB(String(Math.round(data.abc_b_pct * 100)));
    setXyzX(String(data.xyz_x_max));
    setXyzY(String(data.xyz_y_max));
  }, [data]);

  const onSave = async () => {
    if (Object.values(errors).some(Boolean)) {
      setChecked(true);
      setFormError(null);
      return;
    }
    const a = Number(abcA);
    const b = Number(abcB);
    const x = Number(xyzX);
    const y = Number(xyzY);

    if (!(a > 0 && a < 100) || !(b > 0 && b < 100)) {
      setFormError('Class A and B cuts must each be between 0 and 100%.');
      return;
    }
    if (a + b >= 100) {
      setFormError('Class A + B must be below 100% - the remainder is class C.');
      return;
    }
    if (!(x > 0) || !(y > 0)) {
      setFormError('XYZ variability ceilings must be greater than 0.');
      return;
    }
    if (x > y) {
      setFormError('The X ceiling must be less than or equal to the Y ceiling.');
      return;
    }
    setFormError(null);
    await save.mutateAsync({
      abc_a_pct: a / 100,
      abc_b_pct: b / 100,
      xyz_x_max: x,
      xyz_y_max: y,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardHeading>
          <CardTitle>Classification thresholds</CardTitle>
        </CardHeading>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">
          <strong>ABC</strong> ranks items by <em>value</em> (cumulative share of stock value) - A is
          your most valuable items, then B, then C. <strong>XYZ</strong> ranks items by{' '}
          <em>demand variability</em> - X is steady and predictable, Y is variable, Z is erratic.
        </p>

        {!data?.exists && !isLoading ? (
          <Alert>
            <AlertDescription>
              No thresholds saved yet - the seeded defaults below are shown. Save to store them.
            </AlertDescription>
          </Alert>
        ) : null}

        {isError ? (
          <Alert variant="destructive">
            <AlertDescription>Failed to load classification thresholds.</AlertDescription>
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label className="mb-1 block">Class A ≤ (% of cumulative value)</Label>
              <NumberInput min={1} max={99} inputMode="numeric" value={abcA} onChange={(e) => setAbcA(e.target.value)} error={checked ? errors.abcA : undefined} />
              <p className="mt-1 text-2xs text-muted-foreground">
                Items making up the top share of value are class A.
              </p>
            </div>
            <div>
              <Label className="mb-1 block">Class B ≤ (% of cumulative value)</Label>
              <NumberInput min={1} max={99} inputMode="numeric" value={abcB} onChange={(e) => setAbcB(e.target.value)} error={checked ? errors.abcB : undefined} />
              <p className="mt-1 text-2xs text-muted-foreground">
                The next share up to this cut is class B; the remainder is class C.
              </p>
            </div>
            <div>
              <Label className="mb-1 block">Class X ≤ (demand CV)</Label>
              <NumberInput min={0} step="0.05" inputMode="decimal" value={xyzX} onChange={(e) => setXyzX(e.target.value)} error={checked ? errors.xyzX : undefined} />
              <p className="mt-1 text-2xs text-muted-foreground">
                Coefficient of variation. Lower = steadier demand. Items at/under this are class X.
              </p>
            </div>
            <div>
              <Label className="mb-1 block">Class Y ≤ (demand CV)</Label>
              <NumberInput min={0} step="0.05" inputMode="decimal" value={xyzY} onChange={(e) => setXyzY(e.target.value)} error={checked ? errors.xyzY : undefined} />
              <p className="mt-1 text-2xs text-muted-foreground">
                Items above X but at/under this are class Y; anything higher is class Z.
              </p>
            </div>
          </div>
        )}

        <p className="rounded-md bg-muted/40 px-3 py-2 text-2xs text-muted-foreground">
          Takes effect on the next analytics run, which reclassifies items; the following reorder run
          then uses the new classes.
        </p>
      </CardContent>
      <CardFooter className="justify-end">
        <Button onClick={() => void onSave()} disabled={isLoading || save.isPending}>
          {save.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
          Save thresholds
        </Button>
      </CardFooter>
    </Card>
  );
}
