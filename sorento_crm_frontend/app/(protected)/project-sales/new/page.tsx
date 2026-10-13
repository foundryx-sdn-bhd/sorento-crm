import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { registerProjectCrumbs } from '@/app/(protected)/project-sales/_shared/lib/crumbs';
import RequireAccess from '@/app/components/common/RequireAccess';
import BackToList from '@/components/common/BackToList';
import { ProjectForm } from '../_shared/components/ProjectForm';

export const metadata: Metadata = {
  title: 'Register a project',
  description: 'Claim a development before spending time on it.',
};

export default function RegisterProjectPage() {
  return (
    <RequireAccess permission="projects.projects.edit">
      <Container className="space-y-6">
        {/* Crumbs left, one Back right (D6, S3-01); no subtitle under the title
            (D2, one primary CTA lives at the foot of the form instead). */}
        <PageHeader
          title="Register a project"
          crumbs={registerProjectCrumbs()}
          actions={
            <BackToList listPath="/project-sales/pipeline" label="Back to projects" />
          }
        />
        <ProjectForm mode="create" />
      </Container>
    </RequireAccess>
  );
}
