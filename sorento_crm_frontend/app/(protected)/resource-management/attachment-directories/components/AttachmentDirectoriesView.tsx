'use client';

import { useId, useState, useMemo, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  DndContext,
  DragOverlay,
  MouseSensor,
  TouchSensor,
  pointerWithin,
  useSensor,
  useSensors,
  useDndMonitor,
  type DragEndEvent,
} from '@dnd-kit/core';
import { toast } from '@/lib/toast';
import { FileText, Folder, PanelLeftOpen } from 'lucide-react';
import type { ImperativePanelHandle } from 'react-resizable-panels';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useIsMobile } from '@/hooks/use-mobile';
import {
  useBulkMoveAttachments,
  useDirectoryTree,
  useMoveDirectory,
  useUpdateAttachment,
} from '../../attachments/hooks/useAttachments';
import type { AttachmentDirectoryTreeNode } from '../../attachments/services/directoryService';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import DirectoryTreeSidebar from './DirectoryTreeSidebar';
import AttachmentsInFolderPanel from './AttachmentsInFolderPanel';
import { useFolderUrl, type OpenFolder } from './useFolderUrl';
import BulkAttachmentAccessLevelsDialog, {
  type BulkAccessLevelsScope,
} from './BulkAttachmentAccessLevelsDialog';

export const DND_ID_ATTACHMENT_PREFIX = 'attachment-';
export const DND_ID_FOLDER_PREFIX = 'folder-';
export const DND_ID_FOLDER_ALL = 'folder-all';
export const DND_ID_FOLDER_BETWEEN_PREFIX = 'folder-between::';
// Grid (right-pane) folder rows/cards use a DISTINCT prefix from the tree's
// `folder-` ids. A folder visible in BOTH the tree and the right pane would
// otherwise register two dnd-kit droppables/draggables with the same id - the
// registry keeps only one, silently breaking drag-into for whichever folder is
// shown in both places (the bug: top-of-list folders failed, lower ones worked).
export const DND_ID_GRID_FOLDER_PREFIX = 'gridfolder-';
// Breadcrumb crumbs are also folder drop targets; same reason they need a prefix
// distinct from the tree's `folder-` (a path ancestor is in both the breadcrumb
// and the expanded tree).
export const DND_ID_CRUMB_FOLDER_PREFIX = 'crumbfolder-';
// Root ("All attachments") drop sentinel: the tree uses `folder-all`, the
// breadcrumb home crumb uses `crumbfolder-all` (distinct so they don't collide),
// both resolve to the root (directory_id = null).
export const DND_ID_CRUMB_FOLDER_ALL = 'crumbfolder-all';
export function isRootDrop(dndId: string): boolean {
  return dndId === DND_ID_FOLDER_ALL || dndId === DND_ID_CRUMB_FOLDER_ALL;
}

// Every folder droppable/draggable id, by surface (longest/most-specific first
// so the bare `folder-` check can't shadow the others).
const FOLDER_ID_PREFIXES = [
  DND_ID_GRID_FOLDER_PREFIX,
  DND_ID_CRUMB_FOLDER_PREFIX,
  DND_ID_FOLDER_PREFIX,
];

// True for any folder drop/drag id across tree / grid / breadcrumb (NOT the
// between-zone or `folder-all` root sentinel - callers handle those).
export function isFolderId(dndId: string): boolean {
  if (dndId.startsWith(DND_ID_FOLDER_BETWEEN_PREFIX)) return false;
  return FOLDER_ID_PREFIXES.some((p) => dndId.startsWith(p));
}

// Resolve any folder dnd id (tree/grid/breadcrumb) to its directory id. Callers
// handle `folder-all` (= root, null) before calling.
export function extractFolderId(dndId: string): string {
  for (const p of FOLDER_ID_PREFIXES) {
    if (dndId.startsWith(p)) return dndId.slice(p.length);
  }
  return dndId;
}

