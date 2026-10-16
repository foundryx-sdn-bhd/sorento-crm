import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { ConsignmentsList } from './components/ConsignmentsList';

export const metadata: Metadata = {
  title: 'Consignment',
  description: 'AutoCount consignments.',
};

export default function ConsignmentsPage() {
  return (
    <>
      <Container>
        <PageHeader title="Consignment" />
      </Container>

      <Container>
        <ConsignmentsList />
      </Container>
    </>
  );
}
