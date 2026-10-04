'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { UserRoundCog, UserRoundPlus, UserRoundCheck } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { toast } from '@/lib/toast';
import { formatDateTime, parseDateTimeAsUTC } from '@/lib/helpers';
import {
  useTeamPendingSLA,
  useVisibleUsers,
  useTakeoverSLATracking,
  useReassignSLATracking,
} from '@/app/(protected)/sla-management/conversation-sla-tracking/hooks/useTeamPendingSLA';
import { useSubscribeCoverage } from '@/app/(protected)/account/notifications/hooks/useCoverage';
import ReassignDialog from '@/app/(protected)/sla-management/conversation-sla-tracking/components/ReassignDialog';
import type { TeamPendingItem } from '@/app/(protected)/sla-management/conversation-sla-tracking/services/conversationSLATrackingService';

const ALL = '__all__';

export default function TeamPendingList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [assigneeFilter, setAssigneeFilter] = useState(ALL);
  const [teamFilter, setTeamFilter] = useState(ALL);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [reassignTarget, setReassignTarget] = useState<{ id: string; label: string } | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [assigneeFilter, teamFilter, search]);

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = useTeamPendingSLA({
    page: pagination.pageIndex + 1,
    limit: pagination.pageSize,
    assignee: assigneeFilter !== ALL ? assigneeFilter : undefined,
    team: teamFilter !== ALL ? teamFilter : undefined,
    query: search || undefined,
  });

  const { data: visibleUsers = [] } = useVisibleUsers();
  const takeoverMutation = useTakeoverSLATracking();
  const reassignMutation = useReassignSLATracking();
  const subscribeMutation = useSubscribeCoverage();

  const rows = useMemo(() => data?.data ?? [], [data]);

  // Team filter options: distinct {team_id, team_label} from the loaded rows.
  const teamOptions = useMemo(() => {
    const map = new Map<string, string>();
    rows.forEach((r) => map.set(r.team_id, r.team_label));
    return Array.from(map.entries()).map(([id, label]) => ({ id, label }));
  }, [rows]);

  const mutatingId =
    (takeoverMutation.isPending && takeoverMutation.variables?.id) ||
    (reassignMutation.isPending && reassignMutation.variables?.id) ||
    null;

  const taskLabel = (r: TeamPendingItem) =>
    `${r.reference ?? (r.source_entity_type ?? 'Task')}`;

  const handleSubscribe = (r: TeamPendingItem) => {
    subscribeMutation.mutate(
      { targetUserId: r.assignee_id },
      {
        onSuccess: () => toast.success(`Now covering for ${r.assignee_name}.`),
      },
    );
  };

  const columns = useMemo<ColumnDef<TeamPendingItem>[]>(
    () => [
      buildSelectColumn<TeamPendingItem>(),
      {
        accessorKey: 'reference',
        header: ({ column }) => <DataGridColumnHeader title="Reference" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          const ref = r.reference || '-';
          return <span className="truncate block" title={ref}>{ref}</span>;
        },
        size: 200,
        meta: { headerTitle: 'Reference', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'assignee_name',
        header: ({ column }) => <DataGridColumnHeader title="Assignee" column={column} />,
        cell: ({ row }) => (
          <span className="truncate block" title={row.original.assignee_name}>
            {row.original.assignee_name}
          </span>
        ),
        size: 180,
        meta: { headerTitle: 'Assignee', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'team_label',
        header: ({ column }) => <DataGridColumnHeader title="Team" column={column} />,
        cell: ({ row }) => (
          <span className="truncate block" title={row.original.team_label}>
            {row.original.team_label}
          </span>
        ),
        size: 180,
        meta: { headerTitle: 'Team', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'policy_name',
        header: ({ column }) => <DataGridColumnHeader title="Policy" column={column} />,
        cell: ({ row }) => {
          const p = row.original.policy_name || '-';
          return <span className="truncate block" title={p}>{p}</span>;
        },
        size: 160,
        meta: { headerTitle: 'Policy', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'due_at',
        header: ({ column }) => <DataGridColumnHeader title="Due at" column={column} />,
        cell: ({ row }) => {
          const due = row.original.due_at;
          if (!due) return '-';
          const overdue = parseDateTimeAsUTC(due).getTime() < Date.now();
          const text = formatDateTime(parseDateTimeAsUTC(due));
          return (
            <span className={overdue ? 'text-destructive font-medium' : ''} title={text}>
              {text}
            </span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Due at' },
      },
      {
        accessorKey: 'current_tier',
        header: ({ column }) => <DataGridColumnHeader title="Tier" column={column} />,
        cell: ({ row }) => <Badge variant="secondary">Tier {row.original.current_tier}</Badge>,
        size: 100,
        meta: { headerTitle: 'Tier' },
      },
      {
        accessorKey: 'next_action',
        header: ({ column }) => <DataGridColumnHeader title="Next action" column={column} />,
        cell: ({ row }) => {
          const r = row.original;
          const action = r.is_form_sla ? r.next_action ?? 'Action required' : 'Reply';
          return <span className="truncate block" title={action}>{action}</span>;
        },
        size: 170,
        meta: { headerTitle: 'Next action' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => {
          const r = row.original;
          const rowBusy = mutatingId === r.id;
          return (
            <div className="flex items-center gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    disabled={rowBusy}
                    onClick={() => takeoverMutation.mutate({ id: r.id, teamId: r.team_id })}
                    aria-label="Takeover"
                  >
                    <UserRoundPlus className="size-4 text-muted-foreground" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Takeover - assign this task to me</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    disabled={rowBusy}
                    onClick={() => setReassignTarget({ id: r.id, label: taskLabel(r) })}
                    aria-label="Reassign"
                  >
                    <UserRoundCog className="size-4 text-muted-foreground" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Reassign to a colleague</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    disabled={subscribeMutation.isPending}
                    onClick={() => handleSubscribe(r)}
                    aria-label={`Cover for ${r.assignee_name}`}
                  >
                    <UserRoundCheck className="size-4 text-muted-foreground" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Cover for {r.assignee_name}</TooltipContent>
              </Tooltip>
            </div>
          );
        },
        size: 130,
        enableHiding: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mutatingId, takeoverMutation, subscribeMutation.isPending],
  );

  const total = data?.total ?? 0;
  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(total / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    columnResizeMode: 'onChange',
  });

  const hasActiveFilters = assigneeFilter !== ALL || teamFilter !== ALL;

  return (
    <>
      <DataGrid
        table={table}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        recordCount={total}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        emptyMessage="No open tasks across your teams."
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  isSettling={isSearchInFlight(searchSettling, isFetching, search)}
                  placeholder="Search number, contact, assignee…"
                  className="w-full max-w-xs"
                />
              }
              filters={{
                kind: 'custom',
                active: hasActiveFilters,
                activeCount:
                  (assigneeFilter !== ALL ? 1 : 0) + (teamFilter !== ALL ? 1 : 0),
                content: (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <label className="text-xs text-muted-foreground">Assignee</label>
                      <SearchableSelect
                        value={assigneeFilter}
                        onChange={setAssigneeFilter}
                        options={[
                          { value: ALL, label: 'All assignees' },
                          ...visibleUsers.map((u) => ({
                            value: u.id,
                            label: u.name || u.email,
                          })),
                        ]}
                        placeholder="All assignees"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-xs text-muted-foreground">Team</label>
                      <SearchableSelect
                        value={teamFilter}
                        onChange={setTeamFilter}
                        options={[
                          { value: ALL, label: 'All teams' },
                          ...teamOptions.map((t) => ({
                            value: t.id,
                            label: t.label,
                          })),
                        ]}
                        placeholder="All teams"
                      />
                    </div>
                    {hasActiveFilters && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => {
                          setAssigneeFilter(ALL);
                          setTeamFilter(ALL);
                        }}
                      >
                        Clear filters
                      </Button>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: 'team_pending_export.xlsx' }}
              onRefresh={() => void refetch()}
              isRefreshing={isFetching}
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

      <ReassignDialog
        open={!!reassignTarget}
        onOpenChange={(o) => !o && setReassignTarget(null)}
        taskLabel={reassignTarget?.label}
        submitting={reassignMutation.isPending}
        onConfirm={(userId) => {
          if (!reassignTarget) return;
          reassignMutation.mutate(
            { id: reassignTarget.id, userId },
            { onSuccess: () => setReassignTarget(null) },
          );
        }}
      />
    </>
  );
}
