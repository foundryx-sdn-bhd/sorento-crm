/**
 * FulfilmentPriorityPanel - loading / empty / error / data states + save.
 * PLAN-demo-followups-19aug-ladder-v2.md C1/C2, amended by borrow ladder v7.1 S1
 * (AC-S1-4): the two cross-group cap inputs are gone and `TBA date from` sits beside
 * `Purchasing covers demand until`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const hooks = vi.hoisted(() => ({
  useFulfilmentPriority: vi.fn(),
  useSaveFulfilmentPriority: vi.fn(),
}));
vi.mock('../hooks/usePolicies', () => hooks);

import { FulfilmentPriorityPanel } from './FulfilmentPriorityPanel';

const DATA = {
  name: 'Fair fulfilment priority (delivery date, document date, customer credit)',
  factors: {
    po_document_sequence: 1,
    demand_class: 3,
    need_by_date: 3,
    document_age: 1,
    customer_credit: 1,
  },
  demand_class_weights: { project: 1, retail: 0.4 },
  reorder_coverage_until: '2026-10-31',
  tba_date_from: '2029-01-01',
  transfer_days: 0,
  exists: true,
};

/** The column default of `scm.priority_policy.tba_date_from` (migration 443). */
const DEFAULT_TBA = '2029-01-01';

