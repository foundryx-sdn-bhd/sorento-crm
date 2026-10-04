import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { IdeaDetail, IdeaDetailHeader } from '@/components/ideas/IdeaDetail';

export const metadata: Metadata = {
  title: 'Idea',
  description: 'View an idea.',
};

export default async function IdeaDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <Container>
        <IdeaDetailHeader id={id} />
      </Container>
      <Container>
        <IdeaDetail id={id} />
      </Container>
    </>
  );
}
