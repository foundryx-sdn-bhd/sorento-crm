/**
 * buildDriveBulkActions - the single "Action" dropdown gating (UAC F2/F3/F6).
 */
import { describe, it, expect, vi } from 'vitest';
import { buildDriveBulkActions, type DriveBulkActionHandlers } from './driveBulkActions';

const handlers: DriveBulkActionHandlers = {
  onMove: vi.fn(),
  onExport: vi.fn(),
  onSetAccessLevels: vi.fn(),
  onSetAttachmentType: vi.fn(),
  onSetCompany: vi.fn(),
  onResubmit: vi.fn(),
  onDownload: vi.fn(),
  onDelete: vi.fn(),
  onRestore: vi.fn(),
};

function keys(state: Parameters<typeof buildDriveBulkActions>[0]) {
  return buildDriveBulkActions(state, handlers).map((a) => a.key);
}

describe('buildDriveBulkActions', () => {
  it('returns nothing with an empty selection', () => {
    expect(
      keys({
        selectionCount: 0,
        selectionHasFolder: false,
        isTrashView: false,
        isResubmitting: false,
        canRestore: false,
      })
    ).toEqual([]);
  });

  it('F2: a file-only selection exposes ALL bulk actions in one menu', () => {
    expect(
      keys({
        selectionCount: 2,
        selectionHasFolder: false,
        isTrashView: false,
        isResubmitting: false,
        canRestore: false,
      })
    ).toEqual([
      'bulk-move',
      'bulk-download',
      'bulk-export',
      'bulk-access-levels',
      'bulk-attachment-type',
      'bulk-company',
      'bulk-resubmit',
      'bulk-delete',
    ]);
  });

  it('F3: when the selection contains a folder, file-only actions are disabled but shared ones stay enabled', () => {
    const actions = buildDriveBulkActions(
      {
        selectionCount: 2,
        selectionHasFolder: true,
        isTrashView: false,
        isResubmitting: false,
        canRestore: false,
      },
      handlers
    );
    const byKey = Object.fromEntries(actions.map((a) => [a.key, a]));
    // shared
    expect(byKey['bulk-move'].disabled).toBeFalsy();
    expect(byKey['bulk-delete'].disabled).toBeFalsy();
    // file-only -> disabled with a reason
    expect(byKey['bulk-export'].disabled).toBe(true);
    expect(byKey['bulk-access-levels'].disabled).toBe(true);
    expect(byKey['bulk-attachment-type'].disabled).toBe(true);
    expect(byKey['bulk-resubmit'].disabled).toBe(true);
    expect(byKey['bulk-export'].disabledReason).toMatch(/folder/i);
    expect(byKey['bulk-access-levels'].disabledReason).toMatch(/folder/i);
  });

  it('F6: Rename and Replace are never in the bulk menu', () => {
    const all = keys({
      selectionCount: 3,
      selectionHasFolder: false,
      isTrashView: false,
      isResubmitting: false,
      canRestore: false,
    });
    expect(all).not.toContain('bulk-rename');
    expect(all).not.toContain('bulk-replace');
  });

  it('does NOT include a Download-as-ZIP action (feature dropped)', () => {
    const all = keys({
      selectionCount: 2,
      selectionHasFolder: false,
      isTrashView: false,
      isResubmitting: false,
      canRestore: false,
    });
    expect(all).not.toContain('bulk-zip');
  });

  it('AC-F1: bulk-company is enabled for files, for folders and for a mix; absent in trash', () => {
    const filesOnly = buildDriveBulkActions(
      { selectionCount: 2, selectionHasFolder: false, isTrashView: false, isResubmitting: false, canRestore: false },
      handlers,
    );
    const foldersOnly = buildDriveBulkActions(
      { selectionCount: 2, selectionHasFolder: true, isTrashView: false, isResubmitting: false, canRestore: false },
      handlers,
    );
    // A mixed selection is represented the same way as folders-only: at least
    // one folder is present, so `selectionHasFolder` is the only signal the
    // state carries either way.
    const mixed = buildDriveBulkActions(
      { selectionCount: 3, selectionHasFolder: true, isTrashView: false, isResubmitting: false, canRestore: false },
      handlers,
    );
    for (const actions of [filesOnly, foldersOnly, mixed]) {
      const setCompany = actions.find((a) => a.key === 'bulk-company');
      expect(setCompany).toBeDefined();
      expect(setCompany?.disabled).toBeFalsy();
    }
    const trash = buildDriveBulkActions(
      { selectionCount: 1, selectionHasFolder: false, isTrashView: true, isResubmitting: false, canRestore: true },
      handlers,
    );
    expect(trash.some((a) => a.key === 'bulk-company')).toBe(false);
  });

  it('Trash exposes only Restore + Delete', () => {
    expect(
      keys({
        selectionCount: 1,
        selectionHasFolder: false,
        isTrashView: true,
        isResubmitting: false,
        canRestore: true,
      })
    ).toEqual(['bulk-restore', 'bulk-permanent-delete']);
  });

  describe('AC-A1..A3: Download selected', () => {
    const base = { isTrashView: false, isResubmitting: false, canRestore: false };

    it('A1: bulk-download is enabled for file-only, folder-only and mixed selections, right after bulk-move', () => {
      const states = [
        { selectionCount: 2, selectionHasFolder: false },
        { selectionCount: 2, selectionHasFolder: true },
        { selectionCount: 3, selectionHasFolder: true },
      ];
      for (const s of states) {
        const actions = buildDriveBulkActions({ ...base, ...s }, handlers);
        const idx = actions.findIndex((a) => a.key === 'bulk-download');
        expect(idx).toBeGreaterThan(-1);
        expect(actions[idx].label).toBe('Download selected');
        expect(actions[idx].disabled).toBeFalsy();
        expect(actions[idx - 1].key).toBe('bulk-move');
        actions[idx].onClick?.();
      }
      expect(handlers.onDownload).toHaveBeenCalledTimes(3);
    });

    it('A2: Export selected stays present and disabled when a folder is selected', () => {
      const actions = buildDriveBulkActions(
        { ...base, selectionCount: 2, selectionHasFolder: true },
        handlers,
      );
      const exp = actions.find((a) => a.key === 'bulk-export');
      expect(exp?.disabled).toBe(true);
      expect(exp?.disabledReason).toMatch(/folder/i);
      expect(actions.find((a) => a.key === 'bulk-download')?.disabled).toBeFalsy();
    });

    it('A3: the trash view has no Download', () => {
      const all = keys({
        selectionCount: 1,
        selectionHasFolder: false,
        isTrashView: true,
        isResubmitting: false,
        canRestore: true,
      });
      expect(all).toEqual(['bulk-restore', 'bulk-permanent-delete']);
    });
  });
});
