import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import RequireAccess from '@/app/components/common/RequireAccess';
import { SnapshotsList } from '../components/SnapshotsList';

export const metadata: Metadata = {
  title: 'Stock Debt snapshots',
  description: 'The Stock Debt board as it stood when it was saved.',
};

export default function StockDebtSnapshotsPage() {
  return (
    <RequireAccess permission="projects.stock_debt.view">
      <Container className="space-y-6">
        <PageHeader
          title="Snapshots"
          crumbs={[
            { title: 'Stock Debt', path: '/project-sales/stock-debt' },
            { title: 'Snapshots' },
          ]}
          actions={
            <BackToList
              listPath="/project-sales/stock-debt"
              label="Back to stock debt"
              appendListState={false}
            />
          }
        />
        <SnapshotsList />
      </Container>
    </RequireAccess>
  );
}
