/**
 * AutoCount Cancel SO / Cancel PO on an order line (AC-CANCEL-CRM, owner hand test 13 Oct).
 *
 * AutoCount adds a cancel to the line's TransferedQty, so the CRM derives `qty_cancelled` and
 * lists the cancel documents behind it (`cancels`). One place for how that reads on the SO and
 * PO Lines tabs: the tooltip naming each document and how it was matched, and the
 * "Partly cancelled" status a line gets when some, not all, of it was cancelled.
 */
import type { StatusBadgeVariant } from '@/lib/status-badge';

export interface CancelSource {
  doc_no: string;
  /** Null for a `group_whole` document: only the line's own total is known. */
  qty: number | null;
  match_reason?: string | null;
}

/** How a cancel line was matched to this order line, in the owner's words. */
export const MATCH_LABEL: Record<string, string> = {
  line_key: 'line key',
  item: 'item',
  // AutoCount names no line, but every transfer on the item's lines was cancelled, so each
  // line's share is its own transferred qty (owner rule, 13 Oct: exact links only).
  group_whole: 'every line of the item',
  // AutoCount names the SO and the item but the CRM cannot pick one of several lines.
  undetermined: 'not tied to one line',
};

/** "XS-016501: 36 (line key)", one document per line ("XS-016502 (every line of the item)" when
 *  the document's share of this line is not known); undefined when nothing cancelled. */
export function cancelSourcesTitle(cancels: CancelSource[] | null | undefined): string | undefined {
  if (!cancels || cancels.length === 0) return undefined;
  return cancels
    .map((c) => {
      // A reason with no words here (an old row, a new backend value) shows no label, never
      // its internal code.
      const how = MATCH_LABEL[c.match_reason ?? ''];
      const head = c.qty == null ? c.doc_no : `${c.doc_no}: ${Number(c.qty)}`;
      return how ? `${head} (${how})` : head;
    })
    .join('\n');
}

export const PARTLY_CANCELLED = { label: 'Partly cancelled', variant: 'warning' as StatusBadgeVariant };

/** Some of the line was cancelled in AutoCount and the line is still open (owner, 13 Oct, (b)):
 *  a done line reads its own status (Completed, or Cancelled for a whole cancel) and the Qty
 *  cancelled column carries the cancel. */
export function isPartlyCancelled(line: {
  qty_cancelled?: number | null;
  line_status?: string | null;
}): boolean {
  return Number(line.qty_cancelled ?? 0) > 0 && (line.line_status ?? 'open') === 'open';
}
