import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import ModelPricesList from './components/ModelPricesList';
import UnpricedModelsList from './components/UnpricedModelsList';

export const metadata: Metadata = {
  title: 'Model prices',
  description: 'USD price per 1M tokens for each AI model.',
};

export default function ModelPricesPage() {
  return (
    <>
      <Container>
        <PageHeader title="Model prices" />
      </Container>

      <Container className="min-w-0 space-y-4">
        <ModelPricesList />
        <UnpricedModelsList />
      </Container>
    </>
  );
}
