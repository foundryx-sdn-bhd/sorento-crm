'use client';

import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { ToolbarAction } from '@/components/ui/data-grid-list-toolbar';

/**
 * The single "Actions" dropdown for a DataGrid bulk strip - the same pattern the
 * Unified Drive uses (`AttachmentsInFolderPanel` + `buildDriveBulkActions`).
 * Fed as `DataGridListToolbar`'s `bulkActionsSlot`. Renders NOTHING when no
 * action applies to the current selection (so the button disappears entirely,
 * per the "hide when none apply" rule) - the caller's pure builder returns `[]`
 * in that case.
 */
export function BulkActionsMenu({
  actions,
  modal,
  onCloseAutoFocus,
}: {
  actions: ToolbarAction[];
  /** False keeps the list readable (not hidden from assistive tech) while the menu is open or closing. */
  modal?: boolean;
  /** Prevent the default to keep focus where an item just put it (an item that opens a dialog). */
  onCloseAutoFocus?: (event: Event) => void;
}) {
  if (!actions.length) return null;
  return (
    <DropdownMenu modal={modal}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          Actions
          <ChevronDown className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" onCloseAutoFocus={onCloseAutoFocus}>
        {actions.map((action) => {
          const Icon = action.icon;
          return (
            <DropdownMenuItem
              key={action.key}
              disabled={action.disabled}
              onClick={action.onClick}
              className={action.destructive ? 'text-destructive focus:text-destructive' : undefined}
              title={action.disabled ? action.disabledReason : undefined}
            >
              {Icon ? <Icon className="size-4" /> : null}
              {action.label}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
