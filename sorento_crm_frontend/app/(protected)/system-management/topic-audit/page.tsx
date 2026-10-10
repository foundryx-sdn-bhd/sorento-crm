import { Metadata } from 'next';
import TopicAuditScreen from './components/TopicAuditScreen';

export const metadata: Metadata = {
  title: 'Topic Audit',
  description: 'Judge a sample of labelled asks',
};

export default function TopicAuditPage() {
  return <TopicAuditScreen />;
}
