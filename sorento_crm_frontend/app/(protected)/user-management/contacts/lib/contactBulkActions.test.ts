import { describe, expect, it, vi } from 'vitest';
import { buildContactBulkActions, type ContactBulkActionHandlers } from './contactBulkActions';

const handlers = (): ContactBulkActionHandlers => ({
  onExport: vi.fn(),
  onLinkCustomers: vi.fn(),
  onEnableMessaging: vi.fn(),
  onDisableMessaging: vi.fn(),
  onSync: vi.fn(),
  onCopyAccess: vi.fn(),
  onDelete: vi.fn(),
});

const base = { selectedCount: 3, canEdit: true, outboundBusy: false, syncBusy: false };

describe('buildContactBulkActions', () => {
  it('puts every bulk action in one menu, destructive items last and red', () => {
    const actions = buildContactBulkActions(base, handlers());
    expect(actions.map((a) => a.label)).toEqual([
      'Export',
      'Link customers (3)',
      'Enable messaging (3)',
      'Sync from Respond (3)',
      'Copy access from contact (3)',
      'Disable messaging (3)',
      'Delete (3)',
    ]);
    expect(actions.filter((a) => a.destructive).map((a) => a.key)).toEqual(['outbound-disable', 'delete']);
  });

  it('wires each item to its own handler', () => {
    const h = handlers();
    const byKey = Object.fromEntries(buildContactBulkActions(base, h).map((a) => [a.key, a]));
    byKey['copy-access'].onClick?.();
    byKey['delete'].onClick?.();
    byKey['outbound-disable'].onClick?.();
    byKey['export'].onClick?.();
    expect(h.onCopyAccess).toHaveBeenCalledOnce();
    expect(h.onDelete).toHaveBeenCalledOnce();
    expect(h.onDisableMessaging).toHaveBeenCalledOnce();
    expect(h.onExport).toHaveBeenCalledOnce();
    expect(h.onEnableMessaging).not.toHaveBeenCalled();
  });

  it('hides the edit-only items without contacts.edit', () => {
    const keys = buildContactBulkActions({ ...base, canEdit: false }, handlers()).map((a) => a.key);
    expect(keys).not.toContain('link-customers');
    expect(keys).not.toContain('copy-access');
    expect(keys).toContain('delete');
  });

  it('disables messaging and sync items while their request runs', () => {
    const actions = buildContactBulkActions({ ...base, outboundBusy: true, syncBusy: true }, handlers());
    const disabled = actions.filter((a) => a.disabled).map((a) => a.key);
    expect(disabled).toEqual(['outbound-enable', 'sync', 'outbound-disable']);
  });

  it('returns nothing with nothing selected, so the Actions button hides', () => {
    expect(buildContactBulkActions({ ...base, selectedCount: 0 }, handlers())).toEqual([]);
  });
});
