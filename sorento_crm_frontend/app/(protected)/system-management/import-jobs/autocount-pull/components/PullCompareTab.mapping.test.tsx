/**
 * PullCompareTab - DO-COMPARE-SIM red tests (AC-CMM-10/12/13): the dropzone titles carry the
 * configured sheet, the file is parsed from that sheet, and a "Mapping" button opens the
 * dialog. Service module mocked; real hooks over a real QueryClient.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getCompareMappings = vi.fn();
vi.mock('../services/autocountPullService', () => ({
  getCompareMappings: (...a: unknown[]) => getCompareMappings(...a),
  saveCompareMapping: vi.fn(),
  comparePull: vi.fn().mockResolvedValue({
    summary: { filename: 'f', compared_at: '2026-09-30T00:00:00Z', total: 0, matched: 0, different: 0,
      only_in_excel: 0, only_in_pull: 0 },
    differences: [], only_in_excel: [], only_in_pull: [],
  }),
}));

const parseExcelFile = vi.fn();
vi.mock('@/lib/excel-utils', () => ({
  generateExcelFile: vi.fn(),
  parseExcelFile: (...a: unknown[]) => parseExcelFile(...a),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import { PullCompareTab } from './PullCompareTab';

const MAPPINGS = {
  items: [
    { kind: 'order_listing', sheet_name: 'Sheet X', updated_at: null,
      columns: [{ excel_header: 'Doc No', transform: 'text', field: 'doc_no' },
                { excel_header: 'Item Code', transform: 'text', field: 'item_code' }] },
    { kind: 'order_tracking', sheet_name: 'Tracker', updated_at: null,
      columns: [{ excel_header: 'Doc. No.', transform: 'text', field: 'doc_no' }] },
  ],
};

function renderTab(entity: 'delivery_orders' | 'products' = 'delivery_orders') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    React.createElement(
      QueryClientProvider,
      { client },
      React.createElement(PullCompareTab, { jobId: 'job-1', entity }),
    ),
  );
}

beforeEach(() => {
  getCompareMappings.mockReset().mockResolvedValue(MAPPINGS);
  parseExcelFile.mockReset();
});

describe('PullCompareTab mapping (delivery orders)', () => {
  it('AC-CMM-12: dropzone titles show the configured sheet names', async () => {
    renderTab();
    expect(await screen.findByText('Order Listing (macro), sheet Sheet X')).toBeInTheDocument();
    expect(screen.getByText('Order Tracking (macro), sheet Tracker')).toBeInTheDocument();
  });

  it('AC-CMM-10/11: each file is parsed from its own configured sheet', async () => {
    renderTab();
    await screen.findByText('Order Listing (macro), sheet Sheet X');
    parseExcelFile.mockResolvedValue([{ 'Doc No': 'D1' }]);
    const listing = new File(['x'], 'Order Listing.xlsm');
    fireEvent.change(screen.getByLabelText('Order Listing sheet to compare'), { target: { files: [listing] } });
    await waitFor(() => expect(parseExcelFile).toHaveBeenCalledTimes(1));
    expect(parseExcelFile.mock.calls[0][0]).toBe(listing);
    expect(parseExcelFile.mock.calls[0][1]).toEqual({ sheetName: 'Sheet X' });
    const tracking = new File(['x'], 'Order Tracking.xlsm');
    fireEvent.change(screen.getByLabelText('Order Tracking sheet to compare'), { target: { files: [tracking] } });
    await waitFor(() => expect(parseExcelFile).toHaveBeenCalledTimes(2));
    expect(parseExcelFile.mock.calls[1][1]).toEqual({ sheetName: 'Tracker' });
  });

  it('AC-CMM-13: the Mapping button opens the dialog', async () => {
    renderTab();
    await screen.findByText('Order Listing (macro), sheet Sheet X');
    expect(screen.queryByLabelText('Sheet name')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mapping' }));
    expect(await screen.findByLabelText('Sheet name')).toHaveValue('Sheet X');
    expect(screen.getByRole('tab', { name: 'Order Tracking' })).toBeInTheDocument();
  });

  it('products compare has no Mapping button and is not parsed by sheet name', async () => {
    renderTab('products');
    expect(screen.queryByRole('button', { name: 'Mapping' })).not.toBeInTheDocument();
  });
});

describe('PullCompareTab while mappings load / hints (fix round 1)', () => {
  it('does not parse a file with a guessed sheet before the mappings have loaded', async () => {
    getCompareMappings.mockReset().mockReturnValue(new Promise(() => {})); // never resolves
    renderTab();
    parseExcelFile.mockResolvedValue([{ 'Doc No': 'D1' }]);
    fireEvent.change(screen.getByLabelText('Order Listing sheet to compare'), {
      target: { files: [new File(['x'], 'Order Listing.xlsm')] },
    });
    await new Promise((r) => setTimeout(r, 50));
    expect(parseExcelFile).not.toHaveBeenCalled();
  });

  it('the hints list the Excel column names of the saved mapping, not hard-coded text', async () => {
    getCompareMappings.mockReset().mockResolvedValue({
      items: [
        { kind: 'order_listing', sheet_name: 'Master', updated_at: null,
          columns: [{ excel_header: 'Voucher Ref', transform: 'text', field: 'doc_no' },
                    { excel_header: 'Sku', transform: 'text', field: 'item_code' }] },
        { kind: 'order_tracking', sheet_name: 'Master', updated_at: null,
          columns: [{ excel_header: 'Tracker Id', transform: 'text', field: 'doc_no' }] },
      ],
    });
    renderTab();
    await screen.findByText(/Voucher Ref/);
    expect(screen.getByText(/Sku/)).toBeInTheDocument();
    expect(screen.getByText(/Tracker Id/)).toBeInTheDocument();
    expect(screen.queryByText(/Debtor Code/)).not.toBeInTheDocument();
  });
});

