import { Badge } from '@/components/ui/badge';
import {
  BILLING_STATUS_LABEL,
  BILLING_TYPE_LABEL,
  type BillingDocumentStatus,
  type BillingDocumentType,
} from '../types/billingDocument.types';

export function BillingTypeBadge({ type }: { type: BillingDocumentType }) {
  return (
    <Badge variant="info" appearance="light">
      {BILLING_TYPE_LABEL[type] ?? type}
    </Badge>
  );
}

/** Posted is neutral, Cancelled is muted. */
export function BillingStatusBadge({ status }: { status: BillingDocumentStatus }) {
  return (
    <Badge variant={status === 'cancelled' ? 'outline' : 'secondary'} appearance="light">
      {BILLING_STATUS_LABEL[status] ?? status}
    </Badge>
  );
}
