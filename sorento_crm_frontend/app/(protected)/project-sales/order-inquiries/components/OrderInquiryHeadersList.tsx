'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useHasPermission } from '@/hooks/usePermissions';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { getUserLookup } from '@/services/userSelectService';
import {
  useOrderInquiryHeaders,
  useOrderInquiryWorklistSummary,
  useParkedPreview,
  useReserveStatusSummary,
  useSendParked,
  type ParkedKind,
} from '../../_shared/hooks/useOrderInquiry';
import { ParkedSendDialog } from '@/components/common/ParkedSendDialog';
import { formatInquiryQty } from '../../_shared/lib/orderInquiryWorklist';
import { ConsignmentBadge } from '@/app/(protected)/scm/lib/docType';
import {
  ORDER_INQUIRY_RESERVE_STATUSES,
  orderInquiryHeaderStatusLabel,
  orderInquiryHeaderStatusVariant,
  orderInquiryReserveStatusLabel,
  orderInquiryReserveStatusVariant,
  type OrderInquiryReserveStatus,
} from '../../_shared/lib/orderInquiryHeaderStatus';
import type {
  OrderInquiryHeader,
  OrderInquiryHeaderListParams,
} from '../../_shared/types/orderInquiry.types';

/** `PLAN-oi-header-list-detail.md`, AC-HL-02: keyed under the same permission the row
 * worklist listing already uses (`projects.projects.view`), a stable id after it so the
 * two listings' column preferences never collide. */
const LISTING_KEY = 'projects.projects.view::order-inquiry-headers';

type StateFilter = 'all' | 'outstanding' | 'completed';

const ACKNOWLEDGE_PERMISSION = 'projects.order_inquiries.acknowledge';
const RESERVE_PERMISSION = 'projects.order_inquiries.reserve';

function reserveFrom(value: string | null): OrderInquiryReserveStatus | '' {
  return (ORDER_INQUIRY_RESERVE_STATUSES as string[]).includes(value ?? '')
    ? (value as OrderInquiryReserveStatus)
    : '';
}

function stateFrom(value: string | null): StateFilter {
  return value === 'all' || value === 'completed' ? value : 'outstanding';
}

const DEFAULT_SORTING: SortingState = [{ id: 'raised_at', desc: false }];

/**
 * The Documents view (S4): one row per order inquiry HEADER, oldest raised first,
 * Outstanding by default (the journey's own first screen). The per-line worklist
 * purchasing already uses stays one toggle away, unchanged (AC-HL-01, `?view=lines`).
 *
 * Every piece of screen state that narrows or orders the list travels in the URL
 * (AC-HL-03) - `?state=&sort=&dir=&query=&page=&limit=&raised_by=&agent=&project=` -
 * so a reload or a shared link reopens on exactly what was on screen, the same
 * `router.replace` pattern `OrderInquiriesClient.tsx` (the Lines view) already uses for
 * its own filters.
 */
