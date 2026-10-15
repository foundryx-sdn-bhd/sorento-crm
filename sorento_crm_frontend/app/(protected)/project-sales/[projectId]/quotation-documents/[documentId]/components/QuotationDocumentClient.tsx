'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PenLine } from 'lucide-react';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useHasPermission } from '@/hooks/usePermissions';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import DetailActions from '@/components/common/DetailActions';
import { EntityDownloadsButton } from '@/components/my-downloads/EntityDownloadsButton';
import {
  useQuotationApprovalGraph,
  useQuotationDocument,
  useQuotationDocumentMutations,
  useQuotationDocuments,
  useQuotationIssues,
} from '../../../../_shared/hooks/useQuotationDocuments';
import RecordNavigation from '@/components/common/RecordNavigation';
import {
  useProject,
  useProjectQuotationVersions,
  useQuotationMutations,
  useQuotations,
} from '../../../../_shared/hooks/useProjects';
import {
  isLockedForApproval,
  nextAction,
  quotationStanding,
} from '../../../../_shared/lib/quotationDecision';
import { useQuotationDocumentActions } from '../../actions';
import { APPROVE_PERMISSION, QuotationApprovalPanel } from './QuotationApprovalPanel';
import { QuotationRejectDialog } from './QuotationRejectDialog';
import { ReviseApprovalDialog } from './ReviseApprovalDialog';
import { QuotationChangesRequestedPanel } from './QuotationChangesRequestedPanel';
import { QuotationDocumentProvider } from './QuotationDocumentContext';
import { QuotationDocumentTabs } from './QuotationDocumentTabs';
import { ReviseToEditDialog } from './ReviseToEditDialog';

/**
 * One quotation DOCUMENT: the letterhead the customer receives, and the tabs it is read through.
 *
 * This is the shell every tab renders inside. The identity of the record - its status, the project
 * title and developer lines, the one CTA and the gear - sits ABOVE the tabs and stays on screen
 * wherever the reader goes. The letterhead card (refs, recipient, total) is the Header tab (#1341).
 *
 * The header follows the system's own rule and nothing else: ONE primary CTA, and every other
 * action behind the gear. Download is not a call to action, it is a thing you can also do;
 * issuing is the move that changes what the customer holds. Edit quotation is behind the gear for
 * the same reason, and opens the whole quotation in the form page (#1341): this page is a read.
 *
 * The document is fetched HERE, once, and handed to the tabs through context. Fetching it per tab
 * would let two tabs hold two versions of one quotation, and this is also the only place state can
 * live that has to survive a tab switch: routed tabs unmount their panels on the way out.
 */
