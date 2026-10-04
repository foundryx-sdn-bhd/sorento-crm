/**
 * Editing a packing list must not cost the container its factories.
 *
 * One container is routinely loaded by two or three of them, and the attribution lives on
 * the LINE - the header names nobody once the container is mixed. The form has no per-line
 * supplier picker on purpose (the attribution comes from the upload), so the only thing that
 * keeps it is the round trip through this form: read it on hydrate, send it back on save.
 * Drop it and one hand edit of a mixed container hands every line to whoever the header says.
 *
 * SearchableSelect is mocked to a native <select>: the real one is a Radix popover + cmdk
 * list, which is not deterministic under jsdom.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

// Static AND async: the supplier picker is server-searched, so it hands over `fetchOptions`
// and a `selectedOption` rather than a full `options` list.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    selectedOption,
    placeholder,
  }: {
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    selectedOption?: { value: string; label: string };
    placeholder?: string;
  }) => {
    const list = options ?? (selectedOption ? [selectedOption] : []);
    return (
      <select
        aria-label={placeholder ?? 'select'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{placeholder ?? ''}</option>
        {list.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  },
}));

const state = {
  packingList: null as unknown,
  update: vi.fn(),
  create: vi.fn(),
};

vi.mock('../hooks/usePackingLists', () => ({
  usePackingList: () => ({ data: state.packingList, isLoading: false }),
  useCreatePackingList: () => ({ mutateAsync: state.create, isPending: false }),
  useUpdatePackingList: () => ({ mutateAsync: state.update, isPending: false }),
  useClearanceCheckpoints: () => ({ data: [], isLoading: false }),
}));

vi.mock('../../suppliers/hooks/useSupplierSelectQuery', () => ({
  useSupplierSelectQuery: () => ({
    data: [
      { id: 'sup-a', supplier_code: '400-K029', supplier_name: 'KAILU HARDWARE FACTORY' },
      { id: 'sup-b', supplier_code: '400-C011', supplier_name: 'CAIZHOU SANITARY' },
    ],
  }),
}));

vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProducts: () =>
    Promise.resolve({
      data: [
        { id: 'p-1', product_code: 'SRTWT7443', product_name: 'Basin Mixer Tall' },
        { id: 'p-2', product_code: 'MCHWT1200', product_name: 'Shower Set' },
      ],
    }),
}));

import PackingListForm from './PackingListForm';

/** A mixed container: two factories on the lines, and no supplier on the header. */
function mixedContainer() {
  return {
    id: 'pl-1',
    shipment_number: 'SPO-0042',
    supplier_id: null,
    supplier: undefined,
    shipment_date: '2026-07-30',
    shipping_container_number: 'FSCU8103365',
    shipment_status: 'in_transit',
    shipment_lines: [
      {
        id: 'l-1',
        product_id: 'p-1',
        quantity_shipped: 490,
        supplier_id: 'sup-a',
        product: { id: 'p-1', product_code: 'SRTWT7443', product_name: 'Basin Mixer Tall' },
      },
      {
        id: 'l-2',
        product_id: 'p-2',
        quantity_shipped: 900,
        supplier_id: 'sup-b',
        product: { id: 'p-2', product_code: 'MCHWT1200', product_name: 'Shower Set' },
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  state.update = vi.fn().mockResolvedValue({});
  state.create = vi.fn().mockResolvedValue({});
  state.packingList = mixedContainer();
});

describe('PackingListForm - a save keeps the lines with their factory', () => {
  it('sends each line back with the supplier it arrived with', async () => {
    render(<PackingListForm packingListId="pl-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Update' }));

    await waitFor(() => expect(state.update).toHaveBeenCalled());
    const payload = state.update.mock.calls[0][0].data;
    expect(payload.shipment_lines).toEqual([
      { id: 'l-1', product_id: 'p-1', quantity_shipped: 490, supplier_id: 'sup-a' },
      { id: 'l-2', product_id: 'p-2', quantity_shipped: 900, supplier_id: 'sup-b' },
    ]);
  });

  it('leaves a line the upload never attributed unattributed', async () => {
    // Unset, never the header's supplier - the backend falls back for a line that
    // genuinely has none, and guessing here would invent an owner.
    const list = mixedContainer();
    list.shipment_lines[1].supplier_id = null as unknown as string;
    state.packingList = list;
    render(<PackingListForm packingListId="pl-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Update' }));

    await waitFor(() => expect(state.update).toHaveBeenCalled());
    const payload = state.update.mock.calls[0][0].data;
    expect(payload.shipment_lines[1]).toEqual({
      id: 'l-2',
      product_id: 'p-2',
      quantity_shipped: 900,
    });
  });
});

describe('PackingListForm - regions (review round 1)', () => {
  it('sends the record\'s regions back on Update', async () => {
    state.packingList = { ...mixedContainer(), regions: ['east'] };
    render(<PackingListForm packingListId="pl-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Update' }));

    await waitFor(() => expect(state.update).toHaveBeenCalled());
    expect(state.update.mock.calls[0][0].data.regions).toEqual(['east']);
  });

  it('defaults a record with no regions to West only on Update', async () => {
    render(<PackingListForm packingListId="pl-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Update' }));

    await waitFor(() => expect(state.update).toHaveBeenCalled());
    expect(state.update.mock.calls[0][0].data.regions).toEqual(['west']);
  });
});
