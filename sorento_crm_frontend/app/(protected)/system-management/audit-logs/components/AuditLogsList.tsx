'use client';

import { useMemo, useRef, useState, useEffect } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  PaginationState,
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { X } from 'lucide-react';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { actorDisplay, actorKindLabel, authMethodLabel } from '@/lib/audit-actor';
import { getUsersSelect } from '@/services/userSelectService';
import { useAuditLogs } from '../hooks/useAuditLogs';
import type { AuditLog } from '../types/auditLog.types';

const ACTION_ALL = '__all__';
const ENTITY_TYPE_ALL = '__all__';

// Entity types the audit trigger records (stored value = table/entity name). A
// fixed list beats free text so a singular/plural typo ("user" vs "users") can't
// silently return zero rows. Keep in sync with the __audit_track__ models.
// Exported so a test can pin the list: a model that starts being audited without
// an entry here is simply unfilterable, with nothing on screen to say so.
export const ENTITY_TYPES = [
  'product',
  'orders',
  'customer',
  'suppliers',
  'purchase_request',
  'complaint',
  'stock_inquiry',
  'forms',
  'users',
  'promotions',
  'attachment',
  'notification',
  'impersonation_session',
  'contact_impersonation_session',
] as const;

function actionBadgeVariant(
  action: string,
): 'success' | 'warning' | 'destructive' | 'secondary' | 'info' {
  switch (action.toUpperCase()) {
    case 'INSERT':
    case 'CREATE':
      return 'success';
    case 'UPDATE':
      return 'warning';
    case 'DELETE':
      return 'destructive';
    case 'IMPORT':
      return 'info';
    default:
      return 'secondary';
  }
}

/** The record in words; the raw entity_id is never shown (no UUIDs in the UI). */
function recordLabel(log: AuditLog): string {
  if (log.entity_label) return log.entity_label;
  const pretty = log.entity_type.replace(/_/g, ' ');
  return pretty.charAt(0).toUpperCase() + pretty.slice(1);
}