export function OrderInquiryHeadersList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [reserveFilter, setReserveFilter] = useState<OrderInquiryReserveStatus | ''>(() =>
    reserveFrom(searchParams.get('reserve')),
  );
  // A reserve status spans outstanding and completed documents, so landing on one (the
  // email link) with no explicit state shows All.
  const [stateFilter, setStateFilter] = useState<StateFilter>(() =>
    searchParams.get('state') == null && reserveFrom(searchParams.get('reserve')) !== ''
      ? 'all'
      : stateFrom(searchParams.get('state')),
  );
  const [sorting, setSorting] = useState<SortingState>(() => {
    const sort = searchParams.get('sort');
    if (!sort) return DEFAULT_SORTING;
    return [{ id: sort, desc: searchParams.get('dir') === 'desc' }];
  });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch(searchParams.get('query') ?? '');
  const [raisedByFilter, setRaisedByFilter] = useState(
    () => searchParams.get('raised_by') ?? '',
  );
  const [agentFilter, setAgentFilter] = useState(() => searchParams.get('agent') ?? '');
  const [projectFilter, setProjectFilter] = useState(
    () => searchParams.get('project') ?? '',
  );
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: Math.max(0, Number(searchParams.get('page') ?? '1') - 1),
    pageSize: Number(searchParams.get('limit') ?? '25'),
  });

  // URL-synced (AC-HL-03): a reload or a shared link opens on the same toggle, sort,
  // search and page. `replace`, not `push` - turning a dial is not a place to go back to.
  useEffect(() => {
    const next = new URLSearchParams();
    if (stateFilter !== 'outstanding') next.set('state', stateFilter);
    if (searchQuery) next.set('query', searchQuery);
    const sort = sorting[0];
    if (sort && (sort.id !== 'raised_at' || sort.desc)) {
      next.set('sort', sort.id);
      next.set('dir', sort.desc ? 'desc' : 'asc');
    }
    if (pagination.pageIndex > 0) next.set('page', String(pagination.pageIndex + 1));
    if (pagination.pageSize !== 25) next.set('limit', String(pagination.pageSize));
    if (raisedByFilter) next.set('raised_by', raisedByFilter);
    if (agentFilter) next.set('agent', agentFilter);
    if (projectFilter) next.set('project', projectFilter);
    if (reserveFilter) next.set('reserve', reserveFilter);
    const nextQuery = next.toString();
    if (nextQuery === searchParams.toString()) return;
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
  }, [
    stateFilter,
    searchQuery,
    sorting,
    pagination.pageIndex,
    pagination.pageSize,
    raisedByFilter,
    agentFilter,
    projectFilter,
    reserveFilter,
    pathname,
    router,
    searchParams,
  ]);

  useResetPageOnFilterChange(setPagination, [
    stateFilter,
    searchQuery,
    raisedByFilter,
    agentFilter,
    projectFilter,
    reserveFilter,
  ]);

  const params = useMemo<OrderInquiryHeaderListParams>(
    () => ({
      state: stateFilter,
      query: searchQuery || undefined,
      raised_by: raisedByFilter || undefined,
      agent: agentFilter || undefined,
      project: projectFilter || undefined,
      reserve: reserveFilter || undefined,
      sort: sorting[0]?.id ?? 'raised_at',
      dir: sorting[0]?.desc ? 'desc' : 'asc',
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
    }),
    [
      stateFilter,
      searchQuery,
      raisedByFilter,
      agentFilter,
      projectFilter,
      reserveFilter,
      sorting,
      pagination.pageIndex,
      pagination.pageSize,
    ],
  );

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } =
    useOrderInquiryHeaders(params);

  // Raised by, Agent, Project options (AC-HL-05, W; B1/B2 reviewer, fix round 22 Sep
  // 2026). `raised_by` is the shared `services/userSelectService` (CLAUDE.md's own
  // default for a person picker) - the header contract's own `raised_by` is `users.id`,
  // exact. Agent and Project both reuse the worklist's own summary facet
  // (`useOrderInquiryWorklistSummary`, `OrderInquiriesClient.tsx`'s own
  // `summary.data?.agents`/`summary.data?.projects`), never a second endpoint - and both
  // option VALUEs are now the same TEXT the backend compares to:
  //   - Agent: `entry.label` is already `person_label or sales_agent` (the facet's own
  //     Python fallback, `order_inquiry_worklist_service.py::_agents`) - the SQL-side
  //     equivalent (`_AGENT_NAME`, `order_inquiry_header_service.py`) is what
  //     `&agent=<name>` compares against, so the two always agree even when an agent
  //     carries no `person_label` (80 of 80 on the prod copy).
  //   - Project: `entry.id` (the facet's own `_PROJECT_TITLE` text, not a `projects.id`)
  //     is what `&project=<title>` compares against - an adopted AutoCount order has no
  //     registered `Project` row (0 of 738 headers on the prod copy), so the REGISTERED
  //     project list (`useProjects`) matched nothing and is dropped here.
  const usersQuery = useQuery({
    queryKey: ['user-lookup', 'with-inactive'],
    queryFn: () => getUserLookup({ include_inactive: true }),
    staleTime: 5 * 60 * 1000,
  });
  const worklistSummary = useOrderInquiryWorklistSummary({});

  const canAcknowledge = useHasPermission(ACKNOWLEDGE_PERMISSION);
  const canReserve = useHasPermission(RESERVE_PERMISSION);
  const reserveSummary = useReserveStatusSummary();
  const [parkedKind, setParkedKind] = useState<ParkedKind | null>(null);
  const parkedPreview = useParkedPreview(parkedKind);
  const sendParked = useSendParked(parkedKind ?? 'requests');
  const myParkedRequests = reserveSummary.data?.my_parked_requests ?? 0;
  const myParkedNotices = reserveSummary.data?.my_parked_notices ?? 0;
  const sendActions = useMemo(
    () => [
      ...(canAcknowledge
        ? [
            {
              key: 'send-parked-requests',
              label: `Send all parked requests to CS (${myParkedRequests})`,
              disabled: myParkedRequests === 0,
              disabledReason: 'Nothing of yours is parked',
              onClick: () => setParkedKind('requests'),
            },
          ]
        : []),
      ...(canReserve
        ? [
            {
              key: 'send-parked-notices',
              label: `Send all parked reserves to purchasing (${myParkedNotices})`,
              disabled: myParkedNotices === 0,
              disabledReason: 'Nothing of yours is parked',
              onClick: () => setParkedKind('notices'),
            },
          ]
        : []),
    ],
    [canAcknowledge, canReserve, myParkedRequests, myParkedNotices],
  );

  const filterOptions = useMemo(
    () => ({
      reserve: ORDER_INQUIRY_RESERVE_STATUSES.map((status) => ({
        value: status,
        label: `${orderInquiryReserveStatusLabel(status)} (${reserveSummary.data?.[status] ?? 0})`,
      })),
      raisedBy: (usersQuery.data ?? []).map((user) => ({
        value: user.id,
        label: user.name || 'Unnamed user',
      })),
      agents: (worklistSummary.data?.agents ?? []).map((entry) => ({
        value: entry.label,
        label: entry.label,
      })),
      projects: (worklistSummary.data?.projects ?? []).map((entry) => ({
        value: entry.id,
        label: entry.label,
      })),
    }),
    [usersQuery.data, worklistSummary.data, reserveSummary.data],
  );

  const rows = useMemo<OrderInquiryHeader[]>(() => data?.data ?? [], [data]);

  const detailSearch = useMemo(
    () =>
      buildDetailSearch(
        { pageIndex: pagination.pageIndex, pageSize: pagination.pageSize, sorting, searchQuery },
        {
          state: stateFilter !== 'outstanding' ? stateFilter : undefined,
          raised_by: raisedByFilter || undefined,
          agent: agentFilter || undefined,
          project: projectFilter || undefined,
          reserve: reserveFilter || undefined,
        },
      ),
    [
      pagination.pageIndex,
      pagination.pageSize,
      sorting,
      searchQuery,
      stateFilter,
      raisedByFilter,
      agentFilter,
      projectFilter,
      reserveFilter,
    ],
  );
  const detailHref = useCallback(
    (header: OrderInquiryHeader) =>
      `/project-sales/order-inquiries/${header.id}${detailSearch ? `?${detailSearch}` : ''}`,
    [detailSearch],
  );

  const columns = useMemo<ColumnDef<OrderInquiryHeader>[]>(
    () => [
      {
        accessorKey: 'raised_at',
        header: ({ column }) => <DataGridColumnHeader title="Raised at" column={column} />,
        // S2 (reviewer, fix round 22 Sep 2026): 185, wide enough for the full
        // date-time string not to truncate at 1280; `title` so a narrower viewport
        // still carries the full value on hover/long-press.
        size: 185,
        meta: { headerTitle: 'Raised at', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) =>
          row.original.raised_at ? (
            <span
              className="block truncate whitespace-nowrap"
              title={formatDateTimeInMalaysia(row.original.raised_at)}
            >
              {formatDateTimeInMalaysia(row.original.raised_at)}
            </span>
          ) : (
            <span className="text-muted-foreground">Unknown</span>
          ),
      },
      {
        accessorKey: 'inquiry_no',
        header: ({ column }) => <DataGridColumnHeader title="OI no" column={column} />,
        size: 140,
        meta: { headerTitle: 'OI no', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => (
          <span className="block truncate tabular-nums font-medium" title={row.original.inquiry_no}>
            {row.original.inquiry_no}
          </span>
        ),
      },
      {
        accessorKey: 'so_number',
        header: ({ column }) => <DataGridColumnHeader title="S/O no" column={column} />,
        size: 140,
        meta: { headerTitle: 'S/O no', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => {
          const { so_number, sales_order_id, so_doc_type } = row.original;
          if (!so_number) return <span className="text-muted-foreground">-</span>;
          // AC-CONSIGN-CRM: a consignment reads as one beside its number.
          const number = !sales_order_id ? (
            <span className="block truncate" title={so_number}>
              {so_number}
            </span>
          ) : (
            <Link
              href={`/scm/sales-orders/${sales_order_id}`}
              onClick={(e) => e.stopPropagation()}
              className="block truncate font-medium text-primary hover:underline"
              title={so_number}
            >
              {so_number}
            </Link>
          );
          return (
            <div className="flex min-w-0 flex-col gap-0.5">
              {number}
              <ConsignmentBadge docType={so_doc_type} size="xs" />
            </div>
          );
        },
      },
      {
        // AC-LS-03's sort vocabulary is `raised_by`, not the accessor field
        // `raised_by_name` - same reason `customer`/`project`/`agent` below take an
        // explicit id too, so the sort param this column sends is one the contract
        // (and this mock's own `SORTERS` map) actually recognises.
        id: 'raised_by',
        accessorFn: (row) => row.raised_by_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Raised by" column={column} />,
        size: 150,
        meta: { headerTitle: 'Raised by', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) =>
          row.original.raised_by_name ? (
            <span className="block truncate" title={row.original.raised_by_name}>
              {row.original.raised_by_name}
            </span>
          ) : (
            <span className="text-muted-foreground">Not recorded</span>
          ),
      },
      {
        accessorKey: 'lines_total',
        header: ({ column }) => (
          <DataGridColumnHeader title="Lines" column={column} className="justify-end" />
        ),
        size: 90,
        meta: {
          headerTitle: 'Lines',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
        // AC-HL-04: the visible figure is the total; an OUTSTANDING row's title also
        // states how many of them still wait for a confirm.
        cell: ({ row }) => {
          const { lines_total, lines_to_confirm, status } = row.original;
          const title =
            status === 'outstanding' ? `${lines_to_confirm} / ${lines_total}` : String(lines_total);
          return (
            <span className="block" title={title}>
              {lines_total}
            </span>
          );
        },
      },
      {
        accessorKey: 'qty_total',
        header: ({ column }) => (
          <DataGridColumnHeader title="Qty" column={column} className="justify-end" />
        ),
        size: 100,
        meta: {
          headerTitle: 'Qty',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
        cell: ({ row }) => formatInquiryQty(row.original.qty_total),
      },
      {
        id: 'customer',
        accessorFn: (row) => row.customer_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
        // S2 (reviewer, fix round 22 Sep 2026): trimmed 170->150 so Status still fits
        // at 1280 once Raised at widened.
        size: 150,
        meta: { headerTitle: 'Customer', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) =>
          row.original.customer_name ? (
            <span className="block truncate" title={row.original.customer_name}>
              {row.original.customer_name}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
      },
      {
        id: 'project',
        accessorFn: (row) => row.project_title ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Project" column={column} />,
        // S2 (reviewer, fix round 22 Sep 2026): trimmed 190->150, same reason.
        size: 150,
        meta: { headerTitle: 'Project', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) =>
          row.original.project_title ? (
            <span className="block truncate" title={row.original.project_title}>
              {row.original.project_title}
            </span>
          ) : (
            <span className="text-muted-foreground">No project</span>
          ),
      },
      {
        id: 'agent',
        accessorFn: (row) => row.agent_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        size: 130,
        meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) =>
          row.original.agent_name ? (
            <span className="block truncate" title={row.original.agent_name}>
              {row.original.agent_name}
            </span>
          ) : (
            <span className="text-muted-foreground">Not assigned</span>
          ),
      },
      {
        accessorKey: 'so_date',
        header: ({ column }) => <DataGridColumnHeader title="SO date" column={column} />,
        size: 120,
        meta: { headerTitle: 'SO date' },
        cell: ({ row }) =>
          row.original.so_date ? (
            formatDateInMalaysia(row.original.so_date)
          ) : (
            <span className="text-muted-foreground">No date</span>
          ),
      },
      {
        id: 'reserve_status',
        header: () => <span>Reserve</span>,
        size: 190,
        enableSorting: false,
        meta: { headerTitle: 'Reserve' },
        cell: ({ row }) => {
          const { reserve_status, parked_by_name } = row.original;
          if (!reserve_status) return <span className="text-muted-foreground">-</span>;
          return (
            <div className="flex min-w-0 flex-col items-start gap-0.5">
              <Badge
                variant={orderInquiryReserveStatusVariant(reserve_status)}
                appearance="light"
                size="md"
              >
                {orderInquiryReserveStatusLabel(reserve_status)}
              </Badge>
              {parked_by_name && reserve_status === 'parked' ? (
                <span className="block max-w-full truncate text-xs text-muted-foreground" title={parked_by_name}>
                  {parked_by_name}
                </span>
              ) : null}
            </div>
          );
        },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 130,
        meta: { headerTitle: 'Status' },
        cell: ({ row }) => (
          <Badge
            variant={orderInquiryHeaderStatusVariant(row.original.status)}
            appearance="light"
            size="md"
          >
            {orderInquiryHeaderStatusLabel(row.original.status)}
          </Badge>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((data?.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const filtersActive =
    (raisedByFilter ? 1 : 0) +
    (agentFilter ? 1 : 0) +
    (projectFilter ? 1 : 0) +
    (reserveFilter ? 1 : 0);
  const isFiltered = Boolean(searchQuery) || filtersActive > 0;

  // AC-HL-07: Outstanding empty is a work-queue empty state with a way out; All empty is
  // a genuinely empty book. Anything narrowed by search/filter reads as a search miss,
  // the same wording the sibling Lines view and every other listing use.
  const emptyMessage = isFiltered
    ? 'No order inquiry matches this search and filter.'
    : stateFilter === 'outstanding'
      ? 'Nothing to confirm'
      : stateFilter === 'all'
        ? 'No order inquiries yet'
        : 'No completed order inquiries yet';

  return (
    <DataGrid
      table={table}
      recordCount={data?.total || 0}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      error={error}
      onRetry={() => void refetch()}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      emptyMessage={emptyMessage}
      rowHref={detailHref}
      listingKey={LISTING_KEY}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <>
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                  placeholder="Search OI, SO, customer, project or product..."
                  className="w-64"
                />
                <ToggleGroup
                  type="single"
                  variant="outline"
                  value={stateFilter}
                  onValueChange={(v) => v && setStateFilter(v as StateFilter)}
                >
                  <ToggleGroupItem value="all" className="px-3">
                    All
                  </ToggleGroupItem>
                  <ToggleGroupItem value="outstanding" className="px-3">
                    Outstanding
                  </ToggleGroupItem>
                  <ToggleGroupItem value="completed" className="px-3">
                    Completed
                  </ToggleGroupItem>
                </ToggleGroup>
              </>
            }
            secondaryActions={sendActions}
            secondaryActionsAsMenu
            filters={{
              kind: 'custom',
              // The list stays readable (and the state toggle reachable) while a filter is set.
              modal: false,
              active: filtersActive > 0,
              activeCount: filtersActive,
              activeSummary: reserveFilter
                ? {
                    label: `Reserve: ${orderInquiryReserveStatusLabel(reserveFilter)}`,
                    onClear: () => setReserveFilter(''),
                  }
                : undefined,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="oi-header-reserve" className="mb-1 block">
                      Reserve
                    </Label>
                    <SearchableSelect
                      id="oi-header-reserve"
                      value={reserveFilter}
                      onChange={(value) => {
                        setReserveFilter(reserveFrom(value));
                        if (value) setStateFilter('all');
                      }}
                      options={filterOptions.reserve}
                      placeholder="Any reserve status"
                      clearable
                    />
                  </div>
                  <div>
                    <Label htmlFor="oi-header-raised-by" className="mb-1 block">
                      Raised by
                    </Label>
                    <SearchableSelect
                      id="oi-header-raised-by"
                      value={raisedByFilter}
                      onChange={setRaisedByFilter}
                      options={filterOptions.raisedBy}
                      loadError={usersQuery.error}
                      onRetry={() => void usersQuery.refetch()}
                      placeholder="Anyone"
                      clearable
                    />
                  </div>
                  <div>
                    <Label htmlFor="oi-header-agent" className="mb-1 block">
                      Agent
                    </Label>
                    <SearchableSelect
                      id="oi-header-agent"
                      value={agentFilter}
                      onChange={setAgentFilter}
                      options={filterOptions.agents}
                      placeholder="Any agent"
                      clearable
                    />
                  </div>
                  <div>
                    <Label htmlFor="oi-header-project" className="mb-1 block">
                      Project
                    </Label>
                    <SearchableSelect
                      id="oi-header-project"
                      value={projectFilter}
                      onChange={setProjectFilter}
                      options={filterOptions.projects}
                      placeholder="Any project"
                      clearable
                    />
                  </div>
                  {filtersActive > 0 ? (
                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setRaisedByFilter('');
                          setAgentFilter('');
                          setProjectFilter('');
                          setReserveFilter('');
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  ) : null}
                </div>
              ),
            }}
            exportConfig={false}
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
      <ParkedSendDialog
        open={parkedKind !== null}
        onOpenChange={(next) => {
          if (!next) setParkedKind(null);
        }}
        title={
          parkedKind === 'notices'
            ? 'Send all parked reserves to purchasing'
            : 'Send all parked requests to CS'
        }
        preview={parkedPreview.data}
        isLoading={parkedPreview.isLoading}
        onSend={() => sendParked.mutateAsync()}
        isSending={sendParked.isPending}
      />
    </DataGrid>
  );
}

export default OrderInquiryHeadersList;
