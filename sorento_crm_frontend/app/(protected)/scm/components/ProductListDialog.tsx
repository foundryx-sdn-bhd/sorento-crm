'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Info,
  PackageOpen,
  } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { EM_DASH, fmtDate, fmtDecimal, fmtDoc, fmtInt, fmtMoney, fmtSigned } from '../lib/format';
import { useDemandExplain, useScmProducts } from '../hooks/useScmDashboard';
import type { ScmFilters } from '../services/scmDashboardService';
import type { HealthState, ProductSummary } from '../types/scm.types';
import {
  AvgDemandInfo,
  ClassChip,
  CommittedStockoutPill,
  DaysOfCoverInfo,
  LEAD_TIME_HINT,
  NetPositionInfo,
  REORDER_POINT_FORMULA,
  SAFETY_STOCK_HINT,
  StateChip,
} from './HealthIndicators';
import { distinctName } from '@/lib/product-display';

const PAGE_SIZE = 50;

/** Drill-down target scope (health state and/or a single warehouse). */
export interface ProductDrillTarget {
  status?: HealthState | null;
  warehouse?: string | null;
}

/**
 * Unified read-only drill-down popup. One component backs every "what's behind
 * this number?" surface on the dashboard - roll-up stat tiles, warehouse tile
 * counts, and the warehouse "view products" affordance. Callers pass the base
 * `filters` + a `target` scope; the popup owns its own server-side search / sort
 * / pagination (these sets can be thousands of rows). No mutations, no UUIDs.
 */
export interface ProductListDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** Current dashboard filters - scope the drill-down set. */
  filters: ScmFilters;
  /** The scope this popup drills into (health state / warehouse). Null = closed. */
  target: ProductDrillTarget | null;
  /** When set, renders a "View in list" action that switches to the Product
   *  perspective pre-filtered (and closes the dialog). */
  onViewInList?: () => void;
}

type SortDir = 'asc' | 'desc';
interface SortState {
  field: string;
  dir: SortDir;
}

interface DrillColumn {
  id: string;
  label: string;
  align: 'left' | 'right' | 'center';
  info?: boolean;
  docInfo?: boolean;
  /** Renders the avg-daily-demand (i) header tooltip (M8-B9). */
  demandInfo?: boolean;
  sortable?: boolean;
}

/** Columns mirror the Product-perspective grid order + alignment. Every data
 *  column is server-sortable (the BE allow-lists each field; Value/Demand sort by
 *  class letter A<B<C / X<Y<Z with unknown last). */
const COLUMNS: DrillColumn[] = [
  { id: 'sku', label: 'SKU', align: 'left', sortable: true },
  { id: 'status', label: 'Status', align: 'left', sortable: true },
  { id: 'net_position', label: 'Net position', align: 'right', info: true, sortable: true },
  { id: 'on_hand', label: 'On hand', align: 'right', sortable: true },
  { id: 'on_order', label: 'On order', align: 'right', sortable: true },
  { id: 'committed', label: 'Committed', align: 'right', sortable: true },
  { id: 'stock_valuation', label: 'Stock valuation', align: 'right', sortable: true },
  { id: 'avg_daily_demand', label: 'Avg daily demand', align: 'right', demandInfo: true, sortable: true },
  { id: 'days_of_cover', label: 'Days of cover', align: 'right', docInfo: true, sortable: true },
  // Plain-language headers; underlying fields stay abc_class/xyz_class.
  { id: 'abc_class', label: 'Value', align: 'center', sortable: true },
  { id: 'xyz_class', label: 'Demand', align: 'center', sortable: true },
];

/** The Reorder-point column added only to the "Low stock" drill (M8-B8) so the
 *  net <= reorder-point relationship is visible. Not server-sortable (the BE does
 *  not allow-list it). Inserted right after Days of cover. */
const REORDER_POINT_COLUMN: DrillColumn = {
  id: 'reorder_point',
  label: 'Reorder point',
  align: 'right',
};

/** The drill columns for a target: the Low-stock drill inserts Reorder point. */
function columnsForTarget(isLowDrill: boolean): DrillColumn[] {
  if (!isLowDrill) return COLUMNS;
  const idx = COLUMNS.findIndex((c) => c.id === 'days_of_cover');
  return [
    ...COLUMNS.slice(0, idx + 1),
    REORDER_POINT_COLUMN,
    ...COLUMNS.slice(idx + 1),
  ];
}

