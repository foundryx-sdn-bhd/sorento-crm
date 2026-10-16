/**
 * CONTACT-TAGS S3, UAC T5.3: the bulk Actions item "Apply tag settings (n)" for a user with
 * contacts.edit. Assumed: contactTagBulkActions({ count, canEdit, onAdd, onRemove, onApply }).
 */
import { describe, it, expect, vi } from 'vitest';
import { contactTagBulkActions } from './contactTagBulkActions';

const base = { count: 3, onAdd: vi.fn(), onRemove: vi.fn(), onApply: vi.fn() };

describe('contactTagBulkActions', () => {
  it('lists Apply tag settings (n) for an editor and calls onApply', () => {
    const actions = contactTagBulkActions({ ...base, canEdit: true } as never);
    const apply = actions.find((a: { label: string }) => a.label === 'Apply tag settings (3)');
    expect(apply).toBeDefined();
    apply!.onClick();
    expect(base.onApply).toHaveBeenCalled();
  });

  it('lists nothing without edit', () => {
    expect(contactTagBulkActions({ ...base, canEdit: false } as never)).toEqual([]);
  });
});
