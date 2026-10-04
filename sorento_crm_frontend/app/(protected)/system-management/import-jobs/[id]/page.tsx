'use client';

import { use, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { MoveLeft, ChevronLeft, ChevronRight, Download } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  getImportJob,
  getImportJobs,
  getImportJobSourceUrl,
} from '../services/importJobService';
import { useCancelImportJob, useImportJobStatus } from '../hooks/useImportJobs';
import { OutcomeBreakdownCard } from '../components/OutcomeBreakdownCard';
import { ImportJobRowsCard } from '../components/ImportJobRowsCard';
import { PlanningChangeOutcomeCard } from '../components/PlanningChangeOutcomeCard';
import { toast } from '@/lib/toast';
import type { OutstandingPlanningChangeBatch } from '../../../scm/reorder/services/outstandingImportService';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { AutocountPullReview } from '../autocount-pull/components/AutocountPullReview';
import { usePull } from '../autocount-pull/hooks/useAutocountPull';

/** The pull job types get the review header/counters/tabs above the usual cards
 *  (PLAN-autocount-pull-review.md; delivery orders joined in
 *  PLAN-autocount-do-pull-crm-30sep.md) - every other job type renders exactly as before. */
const AUTOCOUNT_PULL_JOB_TYPES = new Set([
  'autocount_products_pull',
  'autocount_stock_pull',
  'autocount_delivery_orders_pull',
  'autocount_grn_pull',
]);

type PullEntity = 'products' | 'stock_balances' | 'delivery_orders' | 'goods_receive_notes';

/** AC-DS-12: the entity a pull job's OWN `job_type` names, for the main header's Back
 *  button - a fallback for the (rare) render where `pullStatus` has not loaded yet, so
 *  the pull's own metadata `entity` is the preferred source wherever it is available. */
function pullEntityFromJobType(jobType: string): PullEntity | null {
  if (jobType === 'autocount_products_pull') return 'products';
  if (jobType === 'autocount_stock_pull') return 'stock_balances';
  if (jobType === 'autocount_delivery_orders_pull') return 'delivery_orders';
  if (jobType === 'autocount_grn_pull') return 'goods_receive_notes';
  return null;
}

/** Where a pull job's Back button goes: the list it was pulled FROM (AC-DS-12). */
const PULL_BACK: Record<PullEntity, { label: string; href: string }> = {
  products: { label: 'Back to Products', href: '/master-data-management/products' },
  stock_balances: { label: 'Back to Stock', href: '/inventory-management/stock' },
  delivery_orders: { label: 'Back to Delivery Orders', href: '/order-management/orders' },
  goods_receive_notes: { label: 'Back to Goods Receipt Notes', href: '/procurement-management/grn' },
};

const JOB_TYPE_LABELS: Record<string, string> = {
  order_import: 'Order Import',
  order_tracking_import: 'Order Tracking Import',
  product_import: 'Product Import',
  stock_import: 'Stock Import',
  spo_import: 'SPO Import',
  attachment_bulk_import: 'Attachment Bulk Import',
  grn_listing_import: 'Upload GRN',
  grn_lines_import: 'Upload GRN Lines',
  customer_import: 'Customer Import',
  outstanding_so_import: 'Outstanding Sales Orders Import',
  outstanding_po_import: 'Outstanding Purchase Orders Import',
  po_history_import: 'Purchase History Import',
  sales_history_import: 'Sales History Import',
  order_inquiry_import: 'Order Inquiry Import',
  autocount_products_pull: 'AutoCount Products Pull',
  autocount_stock_pull: 'AutoCount Stock Pull',
  autocount_delivery_orders_pull: 'AutoCount Delivery Orders Pull',
  autocount_products_apply: 'AutoCount Products Apply',
  autocount_stock_apply: 'AutoCount Stock Apply',
  autocount_delivery_orders_apply: 'AutoCount Delivery Orders Apply',
  autocount_grn_pull: 'AutoCount GRN Pull',
  autocount_grn_apply: 'AutoCount GRN Apply',
};

function getJobTypeLabel(jobType: string): string {
  return JOB_TYPE_LABELS[jobType] ?? jobType;
}

/**
 * `result.upload.planning_change_batch` (`_run_scm_upload_job` nests the outstanding
 * channel's own answer under `upload`) - `null` on every job that never raised one, which is
 * most of them.
 */
