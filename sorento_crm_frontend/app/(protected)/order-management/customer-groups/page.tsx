import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import CustomerGroupsList from './components/CustomerGroupsList';

export const metadata: Metadata = {
  title: 'Customer Groups',
  description: 'Ledgers grouped into one customer.',
};

export default async function CustomerGroupsPage() {
  return (
    <>
      <Container>
        <PageHeader title="Customer Groups" />
      </Container>

      <Container>
        <CustomerGroupsList />
      </Container>
    </>
  );
}
