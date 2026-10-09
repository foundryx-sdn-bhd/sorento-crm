import { Suspense } from 'react';
import { Metadata } from 'next';
import { ReportPage } from '@/components/reports/ReportPage';

export const metadata: Metadata = {
  title: 'Bot Failures',
  description: 'Where the chatbot let customers down',
};

export default function BotFailuresPage() {
  return (
    <Suspense fallback={null}>
      <ReportPage
        reportKey="chat_bot_failures"
        breadcrumb={[{ label: 'System Management' }, { label: 'Bot Failures' }]}
      />
    </Suspense>
  );
}
