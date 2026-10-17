'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CalendarClock, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { formatDateTime, parseDateTimeAsUTC } from '@/lib/helpers';
import {
  getExtendPreview,
  type ExtendSLAInput,
  type ExtendSLAPreview,
} from '../services/conversationSLATrackingService';
import { useExtendSLATracking } from '../hooks/useExtendSLATracking';

type Mode = 'days' | 'date';

interface ExtendDueDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trackingId: string;
  /** Current resolution deadline (naive-UTC ISO), used to seed the min date and the
   *  "Current due" line. */
  currentDueAt: string | null;
  /** Human-readable row label (e.g. "Complaint · CMP-0001") shown in the header. */
  label?: string;
  /** Fired after a successful extend (in addition to the hook's query invalidation),
   *  e.g. to refetch a surface keyed outside react-query. */
  onExtended?: () => void;
}

/** Day-after the current due, as a YYYY-MM-DD string, for the date input's `min`. */
function minDateString(currentDueAt: string | null): string | undefined {
  if (!currentDueAt) return undefined;
  const d = parseDateTimeAsUTC(currentDueAt);
  if (Number.isNaN(d.getTime())) return undefined;
  d.setDate(d.getDate() + 1);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export default function ExtendDueDialog({
  open,
  onOpenChange,
  trackingId,
  currentDueAt,
  label,
  onExtended,
}: ExtendDueDialogProps) {
  const [mode, setMode] = useState<Mode>('days');
  const [days, setDays] = useState('1');
  const [targetDate, setTargetDate] = useState('');
  const [reason, setReason] = useState('');
  // Set by an Extend click that found the days wrong; the sentence then follows the typing.
  const [daysChecked, setDaysChecked] = useState(false);
  const daysError = numberFieldError(days, { min: 1, integer: true });

  const [preview, setPreview] = useState<ExtendSLAPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const extendMutation = useExtendSLATracking();
  const minDate = useMemo(() => minDateString(currentDueAt), [currentDueAt]);

  // Reset all state whenever the dialog (re)opens for a row.
  useEffect(() => {
    if (open) {
      setMode('days');
      setDays('1');
      setTargetDate('');
      setReason('');
      setDaysChecked(false);
      setPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
    }
  }, [open, trackingId]);

  // The currently-active input as the preview/submit payload (exactly one of the
  // two fields), or null when the input is incomplete/invalid.
  const activeInput = useMemo<ExtendSLAInput | null>(() => {
    if (mode === 'days') {
      const n = Number(days);
      if (!Number.isInteger(n) || n < 1) return null;
      return { days: n };
    }
    if (!targetDate) return null;
    return { target_date: targetDate };
  }, [mode, days, targetDate]);

  // Debounced (~400ms) preview as the user types days / picks a date.
  useEffect(() => {
    if (!open || !activeInput) {
      setPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }
    let active = true;
    setPreviewLoading(true);
    setPreviewError(null);
    const handle = setTimeout(() => {
      getExtendPreview(trackingId, activeInput)
        .then((res) => {
          if (active) {
            setPreview(res);
            setPreviewLoading(false);
          }
        })
        .catch((e: unknown) => {
          if (active) {
            setPreview(null);
            setPreviewError(e instanceof Error ? e.message : 'Failed to preview extension');
            setPreviewLoading(false);
          }
        });
    }, 400);
    return () => {
      active = false;
      clearTimeout(handle);
    };
  }, [open, trackingId, activeInput]);

  const reasonTrimmed = reason.trim();
  const canSubmit =
    (mode === 'days' || !!activeInput) &&
    !!reasonTrimmed &&
    !previewLoading &&
    !extendMutation.isPending;

  const handleSubmit = () => {
    if (mode === 'days' && daysError) {
      setDaysChecked(true);
      return;
    }
    if (!activeInput || !reasonTrimmed) return;
    extendMutation.mutate(
      { id: trackingId, ...activeInput, reason: reasonTrimmed },
      {
        onSuccess: () => {
          onOpenChange(false);
          onExtended?.();
        },
      },
    );
  };

  const currentDueText = currentDueAt
    ? formatDateTime(parseDateTimeAsUTC(currentDueAt))
    : '-';
  const newDueText =
    preview?.new_due_at ? formatDateTime(parseDateTimeAsUTC(preview.new_due_at)) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* The dialog is portaled, but React synthetic events still bubble through the
          React tree to the pending-task row that owns this button - whose Space/Enter
          and click handlers open the Respond.io inbox. Stop propagation here so typing
          a space in the reason box (or clicking inside the dialog) never activates the
          row. Radix's Escape-to-close uses a document-level listener, so this is safe. */}
      <DialogContent
        className="sm:max-w-md"
        onKeyDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CalendarClock className="size-4" />
            Extend resolution deadline
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-1">
          {label ? (
            <p className="truncate text-sm text-muted-foreground" title={label}>
              {label}
            </p>
          ) : null}

          <div>
            <p className="text-sm text-muted-foreground">Current due</p>
            <p className="font-medium">{currentDueText}</p>
          </div>

          {/* Mode toggle: two views of one target datetime. */}
          <div className="inline-flex rounded-md border p-0.5">
            <Button
              type="button"
              size="sm"
              variant={mode === 'days' ? 'primary' : 'ghost'}
              className="h-7 px-2.5"
              onClick={() => setMode('days')}
            >
              Working days
            </Button>
            <Button
              type="button"
              size="sm"
              variant={mode === 'date' ? 'primary' : 'ghost'}
              className="h-7 px-2.5"
              onClick={() => setMode('date')}
            >
              Specific date
            </Button>
          </div>

          {mode === 'days' ? (
            <div className="space-y-1.5">
              <Label htmlFor="extend-days">Additional working days</Label>
              <NumberInput
                id="extend-days"
                min={1}
                step={1}
                value={days}
                onChange={(e) => setDays(e.target.value)}
                error={daysChecked ? daysError : undefined}
                autoFocus
              />
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="extend-date">New due date</Label>
              <Input
                id="extend-date"
                type="date"
                min={minDate}
                value={targetDate}
                onChange={(e) => setTargetDate(e.target.value)}
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                Must be after the current due date. Time of day is preserved.
              </p>
            </div>
          )}

          {/* Live preview from the backend (debounced). */}
          <div
            className="min-h-[2.5rem] rounded-md border bg-muted/40 px-3 py-2 text-sm"
            aria-live="polite"
          >
            {previewLoading ? (
              <span className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" /> Calculating…
              </span>
            ) : previewError ? (
              <span className="text-destructive">{previewError}</span>
            ) : newDueText ? (
              <span>
                <span className="text-muted-foreground">New due: </span>
                <span className="font-medium">{newDueText}</span>
                {preview ? (
                  <span className="text-muted-foreground">
                    {' '}
                    (+{preview.working_days} working day
                    {preview.working_days === 1 ? '' : 's'})
                  </span>
                ) : null}
              </span>
            ) : (
              <span className="text-muted-foreground">
                {mode === 'days'
                  ? 'Enter the number of working days to add.'
                  : 'Pick a date to preview the new deadline.'}
              </span>
            )}
          </div>

          {/* Non-blocking soft-limit warnings - do NOT disable submit (UAC-13/16). */}
          {preview?.warnings?.length ? (
            <div className="space-y-1.5">
              {preview.warnings.map((w, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200"
                >
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="space-y-1.5">
            <Label htmlFor="extend-reason">
              Reason <span className="text-destructive">*</span>
            </Label>
            <Textarea
              id="extend-reason"
              placeholder="Why does this deadline need to move?"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={extendMutation.isPending}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            {extendMutation.isPending ? 'Extending…' : 'Extend deadline'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
