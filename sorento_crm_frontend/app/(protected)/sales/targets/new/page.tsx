import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import TargetRecord from '../components/TargetRecord';

export const metadata: Metadata = {
  title: 'New target',
  description: 'Set a sales target for an agent, a team or a dealer.',
};

/**
 * A new target opens the target record itself, empty (the S1 hand test of 27 Sep, F4: one view
 * for create and edit, no modal). `?kind=agent|team|dealer` presets Target for, `&subject=` the agent
 * or team, as Set target does from a team or an agent's line.
 */
export default async function NewTargetPage({
  searchParams,
}: {
  searchParams: Promise<{ kind?: string; subject?: string }>;
}) {
  const { kind, subject } = await searchParams;
  const preset = {
    ...(kind === 'agent' || kind === 'team' || kind === 'dealer' ? { kind } : {}),
    ...(subject && (kind === 'agent' || kind === 'team') ? { subjectId: subject } : {}),
  } as { kind?: 'agent' | 'team' | 'dealer'; subjectId?: string };
  return (
    <>
      <Container>
        <PageHeader
          title="Target"
          actions={<BackToList listPath="/sales/targets" label="Back to targets" />}
        />
      </Container>
      <Container>
        <TargetRecord preset={preset} />
      </Container>
    </>
  );
}