function planningChangeBatchOf(
  result: unknown,
): OutstandingPlanningChangeBatch | null {
  const upload = (result as { upload?: { planning_change_batch?: unknown } } | null | undefined)
    ?.upload;
  const batch = upload?.planning_change_batch as OutstandingPlanningChangeBatch | null | undefined;
  return batch ?? null;
}

/** A finished AutoCount Delivery Orders apply's own counts (`metadata.autocount_apply.counts`,
 *  written by `_apply_delivery_orders`) - `null` for every other job, and for a DO apply
 *  still running. */
type DeliveryOrdersApplyCounts = {
  created: number;
  adopted: number;
  updated: number;
  unchanged: number;
  failed: number;
  retryable: number;
  lines_deleted: number;
};

function deliveryOrdersApplyCountsOf(job: {
  job_type: string;
  job_metadata?: unknown;
}): DeliveryOrdersApplyCounts | null {
  // The GRN apply writes the same document counts (`_apply_goods_receive_notes`).
  if (job.job_type !== 'autocount_delivery_orders_apply' && job.job_type !== 'autocount_grn_apply') {
    return null;
  }
  const meta = job.job_metadata as
    | { autocount_apply?: { counts?: DeliveryOrdersApplyCounts } }
    | null
    | undefined;
  return meta?.autocount_apply?.counts ?? null;
}

const DO_APPLY_COUNT_FIELDS: { key: keyof DeliveryOrdersApplyCounts; label: string; tone?: string }[] = [
  { key: 'created', label: 'Created', tone: 'text-emerald-600' },
  { key: 'adopted', label: 'Adopted', tone: 'text-emerald-600' },
  { key: 'updated', label: 'Updated', tone: 'text-emerald-600' },
  { key: 'unchanged', label: 'Unchanged', tone: 'text-yellow-600' },
  { key: 'failed', label: 'Failed', tone: 'text-red-600' },
  { key: 'retryable', label: 'Retryable', tone: 'text-red-600' },
  { key: 'lines_deleted', label: 'Lines deleted' },
];

const TERMINAL_STATUSES = ['finished', 'failed', 'cancelled'];

type ImportJobDetailPageProps = {
  params: Promise<{ id: string }>;
};

