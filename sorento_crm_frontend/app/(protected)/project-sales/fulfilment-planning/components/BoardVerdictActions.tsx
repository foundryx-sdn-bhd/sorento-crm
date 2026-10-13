'use client';

import * as React from 'react';
import { Check, Pencil, Undo2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import { verdictOf } from './BoardDecisionPill';
import { suggestedDecisionFor } from '../../_shared/lib/boardAmend';
import type {
  BoardContribution,
  BoardDecision,
} from '../../_shared/types/fulfilmentPlanning.types';

/**
 * What a planner can do about ONE line without leaving the row it is on
 * (`board-verdict-actions-chips-acceptance-criteria.md` section B, owner finding 22 Sep 2026
 * on SO402757: a "Change proposed" row offered no accept and no reject at all, so answering
 * the book meant opening every row in turn).
 *
 * ONE component for both readings of the board (AC-B11) - the list view's Verdict column and
 * the cell breakdown's Decision column - because they are two views of one draft and a
 * planner toggles between them to act on the same line. Two sets of icons that merely
 * resembled each other would drift the first time either changed.
 *
 * WHICH buttons show is read off `verdictOf` (`BoardDecisionPill`), never a second derivation
 * beside it: the pill and the buttons answer the same question ("how far has this line got"),
 * and a line whose pill says `Suggestion changed` must not be offered a quick Save of a
 * suggestion the engine has since withdrawn.
 *
 * THE REJECT POPOVER'S STATE LIVES HERE, not in a child of the branch that renders it
 * (BL-1, reviewer, fix round 2). `FulfilmentBoardPanel.decide` is local-first: it writes the
 * session draft SYNCHRONOUSLY, awaits the network, and reverts the key when the write fails.
 * So pressing Reject flips this line's verdict to `rejected` before `onDecide` has resolved,
 * which stops it being a "proposed" line - and a popover mounted inside that condition would
 * unmount mid-write, taking the typed reason with it, then come back blank when the revert
 * lands. This component's own identity survives the flip (the cell is re-rendered, never
 * remounted), so the reason does too.
 */
export function BoardVerdictActions({
  contribution,
  decision,
  onDecide,
  onChange,
}: {
  contribution: BoardContribution;
  /** This line's entry in the board's session draft, or null while nobody decided it here. */
  decision: BoardDecision | null;
  /**
   * The board's own write path, per line. Resolves `false` when the write failed, which is
   * what keeps the reject popover open over a reason the planner would otherwise retype.
   */
  onDecide: (decision: BoardDecision | null) => Promise<boolean> | void;
  /** Open this line's decision panel (AC-B10) - the caller owns the expansion state. */
  onChange: () => void;
}) {
  const [rejecting, setRejecting] = React.useState(false);
  const [reason, setReason] = React.useState('');
  const [suspected, setSuspected] = React.useState(false);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const fieldId = React.useId();

  const verdict = verdictOf(contribution, decision);
  const label = `${contribution.so_number} line ${contribution.line_no}`;
  const blank = reason.trim().length === 0;

  const submitRejection = async () => {
    if (blank) return;
    const written = await onDecide({
      verdict: 'rejected',
      reason: reason.trim(),
      suspected_system_issue: suspected,
    });
    // A failed write leaves the popover exactly as it was, typed reason included: the
    // planner said it once, and the board losing it would be the board's fault twice.
    if (written === false) return;
    setRejecting(false);
  };

  // Nothing to decide, and nothing to undo (AC-B5): the book removed the line, or its sales
  // order states no fulfilment location, and the pill already says which.
  if (verdict === 'cancelled' || verdict === 'unplannable') return null;

  // The engine's own proposal is still the live answer - either untouched (`suggested`) or
  // pre-marked by the open change batch and not yet written (`change_proposed`, which
  // `verdictOf` reads off `isPreMarkOnly`) - so it can be taken or refused in one press
  // (AC-B1/AC-B2).
  const proposed = verdict === 'suggested' || verdict === 'change_proposed';
  // Something HAS been written for this line - here, or by another planner, or against a
  // suggestion the engine has since changed - so the one thing left is to unwrite it
  // (AC-B3). A confirmed line has no session draft to shed - Reject on it is a fresh
  // decision, not an undo (R3(b), `PLAN-board-reject-on-confirmed-line.md`).
  const undoable = verdict === 'saved' || verdict === 'rejected' || verdict === 'stale';
  // AC-R1 (`board-reject-on-confirmed-line-acceptance-criteria.md`, R3(b) - replaces AC-B4
  // of `PLAN-board-verdict-actions-chips.md`, "Confirmed line gets Change decision only"):
  // rejecting a covered line is now a real action, not a dead button, so the X joins the
  // pencil beside a `confirmed` pill too. Accept stays proposed-only - there is nothing to
  // "accept" on a line already confirmed.
  //
  // N1 (fix round, `PLAN-board-reject-on-confirmed-line.md`): a `confirmed` pill also covers
  // an INQUIRY-DECIDED line - a live order-inquiry row names it with no active decision at
  // all (#875, migrated sheet lines) - and `Confirm` has nothing to withdraw there: no
  // `SOSupplyDecision`, so no `line_snapshots` entry `rejected_line_ids` could ever name.
  // The X is offered only where an ACTIVE decision covers the line (`contribution.decision`
  // is the frozen decision object, non-null exactly then), not merely where the pill reads
  // Confirmed.
  const rejectable = proposed || (verdict === 'confirmed' && Boolean(contribution.decision));

  return (
    <>
      {proposed ? (
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          className="shrink-0"
          title="Save as suggested"
          aria-label={`Save ${label} as suggested`}
          onClick={(event) => {
            // The row is a clickable surface of its own (it opens the decision panel); every
            // button here sits on top of it and must not trigger it (AC-B6).
            event.stopPropagation();
            // R2: accept writes what Confirm would have written for this line, so accepting
            // the book's proposal and confirming it cannot post two different compositions.
            void onDecide(suggestedDecisionFor(contribution));
          }}
        >
          <Check className="size-3.5" aria-hidden />
        </Button>
      ) : null}
      {/* Mounted for as long as the line is refusable OR the refusal is still on screen
          (BL-1): the second half is the in-flight write, where the draft already says
          `rejected` and the planner is still looking at their own reason. The TRIGGER stays
          with it rather than being dropped on `proposed` alone - Radix anchors this content
          on the trigger, and a content with no anchor jumps to the corner of the viewport
          for exactly as long as the write takes. Once the write settles, either the popover
          closed itself (success) or the draft reverted (failure), so a settled `Rejected`
          line shows no X: `rejecting` is false by then. */}
      {rejectable || rejecting ? (
        <Popover
          open={rejecting}
          onOpenChange={(next) => {
            setRejecting(next);
            // Every opening starts blank (AC-B9): a reason abandoned on Escape belongs to
            // the press that abandoned it, not to the next line the planner looks at.
            if (next) {
              setReason('');
              setSuspected(false);
            }
          }}
        >
          <PopoverTrigger asChild onClick={(event) => event.stopPropagation()}>
            <Button
              type="button"
              mode="icon"
              variant="ghost"
              size="sm"
              className="shrink-0"
              title="Reject"
              aria-label={`Reject ${label}`}
            >
              <X className="size-3.5" aria-hidden />
            </Button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              align="end"
              className="w-72 space-y-3"
              // A portalled popover still bubbles its React events up to the row this
              // trigger sits in, so typing a reason or ticking the box would otherwise
              // expand the row underneath it (AC-B9).
              onClick={(event) => event.stopPropagation()}
              onOpenAutoFocus={(event) => {
                // The reason is the whole point of the popover, so the caret starts in it -
                // and that is also what puts Escape inside the content, where Radix closes
                // on it.
                event.preventDefault();
                textareaRef.current?.focus();
              }}
            >
              {/* The SAME two fields the decision panel's own footer asks for
                  (`BoardLineDecisionPanel`: "Why this differs" + the system-problem flag),
                  over the X it is pressed on rather than three clicks away inside the
                  expanded row. A reason is required, because a rejection is read by whoever
                  has to answer it - purchasing, or the planner themselves next week - and
                  "rejected" on its own says nothing about what to do instead. */}
              <div className="space-y-1">
                <label
                  className="block text-2xs uppercase tracking-wide text-muted-foreground"
                  htmlFor={`verdict-reason-${fieldId}`}
                >
                  Why this differs
                  <span className="text-destructive"> *</span>
                </label>
                <Textarea
                  id={`verdict-reason-${fieldId}`}
                  ref={textareaRef}
                  rows={2}
                  value={reason}
                  placeholder="In your own words"
                  onChange={(event) => setReason(event.target.value)}
                />
              </div>
              <label
                className="flex items-start gap-2 text-sm"
                htmlFor={`verdict-suspect-${fieldId}`}
              >
                <Checkbox
                  id={`verdict-suspect-${fieldId}`}
                  checked={suspected}
                  onCheckedChange={(value) => setSuspected(value === true)}
                  className="mt-0.5"
                />
                <span>This might be a system problem, flag it for investigation</span>
              </label>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={blank}
                title={blank ? 'Say why this line is being refused first.' : undefined}
                onClick={() => {
                  void submitRejection();
                }}
              >
                <X className="size-4" aria-hidden />
                Reject
              </Button>
            </PopoverContent>
          </PopoverPortal>
        </Popover>
      ) : null}
      {undoable ? (
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          className="shrink-0"
          title="Undo"
          aria-label={`Undo ${label}`}
          onClick={(event) => {
            event.stopPropagation();
            void onDecide(null);
          }}
        >
          <Undo2 className="size-3.5" aria-hidden />
        </Button>
      ) : null}
      <Button
        type="button"
        mode="icon"
        variant="ghost"
        size="sm"
        className="shrink-0"
        title="Change decision"
        aria-label={`Change decision for ${label}`}
        onClick={(event) => {
          event.stopPropagation();
          onChange();
        }}
      >
        <Pencil className="size-3.5" aria-hidden />
      </Button>
    </>
  );
}

export default BoardVerdictActions;
