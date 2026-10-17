/**
 * buildBreadcrumb - the Drive breadcrumb trail (UAC A2).
 */
import { describe, it, expect } from 'vitest';
import { buildBreadcrumb, folderAfterDelete, isFolderUnavailable } from './drivePath';
import type { AttachmentDirectoryTreeNode } from '../../attachments/services/directoryService';

const tree: AttachmentDirectoryTreeNode[] = [
  {
    id: 'mkt',
    name: 'Marketing',
    parent_id: null,
    sort_order: 0,
    created_at: '',
    children: [
      {
        id: 'camp',
        name: 'Campaigns',
        parent_id: 'mkt',
        sort_order: 0,
        created_at: '',
        children: [
          { id: 'q1', name: 'Q1', parent_id: 'camp', sort_order: 0, created_at: '', children: [] },
        ],
      },
    ],
  },
];

describe('buildBreadcrumb', () => {
  it('returns only the root crumb when folderId is null', () => {
    expect(buildBreadcrumb(tree, null)).toEqual([{ id: null, name: 'All files' }]);
  });

  it('builds root -> ... -> current for a nested folder', () => {
    expect(buildBreadcrumb(tree, 'q1')).toEqual([
      { id: null, name: 'All files' },
      { id: 'mkt', name: 'Marketing' },
      { id: 'camp', name: 'Campaigns' },
      { id: 'q1', name: 'Q1' },
    ]);
  });

  it('honours a custom root label', () => {
    expect(buildBreadcrumb(tree, 'mkt', 'Home')[0]).toEqual({ id: null, name: 'Home' });
  });

  it('falls back to just root when the folder is not in the tree', () => {
    expect(buildBreadcrumb(tree, 'missing')).toEqual([{ id: null, name: 'All files' }]);
  });
});

describe('isFolderUnavailable (FILES-URL-NAV AC-6)', () => {
  it('is false at the drive root', () => {
    expect(isFolderUnavailable(tree, true, null)).toBe(false);
  });

  it('is false while the tree is still loading, so a valid deep link never flashes the error', () => {
    expect(isFolderUnavailable([], false, 'q1')).toBe(false);
  });

  it('is false for a folder in the tree, at any depth', () => {
    expect(isFolderUnavailable(tree, true, 'mkt')).toBe(false);
    expect(isFolderUnavailable(tree, true, 'q1')).toBe(false);
  });

  it('is true for a folder id the loaded tree does not hold (deleted, no access, typo)', () => {
    expect(isFolderUnavailable(tree, true, 'gone')).toBe(true);
  });
});

describe('folderAfterDelete (FILES-URL-NAV AC-8)', () => {
  it('returns undefined when the deleted folder is not the current one or an ancestor of it', () => {
    expect(folderAfterDelete(tree, 'mkt', 'q1')).toBeUndefined();
    expect(folderAfterDelete(tree, null, 'q1')).toBeUndefined();
  });

  it('returns the parent when the current folder itself is deleted', () => {
    expect(folderAfterDelete(tree, 'q1', 'q1')).toBe('camp');
  });

  it('returns the deleted folder\'s parent when an ancestor of the current folder is deleted', () => {
    expect(folderAfterDelete(tree, 'q1', 'camp')).toBe('mkt');
  });

  it('returns null (drive root) when a top-level folder on the path is deleted', () => {
    expect(folderAfterDelete(tree, 'q1', 'mkt')).toBeNull();
  });
});
