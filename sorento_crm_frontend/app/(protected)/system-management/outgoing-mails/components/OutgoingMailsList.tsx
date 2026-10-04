'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { sanitizedHtml } from '@/lib/sanitize';
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
import { useOutgoingMails } from '../hooks/useOutgoingMails';
import type { OutgoingMail } from '../types/outgoingMail.types';

function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
}

/** Split plain text on http(s) URLs and render anchors so the Outgoing Mails preview is clickable. */
function linkifyPlainText(text: string): ReactNode {
  const parts = text.split(/(https?:\/\/[^\s<]+)/gi);
  return parts.map((part, i) => {
    if (!/^https?:\/\//i.test(part)) {
      return <span key={i}>{part}</span>;
    }
    let href = part;
    let suffix = '';
    const trailing = part.match(/[.,;:!?]+$/);
    if (trailing) {
      const t = trailing[0];
      const candidate = part.slice(0, -t.length);
      if (candidate.length > 'https://x'.length && /^https?:\/\/\S+$/i.test(candidate)) {
        href = candidate;
        suffix = t;
      }
    }
    return (
      <span key={i}>
        <a
          href={href}
          className="text-primary underline font-medium break-all"
          target="_blank"
          rel="noopener noreferrer"
        >
          {href}
        </a>
        {suffix}
      </span>
    );
  });
}

export default function OutgoingMailsList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [status, setStatus] = useState<string>('__all__');
  const {
    value: queryInput,
    setValue: setQueryInput,
    debouncedValue: query,
    isSettling: querySettling,
  } = useDebouncedSearch();
  const [contentMail, setContentMail] = useState<OutgoingMail | null>(null);
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

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useOutgoingMails({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    status: status === '__all__' ? undefined : status,
    query: query || undefined,
  });

  const columns = useMemo<ColumnDef<OutgoingMail>[]>(
    () => [
      buildSelectColumn<OutgoingMail>(),
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Queued At" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.created_at),
        size: 180,
        meta: { headerTitle: 'Queued At', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'to_email',
        header: ({ column }) => <DataGridColumnHeader title="To" column={column} />,
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.to_email || '-'}</span>
        ),
        size: 220,
        meta: { headerTitle: 'To' },
      },
      {
        accessorKey: 'subject',
        header: ({ column }) => <DataGridColumnHeader title="Subject" column={column} />,
        cell: ({ row }) => (
          <span title={row.original.subject || ''} className="truncate block max-w-[420px]">
            {row.original.subject || '-'}
          </span>
        ),
        size: 420,
        meta: { headerTitle: 'Subject' },
      },
      {
        id: 'content',
        header: ({ column }) => <DataGridColumnHeader title="Content" column={column} />,
        cell: ({ row }) => {
          const mail = row.original;
          const body = mail.body;
          const preview = body
            ? (body.startsWith('<') ? stripHtml(body) : body).slice(0, 80)
            : '';
          const hasContent = !!body;
          return (
            <div className="flex items-center gap-2 max-w-[280px]">
              <span className="truncate text-muted-foreground text-xs" title={preview}>
                {preview ? `${preview}${preview.length >= 80 ? '…' : ''}` : '-'}
              </span>
              {hasContent && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="shrink-0 h-8 w-8"
                  aria-label="View full content"
                  onClick={(e) => {
                    e.stopPropagation();
                    setContentMail(mail);
                  }}
                  title="View full content"
                >
                  <Eye className="size-4" />
                </Button>
              )}
            </div>
          );
        },
        size: 320,
        meta: { headerTitle: 'Content' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <Badge status={row.original.status}>
            {row.original.status}
          </Badge>
        ),
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'sent_at',
        header: ({ column }) => <DataGridColumnHeader title="Sent At" column={column} />,
        cell: ({ row }) =>
          row.original.sent_at ? formatDateTimeInMalaysia(row.original.sent_at) : '-',
        size: 180,
        meta: { headerTitle: 'Sent At' },
      },
      {
        accessorKey: 'error_message',
        header: ({ column }) => <DataGridColumnHeader title="Error" column={column} />,
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground" title={row.original.error_message || ''}>
            {row.original.error_message || '-'}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Error' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
  });

  return (
    <DataGrid table={table} recordCount={data?.pagination.total || 0} isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      tableLayout={{ columnsVisibility: true }}
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
                placeholder="Search by email or subject..."
                className="w-72"
              />
            }
            filters={{
              kind: 'custom',
              active: status !== '__all__',
              activeCount: status !== '__all__' ? 1 : 0,
              content: (
                <div className="space-y-3">
                  <SearchableSelect
                    value={status}
                    onChange={setStatus}
                    options={[
                      { value: '__all__', label: 'All statuses' },
                      { value: 'pending', label: 'Pending' },
                      { value: 'sent', label: 'Sent' },
                      { value: 'failed', label: 'Failed' },
                    ]}
                    placeholder="Status"
                  />
                  {status !== '__all__' && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full"
                      onClick={() => setStatus('__all__')}
                    >
                      Clear filters
                    </Button>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'outgoing_mails_export.xlsx' }}
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
      <Dialog open={!!contentMail} onOpenChange={(open) => !open && setContentMail(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>Email content</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto rounded border bg-muted/30 p-4 text-sm">
            {contentMail?.body?.startsWith('<') ? (
              <div
                className="prose prose-sm dark:prose-invert max-w-none"
                dangerouslySetInnerHTML={sanitizedHtml(contentMail.body)}
              />
            ) : (
              <div className="whitespace-pre-wrap break-words font-sans">
                {contentMail?.body ? linkifyPlainText(contentMail.body) : '-'}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </DataGrid>
  );
}

