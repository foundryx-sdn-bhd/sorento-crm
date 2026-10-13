import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { projectCrumbs } from '@/app/(protected)/project-sales/_shared/lib/crumbs';
import RequireAccess from '@/app/components/common/RequireAccess';
import BackToList from '@/components/common/BackToList';
import { ProjectDetailClient } from './components/ProjectDetailClient';

export const metadata: Metadata = {
  title: 'Project',
  description: 'A single project pursuit: its stage, cast, quotations and outcome.',
};

export default async function ProjectDetailPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return (
    <RequireAccess permission="projects.projects.view">
      <Container className="space-y-6">
        {/* Crumbs left, one Back right (D6, S3-01). The Back carries the pipeline's
            query string, so it returns to the page the reader left. */}
        <PageHeader
          title="Project"
          crumbs={projectCrumbs(projectId)}
          actions={
            <BackToList listPath="/project-sales/pipeline" label="Back to projects" />
          }
        />
        <ProjectDetailClient projectId={projectId} />
      </Container>
    </RequireAccess>
  );
}
