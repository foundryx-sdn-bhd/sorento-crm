'use client';

import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { useDroppable, useDraggable, useDndContext } from '@dnd-kit/core';
import {
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Folder,
  FolderOpen,
  Plus,
  Pencil,
  Trash2,
  MoreHorizontal,
  Shield,
  PanelLeftClose,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollAreaZag } from '@/components/ui/scroll-area-zag';
import { cn } from '@/lib/utils';
import type { AttachmentDirectoryTreeNode } from '../../attachments/services/directoryService';
import {
  useCreateDirectory,
  useUpdateDirectory,
  useDeleteDirectory,
  useDirectoryTree,
} from '../../attachments/hooks/useAttachments';
import { useQuickAccess, useAddQuickAccess, useRemoveQuickAccess } from '@/hooks/useQuickAccess';
import { Star, StarOff } from 'lucide-react';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { findPathIds, folderAfterDelete } from './drivePath';

interface DirectoryTreeSidebarProps {
  selectedId: string | null;
  /** `replace` rewrites the current history entry instead of adding a Back step. */
  onSelect: (id: string | null, opts?: { replace?: boolean }) => void;
  /** When set, folder menu includes adjusting access levels for all attachments in the folder subtree. */
  onAdjustFolderAccessLevels?: (directoryId: string) => void;
  /** When provided, the header shows a button to collapse the whole Folders panel. */
  onCollapsePanel?: () => void;
}

const FOLDER_DROP_PREFIX = 'folder-';
const FOLDER_ALL_ID = 'folder-all';
const FOLDER_BETWEEN_PREFIX = 'folder-between::';

function encodeBetweenId(parentId: string | null, index: number): string {
  return `${FOLDER_BETWEEN_PREFIX}${parentId ?? 'root'}::${index}`;
}

function BetweenDropZone({ parentId, index, depth }: { parentId: string | null; index: number; depth: number }) {
  // Between-zones are for REORDERING folders only. When the active drag is a
  // file/attachment (move-into intent), they must be DISABLED - otherwise the
  // 2px zone (z-10, negative margins) sitting under a collapsed folder's name
  // steals the `over` from the folder row, showing a blue reorder bar and
  // dropping nothing. Disabling lets the folder-row droppable win (highlight +
  // move-into), regardless of collapse state.
  const { active } = useDndContext();
  const reorderActive = active?.data?.current?.type === 'folder';
  const { setNodeRef, isOver } = useDroppable({
    id: encodeBetweenId(parentId, index),
    data: { type: 'folder-between', parentId, index },
    disabled: !reorderActive,
  });
  return (
    <div
      ref={setNodeRef}
      className={cn(
        '-my-1 h-2 rounded transition-colors relative z-10',
        isOver && reorderActive && 'bg-primary'
      )}
      style={{ marginLeft: 8 + depth * 16, marginRight: 8 }}
    />
  );
}

function collectAllFolderIds(nodes: AttachmentDirectoryTreeNode[]): Set<string> {
  const ids = new Set<string>();
  for (const node of nodes) {
    ids.add(node.id);
    if (node.children?.length) {
      collectAllFolderIds(node.children).forEach((id) => ids.add(id));
    }
  }
  return ids;
}

function findNodeById(nodes: AttachmentDirectoryTreeNode[], targetId: string): AttachmentDirectoryTreeNode | null {
  for (const node of nodes) {
    if (node.id === targetId) return node;
    if (node.children?.length) {
      const found = findNodeById(node.children, targetId);
      if (found) return found;
    }
  }
  return null;
}

function collectSubtreeFolderIds(node: AttachmentDirectoryTreeNode): Set<string> {
  const ids = new Set<string>([node.id]);
  if (node.children?.length) {
    node.children.forEach((child) =>
      collectSubtreeFolderIds(child).forEach((id) => ids.add(id))
    );
  }
  return ids;
}

