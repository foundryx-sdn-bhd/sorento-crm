/**
 * Browser blocker (STOCK-DEBT-SNAPSHOT final pass): ticking "Park this first" with several
 * rows of the SAME product froze the tab ("[QueriesObserver]: Duplicate Queries found",
 * renderer at 100% CPU). The dialog runs the REAL `useReserveRowOptions` here; only its data
 * source (`getStockDetail`) is mocked. Signals: the render count after a tick stays small and
 * the dialog stays responsive through tick / untick / tick.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const getStockDetail = vi.fn(async () => ({
  locations: [
    { warehouse_id: 'wh-brw', location: 'BRW', available_qty: '972' },
    { warehouse_id: 'wh-mwh', location: 'MWH', available_qty: '0' },
  ],
}));
vi.mock('../services/fulfilmentPlanningService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/fulfilmentPlanningService')>();
  return { ...actual, getStockDetail: (...args: unknown[]) => (getStockDetail as never as (...a: unknown[]) => unknown)(...args) };
});

// Counts every render of the dialog's grid cells' parent chain by spying on the options hook.
import * as optionsModule from '../hooks/useReserveRowOptions';
const renderSpy = vi.spyOn(optionsModule, 'useReserveRowOptions');

import { RequestReserveDialog } from './RequestReserveDialog';

const ROWS = Array.from({ length: 6 }, (_, i) => ({
  id: `row-${i + 1}`,
  item_code: 'SRTWT904-NL',
  product_id: 'prod-904',
  location: 'BRW-BB',
  delivery_date: `2027-0${i + 1}-01`,
  qty: '30',
  linked_qty: '0',
  reserved_qty: '0',
  bundled_qty: '0',
  verb: 'ORDER',
  state: 'raised',
  reserve_state: null,
}));

function renderDialog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RequestReserveDialog inquiryId="oi-1" open onOpenChange={vi.fn()} rows={ROWS as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Park this first with several rows of one product', () => {
  it('settles after the tick: the button reads Park request and renders stay bounded', async () => {
    renderDialog();
    const box = await screen.findByLabelText(/park this first/i);
    await waitFor(() => expect(getStockDetail).toHaveBeenCalled());
    await screen.findAllByText(/BRW/);
    // let the initial load settle, then count only what the tick causes
    await new Promise((r) => setTimeout(r, 100));
    const before = renderSpy.mock.calls.length;

    fireEvent.click(box);

    expect(await screen.findByRole('button', { name: 'Park request' }, { timeout: 2000 })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 300));
    expect(renderSpy.mock.calls.length - before).toBeLessThan(50);
  }, 8000);

  it('stays responsive through tick, untick, tick', async () => {
    renderDialog();
    await screen.findAllByText(/BRW/);
    const box = await screen.findByLabelText(/park this first/i);
    await new Promise((r) => setTimeout(r, 100));
    const before = renderSpy.mock.calls.length;

    fireEvent.click(box);
    expect(await screen.findByRole('button', { name: 'Park request' }, { timeout: 2000 })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/park this first/i));
    expect(await screen.findByRole('button', { name: 'Send request' }, { timeout: 2000 })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/park this first/i));
    expect(await screen.findByRole('button', { name: 'Park request' }, { timeout: 2000 })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 300));
    expect(renderSpy.mock.calls.length - before).toBeLessThan(60);
  }, 8000);
});
