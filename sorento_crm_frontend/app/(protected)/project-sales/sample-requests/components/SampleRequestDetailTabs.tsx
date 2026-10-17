'use client';

import type { ReactNode } from 'react';
import FormDetailWithSLATabs from '@/app/(protected)/sla-management/_shared/FormDetailWithSLATabs';
import { useHasPermission } from '@/hooks/usePermissions';

/**
 * The PR / SF / SI detail tabs for a sample request. The comment thread is read with
 * `projects.sample_requests.view` (form_comment_service VIEW_PERMISSION), which a salesperson
 * holding only `create` lacks, so the Comments tab is offered to view holders only rather
 * than answering the owner with a permission-denied toast.
 */
export default function SampleRequestDetailTabs({
  id,
  children,
}: {
  id: string;
  children: ReactNode;
}) {
  const canView = useHasPermission('projects.sample_requests.view');
  return (
    <FormDetailWithSLATabs
      sourceEntityType="sample_request"
      sourceEntityId={id}
      commentsKind={canView ? 'sample_request' : undefined}
    >
      {children}
    </FormDetailWithSLATabs>
  );
}
