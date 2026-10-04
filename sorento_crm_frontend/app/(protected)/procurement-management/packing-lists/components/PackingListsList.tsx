'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
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
import { Download, Eye, Plus, RefreshCw, Trash2, Upload } from 'lucide-react';
import { toast } from '@/lib/toast';
import { getLatestContainerStatusDocument } from '../services/packingListService';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { PACKING_LIST_REGION_OPTIONS, regionLabel } from '@/lib/packing-list-regions';
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn, selectedRowIds } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { usePackingLists } from '../hooks/usePackingLists';
import type { PackingList } from '../types/packingList.types';
import { formatDate, formatDateTimeInMalaysia } from '@/lib/helpers';
import { formatStatusLabel } from '@/lib/status-badge';
import PackingListDeleteDialog from './packing-list-delete-dialog';
import PackingListBulkDeleteDialog from './PackingListBulkDeleteDialog';
import ContainerStatusImportDialog from './ContainerStatusImportDialog';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import AttachmentUploadDialog from '@/app/(protected)/resource-management/attachments/components/AttachmentUploadDialog';
import { useAttachmentTypesList } from '@/app/(protected)/resource-management/attachments/hooks/useAttachments';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { useHasAnyPermission } from '@/hooks/usePermissions';

export default function PackingListsList() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const qc = useQueryClient();
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [attachmentUploadOpen, setAttachmentUploadOpen] = useState(false);
  const [regionFilter, setRegionFilter] = useState('');

  // The Drive-style Upload button fires the create-attachment write route directly
  // (`POST /api/v1/resource-management/attachments`), gated on its own permission -
  // the write route's permission rather than the page's own.
  const canUploadAttachment = useHasAnyPermission(['resource.attachments.upload']);
  const { data: attachmentTypes = [] } = useAttachmentTypesList();
  // `code` is the stable machine key (falls back to the editable `type_name` label for
  // a tenant whose seed predates the column) - either miss leaves the dialog unpreset
  // and unlocked rather than blocking the button.
  const packingListAttachmentType =
    attachmentTypes.find((type) => type.code === 'packing_list') ||
    attachmentTypes.find((type) => type.type_name === 'Packing List');

  // The "no container status imported yet" empty state on a packing list detail page
  // links back here with ?import=container-status so the CTA lands on the upload
  // itself rather than dead-ending on the list.
  useEffect(() => {
    if (searchParams.get('import') === 'container-status') setImportDialogOpen(true);
  }, [searchParams]);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'created_at', desc: true },
  ]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setRegionFilter(state.filters.region ?? '');
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [searchQuery, regionFilter]);
  /**
   * Clearance columns are OFF by default. There are 17 of them; showing them all
   * would bury the eight columns everyone already uses. Each user turns on the ones
   * they care about via the toolbar's column picker and the choice persists through
   * the existing `listing_key` column-config personalization.
   */
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    liner_code: false,
    china_forwarder: false,
    malaysia_forwarder: false,
    consignee: false,
    loc: false,
    free_days_available: false,
    loading_date: false,
    etc_date: false,
    etd_date: false,
    eta_delay_date: false,
    inspection_date: false,
    approval_date: false,
    gatepass_date: false,
    warehouse_arrival_date: false,
    informed_collection_date: false,
    collection_date: false,
  });
  const [packingListToDelete, setPackingListToDelete] =
    useState<PackingList | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewItem, setPreviewItem] = useState<AttachmentPreviewItem | null>(null);

  const { data, isLoading, isPlaceholderData, refetch, isFetching } = usePackingLists({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    region: regionFilter || undefined,
  });

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from.
  const rowHref = (row: PackingList) => {
    const search = buildDetailSearch({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
    }, { region: regionFilter || undefined });
    const qs = search ? `?${search}` : '';
    return `/procurement-management/packing-lists/${row.id}${qs}`;
  };

  /** Open the latest imported workbook, either inline or as a download.
   *
   * The link is signed and short-lived, so it is fetched on click rather than
   * rendered as an href - a stale URL in the DOM would 403 by the time anyone
   * used it. A toast carries the "nothing imported yet" case, which is a normal
   * state before the first import, not an error worth a dialog.
   *
   * Preview reuses the shared AttachmentPreviewModal, which renders xlsx as a
   * sheet in place. Answering "what does the sheet say" should not cost a
   * download, an Excel launch and a file left in ~/Downloads. */
  const openContainerStatus = async (mode: 'preview' | 'download') => {
    try {
      const doc = await getLatestContainerStatusDocument();
      if (mode === 'download') {
        window.open(doc.url, '_blank', 'noopener,noreferrer');
        return;
      }
      setPreviewItem({
        id: doc.attachment_id,
        name: doc.filename,
        // The signed URL is the cacheable source for the inline render; bytes for
        // the modal's own Download button come through the authenticated route.
        url: doc.url,
        downloadUrl: `/api/v1/resource-management/attachments/${doc.attachment_id}/download`,
        sizeBytes: doc.size,
      });
      setPreviewOpen(true);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : 'Could not fetch the Container Status workbook',
      );
    }
  };

  const columns = useMemo<ColumnDef<PackingList>[]>(() => {
    /** Clearance date column: same shape 11 times, so build it once. */
    const dateColumn = (
      key: keyof PackingList,
      title: string,
      size = 130,
    ): ColumnDef<PackingList> => ({
      accessorKey: key as string,
      header: ({ column }) => (
        <DataGridColumnHeader title={title} column={column} />
      ),
      cell: ({ row }) => {
        const value = row.original[key] as string | null | undefined;
        return value ? formatDate(new Date(value)) : '-';
      },
      size,
      meta: { headerTitle: title, skeleton: <Skeleton className="h-4 w-20" /> },
    });

    /** Clearance text column - truncated with a title, per ARCHITECTURE-RULES. */
    const textColumn = (
      key: keyof PackingList,
      title: string,
      size = 140,
    ): ColumnDef<PackingList> => ({
      accessorKey: key as string,
      header: ({ column }) => (
        <DataGridColumnHeader title={title} column={column} />
      ),
      cell: ({ row }) => {
        const value = row.original[key] as string | number | null | undefined;
        if (value === null || value === undefined || value === '') return '-';
        return (
          <span className="block truncate" title={String(value)}>
            {String(value)}
          </span>
        );
      },
      size,
      meta: { headerTitle: title, skeleton: <Skeleton className="h-4 w-24" /> },
    });

    return [
      buildSelectColumn<PackingList>(),
      {
        accessorKey: 'shipment_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Shipment Number" column={column} />
        ),
        cell: ({ row }) => row.original.shipment_number || '-',
        size: 150,
        meta: { headerTitle: 'Shipment Number', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'shipping_container_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Container Number" column={column} />
        ),
        cell: ({ row }) =>
          row.original.shipping_container_number || '-',
        size: 160,
        meta: { headerTitle: 'Container Number', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'supplier.supplier_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Supplier" column={column} />
        ),
        cell: ({ row }) => row.original.supplier?.supplier_name || '-',
        size: 200,
        meta: { headerTitle: 'Supplier', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'shipment_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Shipment Date" column={column} />
        ),
        cell: ({ row }) =>
          row.original.shipment_date
            ? formatDate(new Date(row.original.shipment_date))
            : '-',
        size: 120,
        meta: { headerTitle: 'Shipment Date', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'estimated_arrival_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Expected Arrival" column={column} />
        ),
        cell: ({ row }) =>
          row.original.estimated_arrival_date
            ? formatDate(new Date(row.original.estimated_arrival_date))
            : '-',
        size: 150,
        meta: { headerTitle: 'Expected Arrival', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'shipment_status',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        cell: ({ row }) => {
          const status = row.original.shipment_status;
          return (
            <Badge status={status}>
              {formatStatusLabel(status)}
            </Badge>
          );
        },
        size: 130,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'regions',
        header: ({ column }) => (
          <DataGridColumnHeader title="Regions" column={column} />
        ),
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-1">
            {(row.original.regions ?? []).map((code) => (
              <Badge key={code} variant="outline" size="sm">
                {regionLabel(code)}
              </Badge>
            ))}
          </div>
        ),
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Regions', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'total_items_shipped',
        header: ({ column }) => (
          <DataGridColumnHeader title="Items" column={column} />
        ),
        cell: ({ row }) =>
          row.original.display_total_items ??
          row.original.total_items_shipped ??
          0,
        size: 100,
        meta: { headerTitle: 'Items', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Created At" column={column} />
        ),
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.created_at),
        size: 120,
        meta: { headerTitle: 'Created At', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      // --- Container status / clearance chain. All hidden by default. ---
      textColumn('liner_code', 'Liner', 110),
      textColumn('china_forwarder', 'China Forwarder'),
      textColumn('malaysia_forwarder', 'MY Forwarder'),
      textColumn('consignee', 'Consignee', 170),
      textColumn('loc', 'Loc', 90),
      textColumn('free_days_available', 'Free Days', 110),
      dateColumn('loading_date', 'Loading'),
      dateColumn('etc_date', 'ETC', 110),
      dateColumn('etd_date', 'ETD', 110),
      dateColumn('eta_delay_date', 'ETA Delay'),
      dateColumn('inspection_date', 'Inspection'),
      dateColumn('approval_date', 'Approval'),
      dateColumn('gatepass_date', 'Gatepass'),
      dateColumn('warehouse_arrival_date', 'W/H Arrival'),
      dateColumn('informed_collection_date', 'Informed Collection', 170),
      dateColumn('collection_date', 'Collection'),
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => (
          <RowActionsMenu
            ariaLabel="packing list"
            actions={[
              {
                key: 'packing_list.delete',
                label: 'Delete packing list',
                icon: Trash2,
                kind: 'destructive',
                run: () => setPackingListToDelete(row.original),
              },
            ]}
          />
        ),
        size: 60,
        enableHiding: false,
      },
    ];
  }, []);

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
    columnResizeMode: 'onChange',
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  //
  // Upload is primary (R3, REVERSED 7 Sep 2026): the Drive-style Create Attachment
  // dialog, preset and locked to the Packing List type, is now how a packing list is
  // filed - same n8n intake as any Files upload, filed under that type's default
  // folder. The former reader dialog ("Upload supplier documents") has no home here
  // any more (S3, AC-C2): this page has no supplier-document upload of its own - a
  // packing list is born by convert (Proforma Invoices) or by hand (Create Packing
  // List). Create Packing List is the fallback primary when the write route for
  // Upload is out of reach.
  const listPrimaryAction = canUploadAttachment ? (
    <Button onClick={() => setAttachmentUploadOpen(true)}>
      <Upload />
      Upload
    </Button>
  ) : (
    <Button onClick={() => router.push('/procurement-management/packing-lists/new')}>
      <Plus />
      Create Packing List
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      standardToolbar={false}
      tableLayout={{ width: 'fixed', columnsVisibility: true, columnsResizable: true }}
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
                placeholder="Search by shipment or container number..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: !!regionFilter,
              activeCount: regionFilter ? 1 : 0,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="packing-list-region-filter">Region</Label>
                    <SearchableSelect
                      id="packing-list-region-filter"
                      value={regionFilter}
                      onChange={setRegionFilter}
                      options={PACKING_LIST_REGION_OPTIONS}
                      placeholder="All regions"
                      triggerClassName="mt-1"
                      clearable
                    />
                  </div>
                </div>
              ),
            }}
            exportConfig={{ filename: 'packing_lists_export.xlsx' }}
            primaryAction={listPrimaryAction}
            secondaryActions={[
              // Two or more secondary actions collapse into the toolbar's "Actions"
              // dropdown, which is where the delivery order import lives. Refresh
              // moves in with the import rather than sitting as its own icon button,
              // so this toolbar matches that page.
              {
                key: 'refresh',
                label: 'Refresh',
                icon: RefreshCw,
                onClick: () => void refetch(),
              },
              // Only offered here when Upload is the primary action - when the write
              // route for Upload is out of reach, Create Packing List already IS the
              // primary button above and would otherwise appear twice.
              ...(canUploadAttachment
                ? [
                    {
                      key: 'create-packing-list',
                      label: 'Create Packing List',
                      icon: Plus,
                      onClick: () => router.push('/procurement-management/packing-lists/new'),
                    },
                  ]
                : []),
              {
                key: 'preview-container-status',
                label: 'Preview Container Status (latest)',
                icon: Eye,
                onClick: () => void openContainerStatus('preview'),
              },
              {
                key: 'download-container-status',
                label: 'Download Container Status (latest)',
                icon: Download,
                onClick: () => void openContainerStatus('download'),
              },
              {
                key: 'import-container-status',
                label: 'Import Container Status',
                icon: Upload,
                onClick: () => setImportDialogOpen(true),
              },
            ]}
            bulkActions={[
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
      {packingListToDelete && (
        <PackingListDeleteDialog
          open
          closeDialog={() => setPackingListToDelete(null)}
          packingList={packingListToDelete}
          onSuccess={() => refetch()}
        />
      )}
      <ContainerStatusImportDialog
        open={importDialogOpen}
        onOpenChange={setImportDialogOpen}
      />
      <AttachmentUploadDialog
        open={attachmentUploadOpen}
        onOpenChange={setAttachmentUploadOpen}
        defaultTypeId={packingListAttachmentType?.id}
        lockType={!!packingListAttachmentType}
        defaultDirectoryId={packingListAttachmentType?.default_directory_id ?? null}
        onSuccess={() => {
          void qc.invalidateQueries({ queryKey: ['packing-lists'] });
        }}
      />
      <AttachmentPreviewModal
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        items={previewItem ? [previewItem] : []}
      />
      <PackingListBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={(open) => {
          setBulkDeleteDialogOpen(open);
          if (!open) setRowSelection({});
        }}
        packingListIds={selectedRowIds(table)}
        onSuccess={() => {
          setRowSelection({});
          refetch();
        }}
      />
    </DataGrid>
  );
}
