import type { AttachmentDirectoryTreeNode } from '../../attachments/services/directoryService';

export interface BreadcrumbCrumb {
  /** null = drive root. */
  id: string | null;
  name: string;
}

/**
 * Build the breadcrumb trail (root → … → folder) for `folderId` from the tree.
 * Always begins with the synthetic root crumb. Returns just the root when
 * `folderId` is null or not found.
 */
export function buildBreadcrumb(
  tree: AttachmentDirectoryTreeNode[],
  folderId: string | null,
  rootLabel = 'All files'
): BreadcrumbCrumb[] {
  const root: BreadcrumbCrumb = { id: null, name: rootLabel };
  if (!folderId) return [root];

  const trail: BreadcrumbCrumb[] = [];
  const walk = (nodes: AttachmentDirectoryTreeNode[], path: BreadcrumbCrumb[]): boolean => {
    for (const node of nodes) {
      const next = [...path, { id: node.id, name: node.name }];
      if (node.id === folderId) {
        trail.push(...next);
        return true;
      }
      if (node.children?.length && walk(node.children, next)) return true;
    }
    return false;
  };
  walk(tree, []);
  return [root, ...trail];
}

export interface DirectoryOption {
  value: string;
  label: string;
}

/**
 * Every directory in the tree, flattened, each labelled with its full path
 * (`Documents / Packing Lists`) so a folder several levels deep is not confused with a
 * same-named one elsewhere. Feeds a plain `SearchableSelect` - the attachment type's
 * `Default folder` field (R4) and the generic upload dialog's folder picker both need a
 * flat list, not the tree widget `MoveToDialog` uses to move files around.
 */
export function flattenDirectoryOptions(
  tree: AttachmentDirectoryTreeNode[]
): DirectoryOption[] {
  const out: DirectoryOption[] = [];
  const walk = (nodes: AttachmentDirectoryTreeNode[], prefix: string) => {
    for (const node of nodes) {
      const label = prefix ? `${prefix} / ${node.name}` : node.name;
      out.push({ value: node.id, label });
      if (node.children?.length) walk(node.children, label);
    }
  };
  walk(tree, '');
  return out;
}

/** Folder ids from the top level down to `folderId` (inclusive), or null if not in the tree. */
export function findPathIds(
  nodes: AttachmentDirectoryTreeNode[],
  folderId: string,
  path: string[] = []
): string[] | null {
  for (const node of nodes) {
    const next = [...path, node.id];
    if (node.id === folderId) return next;
    if (node.children?.length) {
      const found = findPathIds(node.children, folderId, next);
      if (found) return found;
    }
  }
  return null;
}

/**
 * True when the URL names a folder the loaded tree does not hold: deleted, in Trash,
 * outside the viewer's companies, or a mistyped link. The drive listing returns an empty
 * 200 for such an id, so without this the page would show a silent empty folder. False
 * while the tree is still loading, so a valid deep link never flashes the error.
 */
export function isFolderUnavailable(
  tree: AttachmentDirectoryTreeNode[],
  treeReady: boolean,
  folderId: string | null
): boolean {
  if (!folderId || !treeReady) return false;
  return findPathIds(tree, folderId) === null;
}

/**
 * Where to go after `deletedId` is deleted while `currentId` is open: the deleted folder's
 * parent (null = drive root) when it is the open folder or one of its ancestors, otherwise
 * `undefined` (stay put).
 */
export function folderAfterDelete(
  tree: AttachmentDirectoryTreeNode[],
  currentId: string | null,
  deletedId: string
): string | null | undefined {
  if (!currentId) return undefined;
  const path = findPathIds(tree, currentId);
  if (!path) return undefined;
  const idx = path.indexOf(deletedId);
  if (idx === -1) return undefined;
  return idx === 0 ? null : path[idx - 1];
}
