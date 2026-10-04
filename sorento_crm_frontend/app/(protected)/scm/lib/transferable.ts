/**
 * AutoCount's `Transferable` flag on a sales order (`sales_orders.is_transferable`,
 * SO-TRANSFERABLE). Shared by the sales-order list's column and filter and the detail page's
 * General tab, so both read the same words for the same order.
 *
 * `false` is an order AutoCount has not confirmed for the queue yet (Stock Debt and the
 * fulfilment ladder skip it); `null` is an order whose source never said, which counts.
 */
export type TransferableBadgeVariant = 'success' | 'warning' | 'secondary';

export interface TransferableBadge {
  variant: TransferableBadgeVariant;
  label: string;
}

export function transferableBadge(value: boolean | null | undefined): TransferableBadge {
  if (value === true) return { variant: 'success', label: 'Yes' };
  if (value === false) return { variant: 'warning', label: 'No' };
  return { variant: 'secondary', label: 'Not stated' };
}

/** The list filter's options; the values are the route's own `transferable` vocabulary. */
export const TRANSFERABLE_FILTER_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
  { value: 'unknown', label: 'Not stated' },
];
