import { Metadata } from 'next';
import { IdeasListView } from '@/components/ideas/IdeasListView';
import { IdeasScopeToggle } from '@/components/ideas/IdeasScopeToggle';

export const metadata: Metadata = {
  title: 'Ideas',
  description: 'Share and explore product ideas.',
};

export default function IdeasPage() {
  return <IdeasListView toolbarScopeSlot={<IdeasScopeToggle />} />;
}
