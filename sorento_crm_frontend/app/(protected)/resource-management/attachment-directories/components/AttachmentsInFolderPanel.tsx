'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
} from '@tanstack/react-table';
import {
  Download,
  Trash2,
  Plus,
  RefreshCw,
  FileArchive,
  RotateCcw,
  Shield,
  Tag,
  FolderInput,
  FolderOpen,
  ChevronDown,
  List,
  LayoutGrid,
  Building2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar, type ToolbarAction } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTableRowSelect, DataGridTableRowSelectAll } from '@/components/ui/data-grid-table';
import DriveListView from './DriveListView';
import DriveGridView from './DriveGridView';
import DriveBreadcrumb from './DriveBreadcrumb';
import DriveRowActions from './DriveRowActions';
import AccessLevelsCell from './AccessLevelsCell';
import MoveToDialog, { type MoveSelection } from './MoveToDialog';
import { buildDriveBulkActions } from './driveBulkActions';
import { buildBreadcrumb } from './drivePath';
import { useDriveViewMode } from './useDriveViewMode';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import {
  useDriveContents,
  useDownloadAttachment,
  useRestoreAttachment,
  useBulkRestoreAttachments,
  useRestoreDirectory,
  useUpdateAttachment,
  useDirectoryTree,
  useCreateDirectory,
  useUpdateDirectory,
  useDeleteDirectory,
} from '../../attachments/hooks/useAttachments';
import { useAttachmentTypes } from '../../attachment-types/hooks/useAttachmentTypes';
import { useCompany } from '@/app/providers/CompanyProvider';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Calendar } from '@/components/ui/calendar';
import { getUserLookup, type UserLookupItem } from '@/services/userSelectService';
import { useQuery } from '@tanstack/react-query';
import { format } from 'date-fns';
import { CalendarDays } from 'lucide-react';
import type { DateRange } from 'react-day-picker';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { getAttachmentPreviewUrl, resubmitAttachmentWebhook } from '../../attachments/services/attachmentService';
import {
  isFileItem,
  isFolderItem,
  type DriveItem,
  type DriveFileItem,
} from '../../attachments/services/driveService';
import type { Attachment } from '../../attachments/types/attachment.types';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useIsMobile } from '@/hooks/use-mobile';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import AttachmentUploadDialog from '../../attachments/components/AttachmentUploadDialog';
import AttachmentBulkImportDialog from '../../attachments/components/AttachmentBulkImportDialog';
import AttachmentDeleteDialog from '../../attachments/components/attachment-delete-dialog';
import AttachmentBulkDeleteDialog from '../../attachments/components/AttachmentBulkDeleteDialog';
import AttachmentDetailModal from '../../attachments/components/AttachmentDetailModal';
import EditAttachmentTypeDialog from '../../attachments/components/EditAttachmentTypeDialog';
import SetCompanyDialog, { SHARED_COMPANY_VALUE } from '../../attachments/components/SetCompanyDialog';
import { attachmentCompanyLabel } from '../../attachments/types/attachment.types';
import { Badge } from '@/components/ui/badge';
import { TRASH_VIEW_ID, TRASH_FOLDER_PREFIX, FOLDER_ALL_ID } from '../constants';

interface AttachmentsInFolderPanelProps {
  directoryId: string | null;
  directoryName?: string | null;
  /** Called when user restores a folder from trash; parent can switch view. */
  onRestoreFolder?: () => void;
  /** Drill into / navigate to a folder (drives both panes). null = root. */
  onSelectFolder?: (id: string | null) => void;
  /** Bulk adjust access levels for the selected attachment rows. */
  onBulkAdjustAccessLevels?: (attachmentIds: string[]) => void;
  /** Toggle the left tree drawer (mobile). */
  onToggleTreeDrawer?: () => void;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
}

