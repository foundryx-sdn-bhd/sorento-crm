'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { CheckCircle2, Stamp } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/common/PageHeader';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useProject } from '../../_shared/hooks/useProjects';
import { usePOIntakeController } from '../../_shared/hooks/usePOIntake';
import { useReviewOriginHref } from '../../_shared/hooks/useReviewOrigin';
import type { POVersion, POVersionLine } from '../../_shared/types/poIntake.types';
import { formatMyrExact, isMoneyZero, subtractMoney, sumMoney } from '../../_shared/lib/money';
import { POIntakeDocumentViewer } from './POIntakeDocumentViewer';
import {
  POIntakeExtractionFailed,
  POIntakeExtractionProgress,
  POIntakeNoLines,
  POIntakePartialBanner,
  POIntakeSkeleton,
} from './POIntakeExtractionStatus';
import {
  POIntakeLinesGrid,
  blockingNotes,
  lineNeedsAttention,
  type POIntakeLinesGridHandle,
} from './POIntakeLinesGrid';
import { POIntakeUploadDialog } from './POIntakeUploadDialog';

type ReviewTab = 'lines' | 'documents';

/**
 * "Did I get this right?" for one uploaded customer PO (S6, mockups/po-review.html).
 *
 * One page, one lines table, focused on what was identified as needing a look; the PDF and
 * the document notes move together onto their own Documents tab, always (R14(b)/R18), so
 * Lines has the full page width at every size. One primary button, always the next step
 * (Confirm this PO, then Approve, then Countersign); the status trail states the other two
 * without a card of their own.
 */
