import type { ToolbarAction } from '@/components/ui/data-grid-list-toolbar';

/**
 * Builds the SINGLE "Action" dropdown's items for the Unified Drive bulk strip
 * (UAC F2/F3/F6). Pure so the selection-gating is unit-testable without the
 * DataGrid stack.
 *
 * Gating:
 * - Mixed folder+file selections are allowed (F1).
 * - Shared actions (Move, Download, Delete) stay enabled on any selection.
 * - Download is the file-bundle ZIP (folders + files, background job); Export stays the xlsx
 *    rows export.
 * - File-only actions (Export selected, Set access levels, Set attachment type,
 *    Resubmit) are DISABLED when the selection contains a folder (F3) - folders
 *    have no such attributes.
 * - Trash exposes Restore + Delete only.
 * - Single-only actions (Rename, Replace) are not in the bulk menu at all (F6) - 
 *    they live on the per-row menu.
 *
 * Export here is the SELECTED-rows xlsx export (reuses the toolbar's export
 * dialog via `onExport`); the toolbar's standalone Export button only shows in
 * the no-selection (list-level) state.
 */
export interface DriveBulkActionHandlers {
  onMove: () => void;
  onDownload: () => void;
  onExport: () => void;
  onSetAccessLevels: () => void;
  onSetAttachmentType: () => void;
  onSetCompany: () => void;
  onResubmit: () => void;
  onDelete: () => void;
  onRestore: () => void;
}

export interface DriveBulkActionState {
  selectionCount: number;
  selectionHasFolder: boolean;
  isTrashView: boolean;
  isResubmitting: boolean;
  canRestore: boolean;
}

const FILE_ONLY_DISABLED_REASON = 'Not available when a folder is selected.';

export function buildDriveBulkActions(
  state: DriveBulkActionState,
  handlers: DriveBulkActionHandlers
): ToolbarAction[] {
  if (state.selectionCount === 0) return [];

  if (state.isTrashView) {
    return [
      {
        key: 'bulk-restore',
        label: 'Restore selected',
        disabled: !state.canRestore,
        onClick: handlers.onRestore,
      },
      {
        key: 'bulk-permanent-delete',
        label: 'Delete selected',
        destructive: true,
        onClick: handlers.onDelete,
      },
    ];
  }

  const fileOnlyReason = state.selectionHasFolder ? FILE_ONLY_DISABLED_REASON : undefined;
  return [
    { key: 'bulk-move', label: 'Move to…', onClick: handlers.onMove },
    { key: 'bulk-download', label: 'Download selected', onClick: handlers.onDownload },
    {
      key: 'bulk-export',
      label: 'Export selected',
      disabled: state.selectionHasFolder,
      disabledReason: fileOnlyReason,
      onClick: handlers.onExport,
    },
    {
      key: 'bulk-access-levels',
      label: 'Set access levels',
      disabled: state.selectionHasFolder,
      disabledReason: fileOnlyReason,
      onClick: handlers.onSetAccessLevels,
    },
    {
      key: 'bulk-attachment-type',
      label: 'Set attachment type',
      disabled: state.selectionHasFolder,
      disabledReason: fileOnlyReason,
      onClick: handlers.onSetAttachmentType,
    },
    // Company is a folder AND a file attribute (R17), so - unlike the file-only
    // actions above it - this stays enabled on any mix of the two (R4).
    {
      key: 'bulk-company',
      label: 'Set company',
      onClick: handlers.onSetCompany,
    },
    {
      key: 'bulk-resubmit',
      label: 'Resubmit selected',
      disabled: state.selectionHasFolder || state.isResubmitting,
      disabledReason: fileOnlyReason,
      onClick: handlers.onResubmit,
    },
    {
      key: 'bulk-delete',
      label: 'Delete selected',
      destructive: true,
      onClick: handlers.onDelete,
    },
  ];
}
