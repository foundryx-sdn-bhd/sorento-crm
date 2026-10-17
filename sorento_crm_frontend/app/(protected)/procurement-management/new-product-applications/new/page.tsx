import { Metadata } from 'next';
import Link from 'next/link';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { Button } from '@/components/ui/button';
import NewProductApplicationForm from '../components/NewProductApplicationForm';

export const metadata: Metadata = {
  title: 'New Product Application',
  description: 'Create a new product application',
};

export default async function NewNewProductApplicationPage({
  searchParams,
}: {
  searchParams: Promise<{ stock_inquiry_id?: string }>;
}) {
  const { stock_inquiry_id } = await searchParams;
  return (
    <Container>
      <PageHeader
        title="New Product Application"
        actions={
          <Button variant="outline" asChild>
            <Link href="/procurement-management/new-product-applications">
              Cancel
            </Link>
          </Button>
        }
      />
      <div className="mt-6">
        <NewProductApplicationForm stockInquiryId={stock_inquiry_id} />
      </div>
    </Container>
  );
}
