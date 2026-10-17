'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { DatePicker } from '@/components/ui/date-picker';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { formatLocalDateToYyyyMmDd, todayMalaysiaYyyyMmDd } from '@/lib/helpers';
import { usePointRatios, useSavePointRatio } from '../hooks/useSalesTargets';
import { formatFigure, shortDate } from '../lib/format';

/**
 * RM per point, a dated history for the acting company (owner ruling 5, Q4; mock frame E). A
 * period uses the ratio in force on its last day; the same date again replaces that date's ratio.
 */
export default function PointRatioDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data, isLoading } = usePointRatios(open);
  const save = useSavePointRatio();
  const [ratio, setRatio] = useState('');
  const [from, setFrom] = useState(() => todayMalaysiaYyyyMmDd());
  const value = Number(ratio);
  const canAdd = ratio.trim() !== '' && Number.isFinite(value) && value > 0 && !!from && !save.isPending;
  const rows = data?.rows ?? [];

  const add = async () => {
    if (!canAdd) return;
    try {
      await save.mutateAsync({ effective_from: from, rm_per_point: value });
      setRatio('');
    } catch {
      // The hook toasted the reason.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Point ratio</DialogTitle>
          <DialogDescription>RM per point, from a date on.</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div className="flex flex-col gap-1">
              <Label htmlFor="point-ratio-value" className="text-xs text-muted-foreground">
                RM per point
              </Label>
              <NumberInput
                id="point-ratio-value"
                min={0}
                step="any"
                value={ratio}
                onChange={(e) => setRatio(e.target.value)}
                className="h-8"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="point-ratio-from" className="text-xs text-muted-foreground">
                From
              </Label>
              <DatePicker
                id="point-ratio-from"
                ariaLabel="From"
                // A civil date: noon keeps it on the same day in any time zone.
                value={from ? new Date(`${from}T12:00:00`) : undefined}
                onChange={(d) => setFrom(d ? formatLocalDateToYyyyMmDd(d) : '')}
              />
            </div>
            <Button size="sm" onClick={() => void add()} disabled={!canAdd}>
              Add
            </Button>
          </div>
          {isLoading ? null : rows.length ? (
            <div className="overflow-x-auto rounded-lg border">
              <table aria-label="Point ratios" className="w-full text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 text-start font-medium">From</th>
                    <th className="px-3 py-2 text-end font-medium">RM per point</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td className="whitespace-nowrap px-3 py-2">{shortDate(r.effective_from)}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">{formatFigure(r.rm_per_point)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed py-6 text-center text-sm font-medium">No ratio yet</div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
