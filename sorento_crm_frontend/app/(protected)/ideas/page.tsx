import { Metadata } from 'next';
import { IdeasListView } from '@/components/ideas/IdeasListView';

export const metadata: Metadata = {
  title: 'Ideas',
  description: 'Share and explore product ideas.',
};

export default function IdeasPage() {
  return <IdeasListView />;
}