function AllAttachmentsDropTarget({
  isSelected,
  onSelect,
}: {
  isSelected: boolean;
  onSelect: () => void;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: FOLDER_ALL_ID,
    data: { type: 'folder', directoryId: null },
  });
  return (
    <button
      ref={setNodeRef}
      type="button"
      className={cn(
        'w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-left',
        isSelected && 'bg-primary text-primary-foreground font-medium',
        isOver && 'ring-2 ring-primary ring-inset bg-primary/15'
      )}
      onClick={onSelect}
    >
      <Folder className={cn('size-4 shrink-0', isSelected ? 'text-primary-foreground' : 'text-muted-foreground')} />
      All attachments
    </button>
  );
}

function filterTreeBySearch(
  nodes: AttachmentDirectoryTreeNode[],
  query: string
): { filtered: AttachmentDirectoryTreeNode[]; matchedAncestors: Set<string> } {
  const q = query.trim().toLowerCase();
  const matchedAncestors = new Set<string>();
  if (!q) return { filtered: nodes, matchedAncestors };

  const visit = (node: AttachmentDirectoryTreeNode): AttachmentDirectoryTreeNode | null => {
    const selfMatch = node.name.toLowerCase().includes(q);
    const filteredChildren = (node.children ?? [])
      .map(visit)
      .filter((n): n is AttachmentDirectoryTreeNode => n !== null);
    if (selfMatch || filteredChildren.length > 0) {
      if (filteredChildren.length > 0) matchedAncestors.add(node.id);
      return { ...node, children: filteredChildren };
    }
    return null;
  };

  const filtered = nodes.map(visit).filter((n): n is AttachmentDirectoryTreeNode => n !== null);
  return { filtered, matchedAncestors };
}

const ATTACHMENT_DIRECTORIES_PATH = '/resource-management/attachment-directories';

