import { Metadata } from 'next';
import { IdeasBoardView } from '@/components/ideas/IdeasBoardView';

export const metadata: Metadata = {
  title: 'Ideas board',
  description: 'Triage ideas by status.',
};

export default function IdeasBoardPage() {
  return <IdeasBoardView />;
}
