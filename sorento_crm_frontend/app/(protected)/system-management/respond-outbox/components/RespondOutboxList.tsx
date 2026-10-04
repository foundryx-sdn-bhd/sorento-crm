'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { Eye } from 'lucide-react';
import { useRespondOutbox } from '../hooks/useRespondOutbox';
import type { RespondOutboxRow } from '../types/respondOutbox.types';

const BUSINESS_LABELS: Record<string, string> = {
  complaints: 'Complaint',
  stock_inquiries: 'Stock Inquiry',
  purchase_requests: 'Purchase Request',
  sponsorship_forms: 'Sponsorship Form',
};

export default function RespondOutboxList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [status, setStatus] = useState<string>('__all__');
  const {
    value: queryInput,
    setValue: setQueryInput,
    debouncedValue: query,
    isSettling: querySettling,
  } = useDebouncedSearch();
  const [detail, setDetail] = useState<RespondOutboxRow | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [query]);

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = useRespondOutbox({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    status: status === '__all__' ? undefined : status,
    query: query || undefined,
  });

  const rows = data?.data ?? [];
  const total = data?.pagination?.total ?? 0;

  const columns = useMemo<ColumnDef<RespondOutboxRow>[]>(
    () => [
      buildSelectColumn<RespondOutboxRow>(),
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Sent At" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.created_at),
        size: 160,
        meta: { headerTitle: 'Sent At', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'contact',
        header: ({ column }) => <DataGridColumnHeader title="Contact" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          return (
            <div className="min-w-0">
              <p className="truncate text-sm font-medium" title={r.contact_name ?? undefined}>
                {r.contact_name ?? r.contact_phone ?? r.contact_identifier ?? '-'}
              </p>
              {r.contact_phone && (
                <p className="text-xs text-muted-foreground">{r.contact_phone}</p>
              )}
            </div>
          );
        },
        size: 180,
        meta: { headerTitle: 'Contact' },
      },
      {
        accessorKey: 'sent_as',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.sent_as === 'template' ? 'secondary' : 'outline'}>
            {row.original.sent_as === 'template' ? 'Template' : 'Text'}
          </Badge>
        ),
        size: 90,
        meta: { headerTitle: 'Type' },
      },
      {
        id: 'message',
        header: ({ column }) => <DataGridColumnHeader title="Message / Template" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          // Template rows: show the rendered message (variables filled), with the
          // template name as a secondary label. Text rows: the message text.
          if (r.sent_as === 'template') {
            const filled = r.message_text;
            return (
              <div className="max-w-[360px]">
                {r.template_name && (
                  <span className="block truncate text-xs text-muted-foreground" title={r.template_name}>
                    {r.template_name}
                  </span>
                )}
                <span className="block truncate text-sm" title={filled ?? undefined}>
                  {filled ?? (r.template_name ? '' : '-')}
                </span>
              </div>
            );
          }
          return (
            <span className="block max-w-[360px] truncate text-sm" title={r.message_text ?? undefined}>
              {r.message_text ?? '-'}
            </span>
          );
        },
        size: 380,
        meta: { headerTitle: 'Message / Template' },
      },
      {
        accessorKey: 'business_table',
        header: ({ column }) => <DataGridColumnHeader title="Linked" column={column} />,
        cell: ({ row }) =>
          row.original.business_table
            ? BUSINESS_LABELS[row.original.business_table] ?? row.original.business_table
            : '-',
        size: 130,
        meta: { headerTitle: 'Linked' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <Badge status={row.original.status}>{row.original.status}</Badge>
            {row.original.status_code ? (
              <span className="text-xs text-muted-foreground">{row.original.status_code}</span>
            ) : null}
          </div>
        ),
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => (
          <Button variant="ghost" size="sm" onClick={() => setDetail(row.original)}>
            <Eye className="size-4" />
          </Button>
        ),
        size: 60,
        enableHiding: false,
      },
    ],
    [],
  );

  const table = useReactTable({
    data: rows,
    columns,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    manualPagination: true,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  return (
    <>
      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading || isFetching}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        tableClassNames={{ edgeCell: 'px-4' }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={queryInput}
                  onChange={setQueryInput}
                  isSettling={isSearchInFlight(querySettling, isFetching, query)}
                  placeholder="Search message or contact…"
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: status !== '__all__',
                activeCount: status !== '__all__' ? 1 : 0,
                content: (
                  <div className="space-y-1.5">
                    <Label>Status</Label>
                    <SearchableSelect
                      value={status}
                      onChange={(v) => {
                        setStatus(v);
                        setPagination((p) => ({ ...p, pageIndex: 0 }));
                      }}
                      options={[
                        { value: '__all__', label: 'All statuses' },
                        { value: 'success', label: 'Success' },
                        { value: 'failed', label: 'Failed' },
                        { value: 'pending', label: 'Pending' },
                        { value: 'processing', label: 'Processing' },
                      ]}
                      placeholder="Status"
                      triggerClassName="w-full"
                    />
                  </div>
                ),
              }}
              exportConfig={{ filename: 'respond_outbox_export.xlsx' }}
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

      <Dialog open={!!detail} onOpenChange={(o) => !o && setDetail(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Outgoing Respond message</DialogTitle>
          </DialogHeader>
          {detail && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <Field label="Sent at" value={formatDateTimeInMalaysia(detail.created_at)} />
                <Field label="Status" value={`${detail.status}${detail.status_code ? ` (${detail.status_code})` : ''}`} />
                <Field label="Contact" value={detail.contact_name ?? detail.contact_phone ?? detail.contact_identifier ?? '-'} />
                <Field label="Type" value={detail.sent_as === 'template' ? `Template: ${detail.template_name ?? ''}` : 'Text'} />
                <Field label="Linked" value={detail.business_table ? `${BUSINESS_LABELS[detail.business_table] ?? detail.business_table} · ${detail.business_id ?? ''}` : '-'} />
              </div>
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">Message</p>
                <pre className="whitespace-pre-wrap rounded-md border bg-muted/40 p-3 text-xs">
                  {detail.message_text ?? detail.template_name ?? '-'}
                </pre>
              </div>
              {detail.button_url && (
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Button link</p>
                  <pre className="whitespace-pre-wrap break-all rounded-md border bg-muted/40 p-3 text-xs">
                    {detail.button_url}
                  </pre>
                </div>
              )}
              {detail.error_message && (
                <div>
                  <p className="mb-1 text-xs font-medium text-destructive">Error</p>
                  <pre className="whitespace-pre-wrap rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
                    {detail.error_message}
                  </pre>
                </div>
              )}
              {detail.response_payload && (
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Respond.io response</p>
                  <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-3 text-xs">
                    {detail.response_payload}
                  </pre>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="break-words">{value}</p>
    </div>
  );
}
