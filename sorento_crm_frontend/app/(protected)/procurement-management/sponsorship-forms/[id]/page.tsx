import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import PurchaseRequestDetail from '../../purchase-requests/components/PurchaseRequestDetail';
import FormDetailTabsWithRevisions from '@/app/(protected)/sla-management/_shared/FormDetailTabsWithRevisions';
import RecordEntityRegistrar from '@/components/common/RecordEntityRegistrar';

export const metadata: Metadata = {
  title: 'Sponsorship Form Details',
  description: 'View sponsorship form details',
};

export default function SponsorshipFormDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  return (
    <Container>
      <PageHeader
        title="Sponsorship Form"
        actions={
          <BackToList
            listPath="/procurement-management/sponsorship-forms"
            label="Back to sponsorship forms"
          />
        }
      />
      <div className="mt-6">
        <SponsorshipFormDetailWrapper params={params} />
      </div>
    </Container>
  );
}

async function SponsorshipFormDetailWrapper({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <FormDetailTabsWithRevisions
      sourceEntityType="sponsorship_form"
      sourceEntityId={id}
      revisionsKind="sponsorship_form"
      commentsKind="sponsorship_form"
    >
      <RecordEntityRegistrar entityType="sponsorship_form" id={id} />
      <PurchaseRequestDetail
        requestId={id}
        basePath="/procurement-management/sponsorship-forms"
      />
    </FormDetailTabsWithRevisions>
  );
}
