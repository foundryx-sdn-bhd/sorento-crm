'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { Skeleton } from '@/components/ui/skeleton';
import { AskCard } from '@/components/stock-asks/AskCard';
import { AskDoneByCell, AskedAtCell, AskNoteCell, AskStateCell } from '@/components/stock-asks/AskEditCells';
import type { ListBoardViewMode } from '@/hooks/useListBoardViewPreference';
import { toast } from '@/lib/toast';
import {
  BRANCH_LABEL,
  BRANCH_VARIANT,
  notifiedLabel,
  type StockAsk,
  type StockAskPage,
  type StockAskPatch,
} from '@/lib/stock-asks';
import {
  NotASalesAgentError,
  listCustomerAsks,
  updateCustomerAsk,
} from '../lib/customer-asks-service';
import { codeWithName } from '@/lib/product-display';

/**
 * Sales-asks-todo S1: the "Show done" history under the portal to-do. It is the #1333 list
 * (paged) fixed to `state=done`; the to-do above owns everything still open. Reloads when the
 * to-do writes (`refreshKey`), so a Reopen leaves this list at once. ASKS-UX item 2: it follows
 * the landing's view, the grid in List and the to-do's own `AskCard`s in Cards (a card opens the
 * conversation, its Reopen goes through the to-do so both lists move); the DataGrid provider
 * stays around both so the pager is one component.
 */
export function CustomerAsksHistory({
  search,
  refreshKey,
  view,
  onOpen,
  onReopen,
  pendingAskId = null,
}: {
  search: string;
  refreshKey: number;
  view: ListBoardViewMode;
  onOpen: (ask: StockAsk) => void;
  onReopen: (askId: string) => void;
  pendingAskId?: string | null;
}) {
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 20 });
  const [page, setPage] = useState<StockAskPage | null>(null);
  const [loading, setLoading] = useState(true);
  const q = search.trim();

  // A new search starts again from page 1.
  useEffect(() => {
    setPagination((p) => (p.pageIndex === 0 ? p : { ...p, pageIndex: 0 }));
  }, [q]);

  const load = useCallback(() => {
    setLoading(true);
    return listCustomerAsks({
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
      q,
      state: 'done',
    })
      .then((data) => setPage(data))
      .catch((error: unknown) => {
        if (!(error instanceof NotASalesAgentError)) {
          toast.error(error instanceof Error ? error.message : 'Failed to load customer asks');
        }
      })
      .finally(() => setLoading(false));
    // refreshKey is a reload trigger, not a value the callback reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pagination.pageIndex, pagination.pageSize, q, refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  const save = useCallback((askId: string, patch: StockAskPatch) => {
    updateCustomerAsk(askId, patch)
      .then((updated) => {
        setPage((prev) =>
          prev ? { ...prev, data: prev.data.map((r) => (r.id === updated.id ? updated : r)) } : prev,
        );
        toast.success('Ask updated');
      })
      .catch((error: unknown) =>
        toast.error(error instanceof Error ? error.message : 'Failed to update the ask'),
      );
  }, []);

  const rows = page?.data ?? [];
  const total = page?.pagination?.total ?? 0;

  const columns = useMemo<ColumnDef<StockAsk>[]>(
    () => [
      {
        id: 'created_at',
        header: 'Asked at',
        size: 215,
        cell: ({ row }) => <AskedAtCell ask={row.original} />,
      },
      {
        id: 'customer_name',
        header: 'Customer',
        size: 170,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_name ?? undefined}>
            {row.original.customer_name || '-'}
          </span>
        ),
      },
      {
        id: 'contact_name',
        header: 'Contact',
        size: 130,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.contact_name ?? undefined}>
            {row.original.contact_name || '-'}
          </span>
        ),
      },
      {
        id: 'product_code',
        header: 'Product',
        size: 140,
        cell: ({ row }) => (
          <span className="block truncate" title={codeWithName(row.original.product_code, row.original.product_name)}>
            {row.original.product_code}
          </span>
        ),
      },
      {
        id: 'quantity',
        header: 'Qty',
        size: 60,
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
        size: 280,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.answer_summary}>
            {row.original.answer_summary}
          </span>
        ),
      },
      {
        id: 'notified_agent',
        header: 'Notified',
        size: 100,
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
        size: 110,
        cell: ({ row }) => (
          <AskStateCell ask={row.original} editable onSave={(patch) => save(row.original.id, patch)} />
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
        size: 220,
        cell: ({ row }) => (
          <AskNoteCell ask={row.original} editable onSave={(patch) => save(row.original.id, patch)} />
        ),
      },
    ],
    [save],
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

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={loading && !page}
      // The portal has no CRM session to keep column preferences under.
      listingKey={null}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
    >
      {view === 'board' ? (
        <section aria-labelledby="ask-history-done" className="space-y-2">
          <h2 id="ask-history-done" className="text-sm font-semibold">
            Done
          </h2>
          {loading && !page ? (
            <div className="space-y-2" role="status" aria-label="Loading">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : rows.length === 0 ? (
            <p className="rounded-lg border px-6 py-8 text-center text-sm text-muted-foreground">No done asks yet</p>
          ) : (
            <ul className="space-y-2.5">
              {rows.map((ask) => (
                <li key={ask.id}>
                  <AskCard
                    ask={ask}
                    pending={pendingAskId === ask.id}
                    onOpen={onOpen}
                    onReopen={onReopen}
                  />
                </li>
              ))}
            </ul>
          )}
          {/* Also past page 1: a Reopen that empties the last page must leave a way back. */}
          {total > pagination.pageSize || pagination.pageIndex > 0 ? (
            <div className="flex justify-between border-t pt-3">
              <DataGridPagination />
            </div>
          ) : null}
        </section>
      ) : (
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
      )}
    </DataGrid>
  );
}
