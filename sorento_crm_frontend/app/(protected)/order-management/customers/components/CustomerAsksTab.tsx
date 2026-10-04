'use client';

import { useMemo, useState } from 'react';
import {
  type ColumnDef,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { MessageSquareText } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { AskDoneByCell, AskedAtCell, AskNoteCell, AskStateCell } from '@/components/stock-asks/AskEditCells';
import { useHasPermission } from '@/hooks/usePermissions';
import { BRANCH_LABEL, BRANCH_VARIANT, notifiedLabel, type StockAsk } from '@/lib/stock-asks';
import { useCustomerAsksQuery, useUpdateAskMutation } from '../hooks/useCustomerAsks';

/**
 * Chatbot stock ask v2 S5 (R9): every stock ask the chatbot answered for this customer's
 * contacts. The office works State and Note in place with `order_management.customers.edit`;
 * without it the same cells read as values.
 */
export function CustomerAsksTab({ customerId }: { customerId: string }) {
  const canEdit = useHasPermission('order_management.customers.edit');
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 20 });
  const { data, isLoading, isPlaceholderData, error, refetch } = useCustomerAsksQuery(customerId, pagination);
  const { mutate: updateAsk } = useUpdateAskMutation(customerId);
  const rows = data?.data ?? [];
  const total = data?.pagination?.total ?? 0;

  const columns = useMemo<ColumnDef<StockAsk>[]>(
    () => [
      {
        id: 'created_at',
        header: 'Asked at',
        size: 215,
        cell: ({ row }) => <AskedAtCell ask={row.original} />,
      },
      {
        id: 'contact_name',
        header: 'Contact',
        size: 150,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.contact_name ?? undefined}>
            {row.original.contact_name || '-'}
          </span>
        ),
      },
      {
        id: 'product_code',
        header: 'Product',
        size: 150,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.product_name ?? row.original.product_code}>
            {row.original.product_code}
          </span>
        ),
      },
      {
        id: 'quantity',
        header: 'Qty',
        size: 70,
        cell: ({ row }) => <span className="tabular-nums">{row.original.quantity ?? '-'}</span>,
      },
      {
        id: 'branch',
        header: 'Branch',
        size: 185,
        cell: ({ row }) => (
          <Badge variant={BRANCH_VARIANT[row.original.branch] ?? 'secondary'} appearance="light">
            {BRANCH_LABEL[row.original.branch] ?? row.original.branch}
          </Badge>
        ),
      },
      {
        id: 'answer_summary',
        header: 'Answer',
        size: 320,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.answer_summary}>
            {row.original.answer_summary}
          </span>
        ),
      },
      {
        id: 'notified_agent',
        header: 'Notified',
        size: 110,
        cell: ({ row }) => {
          const n = notifiedLabel(row.original);
          return (
            <Badge variant={n.variant} appearance="light" title={n.title}>
              {n.label}
            </Badge>
          );
        },
      },
      {
        id: 'state',
        header: 'State',
        size: 120,
        cell: ({ row }) => (
          <AskStateCell
            ask={row.original}
            editable={canEdit}
            onSave={(patch) => updateAsk({ askId: row.original.id, patch })}
          />
        ),
      },
      {
        id: 'done_by',
        header: 'Done by',
        size: 240,
        cell: ({ row }) => <AskDoneByCell ask={row.original} />,
      },
      {
        id: 'note',
        header: 'Note',
        size: 240,
        cell: ({ row }) => (
          <AskNoteCell
            ask={row.original}
            editable={canEdit}
            onSave={(patch) => updateAsk({ askId: row.original.id, patch })}
          />
        ),
      },
    ],
    [canEdit, updateAsk],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { pagination },
    onPaginationChange: (updater) =>
      setPagination((prev) => (typeof updater === 'function' ? updater(prev) : updater)),
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    manualPagination: true,
    pageCount: Math.ceil(total / pagination.pageSize) || 1,
    columnResizeMode: 'onChange',
  });

  if (!isLoading && total === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <MessageSquareText className="size-8 text-muted-foreground" />
          <p className="font-medium">No stock asks yet</p>
          <p className="text-sm text-muted-foreground">
            Stock questions this customer&apos;s contacts ask the WhatsApp chatbot are listed here.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      listingKey="order_management.customers.view::stock_asks"
      tableLayout={{ width: 'fixed', columnsResizable: true }}
    >
      <Card>
        <CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </CardHeader>
        <CardFooter className="flex justify-between border-t px-4 py-3">
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}
