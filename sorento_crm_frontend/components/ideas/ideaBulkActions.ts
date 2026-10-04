import {
  Archive,
  ArchiveRestore,
  ArrowRight,
  FileText,
  GitMerge,
  Split,
  Trash2,
} from 'lucide-react';
import type { ToolbarAction } from '@/components/ui/data-grid-list-toolbar';
import type { RecordAction } from '@/components/common/recordActions';
import { advanceTransition, restoreTransition } from '@/lib/ideaTransitions';
import type { Idea } from '@/types/ideas';

/**
 * The Ideas list's actions and their visibility rules, mirrored from ss
 * `use-ideas-list-config.tsx` (ss main). One builder feeds the bulk "Actions" menu and a row's "..."
 * menu, so the two cannot drift. The caller only asks for it when the viewer may manage ideas.
 */
export interface IdeaActionHandlers {
  promote: (rows: Idea[]) => void;
  merge: (rows: Idea[]) => void;
  unmerge: (rows: Idea[]) => void;
  advance: (rows: Idea[]) => void;
  restore: (rows: Idea[]) => void;
  archive: (rows: Idea[]) => void;
  remove: (rows: Idea[]) => void;
}

/** "Move to {label}" when every row's advance target agrees, else the generic verb. */
export function advanceLabel(rows: Idea[]): string {
  const labels = new Set(rows.map((r) => advanceTransition(r)?.toStatusLabel));
  const only = [...labels][0];
  return labels.size === 1 && only
    ? `Move to ${only}`
    : 'Advance to next stage';
}

const mixedProducts = (rows: Idea[]) =>
  new Set(rows.map((r) => r.productName)).size > 1;
const MIXED = 'These ideas belong to different products.';

export function buildIdeaActions(
  rows: Idea[],
  on: IdeaActionHandlers,
): ToolbarAction[] {
  if (rows.length === 0) return [];
  const none = (pred: (r: Idea) => boolean) => rows.every((r) => !pred(r));
  const allArchived = rows.every((r) => r.statusIsArchived);
  const noneArchived = none((r) => r.statusIsArchived);
  const actions: ToolbarAction[] = [];

  if (noneArchived) {
    actions.push({
      key: 'promote',
      label: 'Promote to BR',
      icon: FileText,
      disabled: mixedProducts(rows),
      disabledReason: MIXED,
      onClick: () => on.promote(rows),
    });
  }
  if (rows.length >= 2 && noneArchived) {
    actions.push({
      key: 'merge',
      label: 'Merge',
      icon: GitMerge,
      disabled: mixedProducts(rows),
      disabledReason: MIXED,
      onClick: () => on.merge(rows),
    });
  }
  if (rows.every((r) => r.mergedCount > 0)) {
    actions.push({
      key: 'unmerge',
      label: 'Unmerge',
      icon: Split,
      onClick: () => on.unmerge(rows),
    });
  }
  if (noneArchived) {
    actions.push({
      key: 'advance',
      label: advanceLabel(rows),
      icon: ArrowRight,
      disabled: rows.some((r) => !advanceTransition(r)),
      disabledReason: 'Some of these ideas have no next stage.',
      onClick: () => on.advance(rows),
    });
    actions.push({
      key: 'archive',
      label: 'Archive',
      icon: Archive,
      onClick: () => on.archive(rows),
    });
  }
  // ss also flags closed, duplicate and rejected as archived: with no outgoing transition there is
  // nothing to restore to, so Restore is not offered at all (AC-K-05).
  if (allArchived && rows.every((r) => restoreTransition(r))) {
    actions.push({
      key: 'restore',
      label: 'Restore',
      icon: ArchiveRestore,
      onClick: () => on.restore(rows),
    });
  }
  actions.push({
    key: 'delete',
    label: 'Delete',
    icon: Trash2,
    destructive: true,
    onClick: () => on.remove(rows),
  });
  return actions;
}

/** The same actions for one row's "..." menu (`RecordAction`s: secondary first, Delete last). */
export function buildIdeaRowActions(
  idea: Idea,
  on: IdeaActionHandlers,
): RecordAction[] {
  return buildIdeaActions([idea], on)
    .filter((a) => a.key !== 'merge')
    .map((a) => ({
      key: `idea.${a.key}`,
      label: a.label,
      icon: a.icon,
      kind: a.destructive ? ('destructive' as const) : ('secondary' as const),
      disabled: a.disabled,
      disabledReason: a.disabled ? a.disabledReason : undefined,
      run: () => a.onClick?.(),
    }));
}
