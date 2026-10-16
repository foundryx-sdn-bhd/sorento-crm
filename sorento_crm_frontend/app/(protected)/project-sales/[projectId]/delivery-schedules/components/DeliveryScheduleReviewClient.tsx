'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { AlertTriangle, History, RefreshCw } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { PageHeader } from '@/components/common/PageHeader';
import RecordNavigation from '@/components/common/RecordNavigation';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { useProject } from '../../../_shared/hooks/useProjects';
import {
  useDeliverySchedulePriorVersion,
  useDeliveryScheduleVersion,
  useDeliveryScheduleVersionMutations,
  useDeliverySchedules,
} from '../../../_shared/hooks/useDeliverySchedules';
import { usePOVersion } from '../../../_shared/hooks/usePOIntake';
import { useReviewOriginHref } from '../../../_shared/hooks/useReviewOrigin';
import { resolveExtractionPhase } from '../../../_shared/types/deliverySchedule.types';
import type {
  DeliveryScheduleConfirmBody,
  DeliveryScheduleVersion,
} from '../../../_shared/types/deliverySchedule.types';
import { DeliveryScheduleUploadDialog } from '../../components/DeliveryScheduleUploadDialog';
import { POIntakeDocumentViewer } from '../../components/POIntakeDocumentViewer';
import { POIntakeExtractionProgress } from '../../components/POIntakeExtractionStatus';
import {
  demoScheduleVersionState,
  useDemoScheduleState,
} from '../_demo/scheduleDemo';
import {
  blocksConfirm,
  buildCellMap,
  buildCellMetaMap,
  buildColumnStates,
  cellMapKey,
  dateColumns as buildDateColumns,
  groupPhasesByArea,
  isQty,
  normaliseQty,
} from '../lib/scheduleTotals';
import type { ColumnState } from '../lib/scheduleTotals';
import { DeliveryScheduleByDateMatrix } from './DeliveryScheduleByDateMatrix';
import { DeliveryScheduleColumnCards } from './DeliveryScheduleColumnCards';
import { DeliveryScheduleConfirmDialog } from './DeliveryScheduleConfirmDialog';
import { DeliveryScheduleMatrix } from './DeliveryScheduleMatrix';
import type { ColumnFocusRequest, ScheduleGridController } from './DeliveryScheduleMatrix';
import { DeliveryScheduleNotes } from './DeliveryScheduleNotes';
import { poProductOptions } from './DeliveryScheduleProductPicker';
import { DeliveryScheduleRevisionDiff } from './DeliveryScheduleRevisionDiff';
import { DeliveryScheduleRevisionProposals } from './DeliveryScheduleRevisionProposals';

type ReviewTab = 'schedule' | 'documents';
type RowFilter = 'attention' | 'all';

/**
 * The chosen segment of "By area | By date" and "Need attention | All rows" (W2).
 *
 * Owner hand test, 26 Sep: the outline toggle's selected state, a slightly whiter background,
 * left them unable to tell which segment was on. Filled with the accent and bold, the same
 * selected style as the dealer-kit segmented control (`PrintBySelect`). The focus ring is a
 * ring offset from the edge, never a fill, so focus and selection read as two things.
 */
const SEGMENT_CLASS =
  'px-3 data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:font-semibold ' +
  'data-[state=on]:text-primary-foreground data-[state=on]:hover:bg-primary/90 ' +
  'data-[state=on]:hover:text-primary-foreground focus-visible:ring-2 focus-visible:ring-ring ' +
  'focus-visible:ring-offset-2';

/**
 * Reviewing one version of a delivery schedule (S5, mockups/delivery-schedule-review.html).
 *
 * One page, one table: the matrix IS the reconciliation. A column that does not agree with the
 * PO carries its Flag on its own row, with the fix and "Dismiss with a reason" behind it (R20),
 * and while the version is unconfirmed the matrix opens on "Need attention", the rows that hold
 * up Confirm schedule by the same rule the server applies. The schedule file has its own
 * Documents tab (R18); the revision diff, the re-dating proposals and the document notes sit
 * behind one History button rather than three tabs. One primary button.
 */
