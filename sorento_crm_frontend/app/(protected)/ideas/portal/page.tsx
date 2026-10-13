import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { IdeasPortal } from './components/IdeasPortal';

export const metadata: Metadata = {
  title: 'Roadmap',
  description: 'The Ideas roadmap, feedback and changelog.',
};

export default function IdeasPortalPage() {
  return (
    <>
      <Container>
        <PageHeader title="Roadmap" />
      </Container>
      <IdeasPortal />
    </>
  );
}
