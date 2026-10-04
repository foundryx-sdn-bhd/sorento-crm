import { useHasPermission } from '@/hooks/usePermissions';

/** View, capture, vote, comment and attach stay on the board permission (the sidebar entry's own). */
export const IDEAS_VIEW_PERMISSION = 'ideation.board.view';

/** Triage actions: status move, edit, merge/unmerge, board reorder, archive, delete, promote. */
export const IDEAS_MANAGE_PERMISSION = 'ideation.ideas.manage';

export function useCanManageIdeas(): boolean {
  return useHasPermission(IDEAS_MANAGE_PERMISSION);
}
