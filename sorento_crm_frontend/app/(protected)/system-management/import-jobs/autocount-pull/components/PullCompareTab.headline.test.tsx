/**
 * PullCompareTab - DO-COMPARE-SIM fix round 4: the delivery-orders headline adds up the
 * `source_summary` of the source results on screen, never the server's combined `summary`
 * (which still counts a file that failed to parse or was removed).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getCompareMappings = vi.fn();
const comparePull = vi.fn();
vi.mock('../services/autocountPullService', () => ({
  getCompareMappings: (...a: unknown[]) => getCompareMappings(...a),
  saveCompareMapping: vi.fn(),
  comparePull: (...a: unknown[]) => comparePull(...a),
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
    { kind: 'order_listing', sheet_name: 'Master', updated_at: null,
      columns: [{ excel_header: 'Doc No', transform: 'text', field: 'doc_no' }] },
    { kind: 'order_tracking', sheet_name: 'Master', updated_at: null,
      columns: [{ excel_header: 'Doc. No.', transform: 'text', field: 'doc_no' }] },
  ],
};

// The server's stored COMBINED summary, deliberately not the sum a screen should show.
const COMBINED = {
  filename: 'both', compared_at: '2026-09-30T00:00:00Z', total: 4512, matched: 4498, different: 14,
  only_in_excel: 49, only_in_pull: 0,
};
const LISTING = { filename: 'listing.xlsm', compared_at: '2026-09-30T00:00:00Z', total: 3362,
  matched: 3362, different: 0, only_in_excel: 0, only_in_pull: 0 };
const TRACKING = { filename: 'tracking.xlsm', compared_at: '2026-09-30T00:00:01Z', total: 1150,
  matched: 1136, different: 14, only_in_excel: 49, only_in_pull: 0 };

function response(source: 'lines' | 'headers', sourceSummary: object) {
  return {
    summary: { ...COMBINED, compared_at: source === 'headers' ? '2026-09-30T00:00:01Z' : '2026-09-30T00:00:00Z' },
    source_summary: sourceSummary, source, differences: [], only_in_excel: [], only_in_pull: [],
    rows_in_window: 10, ignored_outside_window: 0,
  };
}

async function drop(label: string, name: string) {
  parseExcelFile.mockResolvedValueOnce([{ 'Doc No': 'D1', 'Doc. No.': 'D1' }]);
  const before = comparePull.mock.calls.length;
  fireEvent.change(screen.getByLabelText(label), { target: { files: [new File(['x'], name)] } });
  await waitFor(() => expect(comparePull.mock.calls.length).toBe(before + 1));
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    React.createElement(QueryClientProvider, { client },
      React.createElement(PullCompareTab, { jobId: 'job-1', entity: 'delivery_orders' })),
  );
}

beforeEach(() => {
  getCompareMappings.mockReset().mockResolvedValue(MAPPINGS);
  parseExcelFile.mockReset();
  comparePull.mockReset().mockImplementation(async (_job: string, _name: string, _rows: unknown, source: string) =>
    source === 'headers' ? response('headers', TRACKING) : response('lines', LISTING));
});

describe('delivery orders headline', () => {
  it('(a) only the Listing on screen: its own numbers, not the combined summary', async () => {
    renderTab();
    await screen.findByText(/Columns read: Doc No/);
    await drop('Order Listing sheet to compare', 'listing.xlsm');
    expect(await screen.findByText('100% match.')).toBeInTheDocument();
    expect(screen.getByText(/3362 of 3362/)).toBeInTheDocument();
    expect(screen.queryByText(/4498/)).not.toBeInTheDocument();
  });

  it('(b) both on screen: the two source summaries added up', async () => {
    renderTab();
    await screen.findByText(/Columns read: Doc No/);
    await drop('Order Listing sheet to compare', 'listing.xlsm');
    await drop('Order Tracking sheet to compare', 'tracking.xlsm');
    expect(await screen.findByText('4498 of 4512 match.')).toBeInTheDocument();
    expect(screen.getByText(/49 only in your Excel/)).toBeInTheDocument();
  });

  it('(c) removing the Tracking chip drops the headline to the Listing numbers', async () => {
    renderTab();
    await screen.findByText(/Columns read: Doc No/);
    await drop('Order Listing sheet to compare', 'listing.xlsm');
    await drop('Order Tracking sheet to compare', 'tracking.xlsm');
    expect(await screen.findByText('4498 of 4512 match.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove tracking.xlsm' }));
    await waitFor(() => expect(screen.getByText('100% match.')).toBeInTheDocument());
    expect(screen.getByText(/3362 of 3362/)).toBeInTheDocument();
    expect(screen.queryByText(/4498/)).not.toBeInTheDocument();
  });
});
