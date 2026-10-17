import { useHasPermission } from '@/hooks/usePermissions';

export const BILLING_VIEW_SLUG = 'finance.billing_documents.view';
export const BILLING_EXPORT_SLUG = 'finance.billing_documents.export';

/**
 * The one slug every outstanding figure is gated on (list column, Open only filter, record
 * meta strip, customer meta and tab column, sales order Invoices column). It is the view slug
 * today; switching outstanding to a slug of its own is a change to this line only.
 */
export const OUTSTANDING_VIEW_SLUG = BILLING_VIEW_SLUG;

export function useCanViewBilling(): boolean {
  return useHasPermission(BILLING_VIEW_SLUG);
}

export function useCanViewOutstanding(): boolean {
  return useHasPermission(OUTSTANDING_VIEW_SLUG);
}

/** Export needs both the export slug and the view slug. */
export function useCanExportBilling(): boolean {
  const canExport = useHasPermission(BILLING_EXPORT_SLUG);
  const canView = useHasPermission(BILLING_VIEW_SLUG);
  return canExport && canView;
}
