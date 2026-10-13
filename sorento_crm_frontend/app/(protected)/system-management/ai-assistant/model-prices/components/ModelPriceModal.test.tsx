import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

vi.mock('../../hooks/useModelPrices', () => ({
  useCreateModelPrice: () => ({ mutate: vi.fn(), isPending: false, error: null, reset: vi.fn() }),
  useUpdateModelPrice: () => ({ mutate: vi.fn(), isPending: false, error: null, reset: vi.fn() }),
}));

import ModelPriceModal from './ModelPriceModal';

afterEach(() => cleanup());

function renderEdit(price: Record<string, unknown>) {
  return render(
    <ModelPriceModal
      open
      onOpenChange={() => {}}
      price={{ id: 'p-1', model: 'zzt-model', note: null, updated_at: '2026-10-13T14:23:33', ...price } as never}
    />,
  );
}

const values = () => ({
  input: (screen.getByLabelText(/^Input/) as HTMLInputElement).value,
  cached: (screen.getByLabelText(/^Cached input/) as HTMLInputElement).value,
  output: (screen.getByLabelText(/^Output/) as HTMLInputElement).value,
});

describe('ModelPriceModal prefill (MP-4)', () => {
  it('prefills clean prices', () => {
    renderEdit({ input_usd_per_1m: 0.3, cached_input_usd_per_1m: 0.03, output_usd_per_1m: 2.5 });
    expect(values()).toEqual({ input: '0.3', cached: '0.03', output: '2.5' });
  });

  it('prefills the same trimmed format as the list, never float noise', () => {
    // What the browser pass saw come back from the API for a price saved as 0.30 / 0.03.
    renderEdit({
      input_usd_per_1m: 0.30000001192092896,
      cached_input_usd_per_1m: 0.029999999329447743,
      output_usd_per_1m: 2.5,
    });
    expect(values()).toEqual({ input: '0.3', cached: '0.03', output: '2.5' });
  });
});
