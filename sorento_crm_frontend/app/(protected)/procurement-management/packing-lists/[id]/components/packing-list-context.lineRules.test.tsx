import { describe, it, expect, vi } from 'vitest';

vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), custom: vi.fn() } }));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('../../hooks/usePackingLists', () => ({
  usePackingList: vi.fn(),
  usePackingListSourceInvoices: vi.fn(),
  useClearanceCheckpoints: vi.fn(),
  useUpdatePackingList: vi.fn(),
}));
vi.mock('../../../suppliers/hooks/useSupplierSelectQuery', () => ({
  useSupplierSelectQuery: vi.fn(),
}));

import { lineNumberError } from './packing-list-context';

describe('packing list line quantity_shipped (S7)', () => {
  it('is a whole number: 2.5 gets the whole-number sentence', () => {
    const line = { id: 'l-1', quantity_shipped: '2.5' } as never;
    expect(lineNumberError(line, 'quantity_shipped')).toBe('Enter a whole number of at least 0.');
  });
  it('accepts 3', () => {
    const line = { id: 'l-1', quantity_shipped: '3' } as never;
    expect(lineNumberError(line, 'quantity_shipped')).toBeUndefined();
  });
});
