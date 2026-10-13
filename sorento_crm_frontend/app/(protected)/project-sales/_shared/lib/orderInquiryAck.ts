/**
 * The handshake, in the words the screen reads it by (`PLAN-scm-oi-handshake.md`).
 *
 * One place for the four states' labels, their colour and the sentence a cell prints, so
 * the column, the filter and the bulk bar cannot come to disagree about what "Changed"
 * means. Nothing here explains the feature - the words are the answer, not a lesson.
 */
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import type {
  OrderInquiryAckFields,
  OrderInquiryAckState,
  OrderInquiryBundledHostChange,
} from '../types/orderInquiry.types';

export const ACK_STATES: OrderInquiryAckState[] = [
  'awaiting',
  'acknowledged',
  'changed',
  'rejected',
];

/**
 * The words the screen reads the handshake by (R7). "Confirm" replaced "Acknowledge"
 * everywhere a person can see it; the stored `ack_state` values and the permission slug
 * are untouched, so this is the only place the two vocabularies meet.
 */
export const ACK_LABELS: Record<OrderInquiryAckState, string> = {
  awaiting: 'To confirm',
  acknowledged: 'Confirmed',
  changed: 'Changed',
  rejected: 'Rejected',
};

/**
 * How the page says "show me everything" in the URL. An ABSENT `?ack=` opens on the
 * default, so a cleared filter needs a word of its own or a reload would put the default
 * straight back over the choice.
 */
export const ACK_ANY = 'all';

/**
 * What the Confirmed filter offers, in the order purchasing reads them (PLAN-oi-confirm-
 * per-so, R3: G4/G5 reversed). A row is born `awaiting` again - the handshake is back on -
 * so "To confirm" is purchasing's own work queue and the page's own default (`ack`
 * absent), with an explicit "All" beside it for "show me everything regardless of where
 * it stands". `to_confirm` is not a stored `ack_state` (it is `awaiting` OR `changed`
 * together) - the backend has read it since the handshake plan and this is the first
 * time the FE offers it again.
 */
export const ACK_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'to_confirm', label: 'To confirm' },
  { value: 'acknowledged', label: ACK_LABELS.acknowledged },
  { value: 'changed', label: ACK_LABELS.changed },
  { value: 'rejected', label: ACK_LABELS.rejected },
  { value: ACK_ANY, label: 'All' },
];

export function ackStateOf(row: OrderInquiryAckFields): OrderInquiryAckState {
  const value = (row.ack_state ?? 'awaiting') as OrderInquiryAckState;
  return ACK_STATES.includes(value) ? value : 'awaiting';
}

/** A row purchasing has not refused, so there is still something to refuse. */
export function isRejectable(row: OrderInquiryAckFields): boolean {
  return ackStateOf(row) !== 'rejected';
}

/**
 * A row the bulk Reject may take (plan section 1, Reject): any OWED row, whether or not
 * it already carries links. Drafts are links on an unconfirmed row, so most rows in
 * front of purchasing are `placed` - refusing only unlinked ones would refuse almost
 * nothing. A cancelled or actioned row has nothing left to refuse, and a rejected one
 * has already been.
 */
export function isBulkRejectable(row: OrderInquiryAckFields & { state?: string }): boolean {
  if (!isRejectable(row)) return false;
  return row.state === 'raised' || row.state === 'partly_linked' || row.state === 'placed';
}

/**
 * What the row said before CS last amended it - the Was half of the Was / Now table.
 *
 * Read off the row's own `previous_qty` / `previous_delivery_date`, which the settle-in-place
 * writes (`project_order_inquiry_service._settle_row_in_place`). It used to be parsed back
 * out of the note beside them, and the note is a sentence for a person: "Was 10, no previous
 * delivery date" gave up the quantity as `10,` - the sentence's own comma read as part of
 * the number. A figure the screen prints is asked for as a figure.
 *
 * A row with no previous quantity returns nothing at all rather than a guess, and the cell
 * then prints the state without a table.
 */
