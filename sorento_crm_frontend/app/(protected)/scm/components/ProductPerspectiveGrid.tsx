'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  type Table as TanstackTable,
  getCoreRowModel,
  getExpandedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { ChevronDown, ChevronRight, Split } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import {
  EM_DASH,
  fmtDaysAgo,
  fmtDecimal,
  fmtDoc,
  fmtInt,
  fmtMoney,
  fmtSigned,
} from '../lib/format';
import { useNetPosition } from '../hooks/useScmDashboard';
import type { ScmFilters } from '../services/scmDashboardService';
import type { HealthState, NetPositionRow } from '../types/scm.types';
import {
  ClassChip,
  CommittedStockoutPill,
  DaysOfCoverInfo,
  HealthLegend,
  NetPositionInfo,
  StateChip,
} from './HealthIndicators';
import { DemandTrendLine } from './DemandTrendLine';
import { WarehouseBreakdownTable } from './WarehouseBreakdownTable';
import { distinctName } from '@/lib/product-display';

/** Right-aligned tabular numeric header + cell classes (the "type signature").
 *  `whitespace-nowrap` keeps numerics - money especially - on a single line
 *  (per the no-wrap-on-numerics rule); columns are sized to fit. */
const NUM = {
  headerClassName: 'text-right',
  cellClassName: 'text-right tabular-nums whitespace-nowrap',
} as const;

/** Grid row with a derived days-of-cover ∞ flag (stock on hand, but no demand). */
type M2Row = NetPositionRow & { _doc_infinite: boolean };

/** ∞ days-of-cover = there is stock (net position > 0) but no forward demand, so
 *  the cover never runs out. Deficits (net < 0) render "-", not ∞. */
function docInfinite(row: NetPositionRow): boolean {
  return (row.avg_daily_demand === null || row.avg_daily_demand === 0) && row.net_position > 0;
}

function DeferredCell() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="text-muted-foreground">{EM_DASH}</span>
      </TooltipTrigger>
      <TooltipContent>Available in a later step (needs demand data)</TooltipContent>
    </Tooltip>
  );
}

