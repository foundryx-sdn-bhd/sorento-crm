import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import CustomerGroupDetail from './components/CustomerGroupDetail';

export const metadata: Metadata = {
  title: 'Customer Group',
  description: 'Customer group detail.',
};

export default async function CustomerGroupDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <>
      <Container>
        <PageHeader
          title="Customer Group"
          actions={
            <BackToList listPath="/order-management/customer-groups" label="Back to customer groups" />
          }
        />
      </Container>

      <Container>
        <CustomerGroupDetail groupId={id} />
      </Container>
    </>
  );
}
