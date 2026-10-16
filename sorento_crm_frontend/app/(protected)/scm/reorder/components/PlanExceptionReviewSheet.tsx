'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, Check, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { EM_DASH, fmtInt } from '../../lib/format';
import { dayLabel } from '../lib/coverageTimeline';
import {
  ACTION_LABELS,
  EXCEPTION_TYPE_LABELS,
  type PlanException,
  type PlanExceptionActionCode,
  type PlanExceptionDecisionInput,
  type ProposedAction,
  type ReadingSignal,
  type TimelinePoint,
} from '../types/planException.types';
import { distinctName } from '@/lib/product-display';

/**
 * Reviewing one Plan Exception (UAC Group D), as a right slide-over off the row.
 *
 * Three things this panel does that a plainer confirmation dialog would not, each
 * because an exception asks somebody to change a supplier's placed order:
 *
 *  1. **Before and after sit side by side** (AC-D4). Showing only the new position asks
 *     the reviewer to hold the old one in their head, and the whole judgement is which
 *     of the two they believe.
 *  2. **The reading is displayed with its sources** (AC-D12), not merely applied. The
 *     proposed actions are ordered BY that reading (AC-D10), so a reviewer who cannot
 *     see it can only disagree with the outcome - never with the reasoning that produced
 *     it. Each signal names the field it came from.
 *  3. **The first action is labelled as the engine's proposal, and is not preselected as
 *     an inevitability.** A reviewer must be able to take the third option without
 *     fighting the form.
 *
 * Rejecting requires a reason (AC-D6). Approving a split requires a quantity strictly
 * inside the exception's own (AC-D11b) - the remainder stays on the original line, so the
 * two parts sum to it and a partial change is representable at all.
 */

function SignalRow({ label, signal }: { label: string; signal: ReadingSignal }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-2xs text-muted-foreground">{label}</span>
      <span className="min-w-0 text-end">
        <span className="text-xs font-medium">
          {signal.value ?? <span className="text-muted-foreground">not recorded</span>}
        </span>
        {/* The source field, so the reasoning is arguable and not magic (AC-D12). */}
        <span className="block truncate text-2xs text-muted-foreground" title={signal.source}>
          {signal.source}
        </span>
      </span>
    </div>
  );
}

