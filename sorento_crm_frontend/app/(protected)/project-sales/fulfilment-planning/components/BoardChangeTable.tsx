'use client';

import * as React from 'react';
import { TriangleAlert } from 'lucide-react';
import { formatDateInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { BoardContribution } from '../../_shared/types/fulfilmentPlanning.types';
import { useLineSplitOpener } from './lineSplitOpener';
import { changedFieldsOf } from '../../_shared/lib/boardChangeAnnotations';
import type { BoardChangeAnnotation } from '../../_shared/lib/boardChangeAnnotations';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';

/**
 * What the book did to this line: ONE amber hazard icon, and a lightbox behind it.
 *
 * Owner feedback, 13 September 2026: the inline Was / Now block this component used to draw
 * on every changed cell was "abit too big", and the list view - the reading a planner
 * actually lives in - could not stay one line tall with a table inside it. So the cell and
 * the row now carry the same warning triangle a Rejected verdict carries, and the detail
 * moves behind a click: "just show whatever changed", as `x -> y`, and nothing else.
 *
 * The icon is the SAME in the grid and in the list, and the dialog is the same component in
 * both, so the two readings of the board cannot come to say different things about one
 * change.
 */
export function BoardChangeTable({
  annotation,
  annotations,
  column,
  compact = false,
  className,
  contribution,
}: {
  /**
   * OI-LINE-SPLIT (UAC A2): the board line these changes are about. With it, and inside the
   * board (`LineSplitOpenerContext`), the lightbox offers "Split delivery" for that line.
   */
  contribution?: BoardContribution;
  /** ONE change - the shape every caller but the list view's own columns passes. */
  annotation?: BoardChangeAnnotation;
  /**
   * EVERY pending change this icon stands for, oldest first (AC-D5, owner finding 22 Sep:
   * "why so many warning signs"). Two batch rows that both moved one line's date are one
   * warning about one line, not two - so the caller hands the group over and the lightbox
   * stacks a Was/Now table per change instead of the row growing a second triangle.
   */
  annotations?: BoardChangeAnnotation[];
  /**
   * Which list column this copy of the icon sits in (AC-C9): `required_date` when the date
   * moved, `outstanding` when the quantity did, `suggested` for the composed suggestion.
   * Read by the tests and by nothing else - the grid has one column and passes none.
   */
  column?: 'required_date' | 'outstanding' | 'suggested';
  /** Inside a board cell, where every pixel costs width. */
  compact?: boolean;
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const splitOpener = useLineSplitOpener();
  const canSplit = Boolean(splitOpener && contribution && splitOpener.canOpen(contribution));
  // One list either way, so nothing below has to ask which prop it was called with.
  const changes = annotations ?? (annotation ? [annotation] : []);
  // The icon is addressed by the FIRST change's row (`board-change-icon-<rowId>`) - the only
  // row a single-change caller has, and the oldest of a group.
  const primary = changes[0];
  if (!primary) return null;
  return (
    <>
      <button
        type="button"
        data-testid={`board-change-icon-${primary.rowId}`}
        data-column={column}
        aria-label="What changed"
        title="What changed"
        onClick={(event) => {
          // The cell and the row are both clickable surfaces of their own (the cell opens
          // its breakdown, the row opens its decision panel); this icon is a third thing
          // on top of them and must not trigger either.
          event.stopPropagation();
          setOpen(true);
        }}
        className={cn(
          'inline-flex shrink-0 items-center justify-center rounded text-amber-600',
          'hover:text-amber-700 focus-visible:outline-none focus-visible:ring-1',
          'focus-visible:ring-amber-500',
          compact ? 'size-4' : 'size-5',
          className,
        )}
      >
        <TriangleAlert className={compact ? 'size-3.5' : 'size-4'} aria-hidden />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          data-testid="board-change-dialog"
          className="max-w-[min(28rem,calc(100vw-2rem))]"
        >
          <DialogHeader>
            <DialogTitle>
              {primary.lineNo
                ? `What changed, ${primary.soNumber} (Line ${primary.lineNo})`
                : `What changed, ${primary.soNumber} (${primary.itemCode})`}
            </DialogTitle>
            {/* Radix points the dialog's own `aria-describedby` at a description it expects
                to exist; without one the attribute resolves to nothing and a screen reader is
                handed a dangling id (and the console a warning). One sentence, which is also
                what a first-time reader needs to know about this list. */}
            <DialogDescription>
              Only the fields that moved, then the suggestion.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <BoardChangeSummary annotations={changes} />
          </DialogBody>
          {canSplit ? (
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Close
              </Button>
              <Button
                type="button"
                onClick={() => {
                  setOpen(false);
                  if (contribution) splitOpener?.open(contribution);
                }}
              >
                Split delivery
              </Button>
            </DialogFooter>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

/**
 * What changed, and what the engine suggests about it - the lightbox's whole body.
 *
 * Only the fields that MOVED, as the WAS / NOW table (AC-D1, owner finding 22 Sep 2026: the
 * `label from -> to` lines this used to print read worse than the Order Inquiries box does).
 * The two sides sit in their own columns, so a reader compares down a column instead of
 * parsing an arrow; a field that did not move gets no row at all (`onlyMovedFields`, which
 * ONLY this lightbox asks for), and a row of dashes is not information. A change that moved
 * nothing at all says so in words rather than drawing an empty box (SF-3).
 *
 * A GROUP of changes (AC-D5) stacks one BLOCK per change, NEWEST FIRST - the caller reads a
 * batch oldest first, and the change a planner is answering is the last one to have
 * happened. Each block is that change's own table AND its own facts: the product it used to
 * be, where its held share went, how late it lands, what has physically moved already
 * (SF-4, captain's ruling: no silent loss - reading those off the newest change alone
 * dropped the older change's facts off the screen with nothing to say they existed). A
 * hairline rule separates the blocks. No heading over them: neither `BoardChangeAnnotation`
 * nor the batch ROW behind it carries a timestamp of its own (only the batch does), and a
 * date invented here would be a fact about nothing.
 *
 * The composed suggestion comes LAST, VERBATIM, and off the NEWEST change alone: the
 * sentence is server-composed because only the engine knows which rung covered what, against
 * which document, for whose order, and re-phrasing it here could only drift from what
 * Confirm will post - and an older row's suggestion has already been superseded by the
 * newest one, so printing both would offer a planner two answers to one question.
 *
 * The shortfall has no line of its own any more (AC-C11): the engine's own label already
 * reads "Short 44 by 22 Aug (was Buy 134)", and a bare "Short 44" beside it was the same
 * fact said twice, the second time with less in it.
 */
export function BoardChangeSummary({
  annotations,
}: {
  /** Every change behind one icon, oldest first - the order a batch's rows arrive in. */
  annotations: BoardChangeAnnotation[];
}) {
  const newest = annotations[annotations.length - 1];
  const newestFirst = [...annotations].reverse();
  return (
    <div className="space-y-3 text-sm">
      {newestFirst.map((change, index) => {
        const whereItWent = change.whereItWent ?? [];
        return (
          <div
            key={change.rowId}
            data-testid={`board-change-block-${change.rowId}`}
            className={cn(
              'space-y-2',
              // A rule only BETWEEN blocks: one change is not a list of one.
              index > 0 && 'border-t border-border pt-2',
            )}
          >
            {changedFieldsOf(change).length === 0 ? (
              <p className="text-muted-foreground">
                The book moved this line without changing its quantity, its date or its
                decision.
              </p>
            ) : (
              <BoardChangeWasNowTable annotation={change} omitHeader onlyMovedFields />
            )}

            {/* The product this line used to be (S7): ONE line, not a cancelled plus an
                added pair, so the swap reads as the one thing it is. */}
            {change.productChangedFrom ? (
              <p
                data-testid={`board-change-product-${change.rowId}`}
                className="truncate font-medium"
                title={`Product changed, was ${change.productChangedFrom}`}
              >
                {`Product changed, was ${change.productChangedFrom}`}
              </p>
            ) : null}

            {/* WHERE IT WENT, once Apply has run (Slice D). Under the change it belongs to,
                because it answers the question that change raises - the held quantity had to
                go somewhere, and a planner who reads "Reallocate 202607-S0080 3 to pool"
                here stops hunting for it on another screen. Absent entirely on a row Apply
                has not written: a heading over an empty list is a question, not an answer. */}
            {whereItWent.length > 0 ? (
              <div
                data-testid={`board-change-where-${change.rowId}`}
                className="space-y-1"
              >
                <p className="text-muted-foreground">Where it went</p>
                <ul className="space-y-1 font-medium">
                  {whereItWent.map((line, lineIndex) => (
                    <li
                      // Two documents can legitimately carry the same sentence, so the
                      // position is the only honest key - the same reason the suggestion
                      // list below uses one.
                      key={`${lineIndex}-${line}`}
                      data-testid="board-change-where-line"
                      className="truncate"
                      title={line}
                    >
                      {line}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {/* Kept, but landing after the date the customer now asks for (S12, AC-C6).
                Said in days, never left for the reader to subtract two dates. */}
            {change.lateDays !== null ? (
              <p
                data-testid={`board-change-late-${change.rowId}`}
                className="truncate font-medium"
              >
                {`Late by ${change.lateDays} day${change.lateDays === 1 ? '' : 's'}`}
              </p>
            ) : null}

            {/* Stock that is already physically somewhere else (AC-P3-9). Stated, never
                reversed: a movement is a person's decision, and the plan does not get to
                undo one. */}
            {change.movedTransfer ? (
              <p
                data-testid={`board-change-moved-${change.rowId}`}
                className="truncate font-medium"
                title={change.movedTransfer}
              >
                {change.movedTransfer}
              </p>
            ) : null}
          </div>
        );
      })}

      {/* The suggestion, verbatim (AC-C1), off the newest change alone. A list, because each
          line is a separate thing that happens to a separate component, and a reader has to
          be able to count them. */}
      {newest.suggestionLines.length > 0 ? (
        <ul
          data-testid={`board-change-suggestion-${newest.rowId}`}
          className="space-y-1 font-medium"
        >
          {newest.suggestionLines.map((line, index) => (
            <li
              // The engine's order IS the meaning (held first, then new sourcing), and two
              // components can legitimately carry the same sentence, so the position is the
              // only honest key.
              key={`${index}-${line}`}
              data-testid="board-change-suggestion-line"
              className="truncate"
              title={line}
            >
              {line}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * The Was / Now table, read by the board's own change lightbox (AC-D1) and by the ORDER
 * INQUIRIES worklist's (`OrderInquiryQtyAnnotationDialog`).
 *
 * It was too big for a board CELL, which is why the cell carries a hazard icon instead
 * (AC-C9, 13 September 2026). Inside the lightbox that icon opens there is room for it, and
 * the owner reads two columns faster than a line of arrows (22 September 2026).
 *
 * Every row prints BY DEFAULT, the way it always has: the Order Inquiries dialog states a
 * settled amendment, where a quantity that did not move is still the quantity the row is
 * about, and dropping its Date row left the amendment reading as though the date were the
 * thing that changed (SF-2, reviewer, fix round 2). The BOARD's own lightbox asks for
 * `onlyMovedFields` instead (AC-D2): it prints one table per pending change, and there a
 * Date row repeating one date on both sides is a fact about nothing.
 */
export function BoardChangeWasNowTable({
  annotation,
  compact = false,
  omitDecision = false,
  omitHeader = false,
  onlyMovedFields = false,
}: {
  annotation: BoardChangeAnnotation;
  compact?: boolean;
  /**
   * Drop the Decision row (`PLAN-scm-oi-handshake.md`, the Order Inquiries list). An
   * order inquiry row carries a quantity and a date and no decision of its own, and a
   * Decision row reading "Not decided" on both sides would be a fact about nothing.
   */
  omitDecision?: boolean;
  /**
   * Drop the item-code/SO-number line (review of PR #471: the Order Inquiries lightbox
   * already states both in its `DialogTitle`/`DialogDescription`, so this table's own
   * header would repeat them for no reason).
   */
  omitHeader?: boolean;
  /**
   * Print ONLY the rows `changedFieldsOf` says moved (AC-D2). The board's change lightbox
   * alone: it shows a PENDING change, so the unmoved fields are noise there, while the
   * Order Inquiries dialog shows a SETTLED amendment whose unmoved quantity or date is
   * still part of what a reader is checking.
   */
  onlyMovedFields?: boolean;
}) {
  const text = compact ? 'text-[10px]' : 'text-xs';
  const moved = onlyMovedFields
    ? new Set(changedFieldsOf(annotation).map((field) => field.key))
    : null;
  const shows = (field: 'qty' | 'date' | 'decision') => !moved || moved.has(field);
  return (
    <div
      data-testid={`board-change-${annotation.rowId}`}
      className={cn(
        'w-full rounded border border-amber-300 bg-amber-50/70 px-1.5 py-1 text-start',
        text,
      )}
    >
      {omitHeader ? null : (
        <div className="flex items-center justify-between gap-1">
          {/* `lineNo` 0 means the caller has no line number to print - the order inquiry
              list, whose row IS the line - so the item code stands in its place. */}
          <span className="truncate font-medium text-amber-900" title={annotation.itemCode}>
            {annotation.lineNo ? `Line ${annotation.lineNo}` : annotation.itemCode}
          </span>
          <span className="truncate text-amber-800" title={annotation.soNumber}>
            {annotation.soNumber}
          </span>
        </div>
      )}
      <ScrollArea>
        <table className="w-full table-fixed">
          <thead>
            <tr className="text-amber-800">
              <th scope="col" className="w-[26%] text-start font-normal">
                <span className="sr-only">What changed</span>
              </th>
              <th scope="col" className="w-[37%] text-start font-normal">
                Was
              </th>
              <th scope="col" className="w-[37%] text-start font-normal">
                Now
              </th>
            </tr>
          </thead>
          <tbody className="text-amber-900">
            {shows('qty') ? (
              <tr>
                <th scope="row" className="text-start font-normal text-amber-800">
                  Qty
                </th>
                <td className="tabular-nums">{annotation.was.qty ?? '-'}</td>
                <td className="font-medium tabular-nums" data-testid="change-now-qty">
                  {annotation.closed ? 'Cancelled' : annotation.now.qty ?? '-'}
                </td>
              </tr>
            ) : null}
            {shows('date') ? (
              <tr>
                <th scope="row" className="text-start font-normal text-amber-800">
                  Date
                </th>
                {/* `dd/mm/yyyy` (AC-D4): this table is shared with the Order Inquiries
                    Qty dialog, which reads exactly as it did today, so the format stays
                    where it was rather than following the board's own sentence vocabulary. */}
                <td className="tabular-nums">
                  {annotation.was.date ? formatDateInMalaysia(annotation.was.date) : '-'}
                </td>
                <td className="font-medium tabular-nums">
                  {annotation.closed
                    ? 'Cancelled'
                    : annotation.now.date
                      ? formatDateInMalaysia(annotation.now.date)
                      : '-'}
                </td>
              </tr>
            ) : null}
            {/* `omitDecision` (the Order Inquiries list): an inquiry row carries a
                quantity and a date and no decision of its own, so that dialog's reader has
                no use for the row even where both sides differ. Kept as the `hidden` class
                it has always been in the default reading, so that dialog's DOM is what it
                was; the board's own lightbox drops the row outright with the rest of what
                did not move. */}
            {shows('decision') ? (
              <tr className={omitDecision ? 'hidden' : undefined}>
                <th scope="row" className="text-start font-normal align-top text-amber-800">
                  Decision
                </th>
                <td className="truncate align-top" title={annotation.was.decision ?? ''}>
                  {annotation.was.decision ?? 'Not decided'}
                </td>
                <td
                  className="truncate align-top font-medium"
                  data-testid="change-now-decision"
                  title={annotation.now.decision ?? ''}
                >
                  {annotation.closed
                    ? 'Cancelled'
                    : annotation.now.decision ?? 'Not decided'}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </div>
  );
}

export default BoardChangeTable;