export function DeliveryScheduleReviewClient({
  projectId,
  versionId,
}: {
  projectId: string;
  versionId: string;
}) {
  const demo = useDemoScheduleState();
  const project = useProject(demo ? undefined : projectId);
  const live = useDeliveryScheduleVersion(versionId, { enabled: !demo });
  const view = demo ? demoScheduleVersionState(demo) : live;
  const version = view.data;

  const {
    saveCells,
    resolveProduct,
    dismissColumn,
    confirm,
    retryExtraction,
    acceptProposal,
    rejectProposal,
  } = useDeliveryScheduleVersionMutations(projectId, versionId);
  /** Which proposal a request is in flight for, so only its own card shows pending. */
  const [pendingProposalIndex, setPendingProposalIndex] = React.useState<number | null>(null);
  /**
   * The walk is the project's SCHEDULES, the list this review was opened from, each stepped to
   * at its latest version. Opening an older version is not on that list, so the pager hides
   * itself there (S3-05). The demo screen has no neighbours to ask for.
   */
  const router = useRouter();
  const originHref = useReviewOriginHref();
  const schedules = useDeliverySchedules(demo ? undefined : projectId);
  const scheduleRows = schedules.data ?? [];
  const scheduleIndex = scheduleRows.findIndex(
    (row) => row.latest_version_id === versionId,
  );
  const goToSchedule = (row: { latest_version_id: string | null } | undefined) => {
    if (row?.latest_version_id) {
      router.push(
        `/project-sales/${projectId}/delivery-schedules/${row.latest_version_id}`,
      );
    }
  };
  // The version this one revises, for the was -> now diff. No-op on a version 1 or on demo.
  const priorVersion = useDeliverySchedulePriorVersion(version, { enabled: !demo });

  /**
   * The PO this schedule was checked against, for the column pickers: a column has to land on
   * a line of THIS PO or it cannot reconcile. Read once here rather than once per Flag.
   */
  const poVersion = usePOVersion(version?.po_version_id ?? undefined, !demo);
  const poOptions = React.useMemo(
    () => poProductOptions(poVersion.data?.lines ?? []),
    [poVersion.data?.lines],
  );

  const [drafts, setDrafts] = React.useState<Map<string, string>>(new Map());
  const [confirming, setConfirming] = React.useState(false);
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [uploading, setUploading] = React.useState(false);
  const [activeTab, setActiveTab] = React.useState<ReviewTab>('schedule');
  const [documentPage, setDocumentPage] = React.useState(1);
  /** Null until the reviewer picks one, so the default can follow the version's state. */
  const [rowFilter, setRowFilter] = React.useState<RowFilter | null>(null);
  /**
   * The column a "Fix the quantities" press asked to be put INSIDE. The nonce is what makes a
   * second press of the same button fire again.
   */
  const [focusRequest, setFocusRequest] = React.useState<ColumnFocusRequest>(null);
  /**
   * A column has TWO nodes: the matrix row and the phone card are both mounted and a media
   * query hides one. Both are kept, and the one actually laid out is chosen at jump time.
   */
  const columnRefs = React.useRef<Map<string, Set<HTMLElement>>>(new Map());

  const phase = version ? resolveExtractionPhase(version) : 'queued';
  const canEdit =
    (demo ? true : (project.data?.can_edit ?? false)) && !version?.confirmed_at;

  const storedCells = React.useMemo(
    () => buildCellMap(version?.cells ?? []),
    [version?.cells],
  );
  const cellMeta = React.useMemo(
    () => buildCellMetaMap(version?.cells ?? []),
    [version?.cells],
  );

  /**
   * By area is the document's own columns; By date turns the same cells round by their
   * EFFECTIVE date, so an accepted re-date shows under the date it now goes out on.
   */
  const [viewMode, setViewMode] = React.useState<'phase' | 'date'>('phase');
  const dateColumnsData = React.useMemo(
    () => buildDateColumns({ phases: version?.phases ?? [], cells: version?.cells ?? [] }),
    [version?.phases, version?.cells],
  );

  const columns = React.useMemo(
    () =>
      buildColumnStates(
        version?.products ?? [],
        version?.phases ?? [],
        version?.cells ?? [],
        drafts,
      ),
    [version?.products, version?.phases, version?.cells, drafts],
  );

  const phaseGroups = React.useMemo(
    () => groupPhasesByArea(version?.phases ?? []),
    [version?.phases],
  );

  /** One rule for what blocks, shared by Confirm, its dialog and "Need attention" (lesson e). */
  const blocking = React.useMemo(() => columns.filter(blocksConfirm), [columns]);
  const effectiveFilter: RowFilter = rowFilter ?? (version?.confirmed_at ? 'all' : 'attention');
  /**
   * Rows typed into since "Need attention" was last chosen. They stay in it even once they add
   * up: the keystroke that fixes a row must not unmount the input being typed into, or it never
   * blurs and the fix is never saved. Choosing the view again lets them go.
   */
  const [touched, setTouched] = React.useState<ReadonlySet<string>>(new Set());
  const chooseFilter = (next: RowFilter) => {
    setTouched(new Set());
    setRowFilter(next);
  };
  const visibleColumns =
    effectiveFilter === 'attention'
      ? columns.filter((column) => blocksConfirm(column) || touched.has(column.key))
      : columns;

  const registerColumnRef = React.useCallback((key: string, node: HTMLElement | null) => {
    // A ref callback reports its unmount as a bare null and never says which node it was
    // holding, so nothing is removed here; the detached ones are swept on the next jump.
    if (!node) return;
    const nodes = columnRefs.current.get(key);
    if (nodes) nodes.add(node);
    else columnRefs.current.set(key, new Set([node]));
  }, []);

  // jsdom implements no scrollIntoView, hence the optional call.
  const jumpToColumn = React.useCallback((key: string) => {
    const nodes = columnRefs.current.get(key);
    if (!nodes) return;
    for (const node of nodes) if (!node.isConnected) nodes.delete(node);
    // `offsetParent` is null for anything a media query has hidden. jsdom lays nothing out,
    // so it is null there for every node and the first one stands in.
    const all = Array.from(nodes);
    const target = all.find((node) => node.offsetParent !== null) ?? all[0];
    target?.scrollIntoView?.({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }, []);

  /**
   * Bring the column on screen, then hand it the cursor. The quantities are only editable in
   * By area, so a press from By date switches there first; the views answer the request
   * themselves because only they know which cell is the first editable one.
   */
  const jumpAndFocusColumn = React.useCallback(
    (key: string) => {
      setViewMode('phase');
      jumpToColumn(key);
      setFocusRequest((previous) => ({ key, nonce: (previous?.nonce ?? 0) + 1 }));
    },
    [jumpToColumn],
  );

  const valueFor = React.useCallback(
    (phaseId: string, columnKey: string) => {
      const key = cellMapKey(phaseId, columnKey);
      const draft = drafts.get(key);
      if (draft !== undefined) return draft;
      return storedCells.get(key) ?? '';
    },
    [drafts, storedCells],
  );

  const setDraft = React.useCallback(
    (phaseId: string, columnKey: string, value: string) => {
      setDrafts((previous) => {
        const next = new Map(previous);
        next.set(cellMapKey(phaseId, columnKey), value);
        return next;
      });
      setTouched((previous) =>
        previous.has(columnKey) ? previous : new Set(previous).add(columnKey),
      );
    },
    [],
  );

  const dropDraft = React.useCallback((key: string) => {
    setDrafts((previous) => {
      if (!previous.has(key)) return previous;
      const next = new Map(previous);
      next.delete(key);
      return next;
    });
  }, []);

  /**
   * Saves one cell on blur. The column total is recomputed locally from the drafts, so a
   * corrected column flips to reconciled as the number is typed rather than after the round
   * trip. The write still goes out, and the response replaces the version in the cache.
   */
  const commit = React.useCallback(
    (phaseId: string, column: ColumnState) => {
      const key = cellMapKey(phaseId, column.key);
      const draft = drafts.get(key);
      if (draft === undefined) return;

      const stored = storedCells.get(key) ?? '';
      const unchanged =
        (draft.trim() === '' && stored.trim() === '') ||
        (isQty(draft) && isQty(stored) && normaliseQty(draft) === normaliseQty(stored));
      if (unchanged) {
        dropDraft(key);
        return;
      }

      if (draft.trim() !== '' && !isQty(draft)) {
        toast.error(`"${draft}" is not a quantity.`);
        return;
      }
      if (!column.productId) return;
      if (demo) return;

      // "0" deletes the cell, per the contract, which is how a blank is written back.
      const qty = draft.trim() === '' ? '0' : draft.trim();
      saveCells.mutate(
        [{ phase_id: phaseId, product_id: column.productId, qty }],
        { onSuccess: () => dropDraft(key) },
      );
    },
    [demo, drafts, dropDraft, saveCells, storedCells],
  );

  /** A picked product is remembered for the customer's next schedule, and says so once. */
  const onResolveProduct = React.useCallback(
    (columnIndex: number, productId: string) => {
      const customerCode = columns.find((column) => column.index === columnIndex)?.customerCode;
      const remembered = () => {
        if (customerCode) {
          toast.success(
            `${customerCode} will resolve to this product on this customer's next schedule.`,
          );
        }
      };
      if (demo) {
        remembered();
        return;
      }
      resolveProduct.mutate({ productIndex: columnIndex, productId }, { onSuccess: remembered });
    },
    [columns, demo, resolveProduct],
  );

  const controller: ScheduleGridController = {
    columns: visibleColumns,
    totalsColumns: columns,
    phaseGroups,
    valueFor,
    setDraft,
    commit,
    canEdit,
    flagActions: {
      canEdit,
      poOptions,
      resolveProduct: onResolveProduct,
      fixQuantities: jumpAndFocusColumn,
      // The hook toasts a refusal; the dialog closes either way.
      dismiss: demo
        ? undefined
        : (columnIndex, reason) =>
            dismissColumn
              .mutateAsync({ columnIndex, dismissed: true, reason })
              .catch(() => undefined),
      undoDismiss: demo
        ? undefined
        : (columnIndex) => dismissColumn.mutate({ columnIndex, dismissed: false, reason: null }),
      dismissing: dismissColumn.isPending,
    },
    registerColumnRef,
    focusRequest,
    metaFor: (phaseId, columnKey) => cellMeta.get(cellMapKey(phaseId, columnKey)),
  };

  /**
   * The PO this schedule is checked against, from the meta line. The PO record rather than
   * the document confirm screen; the version screen the last resort. It opens in a new tab:
   * the reviewer is mid-reconciliation and leaving the page loses the cells they have typed.
   */
  const poHref = version?.purchase_order_id
    ? `/project-sales/${projectId}/pos/${version.purchase_order_id}`
    : poVersion.data?.purchase_order_id
      ? `/project-sales/${projectId}/pos/${poVersion.data.purchase_order_id}`
      : version?.po_version_id
        ? `/project-sales/${projectId}/purchase-orders/${version.po_version_id}`
        : null;

  const crumbs = [
    { title: 'Project Sales' },
    { title: 'Projects', path: '/project-sales/pipeline' },
    { title: project.data?.title ?? 'Project', path: `/project-sales/${projectId}` },
  ];

  if (view.isLoading) {
    return <ReviewSkeleton />;
  }

  if (view.isError || !version) {
    return (
      <div className="space-y-4">
        <PageHeader title="Delivery schedule" crumbs={crumbs} />
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
          <h2 className="text-sm font-semibold text-destructive">
            This schedule could not be loaded
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {view.error instanceof Error ? view.error.message : 'It may have been deleted.'}
          </p>
          <Button asChild variant="outline" className="mt-4">
            <Link href={`/project-sales/${projectId}?tab=schedules`}>
              Back to delivery schedules
            </Link>
          </Button>
        </div>
      </div>
    );
  }

  const readingNow = phase === 'queued' || phase === 'running';
  const title = version.po_number
    ? `Schedule ${version.po_number} v${version.version_no}`
    : `Schedule v${version.version_no}`;

  /** One primary button: Confirm schedule, or once confirmed, the amendment it left owing. */
  const primary = !version.confirmed_at ? (
    <Button
      type="button"
      disabled={!canEdit || readingNow || phase === 'failed' || columns.length === 0}
      onClick={() => setConfirming(true)}
    >
      Confirm schedule
    </Button>
  ) : version.amendment_preview_url ? (
    <Button asChild>
      <Link href={version.amendment_preview_url}>Review the amendment</Link>
    </Button>
  ) : null;

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            {title}
            <StatusPill version={version} phase={phase} />
          </span>
        }
        crumbs={crumbs}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <RecordNavigation
              index={scheduleIndex >= 0 ? scheduleIndex + 1 : null}
              total={scheduleRows.length}
              hasPrevious={scheduleIndex > 0}
              hasNext={scheduleIndex >= 0 && scheduleIndex < scheduleRows.length - 1}
              onPrevious={() => goToSchedule(scheduleRows[scheduleIndex - 1])}
              onNext={() => goToSchedule(scheduleRows[scheduleIndex + 1])}
              isLoading={schedules.isLoading}
              ariaLabel="schedule"
            />
            {primary}
          </div>
        }
      />
      <p className="text-sm text-muted-foreground" data-testid="schedule-meta">
        <MetaLine version={version} project={project.data} poHref={poHref} />
      </p>

      {readingNow && <POIntakeExtractionProgress version={version} />}

      {phase === 'failed' && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-8 text-center">
          <h2 className="text-sm font-semibold text-destructive">
            This document could not be read
          </h2>
          <p className="mx-auto mt-1 max-w-md break-words text-sm text-muted-foreground">
            {version.extraction_error ?? 'Nothing was extracted from the file.'}
          </p>
          {/* Reading it again leads: the commonest failure is a reader killed part-way, which
              says nothing about the scan. Re-uploading stays for when the document is it. */}
          <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
            {!demo && (
              <Button
                type="button"
                disabled={retryExtraction.isPending}
                onClick={() => void retryExtraction.mutateAsync().catch(() => undefined)}
              >
                <RefreshCw
                  className={`size-4 ${retryExtraction.isPending ? 'animate-spin' : ''}`}
                  aria-hidden
                />
                {retryExtraction.isPending ? 'Starting…' : 'Read it again'}
              </Button>
            )}
            <Button asChild variant="outline">
              <Link href={`/project-sales/${projectId}?tab=schedules`}>Upload it again</Link>
            </Button>
          </div>
        </div>
      )}

      {phase === 'partial' && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-[var(--color-warning-accent,var(--color-yellow-500))]/50 bg-[var(--color-warning-soft,var(--color-yellow-100))] px-3 py-2 text-sm font-medium dark:bg-[var(--color-warning-soft,var(--color-yellow-950))]">
          <AlertTriangle className="size-4 shrink-0" aria-hidden />
          <span className="break-words">
            {typeof version.pages_extracted === 'number' &&
            typeof version.page_count === 'number'
              ? `Only ${version.pages_extracted} of ${version.page_count} pages were read`
              : 'Some of this document was not read'}
          </span>
          {version.extraction_error && (
            <span className="break-words font-normal text-muted-foreground">
              {version.extraction_error}
            </span>
          )}
        </div>
      )}

      {!readingNow && phase !== 'failed' && (
        <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as ReviewTab)}>
          <TabsList variant="line" aria-label="Schedule sections">
            <TabsTrigger value="schedule">Schedule</TabsTrigger>
            <TabsTrigger value="documents">Documents</TabsTrigger>
          </TabsList>

          <TabsContent value="schedule" className="space-y-3">
            {columns.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
                <h2 className="text-sm font-semibold">No columns came out of this document</h2>
                <Button asChild variant="outline" className="mt-4">
                  <Link href={`/project-sales/${projectId}?tab=schedules`}>
                    Back to delivery schedules
                  </Link>
                </Button>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <ToggleGroup
                    type="single"
                    variant="outline"
                    value={viewMode}
                    onValueChange={(next) => next && setViewMode(next as 'phase' | 'date')}
                  >
                    <ToggleGroupItem value="phase" className={SEGMENT_CLASS}>
                      By area
                    </ToggleGroupItem>
                    <ToggleGroupItem value="date" className={SEGMENT_CLASS}>
                      By date
                    </ToggleGroupItem>
                  </ToggleGroup>
                  <ToggleGroup
                    type="single"
                    variant="outline"
                    value={effectiveFilter}
                    onValueChange={(next) => next && chooseFilter(next as RowFilter)}
                  >
                    <ToggleGroupItem value="attention" className={SEGMENT_CLASS}>
                      {`Need attention (${blocking.length})`}
                    </ToggleGroupItem>
                    <ToggleGroupItem value="all" className={SEGMENT_CLASS}>
                      {`All rows (${columns.length})`}
                    </ToggleGroupItem>
                  </ToggleGroup>
                  <Button
                    type="button"
                    variant="outline"
                    className="ms-auto"
                    onClick={() => setHistoryOpen(true)}
                  >
                    <History className="size-4" aria-hidden />
                    History
                  </Button>
                </div>

                {visibleColumns.length === 0 ? (
                  <div
                    data-testid="schedule-all-clear"
                    className="rounded-lg border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground"
                  >
                    Nothing needs attention
                  </div>
                ) : viewMode === 'phase' ? (
                  /* One grid, two shapes. The matrix needs room; a phone gets the cards. */
                  <>
                    <div className="hidden md:block">
                      <DeliveryScheduleMatrix controller={controller} />
                    </div>
                    <div className="md:hidden">
                      <DeliveryScheduleColumnCards controller={controller} />
                    </div>
                  </>
                ) : (
                  /* Read-only, on every width: the inputs live in By area. */
                  <DeliveryScheduleByDateMatrix
                    controller={controller}
                    dateColumns={dateColumnsData}
                  />
                )}
              </>
            )}
          </TabsContent>

          {/* The whole document and nothing under it (owner hand test on PR #1237, item 4).
              `dvh`, not `vh`: this tab is verified at 375px. */}
          <TabsContent value="documents" className="flex h-[calc(100dvh-14rem)] flex-col">
            {version.document_url ? (
              <POIntakeDocumentViewer
                documentUrl={version.document_url}
                documentKey={version.id}
                attachmentId={version.attachment_id}
                pageCount={version.page_count ?? null}
                page={documentPage}
                onPageChange={setDocumentPage}
                className="flex-1"
                documentLabel="Delivery schedule"
              />
            ) : (
              <DocumentEmptyState
                onReupload={canEdit && !demo ? () => setUploading(true) : undefined}
              />
            )}
          </TabsContent>
        </Tabs>
      )}

      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent className="w-full gap-0 p-0 sm:max-w-xl">
          <SheetHeader className="border-b border-border px-5 py-4">
            <SheetTitle>History</SheetTitle>
          </SheetHeader>
          <SheetBody
            className="space-y-4 overflow-y-auto px-5 py-4"
            data-testid="schedule-history"
          >
            {version.version_no > 1 ? (
              <DeliveryScheduleRevisionDiff
                version={version}
                priorVersion={priorVersion.data}
                priorLoading={priorVersion.isLoading}
              />
            ) : (
              <Card>
                <CardContent className="space-y-1 py-4 text-sm">
                  <p className="font-medium">Changes since the previous version</p>
                  <p className="text-muted-foreground">-</p>
                </CardContent>
              </Card>
            )}
            <DeliveryScheduleRevisionProposals
              proposals={version.revision_proposals ?? []}
              canDecide={canEdit}
              pendingIndex={pendingProposalIndex}
              onAccept={(index) => {
                if (demo) return;
                setPendingProposalIndex(index);
                acceptProposal.mutate(index, {
                  onSettled: () => setPendingProposalIndex(null),
                });
              }}
              onReject={(index) => {
                if (demo) return;
                setPendingProposalIndex(index);
                rejectProposal.mutate(index, {
                  onSettled: () => setPendingProposalIndex(null),
                });
              }}
            />
            <DeliveryScheduleNotes notes={version.notes ?? []} />
          </SheetBody>
        </SheetContent>
      </Sheet>

      <DeliveryScheduleConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        blocking={blocking}
        partialRead={phase === 'partial'}
        pending={confirm.isPending}
        onConfirm={async (body: DeliveryScheduleConfirmBody) => {
          try {
            await confirm.mutateAsync(body);
            setConfirming(false);
            // S4: Confirm schedule returns the user to where they came from. With no origin
            // (a deep link or a bookmark) it stays on the page.
            if (originHref) router.push(originHref);
          } catch {
            // The mutation hook already surfaced the message; keep the dialog open so the
            // reviewer can acknowledge and try again without losing what they typed.
          }
        }}
      />

      {uploading && project.data && (
        <DeliveryScheduleUploadDialog
          project={project.data}
          schedules={scheduleRows}
          onDone={() => setUploading(false)}
        />
      )}
    </div>
  );
}

