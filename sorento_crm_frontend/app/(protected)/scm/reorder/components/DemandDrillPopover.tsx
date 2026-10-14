'use client';

import { useState } from 'react';
import Link from 'next/link';
import { AlertCircle, Info } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { EM_DASH, fmtInt } from '../../lib/format';
import { DemandContextHeader, hasDemandContext } from './DemandContextHeader';
import { dayLabel } from '../lib/coverageTimeline';
import { orderInquiryWorklistHref } from '../lib/orderInquiryLink';
import { fmtQty } from '../lib/qtyPrecision';
import { useOrderSummaryDemand } from '../hooks/useSummaryOrder';
import type { OrderSummaryDemandKind } from '../types/summaryOrder.types';
import { distinctName } from '@/lib/product-display';

/**
 * One aggregate on the Summary Order Report, opened behind an information icon
 * (AC-C2.2a).
 *
 * The row carries the total and nothing else. Preventing information fatigue is
 * a stated requirement, not a preference: the printed sheet Mr Loo works from
 * has one number per column, and a screen that inlines every contributing line
 * is a worse sheet, not a better one. So the aggregate is a figure with an icon
 * beside it, and the lines are fetched only when the icon is opened.
 *
 * Project demand decomposes into project, sales order, quantity and required
 * date (AC-C2.3). Dealer outstanding decomposes into dealer, sales order,
 * quantity and DAYS OUTSTANDING (AC-C2.4) - the column the printed sheet has no
 * room for, and the reason a 2-unit line that has waited 214 days outranks a
 * 96-unit line raised in May.
 *
 * **The ordering is the server's.** Dealer lines arrive worst-first and are
 * rendered in the order received, so the ageing a person reads is the ageing the
 * server computed. Nothing here re-sorts.
 */

const KIND_LABEL: Record<OrderSummaryDemandKind, string> = {
  project: 'Project demand',
  retail: 'Retail outstanding',
  unclassified: 'Unclassified demand',
  // The legacy name of `retail`. Kept so an older caller still renders, and it
  // says Retail either way - the user's word for that side of the book.
  dealer: 'Retail outstanding',
};

/** Ageing tone. Only the worst band is coloured, so the colour still means something. */
function ageTone(days: number): string {
  if (days >= 180) return 'text-scm-stockout';
  if (days >= 60) return 'text-scm-overstock';
  return 'text-muted-foreground';
}

export interface DemandDrillPopoverProps {
  productCode: string;
  productName?: string | null;
  kind: OrderSummaryDemandKind;
  /** The aggregate shown on the row. The drill's total must agree with it. */
  totalQty: number;
  /** How many lines are behind it, so the icon can be skipped when there are none. */
  lineCount: number;
  /** Opaque run key, passed through so a past week drills to that week's lines. */
  runId: string | null;
  /**
   * The row's FROZEN `uom_decimal_places` (AC-F12). A measure unit's 1.25 kg must
   * not be rendered as 1 here while the row above says 1.25.
   */
  decimalPlaces?: number;
}

export function DemandDrillPopover({
  productCode,
  productName,
  kind,
  totalQty,
  lineCount,
  runId,
  decimalPlaces = 0,
}: DemandDrillPopoverProps) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError, error, refetch } = useOrderSummaryDemand(
    productCode,
    kind,
    runId,
    open,
  );

  // Nothing contributes, so there is nothing to open. The figure still renders.
  if (lineCount === 0) {
    return (
      <span className="tabular-nums" data-testid={`demand-total-${kind}`}>
        {fmtQty(totalQty, decimalPlaces)}
      </span>
    );
  }

  return (
    <span className="flex items-center justify-end gap-1">
      <span className="tabular-nums" data-testid={`demand-total-${kind}`}>
        {fmtQty(totalQty, decimalPlaces)}
      </span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            onClick={(e) => e.stopPropagation()}
            aria-label={`${KIND_LABEL[kind]} for ${productCode}, ${lineCount} ${
              lineCount === 1 ? 'line' : 'lines'
            }`}
            className="inline-flex shrink-0 items-center rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Info className="size-3.5" aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            align="end"
            collisionPadding={8}
            onClick={(e) => e.stopPropagation()}
            className="w-[min(22rem,calc(100vw-2rem))] p-0"
          >
            <div className="flex flex-col gap-1 border-b px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 break-words">
                <div className="text-xs font-semibold">{KIND_LABEL[kind]}</div>
                <div className="text-2xs text-muted-foreground">
                  {productCode}
                  {distinctName(productCode, productName) ? ` · ${productName}` : ''}
                </div>
              </div>
              <Badge variant="secondary" appearance="light" size="xs" className="w-fit shrink-0">
                {fmtQty(totalQty, decimalPlaces)}
              </Badge>
            </div>

            {data && hasDemandContext(data, kind) ? (
              <div className="border-b px-3 py-2">
                <DemandContextHeader data={data} channel={kind} />
              </div>
            ) : null}

            {isLoading ? (
              <div className="space-y-2 p-3" aria-label="Loading contributing lines" aria-busy="true">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-8 w-full rounded-md" />
                ))}
              </div>
            ) : isError || !data ? (
              <div className="flex flex-col items-center gap-2 p-4 text-center">
                <AlertCircle className="size-4 text-destructive" aria-hidden />
                <p className="text-2xs text-muted-foreground">
                  {error instanceof Error ? error.message : 'Failed to load the contributing lines.'}
                </p>
                <Button variant="outline" size="sm" onClick={() => void refetch()}>
                  Try again
                </Button>
              </div>
            ) : kind === 'project' ? (
              <ProjectLines drill={data} dp={decimalPlaces} />
            ) : kind === 'unclassified' ? (
              <UnclassifiedLines drill={data} dp={decimalPlaces} />
            ) : (
              <RetailLines drill={data} dp={decimalPlaces} />
            )}
          </PopoverContent>
        </PopoverPortal>
      </Popover>
    </span>
  );
}