export function POIntakeConfirmClient({
  projectId,
  versionId,
}: {
  projectId: string;
  versionId: string;
}) {
  const intake = usePOIntakeController(versionId);
  const router = useRouter();
  const originHref = useReviewOriginHref();

  const project = useProject(projectId);
  const canEdit = project.data ? project.data.can_edit !== false : true;

  const gridRef = React.useRef<POIntakeLinesGridHandle>(null);
  const [activeTab, setActiveTab] = React.useState<ReviewTab>('lines');
  const [page, setPage] = React.useState(1);
  const [focusedLineId, setFocusedLineId] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const pendingReview = React.useRef(false);
  // A row named from the Documents tab (jumpToProblem) cannot be focused until the Lines tab's
  // grid has mounted. Radix TabsContent remounts over more than one render pass, so a
  // `useEffect` keyed on `activeTab` fires while `gridRef.current` is still null - verified with
  // a debug log. The callback ref below (`attachGridRef`) runs exactly when the instance is
  // actually attached, so it is the one place both pending jobs (a note review, a line focus)
  // can be finished reliably.
  const pendingFocusLineId = React.useRef<string | null>(null);

  const version = intake.version;

  // The Documents tab is where the viewer lives now (R14(b)), so a jump to a page has to
  // bring the reader to the tab that shows it, not just set a page number nobody can see.
  const showPage = React.useCallback((pageNo: number) => {
    setPage(pageNo);
    setActiveTab('documents');
  }, []);

  const focusLine = React.useCallback((line: POVersionLine) => {
    setFocusedLineId(line.id);
    if (line.page_no) setPage(line.page_no);
  }, []);

  /**
   * "Review them" only ever shows while a note NAMING A LINE is still unreviewed (F4: a note
   * naming no line has nowhere left to review since the Documents-tab annotations grid was
   * removed, so it no longer counts toward `unreviewed` below) - so this always finds one, on
   * the Lines tab, and opens its popover there (S6-4/S9). The Lines tab has to be mounted for
   * the grid ref to answer, so a Documents-tab click switches tabs first and `attachGridRef`
   * below finishes the job once Lines has actually mounted.
   */
  const reviewNextNote = React.useCallback(() => {
    if (activeTab !== 'lines') {
      pendingReview.current = true;
      setActiveTab('lines');
      return;
    }
    gridRef.current?.focusFirstUnreviewedAnnotation();
  }, [activeTab]);

  // See the comment on `pendingFocusLineId` above: this callback ref, not a `useEffect` keyed
  // on `activeTab`, is what finishes a job that had to wait for the Lines tab's grid to mount.
  const attachGridRef = React.useCallback((instance: POIntakeLinesGridHandle | null) => {
    gridRef.current = instance;
    if (!instance) return;
    if (pendingReview.current) {
      pendingReview.current = false;
      instance.focusFirstUnreviewedAnnotation();
    }
    if (pendingFocusLineId.current) {
      const id = pendingFocusLineId.current;
      pendingFocusLineId.current = null;
      instance.focusLine(id);
    }
  }, []);

  // S4: Confirm returns the user to where they came from. With no origin (a deep link or a
  // bookmark) it stays on the page, exactly as before this slice.
  const handleConfirm = React.useCallback(async () => {
    const confirmed = await intake.confirm();
    if (confirmed && originHref) router.push(originHref);
  }, [intake, originHref, router]);

  if (intake.isLoading) return <POIntakeSkeleton />;

  if (intake.isError || !version) {
    return (
      <div className="space-y-4">
        <PageHeader title="Customer PO" crumbs={CRUMBS_LOADING} />
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
          <h2 className="text-sm font-semibold text-destructive">
            This PO document could not be loaded
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {intake.error instanceof Error
              ? intake.error.message
              : 'It may have been removed, or you may not have access to this project.'}
          </p>
          <Button asChild variant="outline" className="mt-4">
            <Link href={`/project-sales/${projectId}`}>Back to the project</Link>
          </Button>
        </div>
      </div>
    );
  }

  const poNumber = version.purchase_order?.po_number ?? version.header.po_number;
  const approvedByName =
    version.purchase_order?.approved_by_name ?? version.approved_by_name ?? null;
  const approvedAt = version.purchase_order?.approved_at ?? version.approved_at ?? null;
  const countersignedByName =
    version.purchase_order?.countersigned_by_name ?? version.countersigned_by_name ?? null;
  const countersignedAt =
    version.purchase_order?.countersigned_at ?? version.countersigned_at ?? null;

  // F4 (owner hand test 25 Sep 2026, item 4): a note naming no line - a signature, "Continue
  // To Next Page", delivery instructions - had its only surface in the Documents-tab
  // annotations grid, which is gone. With nowhere left to review it, it no longer blocks
  // Confirm. A note naming a line still does, through that row's own indicator (F2). The
  // server gates Confirm on this same rule.
  const unreviewed = blockingNotes(version.annotations, version.lines);
  const confirmed = Boolean(version.confirmed_at);
  const readOnly = !canEdit || confirmed;
  const extractionSettled =
    version.extraction_state !== 'failed' && version.extraction_state !== 'queued'
      && version.extraction_state !== 'running';

  const jumpToProblem = () => {
    const target =
      version.lines.find((line) => !line.is_cancelled && !line.arithmetic_ok) ??
      version.lines.find(lineNeedsAttention);
    if (!target) return;
    if (activeTab !== 'lines') {
      pendingFocusLineId.current = target.id;
      setActiveTab('lines');
      return;
    }
    gridRef.current?.focusLine(target.id);
  };

  const crumbs = [
    { title: 'Project Sales' },
    { title: 'Projects', path: '/project-sales/pipeline' },
    { title: project.data?.title ?? 'Project', path: `/project-sales/${projectId}` },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            {poNumber ? `PO ${poNumber} v${version.version_no}` : 'PO number not read yet'}
            <StatusPill version={version} />
            {!canEdit && <Badge variant="outline">Read only</Badge>}
          </span>
        }
        crumbs={crumbs}
        actions={
          extractionSettled ? (
            <div className="flex flex-col items-end gap-2">
              {!readOnly && (
                <Button
                  type="button"
                  disabled={
                    unreviewed.length > 0 || intake.isConfirming || version.lines.length === 0
                  }
                  onClick={() => void handleConfirm()}
                >
                  <CheckCircle2 className="size-4" aria-hidden />
                  {intake.isConfirming ? 'Confirming…' : 'Confirm this PO'}
                </Button>
              )}
              {confirmed && !approvedAt && canEdit && (
                <Button
                  type="button"
                  disabled={intake.isStamping}
                  onClick={() => void intake.approve()}
                >
                  <Stamp className="size-4" aria-hidden />
                  Approve
                </Button>
              )}
              {approvedAt && !countersignedAt && canEdit && (
                <Button
                  type="button"
                  disabled={intake.isStamping}
                  onClick={() => void intake.countersign()}
                >
                  <Stamp className="size-4" aria-hidden />
                  Countersign
                </Button>
              )}
              {!readOnly && unreviewed.length > 0 && (
                <span className="flex flex-wrap items-center gap-1 text-right text-xs text-muted-foreground">
                  {`${unreviewed.length} handwritten note${unreviewed.length === 1 ? '' : 's'} still unreviewed`}
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    className="h-auto p-0 text-xs"
                    onClick={reviewNextNote}
                  >
                    Review them
                  </Button>
                </span>
              )}
              <StatusTrail
                confirmedAt={version.confirmed_at}
                confirmedBy={version.confirmed_by_name ?? null}
                approvedAt={approvedAt}
                approvedBy={approvedByName}
                countersignedAt={countersignedAt}
                countersignedBy={countersignedByName}
              />
            </div>
          ) : undefined
        }
      />
      {extractionSettled && (
        <p className="text-sm text-muted-foreground">
          {project.data?.project_code
            ? `${project.data.title} (${project.data.project_code})`
            : project.data?.title}
          {` · ${version.lines.length} line${version.lines.length === 1 ? '' : 's'}`}
          <TotalsMetaLine version={version} onJumpToProblem={jumpToProblem} />
        </p>
      )}

      {version.extraction_state === 'failed' ? (
        <POIntakeExtractionFailed
          version={version}
          onReupload={canEdit ? () => setUploading(true) : undefined}
          onRetry={canEdit ? () => void intake.retryExtraction() : undefined}
          isRetrying={intake.isRetrying}
        />
      ) : intake.isPolling ? (
        <POIntakeExtractionProgress version={version} />
      ) : (
        <>
          {intake.phase === 'partial' && (
            <POIntakePartialBanner
              version={version}
              onReupload={canEdit ? () => setUploading(true) : undefined}
            />
          )}

          <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as ReviewTab)}>
            <TabsList aria-label="PO version sections">
              <TabsTrigger value="lines">
                {`Lines (${version.lines.length})`}
              </TabsTrigger>
              <TabsTrigger value="documents">Documents</TabsTrigger>
            </TabsList>

            <TabsContent value="lines">
              {version.lines.length === 0 ? (
                <POIntakeNoLines onReupload={canEdit ? () => setUploading(true) : undefined} />
              ) : (
                <POIntakeLinesGrid
                  ref={attachGridRef}
                  lines={version.lines}
                  readOnly={readOnly}
                  savingLineIds={intake.savingLineIds}
                  focusedLineId={focusedLineId}
                  onFocusLine={focusLine}
                  onUpdateLine={intake.updateLine}
                  annotations={version.annotations}
                  savingAnnotationIds={intake.savingAnnotationIds}
                  onShowPage={showPage}
                  onAcceptAnnotation={intake.acceptAnnotation}
                  onEditAnnotation={intake.editAnnotation}
                  onRejectAnnotation={intake.rejectAnnotation}
                  defaultFlaggedOnly={!confirmed}
                />
              )}
            </TabsContent>

            {/* Owner hand test 25 Sep 2026, item 4: "just show me the entire document" - the
                annotations grid that used to sit below the PDF is gone, and the viewer takes
                the tab's own full height (the same 14rem margin the Lines tab's own grid
                reserves) instead of a short fixed-height strip, so every page is reachable by
                scrolling the viewer itself. `dvh`, not `vh` (M6-02/M6-03): this tab is verified
                at 375px, and `vh` sits taller than what mobile Safari can actually show. The
                R13 empty state stays as it was; it is a small dashed panel, not a document to
                fill the tab with. */}
            <TabsContent value="documents" className="flex h-[calc(100dvh-14rem)] flex-col">
              {version.document_url ? (
                <POIntakeDocumentViewer
                  documentUrl={version.document_url}
                  documentKey={version.id}
                  attachmentId={version.attachment_id}
                  pageCount={version.page_count}
                  page={page}
                  onPageChange={setPage}
                  className="flex-1"
                />
              ) : (
                <POIntakeDocumentEmptyState
                  onReupload={canEdit ? () => setUploading(true) : undefined}
                />
              )}
            </TabsContent>
          </Tabs>
        </>
      )}

      {uploading && (
        <POIntakeUploadDialog
          projectId={projectId}
          purchaseOrderId={version.purchase_order_id}
          purchaseOrderNumber={poNumber}
          onDone={() => setUploading(false)}
        />
      )}
    </div>
  );
}

