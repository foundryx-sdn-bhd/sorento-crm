'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { DatePicker } from '@/components/ui/date-picker';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import type { AutocountPullScope } from '../types/autocountPull.types';

/** The gateway's own default window (DO-PULL-SS contract 16): the last 31 days. */
export const DEFAULT_WINDOW_DAYS = 31;

/** Today's calendar day in Malaysia, as `YYYY-MM-DD` - the day AutoCount's own DocDate uses. */
export function todayInMalaysia(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kuala_Lumpur',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
}

function dayToDate(day: string): Date {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(year, month - 1, date);
}

function dateToDay(value: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}

/** The window the dialog opens with: the 31 days ending today (owner Q1, prefilled). */
export function defaultWindow(now: Date = new Date()): { fromDay: string; toDay: string } {
  const to = dayToDate(todayInMalaysia(now));
  const from = new Date(to);
  from.setDate(to.getDate() - (DEFAULT_WINDOW_DAYS - 1));
  return { fromDay: dateToDay(from), toDay: dateToDay(to) };
}

export interface PullScopeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the window to pull (both days set) or `null` when both were cleared (the
   *  gateway's default). The dialog closes itself before calling. */
  onPull: (scope: AutocountPullScope | null) => void | Promise<void>;
  companyLabel?: string;
  /** What is pulled, in the title and the sentence under it; delivery orders when absent. */
  documentLabel?: string;
  /** Both days required, no "leave both empty" default: the GRN gateway refuses a build with
   *  no scope (ss#107). */
  requireWindow?: boolean;
}

/**
 * "Pull delivery orders from AutoCount": the DocDate window, prefilled with the last 31 days
 * (owner Q1), two date inputs, nothing else (owner Q2: no per-document field). Products and
 * Stock pull the whole book and never see this dialog; a Delivery Orders click with an open
 * pull goes straight to "Review pull" and skips it too.
 */
export function PullScopeDialog({
  open,
  onOpenChange,
  onPull,
  companyLabel,
  documentLabel = 'delivery orders',
  requireWindow = false,
}: PullScopeDialogProps) {
  const [fromDay, setFromDay] = useState<string>('');
  const [toDay, setToDay] = useState<string>('');

  useEffect(() => {
    if (open) {
      const window = defaultWindow();
      setFromDay(window.fromDay);
      setToDay(window.toDay);
    }
  }, [open]);

  const halfWindow = Boolean(fromDay) !== Boolean(toDay);
  const inverted = Boolean(fromDay && toDay && fromDay > toDay);
  const noWindow = !fromDay && !toDay;
  const canPull = !halfWindow && !inverted && !(requireWindow && noWindow);

  const handlePull = async () => {
    if (!canPull) return;
    const scope = fromDay && toDay ? { fromDay, toDay } : null;
    onOpenChange(false);
    await onPull(scope);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Pull {documentLabel} from AutoCount</DialogTitle>
          <DialogDescription>
            {companyLabel ? `${companyLabel}. ` : ''}AutoCount builds a snapshot of the{' '}
            {documentLabel} dated inside this window; you review it before anything changes.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="do-pull-from-day">From day</Label>
            <DatePicker
              id="do-pull-from-day"
              ariaLabel="From day"
              value={fromDay ? dayToDate(fromDay) : undefined}
              onChange={(date) => setFromDay(date ? dateToDay(date) : '')}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="do-pull-to-day">To day</Label>
            <DatePicker
              id="do-pull-to-day"
              ariaLabel="To day"
              value={toDay ? dayToDate(toDay) : undefined}
              onChange={(date) => setToDay(date ? dateToDay(date) : '')}
            />
          </div>
        </div>
        {!requireWindow && (
          <p className="text-xs text-muted-foreground">
            Leave both empty for the last {DEFAULT_WINDOW_DAYS} days.
          </p>
        )}
        {(halfWindow || (requireWindow && noWindow)) && (
          <p className="text-xs text-destructive">
            {requireWindow ? 'Set both days.' : 'Set both days, or clear both.'}
          </p>
        )}
        {inverted && <p className="text-xs text-destructive">From day is after To day.</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handlePull} disabled={!canPull}>
            Pull
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default PullScopeDialog;