function DirectoryRow({
  node,
  depth,
  index,
  parentId,
  selectedId,
  expandedIds,
  onSelect,
  onCreateSubfolder,
  onRename,
  onDelete,
  onToggleExpand,
  quickAccessEntryIdByDirectoryId,
  onPinToQuickAccess,
  onUnpinFromQuickAccess,
  onAdjustFolderAccessLevels,
}: {
  node: AttachmentDirectoryTreeNode;
  depth: number;
  index: number;
  parentId: string | null;
  selectedId: string | null;
  expandedIds: Set<string>;
  onSelect: (id: string | null) => void;
  onCreateSubfolder: (parentId: string) => void;
  onRename: (id: string, currentName: string) => void;
  onDelete: (id: string, name: string) => void;
  onToggleExpand: (id: string) => void;
  quickAccessEntryIdByDirectoryId: Record<string, string>;
  onPinToQuickAccess: (directoryId: string, label: string) => void;
  onUnpinFromQuickAccess: (entryId: string) => void;
  onAdjustFolderAccessLevels?: (directoryId: string) => void;
}) {
  const isExpanded = expandedIds.has(node.id);
  const hasChildren = node.children && node.children.length > 0;
  const isSelected = selectedId === node.id;

  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `${FOLDER_DROP_PREFIX}${node.id}`,
    data: { type: 'folder', directoryId: node.id },
  });

  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    isDragging,
  } = useDraggable({
    id: `${FOLDER_DROP_PREFIX}${node.id}`,
    data: { type: 'folder', directoryId: node.id, folderName: node.name },
  });

  const rowRef = useRef<HTMLElement | null>(null);
  const setRowRef = useCallback(
    (el: HTMLElement | null) => {
      rowRef.current = el;
      setDropRef(el);
      setDragRef(el);
    },
    [setDropRef, setDragRef]
  );

  // The open folder's row scrolls into view, e.g. after a reload into a folder deep in a
  // long tree; 'nearest' leaves a row that is already visible where it is.
  useEffect(() => {
    if (isSelected) rowRef.current?.scrollIntoView({ block: 'nearest' });
  }, [isSelected]);

  return (
    <div className="flex flex-col">
      <BetweenDropZone parentId={parentId} index={index} depth={depth} />
      <div
        ref={setRowRef}
        {...listeners}
        {...attributes}
        className={cn(
          'group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm min-w-0 touch-none cursor-grab active:cursor-grabbing',
          isSelected && 'bg-primary text-primary-foreground font-medium',
          isOver && 'ring-2 ring-primary ring-inset bg-primary/15',
          isDragging && 'opacity-50'
        )}
        style={{ paddingLeft: 8 + depth * 16 }}
        onClick={() => onSelect(node.id)}
      >
        <button
          type="button"
          className={cn(
            'p-0.5 rounded shrink-0',
            isSelected ? 'hover:bg-primary-foreground/20' : 'hover:bg-muted'
          )}
          onClick={(e) => {
            e.stopPropagation();
            onToggleExpand(node.id);
          }}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label={isExpanded ? 'Collapse' : 'Expand'}
        >
          <ChevronRight
            className={cn(
              'size-4 transition-transform',
              isSelected ? 'text-primary-foreground' : 'text-muted-foreground',
              isExpanded && 'rotate-90'
            )}
          />
        </button>
        {hasChildren && isExpanded ? (
          <FolderOpen className={cn('size-4 shrink-0', isSelected ? 'text-primary-foreground' : 'text-muted-foreground')} />
        ) : (
          <Folder className={cn('size-4 shrink-0', isSelected ? 'text-primary-foreground' : 'text-muted-foreground')} />
        )}
        <span className="whitespace-nowrap" title={node.name}>{node.name}</span>
        <DropdownMenu>
          <DropdownMenuTrigger
            asChild
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <Button
              variant="ghost"
              size="icon"
              className="size-7 shrink-0 opacity-0 group-hover:opacity-100"
              aria-label="Folder actions"
            >
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {quickAccessEntryIdByDirectoryId[node.id] ? (
              <DropdownMenuItem
                onClick={() => onUnpinFromQuickAccess(quickAccessEntryIdByDirectoryId[node.id])}
              >
                <StarOff className="size-4 mr-2" />
                Unpin from Quick Access
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem onClick={() => onPinToQuickAccess(node.id, node.name)}>
                <Star className="size-4 mr-2" />
                Pin to Quick Access
              </DropdownMenuItem>
            )}
            <DropdownMenuItem onClick={() => onCreateSubfolder(node.id)}>
              <Plus className="size-4 mr-2" />
              Add subfolder
            </DropdownMenuItem>
            {onAdjustFolderAccessLevels ? (
              <DropdownMenuItem onClick={() => onAdjustFolderAccessLevels(node.id)}>
                <Shield className="size-4 mr-2" />
                Adjust access levels
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuItem onClick={() => onRename(node.id, node.name)}>
              <Pencil className="size-4 mr-2" />
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              onClick={() => onDelete(node.id, node.name)}
            >
              <Trash2 className="size-4 mr-2" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {hasChildren && isExpanded && (
        <div>
          {node.children!.map((child, idx) => (
            <DirectoryRow
              key={child.id}
              node={child}
              depth={depth + 1}
              index={idx}
              parentId={node.id}
              selectedId={selectedId}
              expandedIds={expandedIds}
              onSelect={onSelect}
              onCreateSubfolder={onCreateSubfolder}
              onRename={onRename}
              onDelete={onDelete}
              onToggleExpand={onToggleExpand}
              quickAccessEntryIdByDirectoryId={quickAccessEntryIdByDirectoryId}
              onPinToQuickAccess={onPinToQuickAccess}
              onUnpinFromQuickAccess={onUnpinFromQuickAccess}
              onAdjustFolderAccessLevels={onAdjustFolderAccessLevels}
            />
          ))}
          <BetweenDropZone parentId={node.id} index={node.children!.length} depth={depth + 1} />
        </div>
      )}
    </div>
  );
}

function buildFolderQuickAccessPath(directoryId: string): string {
  return `${ATTACHMENT_DIRECTORIES_PATH}?directoryId=${directoryId}`;
}

export default function DirectoryTreeSidebar({
  selectedId,
  onSelect,
  onAdjustFolderAccessLevels,
  onCollapsePanel,
}: DirectoryTreeSidebarProps) {
  const { data: tree = [], isLoading } = useDirectoryTree();
  const createMutation = useCreateDirectory();
  const updateMutation = useUpdateDirectory();
  const deleteMutation = useDeleteDirectory();
  const { data: quickAccessList = [] } = useQuickAccess();
  const addQuickAccessMutation = useAddQuickAccess();
  const removeQuickAccessMutation = useRemoveQuickAccess();

  const quickAccessEntryIdByDirectoryId = useMemo(() => {
    const map: Record<string, string> = {};
    for (const item of quickAccessList) {
      const match = item.path.match(/\?directoryId=([^&]+)/);
      if (match) map[match[1]] = item.id;
    }
    return map;
  }, [quickAccessList]);

  const onPinToQuickAccess = useCallback(
    (directoryId: string, label: string) => {
      addQuickAccessMutation.mutate({
        path: buildFolderQuickAccessPath(directoryId),
        label,
      });
    },
    [addQuickAccessMutation]
  );

  const onUnpinFromQuickAccess = useCallback(
    (entryId: string) => {
      removeQuickAccessMutation.mutate(entryId);
    },
    [removeQuickAccessMutation]
  );

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [addParentId, setAddParentId] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState('');

  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameName, setRenameName] = useState('');

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);

  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
  } = useDebouncedSearch();
  const { filtered: filteredTree, matchedAncestors } = useMemo(
    () => filterTreeBySearch(tree, searchQuery),
    [tree, searchQuery]
  );

  const pathToSelected = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const path = findPathIds(tree, selectedId);
    if (!path) return new Set<string>();
    const ids = new Set<string>();
    for (let i = 0; i < path.length - 1; i++) ids.add(path[i]);
    return ids;
  }, [tree, selectedId]);

  const allFolderIds = useMemo(() => collectAllFolderIds(tree), [tree]);

  const subtreeFolderIds = useMemo(() => {
    if (!selectedId) return null;
    const node = findNodeById(tree, selectedId);
    return node ? collectSubtreeFolderIds(node) : null;
  }, [tree, selectedId]);

  const [expandedIdsState, setExpandedIdsState] = useState<Set<string>>(new Set());
  const expandedIds = useMemo(() => {
    const merged = new Set(expandedIdsState);
    pathToSelected.forEach((id) => merged.add(id));
    if (searchQuery.trim()) matchedAncestors.forEach((id) => merged.add(id));
    return merged;
  }, [expandedIdsState, pathToSelected, matchedAncestors, searchQuery]);

  const handleExpandAll = useCallback(() => {
    const idsToExpand = subtreeFolderIds ?? allFolderIds;
    setExpandedIdsState((prev) => {
      const next = new Set(prev);
      idsToExpand.forEach((id) => next.add(id));
      return next;
    });
  }, [subtreeFolderIds, allFolderIds]);

  const handleCollapseAll = useCallback(() => {
    const idsToCollapse = subtreeFolderIds ?? allFolderIds;
    setExpandedIdsState((prev) => {
      const next = new Set(prev);
      idsToCollapse.forEach((id) => next.delete(id));
      return next;
    });
  }, [subtreeFolderIds, allFolderIds]);

  const handleToggleExpand = useCallback((id: string) => {
    setExpandedIdsState((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const openAddDialog = (parentId: string | null) => {
    setAddParentId(parentId);
    setNewFolderName('');
    setAddDialogOpen(true);
  };

  const openRenameDialog = (id: string, currentName: string) => {
    setRenameId(id);
    setRenameName(currentName);
    setRenameDialogOpen(true);
  };

  const openDeleteDialog = (id: string, name: string) => {
    setDeleteTarget({ id, name });
    setDeleteDialogOpen(true);
  };

  const handleCreate = () => {
    if (!newFolderName.trim()) return;
    createMutation.mutate(
      { name: newFolderName.trim(), parentId: addParentId ?? undefined },
      { onSuccess: () => setAddDialogOpen(false) }
    );
  };

  const handleRename = () => {
    if (!renameId || !renameName.trim()) return;
    updateMutation.mutate(
      { id: renameId, data: { name: renameName.trim() } },
      { onSuccess: () => setRenameDialogOpen(false) }
    );
  };

  const handleDelete = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        setDeleteDialogOpen(false);
        setDeleteTarget(null);
        // Deleting the open folder (or an ancestor of it) moves up to the deleted folder's
        // parent and REPLACES the history entry, so Back never lands on the deleted folder.
        const next = folderAfterDelete(tree, selectedId, deleteTarget.id);
        if (next !== undefined) onSelect(next, { replace: true });
      },
    });
  };

  return (
    <>
      <div className="flex h-full w-full min-w-0 flex-col border-r bg-muted/30">
        <div className="flex items-center justify-between gap-1 p-2 border-b">
          <span className="text-sm font-medium truncate">Folders</span>
          <div className="flex items-center gap-0.5 shrink-0">
            <Button
              variant="ghost"
              size="sm"
              className="size-8 p-0"
              title={selectedId ? 'Expand all in selected folder' : 'Expand all'}
              onClick={handleExpandAll}
            >
              <ChevronDown className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="size-8 p-0"
              title={selectedId ? 'Collapse all in selected folder' : 'Collapse all'}
              onClick={handleCollapseAll}
            >
              <ChevronUp className="size-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => openAddDialog(null)}
              data-guide-target="resource-management.files.add-folder-button"
            >
              <Plus className="size-4 mr-1" />
              Add
            </Button>
            {onCollapsePanel && (
              <Button
                variant="ghost"
                size="sm"
                className="size-8 p-0"
                title="Collapse folders panel"
                aria-label="Collapse folders panel"
                onClick={onCollapsePanel}
              >
                <PanelLeftClose className="size-4" />
              </Button>
            )}
          </div>
        </div>
        <div className="p-2 border-b">
          <div className="relative">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder="Search folders..."
              className="w-full"
            />
          </div>
        </div>
        <ScrollAreaZag className="flex-1">
          <div className="min-w-max p-1 py-2">
            {!searchQuery && (
              <AllAttachmentsDropTarget
                isSelected={selectedId === null}
                onSelect={() => onSelect(null)}
              />
            )}
            {isLoading ? (
              <SectionSkeleton rows={5} className="px-2 py-2" />
            ) : filteredTree.length === 0 && searchQuery ? (
              <div className="px-2 py-2 text-sm text-muted-foreground">No folders match &quot;{searchQuery}&quot;</div>
            ) : (
              <>
                {filteredTree.map((node, idx) => (
                  <DirectoryRow
                    key={node.id}
                    node={node}
                    depth={0}
                    index={idx}
                    parentId={null}
                    selectedId={selectedId}
                    expandedIds={expandedIds}
                    onSelect={onSelect}
                    onCreateSubfolder={(parentId) => {
                      setAddParentId(parentId);
                      setNewFolderName('');
                      setAddDialogOpen(true);
                    }}
                    onRename={openRenameDialog}
                    onDelete={openDeleteDialog}
                    onToggleExpand={handleToggleExpand}
                    quickAccessEntryIdByDirectoryId={quickAccessEntryIdByDirectoryId}
                    onPinToQuickAccess={onPinToQuickAccess}
                    onUnpinFromQuickAccess={onUnpinFromQuickAccess}
                    onAdjustFolderAccessLevels={onAdjustFolderAccessLevels}
                  />
                ))}
                {!searchQuery && filteredTree.length > 0 && (
                  <BetweenDropZone parentId={null} index={filteredTree.length} depth={0} />
                )}
              </>
            )}
          </div>
        </ScrollAreaZag>
      </div>

      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{addParentId ? 'New subfolder' : 'New folder'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="new-folder-name">Name</Label>
            <Input
              id="new-folder-name"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              placeholder="Folder name"
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={!newFolderName.trim() || createMutation.isPending}>
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename folder</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="rename-folder-name">Name</Label>
            <Input
              id="rename-folder-name"
              value={renameName}
              onChange={(e) => setRenameName(e.target.value)}
              placeholder="Folder name"
              onKeyDown={(e) => e.key === 'Enter' && handleRename()}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleRename} disabled={!renameName.trim() || updateMutation.isPending}>
              Rename folder
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteDialogOpen} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete folder</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Move &quot;{deleteTarget?.name}&quot; to Trash? This folder and all subfolders will be moved to Trash.
            All attachments in this folder and its subfolders will be archived. You can restore them from the Trash page.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleteMutation.isPending}>
              Move to Trash
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
