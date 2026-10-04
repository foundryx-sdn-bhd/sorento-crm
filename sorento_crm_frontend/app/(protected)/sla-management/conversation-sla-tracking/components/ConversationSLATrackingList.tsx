'use client';

import { useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  VisibilityState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { AlertCircle, CheckCircle, Clock, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { SearchableSelect } from '@/components/common/SearchableSelect';

import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { useConversationSLATracking } from '../hooks/useConversationSLATracking';
import type { ConversationSLATracking } from '../types/conversationSLATracking.types';
import { formatDateTime, formatDuration, formatDurationWithSeconds, parseDateTimeAsUTC } from '@/lib/helpers';
import { getUserLookup, type UserLookupItem } from '@/services/userSelectService';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { CONVERSATION_SLA_TRACKING_PATH } from '../lib/historyLinks';
import { slaHandler } from '../lib/slaHandler';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { useConversationSlaActions } from '../actions';
import { useRowPending } from '@/hooks/useDeferredRowAction';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

/**
 * The row's "..." (D15): the same set the record's gear renders, minus the verbs
 * that need the fetched tracking. Its own component because the action set is a
 * hook, and a hook cannot be called inside a `cell` callback.
 */
function ConversationRowActions({
  tracking,
}: {
  tracking: {
    id: string;
    respond_io_id?: string | null;
    contact?: { respond_io_id?: string | null } | null;
    contact_name?: string | null;
    contact_phone?: string | null;
  };
}) {
  const { actions } = useConversationSlaActions(tracking, { surface: 'toast' });
  return <RowActionsMenu ariaLabel="conversation" actions={actions} />;
}

export default function ConversationSLATrackingList() {
  const router = useRouter();
  const queryClient = useQueryClient();
  // A row whose delete is counting down stays visible and dims (S6-07).
  const rowPending = useRowPending<{ id: string }>('sla_tracking');
  // AC-M2 history deep links: the drawer's "View history" (one contact) and the
  // worklist's "Recently resolved" (what I resolved) land here pre-filtered. The
  // filters are applied by the SAME server-side list query as every other filter
  // - nothing is sliced in the browser.
  const searchParams = useSearchParams();
  const contactFilter = searchParams.get('contact') ?? undefined;
  const resolvedParam = searchParams.get('is_resolved');
  const resolvedFilter =
    resolvedParam === 'true' ? true : resolvedParam === 'false' ? false : undefined;
  const resolvedByFilter = searchParams.get('resolved_by') ?? undefined;
  const sortParam = searchParams.get('sort');
  const dirParam = searchParams.get('dir');
  const hasDeepLinkFilters = Boolean(contactFilter || resolvedParam || resolvedByFilter);

  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>(
    sortParam
      ? [{ id: sortParam, desc: (dirParam ?? 'desc') !== 'asc' }]
      : [{ id: 'created_at', desc: true }],
  );
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    agent_code: false,
    team_set_code: false,
  });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [assignedToFilter, setAssignedToFilter] = useState('__all__');

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearchQuery(state.searchQuery);
    setAssignedToFilter(state.filters.assigned_to ?? '__all__');
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [
    searchQuery,
    assignedToFilter,
    contactFilter,
    resolvedParam,
    resolvedByFilter,
  ]);

  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useConversationSLATracking({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    assigned_to: assignedToFilter && assignedToFilter !== '__all__' ? assignedToFilter : undefined,
    contact: contactFilter,
    is_resolved: resolvedFilter,
    resolved_by: resolvedByFilter,
  });

  const {
    data: respondUsers,
    error: respondUsersError,
    refetch: refetchRespondUsers,
  } = useQuery({
    queryKey: ['user-lookup', 'respond-synced', 'with-inactive'],
    queryFn: () => getUserLookup({ respond_synced: true, include_inactive: true }),
    staleTime: 1000 * 60 * 5,
  });

  const handleRefresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['conversation-sla-tracking'] });
    await queryClient.invalidateQueries({ queryKey: ['conversation-sla-tracking-detail'] });
  };


  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from.
  const rowHref = (row: ConversationSLATracking) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        assigned_to:
          assignedToFilter && assignedToFilter !== '__all__'
            ? assignedToFilter
            : undefined,
        contact: contactFilter,
        is_resolved: resolvedParam ?? undefined,
        resolved_by: resolvedByFilter,
      },
    );
    return `/sla-management/conversation-sla-tracking/${row.id}${search ? `?${search}` : ''}`;
  };

  const getTimeRemaining = (dueAt: string | Date, backendSeconds?: number | null) => {
    if (backendSeconds !== undefined && backendSeconds !== null) {
      return formatDuration(Math.abs(backendSeconds) * 1000);
    }
    const now = Date.now();
    const due = parseDateTimeAsUTC(dueAt).getTime();
    const diff = due - now;
    return formatDuration(Math.abs(diff));
  };

  const getTimeRemainingPercent = (dueAt: string | Date, currentTierStartedAt: string | Date) => {
    const now = Date.now();
    const due = parseDateTimeAsUTC(dueAt).getTime();
    const started = parseDateTimeAsUTC(currentTierStartedAt).getTime();
    const total = due - started;
    const remaining = due - now;
    if (total <= 0) return 0;
    return Math.max(0, Math.min(100, (remaining / total) * 100));
  };

  /** Response overdue: not responded and now > due_at, OR responded after due_at. Uses UTC parsing to match detail view. */
  const isResponseOverdue = (o: ConversationSLATracking) => {
    const due = parseDateTimeAsUTC(o.due_at).getTime();
    const now = Date.now();
    if (o.is_responded && o.responded_at) {
      return parseDateTimeAsUTC(o.responded_at).getTime() > due;
    }
    return now > due;
  };

  /** Resolution overdue: not resolved and now > due_at_resolution, OR resolved after due_at_resolution. Uses UTC parsing to match detail view. */
  const isResolutionOverdue = (o: ConversationSLATracking) => {
    const dueRes = o.due_at_resolution ?? o.resolution_due_at;
    if (!dueRes) return false;
    const due = parseDateTimeAsUTC(dueRes).getTime();
    const now = Date.now();
    if (o.is_resolved && o.resolved_at) {
      return parseDateTimeAsUTC(o.resolved_at).getTime() > due;
    }
    return now > due;
  };

  /** Time elapsed: same logic as detail view - stops only when resolved; until then, now - initiated_at. */
  const getTimeElapsed = (o: ConversationSLATracking): string => {
    if (o.is_resolved && o.resolved_at && o.initiated_at) {
      const ms = parseDateTimeAsUTC(o.resolved_at).getTime() - parseDateTimeAsUTC(o.initiated_at).getTime();
      return formatDurationWithSeconds(ms);
    }
    const ms = Date.now() - parseDateTimeAsUTC(o.initiated_at).getTime();
    return formatDurationWithSeconds(ms);
  };

  /** Response duration from timestamps, to match detail view. */
  const getResponseDuration = (o: ConversationSLATracking): string | null => {
    if (!o.is_responded || !o.responded_at || !o.initiated_at) return null;
    const ms = parseDateTimeAsUTC(o.responded_at).getTime() - parseDateTimeAsUTC(o.initiated_at).getTime();
    return formatDurationWithSeconds(ms);
  };

  const formatSecondsToDuration = (seconds: number | null | undefined) => {
    if (seconds == null) return '-';
    return formatDuration(seconds * 1000);
  };
  const formatSecondsToDurationWithSeconds = (seconds: number | null | undefined) => {
    if (seconds == null) return '-';
    return formatDurationWithSeconds(seconds * 1000);
  };

  const columns = useMemo<ColumnDef<ConversationSLATracking>[]>(
    () => [
      buildSelectColumn<ConversationSLATracking>(),
      {
        accessorKey: 'contact_phone',
        header: ({ column }) => <DataGridColumnHeader title="Contact Phone" column={column} />,
        cell: ({ row }) => {
          const phone = row.original.contact_phone ||
                       row.original.contact?.phone_number ||
                       '-';
          return phone;
        },
        size: 200,
        meta: { headerTitle: 'Contact Phone', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'contact_name',
        header: ({ column }) => <DataGridColumnHeader title="Contact Name" column={column} />,
        cell: ({ row }) => {
          const name = row.original.contact_name ||
                      row.original.contact?.name ||
                      '-';
          return name;
        },
        size: 220,
        meta: { headerTitle: 'Contact Name', skeleton: <Skeleton className="h-4 w-36" /> },
      },
      {
        accessorKey: 'policy_name',
        header: ({ column }) => <DataGridColumnHeader title="Policy" column={column} />,
        cell: ({ row }) => {
          const policyName = row.original.policy?.name ||
                            row.original.policy_name ||
                            row.original.policy?.code ||
                            row.original.policy_code ||
                            '-';
          return policyName;
        },
        size: 200,
        meta: { headerTitle: 'Policy', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'current_tier',
        header: ({ column }) => <DataGridColumnHeader title="Current Tier" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary">Tier {row.original.current_tier}</Badge>
        ),
        size: 120,
        meta: { headerTitle: 'Current Tier' },
      },
      {
        accessorKey: 'assigned_user_name',
        header: ({ column }) => <DataGridColumnHeader title="Assigned To" column={column} />,
        cell: ({ row }) => {
          // Owner ruling S4 (23 Sep 2026): resolve keeps the assignee for
          // audit now, so this column names the assignee on every row, open
          // or resolved - no more role swap. Who resolved it is a detail-page
          // fact (header subtitle), not shown in this listing.
          const handler = slaHandler(row.original);
          if (!handler.name) return '-';
          const title = `${handler.prefix} ${handler.name}`;
          return (
            <span className="block truncate" title={title}>
              {handler.name}
            </span>
          );
        },
        size: 190,
        meta: { headerTitle: 'Assigned To', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'initiated_at',
        header: ({ column }) => <DataGridColumnHeader title="Initiated At" column={column} />,
        cell: ({ row }) => formatDateTime(parseDateTimeAsUTC(row.original.initiated_at)),
        size: 180,
        meta: { headerTitle: 'Initiated At' },
      },
      {
        accessorKey: 'due_at',
        header: ({ column }) => <DataGridColumnHeader title="Due at (response)" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          const overdue = isResponseOverdue(o);
          return (
            <div className="space-y-1">
              <div className={overdue ? 'text-destructive font-medium' : ''}>
                {formatDateTime(parseDateTimeAsUTC(o.due_at))}
              </div>
              {overdue && (
                <div className="text-xs text-destructive flex items-center gap-1">
                  <AlertCircle className="size-3" />
                  Overdue
                </div>
              )}
            </div>
          );
        },
        size: 180,
        meta: { headerTitle: 'Due at (response)' },
      },
      {
        accessorKey: 'due_at_resolution',
        header: ({ column }) => <DataGridColumnHeader title="Due at (resolution)" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          const dueRes = o.due_at_resolution ?? o.resolution_due_at;
          if (!dueRes) return '-';
          const overdue = isResolutionOverdue(o);
          return (
            <div className="space-y-1">
              <div className={overdue ? 'text-destructive font-medium' : ''}>
                {formatDateTime(parseDateTimeAsUTC(dueRes))}
              </div>
              {overdue && (
                <div className="text-xs text-destructive flex items-center gap-1">
                  <AlertCircle className="size-3" />
                  Overdue
                </div>
              )}
            </div>
          );
        },
        size: 180,
        meta: { headerTitle: 'Due at (resolution)' },
      },
      {
        accessorKey: 'time_elapsed',
        header: ({ column }) => <DataGridColumnHeader title="Time elapsed" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          return <span className="text-sm">{getTimeElapsed(o)}</span>;
        },
        size: 120,
        meta: { headerTitle: 'Time elapsed' },
      },
      {
        accessorKey: 'time_remaining_response',
        header: ({ column }) => <DataGridColumnHeader title="Response" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          if (o.is_responded) {
            const overdue = isResponseOverdue(o);
            const str =
              getResponseDuration(o) ??
              (o.response_time != null
                ? formatDurationWithSeconds(Number(o.response_time) * 3600 * 1000)
                : '-');
            return (
              <div className="space-y-0.5">
                <div className="text-xs text-muted-foreground">Response time</div>
                <span className={`text-sm font-medium ${overdue ? 'text-destructive' : 'text-green-600'}`}>
                  {str}
                </span>
              </div>
            );
          }
          const overdue = isResponseOverdue(o);
          const str = o.time_remaining_response_seconds != null
            ? formatDuration(Math.abs(o.time_remaining_response_seconds) * 1000)
            : getTimeRemaining(o.due_at);
          return (
            <div className="space-y-1 w-40">
              <div className="text-xs text-muted-foreground">Time remaining</div>
              <div className="flex items-center gap-2">
                <Clock className={`size-4 ${overdue ? 'text-destructive' : 'text-muted-foreground'}`} />
                <span className={`text-sm ${overdue ? 'text-destructive font-medium' : ''}`}>
                  {overdue ? `${str} overdue` : `${str} left`}
                </span>
              </div>
              <Progress value={getTimeRemainingPercent(o.due_at, o.current_tier_started_at)} className="h-2" />
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'Response' },
      },
      {
        accessorKey: 'time_remaining_resolution',
        header: ({ column }) => <DataGridColumnHeader title="Resolution" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          if (o.is_resolved) {
            const overdue = isResolutionOverdue(o);
            const hours = Number(o.resolution_duration) || 0;
            const str = hours < 1 ? formatSecondsToDurationWithSeconds(hours * 3600) : `${hours.toFixed(1)}h`;
            return (
              <div className="space-y-0.5">
                <div className="text-xs text-muted-foreground">Resolution duration</div>
                <span className={`text-sm font-medium ${overdue ? 'text-destructive' : 'text-green-600'}`}>
                  {str}
                </span>
              </div>
            );
          }
          const sec = o.time_remaining_resolution_seconds;
          const str = formatSecondsToDuration(sec ?? undefined);
          const overdue = isResolutionOverdue(o);
          return (
            <div className="space-y-0.5">
              <div className="text-xs text-muted-foreground">Time remaining</div>
              <span className={`text-sm ${overdue ? 'text-destructive font-medium' : ''}`}>
                {overdue ? 'Overdue' : `${str} left`}
              </span>
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'Resolution' },
      },
      {
        accessorKey: 'agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Agent Code" column={column} />,
        cell: ({ row }) => row.original.agent_code || '-',
        size: 150,
        meta: { headerTitle: 'Agent Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'team_set_code',
        header: ({ column }) => <DataGridColumnHeader title="Team Set Code" column={column} />,
        cell: ({ row }) => row.original.team_set_code || '-',
        size: 160,
        meta: { headerTitle: 'Team Set Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'is_resolved',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          if (row.original.is_resolved) {
            return (
              <Badge variant="success">
                <CheckCircle className="size-3 mr-1" />
                Resolved
              </Badge>
            );
          }
          if (row.original.escalated_at) {
            return (
              <Badge variant="warning">
                <AlertCircle className="size-3 mr-1" />
                Escalated
              </Badge>
            );
          }
          return (
            <Badge variant="info">
              <Clock className="size-3 mr-1" />
              Pending
            </Badge>
          );
        },
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => <ConversationRowActions tracking={row.original} />,
        size: 60,
        enableHiding: false,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, columnVisibility, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  const hasAssigneeFilter = Boolean(assignedToFilter && assignedToFilter !== '__all__');
  const hasActiveFilters = hasAssigneeFilter || hasDeepLinkFilters;
  const activeFilterCount =
    (hasAssigneeFilter ? 1 : 0) +
    (contactFilter ? 1 : 0) +
    (resolvedParam ? 1 : 0) +
    (resolvedByFilter ? 1 : 0);

  // The deep-link filters are not represented in the filter popover, so say them
  // out loud: a listing that is silently showing a subset is a listing nobody can
  // trust. The contact is named from the rows when there are any; the raw ref is
  // an id or a phone, which is what the link had to carry.
  const deepLinkSummary = useMemo(() => {
    const parts: string[] = [];
    if (contactFilter) {
      const name = data?.data?.[0]?.contact_name || data?.data?.[0]?.contact_phone;
      parts.push(name ? `Contact: ${name}` : 'One contact');
    }
    if (resolvedParam === 'true') parts.push('Resolved only');
    if (resolvedParam === 'false') parts.push('Open only');
    if (resolvedByFilter) parts.push(resolvedByFilter === 'me' ? 'Resolved by you' : 'One resolver');
    return parts.join(' · ');
  }, [contactFilter, resolvedParam, resolvedByFilter, data]);

  const handleClearFilters = () => {
    setAssignedToFilter('__all__');
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
    if (hasDeepLinkFilters) router.replace(CONVERSATION_SLA_TRACKING_PATH, { scroll: false });
  };

  return (
    <DataGrid
      table={table}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      error={error}
      onRetry={() => void refetch()}
      rowHref={rowHref}
      rowPending={rowPending}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search by contact phone or name..."
                className="w-64 min-w-[140px] max-w-[280px]"
              />
            }
            filters={{
              kind: 'custom',
              active: hasActiveFilters,
              activeCount: activeFilterCount,
              content: (
                <div className="space-y-3">
                  <h4 className="font-medium text-sm">Filters</h4>
                  <SearchableSelect
                    value={assignedToFilter}
                    onChange={(value) => {
                      setAssignedToFilter(value);
                      setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                    }}
                    loadError={respondUsersError}
                    onRetry={() => void refetchRespondUsers()}
                    options={[
                      { value: '__all__', label: 'All assignees' },
                      ...(respondUsers || []).map((user: UserLookupItem) => ({
                        value: user.respond_user_id || user.id,
                        label: user.name || 'Unnamed user',
                      })),
                    ]}
                    placeholder="Assignee"
                  />
                  {hasActiveFilters && (
                    <Button variant="outline" size="sm" onClick={handleClearFilters} className="w-full">
                      Clear Filters
                    </Button>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'conversation_sla_tracking_export.xlsx' }}
            onRefresh={handleRefresh}
            isRefreshing={isFetching && !isLoading}
          />
          {hasDeepLinkFilters && (
            <div
              data-testid="history-filter-summary"
              className="mt-3 flex flex-wrap items-center gap-2 rounded-md border bg-muted/40 px-3 py-2 text-xs"
            >
              <span className="text-muted-foreground">Showing</span>
              <span className="font-medium">{deepLinkSummary}</span>
              <Button
                size="sm"
                variant="ghost"
                className="ms-auto h-6"
                onClick={handleClearFilters}
              >
                <X className="size-3.5" />
                Show all
              </Button>
            </div>
          )}
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}