function prettyJson(value: Record<string, unknown> | null | undefined): string {
  if (value == null) return '-';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default function AuditLogsList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [entityType, setEntityType] = useState('');
  const [action, setAction] = useState(ACTION_ALL);
  const [userId, setUserId] = useState('');
  const {
    value: entityIdInput,
    setValue: setEntityIdInput,
    debouncedValue: entityId,
    isSettling: entityIdSettling,
  } = useDebouncedSearch();
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);
  // Seed the date range from the URL so a drill-down link (e.g. from the System
  // Health "Audit Activity" tile) lands here pre-filtered to that day.
  const [dateFrom, setDateFrom] = useState(() => searchParams.get('changed_from') ?? '');
  const [dateTo, setDateTo] = useState(() => searchParams.get('changed_to') ?? '');

  const { data, isLoading, isPlaceholderData, isError, error, refetch, isFetching } = useAuditLogs({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    entity_type: entityType || undefined,
    entity_id: entityId || undefined,
    user_id: userId || undefined,
    action: action !== ACTION_ALL ? action : undefined,
    changed_from: dateFrom || undefined,
    changed_to: dateTo || undefined,
  });

  // Users for the "User" picker - the audit user_id is a UUID, so a name must
  // resolve to an id (free text would 500 the UUID-typed column).
  const {
    data: users,
    error: usersError,
    refetch: refetchUsers,
  } = useQuery({
    queryKey: ['audit-user-select'],
    queryFn: () => getUsersSelect(),
    staleTime: 5 * 60 * 1000,
  });
  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [entityId]);

  const activeFilterCount =
    (entityType ? 1 : 0) +
    (action !== ACTION_ALL ? 1 : 0) +
    (userId ? 1 : 0) +
    (dateFrom || dateTo ? 1 : 0);

  // Date inputs are day-granular; widen "to" to end-of-day so the chosen day is inclusive.
  const setFromDay = (v: string) => {
    setDateFrom(v ? `${v}T00:00:00` : '');
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };
  const setToDay = (v: string) => {
    setDateTo(v ? `${v}T23:59:59` : '');
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };

  // Human-readable label for the active date-range drill-down ("Showing 2026-07-02").
  const dateRangeLabel = useMemo(() => {
    if (!dateFrom) return null;
    const fromDay = dateFrom.slice(0, 10);
    const toDay = dateTo ? dateTo.slice(0, 10) : '';
    return toDay && toDay !== fromDay ? `${fromDay} → ${toDay}` : fromDay;
  }, [dateFrom, dateTo]);

  const clearDateRange = () => {
    setDateFrom('');
    setDateTo('');
    setPagination((p) => ({ ...p, pageIndex: 0 }));
    router.replace(pathname);
  };

  const columns = useMemo<ColumnDef<AuditLog>[]>(
    () => [
      {
        accessorKey: 'changed_at',
        header: ({ column }) => <DataGridColumnHeader title="Changed At" column={column} />,
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-sm">
            {formatDateTimeInMalaysia(row.original.changed_at)}
          </span>
        ),
        size: 180,
        meta: { headerTitle: 'Changed At', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'entity_type',
        header: ({ column }) => <DataGridColumnHeader title="Entity Type" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary" appearance="light">
            {row.original.entity_type}
          </Badge>
        ),
        size: 150,
        meta: { headerTitle: 'Entity Type' },
      },
      {
        accessorKey: 'entity_id',
        header: ({ column }) => <DataGridColumnHeader title="Record" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate text-xs text-muted-foreground" title={recordLabel(row.original)}>
            {recordLabel(row.original)}
          </span>
        ),
        size: 160,
        meta: { headerTitle: 'Record' },
      },
      {
        accessorKey: 'action',
        header: ({ column }) => <DataGridColumnHeader title="Action" column={column} />,
        cell: ({ row }) => (
          <Badge variant={actionBadgeVariant(row.original.action)} appearance="light">
            {row.original.action}
          </Badge>
        ),
        size: 110,
        meta: { headerTitle: 'Action' },
      },
      {
        accessorKey: 'user_display_name',
        header: ({ column }) => <DataGridColumnHeader title="Actor" column={column} />,
        cell: ({ row }) => {
          const actor = actorDisplay(row.original);
          return (
            <span className="block truncate text-sm" title={actor}>
              {actor}
            </span>
          );
        },
        size: 170,
        meta: { headerTitle: 'Actor' },
      },
      {
        accessorKey: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        cell: ({ row }) => {
          const desc = row.original.description || '-';
          return (
            <span className="block truncate text-sm text-muted-foreground" title={desc}>
              {desc}
            </span>
          );
        },
        size: 260,
        meta: { headerTitle: 'Description' },
      },
      {
        accessorKey: 'ip_address',
        header: ({ column }) => <DataGridColumnHeader title="IP" column={column} />,
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">{row.original.ip_address || '-'}</span>
        ),
        size: 130,
        meta: { headerTitle: 'IP' },
      },
      {
        id: 'details',
        header: () => <span className="sr-only">Details</span>,
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              setSelectedLog(row.original);
            }}
          >
            Details
          </Button>
        ),
        size: 100,
        meta: { headerTitle: 'Details' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
  });

  const clearFilters = () => {
    setEntityType('');
    setAction(ACTION_ALL);
    setUserId('');
    setDateFrom('');
    setDateTo('');
    setPagination((p) => ({ ...p, pageIndex: 0 }));
    router.replace(pathname);
  };

  if (isError) {
    return (
      <Card>
        <CardTable>
          <div className="p-8 text-center text-muted-foreground">
            <p className="font-medium text-destructive">Failed to load audit logs.</p>
            <p className="text-sm mt-1">
              {error instanceof Error ? error.message : 'Please try again.'}
            </p>
            <Button variant="outline" size="sm" className="mt-4" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        </CardTable>
      </Card>
    );
  }

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        emptyMessage="No audit log entries found."
        onRowClick={(row) => setSelectedLog(row)}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={entityIdInput}
                  onChange={setEntityIdInput}
                  isSettling={isSearchInFlight(entityIdSettling, isFetching, entityId)}
                  placeholder="Search by entity ID..."
                  className="w-72"
                />
              }
              filters={{
                kind: 'custom',
                active: activeFilterCount > 0,
                activeCount: activeFilterCount,
                content: (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Entity type</label>
                      <SearchableSelect
                        value={entityType || ENTITY_TYPE_ALL}
                        onChange={(v) => setEntityType(v === ENTITY_TYPE_ALL ? '' : v)}
                        options={[
                          { value: ENTITY_TYPE_ALL, label: 'All entity types' },
                          ...ENTITY_TYPES.map((t) => ({ value: t, label: t })),
                        ]}
                        placeholder="All entity types"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Action</label>
                      <SearchableSelect
                        value={action}
                        onChange={setAction}
                        options={[
                          { value: ACTION_ALL, label: 'All actions' },
                          { value: 'INSERT', label: 'Insert' },
                          { value: 'CREATE', label: 'Create' },
                          { value: 'UPDATE', label: 'Update' },
                          { value: 'DELETE', label: 'Delete' },
                          { value: 'IMPORT', label: 'Import' },
                        ]}
                        placeholder="Action"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">User</label>
                      <SearchableSelect
                        value={userId}
                        onChange={setUserId}
                        loadError={usersError}
                        onRetry={() => void refetchUsers()}
                        options={[
                          { value: '', label: 'Any user' },
                          ...(users?.map((u) => ({
                            value: u.id,
                            label: u.name || u.email || 'Unnamed user',
                            searchText: `${u.name ?? ''} ${u.email ?? ''}`.trim(),
                          })) ?? []),
                        ]}
                        placeholder="Any user"
                        emptyMessage="No users found."
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-muted-foreground">From date</label>
                        <Input
                          type="date"
                          value={dateFrom ? dateFrom.slice(0, 10) : ''}
                          max={dateTo ? dateTo.slice(0, 10) : undefined}
                          onChange={(e) => setFromDay(e.target.value)}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-muted-foreground">To date</label>
                        <Input
                          type="date"
                          value={dateTo ? dateTo.slice(0, 10) : ''}
                          min={dateFrom ? dateFrom.slice(0, 10) : undefined}
                          onChange={(e) => setToDay(e.target.value)}
                        />
                      </div>
                    </div>
                    {activeFilterCount > 0 && (
                      <Button variant="ghost" size="sm" className="w-full" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: 'audit_logs_export.xlsx' }}
              onRefresh={() => void refetch()}
              isRefreshing={isFetching && !isLoading}
            />
            {dateRangeLabel && (
              <div className="mt-3 flex items-center gap-2" data-testid="audit-date-filter-active">
                <Badge variant="info" appearance="light">
                  Showing {dateRangeLabel}
                </Badge>
                <Button variant="ghost" size="sm" onClick={clearDateRange}>
                  <X className="size-3.5" />
                  Clear date filter
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

      <Dialog open={selectedLog !== null} onOpenChange={(open) => !open && setSelectedLog(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Audit entry details</DialogTitle>
            <DialogDescription>
              {selectedLog
                ? `${selectedLog.action} on ${selectedLog.entity_type} · ${formatDateTimeInMalaysia(
                    selectedLog.changed_at,
                  )}`
                : ''}
            </DialogDescription>
          </DialogHeader>
          {selectedLog && (
            <DialogBody className="space-y-4">
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">Entity type</dt>
                  <dd>{selectedLog.entity_type}</dd>
                </div>
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">Record</dt>
                  <dd className="break-words">{recordLabel(selectedLog)}</dd>
                </div>
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">Actor</dt>
                  <dd className="break-words">{actorDisplay(selectedLog)}</dd>
                </div>
                {actorKindLabel(selectedLog.actor_type) && (
                  <div>
                    <dt className="text-xs font-medium text-muted-foreground">Actor kind</dt>
                    <dd>{actorKindLabel(selectedLog.actor_type)}</dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">Sign-in method</dt>
                  <dd>{authMethodLabel(selectedLog.auth_method)}</dd>
                </div>
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">IP address</dt>
                  <dd>{selectedLog.ip_address || '-'}</dd>
                </div>
                {selectedLog.description && (
                  <div className="col-span-2">
                    <dt className="text-xs font-medium text-muted-foreground">Description</dt>
                    <dd>{selectedLog.description}</dd>
                  </div>
                )}
              </dl>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Old values</p>
                  <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">
                    {prettyJson(selectedLog.old_values)}
                  </pre>
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">New values</p>
                  <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">
                    {prettyJson(selectedLog.new_values)}
                  </pre>
                </div>
              </div>
            </DialogBody>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
