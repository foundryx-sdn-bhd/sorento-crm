'use client';

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import {
  type ColumnDef,
  type PaginationState,
  type SortingState,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Download, Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardFooter, CardTable } from '@/components/ui/card';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { buildGroupHeader } from './groupHeader';
import { getChatMessages } from './services/chatHistoryService';
import { useExportChatHistory } from './hooks/useChatHistory';
import { useFailedChatbotContacts } from './hooks/useChatbotTurns';
import { stageLabel } from './turnPresentation';
import { ChatThreadDrawer } from './components/ChatThreadDrawer';
import { TurnDetailDrawer } from './components/TurnDetailDrawer';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import type {
  ChatHistoryFilters,
  ChatHistoryGroupBy,
  ChatMessageRow,
} from './types/chatHistory.types';

function localInput(offsetHours: number): string {
  const d = new Date(Date.now() - offsetHours * 3600_000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const GROUP_BY_OPTIONS = [
  { value: 'none', label: 'No grouping' },
  { value: 'date', label: 'Group by date' },
  { value: 'contact', label: 'Group by contact' },
  { value: 'contact_date', label: 'Group by contact, then date' },
];

const DIRECTION_OPTIONS = [
  { value: '', label: 'All directions' },
  { value: 'incoming', label: 'Incoming' },
  { value: 'outgoing', label: 'Outgoing' },
];

function LatencyCell({ seconds }: { seconds: number | null }) {
  if (seconds == null) return <span className="text-muted-foreground"> - </span>;
  const variant = seconds > 30 ? 'destructive' : seconds > 10 ? 'warning' : 'success';
  return (
    <Badge variant={variant as never}>
      {seconds < 1 ? `${Math.round(seconds * 1000)}ms` : `${seconds.toFixed(1)}s`}
    </Badge>
  );
}

export default function ChatHistoryPage() {
  // Default to the last 24h - an unbounded scan of this table is never the intent.
  const [dateFrom, setDateFrom] = useState(() => localInput(24));
  const [dateTo, setDateTo] = useState(() => localInput(0));
  const [direction, setDirection] = useState('');
  const [breachedOnly, setBreachedOnly] = useState(false);
  // AC-255. Narrows the LIST to contacts whose chatbot turns failed in this range.
  const [failedTurnsOnly, setFailedTurnsOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [groupBy, setGroupBy] = useState<ChatHistoryGroupBy>('none');

  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'sent_at', desc: true }]);
  const [selected, setSelected] = useState<ChatMessageRow | null>(null);

  // The Chatbot Console's own "trace" link lands here as `?turn=<id>`. The list below is
  // the WhatsApp message table and a console turn never writes a row in it, so the turn
  // is opened by id straight into its own drawer instead of being looked for in a list
  // that can never hold it (browser pass 2, step 3).
  const searchParams = useSearchParams();
  const linkedTurnId = searchParams.get('turn');
  const [tracedTurnId, setTracedTurnId] = useState<string | null>(linkedTurnId);
  useEffect(() => {
    setTracedTurnId(linkedTurnId);
  }, [linkedTurnId]);

  const range: ChatHistoryFilters = useMemo(
    () => ({
      date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
      date_to: dateTo ? new Date(dateTo).toISOString() : undefined,
      direction: (direction || undefined) as ChatHistoryFilters['direction'],
      breached_only: breachedOnly || undefined,
    }),
    [dateFrom, dateTo, direction, breachedOnly],
  );

  // AC-255. Only fetched when the filter is on: an aggregate over the whole turn table
  // behind a toggle most operators never touch would be a page-load cost for nothing.
  const {
    byContactId: failedByContact,
    contactIds: failedContactIds,
    isLoading: failedLoading,
    isSuccess: failedLoaded,
  } = useFailedChatbotContacts({ from: range.date_from, to: range.date_to }, failedTurnsOnly);

  // AC-255. The turn aggregate names the contacts; the LIST is then narrowed on the
  // SERVER by sending them back as repeated `contact_id`. Filtering the fetched page in
  // the browser instead left the pager counting rows it had just hidden and the empty
  // state claiming nothing failed when the failures were simply on page two.
  const filters: ChatHistoryFilters = useMemo(
    () => (failedTurnsOnly ? { ...range, contact_id: failedContactIds } : range),
    [range, failedTurnsOnly, failedContactIds],
  );

  // With the filter on there is nothing to ask for until the aggregate has answered, and
  // an empty answer means an empty list - NOT an unfiltered one, which is what sending no
  // `contact_id` would mean to the endpoint.
  const listEnabled = !failedTurnsOnly || (failedLoaded && failedContactIds.length > 0);

  const { data, isLoading, isPlaceholderData, error, refetch } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['chat-history', filters, searchQuery, pagination, sorting, groupBy],
    queryFn: () =>
      getChatMessages(filters, {
        page: pagination.pageIndex + 1,
        limit: pagination.pageSize,
        sort: sorting[0]?.id,
        dir: sorting[0]?.desc ? 'desc' : 'asc',
        query: searchQuery || undefined,
        group_by: groupBy === 'none' ? undefined : groupBy,
      }),
    enabled: listEnabled,
    staleTime: 15_000,
  });

  const exportMutation = useExportChatHistory();

  const resetPage = () => setPagination((p) => ({ ...p, pageIndex: 0 }));

  const rows = useMemo(() => (listEnabled ? (data?.data ?? []) : []), [data, listEnabled]);

  const columns = useMemo<ColumnDef<ChatMessageRow>[]>(
    () => [
      {
        accessorKey: 'sent_at',
        id: 'sent_at',
        header: ({ column }) => <DataGridColumnHeader title="Time" column={column} />,
        cell: ({ row }) => (
          <span className="whitespace-nowrap">{formatDateTimeInMalaysia(row.original.sent_at)}</span>
        ),
        size: 165,
      },
      {
        accessorKey: 'contact_display',
        id: 'contact_display',
        header: ({ column }) => <DataGridColumnHeader title="Contact" column={column} />,
        cell: ({ row }) => {
          const failed = failedByContact.get(row.original.contact_id);
          return (
            <div className="min-w-0">
              <span className="truncate block" title={row.original.contact_display}>
                {row.original.contact_display}
              </span>
              {failed && (
                // AC-255: the row says what stopped last, so the list itself answers
                // "which of these is worth opening" without opening any of them.
                <Badge variant="destructive" appearance="light" size="sm" className="mt-0.5">
                  failed at {stageLabel(failed.last_failed_stage ?? 'received').toLowerCase()}
                  {failed.count > 1 ? ` (${failed.count})` : ''}
                </Badge>
              )}
            </div>
          );
        },
        size: 210,
      },
      {
        accessorKey: 'type',
        id: 'type',
        header: ({ column }) => <DataGridColumnHeader title="Direction" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.type === 'incoming' ? 'secondary' : 'outline'}>
            {row.original.type}
          </Badge>
        ),
        size: 110,
      },
      {
        accessorKey: 'queue_ticket',
        id: 'queue_ticket',
        enableSorting: false,
        header: ({ column }) => <DataGridColumnHeader title="Queue" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {row.original.queue_ticket != null ? `#${row.original.queue_ticket}` : '-'}
          </span>
        ),
        size: 80,
      },
      {
        accessorKey: 'message',
        id: 'message',
        enableSorting: false,
        header: ({ column }) => <DataGridColumnHeader title="Message" column={column} />,
        cell: ({ row }) => (
          <span className="truncate block" title={row.original.message}>
            {row.original.message}
          </span>
        ),
        size: 640,
      },
      {
        accessorKey: 'latency_seconds',
        id: 'latency_seconds',
        enableSorting: false,
        header: ({ column }) => <DataGridColumnHeader title="Latency" column={column} />,
        cell: ({ row }) => <LatencyCell seconds={row.original.latency_seconds} />,
        size: 100,
      },
      {
        accessorKey: 'delivery_status',
        id: 'delivery_status',
        enableSorting: false,
        header: ({ column }) => <DataGridColumnHeader title="Delivery" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{row.original.delivery_status ?? '-'}</span>
        ),
        size: 110,
      },
    ],
    // `failedByContact` is read by the Contact cell (AC-255's last-failed-stage badge).
    // Without it here the columns memo would keep the first, empty map and the badge
    // would never appear - the filter would look like it silently did nothing.
    [failedByContact],
  );

  const renderGroupHeader = useMemo(() => buildGroupHeader(groupBy), [groupBy]);

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((listEnabled ? (data?.pagination.total ?? 0) : 0) / pagination.pageSize),
    getRowId: (row) => String(row.id),
    state: { pagination, sorting },
    columnResizeMode: 'onChange',
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
  });

  const filtersActive = (direction ? 1 : 0) + (breachedOnly ? 1 : 0) + (failedTurnsOnly ? 1 : 0);

  const GridToolbar = () => {
    const [inputValue, setInputValue] = useState(searchQuery);
    const applySearch = () => {
      setSearchQuery(inputValue);
      resetPage();
    };
    return (
      <div className="p-4">
        <DataGridListToolbar
          table={table}
          searchSlot={
            <div className="relative">
              <Search className="size-4 text-muted-foreground absolute start-3 top-1/2 -translate-y-1/2" />
              <Input
                placeholder="Message, phone, or name"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applySearch()}
                className="ps-9 w-full sm:w-64"
              />
              {searchQuery.length > 0 && (
                <Button
                  mode="icon"
                  variant="dim"
                  className="absolute end-1.5 top-1/2 -translate-y-1/2 h-6 w-6"
                  onClick={() => {
                    setInputValue('');
                    setSearchQuery('');
                    resetPage();
                  }}
                  aria-label="Clear search"
                >
                  <X />
                </Button>
              )}
            </div>
          }
          filters={{
            kind: 'custom',
            active: filtersActive > 0,
            activeCount: filtersActive,
            content: (
              <div className="space-y-4 w-72">
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label htmlFor="from" className="text-xs">From</Label>
                    <Input
                      id="from"
                      type="datetime-local"
                      value={dateFrom}
                      onChange={(e) => {
                        setDateFrom(e.target.value);
                        resetPage();
                      }}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="to" className="text-xs">To</Label>
                    <Input
                      id="to"
                      type="datetime-local"
                      value={dateTo}
                      onChange={(e) => {
                        setDateTo(e.target.value);
                        resetPage();
                      }}
                    />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Direction</Label>
                  <SearchableSelect
                    options={DIRECTION_OPTIONS}
                    value={direction}
                    onChange={(v) => {
                      setDirection(v);
                      resetPage();
                    }}
                    placeholder="All directions"
                  />
                </div>
                <div className="space-y-1" data-testid="chat-history-group-by">
                  <Label className="text-xs">Group by</Label>
                  <SearchableSelect
                    options={GROUP_BY_OPTIONS}
                    value={groupBy}
                    onChange={(v) => {
                      setGroupBy(v as ChatHistoryGroupBy);
                      // Grouping changes the server ordering, so the current
                      // page number no longer refers to the same rows.
                      resetPage();
                    }}
                    placeholder="No grouping"
                  />
                </div>
                <div>
                  <Button
                    variant={failedTurnsOnly ? 'primary' : 'outline'}
                    size="sm"
                    className="w-full"
                    onClick={() => {
                      setFailedTurnsOnly((v) => !v);
                      resetPage();
                    }}
                    aria-pressed={failedTurnsOnly}
                  >
                    {failedTurnsOnly ? 'Failed turns only: on' : 'Failed turns only: off'}
                  </Button>
                </div>
                <div>
                  <Button
                    variant={breachedOnly ? 'primary' : 'outline'}
                    size="sm"
                    className="w-full"
                    onClick={() => {
                      setBreachedOnly((v) => !v);
                      resetPage();
                    }}
                  >
                    {breachedOnly ? 'Breached only: on' : 'Breached only: off'}
                  </Button>
                  <p className="text-xs text-muted-foreground mt-1">
                    Turns whose reply took longer than the p99 target - shows both the
                    incoming message and its reply.
                  </p>
                </div>
              </div>
            ),
          }}
          exportConfig={false}
          primaryAction={
            <Button
              variant="outline"
              onClick={() => exportMutation.mutate(filters)}
              disabled={exportMutation.isPending}
            >
              <Download className="size-4 mr-2" />
              {exportMutation.isPending ? 'Queueing…' : 'Export CSV'}
            </Button>
          }
        />
      </div>
    );
  };

  return (
    <>
      <Container>
        <PageHeader title="Chat History" />
      </Container>

      <Container>
        <DataGrid
          table={table}
          recordCount={listEnabled ? (data?.pagination.total ?? 0) : 0}
          isLoading={isLoading || (failedTurnsOnly && failedLoading)}
          error={error}
          onRetry={() => void refetch()}
          isPlaceholderData={isPlaceholderData}
          onRowClick={(row: ChatMessageRow) => setSelected(row)}
          standardToolbar={false}
          tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
          renderGroupHeader={renderGroupHeader}
          emptyMessage={
            failedTurnsOnly
              ? (failedLoading
                  ? 'Looking for failed turns…'
                  : 'No chatbot turns failed in this range.')
              : 'No messages in this range. Widen the date range or clear filters - chat history is written by the n8n WhatsApp flow.'
          }
        >
          <Card>
            <GridToolbar />
            <CardTable>
              <DataGridTable />
            </CardTable>
            <CardFooter>
              <DataGridPagination />
            </CardFooter>
          </Card>
        </DataGrid>
      </Container>

      <ChatThreadDrawer row={selected} onOpenChange={(open) => !open && setSelected(null)} />
      <TurnDetailDrawer
        turnId={tracedTurnId}
        onOpenChange={(open) => !open && setTracedTurnId(null)}
      />
    </>
  );
}
