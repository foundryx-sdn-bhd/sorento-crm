import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import NewProductApplicationsList from './components/NewProductApplicationsList';

export const metadata: Metadata = {
  title: 'New Product Applications',
  description: 'Requests to open new product codes',
};

export default function NewProductApplicationsPage() {
  return (
    <>
      <Container>
        <PageHeader title="New Product Applications" />
      </Container>

      <Container>
        <NewProductApplicationsList />
      </Container>
    </>
  );
}
