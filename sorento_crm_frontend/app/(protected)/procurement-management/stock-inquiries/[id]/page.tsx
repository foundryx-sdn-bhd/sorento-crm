import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import StockInquiryDetail from '../components/StockInquiryDetail';
import FormDetailTabsWithRevisions from '@/app/(protected)/sla-management/_shared/FormDetailTabsWithRevisions';
import RecordEntityRegistrar from '@/components/common/RecordEntityRegistrar';

export const metadata: Metadata = {
  title: 'Stock Inquiry Details',
  description: 'View stock inquiry details',
};

export default function StockInquiryDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  return (
    <Container>
      <PageHeader
        title="Stock Inquiry"
        actions={
          <BackToList
            listPath="/procurement-management/stock-inquiries"
            label="Back to stock inquiries"
          />
        }
      />
      <div className="mt-6">
        <StockInquiryDetailWrapper params={params} />
      </div>
    </Container>
  );
}

async function StockInquiryDetailWrapper({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <FormDetailTabsWithRevisions
      sourceEntityType="stock_inquiry"
      sourceEntityId={id}
      revisionsKind="stock_inquiry"
      commentsKind="stock_inquiry"
    >
      <RecordEntityRegistrar entityType="stock_inquiry" id={id} />
      <StockInquiryDetail inquiryId={id} />
    </FormDetailTabsWithRevisions>
  );
}
