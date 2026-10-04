'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from '@/lib/toast';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import type { ColumnDef, PaginationState, RowSelectionState } from '@tanstack/react-table';
import { useReactTable, getCoreRowModel } from '@tanstack/react-table';
import { Button } from '@/components/ui/button';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { ListBoardViewToggle } from '@/components/common/ListBoardViewToggle';
import { useListBoardViewPreference } from '@/hooks/useListBoardViewPreference';
import { Plus, Trash2 } from 'lucide-react';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { bulkDeleteTickets, getTickets } from '../services/ticketService';
import type {
  Ticket,
  TicketCategory,
  TicketListFilters,
  TicketPriority,
  TicketSourceChannel,
  TicketStatus,
} from '../types/ticket.types';
import {
  TICKET_CATEGORIES,
  TICKET_PRIORITIES,
  TICKET_SOURCE_CHANNELS,
  TICKET_STATUSES,
} from '../types/ticket.types';
import { TicketPriorityBadge, TicketStatusBadge } from './TicketStatusBadge';
import TicketsKanban from './TicketsKanban';

const PAGE_SIZE = 50;

export default function TicketsList() {
  const searchParams = useSearchParams();
  const { mode, setMode, hydrated } = useListBoardViewPreference('tickets', 'list');

  // URL ?view= overrides persisted choice if provided.
  const urlView = searchParams.get('view');
  useEffect(() => {
    if (!hydrated) return;
    if (urlView === 'list' || urlView === 'board') {
      if (urlView !== mode) setMode(urlView);
    }
  }, [urlView, hydrated, mode, setMode]);

  const [rows, setRows] = useState<Ticket[]>([]);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: PAGE_SIZE,
  });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  // Whether rows are on screen, read by the load effect without becoming a dependency.
  const hasRowsRef = useRef(false);
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debouncedSearch,
    isSettling: debouncedSearchSettling,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<TicketStatus | 'all'>('all');
  const [priorityFilter, setPriorityFilter] = useState<TicketPriority | 'all'>('all');
  const [categoryFilter, setCategoryFilter] = useState<TicketCategory | 'all'>('all');
  const [sourceFilter, setSourceFilter] = useState<TicketSourceChannel | 'all'>('all');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);

  // Reset to page 1 whenever filters change.
  useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [debouncedSearch, statusFilter, priorityFilter, categoryFilter, sourceFilter]);

  // Clear selection on filter / page change.
  useEffect(() => {
    setRowSelection({});
  }, [
    pagination.pageIndex,
    pagination.pageSize,
    debouncedSearch,
    statusFilter,
    priorityFilter,
    categoryFilter,
    sourceFilter,
    mode,
  ]);

  // Only fetch list-mode data when in list mode.
  useEffect(() => {
    if (mode !== 'list') return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    const filters: TicketListFilters & { page: number; limit: number } = {
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
    };
    if (debouncedSearch) filters.q = debouncedSearch;
    if (statusFilter !== 'all') filters.status = statusFilter;
    if (priorityFilter !== 'all') filters.priority = priorityFilter;
    if (categoryFilter !== 'all') filters.category = categoryFilter;
    if (sourceFilter !== 'all') filters.source_channel = sourceFilter;
    getTickets(filters)
      .then((res) => {
        if (cancelled) return;
        setRows(res.data);
        setTotal(res.pagination.total);
        hasRowsRef.current = res.data.length > 0;
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // Rows on screen stay (a refresh after a bulk action failed); the grid shows the
        // failure only in place of no rows, so a failure over rows is said in a toast.
        setLoadError(e);
        if (hasRowsRef.current) {
          toast.error(e instanceof Error ? e.message : 'Could not refresh the tickets.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    mode,
    pagination.pageIndex,
    pagination.pageSize,
    debouncedSearch,
    statusFilter,
    priorityFilter,
    categoryFilter,
    sourceFilter,
    reloadTick,
  ]);

  // D3: the row opens the ticket.
  const rowHref = (row: Ticket) => `/ticket-management/tickets/${row.id}`;

  const columns = useMemo<ColumnDef<Ticket>[]>(
    () => [
      buildSelectColumn<Ticket>({
        rowLabel: (row) => `Select ticket ${row.original.ticket_number ?? row.original.id}`,
      }),
      {
        accessorKey: 'ticket_number',
        header: ({ column }) => <DataGridColumnHeader title="Ticket #" column={column} />,
        cell: ({ row }) => (
          <span className="whitespace-nowrap font-mono text-xs">
            {row.original.ticket_number ?? '-'}
          </span>
        ),
        size: 140,
        meta: { headerTitle: 'Ticket #', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'title',
        header: ({ column }) => <DataGridColumnHeader title="Title" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.title}>
            {row.original.title}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Title', skeleton: <Skeleton className="h-4 w-48" /> },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => <TicketStatusBadge status={row.original.status} />,
        size: 120,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'priority',
        header: ({ column }) => <DataGridColumnHeader title="Priority" column={column} />,
        cell: ({ row }) => <TicketPriorityBadge priority={row.original.priority} />,
        size: 120,
        meta: { headerTitle: 'Priority', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'category',
        header: ({ column }) => <DataGridColumnHeader title="Category" column={column} />,
        cell: ({ row }) => <span className="capitalize">{row.original.category}</span>,
        size: 120,
        meta: { headerTitle: 'Category' },
      },
      {
        accessorKey: 'source_channel',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        cell: ({ row }) => (
          <span className="text-xs">
            {row.original.source_channel === 'ai_assistant'
              ? 'AI Assistant'
              : row.original.source_channel === 'whatsapp_respond'
                ? 'WhatsApp'
                : 'Manual'}
          </span>
        ),
        size: 120,
        meta: { headerTitle: 'Source' },
      },
      {
        accessorKey: 'due_date',
        header: ({ column }) => <DataGridColumnHeader title="Due date" column={column} />,
        cell: ({ row }) => (
          <span className={row.original.is_overdue_resolution ? 'text-destructive' : ''}>
            {row.original.due_date ?? '-'}
          </span>
        ),
        size: 140,
        meta: { headerTitle: 'Due date' },
      },
      {
        id: 'assignee',
        accessorFn: (row) => row.assigned_to_user?.display_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Assignee" column={column} />,
        cell: ({ row }) =>
          row.original.assigned_to_user?.display_name ?? (
            <span className="text-muted-foreground">Unassigned</span>
          ),
        size: 180,
        meta: { headerTitle: 'Assignee' },
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated" column={column} />,
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {new Date(row.original.updated_at).toLocaleString()}
          </span>
        ),
        size: 160,
        meta: { headerTitle: 'Updated' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    enableSorting: false,
    columnResizeMode: 'onChange',
  });

  const selectedCount = Object.keys(rowSelection).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <ListSearchInput
          value={search}
          onChange={setSearch}
          isSettling={isSearchInFlight(debouncedSearchSettling, loading, debouncedSearch)}
          placeholder="Search title, description, ticket number…"
          aria-label="Search tickets"
          className="max-w-sm"
        />
        {mode === 'list' && (
          <SearchableSelect
            value={statusFilter}
            onChange={(v) => setStatusFilter(v as TicketStatus | 'all')}
            options={[
              { value: 'all', label: 'All statuses' },
              ...TICKET_STATUSES.map((s) => ({
                value: s,
                label: s.charAt(0).toUpperCase() + s.slice(1),
              })),
            ]}
            placeholder="Status"
            triggerClassName="w-[160px]"
          />
        )}
        <SearchableSelect
          value={priorityFilter}
          onChange={(v) => setPriorityFilter(v as TicketPriority | 'all')}
          options={[
            { value: 'all', label: 'All priorities' },
            ...TICKET_PRIORITIES.map((p) => ({
              value: p,
              label: p.charAt(0).toUpperCase() + p.slice(1),
            })),
          ]}
          placeholder="Priority"
          triggerClassName="w-[160px]"
        />
        <SearchableSelect
          value={categoryFilter}
          onChange={(v) => setCategoryFilter(v as TicketCategory | 'all')}
          options={[
            { value: 'all', label: 'All categories' },
            ...TICKET_CATEGORIES.map((c) => ({
              value: c,
              label: c.charAt(0).toUpperCase() + c.slice(1),
            })),
          ]}
          placeholder="Category"
          triggerClassName="w-[160px]"
        />
        {mode === 'list' && (
          <SearchableSelect
            value={sourceFilter}
            onChange={(v) => setSourceFilter(v as TicketSourceChannel | 'all')}
            options={[
              { value: 'all', label: 'All sources' },
              ...TICKET_SOURCE_CHANNELS.map((s) => ({
                value: s,
                label:
                  s === 'manual' ? 'Manual' : s === 'ai_assistant' ? 'AI Assistant' : 'WhatsApp',
              })),
            ]}
            placeholder="Source"
            triggerClassName="w-[180px]"
          />
        )}
        <ListBoardViewToggle value={mode} onChange={setMode} />
        <div className="ms-auto flex items-center gap-2">
          {mode === 'list' && selectedCount > 0 && (
            <Button
              variant="destructive"
              onClick={() => setBulkDeleteOpen(true)}
            >
              <Trash2 className="size-4" />
              Delete {selectedCount} selected
            </Button>
          )}
          <Button asChild>
            <Link href="/ticket-management/tickets/new">
              <Plus className="size-4" /> Create Ticket
            </Link>
          </Button>
        </div>
      </div>

      {mode === 'board' ? (
        <TicketsKanban
          filters={{
            q: debouncedSearch || undefined,
            priority: priorityFilter,
            category: categoryFilter,
          }}
        />
      ) : (
        <DataGrid
          table={table}
          recordCount={total}
          isLoading={loading}
          error={loadError}
          onRetry={() => setReloadTick((t) => t + 1)}
          rowHref={rowHref}
          listingKey="tickets.tickets.view"
          emptyMessage="No tickets match these filters."
          tableLayout={{ width: 'fixed', columnsResizable: true }}
        >
          <div className="rounded-md border">
            <DataGridTable />
          </div>
          <div className="flex items-center justify-end pt-2">
            <DataGridPagination />
          </div>
        </DataGrid>
      )}

      <ConfirmDeleteDialog
        open={bulkDeleteOpen}
        onOpenChange={setBulkDeleteOpen}
        description={
          <>
            Permanently delete <strong>{selectedCount}</strong>{' '}
            {selectedCount === 1 ? 'ticket' : 'tickets'}? This action cannot be undone.
          </>
        }
        onDelete={async () => {
          await bulkDeleteTickets(Object.keys(rowSelection));
        }}
        successMessage={`${selectedCount} ${selectedCount === 1 ? 'ticket' : 'tickets'} deleted`}
        onSuccess={() => {
          setRowSelection({});
          setReloadTick((n) => n + 1);
        }}
      />
    </div>
  );
}
