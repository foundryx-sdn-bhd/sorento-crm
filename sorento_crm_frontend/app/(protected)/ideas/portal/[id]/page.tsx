import { Metadata } from 'next';
import BackToList from '@/components/common/BackToList';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { PortalIdeaCard } from './components/PortalIdeaCard';

export const metadata: Metadata = {
  title: 'Roadmap idea',
  description: 'An idea on the roadmap.',
};

export default async function PortalIdeaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <Container>
        <PageHeader
          title="Roadmap"
          actions={<BackToList listPath="/ideas/portal" label="Back to roadmap" appendListState={false} />}
        />
      </Container>
      <PortalIdeaCard id={id} />
    </>
  );
}
