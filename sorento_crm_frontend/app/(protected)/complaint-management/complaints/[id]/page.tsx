import { Metadata } from 'next';
import { Truck } from 'lucide-react';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import ComplaintDetail from '../components/ComplaintDetail';
import ComplaintFulfilmentOrdersSection from '../components/ComplaintFulfilmentOrdersSection';
import FormDetailWithSLATabs from '@/app/(protected)/sla-management/_shared/FormDetailWithSLATabs';
import RecordEntityRegistrar from '@/components/common/RecordEntityRegistrar';

export const metadata: Metadata = {
  title: 'Complaint Details',
  description: 'View complaint details and attachments',
};

export default function ComplaintDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  return (
    <Container>
      <PageHeader
        title="Complaint"
        actions={
          <BackToList
            listPath="/complaint-management/complaints"
            label="Back to complaints"
          />
        }
      />
      <div className="mt-6">
        <ComplaintDetailWrapper params={params} />
      </div>
    </Container>
  );
}

async function ComplaintDetailWrapper({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  
  // Handle "new" route - should not reach here as it has its own page
  if (id === 'new') {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Invalid complaint ID</p>
      </div>
    );
  }

  return (
    <FormDetailWithSLATabs
      sourceEntityType="complaint"
      sourceEntityId={id}
      commentsKind="complaint"
      extraTabs={[
        {
          value: 'fulfilment',
          label: 'Fulfilment DOs',
          icon: <Truck />,
          content: <ComplaintFulfilmentOrdersSection complaintId={id} />,
        },
      ]}
    >
      <RecordEntityRegistrar entityType="complaint" id={id} />
      <ComplaintDetail complaintId={id} />
    </FormDetailWithSLATabs>
  );
}
