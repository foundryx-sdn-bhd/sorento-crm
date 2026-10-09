import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import GrantDetail from '../components/GrantDetail';

export const metadata: Metadata = {
  title: 'Claude connector grant',
  description: 'One staff connection to claude.ai.',
};

export default async function ClaudeConnectorGrantPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <>
      <Container>
        <PageHeader
          title="Claude connector access"
          actions={
            <BackToList listPath="/user-management/claude-connector" label="Back to grants" />
          }
        />
      </Container>

      <Container>
        <GrantDetail id={id} />
      </Container>
    </>
  );
}
