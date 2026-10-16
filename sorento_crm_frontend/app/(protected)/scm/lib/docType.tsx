/**
 * AC-CONSIGN-CRM: the order document type (`sales_orders.doc_type` on the backend, carried to
 * the Order Inquiry, Fulfilment Planning and board rows as `so_doc_type`). Only a consignment
 * is marked; a plain sales order shows nothing, so the rest of the book reads as before.
 */
import { Badge } from '@/components/ui/badge';

export function ConsignmentBadge({
  docType,
  size = 'sm',
}: {
  docType: string | null | undefined;
  size?: 'xs' | 'sm' | 'md';
}) {
  if (docType !== 'consignment') return null;
  return (
    <Badge variant="info" appearance="light" size={size} className="w-fit">
      Consignment
    </Badge>
  );
}
