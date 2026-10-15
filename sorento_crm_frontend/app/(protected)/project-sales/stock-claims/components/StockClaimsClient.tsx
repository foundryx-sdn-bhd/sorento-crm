'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { ClipboardList, Handshake } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateInMalaysia } from '@/lib/helpers';
import { useAllocationClaims } from '../../_shared/hooks/useProjectAllocations';
import type {
  AllocationClaimDirection,
  AllocationClaimRow,
  AllocationClaimState,
} from '../../_shared/types/projectAllocation.types';
import { ALLOCATION_CLAIM_STATE_LABEL } from '../../_shared/types/projectAllocation.types';
import { formatQty } from '../../[projectId]/components/SalesOrderMoney';
import { PageHeader } from '@/components/common/PageHeader';
import { distinctName } from '@/lib/product-display';

const STATE_BADGE: Record<AllocationClaimState, 'warning' | 'success' | 'destructive'> = {
  requested: 'warning',
  accepted: 'success',
  refused: 'destructive',
};

const DIRECTION_OPTIONS = [
  { value: 'incoming', label: 'Lent by my projects' },
  { value: 'outgoing', label: 'Borrowed by my projects' },
  { value: 'all', label: 'Both' },
];

const STATE_OPTIONS = [
  { value: 'all', label: 'Any outcome' },
  { value: 'accepted', label: ALLOCATION_CLAIM_STATE_LABEL.accepted },
  { value: 'requested', label: ALLOCATION_CLAIM_STATE_LABEL.requested },
  { value: 'refused', label: ALLOCATION_CLAIM_STATE_LABEL.refused },
];

/**
 * The Borrow history (AC-H4). A READ.
 *
 * One project taking stock another was holding. Since Stage 1C the taking happens inside
 * the confirmation of a sales order in Fulfilment Planning, by the CS actor who confirms
 * it, and the row is written already released - so this list is the record of what moved
 * and who moved it, not an inbox with a decision left in it. Rows still reading "Waiting"
 * or "Refused" were raised before that, and they keep their answer.
 */
