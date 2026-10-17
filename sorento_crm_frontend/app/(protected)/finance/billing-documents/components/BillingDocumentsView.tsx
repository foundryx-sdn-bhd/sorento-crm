'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Container } from '@/components/common/container';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { PageHeader } from '@/components/common/PageHeader';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { useBillingAgentOptions, useBillingDocumentsQuery } from '../hooks/useBillingDocuments';
import { formatBillingAmount, formatDay } from '../lib/format';
import { DEFAULT_BILLING_SORT, billingFilterQuery } from '../lib/listQuery';
import { useCanExportBilling, useCanViewOutstanding } from '../lib/permissions';
import { searchBillingCustomerOptions } from '../services/billingDocumentService';
import {
  BILLING_STATUS_LABEL,
  BILLING_TYPE_LABEL,
  type BillingDocumentListRow,
  type BillingDocumentStatus,
  type BillingDocumentType,
} from '../types/billingDocument.types';
import { BillingStatusBadge, BillingTypeBadge } from './BillingBadges';
import { OutstandingCell } from './OutstandingCell';

/**
 * Finance > Billing Documents (UAC S2-1..S2-3) and, with `customerId`, the customer record's
 * Billing documents tab (S3-2): the same list pre-filtered, without the page header and the
 * Customer and Agent filters. Read only: no Add, no row actions. The row is the way in.
 * Outstanding and Due date follow OUTSTANDING_VIEW_SLUG.
 */
const TYPE_OPTIONS = (Object.keys(BILLING_TYPE_LABEL) as BillingDocumentType[]).map((value) => ({
  value,
  label: BILLING_TYPE_LABEL[value],
}));
const STATUS_OPTIONS: SearchableSelectOption[] = (
  Object.keys(BILLING_STATUS_LABEL) as BillingDocumentStatus[]
).map((value) => ({ value, label: BILLING_STATUS_LABEL[value] }));