function parseBetweenId(id: string): { parentId: string | null; index: number } | null {
  if (!id.startsWith(DND_ID_FOLDER_BETWEEN_PREFIX)) return null;
  const rest = id.slice(DND_ID_FOLDER_BETWEEN_PREFIX.length);
  const sep = rest.lastIndexOf('::');
  if (sep === -1) return null;
  const parent = rest.slice(0, sep);
  const idx = Number(rest.slice(sep + 2));
  if (!Number.isFinite(idx)) return null;
  return { parentId: parent === 'root' ? null : parent, index: idx };
}

function findFolderNameById(nodes: AttachmentDirectoryTreeNode[], id: string): string | null {
  for (const node of nodes) {
    if (node.id === id) return node.name;
    const found = findFolderNameById(node.children || [], id);
    if (found != null) return found;
  }
  return null;
}

function collectDescendantFolderIds(
  nodes: AttachmentDirectoryTreeNode[],
  folderId: string
): Set<string> {
  const ids = new Set<string>();
  function addSubtree(node: AttachmentDirectoryTreeNode) {
    if (node.children?.length) {
      node.children.forEach((c) => {
        ids.add(c.id);
        addSubtree(c);
      });
    }
  }
  function findAndCollect(nodes: AttachmentDirectoryTreeNode[]): boolean {
    for (const node of nodes) {
      if (node.id === folderId) {
        addSubtree(node);
        return true;
      }
      if (node.children?.length && findAndCollect(node.children)) return true;
    }
    return false;
  }
  findAndCollect(nodes);
  return ids;
}

