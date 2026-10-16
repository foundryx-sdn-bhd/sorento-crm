/** NUMBER-FIELDS round 3 (R4): the footer never reads "NaN" while a line qty holds bad input. */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/link', () => ({ default: ({ children }: { children: React.ReactNode }) => <a>{children}</a> }));
vi.mock('@/app/(protected)/scm/hooks/useFulfilment', () => ({
  useConsolidatedPackingList: () => ({ data: undefined }),
  useShipmentLinePhotos: () => ({ data: undefined }),
}));
vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProducts: vi.fn(),
}));
vi.mock('./ProductComboboxSearchable', () => ({ ProductComboboxSearchable: () => null }));
vi.mock('./SupplierCombobox', () => ({ SupplierCombobox: () => null }));
vi.mock('./ShipmentLinePhotosCell', () => ({ ShipmentLinePhotosCell: () => null }));

const line = (key: string, qty: string) => ({
  key,
  id: key,
  product_id: `p-${key}`,
  product_code: `CODE-${key}`,
  product_name: 'Name',
  description: '',
  quantity_shipped: qty,
  supplier_id: '',
  cartons_count: '1',
  cbm: '',
  material: '',
  pcs_per_carton: '',
  carton_length_cm: '',
  carton_width_cm: '',
  carton_height_cm: '',
  net_weight_per_carton: '',
  gross_weight_per_carton: '',
  uom_id: null,
  currency: null,
  unit_cost: '',
  remarks: '',
});

vi.mock('../[id]/components/packing-list-context', async (importOriginal) => {
  const original = await importOriginal<typeof import('../[id]/components/packing-list-context')>();
  return {
    ...original,
    usePackingListRecord: () => ({
      numbersChecked: false,
      packingList: { id: 'pl-1', shipment_lines: [] },
      editing: true,
      draftLines: [line('a', 'NaN'), line('b', '5')],
      setLineField: vi.fn(),
      addLine: vi.fn(),
      removeLine: vi.fn(),
      suppliers: [],
      supplierNameById: new Map(),
      sourceInvoices: undefined,
      canReadScm: false,
    }),
  };
});

import { PackingListLinesTab } from './PackingListLinesTab';

describe('PackingListLinesTab footer with bad input (R4)', () => {
  it('the footer never shows NaN while one qty holds bad input', () => {
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <PackingListLinesTab />
      </QueryClientProvider>,
    );
    expect(container.querySelector('tfoot')?.textContent ?? '').toContain('Total');
    expect(container.textContent ?? '').not.toContain('NaN');
  });
});
