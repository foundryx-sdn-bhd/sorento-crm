/**
 * SupplierScoringPanel - loading / empty / error / data + live weight-total.
 *   AC-SUP-1 (renders the single row, empty state, "next analytics run" copy),
 *   AC-SUP-2 (weights must total 100%; save upserts fractional weights), AC-STD-4.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const hooks = vi.hoisted(() => ({
  useSupplierScoring: vi.fn(),
  useSaveSupplierScoring: vi.fn(),
}));
vi.mock('../hooks/usePolicies', () => hooks);

import { SupplierScoringPanel } from './SupplierScoringPanel';

const DATA = { delivery_weight: 0.6, quality_weight: 0.4, grace_days: 2, min_sample_size: 5, exists: true };
const mutateAsync = vi.fn();

beforeEach(() => {
  hooks.useSupplierScoring.mockReset();
  hooks.useSaveSupplierScoring.mockReset();
  mutateAsync.mockReset().mockResolvedValue(DATA);
  hooks.useSaveSupplierScoring.mockReturnValue({ mutateAsync, isPending: false });
});

describe('SupplierScoringPanel', () => {
  it('renders a loading skeleton (no inputs) while loading', () => {
    hooks.useSupplierScoring.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(<SupplierScoringPanel />);
    expect(screen.queryByDisplayValue('60')).not.toBeInTheDocument();
    expect(screen.getByText(/next analytics run/i)).toBeInTheDocument();
  });

  it('renders the error state', () => {
    hooks.useSupplierScoring.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<SupplierScoringPanel />);
    expect(screen.getByText(/Failed to load supplier scoring/i)).toBeInTheDocument();
  });

  it('renders the empty state (seeded defaults) when no row exists (AC-SUP-1)', () => {
    hooks.useSupplierScoring.mockReturnValue({
      data: { ...DATA, exists: false },
      isLoading: false,
      isError: false,
    });
    render(<SupplierScoringPanel />);
    expect(screen.getByText(/No scoring policy saved yet/i)).toBeInTheDocument();
  });

  it('renders the stored weights and a valid live total (AC-SUP-1/2)', () => {
    hooks.useSupplierScoring.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<SupplierScoringPanel />);
    expect(screen.getByDisplayValue('60')).toBeInTheDocument(); // delivery 0.6 → 60%
    expect(screen.getByDisplayValue('40')).toBeInTheDocument(); // quality 0.4 → 40%
    // Live total indicator reads 100%.
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('flags a bad weight total and disables Save (AC-SUP-2)', () => {
    hooks.useSupplierScoring.mockReturnValue({
      data: { delivery_weight: 0.7, quality_weight: 0.4, grace_days: 2, min_sample_size: 5, exists: true },
      isLoading: false,
      isError: false,
    });
    render(<SupplierScoringPanel />);
    // 70 + 40 = 110 → indicator warns and Save is disabled.
    expect(screen.getByText(/must be 100%/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Save scoring/i })).toBeDisabled();
  });

  it('save upserts fractional weights (AC-SUP-2)', async () => {
    hooks.useSupplierScoring.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<SupplierScoringPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Save scoring/i }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync).toHaveBeenCalledWith({
      delivery_weight: 0.6,
      quality_weight: 0.4,
      grace_days: 2,
      min_sample_size: 5,
    });
  });

  it('NUMBER-FIELDS: a blank grace blocks the save with a sentence under the field, never saved as 0', () => {
    hooks.useSupplierScoring.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<SupplierScoringPanel />);

    fireEvent.change(screen.getByLabelText(/On-time grace/i), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Save scoring/i }));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText(/^Enter a number of at least 0/)).toBeInTheDocument();
  });

  it('NUMBER-FIELDS: a blank weight keeps Save enabled and says why', () => {
    hooks.useSupplierScoring.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<SupplierScoringPanel />);

    fireEvent.change(screen.getByLabelText(/Delivery weight/i), { target: { value: '' } });
    const save = screen.getByRole('button', { name: /Save scoring/i });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText(/^Enter a number between 0 and 100/)).toBeInTheDocument();
  });
});