export function previousValueOf(row: OrderInquiryAckFields): {
  qty: string;
  date: string | null;
} | null {
  const qty = row.previous_qty;
  if (qty === null || qty === undefined || qty === '') return null;
  return { qty: String(qty), date: row.previous_delivery_date ?? null };
}

/**
 * S5 (`PLAN-oi-replan-received-links.md`): the note AutoCount's own book pairing wrote
 * when `follow_book_repairing` moved or cleared this row's link - `AutoCount moved
 * <document> to <SO new> on <date>` or `AutoCount removed <document> from <SO old> on
 * <date>`. Read by prefix rather than a stored flag: the note IS the record, and
 * nothing else marks a row the book emptied of links apart from it carrying one.
 *
 * `null` when the row's note says nothing of the kind, which is every row today - this
 * is what widens `QtyAnnotationButton`'s gate (AC-RL-46) beyond rejected/changed.
 */
export function movedNoteOf(row: { note?: string | null }): string | null {
  const note = (row.note ?? '').trim();
  if (!note) return null;
  return note.includes('AutoCount moved') || note.includes('AutoCount removed')
    ? note
    : null;
}

/**
 * The (i) for a BUNDLED row (`PLAN-oi-bundled-row-host-change.md`). A companion has no
 * sheet row of its own, no PO of its own and no Was of its own - owner ruling, 19 Sep
 * 2026: "it comes with the X and Y, so it should follow them, to have the same delay" -
 * so its own (i) reads each HOST's own change instead, one line per host, in the order
 * `bundled_host_changes` already carries (rule order, resolved server-side). Nothing is
 * written to the companion row itself; this only reads what the server already sent.
 *
 * Three shapes, per host:
 *   - a host with a Was of its own: "with X: Was 182 on 01/06/2026, now 280 on 01/03/2027"
 *   - a host with a live row but no Was: "with X: 280 on 01/03/2027, no change"
 *   - a host with no live row at all: "with X: no open row"
 *
 * `null` on a row that carries no `bundled_host_changes` at all - not a bundled row, or
 * one whose rule resolved to nothing.
 */
export function bundledHostChangeLines(row: {
  bundled_host_changes?: OrderInquiryBundledHostChange[] | null;
}): string[] | null {
  const entries = row.bundled_host_changes;
  if (!entries || entries.length === 0) return null;
  return entries.map((entry) => bundledHostChangeLine(entry));
}

function bundledHostChangeLine(entry: OrderInquiryBundledHostChange): string {
  if (!entry.qty || !entry.delivery_date) {
    return `with ${entry.item_code}: no open row`;
  }
  const now = `${entry.qty} on ${formatDateInMalaysia(entry.delivery_date)}`;
  if (entry.previous_qty && entry.previous_delivery_date) {
    const was = `${entry.previous_qty} on ${formatDateInMalaysia(entry.previous_delivery_date)}`;
    return `with ${entry.item_code}: Was ${was}, now ${now}`;
  }
  return `with ${entry.item_code}: ${now}, no change`;
}

/** Who refused an order inquiry row, when and why - what both the Sales Order's Lines tab
 * and the planning board carry for an open refusal (PR 1622). */
export interface PurchasingRefusal {
  rejected_by_name?: string | null;
  rejected_at?: string | null;
  rejected_reason?: string | null;
}

/** "Rejected by Joey on 13/10/2026, 2:05 pm: Supplier discontinued this finish" (Malaysia time). */
export function purchasingRefusalTitle(refusal: PurchasingRefusal): string {
  const who = refusal.rejected_by_name ? ` by ${refusal.rejected_by_name}` : '';
  const stamp = refusal.rejected_at ? formatDateTimeInMalaysia(refusal.rejected_at) : '';
  const when = stamp ? ` on ${stamp}` : '';
  return `Rejected${who}${when}: ${refusal.rejected_reason || 'no reason given'}`;
}
