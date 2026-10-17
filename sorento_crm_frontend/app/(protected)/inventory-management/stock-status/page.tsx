import { Suspense } from 'react';
import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import StockStatusPageClient from './components/StockStatusPageClient';

export const metadata: Metadata = {
  title: 'Stock status',
  description: 'Available stock status per item, as AutoCount reports it.',
};

export default function StockStatusPage() {
  return (
    <>
      <Container>
        <PageHeader title="Stock status" />
      </Container>
      <Container>
        {/* useSearchParams needs a boundary in the App Router. */}
        <Suspense fallback={null}>
          <StockStatusPageClient />
        </Suspense>
      </Container>
    </>
  );
}