/** The demand-explain popover body - mounted ONLY while the popover is open (Radix
 *  unmounts closed content), so `useDemandExplain` fetches lazily on open and the
 *  hook never runs for a closed cell / a row without ids. Lists the delivery orders
 *  that drove the outflow (navigable to each order) + rate + variability (M8-B9). */
function DemandExplainBody({
  productId,
  warehouseId,
}: {
  productId: string;
  warehouseId?: string | null;
}) {
  const { data, isLoading, isError } = useDemandExplain(productId, warehouseId, true);
  return (
    <>
      <div className="border-b px-3 py-2 text-xs font-semibold">
        Demand (delivery orders that drove outflow)
      </div>
      {isLoading ? (
        <div className="space-y-2 p-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-5 w-full" />
          ))}
        </div>
      ) : isError ? (
        <div className="px-3 py-3 text-xs text-scm-stockout">
          Couldn&apos;t load the demand detail. Close and reopen to retry.
        </div>
      ) : (
        <div className="px-3 py-2">
          {data && data.demand_dos.length ? (
            <div className="mb-1.5 space-y-1">
              {data.demand_dos.map((doItem) => (
                <Link
                  key={doItem.order_id}
                  href={`/order-management/orders/${doItem.order_id}`}
                  className="flex items-center justify-between gap-2 rounded-sm text-xs hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  title={`Open ${doItem.do_number}`}
                >
                  <span className="font-medium hover:underline">{doItem.do_number}</span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {fmtDate(doItem.order_date)}
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {fmtInt(doItem.qty_out)} out
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="mb-1.5 text-xs text-muted-foreground">
              No delivery orders in the demand window.
            </div>
          )}
          <div className="flex justify-between border-t pt-1.5 text-xs">
            <span className="text-muted-foreground">avg / day</span>
            <span className="tabular-nums">{fmtDecimal(data?.avg_daily_demand)}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-muted-foreground">Coefficient of variation</span>
            <span className="tabular-nums">
              {data?.demand_cv == null ? EM_DASH : fmtDecimal(data.demand_cv, 2)}
            </span>
          </div>
          <p className="mt-0.5 text-2xs text-muted-foreground">
            How spread out demand is - higher means less predictable.
          </p>
        </div>
      )}
    </>
  );
}

/** Avg-daily-demand cell: the number + a click-to-explain (i) opening the delivery
 *  orders that drove the outflow (M8-B9), reusing the Slice A2
 *  `/analytics/explain/demand` endpoint. The (i) needs the row's product/warehouse
 *  UUIDs (carried for this fetch only, never displayed) - absent for a legacy row,
 *  the (i) is simply hidden and the plain number renders. */
function AvgDemandCell({ row }: { row: ProductSummary }) {
  if (!row.product_id) {
    return <span className="tabular-nums">{fmtDecimal(row.avg_daily_demand)}</span>;
  }
  return (
    <span className="inline-flex items-center justify-end gap-1 tabular-nums">
      <span>{fmtDecimal(row.avg_daily_demand)}</span>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            title="Delivery orders behind this demand"
            aria-label={`Explain avg daily demand for ${row.sku}`}
            className="rounded-sm text-muted-foreground/70 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Info className="size-3.5" aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" collisionPadding={8} className="w-80 p-0 text-sm">
          <DemandExplainBody productId={row.product_id} warehouseId={row.warehouse_id} />
        </PopoverContent>
      </Popover>
    </span>
  );
}

/** One value+definition line in the reorder-point explain (M8-F10). A present value
 *  shows its number + the plain definition; a null value shows a dash and notes that
 *  it is set on the reorder plan (only for the missing one), never a fabricated number. */
function RopInputRow({
  label,
  value,
  suffix,
  hint,
}: {
  label: string;
  value: number | null | undefined;
  suffix?: string;
  hint: string;
}) {
  const missing = value == null;
  return (
    <div>
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">
          {missing ? EM_DASH : `${fmtInt(value)}${suffix ?? ''}`}
        </span>
      </div>
      <p className="text-2xs text-muted-foreground">
        {missing ? 'Set on the reorder plan.' : hint}
      </p>
    </div>
  );
}

/** Reorder-point cell for the Low-stock drill (M8-F5 / M8-F10): the value + a
 *  click-to-explain (i) showing the ROP formula and the actual inputs that feed it -
 *  reorder point, demand rate (avg / day), and safety stock + lead time (each with a
 *  one-line plain definition). Safety stock / lead time come from the same latest-run
 *  rec as reorder_point (rec inputs); a null one renders a dash + the "set on the
 *  reorder plan" note rather than a fabricated number. */
