/**
 * How a consignment's status is WORDED (CONSIGN-DELIVERED-FIX, owner ruling 14 Oct).
 *
 * A consignment's goods left the warehouse with the CG, so it is stored `closed` (never open
 * demand) and "Completed" / "Outstanding" say nothing true about it. What the reader wants is
 * where the goods are: still at the customer (ordered less returned), or all back. A
 * cancelled (voided) consignment keeps the sales order's own wording.
 */
import type { StatusBadgeVariant } from '@/lib/status-badge';
import { salesOrderStatusLabel, salesOrderStatusVariant } from './salesOrderStatus';

type Line = { qty_ordered?: number | string | null; qty_returned?: number | string | null };
type Order = { status?: string | null; lines?: Line[] | null };

/** Units still at the customer: ordered less returned, summed over the lines, never below 0. */
export function consignmentAtCustomer(order: Order): number {
  return (order.lines ?? []).reduce(
    (sum, line) => sum + Math.max(Number(line.qty_ordered ?? 0) - Number(line.qty_returned ?? 0), 0),
    0,
  );
}

export function consignmentStatusLabel(order: Order): string {
  if ((order.status ?? '').trim().toLowerCase() === 'cancelled') return salesOrderStatusLabel(order.status);
  return consignmentAtCustomer(order) > 0 ? 'At customer' : 'Fully returned';
}

export function consignmentStatusVariant(order: Order): StatusBadgeVariant {
  if ((order.status ?? '').trim().toLowerCase() === 'cancelled') return salesOrderStatusVariant(order.status);
  return consignmentAtCustomer(order) > 0 ? 'info' : 'secondary';
}
