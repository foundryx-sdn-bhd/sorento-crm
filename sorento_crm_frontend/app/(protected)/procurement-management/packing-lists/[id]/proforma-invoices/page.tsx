'use client';

import RequireAccess from '@/app/components/common/RequireAccess';
import SourceProformaInvoicesCard from '../../components/SourceProformaInvoicesCard';
import { PackingListRecordSkeleton } from '../components/packing-list-skeleton';
import { SCM_READ_PERMISSION, usePackingListRecord } from '../components/packing-list-context';

/**
 * Which proforma invoices this container was drafted from, and how much of each came here.
 *
 * A tab of its own rather than the one-line "Origin" card it replaces: one invoice may be
 * split across two containers, so "200 of 500 came here" is a table's worth of answer and
 * a sentence could only ever give half of it.
 */
export default function PackingListProformaInvoicesPage() {
  // The tab is not offered without SCM read; this answers a deep link (PL-TABS-ACCESS).
  return (
    <RequireAccess permission={SCM_READ_PERMISSION}>
      <PackingListProformaInvoices />
    </RequireAccess>
  );
}

function PackingListProformaInvoices() {
  const { packingListId, packingList, isLoading } = usePackingListRecord();
  if (isLoading) return <PackingListRecordSkeleton />;
  return (
    <SourceProformaInvoicesCard
      packingListId={packingListId}
      convertedOn={packingList?.created_at ?? null}
    />
  );
}
