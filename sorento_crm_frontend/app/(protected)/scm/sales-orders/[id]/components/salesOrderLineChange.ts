import type { BoardChangeAnnotation } from '@/app/(protected)/project-sales/_shared/lib/boardChangeAnnotations';
import { whereItWentFrom } from '@/app/(protected)/project-sales/_shared/lib/boardChangeAnnotations';
import type { PlanningChangeKind } from '@/app/(protected)/project-sales/_shared/types/planningChange.types';
import type { SalesOrderLine } from '@/app/(protected)/scm/types/scm.types';

/**
 * The batch row behind one sales-order line, as the board's own What-changed dialog reads it.
 *
 * WHY THIS SCREEN AT ALL: once Apply has run on a CANCELLED line the line is closed, and a
 * closed line has no cell on the fulfilment board - so the dialog that says where its held
 * quantity went (`Reallocate 202607-S0080 3 to pool`) has nowhere left to open. The line is
 * still on this order, and this is where CS looks next, so the same reading is offered here.
 *
 * ITS OWN MODULE, beside `salesOrderLineDraft.ts` and for the same measured reason: a
 * `'use client'` component file that exports a value which is not a component drops out of
 * React Fast Refresh, and every edit to it then full-reloads the page and wipes the edit
 * session (13 September 2026).
 *
 * THE SIDES: the line carries the batch row's IDENTITY, its RESULT and its own `from`/`to`
 * sides, mapped here exactly as the board's `annotationOf` does (the Now is null once the
 * line is closed, and the decision stays null: this screen has no held or proposed parts).
 * A cancelled row says the one word the book actually said - `changedFieldsOf` prints
 * "Cancelled" off `closed` alone. `lineNo` is 0 because this grid has no line
 * numbers of its own; the dialog titles itself with the item code instead.
 */
export function lineChangeAnnotation(
  line: SalesOrderLine,
  soNumber: string,
): BoardChangeAnnotation | null {
  const change = line.planning_change;
  if (!change) return null;
  const closed = change.kind === 'cancelled';
  return {
    rowId: change.id,
    soNumber,
    lineNo: 0,
    itemCode: line.sku,
    kind: change.kind as PlanningChangeKind,
    closed,
    was: {
      qty: change.from?.qty ?? null,
      date: change.from?.required_date ?? null,
      decision: null,
    },
    now: {
      qty: closed ? null : (change.to?.qty ?? null),
      date: closed ? null : (change.to?.required_date ?? null),
      decision: null,
    },
    suggestionLines: [],
    lateDays: null,
    shortfallQty: null,
    productChangedFrom: null,
    movedTransfer: null,
    whereItWent: whereItWentFrom(change.result),
    projectLineId: null,
  };
}
