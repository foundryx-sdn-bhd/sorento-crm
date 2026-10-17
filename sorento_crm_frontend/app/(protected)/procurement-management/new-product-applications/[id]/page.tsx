import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import RecordEntityRegistrar from '@/components/common/RecordEntityRegistrar';
import NewProductApplicationDetail from '../components/NewProductApplicationDetail';

export const metadata: Metadata = {
  title: 'New Product Application',
  description: 'View a new product application',
};

export default async function NewProductApplicationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <Container>
      <PageHeader
        title="New Product Application"
        actions={
          <BackToList
            listPath="/procurement-management/new-product-applications"
            label="Back to new product applications"
          />
        }
      />
      <div className="mt-6">
        <RecordEntityRegistrar entityType="new_product_application" id={id} />
        <NewProductApplicationDetail applicationId={id} />
      </div>
    </Container>
  );
}
