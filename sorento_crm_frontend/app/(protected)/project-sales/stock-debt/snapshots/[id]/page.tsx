import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import RequireAccess from '@/app/components/common/RequireAccess';
import { SnapshotClient } from '../../components/SnapshotClient';

export const metadata: Metadata = {
  title: 'Stock Debt snapshot',
  description: 'The Stock Debt board as it stood when it was saved.',
};

export default async function StockDebtSnapshotPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <RequireAccess permission="projects.stock_debt.view">
      <Container className="space-y-6">
        <SnapshotClient snapshotId={id} />
      </Container>
    </RequireAccess>
  );
}
