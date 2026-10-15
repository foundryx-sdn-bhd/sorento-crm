import type { OrderInquiryWorklistRow } from '../types/orderInquiry.types';

/** BLOCKER B1 (reviewer fix round): `linked_qty` deliberately EXCLUDES a reserve-kind
 * link (`links_for_rows`' own filter, `project_order_inquiry_service.py`: "a reserve
 * link is not a PO or an SPO document ... `reserved_qty` is where it actually
 * surfaces") - the row's TRUE remaining, the same arithmetic the backend's own
 * `create_request` caps `qty_requested` against, subtracts BOTH. */
export function rowRemaining(row: OrderInquiryWorklistRow): number {
  return (
    Number(row.qty || '0') -
    Number(row.linked_qty || '0') -
    Number(row.reserved_qty || '0') -
    Number(row.bundled_qty || '0')
  );
}

/** OI-RESERVE-RECEIVED (AC-RR-5/40): what a request may ask for - the server's own
 * `reserve_requestable_qty` (links on a fully received PO/SPO do not count against it);
 * the plain remaining only for a payload that predates the field. */
export function rowRequestable(row: OrderInquiryWorklistRow): number {
  return row.reserve_requestable_qty ?? rowRemaining(row);
}

/** AC-RS-23: why a selected row cannot be named on a "Request CS to reserve" ask - the
 * SAME rules the backend's own `create_request` enforces (3.2), read client-side off
 * the worklist row so the menu item can be gated and explained before the request is
 * even sent. `null` means the row is requestable.
 *
 * N9 (reviewer round): names the OFFENDING row - several rows can be ticked at once,
 * and a tooltip reading "already has an open reserve request" with no indication of
 * WHICH of them left the reader guessing. */
export function reserveIneligibleReason(row: OrderInquiryWorklistRow): string | null {
  const item = row.item_code ?? 'This row';
  if (!['ORDER', 'ORDER_BACK'].includes(row.verb)) {
    return `${item}: not an ORDER or ORDER BACK row`;
  }
  if (!['raised', 'partly_linked', 'placed'].includes(row.state)) {
    return `${item}: not open for a reserve request`;
  }
  if (rowRequestable(row) <= 0) {
    return (row.links ?? []).some((link) => link.kind === 'po' || link.kind === 'spo')
      ? `${item}: on a PO/SPO not received yet, nothing left to request`
      : `${item}: has nothing left to request`;
  }
  if (row.reserve_state === 'requested') {
    return `${item}: already has an open reserve request`;
  }
  return null;
}
