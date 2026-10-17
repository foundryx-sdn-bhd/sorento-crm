/**
 * NUMBER-FIELDS: Save stays enabled on a blank Cost; pressing it says why and sends nothing.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const h = vi.hoisted(() => ({ create: vi.fn() }));

vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: () => ({ countdown: null, start: vi.fn() }),
}));
vi.mock('../services/productSupplierCostService', () => ({
  createProductSupplierCost: h.create,
  updateProductSupplierCost: vi.fn(),
}));

import { CostRowDialog } from './CostRowDialog';

describe('CostRowDialog - blank Cost', () => {
  it('keeps Save enabled, shows the message on press and sends nothing', () => {
    render(
      <CostRowDialog
        open
        onOpenChange={() => {}}
        link={{ id: 'link-1', product: { product_code: 'ZZT-001' }, currency: 'CNY' }}
        cost={null}
        onSaved={() => {}}
      />,
    );

    const save = screen.getByRole('button', { name: /save/i });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    expect(screen.getByText(/^Enter a number of at least 0/)).toBeInTheDocument();
    expect(h.create).not.toHaveBeenCalled();
  });
});
