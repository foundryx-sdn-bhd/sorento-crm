import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';

const DASH = '-';
const EDIT = 'system.ai_assistant_settings.edit';

const useModelPricesQuery = vi.fn();
const useHasPermission = vi.fn();
const run = vi.fn();

vi.mock('../../hooks/useModelPrices', () => ({
  useModelPricesQuery: () => useModelPricesQuery(),
  useCreateModelPrice: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateModelPrice: () => ({ mutate: vi.fn(), isPending: false }),
  useModelPriceDeletion: () => ({ run, isRowPending: () => false, targetId: null }),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => useHasPermission(slug),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import ModelPricesList from './ModelPricesList';

const ROWS = [
  {
    id: 'p-1',
    model: 'gpt-5.4-mini',
    input_usd_per_1m: 0.75,
    cached_input_usd_per_1m: 0.075,
    output_usd_per_1m: 4.5,
    note: 'owner-confirmed',
    updated_at: '2026-10-13T14:23:33',
  },
  {
    id: 'p-2',
    model: 'zzt-nocache',
    input_usd_per_1m: 2,
    cached_input_usd_per_1m: null,
    output_usd_per_1m: 8,
    note: null,
    updated_at: '2026-10-13T14:23:33',
  },
];

beforeEach(() => {
  useModelPricesQuery.mockReset().mockReturnValue({ data: ROWS, isLoading: false, isError: false });
  useHasPermission.mockReset().mockImplementation((slug: string) => slug === EDIT);
  run.mockReset();
});
afterEach(() => cleanup());

describe('ModelPricesList (MP-2)', () => {
  it('renders the model, its prices (trailing zeros trimmed) and a dash for no cached price', () => {
    render(<ModelPricesList />);
    const priced = screen.getByText('gpt-5.4-mini').closest('tr') as HTMLElement;
    expect(within(priced).getByText('0.75')).toBeInTheDocument();
    expect(within(priced).getByText('0.075')).toBeInTheDocument();
    expect(within(priced).getByText('4.5')).toBeInTheDocument();
    const nocache = screen.getByText('zzt-nocache').closest('tr') as HTMLElement;
    expect(within(nocache).getByText(DASH)).toBeInTheDocument();
  });

  it('renders updated in Malaysia time', () => {
    render(<ModelPricesList />);
    expect(screen.getAllByText('13/10/2026, 10:23 pm').length).toBeGreaterThan(0);
  });

  it('shows the load error text, never a spinner forever (MP-9)', () => {
    useModelPricesQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('Prices exploded'),
    });
    render(<ModelPricesList />);
    expect(screen.getByText(/Prices exploded/)).toBeInTheDocument();
  });
});

describe('ModelPricesList write gate (MP-7)', () => {
  it('with the edit grant shows Add price, Edit and Delete', () => {
    render(<ModelPricesList />);
    expect(screen.getByRole('button', { name: /add price/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /edit/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /delete/i }).length).toBeGreaterThan(0);
  });

  it('without the edit grant shows no Add price, Edit or Delete', () => {
    useHasPermission.mockReturnValue(false);
    render(<ModelPricesList />);
    expect(screen.getByText('gpt-5.4-mini')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add price/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
  });

  it('Edit opens the modal with the model locked (MP-4)', () => {
    render(<ModelPricesList />);
    fireEvent.click(screen.getAllByRole('button', { name: /edit/i })[0]);
    const field = screen.getByLabelText('Model') as HTMLInputElement;
    expect(field.value).toBe('gpt-5.4-mini');
    expect(field.disabled || field.readOnly).toBe(true);
  });
});