export default function BillingDocumentsView({ customerId }: { customerId?: string }) {
  const embedded = !!customerId;
  const canExport = useCanExportBilling();
  const canSeeOutstanding = useCanViewOutstanding();
  const {
    value: searchQuery,
    setValue: setSearchQuery,
    debouncedValue: debouncedSearch,
    isSettling,
  } = useDebouncedSearch();

  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([DEFAULT_BILLING_SORT]);
  const [documentTypes, setDocumentTypes] = useState<string[]>([]);
  const [status, setStatus] = useState('');
  const [pickedCustomerId, setPickedCustomerId] = useState('');
  const [customerOption, setCustomerOption] = useState<SearchableSelectOption | undefined>();
  const [agentCode, setAgentCode] = useState('');
  const [dateFrom, setDateFrom] = useState<string | null>(null);
  const [dateTo, setDateTo] = useState<string | null>(null);
  const [openOnly, setOpenOnly] = useState(false);

  const effectiveCustomerId = customerId || pickedCustomerId;

  useResetPageOnFilterChange(setPagination, [
    debouncedSearch,
    documentTypes.join(','),
    status,
    effectiveCustomerId,
    agentCode,
    dateFrom ?? '',
    dateTo ?? '',
    openOnly,
  ]);

  const { data: agentOptions, isError: agentsFailed, refetch: refetchAgents } = useBillingAgentOptions();

  // Normalised so the record page's pager rebuilds the identical object from the URL (one cache key).
  const filters = {
    documentTypes: documentTypes.length ? (documentTypes as BillingDocumentType[]) : undefined,
    status: (status as BillingDocumentStatus) || undefined,
    customerId: effectiveCustomerId || undefined,
    agentCode: agentCode || undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
    openOnly: openOnly ? true : undefined,
  };
  const { data, isLoading, isFetching, isPlaceholderData, isError, refetch } = useBillingDocumentsQuery({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery: debouncedSearch,
    ...filters,
  });
  const rows = useMemo<BillingDocumentListRow[]>(() => data?.data ?? [], [data]);
  const total = data?.pagination.total ?? 0;

  const columns = useMemo<ColumnDef<BillingDocumentListRow>[]>(() => {
    const cols: ColumnDef<BillingDocumentListRow>[] = [
      {
        accessorKey: 'document_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        size: 130,
        cell: ({ row }) => <BillingTypeBadge type={row.original.document_type} />,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-5 w-16" /> },
      },
      {
        accessorKey: 'doc_no',
        header: ({ column }) => <DataGridColumnHeader title="Number" column={column} />,
        size: 130,
        cell: ({ row }) => (
          <Link
            href={`/finance/billing-documents/${row.original.id}`}
            onClick={(e) => e.stopPropagation()}
            className="block truncate font-medium text-primary hover:underline"
            title={row.original.doc_no}
          >
            {row.original.doc_no}
          </Link>
        ),
        meta: { headerTitle: 'Number', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'doc_date',
        header: ({ column }) => <DataGridColumnHeader title="Date" column={column} />,
        size: 110,
        cell: ({ row }) => formatDay(row.original.doc_date),
        meta: { headerTitle: 'Date', skeleton: <Skeleton className="h-4 w-16" /> },
      },
    ];
    if (!embedded) {
      cols.push(
        {
          accessorKey: 'customer_name',
          header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
          size: 220,
          cell: ({ row }) => (
            <span className="block truncate" title={row.original.customer_name ?? ''}>
              {row.original.customer_name}
            </span>
          ),
          meta: { headerTitle: 'Customer', skeleton: <Skeleton className="h-4 w-28" /> },
        },
        {
          accessorKey: 'agent_code',
          header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
          size: 150,
          cell: ({ row }) => {
            const label = row.original.agent_name ?? row.original.agent_code ?? '';
            return (
              <span className="block truncate" title={label}>
                {label}
              </span>
            );
          },
          meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-20" /> },
        },
      );
    }
    cols.push({
      accessorKey: 'total',
      header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
      size: 140,
      cell: ({ row }) => formatBillingAmount(row.original.total, row.original.currency_code),
      meta: {
        headerTitle: 'Total',
        headerClassName: 'text-right',
        cellClassName: 'text-right tabular-nums',
        skeleton: <Skeleton className="h-4 w-16" />,
      },
    });
    if (canSeeOutstanding) {
      cols.push(
        {
          accessorKey: 'outstanding',
          header: ({ column }) => <DataGridColumnHeader title="Outstanding" column={column} />,
          size: 140,
          cell: ({ row }) => (
            <OutstandingCell
              outstanding={row.original.outstanding}
              currencyCode={row.original.currency_code}
            />
          ),
          meta: {
            headerTitle: 'Outstanding',
            headerClassName: 'text-right',
            cellClassName: 'text-right',
            skeleton: <Skeleton className="h-4 w-16" />,
          },
        },
        {
          accessorKey: 'due_date',
          header: ({ column }) => <DataGridColumnHeader title="Due date" column={column} />,
          size: 110,
          cell: ({ row }) => (row.original.due_date ? formatDay(row.original.due_date) : null),
          meta: { headerTitle: 'Due date', skeleton: <Skeleton className="h-4 w-16" /> },
        },
      );
    }
    cols.push({
      accessorKey: 'status',
      header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
      size: 120,
      cell: ({ row }) => <BillingStatusBadge status={row.original.status} />,
      meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-5 w-16" /> },
    });
    return cols;
  }, [embedded, canSeeOutstanding]);

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(total / pagination.pageSize) || 1,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const hasFilters = !!(
    documentTypes.length ||
    status ||
    (!embedded && pickedCustomerId) ||
    agentCode ||
    dateFrom ||
    dateTo ||
    openOnly
  );
  const clearFilters = () => {
    setDocumentTypes([]);
    setStatus('');
    setPickedCustomerId('');
    setCustomerOption(undefined);
    setAgentCode('');
    setDateFrom(null);
    setDateTo(null);
    setOpenOnly(false);
  };

  const filterRowQuery = billingFilterQuery(filters);
  const exportPayload = () => ({
    quick_search: debouncedSearch || undefined,
    document_type: filters.documentTypes?.join(','),
    status: filters.status,
    customer_id: filters.customerId,
    agent_code: filters.agentCode,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    open_only: filters.openOnly,
  });

  const emptyMessage =
    debouncedSearch || hasFilters ? (
      'No billing documents match these filters.'
    ) : (
      <div className="flex w-full flex-col items-center gap-1 py-6">
        <span className="text-sm font-medium">No billing documents yet</span>
        <span className="text-sm text-muted-foreground">Nothing has been synced from AutoCount.</span>
      </div>
    );

  const grid = (
    <div className="space-y-3">
      {isError ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          Failed to load billing documents.{' '}
          <button type="button" className="underline" onClick={() => refetch()}>
            Retry
          </button>
        </div>
      ) : null}
      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        listingKey={embedded ? 'finance.billing_documents.view::customer' : 'finance.billing_documents.view'}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        emptyMessage={emptyMessage}
        rowHref={(row) =>
          `/finance/billing-documents/${row.id}${filterRowQuery ? `?${filterRowQuery}` : ''}`
        }
      >
        <Card>
          <CardHeader className="block py-0">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={searchQuery}
                  onChange={setSearchQuery}
                  isSettling={isSearchInFlight(isSettling, isFetching, debouncedSearch)}
                  placeholder="Search number or customer..."
                  className="w-full sm:w-64"
                />
              }
              exportConfig={
                canExport
                  ? {
                      kind: 'listQuery',
                      resourceKey: 'finance_billing_documents',
                      filename: 'billing_documents.xlsx',
                      getPayload: exportPayload,
                    }
                  : false
              }
              onRefresh={() => void refetch()}
              isRefreshing={isFetching && !isLoading}
            />
            <div className="flex flex-wrap items-center gap-3 pb-4">
              <SearchableMultiSelect
                id="billing-filter-type"
                value={documentTypes}
                onChange={setDocumentTypes}
                options={TYPE_OPTIONS}
                placeholder="Type"
                triggerClassName="w-full sm:w-48"
              />
              <SearchableSelect
                id="billing-filter-status"
                aria-label="Status"
                value={status}
                onChange={setStatus}
                options={STATUS_OPTIONS}
                placeholder="Status"
                clearable
                triggerClassName="w-full sm:w-36"
              />
              {!embedded ? (
                <>
                  <SearchableSelect
                    id="billing-filter-customer"
                    aria-label="Customer"
                    value={pickedCustomerId}
                    onChange={setPickedCustomerId}
                    onOptionChange={(opt) => setCustomerOption(opt ?? undefined)}
                    fetchOptions={searchBillingCustomerOptions}
                    selectedOption={customerOption}
                    placeholder="Customer"
                    clearable
                    triggerClassName="w-full sm:w-52"
                  />
                  <SearchableSelect
                    id="billing-filter-agent"
                    aria-label="Agent"
                    value={agentCode}
                    onChange={setAgentCode}
                    options={agentOptions ?? []}
                    loadError={agentsFailed ? new Error('agents') : undefined}
                    onRetry={() => void refetchAgents()}
                    placeholder="Agent"
                    clearable
                    triggerClassName="w-full sm:w-44"
                  />
                </>
              ) : null}
              <DateRangePicker
                id="billing-filter-date"
                aria-label="Document date"
                from={dateFrom}
                to={dateTo}
                onChange={({ from, to }) => {
                  setDateFrom(from);
                  setDateTo(to);
                }}
                placeholder="Date range"
                className="w-full sm:w-56"
              />
              {canSeeOutstanding ? (
                <div className="flex items-center gap-2">
                  <Switch id="billing-filter-open" checked={openOnly} onCheckedChange={setOpenOnly} />
                  <Label htmlFor="billing-filter-open">Open only</Label>
                </div>
              ) : null}
              {hasFilters ? (
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  Clear filters
                </Button>
              ) : null}
            </div>
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>
    </div>
  );

  if (embedded) return grid;
  return (
    <>
      <Container>
        <PageHeader title="Billing Documents" />
      </Container>
      <Container>{grid}</Container>
    </>
  );
}
