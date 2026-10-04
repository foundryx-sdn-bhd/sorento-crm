'use client';

import { useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Eye, FileText, Filter, Plus, RefreshCw, Trash2, Users } from 'lucide-react';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn, selectedRowIds } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { useTenantModules } from '@/hooks/useTenantModules';
import AttachmentDetailModal from '@/app/(protected)/resource-management/attachments/components/AttachmentDetailModal';
import { buildDetailSearch, encodeAdvancedFilter } from '@/lib/listNavQuery';
import { PromotionRowActions } from '../actions';
import { useCompilePromotionsPdf, usePromotions } from '../hooks/usePromotions';
import type { Promotion } from '../types/promotion.types';
import { formatPromotionBoundaryInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import type { ListQueryFilterGroup } from '@/lib/list-query/listQueryService';
import PromotionBulkDeleteDialog from './PromotionBulkDeleteDialog';
import PromotionBulkAccessLevelsDialog from './PromotionBulkAccessLevelsDialog';
import PromotionBulkResubmitDialog from './PromotionBulkResubmitDialog';
import { useHasPermission } from '@/hooks/usePermissions';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function PromotionsList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // Deep link from an expiry-reminder email - restrict the list to exactly the
  // promotions stamped in that batch.
  const expiryNotifyBatchId = searchParams.get('expiry_notify_batch_id') || undefined;
  const compilePdf = useCompilePromotionsPdf();
  const { enabledModuleKeys, isLoading: modulesLoading } = useTenantModules();
  const listQueryToolsEnabled =
    modulesLoading || enabledModuleKeys == null || enabledModuleKeys.has('marketing');

  const {
    data: accessTypeOptions = [],
    error: accessTypesError,
    refetch: refetchAccessTypes,
  } = useContactAccessTypes();
  const accessLevelNameMap = useMemo(() => {
    const m = new Map<string, string>();
    accessTypeOptions.forEach((o) => m.set(o.code, o.name || o.code));
    return m;
  }, [accessTypeOptions]);
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [bulkAccessLevelsDialogOpen, setBulkAccessLevelsDialogOpen] = useState(false);
  const [bulkResubmitDialogOpen, setBulkResubmitDialogOpen] = useState(false);
  // Re-extraction rewrites a promotion's groups and products, so it is gated on the
  // same permission as editing one by hand.
  const canResubmit = useHasPermission('marketing.promotions.edit');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [filterAccessLevel, setFilterAccessLevel] = useState<string>('all');
  const [filterAttachmentState, setFilterAttachmentState] = useState<'all' | 'unlinked' | 'linked_to_trashed' | 'unlinked_or_trashed'>('all');

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setFilterStatus(state.filters.status ?? 'all');
    setFilterAccessLevel(state.filters.user_type ?? 'all');
    setFilterAttachmentState(
      (state.filters.attachment_state as typeof filterAttachmentState) ?? 'all',
    );
  });
  const [advancedFilter, setAdvancedFilter] = useState<ListQueryFilterGroup | null>(null);
  const [viewerAttachmentId, setViewerAttachmentId] = useState<string | null>(null);

  const hasActiveQuickFilters = filterStatus !== 'all' || filterAccessLevel !== 'all' || filterAttachmentState !== 'all';

  /**
   * One query, one key, shared with the record page's pager (S3-03).
   *
   * This list used to hand-roll its own `useQuery` with a key of its own shape,
   * so the pager's rebuilt key never matched: every promotion opened fired a
   * second request and paged whatever THAT returned.
   */
  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = usePromotions({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: filterStatus,
    user_type: filterAccessLevel === 'all' ? undefined : filterAccessLevel,
    attachment_state: filterAttachmentState === 'all' ? undefined : filterAttachmentState,
    expiry_notify_batch_id: expiryNotifyBatchId,
    advancedFilter: advancedFilter ?? undefined,
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [
    advancedFilter,
    filterStatus,
    filterAccessLevel,
    filterAttachmentState,
    expiryNotifyBatchId,
    searchQuery,
  ]);

  const columns = useMemo<ColumnDef<Promotion>[]>(
    () => [
      buildSelectColumn<Promotion>(),
      {
        accessorKey: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        cell: ({ row }) => {
          const desc = row.original.description;
          if (!desc) return <span className="text-muted-foreground">-</span>;
          return (
            <div className="min-w-0 max-w-full truncate" title={desc}>
              {desc}
            </div>
          );
        },
        size: 260,
        minSize: 160,
        enableSorting: false,
        meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'attachments',
        header: ({ column }) => <DataGridColumnHeader title="Attachments" column={column} />,
        cell: ({ row }) => {
          const items = (row.original.attachments ?? [])
            .map((pa) => pa.attachment)
            .filter((a): a is NonNullable<typeof a> => !!a && !!a.original_filename);
          if (!items.length) return <span className="text-muted-foreground">-</span>;
          const handleOpenDetail = (
            e: React.MouseEvent<HTMLButtonElement>,
            attachmentId: string,
          ) => {
            e.stopPropagation();
            setViewerAttachmentId(attachmentId);
          };
          return (
            <div
              className="min-w-0 max-w-full truncate"
              title={items.map((a) => a.original_filename).join('\n')}
            >
              {items.map((a, idx) => (
                <span key={a.id} className="inline-flex items-center gap-1 align-middle">
                  <span>{a.original_filename}</span>
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-primary focus:outline-none"
                    onClick={(e) => handleOpenDetail(e, a.id)}
                    title="View attachment details"
                    aria-label={`View details of ${a.original_filename}`}
                  >
                    <Eye className="size-3.5" />
                  </button>
                  {idx < items.length - 1 ? <span>, </span> : null}
                </span>
              ))}
            </div>
          );
        },
        size: 260,
        minSize: 180,
        enableSorting: false,
        meta: { headerTitle: 'Attachments', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'promotion_type_name',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => {
          // A promotion with no type is served under the default type's rule, so
          // the empty state says which rule it is rather than showing a dash.
          const name = row.original.promotion_type_name;
          if (!name) {
            return (
              <span className="text-muted-foreground truncate" title="Unclassified - treated as the default type">
                Unclassified
              </span>
            );
          }
          return (
            <div className="min-w-0 max-w-full truncate" title={name}>
              {name}
            </div>
          );
        },
        size: 140,
        minSize: 100,
        enableSorting: false,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'access_levels',
        header: ({ column }) => <DataGridColumnHeader title="Access" column={column} />,
        cell: ({ row }) => {
          const levels = row.original.access_levels || [];
          if (!levels.length) return '-';
          return (
            <div className="flex flex-wrap gap-2">
              {levels.map((level) => (
                <Badge key={level} variant="secondary">
                  {accessLevelNameMap.get(level) ?? level}
                </Badge>
              ))}
            </div>
          );
        },
        size: 160,
        minSize: 120,
        meta: { headerTitle: 'Access' },
      },
      {
        accessorKey: 'start_date',
        header: ({ column }) => <DataGridColumnHeader title="Start Date" column={column} />,
        cell: ({ row }) => row.original.start_date ? formatPromotionBoundaryInMalaysia(row.original.start_date) : '-',
        size: 120,
        meta: { headerTitle: 'Start Date' },
      },
      {
        accessorKey: 'end_date',
        header: ({ column }) => <DataGridColumnHeader title="End Date" column={column} />,
        cell: ({ row }) => row.original.end_date ? formatPromotionBoundaryInMalaysia(row.original.end_date) : '-',
        size: 120,
        meta: { headerTitle: 'End Date' },
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.is_active ? 'success' : 'secondary'}>
            <BadgeDot />
            {row.original.is_active ? 'Active' : 'Inactive'}
          </Badge>
        ),
        size: 100,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'products_count',
        header: ({ column }) => <DataGridColumnHeader title="Products" column={column} />,
        cell: ({ row }) => (
          <span className="tabular-nums">{row.original.products_count ?? 0}</span>
        ),
        size: 100,
        meta: { headerTitle: 'Products' },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created At" column={column} />,
        cell: ({ row }) => {
          const created = row.original.created_at;
          if (!created) return <span className="text-muted-foreground">-</span>;
          return (
            <span className="whitespace-nowrap tabular-nums">
              {formatDateTimeInMalaysia(created as unknown as string)}
            </span>
          );
        },
        size: 160,
        minSize: 140,
        meta: { headerTitle: 'Created At' },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => <PromotionRowActions promotionId={row.original.id} />,
        size: 40,
        enableHiding: false,
      },
    ],
    [accessLevelNameMap],
  );

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from.
  const rowHref = (row: Promotion) => {
    const search = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        status: filterStatus !== 'all' ? filterStatus : undefined,
        user_type: filterAccessLevel !== 'all' ? filterAccessLevel : undefined,
        attachment_state:
          filterAttachmentState !== 'all' ? filterAttachmentState : undefined,
        // Both narrow the set, so both have to ride along or the pager walks a
        // wider one than the reader is looking at.
        expiry_notify_batch_id: expiryNotifyBatchId,
        advFilter: encodeAdvancedFilter(advancedFilter),
      },
    );
    return `/marketing-management/promotions/${row.id}${search ? `?${search}` : ''}`;
  };

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  // The rows themselves, not just their ids: the resubmit dialog resolves each
  // promotion's flyer attachment from the row it already has.
  const selectedPromotions = table
    .getRowModel()
    .rows.filter((r) => r.getIsSelected())
    .map((r) => r.original);

  const handleCompilePdf = () => {
    // Selected rows in the order they appear in the grid - the merged PDF
    // preserves this ordering.
    const ids = table
      .getRowModel()
      .rows.filter((r) => r.getIsSelected())
      .map((r) => r.original.id);
    if (ids.length === 0) return;
    compilePdf.mutate(ids, { onSuccess: () => setRowSelection({}) });
  };

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/marketing-management/promotions/new')}>
      <Plus />
      Create Promotion
    </Button>
  );

  return (
    <DataGrid
      table={table}
      tableLayout={{ columnsVisibility: true }}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      error={error}
      onRetry={() => void refetch()}
      rowHref={rowHref}
      standardToolbar={false}
    >
      <Card>
        <CardHeader className="block">
          {expiryNotifyBatchId && (
            <div className="mb-3 flex items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
              <span>Showing promotions from a recent expiry-reminder batch.</span>
              <Button variant="ghost" size="sm" onClick={() => router.replace(pathname)}>
                Clear
              </Button>
            </div>
          )}
          <DataGridListToolbar
            table={table}
            searchSlot={
              <div className="flex items-center gap-2">
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                  placeholder="Search promotions..."
                  className="w-64"
                />
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      className={`gap-1.5 ${hasActiveQuickFilters ? 'border-primary' : ''}`}
                      title="Quick filters"
                    >
                      <Filter className="size-4" />
                      Quick filters
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-72" align="start">
                    <div className="space-y-4">
                      <h4 className="font-medium">Filters</h4>
                      <div className="space-y-2">
                        <Label>Status</Label>
                        <SearchableSelect
                          value={filterStatus}
                          onChange={setFilterStatus}
                          options={[
                            { value: 'all', label: 'All' },
                            { value: 'active', label: 'Active' },
                            { value: 'inactive', label: 'Inactive' },
                          ]}
                          placeholder="All"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label>Access level</Label>
                        <SearchableSelect
                          value={filterAccessLevel}
                          onChange={setFilterAccessLevel}
                          loadError={accessTypesError}
                          onRetry={() => void refetchAccessTypes()}
                          options={[
                            { value: 'all', label: 'All' },
                            ...accessTypeOptions.map((opt) => ({
                              value: opt.code,
                              label: opt.name || opt.code,
                            })),
                          ]}
                          placeholder="All"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label>Attachment state</Label>
                        <SearchableSelect
                          value={filterAttachmentState}
                          onChange={(v) =>
                            setFilterAttachmentState(v as 'all' | 'unlinked' | 'linked_to_trashed' | 'unlinked_or_trashed')
                          }
                          options={[
                            { value: 'all', label: 'All' },
                            { value: 'unlinked', label: 'No attachments' },
                            { value: 'linked_to_trashed', label: 'Linked to trashed' },
                            { value: 'unlinked_or_trashed', label: 'No attachments or trashed' },
                          ]}
                          placeholder="All"
                        />
                      </div>
                      {hasActiveQuickFilters && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="w-full"
                          onClick={() => {
                            setFilterStatus('all');
                            setFilterAccessLevel('all');
                            setFilterAttachmentState('all');
                          }}
                        >
                          Clear quick filters
                        </Button>
                      )}
                    </div>
                  </PopoverContent>
                </Popover>
              </div>
            }
            filters={
              listQueryToolsEnabled
                ? {
                    kind: 'listQuery',
                    resourceKey: 'promotions',
                    advancedFilter,
                    onApply: setAdvancedFilter,
                    getPayload: () => ({
                      filter: advancedFilter ?? undefined,
                      quick_search: searchQuery || undefined,
                      promotion_status: filterStatus,
                      promotion_access_level:
                        filterAccessLevel === 'all' ? undefined : filterAccessLevel,
                    }),
                  }
                : undefined
            }
            exportConfig={
              listQueryToolsEnabled
                ? {
                    kind: 'listQuery',
                    resourceKey: 'promotions',
                    filename: 'promotions-export',
                    getPayload: () => ({
                      filter: advancedFilter ?? undefined,
                      quick_search: searchQuery || undefined,
                      promotion_status: filterStatus,
                      promotion_access_level:
                        filterAccessLevel === 'all' ? undefined : filterAccessLevel,
                    }),
                  }
                : false
            }
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
            bulkActions={[
              {
                key: 'compile-pdf',
                label: 'Compile PDF',
                icon: FileText,
                onClick: handleCompilePdf,
                disabled: compilePdf.isPending,
              },
              {
                key: 'access-levels',
                label: 'Set Access Levels',
                icon: Users,
                onClick: () => setBulkAccessLevelsDialogOpen(true),
              },
              ...(canResubmit
                ? [
                    {
                      key: 'resubmit',
                      label: 'Resubmit',
                      icon: RefreshCw,
                      onClick: () => setBulkResubmitDialogOpen(true),
                    },
                  ]
                : []),
              {
                key: 'delete',
                label: 'Delete',
                icon: Trash2,
                destructive: true,
                onClick: () => setBulkDeleteDialogOpen(true),
              },
            ]}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
      <PromotionBulkAccessLevelsDialog
        open={bulkAccessLevelsDialogOpen}
        onOpenChange={(open) => {
          setBulkAccessLevelsDialogOpen(open);
          if (!open) setRowSelection({});
        }}
        promotionIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
      <PromotionBulkResubmitDialog
        open={bulkResubmitDialogOpen}
        onOpenChange={(open) => {
          setBulkResubmitDialogOpen(open);
          if (!open) setRowSelection({});
        }}
        promotions={selectedPromotions}
        onSuccess={() => setRowSelection({})}
      />
      <PromotionBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={(open) => {
          setBulkDeleteDialogOpen(open);
          if (!open) setRowSelection({});
        }}
        promotionIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
      <AttachmentDetailModal
        open={viewerAttachmentId !== null}
        onOpenChange={(open) => {
          if (!open) setViewerAttachmentId(null);
        }}
        attachmentId={viewerAttachmentId}
      />
    </DataGrid>
  );
}