export default function ImportJobDetailPage({ params }: ImportJobDetailPageProps) {
  const { id } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const pageParam = searchParams.get('page');
  const pageSizeParam = searchParams.get('pageSize');
  const pageIndex = pageParam ? Math.max(0, parseInt(pageParam, 10) - 1) : null;
  const pageSize = pageSizeParam ? Math.max(1, parseInt(pageSizeParam, 10)) : 50;
  // Row-grid filters live here so clicking a reason in the breakdown can drive the grid.
  const [rowsCodeFilter, setRowsCodeFilter] = useState('');
  const [rowsOutcomeFilter, setRowsOutcomeFilter] = useState('');

  const { data: job, isLoading } = useQuery({
    queryKey: ['import-job', id],
    queryFn: () => getImportJob(id),
    staleTime: 1000 * 30,
    refetchOnWindowFocus: true,
    retry: 1,
  });

  const { data: jobsListData } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['import-jobs', pageIndex ?? 0, pageSize],
    queryFn: () => getImportJobs({ pageIndex: pageIndex ?? 0, pageSize, sorting: [] }),
    enabled: pageIndex !== null && !!job,
  });

  const jobIds = jobsListData?.data?.map((j) => j.job_id) ?? [];
  const currentIndex = jobIds.indexOf(id);
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < jobIds.length - 1;
  const prevId = hasPrev ? jobIds[currentIndex - 1] : null;
  const nextId = hasNext ? jobIds[currentIndex + 1] : null;
  const totalOnPage = jobIds.length;
  const showPagination = pageIndex !== null && totalOnPage > 0 && currentIndex >= 0;

  const isPullJob = Boolean(job && AUTOCOUNT_PULL_JOB_TYPES.has(job.job_type));
  // E2 (small-fix track, fix round 2): the list's row click lands on the URL with the RQ
  // job id (`import_jobs.job_id`), which the generic job endpoints below accept alongside
  // the DB id - but `/api/v1/autocount/pulls/{id}` only knows `import_jobs.id`. `job.id` is
  // always that DB id once the job has loaded, so every pull-shaped call goes through it
  // instead of the raw URL param.
  const pullJobId = job?.id ?? '';
  const { data: pullStatus } = usePull(pullJobId, isPullJob);
  // The pull's own review component polls itself every 10s (AC-BD-6) - the generic 2s
  // job-progress poll below has nothing to show while FoundryX is still building (no RQ job
  // exists yet for that phase), so it stays off for as long as that is true.
  const pullStillBuilding = isPullJob && pullStatus?.phase === 'building';
  // D2 (small-fix track, browser e2e run 3): a pull job's OWN `job.result` is never the
  // per-row envelope the generic Results / Outcome breakdown / Rows cards below expect - it
  // is either stale (whatever the last apply on this job row left, if any) or entirely
  // absent, so those three cards printed copy meant for pre-row-capture jobs and numbers
  // that contradicted the pull card's own counters, in EVERY phase, not only Building /
  // Preparing (B2's narrower guard). The pull's own Changes tab already wraps the same rows
  // card once there is something to show (AC-RV-2); Job Summary is the only generic card a
  // pull job keeps.

  // Poll for status updates if job is still processing
  const { data: statusData } = useImportJobStatus(id, !isLoading && !!job && !pullStillBuilding);
  const cancelJobMutation = useCancelImportJob();
  const queryClient = useQueryClient();

  // The job query is only refetched on focus, so a job that finishes while the page is open
  // kept its running copy (no final counts, no metadata), and the rows card kept the empty
  // page it read mid-run. Re-read both once the 2s status poll reports a terminal status the
  // loaded job does not have yet.
  const polledStatus = statusData?.status;
  const loadedStatus = job?.status;
  useEffect(() => {
    if (
      polledStatus &&
      loadedStatus &&
      polledStatus !== loadedStatus &&
      TERMINAL_STATUSES.includes(polledStatus)
    ) {
      queryClient.invalidateQueries({ queryKey: ['import-job', id] });
      queryClient.invalidateQueries({ queryKey: ['import-job-rows', id] });
    }
  }, [polledStatus, loadedStatus, id, queryClient]);

  if (isLoading) {
    return (
      <>
        <Container>
          <PageHeader
            title="Import Job"
            actions={
              <Button asChild variant="outline">
                <Link href="/system-management/import-jobs">
                  <MoveLeft /> Back to Import Jobs
                </Link>
              </Button>
            }
          />
        </Container>
        <Container>
          <div className="space-y-6">
            <Skeleton className="h-10 w-64" />
            <Skeleton className="h-96 w-full" />
          </div>
        </Container>
      </>
    );
  }

  if (!job) {
    return (
      <>
        <Container>
          <PageHeader
            title="Import Job"
            actions={
              <Button asChild variant="outline">
                <Link href="/system-management/import-jobs">
                  <MoveLeft /> Back to Import Jobs
                </Link>
              </Button>
            }
          />
        </Container>
        <Container>
          <div className="text-center py-12">
            <p className="text-muted-foreground">Import job not found</p>
            <Button variant="outline" onClick={() => router.push('/system-management/import-jobs')} className="mt-4">
              Back to Import Jobs
            </Button>
          </div>
        </Container>
      </>
    );
  }

  const progress = statusData?.progress;
  const progressPercentage = progress ? progress.percentage : (job.total_rows > 0 ? Math.round((job.processed_rows / job.total_rows) * 100) : 0);
  const currentStatus = statusData?.status || job.status;
  const canCancel = ['pending', 'queued', 'started'].includes(currentStatus);

  // Use statusData.progress when available (polled every 2s) so Processed/Success/Failed/Skipped stay in sync
  const displayProcessed = progress ? progress.processed : job.processed_rows;
  const displaySuccessful = progress ? progress.successful : job.successful_rows;
  const displayFailed = progress ? progress.failed : job.failed_rows;
  const displaySkipped = progress ? progress.skipped : job.skipped_rows;
  // The polled total too: the job query's own copy is the one read at page load.
  const displayTotal = progress ? progress.total : job.total_rows;
  const doApplyCounts = deliveryOrdersApplyCountsOf(job);
  const planningChangeBatch = planningChangeBatchOf(job.result);

  // AC-DS-12/13: only the MAIN header's Back button - the loading / not-found ones above
  // stay "Back to Import Jobs" always. A `page` param means the caller came from the
  // Import Jobs list, so that trail wins whatever the job type; otherwise a pull job goes
  // back to wherever it was pulled FROM (Products / Stock), never the generic list.
  // N4 (Phase 3 review): `job.job_type` alone, never `pullStatus?.entity` - the pull's own
  // `entity` is set once at `start_pull` from the SAME job type this derives from, so the
  // two can never disagree, and reading it straight off `job` needs no extra `usePull`
  // fetch to have resolved first.
  const pullEntity = isPullJob ? pullEntityFromJobType(job.job_type) : null;
  const backToOwnList = isPullJob && pageIndex === null && pullEntity ? PULL_BACK[pullEntity] : null;
  const backLabel = backToOwnList ? backToOwnList.label : 'Back to Import Jobs';
  const backHref = backToOwnList
    ? backToOwnList.href
    : pageIndex !== null
      ? `/system-management/import-jobs?page=${pageIndex + 1}&pageSize=${pageSize}`
      : '/system-management/import-jobs';

  return (
    <>
      <Container>
        <PageHeader
          title="Import Job Details"
          actions={
            <>
              {showPagination && (
                <div className="flex items-center gap-1 mr-2">
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-8 w-8"
                    disabled={!hasPrev}
                    asChild={!!prevId}
                    aria-label="Previous import job"
                  >
                    {prevId ? (
                      <Link href={`/system-management/import-jobs/${prevId}?page=${(pageIndex ?? 0) + 1}&pageSize=${pageSize}`}>
                        <ChevronLeft className="size-4" />
                      </Link>
                    ) : (
                      <span><ChevronLeft className="size-4" /></span>
                    )}
                  </Button>
                  <span className="text-sm text-muted-foreground tabular-nums px-1 min-w-[4rem] text-center">
                    {currentIndex + 1} / {totalOnPage}
                  </span>
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-8 w-8"
                    disabled={!hasNext}
                    asChild={!!nextId}
                    aria-label="Next import job"
                  >
                    {nextId ? (
                      <Link href={`/system-management/import-jobs/${nextId}?page=${(pageIndex ?? 0) + 1}&pageSize=${pageSize}`}>
                        <ChevronRight className="size-4" />
                      </Link>
                    ) : (
                      <span><ChevronRight className="size-4" /></span>
                    )}
                  </Button>
                </div>
              )}
              <Button asChild variant="outline">
                <Link href={backHref}>
                  <MoveLeft /> {backLabel}
                </Link>
              </Button>
              {canCancel && (
                <Button
                  variant="outline"
                  disabled={cancelJobMutation.isPending}
                  onClick={() => {
                    cancelJobMutation.mutate(id, {
                      onSuccess: (data) => {
                        toast.success(data.message || 'Job cancelled');
                      },
                      onError: (error) => {
                        toast.error(error instanceof Error ? error.message : 'Failed to cancel job');
                      },
                    });
                  }}
                >
                  {cancelJobMutation.isPending ? 'Cancelling...' : 'Cancel Job'}
                </Button>
              )}
            </>
          }
        />
      </Container>

      <Container>
        <div className="space-y-6">
          {/* AutoCount pull review - header pill, counters, Changes / Excel view / Compare
              tabs, Download and Confirm. Renders above the usual cards; every other job
              type is untouched. */}
          {isPullJob && <AutocountPullReview jobId={pullJobId} />}

          {/* Summary Card */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Job Summary</CardTitle>
                <Badge status={statusData?.status || job.status}>
                  {(statusData?.status || job.status || '').toUpperCase()}
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 text-sm">
                <div>
                  <p className="text-muted-foreground">Job Type</p>
                  <p className="font-medium">{getJobTypeLabel(job.job_type)}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Status</p>
                  <p className="font-medium">
                    <Badge status={statusData?.status || job.status}>
                      {(statusData?.status || job.status || '').toUpperCase()}
                    </Badge>
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Total Rows</p>
                  <p className="font-medium text-lg">{displayTotal}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Processed</p>
                  <p className="font-medium text-lg">{displayProcessed} / {displayTotal}</p>
                </div>
                {job.filename && (
                  <div>
                    <p className="text-muted-foreground">Filename</p>
                    <p className="font-medium">{job.filename}</p>
                  </div>
                )}
                {job.has_source_file && (
                  <div>
                    <p className="text-muted-foreground">Source File</p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-1"
                      onClick={async () => {
                        try {
                          const { url } = await getImportJobSourceUrl(id);
                          window.open(url, '_blank', 'noopener,noreferrer');
                        } catch {
                          toast.error('Could not open the original uploaded file.');
                        }
                      }}
                    >
                      <Download className="size-4" />
                      Download original
                    </Button>
                  </div>
                )}
                <div>
                  <p className="text-muted-foreground">Created At</p>
                  <p className="font-medium">{formatDateTimeInMalaysia(job.created_at)}</p>
                </div>
                {job.started_at && (
                  <div>
                    <p className="text-muted-foreground">Started At</p>
                    <p className="font-medium">{formatDateTimeInMalaysia(job.started_at)}</p>
                  </div>
                )}
                {job.completed_at && (
                  <div>
                    <p className="text-muted-foreground">Completed At</p>
                    <p className="font-medium">{formatDateTimeInMalaysia(job.completed_at)}</p>
                  </div>
                )}
              </div>

              {/* Progress Bar - show when we have total from progress (polled) or job */}
              {(progress?.total ?? job.total_rows) > 0 && ['pending', 'queued', 'started'].includes(currentStatus) && (
                <div className="mt-6 space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Progress</span>
                    <span className="font-medium">{progressPercentage}%</span>
                  </div>
                  <Progress value={progressPercentage} />
                </div>
              )}
            </CardContent>
          </Card>

          {(statusData?.error || job.error) && (
            <Card>
              <CardHeader>
                <CardTitle>Error</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-destructive whitespace-pre-wrap">
                  {statusData?.error || job.error}
                </p>
              </CardContent>
            </Card>
          )}

          {/* Results Card - D2: never for a pull job, any phase (see the note above
              `isPullJob`'s own definition). */}
          {!isPullJob && (
            <Card>
              <CardHeader>
                <CardTitle>Results</CardTitle>
              </CardHeader>
              <CardContent>
                {doApplyCounts ? (
                  <div className="grid gap-4 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 text-sm">
                    {DO_APPLY_COUNT_FIELDS.map(({ key, label, tone }) => (
                      <div key={key}>
                        <p className="text-muted-foreground">{label}</p>
                        <p className={`font-medium text-lg ${tone ?? ''}`}>{doApplyCounts[key] ?? 0}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 text-sm">
                  <div>
                    <p className="text-muted-foreground">Successful</p>
                    <p className="font-medium text-lg text-emerald-600">{displaySuccessful}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Failed</p>
                    <p className="font-medium text-lg text-red-600">{displayFailed}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Skipped</p>
                    <p className="font-medium text-lg text-yellow-600">{displaySkipped}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Processed</p>
                    <p className="font-medium text-lg">{displayProcessed}</p>
                  </div>
                </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* The planned-line reaction this SO book upload raised, once the worker wrote it. */}
          {planningChangeBatch && <PlanningChangeOutcomeCard batch={planningChangeBatch} />}

          {/* Outcome breakdown - every reason, exact counts, never truncated. D2: same guard
              as Results above. */}
          {!isPullJob && (
            <OutcomeBreakdownCard
              result={job.result}
              activeCode={rowsCodeFilter}
              onSelectCode={(code, group) => {
                setRowsCodeFilter(code === rowsCodeFilter ? '' : code);
                const outcomeForGroup =
                  group === 'successful' ? '' : group === 'skipped' ? 'skipped' : 'failed';
                setRowsOutcomeFilter(code === rowsCodeFilter ? '' : outcomeForGroup);
              }}
            />
          )}

          {/* Per-row drill-down - D2: same guard as Results above. */}
          {!isPullJob && (
            <ImportJobRowsCard
              jobId={id}
              result={job.result}
              codeFilter={rowsCodeFilter}
              outcomeFilter={rowsOutcomeFilter}
              onChangeCode={setRowsCodeFilter}
              onChangeOutcome={setRowsOutcomeFilter}
            />
          )}

          {/* Error Card */}
          {job.error && (
            <Card>
              <CardHeader>
                <CardTitle className="text-red-600">Error</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-red-700 dark:text-red-400">{job.error}</p>
              </CardContent>
            </Card>
          )}

          {/* Result Details (if available) */}
          {job.result && typeof job.result === 'object' && Object.keys(job.result).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Result Details</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {(typeof job.result.allocations_created === 'number' ||
                  typeof job.result.allocations_updated === 'number') && (
                  <div className="flex flex-wrap gap-2 text-sm">
                    {typeof job.result.allocations_created === 'number' && (
                      <span className="rounded bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-400 px-2 py-1">
                        Allocations created: {job.result.allocations_created}
                      </span>
                    )}
                    {typeof job.result.allocations_updated === 'number' && (
                      <span className="rounded bg-sky-50 dark:bg-sky-950/30 text-sky-700 dark:text-sky-400 px-2 py-1">
                        Allocations updated: {job.result.allocations_updated}
                      </span>
                    )}
                  </div>
                )}
                {/* G12 (`PLAN-scm-reorder-oi-feedback-1sep.md` S6, AC-6.11): open
                    project-bin PO/SPO lines no claim names, company-wide as of this
                    upload's own claim-resolution pass - what Joey backfills
                    FromSODocList in AutoCount against. */}
                {typeof job.result.unclaimed_project_bin_lines === 'number' && (
                  <div className="flex flex-wrap gap-2 text-sm">
                    <span
                      className={
                        job.result.unclaimed_project_bin_lines > 0
                          ? 'rounded bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-400 px-2 py-1'
                          : 'rounded bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-400 px-2 py-1'
                      }
                    >
                      Unclaimed project-bin lines: {job.result.unclaimed_project_bin_lines}
                    </span>
                  </div>
                )}
                {Array.isArray(job.result.successful_rows_detail) && job.result.successful_rows_detail.length > 0 && (
                  <div>
                    <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400 mb-2">
                      Successful lines ({job.result.successful_rows_detail.length})
                    </p>
                    <ul className="text-sm space-y-1 max-h-60 overflow-y-auto bg-emerald-50 dark:bg-emerald-950/30 p-3 rounded">
                      {job.result.successful_rows_detail.map(
                        (
                          entry: {
                            grn_number?: string;
                            product_code?: string;
                            warehouse?: string;
                            quantity?: number;
                          },
                          i: number
                        ) => (
                          <li key={i} className="flex gap-2 flex-wrap">
                            <span className="font-mono shrink-0">
                              {entry.grn_number ?? '-'}
                            </span>
                            <span className="text-muted-foreground">·</span>
                            <span>{entry.product_code ?? '-'}</span>
                            <span className="text-muted-foreground">·</span>
                            <span>{entry.warehouse ?? '-'}</span>
                            <span className="text-muted-foreground">·</span>
                            <span>Qty {entry.quantity ?? '-'}</span>
                          </li>
                        )
                      )}
                    </ul>
                  </div>
                )}
                {Array.isArray(job.result.warnings) && job.result.warnings.length > 0 && (
                  <div>
                    <p className="text-sm font-medium text-amber-600 dark:text-amber-500 mb-2">
                      Warnings ({job.result.warnings.length})
                    </p>
                    <ul className="text-sm space-y-1 max-h-60 overflow-y-auto bg-muted/50 p-3 rounded list-disc pl-6 text-amber-600 dark:text-amber-500">
                      {/* Warnings are plain strings for most importers, but the
                          order-tracking import emits {row, warning, data} objects - 
                          rendering an object as a React child crashes the page. */}
                      {(job.result.warnings as unknown[]).map((w, i: number) => {
                        if (typeof w === 'string') return <li key={i}>{w}</li>;
                        const obj = (w ?? {}) as { row?: number; warning?: string };
                        const text =
                          obj.warning ??
                          (typeof w === 'object' ? JSON.stringify(w) : String(w));
                        return (
                          <li key={i}>
                            {obj.row != null ? `Row ${obj.row}: ` : ''}
                            {text}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
                {Array.isArray(job.result.skipped_rows_detail) && job.result.skipped_rows_detail.length > 0 && (
                  <div>
                    <p className="text-sm font-medium text-muted-foreground mb-2">
                      Skipped rows ({job.result.skipped_rows_detail.length})
                    </p>
                    <ul className="text-sm space-y-1 max-h-60 overflow-y-auto bg-muted/50 p-3 rounded">
                      {job.result.skipped_rows_detail.map((entry: { row?: number; reason?: string }, i: number) => (
                        <li key={i} className="flex gap-2">
                          <span className="font-mono text-muted-foreground shrink-0">Row {entry.row ?? i + 1}:</span>
                          <span>{entry.reason ?? 'Unknown'}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <details className="text-sm">
                  <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Full result (JSON)</summary>
                  <pre className="text-xs bg-muted p-4 rounded overflow-auto mt-2">
                    {JSON.stringify(job.result, null, 2)}
                  </pre>
                </details>
              </CardContent>
            </Card>
          )}
        </div>
      </Container>
    </>
  );
}
