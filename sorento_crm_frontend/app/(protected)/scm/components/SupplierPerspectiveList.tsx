'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ChevronDown, ChevronRight, Clock, Package, Truck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { EM_DASH, fmtDate, fmtInt, fmtPct, fmtSigned } from '../lib/format';
import type {
  HealthState,
  SupplierGroup,
  SupplierPerformance,
} from '../types/scm.types';
import { ConfidenceBadge, HealthLegend, StateChip } from './HealthIndicators';
import { ScmPager } from './ScmPager';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { distinctName } from '@/lib/product-display';

/** Supplier cards per page. */
const SUPPLIER_PAGE_SIZE = 10;

/** One metric in the supplier scorecard - reuses the roll-up stat-tile look. */
function ScoreMetric({
  label,
  value,
  muted,
  accentClass,
}: {
  label: string;
  value: string;
  muted?: boolean;
  accentClass?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-background px-3 py-2">
      <div className="truncate text-2xs font-medium text-muted-foreground" title={label}>
        {label}
      </div>
      <div
        className={cn(
          'mt-0.5 text-lg font-semibold tabular-nums tracking-tight',
          muted && 'text-muted-foreground',
          accentClass,
        )}
      >
        {value}
      </div>
    </div>
  );
}

/** Composite score band → colour accent (never the sole signal; the number is
 *  always shown). */
function compositeAccent(score: number): string {
  if (score >= 80) return 'text-scm-healthy';
  if (score >= 60) return 'text-scm-overstock';
  return 'text-scm-stockout';
}

/**
 * Supplier performance scorecard - real supplier-level analytics
 * (`supplier.performance`), null when the supplier was never scored. A
 * `low`-confidence score is muted end-to-end + flagged so a thin sample is
 * never mistaken for a confident one (plan §6).
 */
function SupplierScorecard({ supplier }: { supplier: SupplierGroup }) {
  const perf: SupplierPerformance | null = supplier.performance;

  if (!perf) {
    return (
      <div className="border-b border-border bg-muted/10 px-4 py-3 text-2xs text-muted-foreground">
        No performance history yet - needs received purchase orders to score.
      </div>
    );
  }

  const low = perf.confidence === 'low';
  return (
    <div className={cn('border-b border-border px-4 py-3', low && 'bg-scm-low-soft/30')}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-2xs font-medium text-muted-foreground">
          Supplier scorecard
          <span className="ml-1 text-muted-foreground/70">
            · {fmtInt(perf.sample_size)} order{perf.sample_size === 1 ? '' : 's'} scored
          </span>
        </div>
        <ConfidenceBadge confidence={perf.confidence} sampleSize={perf.sample_size} />
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <ScoreMetric
          label="Composite score"
          value={perf.composite_score === null ? EM_DASH : `${fmtInt(perf.composite_score)}/100`}
          muted={low}
          accentClass={
            low || perf.composite_score === null
              ? undefined
              : compositeAccent(perf.composite_score)
          }
        />
        <ScoreMetric label="On-time rate" value={fmtPct(perf.on_time_rate)} muted={low} />
        <ScoreMetric
          label="Avg lead time"
          value={`${fmtInt(perf.avg_lead_time_days)} d`}
          muted={low}
        />
        <ScoreMetric label="Reject rate" value={fmtPct(perf.reject_rate)} muted={low} />
        <ScoreMetric label="Fill rate" value={fmtPct(perf.fill_rate)} muted={low} />
      </div>
    </div>
  );
}