function ReorderPointCell({ row }: { row: ProductSummary }) {
  if (row.reorder_point == null) {
    return <span className="tabular-nums text-muted-foreground">{EM_DASH}</span>;
  }
  return (
    <span className="inline-flex items-center justify-end gap-1 tabular-nums">
      <span>{fmtInt(row.reorder_point)}</span>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            title="How the reorder point is worked out"
            aria-label={`Explain reorder point for ${row.sku}`}
            className="rounded-sm text-muted-foreground/70 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Info className="size-3.5" aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" collisionPadding={8} className="w-72 p-0 text-sm">
          <div className="border-b px-3 py-2 text-xs font-semibold">Reorder point</div>
          <div className="space-y-1.5 px-3 py-2">
            <p className="text-2xs text-muted-foreground">{REORDER_POINT_FORMULA}</p>
            <div className="flex justify-between text-xs">
              <span className="text-muted-foreground">Reorder point</span>
              <span className="tabular-nums font-semibold">{fmtInt(row.reorder_point)}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-muted-foreground">Demand rate (avg / day)</span>
              <span className="tabular-nums">{fmtDecimal(row.avg_daily_demand)}</span>
            </div>
            <RopInputRow
              label="Safety stock"
              value={row.safety_stock}
              hint={SAFETY_STOCK_HINT}
            />
            <RopInputRow
              label="Lead time"
              value={row.lead_time_days}
              suffix=" days"
              hint={LEAD_TIME_HINT}
            />
          </div>
        </PopoverContent>
      </Popover>
    </span>
  );
}

