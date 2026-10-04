'use client';

import { useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import PromotionTypeFormModal from './PromotionTypeFormModal';
import { usePromotionTypes } from '../hooks/usePromotionTypes';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import type { PromotionType } from '../types/promotionType.types';

/** "PP Promo · until end of year" - the rule in the words an admin set it in. */
function expiryRuleLabel(row: PromotionType): string {
  if (!row.show_expired) return 'Not served once expired';
  const bounds: string[] = [];
  if (row.expired_valid_until_year_end) bounds.push('until end of year');
  if (row.expired_max_age_days != null) bounds.push(`within ${row.expired_max_age_days} days`);
  return bounds.length ? `Still applies ${bounds.join(' and ')}` : 'Still applies, no time limit';
}

export default function PromotionTypesList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<PromotionType | null>(null);

  const { data, isLoading, refetch, isFetching, error } = usePromotionTypes();
  // Delete asks nothing (D7): the row dims and a toast counts down with Cancel.
  // Promotions of a deleted type become unclassified and follow the default
  // type, which is what the server does whichever way the delete arrives.
  const deletion = useDeferredRowAction({
    actionKey: 'promotion_type.delete',
    entityType: 'promotion_type',
    successMessage: 'Promotion type deleted',
    invalidateKeys: [['promotion-types'], ['promotions']],
  });
  const rowPending = useRowPending<PromotionType>('promotion_type');
  const rows = data?.data ?? [];

  const columns = useMemo<ColumnDef<PromotionType>[]>(
    () => [
      {
        accessorKey: 'type_name',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => (
          <div className="flex items-center gap-2 truncate" title={row.original.type_name}>
            <span className="truncate">{row.original.type_name}</span>
            {row.original.is_default && (
              <Badge variant="secondary">
                Default
              </Badge>
            )}
          </div>
        ),
        size: 200,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'type_code',
        header: ({ column }) => <DataGridColumnHeader title="Code" column={column} />,
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.type_code}</span>
        ),
        size: 120,
        meta: { headerTitle: 'Code', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        id: 'show_expired',
        accessorFn: (row) => (row.show_expired ? 1 : 0),
        header: ({ column }) => <DataGridColumnHeader title="When expired" column={column} />,
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <Badge variant={row.original.show_expired ? 'success' : 'destructive'}>
              <BadgeDot />
              {row.original.show_expired ? 'Still served' : 'Not served'}
            </Badge>
          </div>
        ),
        size: 140,
        meta: { headerTitle: 'When expired' },
      },
      {
        id: 'expiry_rule',
        accessorFn: (row) => expiryRuleLabel(row),
        header: ({ column }) => <DataGridColumnHeader title="Rule" column={column} />,
        cell: ({ row }) => (
          <div className="truncate" title={expiryRuleLabel(row.original)}>
            {expiryRuleLabel(row.original)}
          </div>
        ),
        size: 240,
        meta: { headerTitle: 'Rule', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        id: 'match_markers',
        accessorFn: (row) => (row.match_markers || []).join(', '),
        header: ({ column }) => <DataGridColumnHeader title="File-name markers" column={column} />,
        cell: ({ row }) => {
          const markers = (row.original.match_markers || []).join(', ');
          return (
            <div className="truncate" title={markers || 'No marker (default type)'}>
              {markers || '-'}
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'File-name markers', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'promotions_count',
        header: ({ column }) => <DataGridColumnHeader title="Promotions" column={column} />,
        cell: ({ row }) => <span>{row.original.promotions_count ?? 0}</span>,
        size: 110,
        meta: { headerTitle: 'Promotions' },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-1">
            <Button
              mode="icon"
              variant="ghost"
              aria-label={`Edit ${row.original.type_name}`}
              onClick={() => {
                setEditing(row.original);
                setFormOpen(true);
              }}
            >
              <Pencil className="size-4" />
            </Button>
            <Button
              mode="icon"
              variant="ghost"
              aria-label={`Delete ${row.original.type_name}`}
              onClick={() =>
                deletion.run({
                  id: row.original.id,
                  subject: row.original.type_name,
                })
              }
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ),
        size: 90,
        enableHiding: false,
      },
    ],
    [deletion],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(rows.length / pagination.pageSize) || 1,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setEditing(null);
        setFormOpen(true);
      }}
    >
      <Plus />
      Add Promotion Type
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      rowPending={rowPending}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      emptyMessage="No promotion types yet. Add one to control what happens to a promotion after it ends."
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>

      <PromotionTypeFormModal
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) setEditing(null);
        }}
        promotionType={editing}
      />
    </DataGrid>
  );
}
