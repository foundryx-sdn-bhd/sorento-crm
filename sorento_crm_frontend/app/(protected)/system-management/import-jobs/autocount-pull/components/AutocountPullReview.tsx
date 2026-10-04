'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  useConfirmPull,
  useDiscardPull,
  useDownloadPullXlsx,
  usePull,
  useRefreshRowsOnReview,
  useStartPull,
} from '../hooks/useAutocountPull';
import { startPullErrorMessage } from '../services/autocountPullService';
import { isCompareFullMatch } from '../types/compareMatch';
import { PullChangesTab } from './PullChangesTab';
import { PullExcelViewTab } from './PullExcelViewTab';
import { PullCompareTab } from './PullCompareTab';
import {
  isDocumentEntity,
  type AutocountPull,
  type AutocountPullEntity,
  type AutocountPullPhase,
  type AutocountPullScope,
  type DeliveryOrderPullCounts,
  type GoodsReceiveNotePullCounts,
  type ProductPullCounts,
  type StockPullCounts,
} from '../types/autocountPull.types';

const PHASE_LABEL: Record<AutocountPullPhase, string> = {
  building: 'Building',
  previewing: 'Preparing',
  review: 'Awaiting confirm',
  confirmed: 'Confirmed',
  failed: 'Failed',
  expired: 'Expired',
  discarded: 'Discarded',
};

const PHASE_VARIANT: Record<
  AutocountPullPhase,
  'info' | 'warning' | 'success' | 'destructive' | 'secondary'
> = {
  building: 'info',
  previewing: 'info',
  review: 'warning',
  confirmed: 'success',
  failed: 'destructive',
  expired: 'destructive',
  discarded: 'secondary',
};

const ENTITY_LABEL: Record<AutocountPullEntity, string> = {
  products: 'AutoCount products pull',
  stock_balances: 'AutoCount stock pull',
  delivery_orders: 'AutoCount delivery orders pull',
  goods_receive_notes: 'AutoCount goods receipt notes pull',
};

/** Known `pull.warnings` codes only - an unrecognised code renders nothing rather than a
 * raw slug (captain ruling, SR4 fix round). */
const WARNING_LABEL: Record<string, string> = {
  stock_list_not_archived: 'Stock List file was not replaced',
  content_hash_mismatch: 'Snapshot checksum did not match',
};