function SupplierCard({ supplier }: { supplier: SupplierGroup }) {
  // One supplier can carry many SKUs, so the product list collapses to keep the
  // list scannable supplier-to-supplier. Default COLLAPSED; the header + the
  // scorecard stay visible. Mirrors the Product grid's chevron disclosure.
  const [expanded, setExpanded] = useState(false);
  const skuCount = supplier.skus.length;

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate font-semibold" title={supplier.supplier_name}>
            {supplier.supplier_name}
          </div>
          <div className="mt-0.5 text-2xs text-muted-foreground">
            {fmtInt(supplier.skus.length)} SKU{supplier.skus.length === 1 ? '' : 's'} supplied
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="flex items-center justify-end gap-1 text-2xs text-muted-foreground">
              <Clock className="size-3" /> Lead time
            </div>
            <div className="font-medium tabular-nums">
              {supplier.declared_lead_time_days === null
                ? EM_DASH
                : `${supplier.declared_lead_time_days} days`}
            </div>
          </div>
          <div className="text-right">
            <div className="flex items-center justify-end gap-1 text-2xs text-muted-foreground">
              <Truck className="size-3" /> Incoming
            </div>
            <div className="font-medium tabular-nums text-scm-incoming">
              {supplier.incoming_po_count > 0
                ? `${fmtInt(supplier.incoming_po_count)} PO`
                : EM_DASH}
            </div>
          </div>
        </div>
      </div>

      <SupplierScorecard supplier={supplier} />

      {/* Disclosure header - click anywhere to expand/collapse the product list.
          Same chevron affordance as the Product grid's expandable rows. */}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className={cn(
          'flex w-full items-center gap-2 px-4 py-2 text-start text-2xs text-muted-foreground transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
          expanded && 'border-b border-border',
        )}
      >
        {expanded ? (
          <ChevronDown className="size-4 shrink-0" aria-hidden />
        ) : (
          <ChevronRight className="size-4 shrink-0" aria-hidden />
        )}
        <span className="font-medium text-foreground">Products supplied</span>
        <span className="tabular-nums">
          · {fmtInt(skuCount)} SKU{skuCount === 1 ? '' : 's'}
        </span>
      </button>

      {expanded ? (
        <ScrollArea>
          <table className="w-auto min-w-full text-sm">
            <thead>
              <tr className="text-2xs text-muted-foreground">
                <th className="px-4 py-2 text-left font-medium">SKU</th>
                <th className="px-2 py-2 text-right font-medium">On hand</th>
                <th className="px-2 py-2 text-right font-medium">On order</th>
                <th className="px-2 py-2 text-right font-medium">Net position</th>
                <th className="px-2 py-2 text-left font-medium">Status</th>
                <th className="px-4 py-2 text-right font-medium">Next ETA</th>
              </tr>
            </thead>
            <tbody>
              {supplier.skus.map((s) => (
                <tr key={s.sku} className="border-t border-border/60">
                  <td className="px-4 py-2">
                    <div className="font-medium">{s.sku}</div>
                    {distinctName(s.sku, s.product_name) ? (
                      <div className="truncate text-xs text-muted-foreground" title={s.product_name}>
                        {s.product_name}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums">{fmtInt(s.on_hand)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{fmtInt(s.on_order)}</td>
                  <td className="px-2 py-2 text-right font-medium tabular-nums">
                    {fmtSigned(s.net_position)}
                  </td>
                  <td className="px-2 py-2">
                    <StateChip state={s.status} />
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                    {fmtDate(s.incoming_po_eta)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ScrollBar orientation="horizontal" />
        </ScrollArea>
      ) : null}
    </Card>
  );
}

export function SupplierPerspectiveList({
  data,
  isLoading,
  isError,
  onRetry,
  activeState,
  onToggleHealth,
}: {
  data?: SupplierGroup[];
  isLoading: boolean;
  isError: boolean;
  onRetry?: () => void;
  activeState?: HealthState | null;
  onToggleHealth: (state: HealthState) => void;
}) {
  const [page, setPage] = useState(1);

  // Reset to page 1 whenever a filter re-fetch hands us a new supplier set.
  useEffect(() => {
    setPage(1);
  }, [data]);

  const total = data?.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / SUPPLIER_PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const paged = useMemo(
    () => (data ?? []).slice((safePage - 1) * SUPPLIER_PAGE_SIZE, safePage * SUPPLIER_PAGE_SIZE),
    [data, safePage],
  );

  if (isError) {
    return (
      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        <div className="text-sm text-scm-stockout">Couldn&apos;t load suppliers.</div>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </Card>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="p-4">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="mt-3 h-16 w-full" />
          </Card>
        ))}
      </div>
    );
  }

  if (!data?.length) {
    return (
      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-muted">
          <Package className="size-6 text-muted-foreground" />
        </span>
        <div>
          <div className="font-medium">No suppliers in scope</div>
          <div className="mt-1 text-sm text-muted-foreground">
            Widen the supplier filter, or link suppliers to products to see their positions.
          </div>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link href="/procurement-management">Go to procurement</Link>
        </Button>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {paged.map((s) => (
        <SupplierCard key={s.supplier_code} supplier={s} />
      ))}
      <ScmPager
        page={safePage}
        pageCount={pageCount}
        total={total}
        unit="supplier"
        onPageChange={setPage}
      />
      <HealthLegend className="px-1" activeState={activeState} onToggle={onToggleHealth} />
    </div>
  );
}
