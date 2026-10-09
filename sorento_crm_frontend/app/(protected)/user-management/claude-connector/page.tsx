import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import GrantsList from './components/GrantsList';

export const metadata: Metadata = {
  title: 'Claude connector access',
  description: 'Staff connections to claude.ai.',
};

export default async function ClaudeConnectorPage() {
  return (
    <>
      <Container>
        <PageHeader title="Claude connector access" />
      </Container>

      <Container>
        <GrantsList />
      </Container>
    </>
  );
}
