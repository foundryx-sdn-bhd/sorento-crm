import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../lib/sales-opportunity-service', () => ({
  getPortalProductOptions: vi.fn(async () => []),
}));

import { PortalOpportunityLineRow, type LineDraft } from './PortalOpportunityLineRow';

afterEach(() => cleanup());

const line: LineDraft = { key: 'k1', productId: 'p1', productLabel: 'P1', qty: '', unitPrice: '' };

describe('PortalOpportunityLineRow number fields', () => {
  it('shows no sentence before a Save, and none for a blank optional unit price after one', () => {
    const { rerender } = render(
      <PortalOpportunityLineRow line={line} onChange={() => {}} onRemove={() => {}} />,
    );
    expect(screen.queryByText(/^Enter a number/)).not.toBeInTheDocument();
    rerender(<PortalOpportunityLineRow line={line} onChange={() => {}} onRemove={() => {}} showErrors />);
    // Qty is required; unit price may stay blank.
    expect(screen.getAllByText(/^Enter a number/)).toHaveLength(1);
    expect(screen.getByText('Enter a number of at least 0.01.')).toBeInTheDocument();
  });

  it('hands the typed qty through unchanged, blank included', () => {
    const onChange = vi.fn();
    render(<PortalOpportunityLineRow line={{ ...line, qty: '3' }} onChange={onChange} onRemove={() => {}} />);
    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith({ qty: '' });
  });
});