function AttachmentDirectoriesContent({
  selectedId,
  setSelectedId,
  selectedFolderName,
  onOpenAccessLevels,
}: {
  selectedId: string | null;
  setSelectedId: OpenFolder;
  selectedFolderName: string | null;
  onOpenAccessLevels: (scope: BulkAccessLevelsScope) => void;
}) {
  const [activeDrag, setActiveDrag] = useState<{
    id: string;
    name: string;
    type: 'attachment' | 'folder';
  } | null>(null);
  const sidebarPanelRef = useRef<ImperativePanelHandle>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const isMobile = useIsMobile();
  const [treeDrawerOpen, setTreeDrawerOpen] = useState(false);

  useDndMonitor({
    onDragStart: ({ active }) => {
      const id = String(active.id);
      const data = active.data?.current as { attachmentName?: string; folderName?: string } | undefined;
      if (id.startsWith(DND_ID_ATTACHMENT_PREFIX)) {
        setActiveDrag({ id, name: data?.attachmentName ?? 'Attachment', type: 'attachment' });
      } else if (isFolderId(id) && !isRootDrop(id)) {
        setActiveDrag({ id, name: data?.folderName ?? 'Folder', type: 'folder' });
      }
    },
    onDragEnd: () => setActiveDrag(null),
    onDragCancel: () => setActiveDrag(null),
  });

  if (isMobile) {
    // Mobile (UAC H1-H4): full-width right pane; the left tree lives in a Sheet
    // drawer toggled from the panel; drag is disabled (Move via dialog).
    return (
      <>
        <div className="relative h-full min-h-0 rounded-lg border bg-card">
          <div className="h-full overflow-auto p-2">
            <AttachmentsInFolderPanel
              directoryId={selectedId}
              directoryName={selectedFolderName}
              onSelectFolder={setSelectedId}
              onBulkAdjustAccessLevels={(ids) => onOpenAccessLevels({ attachmentIds: ids })}
              onToggleTreeDrawer={() => setTreeDrawerOpen(true)}
            />
          </div>
        </div>
        <Sheet open={treeDrawerOpen} onOpenChange={setTreeDrawerOpen}>
          <SheetContent side="left" className="w-[85vw] max-w-xs p-0">
            <SheetHeader className="sr-only">
              <SheetTitle>Folders</SheetTitle>
            </SheetHeader>
            <DirectoryTreeSidebar
              selectedId={selectedId}
              onSelect={(id, opts) => {
                setSelectedId(id, opts);
                setTreeDrawerOpen(false);
              }}
              onAdjustFolderAccessLevels={(id) => onOpenAccessLevels({ directoryId: id })}
            />
          </SheetContent>
        </Sheet>
        <DragOverlay dropAnimation={null}>{null}</DragOverlay>
      </>
    );
  }

  return (
    <>
      <div className="relative flex-1 min-h-0">
        {sidebarCollapsed && (
          <Button
            variant="outline"
            size="sm"
            className="absolute left-2 top-2 z-20 gap-1 shadow-sm"
            onClick={() => sidebarPanelRef.current?.expand()}
            title="Show folders panel"
          >
            <PanelLeftOpen className="size-4" />
            Folders
          </Button>
        )}
        <ResizablePanelGroup
        direction="horizontal"
        className="h-full rounded-lg border bg-card"
      >
        <ResizablePanel
          ref={sidebarPanelRef}
          collapsible
          collapsedSize={0}
          defaultSize={20}
          minSize={15}
          maxSize={45}
          className="min-w-0"
          onCollapse={() => setSidebarCollapsed(true)}
          onExpand={() => setSidebarCollapsed(false)}
        >
          <DirectoryTreeSidebar
            selectedId={selectedId}
            onSelect={setSelectedId}
            onAdjustFolderAccessLevels={(id) => onOpenAccessLevels({ directoryId: id })}
            onCollapsePanel={() => sidebarPanelRef.current?.collapse()}
          />
        </ResizablePanel>
        <ResizableHandle withHandle className="bg-border" />
        <ResizablePanel defaultSize={80} minSize={50} className="min-w-0">
          <div className="h-full overflow-auto p-4">
            <AttachmentsInFolderPanel
              directoryId={selectedId}
              directoryName={selectedFolderName}
              onSelectFolder={setSelectedId}
              onBulkAdjustAccessLevels={(ids) => onOpenAccessLevels({ attachmentIds: ids })}
            />
          </div>
        </ResizablePanel>
        </ResizablePanelGroup>
      </div>
      <DragOverlay dropAnimation={null}>
        {activeDrag ? (
          <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 shadow-md">
            {activeDrag.type === 'folder' ? (
              <Folder className="size-4 text-muted-foreground shrink-0" />
            ) : (
              <FileText className="size-4 text-muted-foreground shrink-0" />
            )}
            <span className="text-sm truncate max-w-[200px]">{activeDrag.name}</span>
          </div>
        ) : null}
      </DragOverlay>
    </>
  );
}

