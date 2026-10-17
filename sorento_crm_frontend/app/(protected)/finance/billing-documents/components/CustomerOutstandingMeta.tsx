'use client';

import { cn } from '@/lib/utils';
import { useCustomerOutstandingQuery } from '../hooks/useBillingDocuments';
import { formatBillingAmount, formatDay } from '../lib/format';
import { useCanViewOutstanding } from '../lib/permissions';

/**
 * The customer's AR figure for the meta strip under the customer header. Renders nothing
 * without OUTSTANDING_VIEW_SLUG, and does not make the call either.
 */
export function CustomerOutstandingMeta({ customerId, className }: { customerId: string; className?: string }) {
  const canSee = useCanViewOutstanding();
  const { data, isLoading } = useCustomerOutstandingQuery(customerId, canSee);
  if (!canSee || isLoading || !data) return null;
  return (
    <p className={cn('text-sm text-muted-foreground', className)} data-testid="customer-outstanding">
      {data.ar_outstanding === null ? (
        'Outstanding: Not synced'
      ) : (
        <>
          Outstanding{' '}
          <span className="font-medium text-foreground">RM {formatBillingAmount(data.ar_outstanding, 'MYR')}</span>
          {data.ar_as_of ? ` as of ${formatDay(data.ar_as_of)}` : ''}
        </>
      )}
    </p>
  );
}
