'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Popover as PopoverPrimitive } from 'radix-ui';
import {
  AlertTriangle,
  ArrowRightLeft,
  ChevronDown,
  Info,
  Layers,
  ShoppingCart,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { cn } from '@/lib/utils';
import { ConfidenceBadge } from '../../components/HealthIndicators';
import { EM_DASH, fmtDoc, fmtInt, fmtSigned, fmtSupplierCost } from '../../lib/format';
import { useReorderRecommendations } from '../hooks/useReorderRun';
import type {
  AllocationLine,
  DispositionAction,
  ReorderRecommendation,
  SupplierChoice,
} from '../types/reorder.types';
import { ReorderExplanationDialog } from './ReorderExplanationDialog';

/** Plain-language header tooltips - spell out every metric for a novice. Keyboard-
 *  accessible (the trigger is focusable) and reused as the aria-label. */
const HEADER_TIPS = {
  order_qty:
    "How many to order = Order-up-to − Net position, rounded to the supplier's pack size / minimum order.",
  reorder_point:
    'The stock level that triggers a reorder = forecast demand over the lead time + safety stock. When net position falls to/below it, reorder.',
  min: 'Min - the floor under a Min/Max policy. When net falls to/below Min, reorder up to Max.',
  max: 'Max - the ceiling a Min/Max policy replenishes up to.',
  order_up_to:
    "The target stock level you replenish to = reorder point + one review-period's demand.",
  net: 'Net position = on hand + on order − committed.',
  days_of_cover:
    'How many days your net position lasts at the forecast demand (net ÷ daily demand).',
  confidence:
    'How much data backs the recommendation (demand pattern + sample size) - not a promise the number is correct.',
} as const;

/** Keyboard-accessible info icon for a metric column header (mirrors the M2
 *  net-position / days-of-cover header tooltips). `stopPropagation` keeps a click
 *  on the icon from toggling the column sort. */
function HeaderInfo({ tip }: { tip: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          role="img"
          aria-label={tip}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          className="inline-flex cursor-help items-center text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Info className="size-3.5" aria-hidden />
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{tip}</TooltipContent>
    </Tooltip>
  );
}

const TYPE_FILTER_OPTIONS = [
  { value: '', label: 'All types' },
  { value: 'buy', label: 'Buy' },
  { value: 'disposition', label: 'Disposition' },
  { value: 'exception', label: 'Exception' },
];

const DISPOSITION_LABEL: Record<DispositionAction, string> = {
  discontinue: 'Discontinue',
  promo: 'Promote',
  hold: 'Hold',
};

/** Type chip - buy / disposition / no-supplier exception are visually distinct. */
function TypeChip({ rec }: { rec: ReorderRecommendation }) {
  if (rec.type === 'buy') {
    return (
      <Badge variant="info" appearance="light" size="md">
        <ShoppingCart className="size-3" />
        Buy
      </Badge>
    );
  }
  if (rec.type === 'exception') {
    return (
      <Badge
        variant="warning"
        appearance="light"
        size="md"
        title="A reorder would fire, but no supplier is linked to source it"
      >
        <AlertTriangle className="size-3" />
        Exception
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" appearance="light" size="md">
      <Layers className="size-3" />
      Disposition
    </Badge>
  );
}

/** Network warehouse cell - a chevron button opens the per-warehouse split. */
function AllocationCell({ allocation }: { allocation: AllocationLine[] }) {
  const total = allocation.reduce((sum, a) => sum + a.qty, 0);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          className="flex items-center gap-1 rounded-sm text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Badge variant="primary" appearance="light" size="md">
            Network
          </Badge>
          <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverPrimitive.Portal>
        <PopoverContent align="start" collisionPadding={8} className="w-64 p-0">
          <div className="border-b px-3 py-2 text-xs font-medium">Suggested allocation</div>
          <div className="max-h-56 overflow-y-auto">
            {allocation.map((a) => (
              <div
                key={a.warehouse_code}
                className="flex items-center justify-between px-3 py-1.5 text-sm"
              >
                <span className="truncate" title={a.warehouse_name}>
                  <span className="font-medium">{a.warehouse_code}</span>{' '}
                  <span className="text-xs text-muted-foreground">{a.warehouse_name}</span>
                </span>
                <span className="tabular-nums">{fmtInt(a.qty)}</span>
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between border-t px-3 py-1.5 text-sm font-semibold">
            <span>Total</span>
            <span className="tabular-nums">{fmtInt(total)}</span>
          </div>
        </PopoverContent>
      </PopoverPrimitive.Portal>
    </Popover>
  );
}

function ScoreDot({ score }: { score: number | null }) {
  if (score === null) return <span className="text-muted-foreground">{EM_DASH}</span>;
  return <span className="tabular-nums">{fmtInt(score)}</span>;
}

/** Selected supplier + an alternatives popover with ranked alternates. */
function SupplierCell({ rec }: { rec: ReorderRecommendation }) {
  if (rec.is_exception || !rec.supplier) {
    return (
      <Badge variant="warning" appearance="light" size="md" title="No linked supplier - resolve before ordering">
        No supplier
      </Badge>
    );
  }
  const others = rec.alternatives.filter((a) => a.supplier_code !== rec.supplier?.supplier_code);
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <div className="min-w-0">
        <div className="truncate font-medium" title={rec.supplier.supplier_name}>
          {rec.supplier.supplier_name}
        </div>
        <div className="text-xs text-muted-foreground tabular-nums">
          {fmtSupplierCost(rec.supplier.unit_cost, rec.supplier.currency)} ·{' '}
          {fmtInt(rec.supplier.lead_time_days)}d lead
        </div>
      </div>
      {others.length ? (
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 shrink-0 px-1.5 text-2xs"
              onClick={(e) => e.stopPropagation()}
            >
              +{others.length}
            </Button>
          </PopoverTrigger>
          <PopoverPrimitive.Portal>
            <PopoverContent align="end" collisionPadding={8} className="w-72 p-0">
              <div className="border-b px-3 py-2 text-xs font-medium">Alternative suppliers</div>
              <div className="max-h-64 overflow-y-auto">
                <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 px-3 py-1.5 text-2xs font-medium text-muted-foreground">
                  <span>Supplier</span>
                  <span className="text-right">Cost</span>
                  <span className="text-right">Lead</span>
                  <span className="text-right">Score</span>
                </div>
                {[rec.supplier, ...others].map((s: SupplierChoice) => {
                  const selected = s.supplier_code === rec.supplier?.supplier_code;
                  return (
                    <div
                      key={s.supplier_code}
                      className={cn(
                        'grid grid-cols-[1fr_auto_auto_auto] items-center gap-x-3 px-3 py-1.5 text-sm',
                        selected && 'bg-muted/50',
                      )}
                    >
                      <span className="flex min-w-0 items-center gap-1">
                        <span className="truncate" title={s.supplier_name}>
                          {s.supplier_name}
                        </span>
                        {selected ? (
                          <Badge variant="primary" appearance="light" size="xs">
                            selected
                          </Badge>
                        ) : null}
                      </span>
                      <span className="text-right tabular-nums">
                        {fmtSupplierCost(s.unit_cost, s.currency)}
                      </span>
                      <span className="text-right tabular-nums">{fmtInt(s.lead_time_days)}d</span>
                      <span className="text-right">
                        <ScoreDot score={s.composite_score} />
                      </span>
                    </div>
                  );
                })}
              </div>
            </PopoverContent>
          </PopoverPrimitive.Portal>
        </Popover>
      ) : null}
    </div>
  );
}

const numMeta = { headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' };

export function ReorderResultsGrid({
  runId,
  enabled,
  typeFilter,
  onTypeFilterChange,
}: {
  runId: string | null;
  enabled: boolean;
  /** Controlled type filter - shared with the clickable stat tiles. */
  typeFilter: string;
  onTypeFilterChange: (value: string) => void;
}) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  // The row-click explanation popup - deterministic derivation from frozen inputs.
  const [explainRec, setExplainRec] = useState<ReorderRecommendation | null>(null);

  const { data, isLoading, isFetching, isPlaceholderData, refetch, error } = useReorderRecommendations(
    runId,
    {
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      type: (typeFilter || null) as 'buy' | 'disposition' | 'exception' | null,
      searchQuery,
      sorting,
    },
    enabled,
  );

  useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery, typeFilter, sorting]);

  const rows = useMemo<ReorderRecommendation[]>(() => data?.data ?? [], [data]);

  const columns = useMemo<ColumnDef<ReorderRecommendation>[]>(
    () => [
      {
        accessorKey: 'type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => <TypeChip rec={row.original} />,
        size: 130,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-6 w-20" /> },
      },
      {
        accessorKey: 'sku',
        header: ({ column }) => <DataGridColumnHeader title="SKU" column={column} />,
        cell: ({ row }) => (
          <div className="flex min-w-0 flex-col">
            <span className="font-medium">{row.original.sku}</span>
            <span className="truncate text-xs text-muted-foreground" title={row.original.product_name}>
              {row.original.product_name}
            </span>
          </div>
        ),
        size: 220,
        meta: { headerTitle: 'SKU' },
      },
      {
        accessorKey: 'warehouse_code',
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          if (r.is_network && r.allocation) return <AllocationCell allocation={r.allocation} />;
          return (
            <span title={r.warehouse_name ?? undefined}>
              <span className="font-medium">{r.warehouse_code ?? EM_DASH}</span>
            </span>
          );
        },
        size: 150,
        meta: { headerTitle: 'Warehouse' },
      },
      {
        accessorKey: 'order_qty',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Order qty" column={column} />
            <HeaderInfo tip={HEADER_TIPS.order_qty} />
          </div>
        ),
        cell: ({ row }) => fmtInt(row.original.order_qty),
        size: 110,
        meta: { headerTitle: 'Order qty', ...numMeta },
      },
      {
        accessorKey: 'reorder_point',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Reorder point" column={column} />
            <span className="text-2xs text-muted-foreground">(ROP)</span>
            <HeaderInfo tip={HEADER_TIPS.reorder_point} />
          </div>
        ),
        cell: ({ row }) => fmtInt(row.original.reorder_point),
        size: 160,
        meta: { headerTitle: 'Reorder point', ...numMeta },
      },
      {
        accessorKey: 'min_qty',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Min" column={column} />
            <HeaderInfo tip={HEADER_TIPS.min} />
          </div>
        ),
        cell: ({ row }) => fmtInt(row.original.min_qty),
        size: 90,
        meta: { headerTitle: 'Min', ...numMeta },
      },
      {
        accessorKey: 'max_qty',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Max" column={column} />
            <HeaderInfo tip={HEADER_TIPS.max} />
          </div>
        ),
        cell: ({ row }) => fmtInt(row.original.max_qty),
        size: 90,
        meta: { headerTitle: 'Max', ...numMeta },
      },
      {
        accessorKey: 'order_up_to',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Order-up-to" column={column} />
            <HeaderInfo tip={HEADER_TIPS.order_up_to} />
          </div>
        ),
        cell: ({ row }) => fmtInt(row.original.order_up_to),
        size: 130,
        meta: { headerTitle: 'Order-up-to', ...numMeta },
      },
      {
        accessorKey: 'net_position',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Net" column={column} />
            <HeaderInfo tip={HEADER_TIPS.net} />
          </div>
        ),
        cell: ({ row }) => (
          <span
            className={cn(
              (row.original.net_position ?? 0) < 0 && 'text-scm-stockout',
            )}
          >
            {fmtSigned(row.original.net_position)}
          </span>
        ),
        size: 90,
        meta: { headerTitle: 'Net', ...numMeta },
      },
      {
        accessorKey: 'days_of_cover',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Runway" column={column} />
            <HeaderInfo tip={HEADER_TIPS.days_of_cover} />
          </div>
        ),
        cell: ({ row }) => fmtDoc(row.original.days_of_cover, false),
        size: 120,
        meta: { headerTitle: 'Runway', ...numMeta },
      },
      {
        accessorKey: 'reason_label',
        header: ({ column }) => <DataGridColumnHeader title="Triggered reason" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          return (
            <div className="flex min-w-0 flex-col gap-1">
              <span className="truncate text-sm" title={r.reason_label ?? undefined}>
                {r.reason_label ?? EM_DASH}
              </span>
              {r.disposition_action ? (
                <Badge variant="outline" size="sm" className="w-fit">
                  {DISPOSITION_LABEL[r.disposition_action]}
                </Badge>
              ) : null}
              {r.transfer_flag ? (
                <span
                  className="flex items-center gap-1 text-2xs text-scm-incoming"
                  title={r.transfer_flag}
                >
                  <ArrowRightLeft className="size-3 shrink-0" aria-hidden />
                  <span className="truncate">{r.transfer_flag}</span>
                </span>
              ) : null}
            </div>
          );
        },
        size: 220,
        meta: { headerTitle: 'Triggered reason' },
      },
      {
        accessorKey: 'confidence',
        header: ({ column }) => (
          <div className="inline-flex items-center gap-1">
            <DataGridColumnHeader title="Confidence" column={column} />
            <HeaderInfo tip={HEADER_TIPS.confidence} />
          </div>
        ),
        cell: ({ row }) =>
          row.original.confidence ? (
            <ConfidenceBadge
              confidence={row.original.confidence}
              sampleSize={row.original.sample_size}
            />
          ) : (
            <span className="text-muted-foreground">{EM_DASH}</span>
          ),
        size: 150,
        meta: { headerTitle: 'Confidence' },
      },
      {
        accessorKey: 'supplier',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        cell: ({ row }) => <SupplierCell rec={row.original} />,
        size: 240,
        meta: { headerTitle: 'Supplier' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const filtersActive = typeFilter ? 1 : 0;

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" />
        <span>
          Read-only recommendations - click any row to see how the numbers were reached. Accepting,
          adjusting and dismissing are not yet available.
        </span>
      </div>

      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        emptyMessage="No recommendations for this run."
        onRowClick={(row) => {
          // Blur whatever the click focused (e.g. the row's Confidence badge,
          // a focusable tooltip trigger) so its Radix tooltip doesn't linger as
          // stray text behind the popup. The modal dialog then traps focus.
          (document.activeElement as HTMLElement | null)?.blur();
          setExplainRec(row);
        }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                  placeholder="Search SKU or product..."
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: filtersActive > 0,
                activeCount: filtersActive,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label className="mb-1 block">Type</Label>
                      <SearchableSelect
                        value={typeFilter}
                        onChange={onTypeFilterChange}
                        options={TYPE_FILTER_OPTIONS}
                        placeholder="All types"
                      />
                    </div>
                    {filtersActive > 0 ? (
                      <div className="flex justify-end">
                        <Button variant="ghost" size="sm" onClick={() => onTypeFilterChange('')}>
                          Clear filters
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ),
              }}
              onRefresh={() => void refetch()}
              isRefreshing={isFetching && !isLoading}
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>

      <ReorderExplanationDialog
        rec={explainRec}
        open={!!explainRec}
        onOpenChange={(o) => !o && setExplainRec(null)}
        recs={rows}
        totalCount={data?.pagination.total ?? rows.length}
        pageItemOffset={pagination.pageIndex * pagination.pageSize}
        onNavigate={setExplainRec}
      />
    </div>
  );
}
