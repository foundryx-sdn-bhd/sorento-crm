/**
 * NUMBER-FIELDS (S3 SNAP screen): GRN line "Qty Received" was rewritten to 0 by
 * `parseInt(...) || 0` the moment it was cleared. After: it stays empty, and saving with it
 * empty sends nothing and the field says why.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  });
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

const updateMutateAsync = vi.fn().mockResolvedValue({});
const createMutateAsync = vi.fn().mockResolvedValue({});
const useGRNMock = vi.fn();
vi.mock('../hooks/useGRN', () => ({
  useCreateGRN: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useUpdateGRN: () => ({ mutateAsync: updateMutateAsync, isPending: false }),
  useGRN: (id: string | null) => useGRNMock(id),
}));

vi.mock('@/app/(protected)/inventory-management/warehouses/services/warehouseService', () => ({
  getWarehouses: vi.fn(async () => ({ data: [] })),
}));
vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProducts: vi.fn(async () => ({ data: [] })),
}));
vi.mock(
  '@/app/(protected)/procurement-management/packing-lists/components/ProductComboboxSearchable',
  () => ({ ProductComboboxSearchable: () => <div data-testid="product-stub" /> }),
);
vi.mock(
  '@/app/(protected)/procurement-management/spo-allocations/components/WarehouseCombobox',
  () => ({ WarehouseCombobox: () => <div data-testid="warehouse-stub" /> }),
);
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: () => <div data-testid="select-stub" />,
}));

import GRNForm from './GRNForm';

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  useGRNMock.mockReturnValue({
    isLoading: false,
    data: {
      id: 'grn-1',
      picking_number: 'GRN-0001',
      spo_number: '',
      picking_date: '2026-10-01',
      picking_status: 'draft',
      notes: '',
      picking_lines: [
        {
          id: 'pl-1',
          product_id: 'prod-1',
          quantity_expected: 10,
          quantity_picked: 10,
          source_warehouse_id: 'wh-1',
        },
      ],
    },
  });
});

describe('GRNForm - Qty Received blank', () => {
  it('stays empty after being cleared (not 0)', () => {
    render(<GRNForm grnId="grn-1" />);
    const input = screen.getByLabelText('Qty Received') as HTMLInputElement;
    expect(input.value).toBe('10');
    fireEvent.change(input, { target: { value: '' } });
    expect(input.value).toBe('');
  });

  it('Update with it empty sends nothing and shows the message', async () => {
    render(<GRNForm grnId="grn-1" />);
    const input = screen.getByLabelText('Qty Received') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update' }));

    expect(await screen.findByText(/^Enter a (whole )?number/)).toBeInTheDocument();
    expect(updateMutateAsync).not.toHaveBeenCalled();
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });
});