export default function AttachmentsInFolderPanel({
  directoryId,
  onRestoreFolder,
  onSelectFolder,
  onBulkAdjustAccessLevels,
  onToggleTreeDrawer,
}: AttachmentsInFolderPanelProps) {
  const isMobile = useIsMobile();
  const [viewMode, setViewMode] = useDriveViewMode();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'name', desc: false }]);
  const {
    value: searchInput,
    setValue: setSearchInputRaw,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  // The debounce handles the fetch; the page-0 reset stays inline with every
  // other filter's onChange rather than an effect keyed off the debounced value,
  // so it fires with the keystroke instead of ~200ms behind it.
  const setSearchInput = (next: string) => {
    setSearchInputRaw(next);
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };
  const [thisFolderOnly, setThisFolderOnly] = useState(false);
  const [accessLevelFilters, setAccessLevelFilters] = useState<string[]>([]);
  const [accessLevelsMatch, setAccessLevelsMatch] = useState<'any' | 'all' | 'exact'>('any');
  const [attachmentTypeId, setAttachmentTypeId] = useState<string>('__all__');
  const [linkStatus, setLinkStatus] = useState<'__all__' | 'linked' | 'unlinked'>('__all__');
  const [storageStatus, setStorageStatus] = useState<'__all__' | 'accessible' | 'missing' | 'unchecked'>('__all__');
  const [companyFilter, setCompanyFilter] = useState<string>('');
  const [uploadedBy, setUploadedBy] = useState<string>('__all__');
  const [uploadedRange, setUploadedRange] = useState<DateRange | undefined>();
  const uploadedAtFrom = uploadedRange?.from ? format(uploadedRange.from, 'yyyy-MM-dd') : '';
  const uploadedAtTo = uploadedRange?.to ? format(uploadedRange.to, 'yyyy-MM-dd') : '';

  const {
    data: usersSelect = [] as UserLookupItem[],
    error: usersSelectError,
    refetch: refetchUsersSelect,
  } = useQuery({
    queryKey: ['user-lookup', 'attachment-filter'],
    queryFn: () => getUserLookup(),
    staleTime: 5 * 60 * 1000,
  });
  const { data: attachmentTypesData } = useAttachmentTypes({
    pageIndex: 0,
    pageSize: 100,
    sorting: [{ id: 'type_name', desc: false }],
    searchQuery: '',
  });
  const attachmentTypes = attachmentTypesData?.data ?? [];
  const { data: accessTypes = [] } = useContactAccessTypes();
  const { data: tree = [] } = useDirectoryTree();

  const { grants: companyGrants } = useCompany();
  const extraFilterCount =
    (attachmentTypeId !== '__all__' ? 1 : 0) +
    (linkStatus !== '__all__' ? 1 : 0) +
    (storageStatus !== '__all__' ? 1 : 0) +
    (companyFilter ? 1 : 0) +
    (uploadedBy !== '__all__' ? 1 : 0) +
    (uploadedAtFrom || uploadedAtTo ? 1 : 0);
  const totalFilterCount = accessLevelFilters.length + extraFilterCount;

  const accessTypeNameByCode = useMemo(() => {
    const m = new Map<string, string>();
    for (const t of accessTypes) m.set(t.code, t.name);
    return m;
  }, [accessTypes]);

  const toggleAccessLevel = (code: string) => {
    setAccessLevelFilters((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };

  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [bulkImportDialogOpen, setBulkImportDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [bulkEditTypeOpen, setBulkEditTypeOpen] = useState(false);
  const [setCompanyDialogOpen, setSetCompanyDialogOpen] = useState(false);
  const [moveDialogOpen, setMoveDialogOpen] = useState(false);
  const [viewModalOpen, setViewModalOpen] = useState(false);
  const [viewAttachmentId, setViewAttachmentId] = useState<string | null>(null);

  const _router = useRouter();
  const _pathname = usePathname();
  const _searchParams = useSearchParams();
  useEffect(() => {
    const deepLinkId = _searchParams?.get('attachment_id');
    if (deepLinkId && deepLinkId !== viewAttachmentId) {
      setViewAttachmentId(deepLinkId);
      setViewModalOpen(true);
      const next = new URLSearchParams(_searchParams?.toString() ?? '');
      next.delete('attachment_id');
      const qs = next.toString();
      _router.replace(qs ? `${_pathname}?${qs}` : (_pathname ?? '/'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [_searchParams]);

  const [selectedAttachment, setSelectedAttachment] = useState<Attachment | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [pendingResubmitIds, setPendingResubmitIds] = useState<Set<string>>(new Set());

  const queryClient = useQueryClient();
  const isTrashView =
    directoryId === TRASH_VIEW_ID || (directoryId ?? '').startsWith(TRASH_FOLDER_PREFIX);
  const trashFolderId =
    directoryId?.startsWith(TRASH_FOLDER_PREFIX) ? directoryId.slice(TRASH_FOLDER_PREFIX.length) : null;

  // "All attachments"/root: omit directory_id so the drive returns root contents.
  // Trash sentinels (__trash__, trash:<id>) never leak as a real folder id - 
  // they resolve to null here; the drive call below applies is_deleted instead.
  const effectiveDirectoryId =
    directoryId === null || directoryId === FOLDER_ALL_ID || isTrashView
      ? null
      : directoryId;

  // Search non-empty OR an active file filter -> recursive scope (backend hides
  // folders). "This folder only" narrows a search back to non-recursive.
  const hasActiveFilter = totalFilterCount > 0;
  const isSearching = searchQuery.trim().length > 0;
  const recursive = (isSearching || hasActiveFilter) && !thisFolderOnly;

  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useDriveContents({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    directory_id: trashFolderId ?? (isTrashView ? null : effectiveDirectoryId),
    recursive: recursive || undefined,
    is_deleted: isTrashView ? true : undefined,
    access_levels: accessLevelFilters.length > 0 ? accessLevelFilters : undefined,
    access_levels_match: accessLevelFilters.length > 0 ? accessLevelsMatch : undefined,
    attachment_type_id: attachmentTypeId !== '__all__' ? attachmentTypeId : undefined,
    link_status: linkStatus !== '__all__' ? linkStatus : undefined,
    storage_status: storageStatus !== '__all__' ? storageStatus : undefined,
    company: companyFilter || undefined,
    uploaded_by: uploadedBy !== '__all__' ? uploadedBy : undefined,
    uploaded_at_from: uploadedAtFrom || undefined,
    uploaded_at_to: uploadedAtTo || undefined,
  });

  const items: DriveItem[] = useMemo(() => data?.data ?? [], [data?.data]);
  const fileItems = useMemo(() => items.filter(isFileItem), [items]);

  // Show the Location column only during a recursive/search scope.
  const showLocation = recursive;

  const restoreMutation = useRestoreAttachment();
  const bulkRestoreMutation = useBulkRestoreAttachments();
  const restoreDirectoryMutation = useRestoreDirectory();
  const downloadMutation = useDownloadAttachment();
  const updateMutation = useUpdateAttachment();
  const createDirectoryMutation = useCreateDirectory();
  const updateDirectoryMutation = useUpdateDirectory();
  const deleteDirectoryMutation = useDeleteDirectory();

  // ---- Folder-row dialogs (context menu on folder rows) --------------------
  const [folderRenameOpen, setFolderRenameOpen] = useState(false);
  const [folderRenameTarget, setFolderRenameTarget] = useState<{ id: string; name: string } | null>(
    null
  );
  const [folderRenameValue, setFolderRenameValue] = useState('');
  const [subfolderOpen, setSubfolderOpen] = useState(false);
  const [subfolderParent, setSubfolderParent] = useState<{ id: string; name: string } | null>(null);
  const [subfolderValue, setSubfolderValue] = useState('');
  const [folderDeleteOpen, setFolderDeleteOpen] = useState(false);
  const [folderDeleteTarget, setFolderDeleteTarget] = useState<{ id: string; name: string } | null>(
    null
  );

  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<DriveFileItem | null>(null);
  const [renameValue, setRenameValue] = useState('');

  const openRename = useCallback((file: DriveFileItem) => {
    setRenameTarget(file);
    setRenameValue(file.stored_filename || file.original_filename || '');
    setRenameDialogOpen(true);
  }, []);

  const submitRename = useCallback(async () => {
    if (!renameTarget) return;
    const next = renameValue.trim();
    if (!next) {
      toast.error('Filename cannot be empty.');
      return;
    }
    if (next === (renameTarget.stored_filename || renameTarget.original_filename)) {
      setRenameDialogOpen(false);
      return;
    }
    try {
      await updateMutation.mutateAsync({ attachmentId: renameTarget.id, data: { stored_filename: next } });
      toast.success('Renamed.');
      setRenameDialogOpen(false);
      setRenameTarget(null);
      queryClient.invalidateQueries({ queryKey: ['drive-contents'] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Rename failed.');
    }
  }, [renameTarget, renameValue, updateMutation, queryClient]);

  const [isResubmittingBulk, setIsResubmittingBulk] = useState(false);

  const handleResubmit = useCallback(
    async (id: string) => {
      setPendingResubmitIds((prev) => new Set(prev).add(id));
      try {
        await resubmitAttachmentWebhook(id);
        toast.success('Resubmitted successfully');
        queryClient.invalidateQueries({ queryKey: ['drive-contents'] });
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Failed to resubmit');
      } finally {
        setPendingResubmitIds((prev) => {
          const nextSet = new Set(prev);
          nextSet.delete(id);
          return nextSet;
        });
      }
    },
    [queryClient]
  );

  const handleDownloadFile = useCallback(
    async (file: DriveFileItem) => {
      try {
        const blob = await downloadMutation.mutateAsync(file.id);
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = file.stored_filename || file.original_filename || 'download';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } catch {
        // toast handled by mutation
      }
    },
    [downloadMutation]
  );

  const handlePreview = useCallback(async (attachmentId: string) => {
    try {
      const previewUrl = await getAttachmentPreviewUrl(attachmentId);
      if (previewUrl) window.open(previewUrl, '_blank');
    } catch {
      toast.error('Failed to open attachment preview');
    }
  }, []);

  // ---- Selection helpers (mixed folder + file) -----------------------------
  // react-table row ids are `${kind}-${id}`; selection state mirrors that.
  const selectedItems = useMemo(
    () => items.filter((it) => rowSelection[`${it.kind}-${it.id}`]),
    [items, rowSelection]
  );
  const selectedFileIds = useMemo(
    () => selectedItems.filter(isFileItem).map((it) => it.id),
    [selectedItems]
  );
  const selectedFolderIds = useMemo(
    () => selectedItems.filter(isFolderItem).map((it) => it.id),
    [selectedItems]
  );
  const selectionHasFolder = selectedFolderIds.length > 0;
  const selectionCount = selectedItems.length;
  // File-only deletable ids for trash-aware bulk delete/restore.
  const selectedDeletableFileIds = useMemo(() => {
    if (isTrashView) return selectedFileIds;
    return selectedItems
      .filter(isFileItem)
      .filter((it) => !it.is_deleted)
      .map((it) => it.id);
  }, [selectedItems, selectedFileIds, isTrashView]);
  // Folder ids for the bulk-delete dialog. Folder soft-delete cascades the
  // subtree; in trash view they're permanently deleted. Folder rows only ever
  // appear in the non-recursive (browse) scope, where they're never deleted, so
  // selectedFolderIds is already the deletable set.
  const selectedDeletableFolderIds = selectedFolderIds;

  const clearSelection = useCallback(() => setRowSelection({}), []);

  // ---- Drill / open --------------------------------------------------------
  const navigateToFolder = useCallback(
    (folderId: string | null) => {
      // Drilling into a folder clears the active search (B7) and resets paging.
      resetSearchQuery('');
      setThisFolderOnly(false);
      setPagination((p) => ({ ...p, pageIndex: 0 }));
      clearSelection();
      onSelectFolder?.(folderId);
    },
    [onSelectFolder, clearSelection]
  );

  const openItem = useCallback(
    (item: DriveItem) => {
      if (isFolderItem(item)) {
        navigateToFolder(item.id);
      } else {
        setViewAttachmentId(item.id);
        setViewModalOpen(true);
      }
    },
    [navigateToFolder]
  );

  const revealInFolder = useCallback(
    (file: DriveFileItem) => {
      navigateToFolder(file.directory_id ?? null);
    },
    [navigateToFolder]
  );

  // ---- Bulk: resubmit / move ----------------------------------------------
  const handleBulkResubmit = useCallback(async () => {
    const ids = selectedDeletableFileIds;
    if (ids.length === 0) return;
    setIsResubmittingBulk(true);
    setPendingResubmitIds((prev) => new Set(Array.from(prev).concat(ids)));
    let successCount = 0;
    let failCount = 0;
    for (const id of ids) {
      try {
        await resubmitAttachmentWebhook(id);
        successCount += 1;
      } catch {
        failCount += 1;
      } finally {
        setPendingResubmitIds((prev) => {
          const nextSet = new Set(Array.from(prev));
          nextSet.delete(id);
          return nextSet;
        });
      }
    }
    setIsResubmittingBulk(false);
    queryClient.invalidateQueries({ queryKey: ['drive-contents'] });
    if (failCount === 0) {
      toast.success('Resubmitted successfully');
      clearSelection();
    } else if (successCount === 0) {
      toast.error(`Failed to resubmit for ${failCount} attachment(s)`);
    } else {
      toast.warning(`Resubmitted ${successCount}, failed ${failCount}`);
    }
  }, [selectedDeletableFileIds, queryClient, clearSelection]);

  // ---- Columns (unified folder + file rows) --------------------------------
  const columns = useMemo<ColumnDef<DriveItem>[]>(() => {
    const cols: ColumnDef<DriveItem>[] = [
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
        id: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        accessorFn: (row) =>
          isFolderItem(row) ? row.name : row.stored_filename || row.original_filename,
        cell: ({ row }) => {
          const item = row.original;
          const label = isFolderItem(item)
            ? item.name
            : item.stored_filename || item.original_filename;
          return (
            <span className="flex items-center gap-2 min-w-0">
              {isFolderItem(item) ? (
                <FolderOpen className="size-4 shrink-0 text-muted-foreground" />
              ) : (
                <Download className="size-4 shrink-0 text-transparent" aria-hidden />
              )}
              <span className="truncate" title={label}>
                {label}
              </span>
            </span>
          );
        },
        size: 260,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
    ];

    if (showLocation) {
      cols.push({
        id: 'location',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        accessorFn: (row) => row.directory_path ?? '',
        enableSorting: false,
        cell: ({ row }) => {
          const loc = row.original.directory_path;
          return loc ? (
            <span className="block truncate text-muted-foreground" title={loc}>
              {loc}
            </span>
          ) : (
            <span className="text-muted-foreground">All files</span>
          );
        },
        // Larger default so "Marketing / Sorento …" paths aren't clipped early;
        // resizable (the grid's columnsResizable flag), truncate + title for
        // overflow (review item B).
        size: 320,
        minSize: 160,
        meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-28" /> },
      });
    }

    cols.push(
      {
        id: 'type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        accessorFn: (row) => (isFileItem(row) ? row.mime_type : null),
        cell: ({ row }) => {
          const item = row.original;
          if (isFolderItem(item)) return <span className="text-muted-foreground">Folder</span>;
          const mimeType = item.mime_type || '-';
          const displayType = mimeType.length > 30 ? mimeType.substring(0, 30) + '...' : mimeType;
          return (
            <span title={mimeType} className="block truncate">
              {displayType}
            </span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'access_levels',
        header: ({ column }) => <DataGridColumnHeader title="Access" column={column} />,
        accessorFn: (row) => (isFileItem(row) ? (row.access_levels ?? []).join(',') : ''),
        enableSorting: false,
        cell: ({ row }) => {
          const item = row.original;
          // Folders have no access levels -> render nothing (review item A).
          if (isFolderItem(item)) return null;
          const levels = Array.from(new Set(item.access_levels ?? []));
          return <AccessLevelsCell levels={levels} nameByCode={accessTypeNameByCode} />;
        },
        size: 200,
        minSize: 140,
        maxSize: 200,
        enableResizing: false,
        meta: {
          headerTitle: 'Access',
          skeleton: <Skeleton className="h-4 w-24" />,
          // Keep access on a single line; the "+N" popover holds the overflow so
          // the column never grows the row height.
          cellClassName: 'overflow-hidden',
        },
      },
      {
        id: 'attachment_type',
        header: ({ column }) => <DataGridColumnHeader title="Attachment Type" column={column} />,
        accessorFn: (row) => (isFileItem(row) ? row.attachment_type?.type_name : null),
        cell: ({ row }) => {
          const item = row.original;
          if (isFolderItem(item)) return <span className="text-muted-foreground"> - </span>;
          return item.attachment_type?.type_name ?? '-';
        },
        size: 150,
        meta: { headerTitle: 'Attachment Type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'size',
        header: ({ column }) => <DataGridColumnHeader title="Size" column={column} />,
        accessorFn: (row) => (isFileItem(row) ? row.file_size_bytes : null),
        cell: ({ row }) => {
          const item = row.original;
          if (isFolderItem(item)) return <span className="text-muted-foreground"> - </span>;
          return item.file_size_bytes ? formatFileSize(item.file_size_bytes) : '-';
        },
        size: 100,
        meta: { headerTitle: 'Size' },
      },
      {
        id: 'uploaded_by',
        header: ({ column }) => <DataGridColumnHeader title="Uploaded By" column={column} />,
        accessorFn: (row) =>
          isFileItem(row) ? row.uploaded_by_user?.name ?? row.uploaded_by_user?.email : null,
        cell: ({ row }) => {
          const item = row.original;
          if (isFolderItem(item)) return <span className="text-muted-foreground"> - </span>;
          return item.uploaded_by_user?.name ?? item.uploaded_by_user?.email ?? '-';
        },
        size: 150,
        meta: { headerTitle: 'Uploaded By' },
      },
      {
        id: 'modified',
        header: ({ column }) => <DataGridColumnHeader title="Modified" column={column} />,
        accessorFn: (row) => (isFileItem(row) ? row.uploaded_at : row.created_at),
        cell: ({ row }) => {
          const item = row.original;
          const ts = isFileItem(item) ? item.uploaded_at : item.created_at;
          return ts ? formatDateTimeInMalaysia(ts as string) : '-';
        },
        size: 180,
        meta: { headerTitle: 'Modified' },
      },
      {
        id: 'company',
        header: ({ column }) => <DataGridColumnHeader title="Company" column={column} />,
        accessorFn: (row) => row.company_name ?? '',
        enableSorting: false,
        cell: ({ row }) => {
          const item = row.original;
          const label = attachmentCompanyLabel(item);
          return (
            <Badge appearance="light" size="sm" className="max-w-full truncate" title={label}>
              {label}
            </Badge>
          );
        },
        size: 110,
        minSize: 90,
        meta: { headerTitle: 'Company', skeleton: <Skeleton className="h-4 w-16" /> },
      }
    );

    return cols;
  }, [showLocation, accessTypeNameByCode]);

  const table = useReactTable({
    columns,
    data: items,
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => `${row.kind}-${row.id}`,
    state: { pagination, sorting, rowSelection },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: (row) => isTrashView || !(isFileItem(row.original) && row.original.is_deleted),
    columnResizeMode: 'onChange',
  });

  // ---- Bulk "Action" dropdown (single button, gated by selection) ----------
  // Gating lives in the pure helper (UAC F2/F3/F6); icons are merged in here.
  const BULK_ACTION_ICONS: Record<string, typeof Trash2> = {
    'bulk-restore': RotateCcw,
    'bulk-permanent-delete': Trash2,
    'bulk-move': FolderInput,
    'bulk-export': Download,
    'bulk-access-levels': Shield,
    'bulk-attachment-type': Tag,
    'bulk-company': Building2,
    'bulk-resubmit': RefreshCw,
    'bulk-delete': Trash2,
  };
  // `openExport` is supplied by the toolbar so "Export selected" reuses its
  // existing export dialog (selected rows -> xlsx); the standalone Export button
  // only appears in the no-selection state.
  const buildBulkActionsWith = (openExport: () => void): ToolbarAction[] =>
    buildDriveBulkActions(
      {
        selectionCount,
        selectionHasFolder,
        isTrashView,
        isResubmitting: isResubmittingBulk,
        canRestore: !bulkRestoreMutation.isPending && selectedDeletableFileIds.length > 0,
      },
      {
        onMove: () => setMoveDialogOpen(true),
        onExport: openExport,
        onSetAccessLevels: () => onBulkAdjustAccessLevels?.(selectedDeletableFileIds),
        onSetAttachmentType: () => setBulkEditTypeOpen(true),
        onSetCompany: () => {
          setSetCompanyContextItem(null);
          setSetCompanyDialogOpen(true);
        },
        onResubmit: handleBulkResubmit,
        onDelete: () => setBulkDeleteDialogOpen(true),
        onRestore: () =>
          bulkRestoreMutation.mutate(selectedDeletableFileIds, { onSuccess: clearSelection }),
      }
    ).map((a) => ({ ...a, icon: BULK_ACTION_ICONS[a.key] }));

  // Secondary (always-visible) actions.
  const secondaryActions: ToolbarAction[] = [];
  if (trashFolderId) {
    secondaryActions.push({
      key: 'restore-folder',
      label: 'Restore folder and contents',
      icon: RotateCcw,
      disabled: restoreDirectoryMutation.isPending,
      onClick: () => restoreDirectoryMutation.mutate(trashFolderId, { onSuccess: onRestoreFolder }),
    });
  }
  if (!isTrashView) {
    secondaryActions.push({
      key: 'bulk-import',
      label: 'Bulk import (ZIP)',
      icon: FileArchive,
      onClick: () => setBulkImportDialogOpen(true),
      dataGuideTarget: 'resource-management.files.bulk-import-button',
    });
  }

  // Single "Action" dropdown for the bulk strip (UAC F2): EVERY bulk action lives
  // here - Export selected, Set access levels, Set attachment type, Resubmit,
  // Delete, Move. File-only actions are disabled when a folder is selected (F3).
  // Rendered as a function so it can reuse the toolbar's selected-rows export.
  const renderBulkActionsSlot = ({ openExport }: { openExport: () => void }) => {
    if (selectionCount === 0) return null;
    const actions = buildBulkActionsWith(openExport);
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="gap-1.5">
            Action
            <ChevronDown className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {actions.map((action) => {
            const Icon = action.icon;
            const isDestructive = action.destructive;
            return (
              <DropdownMenuItem
                key={action.key}
                disabled={action.disabled}
                onClick={action.onClick}
                className={
                  isDestructive ? 'text-destructive focus:text-destructive' : undefined
                }
                title={action.disabled ? action.disabledReason : undefined}
              >
                {Icon ? <Icon className="size-4 mr-2" /> : null}
                {action.label}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    );
  };

  const crumbs = useMemo(
    () => buildBreadcrumb(tree, isTrashView ? null : effectiveDirectoryId),
    [tree, effectiveDirectoryId, isTrashView]
  );

  const draggable = !isTrashView && !isMobile;

  // The Move dialog serves both the bulk strip (multi-select) and the per-row
  // context menu (a single item). `moveContextItem` set => single-item move.
  const [moveContextItem, setMoveContextItem] = useState<DriveItem | null>(null);
  const moveSelection: MoveSelection = moveContextItem
    ? {
        fileIds: isFileItem(moveContextItem) ? [moveContextItem.id] : [],
        folderIds: isFolderItem(moveContextItem) ? [moveContextItem.id] : [],
      }
    : { fileIds: selectedFileIds, folderIds: selectedFolderIds };

  const openMoveForItem = useCallback((item: DriveItem) => {
    setMoveContextItem(item);
    setMoveDialogOpen(true);
  }, []);

  // Set company serves the same two callers as Move (bulk strip + per-row
  // context menu). `setCompanyContextItem` set => single-item.
  const [setCompanyContextItem, setSetCompanyContextItem] = useState<DriveItem | null>(null);
  const setCompanySelection = setCompanyContextItem
    ? {
        fileIds: isFileItem(setCompanyContextItem) ? [setCompanyContextItem.id] : [],
        folderIds: isFolderItem(setCompanyContextItem) ? [setCompanyContextItem.id] : [],
      }
    : { fileIds: selectedFileIds, folderIds: selectedFolderIds };

  const openSetCompanyForItem = useCallback((item: DriveItem) => {
    setSetCompanyContextItem(item);
    setSetCompanyDialogOpen(true);
  }, []);

  const performMove = useCallback(
    (destinationId: string | null) => {
      const fileIds = moveContextItem
        ? isFileItem(moveContextItem)
          ? [moveContextItem.id]
          : []
        : selectedFileIds;
      const folderIds = moveContextItem
        ? isFolderItem(moveContextItem)
          ? [moveContextItem.id]
          : []
        : selectedFolderIds;

      Promise.allSettled([
        ...fileIds.map((id) =>
          updateMutation.mutateAsync({ attachmentId: id, data: { directory_id: destinationId } })
        ),
        ...folderIds.map((id) =>
          updateDirectoryMutation.mutateAsync({ id, data: { parent_id: destinationId } })
        ),
      ]).then((results) => {
        const ok = results.filter((r) => r.status === 'fulfilled').length;
        if (ok > 0) toast.success(`Moved ${ok} item(s).`);
        const failed = results.length - ok;
        if (failed > 0) toast.error(`${failed} move(s) failed.`);
        queryClient.invalidateQueries({ queryKey: ['drive-contents'] });
        setMoveDialogOpen(false);
        setMoveContextItem(null);
        if (!moveContextItem) clearSelection();
      });
    },
    [
      moveContextItem,
      selectedFileIds,
      selectedFolderIds,
      updateMutation,
      updateDirectoryMutation,
      queryClient,
      clearSelection,
    ]
  );

  // ---- Folder context-menu handlers ---------------------------------------
  const openFolderRename = useCallback((item: DriveItem) => {
    if (!isFolderItem(item)) return;
    setFolderRenameTarget({ id: item.id, name: item.name });
    setFolderRenameValue(item.name);
    setFolderRenameOpen(true);
  }, []);

  const submitFolderRename = useCallback(() => {
    if (!folderRenameTarget || !folderRenameValue.trim()) return;
    updateDirectoryMutation.mutate(
      { id: folderRenameTarget.id, data: { name: folderRenameValue.trim() } },
      {
        onSuccess: () => {
          setFolderRenameOpen(false);
          setFolderRenameTarget(null);
          queryClient.invalidateQueries({ queryKey: ['drive-contents'] });
        },
      }
    );
  }, [folderRenameTarget, folderRenameValue, updateDirectoryMutation, queryClient]);

  const openNewSubfolder = useCallback((item: DriveItem) => {
    if (!isFolderItem(item)) return;
    setSubfolderParent({ id: item.id, name: item.name });
    setSubfolderValue('');
    setSubfolderOpen(true);
  }, []);

  const submitNewSubfolder = useCallback(() => {
    if (!subfolderParent || !subfolderValue.trim()) return;
    createDirectoryMutation.mutate(
      { name: subfolderValue.trim(), parentId: subfolderParent.id },
      {
        onSuccess: () => {
          setSubfolderOpen(false);
          queryClient.invalidateQueries({ queryKey: ['drive-contents'] });
        },
      }
    );
  }, [subfolderParent, subfolderValue, createDirectoryMutation, queryClient]);

  const openFolderDelete = useCallback((item: DriveItem) => {
    if (!isFolderItem(item)) return;
    setFolderDeleteTarget({ id: item.id, name: item.name });
    setFolderDeleteOpen(true);
  }, []);

  const submitFolderDelete = useCallback(() => {
    if (!folderDeleteTarget) return;
    deleteDirectoryMutation.mutate(folderDeleteTarget.id, {
      onSuccess: () => {
        setFolderDeleteOpen(false);
        setFolderDeleteTarget(null);
        queryClient.invalidateQueries({ queryKey: ['drive-contents'] });
      },
    });
  }, [folderDeleteTarget, deleteDirectoryMutation, queryClient]);

  // Per-row context menu (right-click / long-press) - replaces the actions
  // column. Reuses the exact handlers the old "..." menu wired up (review C).
  const renderRowContextMenu = useCallback(
    (item: DriveItem) => (
      <DriveRowActions
        item={item}
        isTrashView={isTrashView}
        recursive={recursive}
        resubmitting={isFileItem(item) && pendingResubmitIds.has(item.id)}
        handlers={{
          onOpen: openItem,
          onPreview: handlePreview,
          onDownload: (it) => {
            if (isFileItem(it)) handleDownloadFile(it);
          },
          onRevealInFolder: (it) => {
            if (isFileItem(it)) revealInFolder(it);
          },
          onRename: (it) => {
            if (isFileItem(it)) openRename(it);
          },
          onMove: openMoveForItem,
          onSetCompany: openSetCompanyForItem,
          onResubmit: handleResubmit,
          onRestore: (id) => restoreMutation.mutate(id),
          onDelete: (it) => {
            if (isFileItem(it)) {
              setSelectedAttachment(it as unknown as Attachment);
              setDeleteDialogOpen(true);
            }
          },
          onRenameFolder: openFolderRename,
          onNewSubfolder: openNewSubfolder,
          onDeleteFolder: openFolderDelete,
        }}
      />
    ),
    [
      isTrashView,
      recursive,
      pendingResubmitIds,
      openItem,
      handlePreview,
      handleDownloadFile,
      revealInFolder,
      openRename,
      openMoveForItem,
      openSetCompanyForItem,
      handleResubmit,
      restoreMutation,
      openFolderRename,
      openNewSubfolder,
      openFolderDelete,
    ]
  );

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total || 0}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        error={error}
        onRetry={() => void refetch()}
        onRowClick={(row) => openItem(row)}
        listingKey="resource-management.files.view::unified-drive"
        tableLayout={{
          width: 'fixed',
          columnsResizable: true,
          columnsVisibility: true,
        }}
      >
        <Card>
          <CardHeader className="block">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {isMobile && onToggleTreeDrawer && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onToggleTreeDrawer}
                  aria-label="Show folders"
                >
                  <FolderOpen className="size-4" />
                </Button>
              )}
              <div className="min-w-0 flex-1">
                <DriveBreadcrumb crumbs={crumbs} onNavigate={navigateToFolder} droppable={draggable} />
              </div>
              <div className="flex items-center rounded-md border p-0.5">
                <Button
                  variant={viewMode === 'list' ? 'secondary' : 'ghost'}
                  size="sm"
                  className="size-7 p-0"
                  onClick={() => setViewMode('list')}
                  aria-label="List view"
                  aria-pressed={viewMode === 'list'}
                >
                  <List className="size-4" />
                </Button>
                <Button
                  variant={viewMode === 'grid' ? 'secondary' : 'ghost'}
                  size="sm"
                  className="size-7 p-0"
                  onClick={() => setViewMode('grid')}
                  aria-label="Grid view"
                  aria-pressed={viewMode === 'grid'}
                >
                  <LayoutGrid className="size-4" />
                </Button>
              </div>
            </div>
            <DataGridListToolbar
              table={table}
              searchSlot={
                <div className="flex items-center gap-2">
                  <ListSearchInput
                    value={searchInput}
                    onChange={setSearchInput}
                    isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                    placeholder="Search files & folders..."
                    className="w-64"
                  />
                  {isSearching && (
                    <label className="flex items-center gap-1.5 text-xs text-muted-foreground whitespace-nowrap">
                      <Checkbox
                        checked={thisFolderOnly}
                        onCheckedChange={(v) => {
                          setThisFolderOnly(v === true);
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
                        }}
                      />
                      This folder only
                    </label>
                  )}
                </div>
              }
              filters={{
                kind: 'custom',
                active: totalFilterCount > 0,
                activeCount: totalFilterCount,
                content: (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="font-medium text-sm">Filters</h4>
                      {totalFilterCount > 0 && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-auto px-1 text-xs"
                          onClick={() => {
                            setAccessLevelFilters([]);
                            setAccessLevelsMatch('any');
                            setAttachmentTypeId('__all__');
                            setLinkStatus('__all__');
                            setStorageStatus('__all__');
                            setCompanyFilter('');
                            setUploadedBy('__all__');
                            setUploadedRange(undefined);
                            setPagination((p) => ({ ...p, pageIndex: 0 }));
                          }}
                        >
                          Clear all
                        </Button>
                      )}
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Attachment type</p>
                      <SearchableSelect
                        value={attachmentTypeId}
                        onChange={(v) => {
                          setAttachmentTypeId(v);
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
                        }}
                        options={[
                          { value: '__all__', label: 'All attachment types' },
                          ...attachmentTypes.map((t) => ({
                            value: t.id,
                            label: t.type_name,
                          })),
                        ]}
                        placeholder="All attachment types"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Link status</p>
                      <SearchableSelect
                        value={linkStatus}
                        onChange={(v) => {
                          setLinkStatus(v as '__all__' | 'linked' | 'unlinked');
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
                        }}
                        options={[
                          { value: '__all__', label: 'All files' },
                          { value: 'linked', label: 'Linked' },
                          { value: 'unlinked', label: 'Not linked' },
                        ]}
                        placeholder="All files"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Storage status</p>
                      <SearchableSelect
                        value={storageStatus}
                        onChange={(v) => {
                          setStorageStatus(v as '__all__' | 'accessible' | 'missing' | 'unchecked');
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
                        }}
                        options={[
                          { value: '__all__', label: 'All files' },
                          { value: 'accessible', label: 'Accessible' },
                          { value: 'missing', label: 'Missing (broken)' },
                          { value: 'unchecked', label: 'Unchecked' },
                        ]}
                        placeholder="All files"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Company</p>
                      <SearchableSelect
                        value={companyFilter}
                        onChange={(v) => {
                          setCompanyFilter(v);
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
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
                      <p className="text-xs font-medium">Uploaded by</p>
                      <SearchableSelect
                        value={uploadedBy}
                        onChange={(v) => {
                          setUploadedBy(v);
                          setPagination((p) => ({ ...p, pageIndex: 0 }));
                        }}
                        loadError={usersSelectError}
                        onRetry={() => void refetchUsersSelect()}
                        options={[
                          { value: '__all__', label: 'All users' },
                          ...usersSelect.map((u) => ({
                            value: u.id,
                            label: u.name?.trim() || 'Unnamed user',
                          })),
                        ]}
                        placeholder="All users"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium">Uploaded date range</p>
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button variant="outline" className="w-full justify-start font-normal">
                            <CalendarDays className="size-4 me-2 opacity-70" />
                            {uploadedRange?.from ? (
                              uploadedRange.to ? (
                                <span>
                                  {format(uploadedRange.from, 'dd MMM yyyy')} -{' '}
                                  {format(uploadedRange.to, 'dd MMM yyyy')}
                                </span>
                              ) : (
                                <span>{format(uploadedRange.from, 'dd MMM yyyy')}</span>
                              )
                            ) : (
                              <span className="text-muted-foreground">Any date</span>
                            )}
                          </Button>
                        </PopoverTrigger>
                        <PopoverContent className="w-auto p-0" align="start">
                          <Calendar
                            mode="range"
                            defaultMonth={uploadedRange?.from}
                            selected={uploadedRange}
                            onSelect={(range) => {
                              setUploadedRange(range);
                              setPagination((p) => ({ ...p, pageIndex: 0 }));
                            }}
                            numberOfMonths={2}
                          />
                          {uploadedRange && (
                            <div className="flex justify-end p-2 border-t">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                  setUploadedRange(undefined);
                                  setPagination((p) => ({ ...p, pageIndex: 0 }));
                                }}
                              >
                                Clear
                              </Button>
                            </div>
                          )}
                        </PopoverContent>
                      </Popover>
                    </div>
                    <div className="border-t pt-2">
                      <h4 className="font-medium text-sm mb-1.5">Access levels</h4>
                    </div>
                    {accessTypes.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No access types configured.</p>
                    ) : (
                      <>
                        <label
                          htmlFor="access-filter-select-all"
                          className="flex items-center gap-2 text-sm cursor-pointer border-b pb-2"
                        >
                          <Checkbox
                            id="access-filter-select-all"
                            checked={
                              accessLevelFilters.length === accessTypes.length
                                ? true
                                : accessLevelFilters.length > 0
                                  ? 'indeterminate'
                                  : false
                            }
                            onCheckedChange={(v) => {
                              setAccessLevelFilters(v === true ? accessTypes.map((t) => t.code) : []);
                              setPagination((p) => ({ ...p, pageIndex: 0 }));
                            }}
                          />
                          <span className="font-medium">Select all</span>
                        </label>
                        <div className="flex flex-col gap-2 max-h-72 overflow-y-auto">
                          {accessTypes.map((opt) => {
                            const id = `access-filter-${opt.code}`;
                            const checked = accessLevelFilters.includes(opt.code);
                            return (
                              <label
                                key={opt.code}
                                htmlFor={id}
                                className="flex items-center gap-2 text-sm cursor-pointer"
                              >
                                <Checkbox
                                  id={id}
                                  checked={checked}
                                  onCheckedChange={() => toggleAccessLevel(opt.code)}
                                />
                                <span>{opt.name}</span>
                              </label>
                            );
                          })}
                        </div>
                        <div className="border-t pt-2 space-y-1.5">
                          <p className="text-xs font-medium">Match mode</p>
                          <RadioGroup
                            value={accessLevelsMatch}
                            onValueChange={(v) => {
                              setAccessLevelsMatch(v as 'any' | 'all' | 'exact');
                              setPagination((p) => ({ ...p, pageIndex: 0 }));
                            }}
                            size="sm"
                            className="gap-1.5"
                          >
                            <label htmlFor="access-match-any" className="flex items-start gap-2 text-xs cursor-pointer">
                              <RadioGroupItem id="access-match-any" value="any" className="mt-0.5" />
                              <span>
                                <span className="font-medium">Any of selected</span>
                                <span className="block text-muted-foreground">Show files tagged with at least one chosen level.</span>
                              </span>
                            </label>
                            <label htmlFor="access-match-all" className="flex items-start gap-2 text-xs cursor-pointer">
                              <RadioGroupItem id="access-match-all" value="all" className="mt-0.5" />
                              <span>
                                <span className="font-medium">All of selected</span>
                                <span className="block text-muted-foreground">File must include every chosen level (extras allowed).</span>
                              </span>
                            </label>
                            <label htmlFor="access-match-exact" className="flex items-start gap-2 text-xs cursor-pointer">
                              <RadioGroupItem id="access-match-exact" value="exact" className="mt-0.5" />
                              <span>
                                <span className="font-medium">Exactly these</span>
                                <span className="block text-muted-foreground">File&apos;s levels must match the selection exactly.</span>
                              </span>
                            </label>
                          </RadioGroup>
                        </div>
                      </>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: 'attachments_export.xlsx' }}
              bulkActionsSlot={renderBulkActionsSlot}
              secondaryActions={secondaryActions}
              primaryAction={
                !isTrashView ? (
                  <Button
                    onClick={() => setUploadDialogOpen(true)}
                    data-guide-target="resource-management.files.upload-button"
                  >
                    <Plus className="size-4 mr-2" />
                    Upload
                  </Button>
                ) : undefined
              }
            />
          </CardHeader>
          <CardTable>
            {viewMode === 'grid' ? (
              <div className="p-3">
                {isLoading ? (
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                    {Array.from({ length: 12 }).map((_, i) => (
                      <Skeleton key={i} className="aspect-[3/4] w-full rounded-lg" />
                    ))}
                  </div>
                ) : (
                  <DriveGridView
                    items={items}
                    selectedIds={selectedItems.map((it) => it.id)}
                    draggable={draggable}
                    currentDirectoryId={effectiveDirectoryId}
                    onOpen={openItem}
                    onToggleSelect={(id, next) => {
                      const item = items.find((it) => it.id === id);
                      if (!item) return;
                      setRowSelection((prev) => ({ ...prev, [`${item.kind}-${id}`]: next }));
                    }}
                    renderRowContextMenu={renderRowContextMenu}
                  />
                )}
              </div>
            ) : (
              <ScrollArea>
                <DriveListView
                  draggable={draggable}
                  currentDirectoryId={effectiveDirectoryId}
                  selectedIds={selectedItems.map((it) => it.id)}
                  renderRowContextMenu={renderRowContextMenu}
                />
                <ScrollBar orientation="horizontal" />
              </ScrollArea>
            )}
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>

      <AttachmentUploadDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        defaultDirectoryId={effectiveDirectoryId ?? null}
        onSuccess={() => {}}
      />
      <AttachmentBulkImportDialog
        open={bulkImportDialogOpen}
        onOpenChange={setBulkImportDialogOpen}
        defaultParentDirectoryId={directoryId}
      />

      <AttachmentDeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        attachment={selectedAttachment}
        permanent={isTrashView}
      />

      <AttachmentBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={setBulkDeleteDialogOpen}
        attachmentIds={selectedDeletableFileIds}
        folderIds={selectedDeletableFolderIds}
        permanent={isTrashView}
        onSuccess={clearSelection}
      />

      <EditAttachmentTypeDialog
        open={bulkEditTypeOpen}
        onOpenChange={setBulkEditTypeOpen}
        attachmentIds={selectedDeletableFileIds}
        onSaved={clearSelection}
      />

      <SetCompanyDialog
        open={setCompanyDialogOpen}
        onOpenChange={(open) => {
          setSetCompanyDialogOpen(open);
          if (!open) setSetCompanyContextItem(null);
        }}
        fileIds={setCompanySelection.fileIds}
        folderIds={setCompanySelection.folderIds}
        onApplied={() => {
          if (!setCompanyContextItem) clearSelection();
        }}
      />

      <MoveToDialog
        open={moveDialogOpen}
        onOpenChange={(open) => {
          setMoveDialogOpen(open);
          if (!open) setMoveContextItem(null);
        }}
        selection={moveSelection}
        onMove={performMove}
        isMoving={updateMutation.isPending || updateDirectoryMutation.isPending}
      />

      <AttachmentDetailModal
        open={viewModalOpen}
        onOpenChange={(open) => {
          setViewModalOpen(open);
          if (!open) setViewAttachmentId(null);
        }}
        attachmentId={viewAttachmentId}
        neighbourItems={fileItems.map((a) => ({ id: a.id }))}
        onAttachmentChange={setViewAttachmentId}
      />

      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename file</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="rename-input-folder">
              Filename <span className="text-destructive">*</span>
            </Label>
            <Input
              id="rename-input-folder"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder="new-filename.ext"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !updateMutation.isPending) {
                  e.preventDefault();
                  submitRename();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameDialogOpen(false)} disabled={updateMutation.isPending}>
              Cancel
            </Button>
            <Button onClick={submitRename} disabled={updateMutation.isPending || !renameValue.trim()}>
              {updateMutation.isPending ? 'Saving…' : 'Rename'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Folder context-menu: rename */}
      <Dialog open={folderRenameOpen} onOpenChange={setFolderRenameOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename folder</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="folder-rename-input">Name</Label>
            <Input
              id="folder-rename-input"
              value={folderRenameValue}
              onChange={(e) => setFolderRenameValue(e.target.value)}
              placeholder="Folder name"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !updateDirectoryMutation.isPending) {
                  e.preventDefault();
                  submitFolderRename();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setFolderRenameOpen(false)}
              disabled={updateDirectoryMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={submitFolderRename}
              disabled={updateDirectoryMutation.isPending || !folderRenameValue.trim()}
            >
              {updateDirectoryMutation.isPending ? 'Saving…' : 'Rename'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Folder context-menu: new subfolder */}
      <Dialog open={subfolderOpen} onOpenChange={setSubfolderOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New subfolder{subfolderParent ? ` in ${subfolderParent.name}` : ''}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="folder-subfolder-input">Name</Label>
            <Input
              id="folder-subfolder-input"
              value={subfolderValue}
              onChange={(e) => setSubfolderValue(e.target.value)}
              placeholder="Folder name"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !createDirectoryMutation.isPending) {
                  e.preventDefault();
                  submitNewSubfolder();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setSubfolderOpen(false)}
              disabled={createDirectoryMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={submitNewSubfolder}
              disabled={createDirectoryMutation.isPending || !subfolderValue.trim()}
            >
              {createDirectoryMutation.isPending ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Folder context-menu: delete (mirrors the tree sidebar copy) */}
      <Dialog
        open={folderDeleteOpen}
        onOpenChange={(open) => {
          setFolderDeleteOpen(open);
          if (!open) setFolderDeleteTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete folder</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Move &quot;{folderDeleteTarget?.name}&quot; to Trash? This folder and all subfolders will be
            moved to Trash. All attachments in this folder and its subfolders will be archived. You can
            restore them from the Trash page.
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setFolderDeleteOpen(false)}
              disabled={deleteDirectoryMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={submitFolderDelete}
              disabled={deleteDirectoryMutation.isPending}
            >
              Move to Trash
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