function TimelineColumn({
  title,
  points,
  shortfallAt,
  shortfallQty,
  tone,
}: {
  title: string;
  points: TimelinePoint[];
  shortfallAt: string | null;
  shortfallQty: number | null;
  tone: 'before' | 'after';
}) {
  return (
    <div className="min-w-0 flex-1 rounded-lg border p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-2xs font-medium uppercase text-muted-foreground">{title}</span>
        {shortfallAt ? (
          <span
            className={cn(
              'text-2xs font-medium',
              tone === 'after' ? 'text-scm-stockout' : 'text-muted-foreground',
            )}
            title="Peak deficit, which is the figure the buy plan is built from"
          >
            short {fmtInt(shortfallQty ?? 0)} on {dayLabel(shortfallAt)}
          </span>
        ) : (
          // A legitimate answer, not a missing value: nothing committed is uncovered.
          <span className="text-2xs text-muted-foreground">no shortfall</span>
        )}
      </div>
      <ul className="space-y-1">
        {points.map((p) => (
          <li key={`${p.date}-${p.label ?? 'net'}`} className="flex items-baseline gap-2">
            <span className="w-24 shrink-0 text-2xs text-muted-foreground">
              {dayLabel(p.date)}
            </span>
            <span
              className={cn(
                'w-16 shrink-0 text-end text-xs tabular-nums',
                p.net < 0 && 'font-medium text-scm-stockout',
              )}
            >
              {fmtInt(p.net)}
            </span>
            <span className="min-w-0 truncate text-2xs text-muted-foreground" title={p.label ?? ''}>
              {p.label ?? ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ActionCard({
  action,
  selected,
  onSelect,
  disabled,
}: {
  action: ProposedAction;
  selected: boolean;
  onSelect: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={cn(
        'w-full rounded-lg border p-3 text-start transition-colors',
        selected ? 'border-primary bg-primary/5' : 'hover:bg-muted/50',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{ACTION_LABELS[action.code]}</span>
        {action.rank === 1 ? (
          <span
            className="rounded-full bg-primary/10 px-2 py-0.5 text-2xs font-medium text-primary"
            title="Ranked first by the item's reading, not by quantity"
          >
            Proposed first
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-2xs text-muted-foreground">{action.rationale}</p>
      {action.candidate_so_number ? (
        <p className="mt-1 flex items-center gap-1 text-2xs">
          <ArrowRight className="size-3" />
          {action.candidate_so_number}
          {action.candidate_need_by ? (
            <span className="text-muted-foreground">
              {' '}
              · needed {dayLabel(action.candidate_need_by)}
            </span>
          ) : null}
        </p>
      ) : null}
      {action.candidate_warehouse_code ? (
        <p className="mt-1 flex items-center gap-1 text-2xs">
          <ArrowRight className="size-3" />
          {action.candidate_warehouse_code}
        </p>
      ) : null}
    </button>
  );
}

export interface PlanExceptionReviewSheetProps {
  exception: PlanException | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDecide: (input: PlanExceptionDecisionInput) => void;
  isSaving?: boolean;
}

export function PlanExceptionReviewSheet({
  exception,
  open,
  onOpenChange,
  onDecide,
  isSaving = false,
}: PlanExceptionReviewSheetProps) {
  const [selected, setSelected] = useState<PlanExceptionActionCode | null>(null);
  const [reason, setReason] = useState('');
  const [splitQty, setSplitQty] = useState('');

  // Reset per exception, never carry one row's half-typed reason onto the next.
  useEffect(() => {
    setSelected(null);
    setReason('');
    setSplitQty('');
  }, [exception?.exception_id]);

  const decided = exception ? exception.status !== 'open' : false;
  const splitValue = Number(splitQty);
  const splitInvalid = useMemo(() => {
    if (selected !== 'split') return false;
    if (!splitQty.trim()) return true;
    if (!Number.isFinite(splitValue)) return true;
    // Strictly inside, because the remainder is what stays on the original line: a split
    // of the whole quantity is a move, and a split of zero is nothing (AC-D11b).
    return splitValue <= 0 || splitValue >= (exception?.quantity ?? 0);
  }, [selected, splitQty, splitValue, exception?.quantity]);

  if (!exception) return null;

  const canApprove = selected !== null && !splitInvalid && !decided;
  const canReject = reason.trim().length > 0 && !decided;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {/* The BODY scrolls and the footer stays put, rather than the whole panel scrolling.
          The two timelines, the reading and four action cards are taller than a laptop
          viewport, and with a footer that scrolls away the Approve button sat below the fold
        - present in the DOM, unreachable by clicking, which is the worst of both. */}
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl"
        aria-describedby={undefined}
      >
        <SheetHeader className="border-b p-4 pe-12 sm:p-6 sm:pe-12">
          <SheetTitle className="break-words">
            {exception.product_code}
            {distinctName(exception.product_code, exception.product_name) ? (
              <>
                {/* An explicit space, because the accessible NAME of the heading
                    concatenates its children and a margin is not a word break: without
                    it a screen reader announces "SRT367-GMSorento 367 Gunmetal". */}{' '}
                <span className="text-sm font-normal text-muted-foreground">
                  {exception.product_name}
                </span>
              </>
            ) : null}
          </SheetTitle>
          <SheetDescription>
            {EXCEPTION_TYPE_LABELS[exception.exception_type]} ·{' '}
            {fmtInt(exception.quantity)} {exception.uom ?? ''}
            {exception.po_number ? ` · ${exception.po_number}` : ''}
            {exception.po_expected_date ? ` due ${dayLabel(exception.po_expected_date)}` : ''}
          </SheetDescription>
        </SheetHeader>

        <SheetBody className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
          <section>
            <h4 className="mb-2 text-2xs font-medium uppercase text-muted-foreground">
              Position before and after the restatement
            </h4>
            <div className="flex flex-col gap-3 sm:flex-row">
              <TimelineColumn
                title="Before"
                points={exception.timeline.before_points}
                shortfallAt={exception.timeline.before_shortfall_at}
                shortfallQty={exception.timeline.before_shortfall_qty}
                tone="before"
              />
              <TimelineColumn
                title="After"
                points={exception.timeline.after_points}
                shortfallAt={exception.timeline.after_shortfall_at}
                shortfallQty={exception.timeline.after_shortfall_qty}
                tone="after"
              />
            </div>
          </section>

          <section>
            <h4 className="mb-1 text-2xs font-medium uppercase text-muted-foreground">
              How this item reads
            </h4>
            <div className="rounded-lg border px-3 py-1">
              <SignalRow label="Lifecycle" signal={exception.reading.lifecycle} />
              <SignalRow label="Velocity" signal={exception.reading.velocity} />
              <SignalRow label="Business" signal={exception.reading.business} />
              <SignalRow label="Last purchased" signal={exception.reading.last_po} />
            </div>
          </section>

          <section>
            <h4 className="mb-2 text-2xs font-medium uppercase text-muted-foreground">
              What can be done, best first for this item
            </h4>
            <div className="space-y-2">
              {exception.actions.map((a) => (
                <ActionCard
                  key={a.code}
                  action={a}
                  selected={selected === a.code}
                  onSelect={() => setSelected(a.code)}
                  disabled={decided || isSaving}
                />
              ))}
            </div>

            {selected === 'split' ? (
              <div className="mt-3 space-y-1">
                <Label htmlFor="split-qty" className="text-2xs">
                  Quantity to move (the rest stays on the original line)
                </Label>
                <Input
                  id="split-qty"
                  value={splitQty}
                  onChange={(e) => setSplitQty(e.target.value)}
                  inputMode="numeric"
                  className="h-8 w-40"
                  aria-invalid={splitInvalid}
                />
                {splitInvalid ? (
                  <p className="flex items-center gap-1 text-2xs text-destructive">
                    <AlertTriangle className="size-3" />
                    Between 1 and {fmtInt(exception.quantity - 1)}.
                  </p>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="space-y-1">
            <Label htmlFor="decision-reason" className="text-2xs">
              Reason {decided ? '' : '(required to reject)'}
            </Label>
            <Textarea
              id="decision-reason"
              value={decided ? (exception.decision_reason ?? '') : reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              disabled={decided || isSaving}
              placeholder="Why this decision, in a sentence"
            />
          </section>

          {decided ? (
            <p className="text-2xs text-muted-foreground">
              {exception.status === 'approved' ? 'Approved' : 'Rejected'} by{' '}
              {exception.decided_by ?? EM_DASH}
              {exception.decided_action
                ? ` · ${ACTION_LABELS[exception.decided_action]}`
                : ''}
            </p>
          ) : null}
        </SheetBody>

        <SheetFooter className="flex-row justify-end gap-2 border-t p-4 sm:p-6">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button
            variant="outline"
            disabled={!canReject || isSaving}
            onClick={() =>
              onDecide({
                exception_id: exception.exception_id,
                status: 'rejected',
                action_code: null,
                reason: reason.trim(),
                split_qty: null,
              })
            }
          >
            <X className="size-4" /> Reject
          </Button>
          <Button
            disabled={!canApprove || isSaving}
            onClick={() =>
              onDecide({
                exception_id: exception.exception_id,
                status: 'approved',
                action_code: selected,
                reason: reason.trim() || null,
                split_qty: selected === 'split' ? splitValue : null,
              })
            }
          >
            <Check className="size-4" /> Approve
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

export default PlanExceptionReviewSheet;
