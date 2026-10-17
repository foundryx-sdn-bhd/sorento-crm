/**
 * The stock status panel (UAC AC-13..AC-19, AC-21). `useStockStatus` is mocked: this pins
 * what the panel renders from a response, not the network read.
 */
import React from 'react';
import { fireEvent, render as rtlRender, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { StockStatus } from './stockStatus.types';

const useStockStatusMock = vi.fn();
vi.mock('./useStockStatus', () => ({
  useStockStatus: (...args: unknown[]) => useStockStatusMock(...args),
}));

import { StockStatusPanel } from './StockStatusPanel';

// The grid reads column preferences through React Query; give it a client of its own.
function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const DATA: StockStatus = {
  product_id: 'p1',
  item_code: 'SRTWB1515',
  description: 'Basin',
  as_of: '2026-10-15T06:00:00',
  so_due_by: null,
  locations: [
    { warehouse_id: 'bb', warehouse_code: 'BRW-BB', warehouse_name: 'BRW BB', is_active: true,
      on_hand: 0, so_qty: 7312, po_qty: 1734, spo_qty: 50, forecast: -5528 },
    { warehouse_id: 'mwh', warehouse_code: 'MWH-IB', warehouse_name: 'MWH IB', is_active: true,
      on_hand: 140, so_qty: 0, po_qty: 0, spo_qty: 0, forecast: 140 },
    { warehouse_id: 'con', warehouse_code: 'CON', warehouse_name: 'Consignment', is_active: false,
      on_hand: 1, so_qty: 0, po_qty: 0, spo_qty: 0, forecast: 1 },
  ],
  totals: { on_hand: 141, so_qty: 7312, po_qty: 1734, spo_qty: 50, forecast: -5387,
            incoming_pl: 227, forecast_incl_pl: -5160 },
  incoming_pl: [
    { shipment_id: 'sh1', shipment_number: 'PL-2610-001', container_number: 'SEKU5230843', eta: '2026-10-19',
      still_to_land: 118, on_spo: 0, counted: 118 },
  ],
  sales_orders: [
    { line_id: 'l1', sales_order_id: 'so1', so_number: 'SO341788', debtor_code: '300-K032', debtor_name: 'KEE LIN TRDG',
      project_label: 'MANDARIN PR', salesperson: null, doc_date: '2025-03-17', due_date: '2026-10-31', qty: 7312,
      covered_in_crm: false, warehouse_id: 'bb' },
  ],
  purchase_orders: [
    { line_id: 'pl1', purchase_order_id: 'po1', po_number: '202608-S0006', supplier_code: '400-C030', supplier_name: 'FACTORY',
      issue_date: null, expected_date: '2026-11-05', qty: 1734, warehouse_id: 'bb' },
  ],
  spos: [
    { allocation_id: 'a1', spo_number: 'SPO-2026/10-0024', spo_line_number: 1, supplier_code: '400-C029', supplier_name: 'J',
      issue_date: '2026-10-06', arrival_date: '2026-10-17', container_number: 'SEKU1741573', qty: 50, warehouse_id: 'bb' },
  ],
};

beforeEach(() => {
  useStockStatusMock.mockReset();
  useStockStatusMock.mockReturnValue({ data: DATA, isLoading: false, error: null, refetch: vi.fn() });
});

describe('StockStatusPanel', () => {
  it('reads the item with the All horizon first, and lists every location, inactive ones tagged', () => {
    render(<StockStatusPanel productId="p1" />);
    expect(useStockStatusMock).toHaveBeenLastCalledWith('p1', null);
    expect(screen.getByText('BRW-BB')).toBeInTheDocument();
    expect(screen.getByText('MWH-IB')).toBeInTheDocument();
    const con = screen.getByText('CON').closest('tr') as HTMLElement;
    expect(within(con).getByText('Inactive in CRM')).toBeInTheDocument();
    expect(screen.getByText('Total')).toBeInTheDocument();
    expect(screen.getAllByText('-5,387').length).toBeGreaterThan(0);
  });

  it('carries Incoming PL on the item, not on a location, and the forecast including it', () => {
    render(<StockStatusPanel productId="p1" />);
    const summary = screen.getByTestId('stock-status-summary');
    expect(within(summary).getByText('+227')).toBeInTheDocument();
    expect(within(summary).getByText('-5,160')).toBeInTheDocument();
    fireEvent.click(within(summary).getByRole('button', { name: /Incoming PL/ }));
    expect(screen.getByRole('link', { name: 'PL-2610-001' })).toHaveAttribute(
      'href',
      '/procurement-management/packing-lists/sh1',
    );
  });

  it('searching the locations filters the rows, subtotals what is shown and keeps the item total', async () => {
    render(<StockStatusPanel productId="p1" />);
    await userEvent.type(screen.getByRole('textbox', { name: 'Search locations' }), 'mwh');
    expect(screen.queryByText('BRW-BB')).not.toBeInTheDocument();
    expect(screen.getByText('Shown, 1 of 3')).toBeInTheDocument();
    expect(within(screen.getByTestId('stock-status-summary')).getByText('All locations')).toBeInTheDocument();
  });

  it('opens a location onto its documents, every number linking to its CRM page, this line marked', () => {
    render(<StockStatusPanel productId="p1" lineIds={['l1']} />);
    fireEvent.click(screen.getByText('BRW-BB'));
    const docs = screen.getByTestId('stock-status-documents');
    expect(within(docs).getByRole('link', { name: 'SO341788' })).toHaveAttribute('href', '/scm/sales-orders/so1');
    expect(within(docs).getByRole('link', { name: '202608-S0006' })).toHaveAttribute('href', '/scm/purchase-orders/po1');
    expect(within(docs).getByRole('link', { name: 'SPO-2026/10-0024' })).toHaveAttribute(
      'href',
      '/procurement-management/spo-allocations/SPO-2026%2F10-0024',
    );
    expect(within(docs).getByText('This line')).toBeInTheDocument();
  });

  it('SO totals groups by project by default, one column per location carrying SO', async () => {
    render(<StockStatusPanel productId="p1" />);
    await userEvent.click(screen.getByRole('tab', { name: 'SO totals' }));
    expect(screen.getByText('MANDARIN PR')).toBeInTheDocument();
    expect(screen.getAllByText('BRW-BB').length).toBeGreaterThan(0);
    expect(screen.queryByText('MWH-IB')).not.toBeInTheDocument();
  });

  it('prints no dot separators anywhere', () => {
    const { container } = render(<StockStatusPanel productId="p1" />);
    expect(container.textContent).not.toContain('·');
  });

  it('says so plainly when the item has nothing anywhere', () => {
    useStockStatusMock.mockReturnValue({
      data: { ...DATA, locations: [], incoming_pl: [], sales_orders: [], purchase_orders: [], spos: [] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<StockStatusPanel productId="p1" />);
    expect(screen.getByText('No stock, orders or incoming for this item')).toBeInTheDocument();
  });
});
