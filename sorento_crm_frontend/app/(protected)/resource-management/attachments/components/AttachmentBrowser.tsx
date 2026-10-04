'use client';

import { useCallback, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  ColumnDef,
  PaginationState,
  Row,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Trash2, Plus, RefreshCw, RotateCcw, FileArchive, Tag, Building2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { useAttachmentActions } from '../actions';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar, type ToolbarAction } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable, DataGridTableRowSelect, DataGridTableRowSelectAll } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { useAttachments, useAttachmentTypesList, useBulkRestoreAttachments, useDirectoryTree } from '../hooks/useAttachments';
import { resubmitAttachmentWebhook } from '../services/attachmentService';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { attachmentCompanyLabel, type Attachment } from '../types/attachment.types';
import type { AttachmentDirectoryTreeNode } from '../services/directoryService';

function flattenDirectoryTree(nodes: AttachmentDirectoryTreeNode[], prefix = ''): { id: string; label: string }[] {
  return nodes.flatMap((n) => [
    { id: n.id, label: prefix ? `${prefix} / ${n.name}` : n.name },
    ...flattenDirectoryTree(n.children, prefix ? `${prefix} / ${n.name}` : n.name),
  ]);
}
import { formatDateTime } from '@/lib/helpers';
import AttachmentUploadDialog from './AttachmentUploadDialog';
import AttachmentBulkImportDialog from './AttachmentBulkImportDialog';
import AttachmentBulkDeleteDialog from './AttachmentBulkDeleteDialog';
import EditAttachmentTypeDialog from './EditAttachmentTypeDialog';
import SetCompanyDialog, { SHARED_COMPANY_VALUE } from './SetCompanyDialog';
import { useCompany } from '@/app/providers/CompanyProvider';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

/**
 * The row's "..." (D15): the same set the record's gear renders. Its own
 * component because the action set is a hook.
 */
function AttachmentRowActions({ attachment }: { attachment: Attachment }) {
  const { actions, dialogs } = useAttachmentActions(attachment);
  return (
    <>
      <RowActionsMenu ariaLabel="file" actions={actions} />
      {dialogs}
    </>
  );
}

