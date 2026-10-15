'use client';

import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import { projectCrumbs } from '@/app/(protected)/project-sales/_shared/lib/crumbs';
import { useQuotationDocument } from '../../../../_shared/hooks/useQuotationDocuments';

/**
 * The page title is the document NUMBER, not the id in the URL (no UUIDs in the
 * UI), and the trail below it ends on that same number.
 *
 * The trail is passed rather than derived: the sidebar stops at Pipeline, so on
 * its own it would read "Dashboards > <number>" and offer no way back to the
 * project the document belongs to.
 *
 * It reads the same cached query the screen below it uses, so naming the page
 * costs no extra request; "Quotation Document" stands in only for the moment
 * before that query answers.
 *
 * Not to be confused with `QuotationDocumentHeader`, which is the letterhead
 * card inside the record.
 */
export function QuotationDocumentPageHeader({
  projectId,
  documentId,
}: {
  projectId: string;
  documentId: string;
}) {
  const document = useQuotationDocument(projectId, documentId);
  const title = document.data?.document_no ?? 'Quotation Document';
  return (
    <PageHeader
      title={title}
      crumbs={projectCrumbs(projectId, { tab: 'quotations' }, { title })}
      actions={
        <BackToList
          listPath={`/project-sales/${projectId}?tab=quotations`}
          label="Back to project"
          appendListState={false}
        />
      }
    />
  );
}
