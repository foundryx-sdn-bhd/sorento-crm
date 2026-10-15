'use client';

/**
 * The quotation document action set (D15): Edit, Sign, the two exports, the counter-sign link,
 * then Delete in red.
 *
 * One definition, two renderings: the record's gear (`DetailActionsMenu actions`) and the list
 * row's "..." (`RowActionsMenu`). The hook owns the dialogs and the deferred delete those items
 * open, so a row and the record page cannot disagree about what a click does.
 */

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Download, Link2, PenLine, SquarePen, Trash2 } from 'lucide-react';
import type { RecordAction } from '@/components/common/recordActions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  quotationDocumentsKey,
  useQuotationDocumentMutations,
} from '../../_shared/hooks/useQuotationDocuments';
import { isLockedForApproval } from '../../_shared/lib/quotationDecision';
import {
  listQuotationIssues,
  type QuotationDocument,
  type QuotationIssue,
} from '../../_shared/services/quotationDocumentService';
import { QuotationSignDialog } from './[documentId]/components/QuotationSignDialog';
import { QuotationSignLinkDialog } from './[documentId]/components/QuotationSignLinkDialog';

export interface QuotationDocumentActionSet {
  actions: RecordAction[];
  /** Mount wherever the actions are rendered. */
  dialogs: React.ReactNode;
  /** The delete countdown, for the record page's primary slot. Null on a list row. */
  pending: React.ReactNode;
  openSign: () => void;
  isSigned: boolean;
}

export function useQuotationDocumentActions({
  projectId,
  document,
  canEdit,
  latestIssue,
  onEdit,
  onDeleted,
  surface = 'toast',
}: {
  projectId: string;
  /** Undefined while the record is still loading: no actions yet. */
  document: QuotationDocument | undefined;
  canEdit: boolean;
  /** The record page already holds it; a list row looks it up when an export is pressed. */
  latestIssue?: QuotationIssue | null;
  /** The record page asks before revising; a row opens the quotation instead. */
  onEdit?: () => void;
  onDeleted?: () => void;
  /** `inline` on the record page (countdown in the header, watched from mount); `toast` in a row. */
  surface?: 'inline' | 'toast';
}): QuotationDocumentActionSet {
  const router = useRouter();
  const documentId = document?.id ?? '';
  const mutations = useQuotationDocumentMutations(projectId, documentId);
  const [signing, setSigning] = React.useState(false);
  const [linkToShow, setLinkToShow] = React.useState<string | null>(null);

  const documentPath = `/project-sales/${projectId}/quotation-documents/${documentId}`;
  const isSigned = Boolean(document?.signatory_signature);
  const issued = Boolean(document?.is_issued);

  const remove = useDeferredAction({
    actionKey: 'quotation_document.delete',
    entityType: 'quotation_document',
    entityId: document?.id,
    verb: 'Deleting',
    subject: document?.our_ref ?? document?.document_no ?? '',
    surface,
    watchFromMount: surface === 'inline',
    successMessage: 'Quotation deleted',
    invalidateKeys: [quotationDocumentsKey(projectId)],
    onCommitted: onDeleted,
  });

  const issueToExport = React.useCallback(async () => {
    if (latestIssue) return latestIssue;
    try {
      return (await listQuotationIssues(projectId, documentId))[0] ?? null;
    } catch {
      return null;
    }
  }, [latestIssue, projectId, documentId]);

  const actions: RecordAction[] = [];
  if (!document) return { actions, dialogs: null, pending: null, openSign: () => {}, isSigned };

  if (canEdit) {
    actions.push({
      key: 'quotation_document.edit',
      label: 'Edit quotation',
      icon: SquarePen,
      run: () => {
        if (onEdit) return onEdit();
        // A row cannot ask the revise question: a quotation that is locked or already sent
        // opens on its own page, where Edit does.
        router.push(
          issued || isLockedForApproval(document) ? documentPath : `${documentPath}/edit`,
        );
      },
    });
    actions.push({
      key: 'quotation_document.sign',
      label: isSigned ? 'Sign again' : 'Sign quotation',
      icon: PenLine,
      run: () => setSigning(true),
    });
  }

  const exportHint = issued ? 'Prepared in My Downloads' : 'Send it to the customer first';
  actions.push(
    {
      key: 'quotation_document.pdf',
      label: 'Download PDF',
      icon: Download,
      description: exportHint,
      disabled: !issued || mutations.issuePdf.isPending,
      run: async () => {
        const issue = await issueToExport();
        if (!issue) return;
        try {
          await mutations.issuePdf.mutateAsync({ id: document.id, issueId: issue.id });
        } catch {
          // The mutation already toasted the reason.
        }
      },
    },
    {
      key: 'quotation_document.xlsx',
      label: 'Download Excel',
      icon: Download,
      description: exportHint,
      disabled: !issued || mutations.issueXlsx.isPending,
      run: async () => {
        const issue = await issueToExport();
        if (!issue) return;
        try {
          await mutations.issueXlsx.mutateAsync({ id: document.id, issueId: issue.id });
        } catch {
          // The mutation already toasted the reason.
        }
      },
    },
  );

  if (canEdit) {
    actions.push({
      key: 'quotation_document.sign_link',
      label: 'Copy counter-sign link',
      icon: Link2,
      description: issued ? undefined : 'Send it to the customer first',
      disabled: !issued || mutations.signLink.isPending,
      confirmLabel: 'Copied',
      run: async () => {
        const issue = await issueToExport();
        if (!issue) return false;
        try {
          const link = await mutations.signLink.mutateAsync({
            id: document.id,
            issueId: issue.id,
          });
          // The server returns a relative path on purpose; the origin the customer must land on
          // is whichever one this screen is being used from.
          const url = `${window.location.origin}${link.path}`;
          try {
            await navigator.clipboard.writeText(url);
            return true;
          } catch {
            // Clipboard refused (plain HTTP, browser policy): show the link instead.
            setLinkToShow(url);
            return false;
          }
        } catch {
          return false;
        }
      },
    });
    actions.push({
      key: 'quotation_document.delete',
      label: 'Delete quotation',
      icon: Trash2,
      kind: 'destructive',
      disabled: issued || remove.isPending,
      disabledReason: issued ? 'Sent to the customer, so it cannot be deleted' : undefined,
      run: () => remove.start(),
    });
  }

  const dialogs = (
    <>
      <QuotationSignDialog
        open={signing}
        onOpenChange={setSigning}
        signatoryName={document.signatory_name}
        isSaving={mutations.sign.isPending}
        onSign={async (body) => {
          try {
            await mutations.sign.mutateAsync({ id: document.id, body });
            setSigning(false);
          } catch {
            // The mutation toasted the reason; the dialog stays open to re-apply.
          }
        }}
      />
      <QuotationSignLinkDialog url={linkToShow} onOpenChange={() => setLinkToShow(null)} />
    </>
  );

  return { actions, dialogs, pending: remove.countdown, openSign: () => setSigning(true), isSigned };
}
