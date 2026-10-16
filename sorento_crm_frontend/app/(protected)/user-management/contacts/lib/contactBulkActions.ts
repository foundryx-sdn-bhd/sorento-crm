import {
  Copy,
  Download,
  Link2,
  MessageCircle,
  MessageCircleOff,
  RefreshCw,
  Tag,
  Tags,
  Trash2,
  X,
} from 'lucide-react';
import type { ToolbarAction } from '@/components/ui/data-grid-list-toolbar';

/**
 * The items of the contacts list's bulk "Actions" dropdown (CONTACT-ACTIONS-COPY, owner 16 Oct
 * 2026: "put these all actions under 'Actions' dropdown just like other list view"). Rendered by
 * the shared `BulkActionsMenu` (`app/(protected)/scm/components/BulkActionsMenu.tsx`). Pure, so
 * the order, gating and red items are unit-tested.
 *
 * Link customers and Copy access need `user_management.contacts.edit` (`canEdit`). Disable
 * messaging and Delete are destructive (red) and still open their own dialogs.
 */
export interface ContactBulkActionState {
  selectedCount: number;
  canEdit: boolean;
  outboundBusy: boolean;
  syncBusy: boolean;
}

export interface ContactBulkActionHandlers {
  onExport: () => void;
  onLinkCustomers: () => void;
  onEnableMessaging: () => void;
  onDisableMessaging: () => void;
  onSync: () => void;
  onCopyAccess: () => void;
  onAddTag: () => void;
  onRemoveTag: () => void;
  onApplyTagSettings: () => void;
  onDelete: () => void;
}

export function buildContactBulkActions(
  state: ContactBulkActionState,
  handlers: ContactBulkActionHandlers,
): ToolbarAction[] {
  const n = state.selectedCount;
  if (n === 0) return [];
  return [
    { key: 'export', label: 'Export', icon: Download, onClick: handlers.onExport },
    ...(state.canEdit
      ? [{ key: 'link-customers', label: `Link customers (${n})`, icon: Link2, onClick: handlers.onLinkCustomers }]
      : []),
    {
      key: 'outbound-enable',
      label: `Enable messaging (${n})`,
      icon: MessageCircle,
      disabled: state.outboundBusy,
      onClick: handlers.onEnableMessaging,
    },
    {
      key: 'sync',
      label: `Sync from Respond (${n})`,
      icon: RefreshCw,
      disabled: state.syncBusy,
      onClick: handlers.onSync,
    },
    ...(state.canEdit
      ? [{ key: 'copy-access', label: `Copy access from contact (${n})`, icon: Copy, onClick: handlers.onCopyAccess }]
      : []),
    // CONTACT-TAGS S4b: after Copy access, before the destructive items (edit only).
    ...(state.canEdit
      ? [
          { key: 'tag-add', label: `Add tag (${n})`, icon: Tag, onClick: handlers.onAddTag },
          { key: 'tag-remove', label: `Remove tag (${n})`, icon: X, onClick: handlers.onRemoveTag },
          {
            key: 'tag-apply',
            label: `Apply tag settings (${n})`,
            icon: Tags,
            onClick: handlers.onApplyTagSettings,
          },
        ]
      : []),
    {
      key: 'outbound-disable',
      label: `Disable messaging (${n})`,
      icon: MessageCircleOff,
      destructive: true,
      disabled: state.outboundBusy,
      onClick: handlers.onDisableMessaging,
    },
    { key: 'delete', label: `Delete (${n})`, icon: Trash2, destructive: true, onClick: handlers.onDelete },
  ];
}