export function ProductPerspectiveGrid({
  filters,
  onToggleHealth,
}: {
  filters: ScmFilters;
  onToggleHealth: (state: HealthState) => void;
}) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);

  // Product search now lives in the shared filter bar (present in every
  // perspective), so this grid reads it from `filters.productSearch`. Health
  // (incl. overstock) + ABC/XYZ filtering + pagination are all server-side.
  const { data, isLoading, isPlaceholderData, isFetching, isError, refetch } = useNetPosition(
    filters,
    pagination,
    sorting,
    filters.productSearch,
  );

  useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [filters]);

  // Real analytics rows - only derive the ∞ days-of-cover flag for rendering.
  const rows = useMemo<M2Row[]>(
    () => (data?.data ?? []).map((r) => ({ ...r, _doc_infinite: docInfinite(r) })),
    [data],
  );

  const serverTotal = data?.pagination.total || 0;
  const recordCount = serverTotal;
  const pageCount = Math.ceil(serverTotal / pagination.pageSize);

  /**
   * The page total for one column, as the grid's own `<tfoot>`.
   *
   * A second `<table>` mirroring `getTotalSize()` and every column width was
   * one more thing to keep in step with resize, visibility and pinning, and it
   * scrolled in a box of its own. A footer on the column definition is the
   * mechanism the grid already has: same table, same cell, aligned by
   * construction.
   *
   * The rows come from the table rather than the closure, so the total follows
   * the page without the memo having to depend on the data.
   */
  const pageTotal = useCallback(
    (read: (row: M2Row) => number | null | undefined, format: (n: number) => string) =>
      function Total({ table }: { table: TanstackTable<M2Row> }) {
        const loaded = table.getRowModel().rows;
        if (loaded.length === 0) return null;
        return <>{format(loaded.reduce((acc, r) => acc + (read(r.original) ?? 0), 0))}</>;
      },
    [],
  );

  const columns = useMemo<ColumnDef<M2Row>[]>(
    () => [
      {
        id: 'expander',
        header: '',
        cell: ({ row }) => (
          <Button
            mode="icon"
            variant="ghost"
            size="sm"
            className="size-6"
            onClick={(e) => {
              e.stopPropagation();
              row.toggleExpanded();
            }}
            aria-label={row.getIsExpanded() ? 'Collapse' : 'Expand warehouses'}
          >
            {row.getIsExpanded() ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </Button>
        ),
        size: 44,
        enableHiding: false,
        enableSorting: false,
        meta: {
          expandedContent: (row: M2Row) => (
            <>
              {/* Real 12-month DO-outflow trend, fetched on expand - above the
                  per-warehouse breakdown. Product view aggregates warehouses. */}
              <DemandTrendLine
                className="border-b border-border/60 bg-muted/30 px-4 pb-2 pt-3"
                sku={row.sku}
                demandClass={row.xyz_class}
              />
              <WarehouseBreakdownTable row={row} />
            </>
          ),
        },
      },
      {
        accessorKey: 'sku',
        footer: ({ table }) => (table.getRowModel().rows.length ? 'Page total' : null),
        header: ({ column }) => <DataGridColumnHeader title="SKU" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          const attention = r.stockout_with_committed;
          return (
            <div className={cn('relative flex flex-col gap-0.5', attention && 'pl-2')}>
              {attention ? (
                <span className="absolute inset-y-0 left-0 w-0.5 rounded-full bg-scm-stockout" aria-hidden />
              ) : null}
              <span className={cn('font-medium', attention && 'font-semibold')}>{r.sku}</span>
              {distinctName(r.sku, r.product_name) ? (
                <span className="truncate text-xs text-muted-foreground" title={r.product_name}>
                  {r.product_name}
                </span>
              ) : null}
            </div>
          );
        },
        size: 240,
        meta: { headerTitle: 'SKU', skeleton: <Skeleton className="h-8 w-40" /> },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-1">
            <StateChip state={row.original.status} />
            {row.original.stockout_with_committed ? <CommittedStockoutPill /> : null}
            {row.original.imbalance ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex items-center gap-0.5 rounded-full bg-muted px-1.5 py-0.5 text-2xs text-muted-foreground">
                    <Split className="size-3" />
                    imbalance
                  </span>
                </TooltipTrigger>
                <TooltipContent>Stocked out in one warehouse, surplus in another</TooltipContent>
              </Tooltip>
            ) : null}
          </div>
        ),
        size: 210,
        enableSorting: false,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'net_position',
        footer: pageTotal((r) => r.net_position, fmtSigned),
        header: ({ column }) => (
          <div className="inline-flex items-center justify-end gap-1">
            <DataGridColumnHeader title="Net position" column={column} />
            <NetPositionInfo />
          </div>
        ),
        cell: ({ row }) => (
          <span
            className={cn('font-semibold', row.original.net_position < 0 && 'text-scm-stockout')}
          >
            {fmtSigned(row.original.net_position)}
          </span>
        ),
        size: 120,
        meta: { headerTitle: 'Net position', ...NUM },
      },
      {
        accessorKey: 'on_hand',
        footer: pageTotal((r) => r.on_hand, fmtInt),
        header: ({ column }) => <DataGridColumnHeader title="On hand" column={column} />,
        cell: ({ row }) => fmtInt(row.original.on_hand),
        size: 100,
        meta: { headerTitle: 'On hand', ...NUM },
      },
      {
        accessorKey: 'on_order',
        footer: pageTotal((r) => r.on_order, fmtInt),
        header: ({ column }) => <DataGridColumnHeader title="On order" column={column} />,
        cell: ({ row }) => fmtInt(row.original.on_order),
        size: 100,
        meta: { headerTitle: 'On order', ...NUM },
      },
      {
        accessorKey: 'committed',
        footer: pageTotal((r) => r.committed, fmtInt),
        header: ({ column }) => <DataGridColumnHeader title="Committed" column={column} />,
        cell: ({ row }) => fmtInt(row.original.committed),
        size: 100,
        meta: { headerTitle: 'Committed', ...NUM },
      },
      {
        accessorKey: 'stock_valuation',
        footer: pageTotal((r) => r.stock_valuation, fmtMoney),
        header: ({ column }) => <DataGridColumnHeader title="Stock valuation" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {row.original.stock_valuation === null ? EM_DASH : fmtMoney(row.original.stock_valuation)}
          </span>
        ),
        // Wide enough to hold the largest RM valuation on one line without wrap.
        size: 160,
        meta: { headerTitle: 'Stock valuation', ...NUM },
      },
      {
        accessorKey: 'last_movement_at',
        header: ({ column }) => <DataGridColumnHeader title="Last movement" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{fmtDaysAgo(row.original.last_movement_at)}</span>
        ),
        size: 130,
        meta: { headerTitle: 'Last movement' },
      },
      {
        id: 'avg_daily_demand',
        footer: () => <span className="font-normal text-muted-foreground">{EM_DASH}</span>,
        header: ({ column }) => <DataGridColumnHeader title="Avg daily demand" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{fmtDecimal(row.original.avg_daily_demand)}</span>
        ),
        size: 130,
        meta: { headerTitle: 'Avg daily demand', ...NUM },
      },
      {
        id: 'days_of_cover',
        footer: () => <span className="font-normal text-muted-foreground">{EM_DASH}</span>,
        header: ({ column }) => (
          <div className="inline-flex items-center justify-end gap-1">
            <DataGridColumnHeader title="Runway" column={column} />
            <DaysOfCoverInfo />
          </div>
        ),
        cell: ({ row }) => (
          <span className={cn(row.original._doc_infinite && 'text-scm-overstock')}>
            {fmtDoc(row.original.days_of_cover, row.original._doc_infinite)}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'Runway', ...NUM },
      },
      {
        // Column header is plain-language "Value"; the underlying field stays
        // `abc_class` (A/B/C) - see lib/health display maps.
        id: 'abc_class',
        footer: () => <span className="font-normal text-muted-foreground">{EM_DASH}</span>,
        header: ({ column }) => <DataGridColumnHeader title="Value" column={column} />,
        cell: ({ row }) => (
          <div className="flex justify-center">
            <ClassChip value={row.original.abc_class} kind="abc" />
          </div>
        ),
        size: 104,
        enableSorting: false,
        meta: { headerTitle: 'Value', headerClassName: 'text-center', cellClassName: 'text-center' },
      },
      {
        // Column header is plain-language "Demand"; underlying field stays
        // `xyz_class` (X/Y/Z) - see lib/health display maps.
        id: 'xyz_class',
        footer: () => <span className="font-normal text-muted-foreground">{EM_DASH}</span>,
        header: ({ column }) => <DataGridColumnHeader title="Demand" column={column} />,
        cell: ({ row }) => (
          <div className="flex justify-center">
            <ClassChip value={row.original.xyz_class} kind="xyz" />
          </div>
        ),
        size: 104,
        enableSorting: false,
        meta: { headerTitle: 'Demand', headerClassName: 'text-center', cellClassName: 'text-center' },
      },
      {
        id: 'reorder_point',
        footer: () => <span className="font-normal text-muted-foreground">{EM_DASH}</span>,
        header: ({ column }) => <DataGridColumnHeader title="Reorder point" column={column} />,
        cell: () => <DeferredCell />,
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Reorder point', ...NUM },
      },
    ],
    [pageTotal],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount,
    getRowId: (row) => row.sku,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    manualPagination: true,
    manualSorting: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <div className="space-y-3">
      <DataGrid
        table={table}
        recordCount={recordCount}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              exportConfig={{ filename: 'scm_net_position_export.xlsx' }}
              onRefresh={() => void refetch()}
              isRefreshing={isFetching && !isLoading}
            />
          </CardHeader>
          <CardTable>
            {isError ? (
              <div className="p-10 text-center text-sm text-scm-stockout">
                Couldn&apos;t load net position. Retry from the toolbar.
              </div>
            ) : (
              <DataGridTable />
            )}
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>
      <HealthLegend
        className="px-1"
        activeState={filters.healthStatus}
        onToggle={onToggleHealth}
      />
    </div>
  );
}

