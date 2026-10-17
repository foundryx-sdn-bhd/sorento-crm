import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import SampleRequestsList from './components/SampleRequestsList';

export const metadata: Metadata = {
  title: 'Sample Requests',
  description: 'Sample requests for project sites',
};

export default function SampleRequestsPage() {
  return (
    <>
      <Container>
        <PageHeader title="Sample Requests" />
      </Container>

      <Container>
        <SampleRequestsList />
      </Container>
    </>
  );
}
