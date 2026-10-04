'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
} from '@tanstack/react-table';
import { DateRange } from 'react-day-picker';
import { formatDateTime, getInitials } from '@/lib/helpers';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import {
  DataGrid,
  DataGridApiFetchParams,
  DataGridApiResponse,
} from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { SystemLog } from '@/app/models/system';
import { User } from '@/app/models/user';
import { LogActionsCell } from './log-actions-cell';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

const ActivityLogList = () => {
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'createdAt', desc: true },
  ]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [dateRangeFilter, setDateRangeFilter] = useState<
    DateRange | undefined
  >();

  // Fetch users from the server API
  const fetchOrders = async ({
    pageIndex,
    pageSize,
    sorting,
    searchQuery,
    dateRangeFilter,
  }: DataGridApiFetchParams & {
    dateRangeFilter: DateRange | undefined;
  }): Promise<DataGridApiResponse<SystemLog>> => {
    const sortField = sorting?.[0]?.id || '';
    const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';

    const params = new URLSearchParams({
      page: String(pageIndex + 1),
      limit: String(pageSize),
      ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
      ...(searchQuery ? { query: searchQuery } : {}),
      ...(dateRangeFilter?.from
        ? { createdAtFrom: dateRangeFilter.from.toISOString() }
        : {}),
      ...(dateRangeFilter?.to
        ? { createdAtTo: dateRangeFilter.to.toISOString() }
        : {}),
    });

    const response = await fetch(
      `/api/user-management/logs/?${params.toString()}`,
    );

    if (!response.ok) {
      throw new Error(
        'Oops! Something didn’t go as planned. Please try again in a moment.',
      );
    }

    return response.json();
  };

  // Users query
  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      'system-logs',
      pagination,
      sorting,
      searchQuery,
      dateRangeFilter,
    ],
    queryFn: () =>
      fetchOrders({
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
        dateRangeFilter,
      }),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });

  const handleDateRangeApply = (range: DateRange | undefined) => {
    setDateRangeFilter(range);
    setPagination({ ...pagination, pageIndex: 0 });
  };

  const handleDateRangeReset = () => {
    setDateRangeFilter(undefined);
    setPagination({ ...pagination, pageIndex: 0 });
  };

  const columns = useMemo<ColumnDef<SystemLog>[]>(
    () => [
      buildSelectColumn<SystemLog>(),
      {
        accessorKey: 'user_name',
        id: 'user_name',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="User"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const user = row.original.user as User;
          const avatarUrl = user.avatar || null;
          const initials = getInitials(user.name || user.email);

          return (
            <Link
              href={`/user-management/users/${user.id}`}
              className="group inline-flex items-center gap-1.5"
            >
              <Avatar className="size-6">
                {avatarUrl && (
                  <AvatarImage src={avatarUrl} alt={user.name || ''} />
                )}
                <AvatarFallback className="bg-warning text-warning-foreground text-xs">
                  {initials}
                </AvatarFallback>
              </Avatar>
              <span className="font-medium text-foreground group-hover:text-primary">
                {user.name || user.email}
              </span>
            </Link>
          );
        },
        size: 125,
        meta: {
          headerTitle: 'User',
          headerClassName: 'min-w-56',
          skeleton: <Skeleton className="w-28 h-7" />,
        },
        enableSorting: true,
        enableHiding: false,
      },
      {
        accessorKey: 'entityType',
        id: 'entityType',
        header: ({ column }) => (
          <DataGridColumnHeader title="Event" column={column} />
        ),
        cell: ({ row }) => {
          const event = row.original.event as string;
          const entityType = row.original.entityType as string;

          return (
            <Badge variant="secondary">
              {entityType}: {event}
            </Badge>
          );
        },
        size: 125,
        meta: {
          headerTitle: 'Event',
          skeleton: <Skeleton className="w-14 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'description',
        id: 'description',
        header: ({ column }) => (
          <DataGridColumnHeader title="Description" column={column} />
        ),
        cell: (info) => info.getValue() as string,
        size: 175,
        meta: {
          headerTitle: 'Description',
          skeleton: <Skeleton className="w-40 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'ipAddress',
        id: 'ipAddress',
        header: ({ column }) => (
          <DataGridColumnHeader title="IP Address" column={column} />
        ),
        cell: (info) => info.getValue() as string,
        size: 100,
        meta: {
          headerTitle: 'IP Address',
          skeleton: <Skeleton className="w-20 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'createdAt',
        id: 'createdAt',
        header: ({ column }) => (
          <DataGridColumnHeader title="Timestamp" column={column} />
        ),
        cell: (info) => formatDateTime(new Date(info.getValue() as string)),
        size: 140,
        meta: {
          headerTitle: 'Timestamp',
          skeleton: <Skeleton className="w-20 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        id: 'actions',
        header: 'Actions',
        cell: ({ row }) => <LogActionsCell row={row} />,
        size: 70,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
        meta: {
          skeleton: <Skeleton className="size-5" />,
        },
      },
    ],
    [],
  );

  const [columnOrder, setColumnOrder] = useState<string[]>(
    columns.map((column) => column.id as string),
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row: SystemLog) => row.id,
    getRowCanExpand: (row) => Boolean(row.original.description),
    state: {
      pagination,
      sorting,
      columnOrder,
      rowSelection,
    },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onColumnOrderChange: setColumnOrder,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      tableLayout={{
        columnsPinnable: true,
        columnsMovable: true,
        columnsVisibility: true,
        columnsResizable: true,
      }}
      tableClassNames={{
        edgeCell: 'px-5',
      }}
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
                  onSubmit={() => resetSearch(searchInput)}
                  placeholder="Search logs"
                  className="w-full md:w-56"
                />
            }
            filters={{
              kind: 'custom',
              active: Boolean(dateRangeFilter?.from || dateRangeFilter?.to),
              activeCount: dateRangeFilter?.from || dateRangeFilter?.to ? 1 : 0,
              content: (
                <div className="space-y-2">
                  <p className="text-xs font-medium">Date range</p>
                  <Calendar
                    mode="range"
                    defaultMonth={dateRangeFilter?.from}
                    selected={dateRangeFilter}
                    onSelect={(range) => handleDateRangeApply(range)}
                    numberOfMonths={1}
                  />
                  {(dateRangeFilter?.from || dateRangeFilter?.to) && (
                    <div className="flex justify-end border-t pt-2">
                      <Button variant="ghost" size="sm" onClick={handleDateRangeReset}>
                        Reset
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'system_logs_export.xlsx' }}
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
  );
};

export default ActivityLogList;
