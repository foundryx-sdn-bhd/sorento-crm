/**
 * Container volumes are quoted to three decimals in this trade.
 *
 * The loadable volume of a container is a commercial fact somebody types in, and the
 * difference between 68.125 and 68.13 is a real one when it decides whether a last pallet
 * fits. A panel that silently rounds what was entered makes the stored value and the
 * displayed value disagree, and the reader trusts the one on screen.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getContainerSizes = vi.fn();
const createContainerSize = vi.fn();

vi.mock('../../services/fulfilmentService', () => ({
  getContainerSizes: (...a: unknown[]) => getContainerSizes(...a),
  createContainerSize: (...a: unknown[]) => createContainerSize(...a),
  updateContainerSize: vi.fn(),
  deleteContainerSize: vi.fn(),
}));

import { ContainerSizesPanel } from './ContainerSizesPanel';

function size(over: Record<string, unknown> = {}) {
  return {
    id: 'cs-1',
    code: '40HQ',
    label: '40ft high cube',
    cbm: 68.125,
    is_default: true,
    ...over,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContainerSizesPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getContainerSizes.mockReset();
});

describe('ContainerSizesPanel', () => {
  it('keeps a three-decimal volume as entered', async () => {
    getContainerSizes.mockResolvedValue([size()]);
    renderPanel();

    expect(await screen.findByText(/68\.125 cbm/)).toBeInTheDocument();
  });

  it('does not pad a whole volume with decimals it was not given', async () => {
    getContainerSizes.mockResolvedValue([size({ cbm: 68 })]);
    renderPanel();

    expect(await screen.findByText(/68 cbm/)).toBeInTheDocument();
  });

  it('says so when nothing is configured rather than showing an empty card', async () => {
    getContainerSizes.mockResolvedValue([]);
    renderPanel();

    expect(await screen.findByText('No container size configured.')).toBeInTheDocument();
  });

  it('a blank volume keeps Save enabled, names the gap on press and sends nothing', async () => {
    getContainerSizes.mockResolvedValue([]);
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /add size/i }));
    fireEvent.change(await screen.findByLabelText('Code'), { target: { value: '40HQ' } });

    const save = screen.getByRole('button', { name: /^save container size$/i });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(await screen.findByText(/^Enter a number/)).toBeInTheDocument();
    expect(createContainerSize).not.toHaveBeenCalled();
  });
});