/** Local calendar day, the way the panel computes "today" for its own mirror check. */
function todayIso(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function isoDaysFromToday(days: number): string {
  const now = new Date();
  const shifted = new Date(now.getTime() + days * 86400000);
  return new Date(shifted.getTime() - shifted.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10);
}
const mutateAsync = vi.fn();

beforeEach(() => {
  hooks.useFulfilmentPriority.mockReset();
  hooks.useSaveFulfilmentPriority.mockReset();
  mutateAsync.mockReset().mockResolvedValue(DATA);
  hooks.useSaveFulfilmentPriority.mockReturnValue({ mutateAsync, isPending: false });
});

describe('FulfilmentPriorityPanel', () => {
  it('renders a loading skeleton (no inputs) while loading', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(<FulfilmentPriorityPanel />);
    expect(screen.queryByLabelText('Delivery date')).not.toBeInTheDocument();
  });

  it('renders the error state', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<FulfilmentPriorityPanel />);
    expect(screen.getByText(/Failed to load the fulfilment priority policy/i)).toBeInTheDocument();
  });

  it('renders the empty state when no policy has been activated yet', () => {
    hooks.useFulfilmentPriority.mockReturnValue({
      data: { ...DATA, exists: false },
      isLoading: false,
      isError: false,
    });
    render(<FulfilmentPriorityPanel />);
    expect(screen.getByText(/No fulfilment priority has been activated yet/i)).toBeInTheDocument();
  });

  it('renders the stored weights and settings using the board labels', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);
    expect(screen.getByLabelText('Delivery date')).toHaveValue(3);
    expect(screen.getByLabelText('Order date')).toHaveValue(1);
    expect(screen.getByLabelText('Payment terms')).toHaveValue(1);
    expect(screen.getByLabelText('Demand type')).toHaveValue(3);
    expect(screen.getByLabelText('Purchase order sequence')).toHaveValue(1);
    expect(screen.getByLabelText('Project')).toHaveValue(1);
    expect(screen.getByLabelText('Retail')).toHaveValue(0.4);
    expect(screen.getByLabelText(/Purchasing covers demand until/i)).toHaveValue('2026-10-31');
    expect(screen.getByLabelText(/TBA date from/i)).toHaveValue(DEFAULT_TBA);
    // R5 dropped the cross-group caps, so the two inputs are gone entirely.
    expect(screen.queryByLabelText(/borrow cap/i)).not.toBeInTheDocument();
  });

  it('save sends the edited weights and settings', async () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText('Delivery date'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText(/Purchasing covers demand until/i), {
      target: { value: '2026-12-01' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync).toHaveBeenCalledWith({
      factors: {
        po_document_sequence: 1,
        demand_class: 3,
        need_by_date: 5,
        document_age: 1,
        customer_credit: 1,
      },
      demand_class_weights: { project: 1, retail: 0.4 },
      reorder_coverage_until: '2026-12-01',
      tba_date_from: DEFAULT_TBA,
      transfer_days: 0,
      immediate_window_days: 30,
      pool_share_pct: 50,
      overdue_grace_days: 0,
      overdue_dead_days: 0,
    });
  });

  it('renders the two overdue-grace fields and round-trips them (R-O, AC-O.5)', async () => {
    hooks.useFulfilmentPriority.mockReturnValue({
      data: { ...DATA, overdue_grace_days: 21, overdue_dead_days: 60 },
      isLoading: false,
      isError: false,
    });
    render(<FulfilmentPriorityPanel />);

    // Seeded from what the GET states, not from the panel's own fallback.
    expect(screen.getByLabelText(/Overdue grace \(days\)/i)).toHaveValue(21);
    expect(screen.getByLabelText(/Overdue dead after \(days\)/i)).toHaveValue(60);

    fireEvent.change(screen.getByLabelText(/Overdue grace \(days\)/i), {
      target: { value: '7' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync.mock.calls[0][0].overdue_grace_days).toBe(7);
    expect(mutateAsync.mock.calls[0][0].overdue_dead_days).toBe(60);
  });

  it('seeds the overdue defaults (SHIPPED 0 / 0, captain\'s ruling 3 Sep 2026) when the GET predates migration 464', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    expect(screen.getByLabelText(/Overdue grace \(days\)/i)).toHaveValue(0);
    expect(screen.getByLabelText(/Overdue dead after \(days\)/i)).toHaveValue(0);
  });

  it('blocks a save with an out-of-range overdue grace', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/Overdue grace \(days\)/i), {
      target: { value: '400' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    expect(
      screen.getByText(/Overdue grace \(days\) must be between 0 and 365/i),
    ).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('NUMBER-FIELDS: a blank number blocks the save with a sentence under the field, never saved as 0', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/Pool share/i), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Pool share/i)).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText(/^Enter a whole number between 0 and 100/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Pool share/i), { target: { value: '40' } });
    expect(screen.queryByText(/^Enter a whole number between 0 and 100/)).not.toBeInTheDocument();
  });

  it('renders and saves the transfer-days field (AC-2.3)', async () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    expect(screen.getByLabelText(/Transfer days between bins/i)).toHaveValue(0);

    fireEvent.change(screen.getByLabelText(/Transfer days between bins/i), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync.mock.calls[0][0].transfer_days).toBe(2);
  });

  it('blocks a save with a negative transfer-days value', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/Transfer days between bins/i), {
      target: { value: '-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    expect(screen.getByText(/Transfer days between bins must be 0 or more/i)).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('save with the date cleared sends null, not an empty string', async () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const payload = mutateAsync.mock.calls[0][0];
    expect(payload.reorder_coverage_until).toBeNull();
  });

  it('blocks a save with a negative weight (client mirror of the backend validation)', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText('Delivery date'), { target: { value: '-1' } });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    expect(screen.getByText(/Delivery date weight must be 0 or more/i)).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('blocks a save with a TBA date in the past (client mirror of the 422)', () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/TBA date from/i), {
      target: { value: isoDaysFromToday(-1) },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    expect(screen.getByText('TBA date from must be today or later.')).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('saves a weight over a stored TBA date that has since passed', async () => {
    // The freshness rule is about a CHANGE, not about the value. A TBA date is legal the
    // day it is saved and historic a year later, and the panel sends the stored date back
    // with EVERY save - so checking the value rather than the edit locked the whole screen:
    // no weight, no coverage date and no class weight could be saved again until somebody
    // also picked a new TBA date.
    const stale = isoDaysFromToday(-400);
    hooks.useFulfilmentPriority.mockReturnValue({
      data: { ...DATA, tba_date_from: stale },
      isLoading: false,
      isError: false,
    });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/Delivery date/i), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(screen.queryByText('TBA date from must be today or later.')).not.toBeInTheDocument();
    expect(mutateAsync.mock.calls[0][0].tba_date_from).toBe(stale);
    expect(mutateAsync.mock.calls[0][0].factors.need_by_date).toBe(7);
  });

  it('still blocks MOVING the TBA date to a different past day', () => {
    const stale = isoDaysFromToday(-400);
    hooks.useFulfilmentPriority.mockReturnValue({
      data: { ...DATA, tba_date_from: stale },
      isLoading: false,
      isError: false,
    });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/TBA date from/i), {
      target: { value: isoDaysFromToday(-1) },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    expect(screen.getByText('TBA date from must be today or later.')).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('accepts today itself - the boundary the backend allows', async () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/TBA date from/i), { target: { value: todayIso() } });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync.mock.calls[0][0].tba_date_from).toBe(todayIso());
  });

  it('Reset restores the default TBA date, and hides once the field is back at it', () => {
    // The column is NOT NULL, so "clear" means "back to 2029-01-01", never an empty field.
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    // At the default there is nothing to reset to, so the button is not offered.
    expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/TBA date from/i), { target: { value: '2031-05-01' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));

    expect(screen.getByLabelText(/TBA date from/i)).toHaveValue(DEFAULT_TBA);
    expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument();
  });

  it('the coverage-until Clear button still resolves beside the TBA Reset', () => {
    // Two buttons live in this block now; `Clear` must still find the coverage date and
    // leave the TBA date where it was.
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

    expect(screen.getByLabelText(/Purchasing covers demand until/i)).toHaveValue('');
    expect(screen.getByLabelText(/TBA date from/i)).toHaveValue(DEFAULT_TBA);
  });

  it('an emptied date input saves the column default, never an empty value', async () => {
    // The column is NOT NULL and a native date input hands back '' when cleared, so the
    // one fallback left on this screen turns that into the default rather than a blank.
    hooks.useFulfilmentPriority.mockReturnValue({
      data: { ...DATA, tba_date_from: '2031-05-01' },
      isLoading: false,
      isError: false,
    });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText(/TBA date from/i), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync.mock.calls[0][0].tba_date_from).toBe(DEFAULT_TBA);
  });

  it('an extra factor / demand-class key the backend stored round-trips through save untouched', async () => {
    // `factors` / `demand_class_weights` are open JSONB - a key this screen does not
    // render (a future factor, or one added by another client) must survive a save
    // made through this screen rather than being dropped.
    const withExtras = {
      ...DATA,
      factors: { ...DATA.factors, future_factor: 2.5 },
      demand_class_weights: { ...DATA.demand_class_weights, wholesale: 0.7 },
    };
    hooks.useFulfilmentPriority.mockReturnValue({ data: withExtras, isLoading: false, isError: false });
    render(<FulfilmentPriorityPanel />);

    fireEvent.change(screen.getByLabelText('Delivery date'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const payload = mutateAsync.mock.calls[0][0];
    expect(payload.factors).toEqual({
      po_document_sequence: 1,
      demand_class: 3,
      need_by_date: 5,
      document_age: 1,
      customer_credit: 1,
      future_factor: 2.5,
    });
    expect(payload.demand_class_weights).toEqual({ project: 1, retail: 0.4, wholesale: 0.7 });
  });

  it('a rejected save is not an unhandled promise rejection', async () => {
    hooks.useFulfilmentPriority.mockReturnValue({ data: DATA, isLoading: false, isError: false });
    mutateAsync.mockReset().mockRejectedValue(new Error('ZZT save failed'));
    hooks.useSaveFulfilmentPriority.mockReturnValue({ mutateAsync, isPending: false });
    const onUnhandledRejection = vi.fn();
    window.addEventListener('unhandledrejection', onUnhandledRejection);

    render(<FulfilmentPriorityPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Save fulfilment priority/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    // Give the microtask queue a turn so an unhandled rejection would have fired.
    await new Promise((resolve) => setTimeout(resolve, 0));

    window.removeEventListener('unhandledrejection', onUnhandledRejection);
    expect(onUnhandledRejection).not.toHaveBeenCalled();
  });
});
