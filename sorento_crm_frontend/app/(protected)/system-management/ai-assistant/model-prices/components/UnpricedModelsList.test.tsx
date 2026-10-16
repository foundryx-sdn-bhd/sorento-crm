import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';

const EDIT = 'system.ai_assistant_settings.edit';

const useUnpricedModelsQuery = vi.fn();
const useHasPermission = vi.fn();

vi.mock('../../hooks/useModelPrices', () => ({
  useUnpricedModelsQuery: () => useUnpricedModelsQuery(),
  useCreateModelPrice: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateModelPrice: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => useHasPermission(slug),
}));

import UnpricedModelsList from './UnpricedModelsList';

const ROWS = [
  { model: 'gemini-3.5-flash', calls: 12, last_seen_at: '2026-10-13T14:23:33' },
  { model: 'zzt-other', calls: 1, last_seen_at: '2026-10-12T01:00:00' },
];

beforeEach(() => {
  useUnpricedModelsQuery.mockReset().mockReturnValue({ data: ROWS, isLoading: false, isError: false });
  useHasPermission.mockReset().mockImplementation((slug: string) => slug === EDIT);
});
afterEach(() => cleanup());

describe('UnpricedModelsList (MP-6)', () => {
  it('renders each model with its calls and last seen in Malaysia time', () => {
    render(<UnpricedModelsList />);
    const row = screen.getByText('gemini-3.5-flash').closest('tr') as HTMLElement;
    expect(within(row).getByText('12')).toBeInTheDocument();
    expect(within(row).getByText('13/10/2026, 10:23 pm')).toBeInTheDocument();
  });

  it('Add price opens the modal with the model filled and locked', () => {
    render(<UnpricedModelsList />);
    const row = screen.getByText('gemini-3.5-flash').closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: /add price/i }));
    const field = screen.getByLabelText('Model') as HTMLInputElement;
    expect(field.value).toBe('gemini-3.5-flash');
    expect(field.disabled || field.readOnly).toBe(true);
  });

  it('shows the load error text, never a spinner forever (MP-9)', () => {
    useUnpricedModelsQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('Unpriced exploded'),
    });
    render(<UnpricedModelsList />);
    expect(screen.getByText(/Unpriced exploded/)).toBeInTheDocument();
  });

  it('without the edit grant shows no Add price button (MP-7)', () => {
    useHasPermission.mockReturnValue(false);
    render(<UnpricedModelsList />);
    expect(screen.getByText('gemini-3.5-flash')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add price/i })).not.toBeInTheDocument();
  });
});