function EmptyLines({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-4 text-center text-2xs text-muted-foreground">{children}</div>;
}

function ProjectLines({
  drill,
  dp,
}: {
  drill: NonNullable<ReturnType<typeof useOrderSummaryDemand>['data']>;
  dp: number;
}) {
  if (drill.project_lines.length === 0) {
    return <EmptyLines>No project has committed this item.</EmptyLines>;
  }
  return (
    <div className="max-h-64 overflow-y-auto" data-testid="project-lines">
      {drill.project_lines.map((line, i) => (
        <div
          key={`${line.so_number}-${i}`}
          className="flex items-start justify-between gap-3 border-b px-3 py-2 text-sm last:border-b-0"
        >
          <div className="min-w-0">
            <div className="truncate font-medium" title={line.project_name}>
              {line.project_name}
            </div>
            <div className="truncate text-2xs text-muted-foreground">
              {/* Click-through to the Order Inquiry worklist, scoped to this SO (captain,
                  20 Aug). Project demand only - this line's whole reason for being here IS
                  an Order Inquiry, so there is always something on the other end. */}
              <Link
                href={orderInquiryWorklistHref(line.so_number)}
                onClick={(e) => e.stopPropagation()}
                className="font-medium text-foreground hover:text-primary hover:underline"
                title={`Open ${line.so_number} on the Order Inquiry worklist`}
              >
                {line.so_number}
              </Link>
              {line.line_no !== null && line.line_no !== undefined ? ` line ${line.line_no}` : ''} ·{' '}
              {line.warehouse_code ? `${line.warehouse_code} · ` : ''}
              needed {line.required_date ? dayLabel(line.required_date) : 'no date'}
            </div>
            {/* The trace from a Buy back to the decision that confirmed it (AC-D06).
                A revision and an inquiry number - never a UUID. */}
            <div className="truncate text-2xs text-muted-foreground">
              {line.decision_ref ?? 'No confirmed decision yet'}
            </div>
          </div>
          <span className="shrink-0 tabular-nums">{fmtQty(line.qty, dp)}</span>
        </div>
      ))}
    </div>
  );
}

function RetailLines({
  drill,
  dp,
}: {
  drill: NonNullable<ReturnType<typeof useOrderSummaryDemand>['data']>;
  dp: number;
}) {
  // `retail_lines` is the name; `dealer_lines` is the same lines under the old one.
  const lines = drill.retail_lines ?? drill.dealer_lines ?? [];
  if (lines.length === 0) {
    return <EmptyLines>No retail order is outstanding on this item.</EmptyLines>;
  }
  return (
    <div className="max-h-64 overflow-y-auto" data-testid="retail-lines">
      {lines.map((line, i) => (
        <div
          key={`${line.so_number}-${i}`}
          className="flex items-start justify-between gap-3 border-b px-3 py-2 text-sm last:border-b-0"
        >
          <div className="min-w-0">
            <div className="truncate font-medium" title={line.dealer_name}>
              {line.dealer_name}
            </div>
            <div className="truncate text-2xs text-muted-foreground">
              {line.so_number} · raised{' '}
              {line.ordered_date ? dayLabel(line.ordered_date) : EM_DASH}
            </div>
          </div>
          <div className="shrink-0 text-end">
            <div className="tabular-nums">{fmtQty(line.qty, dp)}</div>
            <div className={cn('text-2xs tabular-nums', ageTone(line.days_outstanding))}>
              {fmtInt(line.days_outstanding)} days
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * Demand whose SO carries no persisted class (AC-E02 / AC-E07).
 *
 * It is shown as an exception with the SO lines behind it, and it is excluded from
 * the actionable suggestion until somebody classifies it. It never becomes a third
 * demand class, and the fulfilment location is never used to guess one.
 */
function UnclassifiedLines({
  drill,
  dp,
}: {
  drill: NonNullable<ReturnType<typeof useOrderSummaryDemand>['data']>;
  dp: number;
}) {
  const lines = drill.unclassified_lines ?? [];
  if (lines.length === 0) {
    return <EmptyLines>Every order on this item carries a demand class.</EmptyLines>;
  }
  return (
    <div className="max-h-64 overflow-y-auto" data-testid="unclassified-lines">
      {lines.map((line, i) => (
        <div
          key={`${line.so_number}-${i}`}
          className="flex items-start justify-between gap-3 border-b px-3 py-2 text-sm last:border-b-0"
        >
          <div className="min-w-0">
            <div className="truncate font-medium" title={line.customer_name}>
              {line.customer_name}
            </div>
            <div className="truncate text-2xs text-muted-foreground">
              {line.so_number}
              {line.line_no !== null ? ` line ${line.line_no}` : ''} · raised{' '}
              {line.ordered_date ? dayLabel(line.ordered_date) : EM_DASH}
            </div>
            <div className="text-2xs text-scm-overstock" title={line.exception}>
              {line.exception}
            </div>
          </div>
          <span className="shrink-0 tabular-nums">{fmtQty(line.qty, dp)}</span>
        </div>
      ))}
    </div>
  );
}

export default DemandDrillPopover;
