import { Suspense } from 'react';
import { Metadata } from 'next';
import { ReportPage } from '@/components/reports/ReportPage';

export const metadata: Metadata = {
  title: 'Top Asks',
  description: 'What customers ask the chatbot',
};

export default function TopAsksPage() {
  return (
    <Suspense fallback={null}>
      <ReportPage
        reportKey="chat_top_asks"
        breadcrumb={[{ label: 'System Management' }, { label: 'Top Asks' }]}
      />
    </Suspense>
  );
}
