import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import AttachmentDirectoriesView from './components/AttachmentDirectoriesView';

export const metadata: Metadata = {
  title: 'Files',
  description: 'Manage folders and attachments.',
};

export default function AttachmentDirectoriesPage() {
  return (
    <>
      <Container>
        <PageHeader title="Files" />
      </Container>

      <Container className="flex flex-col h-[calc(100vh-12rem)] min-h-[480px] overflow-hidden">
        <AttachmentDirectoriesView />
      </Container>
    </>
  );
}
