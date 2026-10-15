import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import { SalesOrderDetail } from '../../sales-orders/[id]/components/SalesOrderDetail';

export const metadata: Metadata = {
  title: 'Consignment',
  description: 'Consignment detail - header and lines.',
};

export default async function ConsignmentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <>
      <Container>
        <PageHeader
          title="Consignment"
          actions={<BackToList listPath="/scm/consignments" label="Back to consignments" />}
        />
      </Container>

      <Container>
        <SalesOrderDetail id={id} docType="consignment" />
      </Container>
    </>
  );
}