/** The header's one status pill: what stage of reading and confirming it is at. */
function StatusPill({
  version,
  phase,
}: {
  version: DeliveryScheduleVersion;
  phase: ReturnType<typeof resolveExtractionPhase>;
}) {
  if (phase === 'queued') return <Badge variant="secondary">Waiting to be read</Badge>;
  if (phase === 'running') return <Badge variant="secondary">Being read</Badge>;
  if (phase === 'failed') return <Badge variant="destructive">Could not be read</Badge>;
  return version.confirmed_at ? (
    <Badge variant="success">Confirmed</Badge>
  ) : (
    <Badge variant="warning">To confirm</Badge>
  );
}

/** Project, date, which PO it was checked against, and who confirmed it: `-` where unknown. */
function MetaLine({
  version,
  project,
  poHref,
}: {
  version: DeliveryScheduleVersion;
  project: { title: string; project_code?: string | null } | undefined;
  poHref: string | null;
}) {
  const projectLabel = project
    ? project.project_code
      ? `${project.title} (${project.project_code})`
      : project.title
    : '-';
  const checkedAgainst =
    version.po_version_no !== null && version.po_version_no !== undefined
      ? `Checked against PO v${version.po_version_no}`
      : null;
  return (
    <>
      {projectLabel}
      {` · Dated ${version.schedule_date ? formatDateInMalaysia(version.schedule_date) : '-'}`}
      {checkedAgainst && (
        <>
          {' · '}
          {poHref ? (
            <a
              href={poHref}
              target="_blank"
              rel="noopener noreferrer"
              className="underline-offset-4 hover:underline"
            >
              {checkedAgainst}
            </a>
          ) : (
            checkedAgainst
          )}
        </>
      )}
      {version.confirmed_at &&
        ` · Confirmed ${formatDateTimeInMalaysia(version.confirmed_at)}${
          version.confirmed_by_name ? ` by ${version.confirmed_by_name}` : ''
        }`}
    </>
  );
}

/** R13: a missing file is a plain empty state, never an error code. */
function DocumentEmptyState({ onReupload }: { onReupload?: () => void }) {
  return (
    <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
      <h3 className="text-sm font-semibold">This PDF is not available yet</h3>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
        The source file has not finished uploading, or could not be found.
      </p>
      {onReupload && (
        <Button type="button" className="mt-4" onClick={onReupload}>
          Upload the schedule again
        </Button>
      )}
    </div>
  );
}

function ReviewSkeleton() {
  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 space-y-2">
          <Skeleton className="h-7 w-72" />
          <Skeleton className="h-4 w-96" />
        </div>
        <Skeleton className="h-8 w-40" />
      </div>
      <Skeleton className="h-9 w-56" />
      <Skeleton className="h-80 w-full" />
    </div>
  );
}