export function ProductListDialog({
  open,
  onOpenChange,
  title,
  filters,
  target,
  onViewInList,
}: ProductListDialogProps) {
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [sort, setSort] = useState<SortState | null>(null);
  const [page, setPage] = useState(1);

  // The Low-stock drill adds a Reorder point column so net <= ROP is visible (M8-B8).
  const isLowDrill = target?.status === 'low';
  const columns = columnsForTarget(isLowDrill);

  // Reset all popup query state whenever the drill scope changes / reopens.
  const scopeKey = `${target?.status ?? ''}|${target?.warehouse ?? ''}|${open}`;
  useEffect(() => {
    resetSearch();
    setSort(null);
    setPage(1);
  }, [scopeKey, resetSearch]);

  // Search / sort changes reset to the first page.
  useEffect(() => {
    setPage(1);
  }, [debounced, sort]);

  // Health (incl. `overstock`, computed from days-of-cover server-side, and `low`
  // = below the demand-aware reorder point, M8-B) + ABC/XYZ filtering + pagination
  // are all authoritative on the backend now.
  const { data, isLoading, isFetching, isError } = useScmProducts(
    filters,
    open ? target : null,
    {
      q: debounced || undefined,
      sort: sort?.field,
      dir: sort?.dir,
      page,
      limit: PAGE_SIZE,
    },
  );

  const rows = data?.data ?? [];
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const toggleSort = (field: string) => {
    setSort((prev) => {
      if (!prev || prev.field !== field) return { field, dir: 'asc' };
      if (prev.dir === 'asc') return { field, dir: 'desc' };
      return null; // third click clears back to the default order
    });
  };

  return (
    // `modal` so the overlay intercepts the outside click: clicking a card
    // behind the popup ONLY closes the popup (no click-through to the card).
    <Dialog open={open} onOpenChange={onOpenChange} modal>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {isLoading
              ? 'Loading products…'
              : `${fmtInt(total)} product${total === 1 ? '' : 's'}`}
          </DialogDescription>
        </DialogHeader>

        <ListSearchInput
          value={search}
          onChange={setSearch}
          isSettling={isSearchInFlight(debouncedSettling, isFetching, debounced)}
          placeholder="Search product code or name…"
          aria-label="Search products"
        />

        <DialogBody className="max-h-[55dvh] overflow-auto">
          {isLoading ? (
            <div className="space-y-2 py-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          ) : isError ? (
            <div className="py-10 text-center text-sm text-scm-stockout">
              Couldn&apos;t load products. Close and reopen to retry.
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <span className="flex size-11 items-center justify-center rounded-full bg-muted">
                <PackageOpen className="size-5 text-muted-foreground" />
              </span>
              <div className="text-sm text-muted-foreground">
                {debounced
                  ? `No products match “${debounced}”.`
                  : 'No products in this scope.'}
              </div>
            </div>
          ) : (
            // DialogBody is already the scroller, on both axes.
            <table className={cn('w-full text-sm', isLowDrill ? 'min-w-[980px]' : 'min-w-[880px]')}>
              <thead className="sticky top-0 z-10 bg-background">
                <tr className="text-2xs text-muted-foreground">
                  {columns.map((col) => {
                    const active = sort?.field === col.id;
                    const alignCls =
                      col.align === 'right'
                        ? 'text-right'
                        : col.align === 'center'
                          ? 'text-center'
                          : 'text-left';
                    return (
                      <th
                        key={col.id}
                        className={cn(
                          'py-2 font-medium',
                          alignCls,
                          col.id === 'sku' ? 'pr-2' : 'px-2',
                        )}
                      >
                        {col.sortable ? (
                          <button
                            type="button"
                            onClick={() => toggleSort(col.id)}
                            className={cn(
                              'inline-flex items-center gap-1 rounded-sm hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                              col.align === 'right' && 'flex-row-reverse',
                              active && 'text-foreground',
                            )}
                          >
                            {col.label}
                            {active ? (
                              sort?.dir === 'asc' ? (
                                <ArrowUp className="size-3" />
                              ) : (
                                <ArrowDown className="size-3" />
                              )
                            ) : (
                              <ChevronsUpDown className="size-3 opacity-40" />
                            )}
                          </button>
                        ) : (
                          <span>{col.label}</span>
                        )}
                        {col.info ? <NetPositionInfo className="ms-1 align-middle" /> : null}
                        {col.docInfo ? <DaysOfCoverInfo className="ms-1 align-middle" /> : null}
                        {col.demandInfo ? <AvgDemandInfo className="ms-1 align-middle" /> : null}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody className={cn(isFetching && 'opacity-60')}>
                {rows.map((p, i) => {
                  // ∞ cover = stock on hand but no forward demand; deficit → "-".
                  const docInfinite =
                    (p.avg_daily_demand === null || p.avg_daily_demand === 0) &&
                    p.net_position > 0;
                  return (
                  <tr
                    key={`${p.sku}-${p.warehouse_code}-${i}`}
                    className="border-t border-border/60"
                  >
                    <td className="py-2 pr-2">
                      <div className="font-medium">{p.sku}</div>
                      <div
                        className="truncate text-xs text-muted-foreground"
                        title={[distinctName(p.sku, p.product_name), p.warehouse_name].filter(Boolean).join(' · ')}
                      >
                        {[distinctName(p.sku, p.product_name), p.warehouse_name].filter(Boolean).join(' · ')}
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex flex-wrap items-center gap-1">
                        <StateChip state={p.status} />
                        {p.stockout_with_committed ? <CommittedStockoutPill /> : null}
                      </div>
                    </td>
                    <td
                      className={cn(
                        'px-2 py-2 text-right font-semibold tabular-nums',
                        p.net_position < 0 && 'text-scm-stockout',
                      )}
                    >
                      {fmtSigned(p.net_position)}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums">{fmtInt(p.on_hand)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">{fmtInt(p.on_order)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">{fmtInt(p.committed)}</td>
                    <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-muted-foreground">
                      {p.stock_valuation === null ? EM_DASH : fmtMoney(p.stock_valuation)}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">
                      <AvgDemandCell row={p} />
                    </td>
                    <td
                      className={cn(
                        'px-2 py-2 text-right tabular-nums',
                        docInfinite && 'text-scm-overstock',
                      )}
                    >
                      {fmtDoc(p.days_of_cover, docInfinite)}
                    </td>
                    {isLowDrill ? (
                      <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">
                        <ReorderPointCell row={p} />
                      </td>
                    ) : null}
                    <td className="px-2 py-2 text-center">
                      <ClassChip value={p.abc_class} kind="abc" />
                    </td>
                    <td className="px-2 py-2 text-center">
                      <ClassChip value={p.xyz_class} kind="xyz" />
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </DialogBody>

        <DialogFooter className="flex-row items-center justify-between gap-2 sm:justify-between">
          {pageCount > 1 ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1 || isFetching}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                aria-label="Previous page"
              >
                <ChevronLeft className="size-4" />
              </Button>
              <span className="tabular-nums">
                Page {page} of {pageCount}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pageCount || isFetching}
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                aria-label="Next page"
              >
                <ChevronRight className="size-4" />
              </Button>
            </div>
          ) : (
            <span />
          )}

          {onViewInList ? (
            <Button
              variant="outline"
              onClick={() => {
                onOpenChange(false);
                onViewInList();
              }}
            >
              View in list
              <ArrowUpRight className="size-4" />
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
