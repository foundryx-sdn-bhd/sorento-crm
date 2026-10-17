import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import RecordEntityRegistrar from '@/components/common/RecordEntityRegistrar';
import SampleRequestDetail from '../components/SampleRequestDetail';
import SampleRequestDetailTabs from '../components/SampleRequestDetailTabs';

export const metadata: Metadata = {
  title: 'Sample Request',
  description: 'View a sample request',
};

export default async function SampleRequestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <Container>
      <PageHeader
        title="Sample Request"
        actions={
          <BackToList listPath="/project-sales/sample-requests" label="Back to sample requests" />
        }
      />
      <div className="mt-6">
        <SampleRequestDetailTabs id={id}>
          <RecordEntityRegistrar entityType="sample_request" id={id} />
          <SampleRequestDetail requestId={id} />
        </SampleRequestDetailTabs>
      </div>
    </Container>
  );
}
