'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
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
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardTable } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { DataGrid } from '@/components/ui/data-grid';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { getApiCallLogs, getApiCallLogSources } from './services/apiCallLogService';
import { ApiCallDetailDrawer } from './components/ApiCallDetailDrawer';
import type { ApiCallLogRow } from './types/apiCallLog.types';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

function localInput(offsetHours: number): string {
  const d = new Date(Date.now() - offsetHours * 3600_000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const OUTCOME_OPTIONS = [
  { value: '', label: 'All outcomes' },
  { value: 'success', label: 'Success' },
  { value: 'client_error', label: 'Client error (4xx)' },
  { value: 'server_error', label: 'Server error (5xx)' },
];

function OutcomeCell({ row }: { row: ApiCallLogRow }) {
  const variant =
    row.outcome === 'success' ? 'success' : row.outcome === 'client_error' ? 'warning' : 'destructive';
  return (
    <Badge variant={variant} appearance="light" size="sm">
      {row.status_code ?? '-'}
    </Badge>
  );
}

export default function ApiCallLogsPage() {
  const [dateFrom, setDateFrom] = useState(() => localInput(24));
  const [dateTo, setDateTo] = useState(() => localInput(0));
  const [source, setSource] = useState('');
  const [outcome, setOutcome] = useState('');
  const [correlationId, setCorrelationId] = useState('');
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const [selected, setSelected] = useState<ApiCallLogRow | null>(null);

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  const filters = useMemo(
    () => ({
      date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
      date_to: dateTo ? new Date(dateTo).toISOString() : undefined,
      source: source || undefined,
      outcome: outcome || undefined,
      correlation_id: correlationId || undefined,
      search: searchQuery || undefined,
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
      sort: sorting[0]?.id,
      dir: (sorting[0]?.desc ? 'desc' : 'asc') as 'asc' | 'desc',
    }),
    [dateFrom, dateTo, source, outcome, correlationId, searchQuery, pagination, sorting],
  );

  const { data, isLoading, isFetching, isPlaceholderData, error, refetch } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['apiCallLogs', filters],
    queryFn: () => getApiCallLogs(filters),
  });

  // Live values, so a caller that starts sending a new X-Source appears in the
  // filter the first time it calls rather than after a code change.
  const { data: sources = [] } = useQuery({
    queryKey: ['apiCallLogSources'],
    queryFn: getApiCallLogSources,
  });

  const sourceOptions = useMemo(
    () => [{ value: '', label: 'All sources' }, ...sources.map((s) => ({ value: s, label: s }))],
    [sources],
  );

  const columns = useMemo<ColumnDef<ApiCallLogRow>[]>(
    () => [
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="When" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.created_at),
        size: 170,
        meta: { headerTitle: 'When' },
      },
      {
        accessorKey: 'source',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary">
            {row.original.source}
          </Badge>
        ),
        size: 100,
        meta: { headerTitle: 'Source' },
      },
      {
        accessorKey: 'method',
        header: ({ column }) => <DataGridColumnHeader title="Method" column={column} />,
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.method}</span>,
        size: 80,
        meta: { headerTitle: 'Method' },
      },
      {
        accessorKey: 'endpoint',
        header: ({ column }) => <DataGridColumnHeader title="Endpoint" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate font-mono text-xs" title={row.original.endpoint}>
            {row.original.endpoint}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Endpoint' },
      },
      {
        accessorKey: 'tool_name',
        header: ({ column }) => <DataGridColumnHeader title="MCP tool" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate text-xs" title={row.original.tool_name ?? ''}>
            {row.original.tool_name ?? '-'}
          </span>
        ),
        size: 150,
        meta: { headerTitle: 'MCP tool' },
      },
      {
        accessorKey: 'status_code',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => <OutcomeCell row={row.original} />,
        size: 90,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'latency_ms',
        header: ({ column }) => <DataGridColumnHeader title="Latency" column={column} />,
        cell: ({ row }) =>
          row.original.latency_ms === null ? '-' : `${row.original.latency_ms} ms`,
        size: 100,
        meta: { headerTitle: 'Latency' },
      },
    ],
    [],
  );

  const rows = data?.data ?? [];
  const total = data?.total ?? 0;

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(total / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    manualPagination: true,
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <Container>
      <PageHeader title="API Call Log" />

      <Card className="mb-5">
        <div className="flex flex-wrap items-end gap-3 p-5">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input
              type="datetime-local"
              value={dateFrom}
              data-testid="api-call-log-from"
              onChange={(e) => setDateFrom(e.target.value)}
              className="w-56"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">To</Label>
            <Input
              type="datetime-local"
              value={dateTo}
              data-testid="api-call-log-to"
              onChange={(e) => setDateTo(e.target.value)}
              className="w-56"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Source</Label>
            <SearchableSelect
              value={source}
              onChange={setSource}
              options={sourceOptions}
              placeholder="All sources"
              className="w-44"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Outcome</Label>
            <SearchableSelect
              value={outcome}
              onChange={setOutcome}
              options={OUTCOME_OPTIONS}
              placeholder="All outcomes"
              className="w-48"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Correlation id</Label>
            <Input
              value={correlationId}
              data-testid="api-call-log-correlation"
              onChange={(e) => setCorrelationId(e.target.value)}
              placeholder="Join an MCP client span"
              className="w-56"
            />
          </div>
        </div>
      </Card>

      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        onRowClick={(row) => setSelected(row)}
      >
        <Card>
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search endpoint, tool, error..."
                data-testid="api-call-log-search"
                className="w-full max-w-xs"
              />
            }
          />
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>

      <ApiCallDetailDrawer row={selected} onClose={() => setSelected(null)} />
    </Container>
  );
}