export function StockClaimsClient() {
  const [direction, setDirection] = React.useState<AllocationClaimDirection>('all');
  const [stateFilter, setStateFilter] = React.useState('all');
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
  } = useDebouncedSearch();
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });

  const claims = useAllocationClaims({
    direction,
    state: stateFilter === 'all' ? undefined : [stateFilter as AllocationClaimState],
    page: pagination.pageIndex + 1,
    limit: pagination.pageSize,
  });

  const all = React.useMemo(() => claims.data?.data ?? [], [claims.data]);
  const rows = React.useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((row) =>
      [row.product_code, row.from_project_code, row.to_project_code, row.warehouse_code]
        .filter(Boolean)
        .some((field) => (field as string).toLowerCase().includes(needle)),
    );
  }, [all, search]);

  const columns = React.useMemo<ColumnDef<AllocationClaimRow>[]>(
    () => [
      {
        id: 'from_project_code',
        header: ({ column }) => <DataGridColumnHeader title="Asked by" column={column} />,
        cell: ({ row }) => {
          const text = `${row.original.from_project_code}${
            row.original.from_project_cs_name ? `, ${row.original.from_project_cs_name}` : ''
          }`;
          return (
            <span className="block truncate font-medium" title={text}>
              {text}
            </span>
          );
        },
        size: 200,
        minSize: 140,
        meta: { headerTitle: 'Asked by', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'to_project_code',
        header: ({ column }) => <DataGridColumnHeader title="Held by" column={column} />,
        cell: ({ row }) => {
          const text = `${row.original.to_project_code}${
            row.original.to_project_cs_name ? `, ${row.original.to_project_cs_name}` : ''
          }`;
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
        size: 200,
        minSize: 140,
        meta: { headerTitle: 'Held by', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'product_code',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          const text = row.original.product_code || 'Not resolved';
          return (
            <span className="block truncate" title={distinctName(row.original.product_code, row.original.product_name) || text}>
              {text}
            </span>
          );
        },
        size: 170,
        minSize: 120,
        meta: { headerTitle: 'Product', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'qty',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate tabular-nums" title={row.original.qty}>
            {formatQty(row.original.qty)}
          </span>
        ),
        size: 90,
        minSize: 70,
        meta: { headerTitle: 'Qty', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      {
        id: 'warehouse_code',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        cell: ({ row }) => {
          const text = row.original.warehouse_code || 'No location';
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
        size: 130,
        minSize: 100,
        meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        id: 'delivery_date',
        header: ({ column }) => <DataGridColumnHeader title="Needed by" column={column} />,
        cell: ({ row }) =>
          row.original.delivery_date ? (
            <span className="block truncate tabular-nums">
              {formatDateInMalaysia(row.original.delivery_date)}
            </span>
          ) : (
            <span className="text-muted-foreground">No date</span>
          ),
        size: 120,
        minSize: 100,
        meta: { headerTitle: 'Needed by', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'sales_order_ref',
        header: ({ column }) => <DataGridColumnHeader title="Sales order" column={column} />,
        cell: ({ row }) => {
          const text = row.original.sales_order_ref || '-';
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
        size: 150,
        minSize: 110,
        meta: { headerTitle: 'Sales order', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'state',
        header: ({ column }) => <DataGridColumnHeader title="Outcome" column={column} />,
        cell: ({ row }) => (
          <div className="flex min-w-0 flex-col gap-0.5">
            <Badge
              variant={STATE_BADGE[row.original.state]}
              appearance="light"
              size="sm"
              className="w-fit"
            >
              {ALLOCATION_CLAIM_STATE_LABEL[row.original.state]}
            </Badge>
            {row.original.reason && (
              <span className="truncate text-xs text-muted-foreground" title={row.original.reason}>
                {row.original.reason}
              </span>
            )}
          </div>
        ),
        size: 200,
        minSize: 140,
        meta: { headerTitle: 'Outcome', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'decided_by',
        header: ({ column }) => <DataGridColumnHeader title="Decided by" column={column} />,
        // Who moved the stock and when. On a Stage 1C Borrow that is the CS actor who
        // confirmed the sales order, stamped in the same transaction.
        cell: ({ row }) => {
          if (!row.original.decided_by_name && !row.original.decided_at) {
            return <span className="text-muted-foreground">Not decided</span>;
          }
          const text = `${row.original.decided_by_name ?? 'Someone'}${
            row.original.decided_at
              ? ` on ${formatDateInMalaysia(row.original.decided_at)}`
              : ''
          }`;
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
        size: 190,
        minSize: 140,
        meta: { headerTitle: 'Decided by', skeleton: <Skeleton className="h-4 w-24" /> },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((claims.data?.total ?? 0) / pagination.pageSize) || 0,
    getRowId: (row) => row.id,
    state: { pagination },
    onPaginationChange: setPagination,
    manualPagination: true,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  const filtersActive = (direction !== 'all' ? 1 : 0) + (stateFilter !== 'all' ? 1 : 0);

  return (
    <>
      <PageHeader
        title="Stock claims"
        actions={
          <Button asChild variant="outline">
            <Link href="/project-sales/fulfilment-planning">
              <ClipboardList className="size-4" aria-hidden />
              Open Fulfilment Planning
            </Link>
          </Button>
        }
      />

      <DataGrid
        table={table}
        recordCount={claims.data?.total ?? 0}
        isLoading={claims.isLoading}
        isPlaceholderData={claims.isPlaceholderData}
        listingKey="projects.projects.view::project-stock-claims"
        tableLayout={{ width: 'fixed', columnsResizable: true }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              exportConfig={false}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  placeholder="Search project, product or location"
                  className="w-full sm:w-72"
                />
              }
              filters={{
                kind: 'custom',
                active: filtersActive > 0,
                activeCount: filtersActive,
                content: (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <p className="text-sm font-medium">Direction</p>
                      <SearchableSelect
                        value={direction}
                        onChange={(value) => {
                          setDirection(value as AllocationClaimDirection);
                          setPagination((current) => ({ ...current, pageIndex: 0 }));
                        }}
                        options={DIRECTION_OPTIONS}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-sm font-medium">Outcome</p>
                      <SearchableSelect
                        value={stateFilter}
                        onChange={(value) => {
                          setStateFilter(value);
                          setPagination((current) => ({ ...current, pageIndex: 0 }));
                        }}
                        options={STATE_OPTIONS}
                      />
                    </div>
                  </div>
                ),
              }}
              onRefresh={async () => {
                await claims.refetch();
              }}
              isRefreshing={claims.isFetching}
            />
          </CardHeader>

          <CardTable>
            {claims.isError ? (
              <div className="px-6 py-10 text-center">
                <h3 className="text-sm font-semibold text-destructive">
                  The stock claims could not be loaded
                </h3>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {claims.error instanceof Error
                    ? claims.error.message
                    : 'Try again in a moment.'}
                </p>
              </div>
            ) : !claims.isLoading && rows.length === 0 ? (
              <div className="px-6 py-10 text-center">
                <Handshake className="mx-auto size-6 text-muted-foreground" aria-hidden />
                {/* The two directions are opposite facts, so one sentence cannot be true
                    of both: "nobody has borrowed from us" is not "we have borrowed from
                    nobody". */}
                <h3 className="mt-2 text-sm font-semibold">
                  {direction === 'outgoing'
                    ? 'Your projects have borrowed nothing'
                    : direction === 'all'
                      ? 'No stock has been borrowed either way'
                      : 'Nothing has been borrowed from your projects'}
                </h3>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {direction === 'outgoing'
                    ? 'A row appears here when a Borrow on one of your sales orders is confirmed in Fulfilment Planning.'
                    : direction === 'all'
                      ? 'A row appears here when a Borrow is confirmed in Fulfilment Planning, on either side of it.'
                      : 'A row appears here when another project borrows stock one of yours is holding.'}
                </p>
              </div>
            ) : (
              <DataGridTable />
            )}
          </CardTable>

          {(claims.data?.total ?? 0) > pagination.pageSize && (
            <CardFooter>
              <DataGridPagination />
            </CardFooter>
          )}
        </Card>
      </DataGrid>
    </>
  );
}