const CRUMBS_LOADING = [{ title: 'Project Sales' }, { title: 'Projects', path: '/project-sales/pipeline' }];

/** The one status pill the header carries: what stage of reading and confirming it is at. */
function StatusPill({ version }: { version: POVersion }) {
  switch (version.extraction_state) {
    case 'queued':
      return <Badge variant="secondary">Waiting to be read</Badge>;
    case 'running':
      return <Badge variant="secondary">Being read</Badge>;
    case 'failed':
      return <Badge variant="destructive">Could not be read</Badge>;
    default:
      return version.confirmed_at ? (
        <Badge variant="success">Confirmed</Badge>
      ) : (
        <Badge variant="warning">To confirm</Badge>
      );
  }
}

/**
 * The document total against our sum, and why they differ, on one line (S6-1) - replaces the
 * separate totals banner. One case is deliberately not alarming: once a handwritten
 * cancellation has been accepted, our sum legitimately drops below the printed total by
 * exactly the cancelled amount, so that gap is named as a fact rather than a fault.
 */
function TotalsMetaLine({
  version,
  onJumpToProblem,
}: {
  version: POVersion;
  onJumpToProblem: () => void;
}) {
  const { totals, lines } = version;
  const cancelled = lines.filter((line) => line.is_cancelled);
  const cancelledTotal = sumMoney(cancelled.map((line) => line.amount));
  const difference = subtractMoney(totals.lines_total, totals.extracted_total);
  const explainedByCancellations =
    difference !== null &&
    cancelledTotal !== null &&
    !isMoneyZero(cancelledTotal) &&
    isMoneyZero(sumMoney([difference, cancelledTotal]));

  if (totals.extracted_total === null) {
    return <> · Document total not read yet · Our sum {formatMyrExact(totals.lines_total)}</>;
  }

  const ourSum = ` · Our sum ${formatMyrExact(totals.lines_total)}`;

  if (difference === null || isMoneyZero(difference)) {
    return (
      <>
        {` · Document total ${formatMyrExact(totals.extracted_total)}`}
        {ourSum}
      </>
    );
  }

  const shortfall = difference.startsWith('-');
  const magnitude = formatMyrExact(difference.replace('-', ''));
  const text = explainedByCancellations
    ? `${magnitude} short, ${cancelled.length} cancelled line${cancelled.length === 1 ? '' : 's'}`
    : `${magnitude} ${shortfall ? 'below' : 'above'} the total printed on the document`;

  return (
    <>
      {` · Document total ${formatMyrExact(totals.extracted_total)}`}
      {ourSum}
      {' · '}
      <Button
        type="button"
        variant="link"
        size="sm"
        className="h-auto p-0 text-sm font-medium text-amber-700 dark:text-amber-400"
        onClick={onJumpToProblem}
      >
        {text}
      </Button>
    </>
  );
}

