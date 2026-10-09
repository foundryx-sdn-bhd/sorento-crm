import { Suspense } from 'react';
import { Metadata } from 'next';
import { ReportPage } from '@/components/reports/ReportPage';

export const metadata: Metadata = {
  title: 'Chat Overview',
  description: 'How the chatbot answered',
};

export default function ChatOverviewPage() {
  return (
    <Suspense fallback={null}>
      <ReportPage
        reportKey="chat_overview"
        breadcrumb={[{ label: 'System Management' }, { label: 'Chat Overview' }]}
      />
    </Suspense>
  );
}
