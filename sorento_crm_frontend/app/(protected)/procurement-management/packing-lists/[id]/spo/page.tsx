'use client';

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';

import RequireAccess from '@/app/components/common/RequireAccess';
import SpoPlannerTable from '../../components/SpoPlannerTable';
import { SCM_READ_PERMISSION, usePackingListRecord } from '../components/packing-list-context';

/** Turning what was packed into shipping orders against the open POs behind it. */
export default function PackingListSpoPage() {
  // `useSearchParams` needs a Suspense boundary above it, or the whole route opts out of
  // static rendering (Next's own `missing-suspense-with-csr-bailout`). The fallback is the
  // planner with no `?edit=`, which is what the page renders for every other visit anyway.
  // The tab is not offered without SCM read; this answers a deep link (PL-TABS-ACCESS).
  return (
    <RequireAccess permission={SCM_READ_PERMISSION}>
      <Suspense fallback={<PackingListSpoPlanner editPurchaseOrderId={null} />}>
        <PackingListSpoPlannerFromUrl />
      </Suspense>
    </RequireAccess>
  );
}

function PackingListSpoPlannerFromUrl() {
  const searchParams = useSearchParams();
  // `?edit=<purchase_order_id>` (R24, AC-K5) - the SPO document's own "Edit in planner"
  // action lands here, and the planner opens straight into edit mode for that SPO. Read
  // HERE rather than in the table so the table has no router dependency of its own.
  return <PackingListSpoPlanner editPurchaseOrderId={searchParams.get('edit')} />;
}

function PackingListSpoPlanner({ editPurchaseOrderId }: { editPurchaseOrderId: string | null }) {
  const { packingListId } = usePackingListRecord();
  return (
    <SpoPlannerTable shipmentId={packingListId} initialEditPurchaseOrderId={editPurchaseOrderId} />
  );
}
