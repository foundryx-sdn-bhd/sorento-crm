'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { BellRing, UserPlus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  useAssignableUsers,
  useAwaitingAcceptance,
  useLeadAcceptanceMutations,
} from '../../_shared/hooks/useLeadAcceptance';
import type { AwaitingAcceptanceRow } from '../../_shared/types/leadAcceptance.types';
import { AssignLeadDialog } from '../../leads/components/AssignLeadDialog';
import { describeWait, informantSummary } from '../../leads/components/acceptance';
import { NudgeAssigneeDialog } from './NudgeAssigneeDialog';
import { PageHeader } from '@/components/common/PageHeader';

const ANY_OWNER = '';

const WAIT_OPTIONS = [
  { value: '', label: 'Any wait' },
  { value: '4', label: 'Over 4 hours' },
  { value: '24', label: 'Over a day' },
  { value: '72', label: 'Over three days' },
];

/**
 * Marketing's worklist: leads handed to somebody who has not accepted them.
 *
 * The server orders it (newest handover first) and takes no sort parameter, so the wait
 * is surfaced two other ways instead: in plain words on every row, and as the "waiting
 * over N hours" filter, which is how "nobody has answered me since Tuesday" is asked.
 */
export function AwaitingAcceptanceClient() {
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [ownerFilter, setOwnerFilter] = React.useState(ANY_OWNER);
  const [minHours, setMinHours] = React.useState('');
  const [assignTarget, setAssignTarget] = React.useState<AwaitingAcceptanceRow | null>(
    null,
  );
  const [nudgeTarget, setNudgeTarget] = React.useState<AwaitingAcceptanceRow | null>(null);

  React.useEffect(() => {
    setPagination((previous) => ({ ...previous, pageIndex: 0 }));
  }, [search, ownerFilter, minHours]);

  const query = useAwaitingAcceptance({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    searchQuery: search || undefined,
    owner_user_id: ownerFilter || undefined,
    min_hours: minHours ? Number(minHours) : undefined,
  });
  const users = useAssignableUsers();
  const { assign, nudge } = useLeadAcceptanceMutations();

  const rows = React.useMemo(() => query.data?.data ?? [], [query.data]);
  const total = query.data?.total ?? 0;

  const columns = React.useMemo<ColumnDef<AwaitingAcceptanceRow>[]>(
    () => [
      {
        accessorKey: 'title',
        header: ({ column }) => <DataGridColumnHeader title="Lead" column={column} />,
        cell: ({ row }) => (
          <div className="min-w-0">
            <Link
              href={`/project-sales/leads/${row.original.id}`}
              className="block truncate text-sm text-primary hover:underline"
              title={row.original.title}
            >
              {row.original.title}
            </Link>
            <span className="block truncate text-xs text-muted-foreground">
              {row.original.lead_code}
            </span>
          </div>
        ),
        size: 260,
        meta: { headerTitle: 'Lead', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'assigned_at',
        header: ({ column }) => <DataGridColumnHeader title="Waiting" column={column} />,
        cell: ({ row }) => {
          const wait = describeWait(row.original.hours_since_assigned) ?? 'Not assigned';
          const overdue = (row.original.hours_since_assigned ?? 0) >= 24;
          return (
            <span
              className={overdue ? 'block truncate font-medium text-destructive' : 'block truncate'}
              title={wait}
            >
              {wait}
            </span>
          );
        },
        size: 140,
        meta: { headerTitle: 'Waiting', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'owner_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Assigned to" column={column} />
        ),
        cell: ({ row }) => {
          const owner = row.original.owner_name ?? 'Nobody';
          return (
            <span className="block truncate" title={owner}>
              {owner}
            </span>
          );
        },
        size: 170,
        meta: { headerTitle: 'Assigned to', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'informant',
        header: ({ column }) => <DataGridColumnHeader title="Told us" column={column} />,
        cell: ({ row }) => {
          const who = informantSummary(row.original) ?? '-';
          return (
            <span className="block truncate" title={who}>
              {who}
            </span>
          );
        },
        size: 200,
        meta: { headerTitle: 'Told us', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'developer_name',
        header: ({ column }) => <DataGridColumnHeader title="Developer" column={column} />,
        cell: ({ row }) => {
          const developer = row.original.developer_name ?? '-';
          return (
            <span className="block truncate" title={developer}>
              {developer}
            </span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Developer', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'estimated_value',
        header: ({ column }) => <DataGridColumnHeader title="Value" column={column} />,
        cell: ({ row }) => {
          const value = row.original.estimated_value
            ? formatMyr(row.original.estimated_value)
            : '-';
          return (
            <span className="block truncate" title={value}>
              {value}
            </span>
          );
        },
        size: 140,
        meta: { headerTitle: 'Value', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setNudgeTarget(row.original)}
                  aria-label={`Nudge ${row.original.owner_name ?? 'the assignee'}`}
                >
                  <BellRing className="size-4 text-muted-foreground" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Nudge</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setAssignTarget(row.original)}
                  aria-label={`Assign ${row.original.lead_code} to somebody else`}
                >
                  <UserPlus className="size-4 text-muted-foreground" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Assign to somebody else</TooltipContent>
            </Tooltip>
          </div>
        ),
        size: 110,
        enableHiding: false,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    getRowId: (row) => row.id,
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    // The endpoint serves ONE order and takes no sort param. A clickable header would
    // re-sort the current page only, which reads as an ordering the list does not have.
    enableSorting: false,
    columnResizeMode: 'onChange',
  });

  const hasActiveFilters = ownerFilter !== ANY_OWNER || minHours !== '';

  return (
    <div className="space-y-5">
      <PageHeader
        title="Awaiting acceptance"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline">
              <Link href="/project-sales/leads">All leads</Link>
            </Button>
          </div>
        }
      />

      {query.isError ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
          <h2 className="text-sm font-semibold text-destructive">
            This list could not be loaded
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {query.error instanceof Error ? query.error.message : 'Try again shortly.'}
          </p>
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            onClick={() => void query.refetch()}
          >
            Try again
          </Button>
        </div>
      ) : (
        <DataGrid
          table={table}
          tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
          recordCount={total}
          isLoading={query.isLoading}
          isPlaceholderData={query.isPlaceholderData}
          emptyMessage={
            <div className="px-6 py-10 text-center">
              <p className="text-sm font-semibold">
                {hasActiveFilters || search
                  ? 'No leads match these filters'
                  : 'Every assigned lead has been accepted'}
              </p>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                Assign a lead from the leads list and it appears here until the
                salesperson accepts it.
              </p>
            </div>
          }
        >
          <Card>
            <CardHeader className="block">
              <DataGridListToolbar
                table={table}
                searchSlot={
                  <ListSearchInput
                    value={searchInput}
                    onChange={setSearchInput}
                    isSettling={isSearchInFlight(searchSettling, query.isFetching, search)}
                    placeholder="Search title or lead code…"
                    aria-label="Search leads awaiting acceptance"
                    className="w-full max-w-xs"
                  />
                }
                filters={{
                  kind: 'custom',
                  active: hasActiveFilters,
                  activeCount:
                    (ownerFilter !== ANY_OWNER ? 1 : 0) + (minHours !== '' ? 1 : 0),
                  content: (
                    <div className="space-y-3">
                      <div className="space-y-1.5">
                        <Label className="text-xs text-muted-foreground">
                          Assigned to
                        </Label>
                        <SearchableSelect
                          value={ownerFilter}
                          onChange={setOwnerFilter}
                          clearable
                          loadError={users.error}
                          onRetry={() => void users.refetch()}
                          options={(users.data ?? []).map((user) => ({
                            value: user.id,
                            label: user.name || 'Unnamed user',
                          }))}
                          placeholder="Anyone"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-xs text-muted-foreground">Waiting</Label>
                        <SearchableSelect
                          value={minHours}
                          onChange={setMinHours}
                          options={WAIT_OPTIONS}
                          placeholder="Any wait"
                        />
                      </div>
                      {hasActiveFilters && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="w-full"
                          onClick={() => {
                            setOwnerFilter(ANY_OWNER);
                            setMinHours('');
                          }}
                        >
                          Clear filters
                        </Button>
                      )}
                    </div>
                  ),
                }}
                exportConfig={false}
                onRefresh={() => void query.refetch()}
                isRefreshing={query.isFetching}
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
      )}

      {assignTarget && (
        <AssignLeadDialog
          leadCode={assignTarget.lead_code}
          currentOwnerName={assignTarget.owner_name}
          submitting={assign.isPending}
          onDone={() => setAssignTarget(null)}
          onConfirm={async (ownerUserId, note) => {
            await assign.mutateAsync({ id: assignTarget.id, ownerUserId, note });
          }}
        />
      )}

      {nudgeTarget && (
        <NudgeAssigneeDialog
          leadCode={nudgeTarget.lead_code}
          assigneeName={nudgeTarget.owner_name}
          submitting={nudge.isPending}
          onDone={() => setNudgeTarget(null)}
          onConfirm={async (note) => {
            if (!nudgeTarget.owner_user_id) return;
            await nudge.mutateAsync({
              id: nudgeTarget.id,
              ownerUserId: nudgeTarget.owner_user_id,
              note,
            });
          }}
        />
      )}
    </div>
  );
}

function formatMyr(value: string): string {
  const amount = Number(value);
  if (Number.isNaN(amount)) return value;
  return `RM ${amount.toLocaleString('en-MY', { maximumFractionDigits: 0 })}`;
}
