import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const createMutate = vi.hoisted(() => vi.fn());
vi.mock('../../hooks/useModelPrices', () => ({
  useCreateModelPrice: () => ({ mutate: createMutate, isPending: false, error: null, reset: vi.fn() }),
  useUpdateModelPrice: () => ({ mutate: vi.fn(), isPending: false, error: null, reset: vi.fn() }),
}));

import ModelPriceModal from './ModelPriceModal';

afterEach(() => {
  cleanup();
  createMutate.mockReset();
});

function renderAdd() {
  return render(<ModelPriceModal open onOpenChange={() => {}} price={null} lockedModel="zzt-model" />);
}

describe('ModelPriceModal number fields', () => {
  it('keeps Save live with a blank price; the click names the field and sends nothing', () => {
    renderAdd();
    fireEvent.change(screen.getByLabelText(/^Output/), { target: { value: '2.5' } });
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(screen.getByText('Enter a number of at least 0.')).toBeInTheDocument();
    expect(createMutate).not.toHaveBeenCalled();
  });

  it('sends the typed prices once they are valid, and a blank cached price as null', () => {
    renderAdd();
    fireEvent.change(screen.getByLabelText(/^Input/), { target: { value: '0.3' } });
    fireEvent.change(screen.getByLabelText(/^Output/), { target: { value: '2.5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(createMutate).toHaveBeenCalledTimes(1);
    expect(createMutate.mock.calls[0][0]).toMatchObject({
      input_usd_per_1m: 0.3,
      cached_input_usd_per_1m: null,
      output_usd_per_1m: 2.5,
    });
  });
});