/** R13: a missing PDF is a plain empty state, never an error code. */
function POIntakeDocumentEmptyState({ onReupload }: { onReupload?: () => void }) {
  return (
    <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
      <h3 className="text-sm font-semibold">This PDF is not available yet</h3>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
        The source file has not finished uploading, or could not be found.
      </p>
      {onReupload && (
        <Button type="button" className="mt-4" onClick={onReupload}>
          Upload the PO again
        </Button>
      )}
    </div>
  );
}

/**
 * Confirmed, Approved, Countersigned as three pills (S6-1): the button above is always the
 * next step, and this states the other two without a bordered card of their own. Who and
 * when stay in the accessible name so a screen reader (and a test) can still find them; the
 * pill itself keeps the mockup's plain label.
 */
function StatusTrail({
  confirmedAt,
  confirmedBy,
  approvedAt,
  approvedBy,
  countersignedAt,
  countersignedBy,
}: {
  confirmedAt: string | null;
  confirmedBy: string | null;
  approvedAt: string | null;
  approvedBy: string | null;
  countersignedAt: string | null;
  countersignedBy: string | null;
}) {
  const stages: Array<{ label: string; at: string | null; by: string | null }> = [
    { label: 'Confirmed', at: confirmedAt, by: confirmedBy },
    { label: 'Approved', at: approvedAt, by: approvedBy },
    { label: 'Countersigned', at: countersignedAt, by: countersignedBy },
  ];

  return (
    <div className="flex items-center gap-1 text-xs">
      {stages.map((stage, index) => {
        const done = Boolean(stage.at);
        return (
          <React.Fragment key={stage.label}>
            {index > 0 && <span className="text-muted-foreground">&rsaquo;</span>}
            <Badge
              variant={done ? 'success' : 'secondary'}
              title={done ? `${stage.by ?? 'Recorded'} · ${formatDateTimeInMalaysia(stage.at as string)}` : 'Not yet'}
            >
              {stage.label}
              <span className="sr-only">
                {done
                  ? ` by ${stage.by ?? 'Recorded'} · ${formatDateTimeInMalaysia(stage.at as string)}`
                  : ' Not yet'}
              </span>
            </Badge>
          </React.Fragment>
        );
      })}
    </div>
  );
}
