import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import PurchaseRequestDetail from '../components/PurchaseRequestDetail';
import FormDetailTabsWithRevisions from '@/app/(protected)/sla-management/_shared/FormDetailTabsWithRevisions';
import RecordEntityRegistrar from '@/components/common/RecordEntityRegistrar';

export const metadata: Metadata = {
  title: 'Purchase Request Details',
  description: 'View purchase request or sponsorship form details',
};

export default function PurchaseRequestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  return (
    <Container>
      <PageHeader
        title="Purchase Request"
        actions={
          <BackToList
            listPath="/procurement-management/purchase-requests"
            label="Back to purchase requests"
          />
        }
      />
      <div className="mt-6">
        <PurchaseRequestDetailWrapper params={params} />
      </div>
    </Container>
  );
}

async function PurchaseRequestDetailWrapper({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <FormDetailTabsWithRevisions
      sourceEntityType="purchase_request"
      sourceEntityId={id}
      revisionsKind="purchase_request"
      commentsKind="purchase_request"
    >
      <RecordEntityRegistrar entityType="purchase_request" id={id} />
      <PurchaseRequestDetail requestId={id} />
    </FormDetailTabsWithRevisions>
  );
}
