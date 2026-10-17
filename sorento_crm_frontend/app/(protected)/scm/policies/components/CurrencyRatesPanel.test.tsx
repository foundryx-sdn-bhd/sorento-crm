/**
 * The screen that turns "this plan will not fund" into "add a rate for CNY".
 *
 * The plan refuses to rank or fund a supplier priced in money it holds no rate for, which
 * is correct and is also, on its own, a dead end: nothing on the plan tells the buyer what
 * to do about it. This panel is the other half - it lists the currencies the purchase-order
 * book actually prices in that have no rate, so the work is named rather than deduced.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
/* The grace window is the server's; what this file proves is that the row parks one. */
const createPendingAction = vi.fn().mockResolvedValue({
  id: 'pa-1',
  action_key: 'currency_rate.delete',
  entity_type: 'currency_rate',
  entity_id: 'USD',
  commit_at: '2026-08-30T10:00:10',
  window_seconds: 10,
});
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getCurrencyRates = vi.fn();
const saveCurrencyRate = vi.fn();
const deleteCurrencyRate = vi.fn();

vi.mock('../../services/currencyRateService', () => ({
  getCurrencyRates: (...a: unknown[]) => getCurrencyRates(...a),
  saveCurrencyRate: (...a: unknown[]) => saveCurrencyRate(...a),
  deleteCurrencyRate: (...a: unknown[]) => deleteCurrencyRate(...a),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn(), dismiss: vi.fn() },
}));

import { CurrencyRatesPanel } from './CurrencyRatesPanel';

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CurrencyRatesPanel />
    </QueryClientProvider>,
  );
}

const loaded = {
  base_currency: 'MYR',
  rates: [
    {
      currency: 'USD',
      rate_to_base: 4.4,
      as_of: '2026-08-01',
      note: 'bank rate',
      updated_at: '2026-08-01T00:00:00',
    },
  ],
  missing: ['CNY'],
};

beforeEach(() => {
  vi.clearAllMocks();
  getCurrencyRates.mockResolvedValue(loaded);
  saveCurrencyRate.mockResolvedValue(undefined);
  deleteCurrencyRate.mockResolvedValue(undefined);
});

describe('CurrencyRatesPanel', () => {
  it('shows each rate with the date it was true', async () => {
    // A six-month-old rate is still a rate, and the buyer is entitled to see that is what
    // they are reading before they approve money against it.
    renderPanel();

    expect(await screen.findByText('USD')).toBeInTheDocument();
    expect(screen.getByText(/4\.4/)).toBeInTheDocument();
    expect(screen.getByText(/01\/08\/2026/)).toBeInTheDocument();
  });

  it('names the currencies the book uses that have no rate', async () => {
    renderPanel();

    await screen.findByText('USD');
    expect(screen.getAllByText(/CNY/).length).toBeGreaterThan(0);
    expect(screen.getByText(/no exchange rate/i)).toBeInTheDocument();
  });

  it('says nothing about missing rates when none are missing', async () => {
    getCurrencyRates.mockResolvedValue({ ...loaded, missing: [] });
    renderPanel();

    await screen.findByText('USD');
    expect(screen.queryByText(/no exchange rate/i)).not.toBeInTheDocument();
  });

  it('offers to add a rate straight from the missing list, prefilled', async () => {
    // The fewest decisions: the buyer already told us the currency by having bought in it.
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /add rate for CNY/i }));

    expect(await screen.findByDisplayValue('CNY')).toBeInTheDocument();
  });

  it('saves a rate against the currency it was entered for', async () => {
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /add rate for CNY/i }));
    fireEvent.change(await screen.findByLabelText(/^rate to/i), { target: { value: '0.62' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save currency rate$/i }));

    await waitFor(() =>
      expect(saveCurrencyRate).toHaveBeenCalledWith(
        'CNY',
        expect.objectContaining({ rate_to_base: 0.62 }),
      ),
    );
  });

  it('refuses to save a rate of zero rather than sending it', async () => {
    // A zero rate prices every item in that currency at nothing. The backend refuses it
    // too; this stops the buyer finding that out via an error toast.
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /add rate for CNY/i }));
    fireEvent.change(await screen.findByLabelText(/^rate to/i), { target: { value: '0' } });

    // NUMBER-FIELDS: Save stays enabled; pressing it says why and sends nothing.
    const saveButton = screen.getByRole('button', { name: /^Save currency rate$/i });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);
    expect(await screen.findByText(/^Enter a number/)).toBeInTheDocument();
    expect(saveCurrencyRate).not.toHaveBeenCalled();
  });

  it('a blank rate keeps Save enabled, names the gap on press and sends nothing', async () => {
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /add rate for CNY/i }));
    const rate = await screen.findByLabelText(/^rate to/i);
    fireEvent.change(rate, { target: { value: '0.5' } });
    fireEvent.change(rate, { target: { value: '' } });

    fireEvent.click(screen.getByRole('button', { name: /^Save currency rate$/i }));
    expect(await screen.findByText(/^Enter a number/)).toBeInTheDocument();
    expect(saveCurrencyRate).not.toHaveBeenCalled();
    fireEvent.change(rate, { target: { value: '0.62' } });
    expect(screen.queryByText(/^Enter a number/)).not.toBeInTheDocument();
  });

  it('parks the removal rather than asking first (S6-10)', async () => {
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /remove USD/i }));

    // D7: the press IS the action. What the plan loses without a rate is the
    // server's consequence either way, and Cancel in the countdown is the way back.
    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'currency_rate.delete',
          entityType: 'currency_rate',
          entityId: 'USD',
        }),
      ),
    );
    expect(deleteCurrencyRate).not.toHaveBeenCalled();
  });

  it('states the base currency rather than leaving the reader to infer it', async () => {
    renderPanel();

    await screen.findByText('USD');
    expect(screen.getAllByText(/MYR/).length).toBeGreaterThan(0);
  });

  it('shows an empty state that says what a rate is for', async () => {
    getCurrencyRates.mockResolvedValue({ base_currency: 'MYR', rates: [], missing: [] });
    renderPanel();

    expect(await screen.findByText(/no exchange rates yet/i)).toBeInTheDocument();
  });
});
