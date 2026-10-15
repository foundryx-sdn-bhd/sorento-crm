/**
 * The OI document's own status (`PLAN-oi-header-list-detail.md`, AC-HL-04): derived,
 * never stored - a header is Outstanding while any non-cancelled line still waits for a
 * confirm, Completed otherwise (a header holding only cancelled lines reads Completed
 * too, AC-LS-02). One place for the word and the Badge tone, shared by the Documents
 * list and the detail page's header card, so the two can never disagree.
 */
import type { OrderInquiryHeaderStatus } from '../types/orderInquiry.types';

export function orderInquiryHeaderStatusLabel(status: OrderInquiryHeaderStatus): string {
  return status === 'outstanding' ? 'Outstanding' : 'Completed';
}

/** Outstanding is the warning tone (there is still something to do); Completed is
 * neutral - it is not a success to celebrate, just a document with nothing left owed. */
export function orderInquiryHeaderStatusVariant(
  status: OrderInquiryHeaderStatus,
): 'warning' | 'secondary' {
  return status === 'outstanding' ? 'warning' : 'secondary';
}

/** AC-SN-30: the reserve status of an OI's request rows, earliest first. */
export type OrderInquiryReserveStatus = 'parked' | 'needs_reserve' | 'notice_parked' | 'reserved';

export const ORDER_INQUIRY_RESERVE_STATUSES: OrderInquiryReserveStatus[] = [
  'parked',
  'needs_reserve',
  'notice_parked',
  'reserved',
];

export function orderInquiryReserveStatusLabel(status: OrderInquiryReserveStatus): string {
  switch (status) {
    case 'parked':
      return 'Parked request to reserve';
    case 'needs_reserve':
      return 'Needs reserve';
    case 'notice_parked':
      return 'Parked reserve';
    default:
      return 'Reserved';
  }
}

export function orderInquiryReserveStatusVariant(
  status: OrderInquiryReserveStatus,
): 'warning' | 'info' | 'success' | 'secondary' {
  switch (status) {
    case 'parked':
      return 'secondary';
    case 'needs_reserve':
      return 'info';
    case 'notice_parked':
      return 'warning';
    default:
      return 'success';
  }
}