export default function AttachmentBrowser() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'uploaded_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [bulkImportDialogOpen, setBulkImportDialogOpen] = useState(false);
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [bulkEditTypeOpen, setBulkEditTypeOpen] = useState(false);
  const [setCompanyDialogOpen, setSetCompanyDialogOpen] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [directoryId, setDirectoryId] = useState<string | null>(null);
  const [attachmentTypeId, setAttachmentTypeId] = useState<string>('__all__');
  const [linkStatus, setLinkStatus] = useState<'__all__' | 'linked' | 'unlinked'>('__all__');
  const [companyFilter, setCompanyFilter] = useState('');
  const [uploadedBy, setUploadedBy] = useState('');
  const [uploadedAtFrom, setUploadedAtFrom] = useState('');
  const [uploadedAtTo, setUploadedAtTo] = useState('');

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearchQuery(state.searchQuery);
    setDirectoryId(state.filters.directory_id ?? null);
    setAttachmentTypeId(state.filters.attachment_type_id ?? '__all__');
    setLinkStatus(
      (state.filters.link_status as 'linked' | 'unlinked') ?? '__all__',
    );
    setCompanyFilter(state.filters.company ?? '');
    setUploadedBy(state.filters.uploaded_by ?? '');
    setUploadedAtFrom(state.filters.uploaded_at_from ?? '');
    setUploadedAtTo(state.filters.uploaded_at_to ?? '');
  });

  const queryClient = useQueryClient();
  const { data: directoryTree = [] } = useDirectoryTree();
  const { data: attachmentTypes = [] } = useAttachmentTypesList();
  const { grants: companyGrants } = useCompany();
  const isTrashView = directoryId === '__trash__';

  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useAttachments({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    directory_id: isTrashView ? undefined : directoryId ?? undefined,
    is_deleted: isTrashView ? true : undefined,
    attachment_type_id: attachmentTypeId !== '__all__' ? attachmentTypeId : undefined,
    link_status: linkStatus !== '__all__' ? linkStatus : undefined,
    company: companyFilter || undefined,
    uploaded_by: uploadedBy.trim() || undefined,
    uploaded_at_from: uploadedAtFrom || undefined,
    uploaded_at_to: uploadedAtTo || undefined,
  });

  // Carry the active list query (search/sort + folder/linkage/type/uploader/date
  // filters) into the detail URL so the detail pager walks the SAME filtered set.
  // Mirrors the exact filter mapping the list fetch above sends.
  const detailSearch = useMemo(
    () =>
      buildDetailSearch(
        {
          pageIndex: pagination.pageIndex,
          pageSize: pagination.pageSize,
          sorting,
          searchQuery,
        },
        {
          directory_id: isTrashView ? undefined : directoryId ?? undefined,
          is_deleted: isTrashView ? 'true' : undefined,
          attachment_type_id:
            attachmentTypeId !== '__all__' ? attachmentTypeId : undefined,
          link_status: linkStatus !== '__all__' ? linkStatus : undefined,
          company: companyFilter || undefined,
          uploaded_by: uploadedBy.trim() || undefined,
          uploaded_at_from: uploadedAtFrom || undefined,
          uploaded_at_to: uploadedAtTo || undefined,
        },
      ),
    [
      pagination.pageIndex,
      pagination.pageSize,
      sorting,
      searchQuery,
      isTrashView,
      directoryId,
      attachmentTypeId,
      linkStatus,
      companyFilter,
      uploadedBy,
      uploadedAtFrom,
      uploadedAtTo,
    ],
  );

  const bulkRestoreMutation = useBulkRestoreAttachments();

  const [isResubmittingBulk, setIsResubmittingBulk] = useState(false);

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  };

  const selectedRowIds = useMemo(() => Object.keys(rowSelection), [rowSelection]);
  const selectedDeletableIds = useMemo(() => {
    const rows = data?.data ?? [];
    if (isTrashView) return selectedRowIds;
    return selectedRowIds.filter((id) => {
      const row = rows.find((r) => r.id === id);
      return row && !row.is_deleted;
    });
  }, [selectedRowIds, data?.data, isTrashView]);

  const handleBulkResubmit = useCallback(async () => {
    const ids = selectedDeletableIds;
    if (ids.length === 0) return;
    setIsResubmittingBulk(true);
    let successCount = 0;
    let failCount = 0;
    for (const id of ids) {
      try {
        await resubmitAttachmentWebhook(id);
        successCount += 1;
      } catch {
        failCount += 1;
      }
    }
    setIsResubmittingBulk(false);
    queryClient.invalidateQueries({ queryKey: ['attachments'] });
    if (failCount === 0) {
      toast.success('Resubmitted successfully');
      setRowSelection({});
    } else if (successCount === 0) {
      toast.error(`Failed to resubmit for ${failCount} attachment(s)`);
    } else {
      toast.warning(`Resubmitted ${successCount}, failed ${failCount}`);
    }
  }, [selectedDeletableIds, queryClient]);

  const columns = useMemo<ColumnDef<Attachment>[]>(
    () => [
      {
        id: 'select',
        header: () => <DataGridTableRowSelectAll />,
        cell: ({ row }) => <DataGridTableRowSelect row={row} />,
        size: 40,
        enableSorting: false,
        meta: { skeleton: <Skeleton className="size-5" /> },
        enableResizing: false,
      },
      {
        id: 'filename',
        accessorFn: (row) => row.stored_filename || row.original_filename,
        cell: ({ row }) => <span>{row.original.stored_filename || row.original.original_filename}</span>,
        header: ({ column }) => <DataGridColumnHeader title="Filename" column={column} />,
        size: 250,
        meta: { headerTitle: 'Filename', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'mime_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => {
          const mimeType = row.original.mime_type || '-';
          // Truncate long MIME types and show tooltip on hover
          const displayType = mimeType.length > 30 ? mimeType.substring(0, 30) + '...' : mimeType;
          return (
            <span title={mimeType} className="block truncate">
              {displayType}
            </span>
          );
        },
        size: 200,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'attachment_type',
        header: ({ column }) => <DataGridColumnHeader title="Attachment Type" column={column} />,
        cell: ({ row }) => row.original.attachment_type?.type_name ?? '-',
        size: 150,
        meta: { headerTitle: 'Attachment Type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'file_size_bytes',
        header: ({ column }) => <DataGridColumnHeader title="Size" column={column} />,
        cell: ({ row }) => row.original.file_size_bytes ? formatFileSize(row.original.file_size_bytes) : '-',
        size: 100,
        meta: { headerTitle: 'Size' },
      },
      {
        accessorKey: 'uploaded_by_user.name',
        header: ({ column }) => <DataGridColumnHeader title="Uploaded By" column={column} />,
        cell: ({ row }) => row.original.uploaded_by_user?.name ?? row.original.uploaded_by_user?.email ?? '-',
        size: 150,
        meta: { headerTitle: 'Uploaded By' },
      },
      {
        accessorKey: 'uploaded_at',
        header: ({ column }) => <DataGridColumnHeader title="Upload at" column={column} />,
        cell: ({ row }) => formatDateTime(new Date(row.original.uploaded_at)),
        size: 180,
        meta: { headerTitle: 'Upload at' },
      },
      {
        accessorKey: 'entity_type',
        header: ({ column }) => <DataGridColumnHeader title="Entity" column={column} />,
        size: 120,
        meta: { headerTitle: 'Entity' },
      },
      {
        accessorKey: 'entity_name',
        header: ({ column }) => <DataGridColumnHeader title="Entity Name" column={column} />,
        size: 150,
        meta: { headerTitle: 'Entity Name' },
      },
      {
        id: 'company',
        header: ({ column }) => <DataGridColumnHeader title="Company" column={column} />,
        accessorFn: (row) => row.company_name ?? '',
        enableSorting: false,
        cell: ({ row }) => {
          const label = attachmentCompanyLabel(row.original);
          return (
            <Badge appearance="light" size="sm" className="max-w-full truncate" title={label}>
              {label}
            </Badge>
          );
        },
        size: 110,
        minSize: 90,
        meta: { headerTitle: 'Company', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        // The row opens the record; everything else is one menu, the same one
        // the record's gear renders (D15). It used to be five icon buttons.
        cell: ({ row }) => <AttachmentRowActions attachment={row.original} />,
        size: 60,
        enableHiding: false,
      },
    ],
    [],
  );

  const handleBulkDelete = () => {
    if (selectedDeletableIds.length > 0) {
      setBulkDeleteDialogOpen(true);
    }
  };

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: (row: Row<Attachment>) => isTrashView || !row.original.is_deleted,
    columnResizeMode: 'onChange',
  });

  // Bulk actions surface in the toolbar's selection strip; trash vs active
  // folders expose different sets, mirroring the previous hand-rolled buttons.
  const bulkActions: ToolbarAction[] = [];
  if (selectedDeletableIds.length > 0 && isTrashView) {
    bulkActions.push({
      key: 'bulk-restore',
      label: `Restore selected (${selectedDeletableIds.length})`,
      icon: RotateCcw,
      disabled: bulkRestoreMutation.isPending,
      onClick: () =>
        bulkRestoreMutation.mutate(selectedDeletableIds, {
          onSuccess: () => setRowSelection({}),
        }),
    });
    bulkActions.push({
      key: 'bulk-permanent-delete',
      label: `Permanently delete (${selectedDeletableIds.length})`,
      icon: Trash2,
      destructive: true,
      onClick: () => setBulkDeleteDialogOpen(true),
    });
  }
  if (selectedDeletableIds.length > 0 && !isTrashView) {
    bulkActions.push({
      key: 'bulk-attachment-type',
      label: `Attachment type (${selectedDeletableIds.length})`,
      icon: Tag,
      onClick: () => setBulkEditTypeOpen(true),
    });
    bulkActions.push({
      key: 'bulk-company',
      label: `Set company (${selectedDeletableIds.length})`,
      icon: Building2,
      onClick: () => setSetCompanyDialogOpen(true),
    });
    bulkActions.push({
      key: 'bulk-resubmit',
      label: `Resubmit selected (${selectedDeletableIds.length})`,
      icon: RefreshCw,
      disabled: isResubmittingBulk,
      onClick: handleBulkResubmit,
    });
    bulkActions.push({
      key: 'bulk-delete',
      label: `Delete selected (${selectedDeletableIds.length})`,
      icon: Trash2,
      destructive: true,
      onClick: handleBulkDelete,
    });
  }

  // Always-visible toolbar actions (not selection-gated).
  const secondaryActions: ToolbarAction[] = [];
  if (!isTrashView) {
    secondaryActions.push({
      key: 'bulk-import',
      label: 'Bulk import (ZIP)',
      icon: FileArchive,
      onClick: () => setBulkImportDialogOpen(true),
    });
  }

  // The folder/trash selector lives in the filter popover; count it as an active
  // filter only when narrowed away from "All folders".
  const totalFilterCount =
    (directoryId !== null ? 1 : 0) +
    (attachmentTypeId !== '__all__' ? 1 : 0) +
    (linkStatus !== '__all__' ? 1 : 0) +
    (companyFilter ? 1 : 0) +
    (uploadedBy.trim() ? 1 : 0) +
    (uploadedAtFrom || uploadedAtTo ? 1 : 0);

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        rowHref={(row) =>
          `/resource-management/attachments/${row.id}${detailSearch ? `?${detailSearch}` : ''}`
        }
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
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
                  placeholder="Search attachments..."
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: totalFilterCount > 0,
                activeCount: totalFilterCount,
                content: (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Folder</p>
                      <SearchableSelect
                        value={directoryId ?? '__all__'}
                        onChange={(v) =>
                          setDirectoryId(v === '__all__' ? null : v === '__trash__' ? '__trash__' : v)
                        }
                        options={[
                          { value: '__all__', label: 'All folders' },
                          ...flattenDirectoryTree(directoryTree).map((d) => ({
                            value: d.id,
                            label: d.label,
                          })),
                          { value: '__trash__', label: 'Trash' },
                        ]}
                        placeholder="All folders"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Attachment type</p>
                      <SearchableSelect
                        value={attachmentTypeId}
                        onChange={(value) => {
                          setAttachmentTypeId(value);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        options={[
                          { value: '__all__', label: 'All attachment types' },
                          ...attachmentTypes.map((type) => ({
                            value: type.id,
                            label: type.type_name,
                          })),
                        ]}
                        placeholder="Attachment type"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Link status</p>
                      <SearchableSelect
                        value={linkStatus}
                        onChange={(value) => {
                          setLinkStatus(value as '__all__' | 'linked' | 'unlinked');
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        options={[
                          { value: '__all__', label: 'All files' },
                          { value: 'linked', label: 'Linked' },
                          { value: 'unlinked', label: 'Not linked' },
                        ]}
                        placeholder="Link status"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Company</p>
                      <SearchableSelect
                        value={companyFilter}
                        onChange={(value) => {
                          setCompanyFilter(value);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        clearable
                        options={[
                          ...companyGrants.map((c) => ({ value: c.id, label: c.name })),
                          { value: SHARED_COMPANY_VALUE, label: 'Shared' },
                        ]}
                        placeholder="All companies"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Uploaded by user id</p>
                      <Input
                        placeholder="Uploaded by user id"
                        value={uploadedBy}
                        onChange={(event) => {
                          setUploadedBy(event.target.value);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        className="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Uploaded from</p>
                      <Input
                        type="date"
                        value={uploadedAtFrom}
                        onChange={(event) => {
                          setUploadedAtFrom(event.target.value);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        className="w-full"
                        aria-label="Uploaded from"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Uploaded to</p>
                      <Input
                        type="date"
                        value={uploadedAtTo}
                        onChange={(event) => {
                          setUploadedAtTo(event.target.value);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        className="w-full"
                        aria-label="Uploaded to"
                      />
                    </div>
                  </div>
                ),
              }}
              exportConfig={{ filename: 'attachments_export.xlsx' }}
              bulkActions={bulkActions}
              secondaryActions={secondaryActions}
              primaryAction={
                !isTrashView ? (
                  <Button
                    onClick={() => setUploadDialogOpen(true)}
                    data-guide-target="resource-management.files.upload-button"
                  >
                    <Plus className="size-4 mr-2" />
                    Create Attachment
                  </Button>
                ) : undefined
              }
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

    <AttachmentUploadDialog
      open={uploadDialogOpen}
      onOpenChange={setUploadDialogOpen}
      defaultDirectoryId={directoryId}
      onSuccess={() => {}}
    />
    <AttachmentBulkImportDialog
      open={bulkImportDialogOpen}
      onOpenChange={setBulkImportDialogOpen}
      defaultParentDirectoryId={directoryId}
    />


    <AttachmentBulkDeleteDialog
      open={bulkDeleteDialogOpen}
      onOpenChange={setBulkDeleteDialogOpen}
      attachmentIds={selectedDeletableIds}
      permanent={isTrashView}
      onSuccess={() => setRowSelection({})}
    />

    <EditAttachmentTypeDialog
      open={bulkEditTypeOpen}
      onOpenChange={setBulkEditTypeOpen}
      attachmentIds={selectedDeletableIds}
      onSaved={() => setRowSelection({})}
    />

    <SetCompanyDialog
      open={setCompanyDialogOpen}
      onOpenChange={setSetCompanyDialogOpen}
      fileIds={selectedDeletableIds}
      folderIds={[]}
      onApplied={() => setRowSelection({})}
    />

    </>
  );
}
