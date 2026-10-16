/**
 * CONTACT-TAGS S4b (owner mock order): the one Actions menu lists Add tag (n), Remove tag (n),
 * Apply tag settings (n) after Copy access and BEFORE the destructive items. Assumed:
 * buildContactBulkActions' handlers gain onAddTag / onRemoveTag / onApplyTagSettings and the builder
 * emits them (edit-only) in that position, so contactTagBulkActions is no longer appended after it.
 */
import { describe, expect, it, vi } from 'vitest';
import { buildContactBulkActions } from './contactBulkActions';

const handlers = () =>
  ({
    onExport: vi.fn(), onLinkCustomers: vi.fn(), onEnableMessaging: vi.fn(), onDisableMessaging: vi.fn(),
    onSync: vi.fn(), onCopyAccess: vi.fn(), onDelete: vi.fn(),
    onAddTag: vi.fn(), onRemoveTag: vi.fn(), onApplyTagSettings: vi.fn(),
  }) as never;

const base = { selectedCount: 3, canEdit: true, outboundBusy: false, syncBusy: false };

describe('buildContactBulkActions tag items', () => {
  it('puts the tag items after Copy access and before the destructive ones', () => {
    const labels = buildContactBulkActions(base, handlers()).map((a) => a.label);
    expect(labels).toEqual([
      'Export',
      'Link customers (3)',
      'Enable messaging (3)',
      'Sync from Respond (3)',
      'Copy access from contact (3)',
      'Add tag (3)',
      'Remove tag (3)',
      'Apply tag settings (3)',
      'Disable messaging (3)',
      'Delete (3)',
    ]);
  });

  it('wires the tag items and hides them without edit', () => {
    const h = handlers() as unknown as Record<string, ReturnType<typeof vi.fn>>;
    const byKey = Object.fromEntries(buildContactBulkActions(base, h as never).map((a) => [a.key, a]));
    byKey['tag-add'].onClick?.();
    byKey['tag-remove'].onClick?.();
    byKey['tag-apply'].onClick?.();
    expect(h.onAddTag).toHaveBeenCalledOnce();
    expect(h.onRemoveTag).toHaveBeenCalledOnce();
    expect(h.onApplyTagSettings).toHaveBeenCalledOnce();
    const keys = buildContactBulkActions({ ...base, canEdit: false }, handlers()).map((a) => a.key);
    expect(keys.filter((k) => k.startsWith('tag-'))).toEqual([]);
  });
});