/** dd/MM/yyyy HH:mm, Malaysia time - the one place this page states a snapshot time. */
function formatSnapshotTime(iso?: string | null): string {
  if (!iso) return '-';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  const parts = new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Kuala_Lumpur',
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('day')}/${get('month')}/${get('year')} ${get('hour')}:${get('minute')}`;
}

function productCounters(counts: ProductPullCounts) {
  return [
    { label: 'Received', value: counts.received },
    { label: 'New', value: counts.new },
    { label: 'Changed', value: counts.changed },
    { label: 'Unchanged', value: counts.unchanged },
    { label: 'Failed', value: counts.failed },
    { label: 'Price to 0', value: counts.price_to_zero },
    { label: 'Left out by AutoCount', value: counts.left_out },
  ];
}

function stockCounters(counts: StockPullCounts) {
  return [
    { label: 'Received', value: counts.received },
    { label: 'Will apply', value: counts.fed },
    { label: 'Not applied, inactive warehouse', value: counts.not_applied_inactive },
    { label: 'Not applied, unknown location', value: counts.not_applied_unknown },
    { label: 'Quantity changes', value: counts.qty_changes },
    { label: 'Set to 0', value: counts.set_to_zero },
    { label: 'Skipped, product not found', value: counts.skipped_product_not_found },
    { label: 'Negative in AutoCount', value: counts.negative_in_autocount },
  ];
}

function deliveryOrderCounters(counts: DeliveryOrderPullCounts) {
  return [
    { label: 'Received', value: counts.received },
    { label: 'New', value: counts.created },
    { label: 'Updated', value: counts.updated },
    { label: 'Adopted by number', value: counts.adopted },
    { label: 'Unchanged', value: counts.unchanged },
    { label: 'Lines to delete', value: counts.lines_to_delete },
    { label: 'Failed', value: counts.failed },
    { label: 'Retry later', value: counts.retryable },
    { label: 'With warnings', value: counts.with_warnings },
  ];
}

/** The DO counters plus the GRN lines the preview linked to a PO / SPO line and the ones it
 *  could not (AC-GP-13). */
function goodsReceiveNoteCounters(counts: GoodsReceiveNotePullCounts) {
  return [
    ...deliveryOrderCounters(counts),
    { label: 'Lines linked to PO / SPO', value: counts.lines_linked },
    { label: 'Lines not linked', value: counts.lines_unlinked },
  ];
}

/** dd/MM/yyyy for a `YYYY-MM-DD` scope day - text, never a Date (a day has no zone). */
function formatScopeDay(day: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : day;
}

/** What a delivery-orders snapshot was asked to cover (DO-PULL-SS contract): one document,
 *  a day window, or the gateway's own default of the last 31 days - printed with the actual
 *  days once the snapshot is read (`window`, the same days the compare cuts the files to). */
function scopeLine(
  scope: AutocountPullScope | null | undefined,
  window?: { fromDay: string | null; toDay: string | null } | null,
  docLabel = 'DO',
): string {
  if (scope?.docNo) return `Scope: ${docLabel} ${scope.docNo}`;
  const fromDay = scope?.fromDay || window?.fromDay;
  const toDay = scope?.toDay || window?.toDay;
  if (fromDay || toDay) {
    const from = fromDay ? formatScopeDay(fromDay) : 'start';
    const to = toDay ? formatScopeDay(toDay) : 'today';
    const suffix = scope?.fromDay || scope?.toDay ? '' : ' (last 31 days)';
    return `Scope: ${from} to ${to}${suffix}`;
  }
  return 'Scope: Last 31 days';
}

function compareSummaryLine(pull: AutocountPull): string | null {
  const compare = pull.compare;
  if (!compare) return null;
  if (isCompareFullMatch(compare)) {
    return `100% match. ${compare.matched} of ${compare.total} items agree with ${compare.filename}.`;
  }
  const parts = [`${compare.matched} of ${compare.total} match`];
  if (compare.different) parts.push(`${compare.different} differ`);
  if (compare.only_in_excel) parts.push(`${compare.only_in_excel} only in your Excel`);
  if (compare.only_in_pull) parts.push(`${compare.only_in_pull} only in AutoCount`);
  return `${parts.join(', ')} against ${compare.filename}.`;
}

export interface AutocountPullReviewProps {
  jobId: string;
}

export function AutocountPullReview({ jobId }: AutocountPullReviewProps) {
  const router = useRouter();
  const [tab, setTab] = useState('changes');
  const { data: pull, isLoading } = usePull(jobId);
  const downloadMutation = useDownloadPullXlsx();
  const confirmMutation = useConfirmPull();
  const discardMutation = useDiscardPull();
  const startMutation = useStartPull();
  // Hooks stay above the early return below (rules of hooks) - this must run on every
  // render, `building`/`previewing` included, so it sees the transition the instant it
  // happens rather than only once the review UI itself mounts.
  useRefreshRowsOnReview(jobId, pull?.phase);

  if (isLoading || !pull) {
    return (
      <Card>
        <CardContent className="py-10">
          <SectionSkeleton rows={3} />
        </CardContent>
      </Card>
    );
  }

  const isBuilding = pull.phase === 'building' || pull.phase === 'previewing';
  const inReview = pull.phase === 'review' || pull.phase === 'confirmed';
  // AC-DS-9: the same set `discard_pull` itself allows (`_OPEN_PHASES`, backend).
  const isDiscardable =
    pull.phase === 'building' || pull.phase === 'previewing' || pull.phase === 'review';
  // AC-DS-11: `failed`/`expired`/`discarded` are the three dead ends "Pull again" answers -
  // `discarded` is not a FAILURE though, so the "pull failed" message below stays scoped to
  // the narrower `failed`/`expired` pair rather than this flag.
  const isDead = pull.phase === 'failed' || pull.phase === 'expired' || pull.phase === 'discarded';

  const handlePullAgain = async () => {
    try {
      // A document pull asks again for the window it covered: the GRN gateway refuses a
      // build with no scope (ss#107), and the same days are what "again" means anyway.
      const newPull = await startMutation.mutateAsync(
        isDocumentEntity(pull.entity) && pull.scope ? { entity: pull.entity, scope: pull.scope } : pull.entity,
      );
      router.push(`/system-management/import-jobs/${newPull.job_id}`);
    } catch (error) {
      toast.error(startPullErrorMessage(error));
    }
  };

  const counts = pull.counts;
  const counters = counts
    ? pull.entity === 'products'
      ? productCounters(counts as ProductPullCounts)
      : pull.entity === 'delivery_orders'
        ? deliveryOrderCounters(counts as DeliveryOrderPullCounts)
        : pull.entity === 'goods_receive_notes'
          ? goodsReceiveNoteCounters(counts as GoodsReceiveNotePullCounts)
          : stockCounters(counts as StockPullCounts)
    : [];
  const downloadLabel = pull.entity === 'stock_balances' ? 'Download Stock List' : 'Download xlsx';
  const summaryLine = compareSummaryLine(pull);

  return (
    <Card>
      <CardHeader className="block space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={PHASE_VARIANT[pull.phase]} appearance="light">
            {PHASE_LABEL[pull.phase]}
          </Badge>
          <span className="text-sm font-medium">
            {ENTITY_LABEL[pull.entity]}, {pull.company_code}
          </span>
          {inReview && (
            <span className="text-xs text-muted-foreground">
              Snapshot {formatSnapshotTime(pull.header?.extractedAt)}, valid until{' '}
              {formatSnapshotTime(pull.header?.expiresAt)}
            </span>
          )}
          {isDocumentEntity(pull.entity) && (
            <span className="text-xs text-muted-foreground">
              {scopeLine(pull.scope, pull.window, pull.entity === 'goods_receive_notes' ? 'GRN' : 'DO')}
            </span>
          )}
          <span className="grow" />
          {(pull.phase === 'review' || pull.phase === 'confirmed') && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => downloadMutation.mutate(jobId)}
              disabled={downloadMutation.isPending}
            >
              <Download className="size-4" />
              {downloadMutation.isPending ? 'Preparing…' : downloadLabel}
            </Button>
          )}
          {isDiscardable && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => discardMutation.mutate(jobId)}
              disabled={discardMutation.isPending}
            >
              {discardMutation.isPending ? 'Discarding…' : 'Discard'}
            </Button>
          )}
          {pull.phase === 'review' && (
            <Button
              size="sm"
              onClick={() => confirmMutation.mutate(jobId)}
              disabled={Boolean(pull.confirm_blocked_reason) || confirmMutation.isPending}
            >
              {confirmMutation.isPending ? 'Confirming…' : 'Confirm'}
            </Button>
          )}
          {pull.phase === 'confirmed' && pull.apply_job_id && (
            <Button asChild variant="outline" size="sm">
              <Link href={`/system-management/import-jobs/${pull.apply_job_id}`}>
                View apply job
              </Link>
            </Button>
          )}
          {isDead && (
            <Button size="sm" onClick={handlePullAgain} disabled={startMutation.isPending}>
              {startMutation.isPending ? 'Starting…' : 'Pull again'}
            </Button>
          )}
        </div>
        {pull.phase === 'review' && pull.confirm_blocked_reason && (
          <p className="text-xs text-destructive">{pull.confirm_blocked_reason}</p>
        )}
        {/* Advisory, not destructive (fix round 3, item 3) - the repo's warning token
            (`--warning`, `css/config.reui.css`), never `text-destructive`. */}
        {(pull.warnings ?? [])
          .filter((code) => WARNING_LABEL[code])
          .map((code) => (
            <p key={code} className="text-xs text-warning">
              {WARNING_LABEL[code]}
            </p>
          ))}
        {summaryLine && <p className="text-xs text-muted-foreground">{summaryLine}</p>}
      </CardHeader>

      <CardContent className="space-y-4">
        {isBuilding && (
          <div className="space-y-2">
            {pull.phase === 'previewing' && pull.preview_progress ? (
              // B3 (small-fix track): the preview's own row-by-row progress - the SAME
              // bar the Building state uses above, "N of M" instead of "Page N of M".
              <>
                <Progress
                  value={
                    (pull.preview_progress.processed / Math.max(pull.preview_progress.total, 1)) * 100
                  }
                />
                <p className="text-xs text-muted-foreground">
                  {pull.preview_progress.processed.toLocaleString()} of{' '}
                  {pull.preview_progress.total.toLocaleString()}
                </p>
              </>
            ) : pull.progress ? (
              <>
                <Progress value={(pull.progress.pagesDone / Math.max(pull.progress.pagesTotal, 1)) * 100} />
                <p className="text-xs text-muted-foreground">
                  Page {pull.progress.pagesDone} of {pull.progress.pagesTotal}
                  {pull.progress.stage ? ` · ${pull.progress.stage}` : ''}
                </p>
              </>
            ) : (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-label="Loading" />
              </div>
            )}
          </div>
        )}

        {(pull.phase === 'failed' || pull.phase === 'expired') && (
          <p className="text-sm text-destructive">
            {pull.error ?? 'The pull failed.'}
          </p>
        )}

        {inReview && (
          <>
            {counters.length > 0 && (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {counters.map((item) => (
                  <div key={item.label} className="rounded-lg border bg-muted/30 px-3 py-2">
                    <p className="text-lg font-semibold tabular-nums">{item.value.toLocaleString()}</p>
                    <p className="text-xs text-muted-foreground">{item.label}</p>
                  </div>
                ))}
              </div>
            )}

            <Tabs value={tab} onValueChange={setTab} className="w-full">
              <TabsList variant="line" className="w-full justify-start">
                <TabsTrigger value="changes">Changes</TabsTrigger>
                <TabsTrigger value="excel-view">Excel view</TabsTrigger>
                <TabsTrigger value="compare">Compare with my Excel</TabsTrigger>
              </TabsList>
              <TabsContent value="changes" className="mt-3 focus-visible:outline-none">
                <PullChangesTab jobId={jobId} />
              </TabsContent>
              <TabsContent value="excel-view" className="mt-3 focus-visible:outline-none">
                <PullExcelViewTab jobId={jobId} entity={pull.entity} />
              </TabsContent>
              <TabsContent value="compare" className="mt-3 focus-visible:outline-none">
                <PullCompareTab jobId={jobId} entity={pull.entity} window={pull.window} />
              </TabsContent>
            </Tabs>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default AutocountPullReview;
