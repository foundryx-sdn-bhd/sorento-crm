'use client';

import { useCurrencyFormat } from '@/hooks/useCurrencyFormat';
import { formatCurrency } from '@/lib/helpers';
import { useCustomerOutstandingQuery } from '../hooks/useBillingDocuments';
import { formatDay } from '../lib/format';
import { useCanViewOutstanding } from '../lib/permissions';

/**
 * The customer's AR figure for the meta strip under the customer header. Renders nothing
 * without OUTSTANDING_VIEW_SLUG, and does not make the call either.
 */
export function CustomerOutstandingMeta({ customerId }: { customerId: string }) {
  const canSee = useCanViewOutstanding();
  const format = useCurrencyFormat();
  const { data, isLoading } = useCustomerOutstandingQuery(customerId, canSee);
  if (!canSee || isLoading || !data) return null;
  return (
    <p className="text-sm text-muted-foreground" data-testid="customer-outstanding">
      {data.ar_outstanding === null ? (
        'Outstanding: Not synced'
      ) : (
        <>
          Outstanding{' '}
          <span className="font-medium text-foreground">{formatCurrency(data.ar_outstanding, format)}</span>
          {data.ar_as_of ? ` as of ${formatDay(data.ar_as_of)}` : ''}
        </>
      )}
    </p>
  );
}