export default function AttachmentDirectoriesView() {
  const queryClient = useQueryClient();
  const dndId = useId();
  // The open folder is the URL's `?directoryId=`, so Back / Forward / reload / a pasted
  // link all land on it (FILES-URL-NAV).
  const { folderId: selectedId, openFolder: setSelectedId } = useFolderUrl();
  const [accessLevelsScope, setAccessLevelsScope] = useState<BulkAccessLevelsScope>(null);
  const { data: tree = [] } = useDirectoryTree();
  const updateAttachmentMutation = useUpdateAttachment();
  const bulkMoveMutation = useBulkMoveAttachments();
  const moveDirectoryMutation = useMoveDirectory();
  const selectedFolderName = useMemo(
    () => (selectedId ? findFolderNameById(tree, selectedId) : null),
    [selectedId, tree]
  );

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } })
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const activeId = String(active.id);
    const overId = String(over.id);

    const isBetween = overId.startsWith(DND_ID_FOLDER_BETWEEN_PREFIX);
    const isOverFolder = overId === DND_ID_FOLDER_ALL || isFolderId(overId);

    const isFolderDrag = activeId !== DND_ID_FOLDER_ALL && isFolderId(activeId);

    // Folder drag: reorder between siblings
    if (isFolderDrag && isBetween) {
      const parsed = parseBetweenId(overId);
      if (!parsed) return;
      const folderId = extractFolderId(activeId);
      if (folderId === parsed.parentId) return;
      const descendants = collectDescendantFolderIds(tree, folderId);
      if (parsed.parentId && descendants.has(parsed.parentId)) return;

      moveDirectoryMutation.mutate(
        { id: folderId, parent_id: parsed.parentId, position: parsed.index },
        {
          onSuccess: () => {
            const targetLabel =
              parsed.parentId == null
                ? 'top level'
                : findFolderNameById(tree, parsed.parentId) ?? 'folder';
            toast.success(`Folder reordered under ${targetLabel}`);
          },
        }
      );
      return;
    }

    // Folder drag: nest into another folder (reparent, append at end)
    if (isFolderDrag && isOverFolder) {
      const folderId = extractFolderId(activeId);
      const targetParentId: string | null =
        isRootDrop(overId) ? null : extractFolderId(overId);

      if (folderId === targetParentId) return; // can't drop on self
      const descendants = collectDescendantFolderIds(tree, folderId);
      if (targetParentId && descendants.has(targetParentId)) return; // can't drop onto own descendant

      moveDirectoryMutation.mutate(
        { id: folderId, parent_id: targetParentId, position: null },
        {
          onSuccess: () => {
            const targetLabel =
              targetParentId == null
                ? 'All attachments'
                : findFolderNameById(tree, targetParentId) ?? 'folder';
            toast.success(`Folder moved to ${targetLabel}`);
          },
        }
      );
      return;
    }

    // Attachment drag: move file to folder
    if (!activeId.startsWith(DND_ID_ATTACHMENT_PREFIX)) return;
    if (!isOverFolder) return;

    const targetDirectoryId: string | null =
      isRootDrop(overId) ? null : extractFolderId(overId);
    const attachmentId = activeId.slice(DND_ID_ATTACHMENT_PREFIX.length);
    const dragData = active.data?.current as
      | { currentDirectoryId?: string | null; attachmentIds?: string[] }
      | undefined;
    const currentDirectoryId = dragData?.currentDirectoryId ?? null;
    if (
      currentDirectoryId === targetDirectoryId ||
      (currentDirectoryId == null && targetDirectoryId == null)
    ) {
      return;
    }
    const batchIds = dragData?.attachmentIds && dragData.attachmentIds.length > 1
      ? dragData.attachmentIds
      : [attachmentId];
    const folderLabel =
      targetDirectoryId == null
        ? 'All attachments'
        : findFolderNameById(tree, targetDirectoryId) ?? 'folder';
    if (batchIds.length > 1) {
      bulkMoveMutation.mutate(
        { attachmentIds: batchIds, directoryId: targetDirectoryId },
        {
          onSuccess: ({ updated }) => {
            toast.success(`Moved ${updated} file(s) to ${folderLabel}`);
          },
        }
      );
    } else {
      updateAttachmentMutation.mutate(
        { attachmentId, data: { directory_id: targetDirectoryId } },
        {
          onSuccess: () => {
            toast.success(`Moved to ${folderLabel}`);
          },
        }
      );
    }
  };

  return (
    <DndContext
      id={dndId}
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragEnd={handleDragEnd}
    >
      <AttachmentDirectoriesContent
        selectedId={selectedId}
        setSelectedId={setSelectedId}
        selectedFolderName={selectedFolderName}
        onOpenAccessLevels={setAccessLevelsScope}
      />
      <BulkAttachmentAccessLevelsDialog
        open={accessLevelsScope !== null}
        onOpenChange={(open) => {
          if (!open) setAccessLevelsScope(null);
        }}
        scope={accessLevelsScope}
        onSuccess={() => queryClient.invalidateQueries({ queryKey: ['attachments'] })}
      />
    </DndContext>
  );
}