export function QuotationDocumentClient({
  projectId,
  documentId,
  children,
}: {
  projectId: string;
  documentId: string;
  /** The open tab, routed in by the layout. */
  children?: React.ReactNode;
}) {
  const router = useRouter();
  const document = useQuotationDocument(projectId, documentId);
  const project = useProject(projectId);
  // Newest first, so [0] is the revision the customer currently holds - the one whose PDF and
  // counter-sign link are the ones worth handing out.
  const issues = useQuotationIssues(projectId, documentId);
  const mutations = useQuotationDocumentMutations(projectId, documentId);
  /**
   * The `quotation` approval graph, so the block below the header can offer the salesperson
   * their own next move by the label the admin gave that edge rather than by a hardcoded one.
   * One install-wide read, cached; it is Setup data, not per-quotation state.
   */
  const approvalGraph = useQuotationApprovalGraph();
  /**
   * Which scopes are still open for editing, straight from the server's own `is_editable`.
   *
   * Read here rather than inside the Lines tab because Edit sits in this header and is on
   * screen from every tab. Both queries are the ones the Lines tab and the line editor already
   * use, so react-query answers them from cache on the usual path.
   */
  const quotations = useQuotations(projectId);
  // The project's OTHER quotation documents, for prev/next in the header.
  const siblingDocuments = useQuotationDocuments(projectId);
  // The project's documents are already in memory and unpaged, so the pager is
  // presentational: position and ends computed here.
  const documentIndex = (siblingDocuments.data ?? []).findIndex(
    (row) => row.id === documentId,
  );
  const scopeVersions = useProjectQuotationVersions(quotations.data);
  const quotationMutations = useQuotationMutations(projectId);
  // Edit quotation opens the form page (#1341): "Edit quotation means I edit the whole quotation".
  const editPath = `/project-sales/${projectId}/quotation-documents/${documentId}/edit`;

  const [activeScopeId, setActiveScopeId] = React.useState<string | null>(null);
  const [revisePrompt, setRevisePrompt] = React.useState(false);
  const [approvalRevisePrompt, setApprovalRevisePrompt] = React.useState(false);
  const [rejecting, setRejecting] = React.useState(false);
  const canApprove = useHasPermission(APPROVE_PERMISSION);

  const selectScope = React.useCallback((scopeId: string) => setActiveScopeId(scopeId), []);

  // Memoised because the total below is: `?? []` mints a new array whenever the document has no
  // scopes, which would re-sum the header on every render of the page.
  const documentScopes = document.data?.scopes;
  const scopes = React.useMemo(() => documentScopes ?? [], [documentScopes]);

  /**
   * Every scope whose current version is frozen, so Edit knows whether it is about to change
   * this quotation or to open the next one.
   *
   * `is_editable` from the server, never `is_current`: an issued version is still the highest
   * one until somebody revises. And never the document's `is_issued` either, which stays true
   * for good and would keep offering a revision to somebody who has already opened one.
   */
  const lockedScopes = React.useMemo(() => {
    // Only THIS document's scopes. `quotations.data` is every scope in the PROJECT, across every
    // quotation document, so counting it told a clean single-scope draft that four scopes were
    // with the customer and offered a revision nobody needed.
    const mine = new Set(scopes.map((scope) => scope.id));
    return (quotations.data ?? [])
      .filter((quotation) => mine.has(quotation.id))
      .map((quotation) => ({
        quotation,
        current: scopeVersions.rows.find(
          (row) => row.quotation.id === quotation.id && row.version.is_current,
        )?.version,
      }))
      .filter((pair) => pair.current && !(pair.current.is_editable ?? pair.current.is_current));
  }, [quotations.data, scopeVersions.rows, scopes]);

  const canEditProject = project.data?.can_edit ?? false;
  const gear = useQuotationDocumentActions({
    projectId,
    document: document.data,
    canEdit: canEditProject,
    latestIssue: issues.data?.[0] ?? null,
    onEdit: startEditing,
    onDeleted: () => router.push(`/project-sales/${projectId}?tab=quotations`),
    surface: 'inline',
  });

  if (document.isLoading || project.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (document.isError || !document.data || !project.data) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
        <h2 className="text-sm font-semibold text-destructive">
          This quotation could not be loaded
        </h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
          {document.error instanceof Error ? document.error.message : 'It may have been deleted.'}
        </p>
        <Button asChild variant="outline" className="mt-4">
          <Link href={`/project-sales/${projectId}?tab=quotations`}>Back to quotations</Link>
        </Button>
      </div>
    );
  }

  const record = document.data;
  const canEdit = project.data.can_edit;
  const standing = quotationStanding(record);
  // R1 on a document nobody has issued, R3 on one that stands at R2. One expression for both:
  // the next revision is always the one after whatever the customer currently holds.
  const nextIssueNo = (record.current_issue_no ?? 0) + 1;
  const latestIssue = issues.data?.[0] ?? null;
  const sorentoSignature = record.signatory_signature ?? null;
  /**
   * AC-H1: no signature, no issue. The server refuses an unsigned document with 422
   * `quotation_document_unsigned`, so the CTA says so up front rather than letting somebody
   * press it and read an error.
   *
   * Read off the signature alone. "It is issued, so it must have been signed" looks equivalent
   * and is wrong on any document issued before the gate existed: the button goes live and the
   * server refuses it.
   */
  const isSigned = Boolean(sorentoSignature);
  const action = nextAction(record, { canEdit, canApprove, isSigned });
  const locked = isLockedForApproval(record);
  /**
   * The "Send for approval" label comes off the graph's own draft -> pending edge, so a renamed
   * edge reads the way the admin named it; with no graph it falls back to the plain words. The
   * target is the pending rung itself: the server walks Rejected -> Draft -> Pending on its own.
   */
  const pendingStatus = approvalGraph.data?.statuses.find((row) => row.key === 'pending_approval');
  const draftStatus = approvalGraph.data?.statuses.find((row) => row.key === 'draft');
  const sendEdge = approvalGraph.data?.transitions.find(
    (edge) =>
      edge.to_status_id === pendingStatus?.id && edge.from_status_id === draftStatus?.id,
  );
  const sendForApprovalLabel = sendEdge?.label || 'Send for approval';

  /** What the next copy of a single frozen scope will be called, e.g. "v3". */
  const nextVersionLabel =
    lockedScopes.length === 1 && lockedScopes[0].current
      ? `v${lockedScopes[0].current.version_no + 1}`
      : null;
  /**
   * What pressing the change-request banner's button will actually do, said in its label.
   *
   * Deliberately NOT "Revise to v3": the Lines tab already carries a per-scope button by that
   * exact name, and two controls reading the same words a few centimetres apart - one acting on
   * one scope, one on the whole document - is worse than a vaguer label. This one is the
   * DOCUMENT's act, and the prompt it opens names the scopes and the version it will mint.
   *
   * With no frozen scope left (a revision is already open) the click goes straight to the
   * form page, and the label has to say that rather than promise a version it will not mint.
   */
  const reviseLabel =
    lockedScopes.length === 0 ? 'Edit this quotation' : 'Revise this quotation';

  /**
   * What the header says about the state it is in, reason and next action together.
   *
   * The screen used to state a fact and stop there ("The customer holds this version, so its
   * lines cannot be changed"), and the client read it and still asked why they could not edit.
   * Every line here names the way forward as well as the reason.
   */
  const headerHints: string[] = [];
  if (!canEdit) {
    headerHints.push('You can read this quotation but not change it.');
  } else if (lockedScopes.length > 0) {
    headerHints.push('The customer holds this version. Edit opens the next one.');
  }
  if (action.hint) headerHints.push(action.hint);

  /**
   * Edit quotation: the whole quotation in the form page (#1341). On a quotation the customer
   * holds it asks first, because the answer is a revision; the form opens once that is made.
   */
  function startEditing() {
    if (locked) {
      setApprovalRevisePrompt(true);
      return;
    }
    if (lockedScopes.length > 0) {
      setRevisePrompt(true);
      return;
    }
    router.push(editPath);
  }

  async function reviseApprovalThenEdit(reason: string) {
    try {
      await mutations.reviseApproval.mutateAsync({ id: documentId, reason });
      setApprovalRevisePrompt(false);
      router.push(editPath);
    } catch {
      // The mutation toasted the reason. The dialog stays open so it can be tried again.
    }
  }

  async function reviseThenEdit() {
    try {
      // Sequentially, not in parallel: each revise reads and freezes the scope's current
      // version, and the failure of one must not leave the rest half-opened behind a spinner.
      for (const pair of lockedScopes) {
        await quotationMutations.revise.mutateAsync(pair.quotation.id);
      }
      setRevisePrompt(false);
      router.push(editPath);
    } catch {
      // The mutation toasted the reason. The dialog stays open so it can be tried again.
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          {/* No document number here: the page title above already is that number, and a
              second copy under the breadcrumb is repeated information (#1335). */}
          <div className="flex flex-wrap items-center gap-2">
            {/* The SAME reading as the project's quotation list. This badge used to be its own
                `is_issued ? Issued : Draft`, which is why a quotation the customer had accepted
                read "Accepted" in the list and "Issued" here - the two surfaces answered the same
                question differently, which is exactly what `quotationStanding` exists to stop. */}
            <span className={`${STATUS_PILL_BASE} ${statusPillClass(standing.label)}`}>
              {standing.label}
            </span>
          </div>
          <h2 className="mt-1 break-words text-xl font-semibold">
            {record.subject_title ?? '-'}
          </h2>
          <p className="break-words text-sm text-muted-foreground">
            {record.recipient_name_snapshot ?? '-'}
          </p>
        </div>

        {/* One CTA, then the gear. Issuing is the move; exports and the delete live behind the
            gear so the header states one intent. Sign sits beside it as an outline button only
            while it is the thing standing in the way. */}
        <div className="flex flex-col items-stretch gap-1.5 sm:items-end">
          {/* Pager, gear, primary (D6), through the shared group rather than a
              hand-rolled row: the order is the same rule on all 39 detail pages,
              and a copy of it here is a copy that can drift. */}
          <DetailActions
            pagerNode={
              /* Step between this project's quotation documents without going back to the
                  list, the users-detail pattern. List mode off the cached documents query -
                  the list screen the user came from already fetched it, so the neighbours
                  cost nothing. Rendered from two documents up, since with one there is nowhere to go. */
              (siblingDocuments.data ?? []).length > 1 && (
                <RecordNavigation
                  index={documentIndex >= 0 ? documentIndex + 1 : null}
                  total={(siblingDocuments.data ?? []).length}
                  hasPrevious={documentIndex > 0}
                  hasNext={
                    documentIndex >= 0 &&
                    documentIndex < (siblingDocuments.data ?? []).length - 1
                  }
                  onPrevious={() =>
                    router.push(
                      `/project-sales/${projectId}/quotation-documents/${
                        (siblingDocuments.data ?? [])[documentIndex - 1].id
                      }`,
                    )
                  }
                  onNext={() =>
                    router.push(
                      `/project-sales/${projectId}/quotation-documents/${
                        (siblingDocuments.data ?? [])[documentIndex + 1].id
                      }`,
                    )
                  }
                  ariaLabel="quotation"
                />
              )
            }
            pendingAction={gear.pending}
            gear={
              <DetailActionsMenu actions={gear.actions} ariaLabel="Quotation actions" />
            }
            primary={
              <>
              {action.kind === 'sign' && (
                <Button type="button" size="sm" onClick={gear.openSign}>
                  <PenLine className="size-4" aria-hidden />
                  Sign
                </Button>
              )}
              {action.kind === 'send_for_approval' && (
                <Button
                  type="button"
                  size="sm"
                  disabled={mutations.submitForApproval.isPending}
                  onClick={() => {
                    if (!pendingStatus) {
                      toast.error('The approval steps are still loading. Try again in a moment.');
                      return;
                    }
                    mutations.submitForApproval.mutate({
                      id: documentId,
                      toStatusId: pendingStatus.id,
                    });
                  }}
                >
                  {sendForApprovalLabel}
                </Button>
              )}
              {action.kind === 'approve' && (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={mutations.reject.isPending}
                    onClick={() => setRejecting(true)}
                  >
                    Reject
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={mutations.approve.isPending}
                    onClick={() => mutations.approve.mutate(documentId)}
                  >
                    Approve
                  </Button>
                </>
              )}
              {action.kind === 'send' && (
                <Button
                  type="button"
                  size="sm"
                  disabled={mutations.issue.isPending}
                  onClick={() => mutations.issue.mutate(documentId)}
                >
                  {/* The owner on "Issue R1": "call this Send to Customer" (#1341). The revision
                      stays in the label, as it did, because R2 is a different paper from R1. The
                      API and the status names keep "issue". */}
                  {`Send to Customer R${nextIssueNo}`}
                </Button>
              )}
              {/* Where a queued export is actually collected. Rendered only once something has
                  been issued, because a download of a revision that does not exist yet cannot: the
                  gear already says "Send it to the customer first", and a chip that can only ever read 0 is a
                  control with nothing behind it.

                  Keyed to the LATEST revision, which is the one whose exports anybody is chasing.
                  Earlier revisions' files are still in the My Downloads drawer. */}
              {latestIssue && (
                <EntityDownloadsButton
                  entityType="quotation_issue"
                  entityId={latestIssue.id}
                  label={latestIssue.our_ref_text ?? `${record.document_no} R${latestIssue.issue_no}`}
                  // The complaint header's own three classes, verbatim. It was always the same
                  // component; passing no className rendered it borderless beside two bordered
                  // buttons, which is why the client read it as not clickable ("the download
                  // should have border just like complaint").
                  className="h-8 border border-border"
                />
              )}
              </>
            }
          />
          {/* Why the screen is in the state it is in, and what to do about it, in one sentence.
              The old copy stated a fact and offered no move, which is what the client read and
              still could not act on: "i don't know when can i edit and when i cannot ... this
              line is confusing". Reason and next action, always together. */}
          {/* Visible, not only a tooltip: a disabled button with the reason hidden behind a
              hover is unreadable on the phone this page also has to work on. */}
          {headerHints.map((hint) => (
            <p key={hint} className="text-xs text-muted-foreground sm:text-right">
              {hint}
            </p>
          ))}
        </div>
      </header>

      {/* What the CUSTOMER said, on the document rather than behind the Signatures tab. Renders
          nothing unless they have actually asked for something, and stands down the moment they
          sign - acceptance wins here exactly as it does on their own counter-sign page.

          Above the price-floor gate on purpose: this is somebody waiting on an answer, the gate
          below is a rule about what may be sent. */}
      <QuotationChangesRequestedPanel
        document={record}
        reviseLabel={reviseLabel}
        // The SAME entry point Edit uses, so a revision is still something the salesperson is
        // asked about. Withheld from a reader.
        onRevise={canEdit ? startEditing : undefined}
        isRevising={quotationMutations.revise.isPending}
      />

      {/* The price-floor gate. Renders nothing at all unless a line on this quotation is
          priced below its floor, so the ordinary quotation gains no chrome and no extra step. */}
      <QuotationApprovalPanel document={record} />

      {/* The letterhead card lives in the Header tab now, first of the tabs (#1341): "i need this
          to be under 'Header' tab to align with our system design". */}
      <QuotationDocumentTabs projectId={projectId} documentId={documentId} />

      <QuotationDocumentProvider
        value={{
          projectId,
          documentId,
          document: record,
          project: project.data,
          canEdit,
          latestIssue,
          sorentoSignature,
          activeScopeId,
          selectScope,
        }}
      >
        {children}
      </QuotationDocumentProvider>

      {gear.dialogs}

      <ReviseToEditDialog
        open={revisePrompt}
        onOpenChange={setRevisePrompt}
        scopeLabels={lockedScopes.map((pair) => pair.quotation.scope_label)}
        nextVersionLabel={nextVersionLabel}
        isRevising={quotationMutations.revise.isPending}
        onConfirm={reviseThenEdit}
      />

      <ReviseApprovalDialog
        open={approvalRevisePrompt}
        onOpenChange={setApprovalRevisePrompt}
        isRevising={mutations.reviseApproval.isPending}
        onConfirm={reviseApprovalThenEdit}
      />

      <QuotationRejectDialog
        open={rejecting}
        onOpenChange={setRejecting}
        isDeciding={mutations.reject.isPending}
        onReject={(reason) => mutations.reject.mutateAsync({ id: documentId, reason })}
      />
    </div>
  );
}
