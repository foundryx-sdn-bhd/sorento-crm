/**
 * PullCompareTab - DO-COMPARE-SIM fix round 2 red tests (browser pass findings 1 to 4).
 * Service module mocked; real hooks over a real QueryClient.
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
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast }));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import { PullCompareTab } from './PullCompareTab';

const MAPPINGS = {
  items: [
    { kind: 'order_listing', sheet_name: 'Master', updated_at: null,
      columns: [{ excel_header: 'Doc No', transform: 'text', field: 'doc_no' },
                { excel_header: 'Item Code', transform: 'text', field: 'item_code' }] },
    { kind: 'order_tracking', sheet_name: 'Master', updated_at: null,
      columns: [{ excel_header: 'Doc. No.', transform: 'text', field: 'doc_no' },
                { excel_header: 'Cancel', transform: 'cancel_flag', field: 'cancel' }] },
  ],
};

function result(rowsInWindow = 1234) {
  return {
    summary: { filename: 'f.xlsm', compared_at: '2026-09-30T00:00:00Z', total: 5, matched: 5, different: 0,
      only_in_excel: 0, only_in_pull: 0 },
    differences: [], only_in_excel: [], only_in_pull: [], rows_in_window: rowsInWindow,
    ignored_outside_window: 0, source: 'lines',
  };
}

function renderTab(entity: 'delivery_orders' | 'products' = 'delivery_orders') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    React.createElement(QueryClientProvider, { client },
      React.createElement(PullCompareTab, { jobId: 'job-1', entity })),
  );
}

async function dropListing(rows: Record<string, unknown>[] | Error, name = 'Order Listing.xlsm') {
  if (rows instanceof Error) parseExcelFile.mockRejectedValueOnce(rows);
  else parseExcelFile.mockResolvedValueOnce(rows);
  const before = parseExcelFile.mock.calls.length;
  fireEvent.change(screen.getByLabelText('Order Listing sheet to compare'), {
    target: { files: [new File(['x'], name)] },
  });
  await waitFor(() => expect(parseExcelFile.mock.calls.length).toBe(before + 1));
}

beforeEach(() => {
  getCompareMappings.mockReset().mockResolvedValue(MAPPINGS);
  comparePull.mockReset().mockResolvedValue(result());
  parseExcelFile.mockReset();
  Object.values(toast).forEach((f) => f.mockReset());
});

describe('fix 2.1: rows are projected to the mapped columns', () => {
  it('posts only the keys named by a mapped Excel header, original spelling kept', async () => {
    renderTab();
    await screen.findByText(/Columns read: Doc No, Item Code/); // mappings loaded
    await dropListing([
      { 'Doc No': 'D1', ' item code ': 'X', 'Debtor Name': 'n', Total_1: 1, Check: 2 },
    ]);
    await waitFor(() => expect(comparePull).toHaveBeenCalledTimes(1));
    const [, , rows, source] = comparePull.mock.calls[0];
    expect(source).toBe('lines');
    expect(rows).toEqual([{ 'Doc No': 'D1', ' item code ': 'X' }]);
  });

  it('the headers source is projected by the tracking mapping', async () => {
    renderTab();
    await screen.findByText(/Columns read: Doc No, Item Code/); // mappings loaded
    parseExcelFile.mockResolvedValueOnce([{ 'Doc. No.': 'D1', cancel: 'F', Agent: 'a', 'Doc No': 'nope' }]);
    fireEvent.change(screen.getByLabelText('Order Tracking sheet to compare'), {
      target: { files: [new File(['x'], 'Order Tracking.xlsm')] },
    });
    await waitFor(() => expect(comparePull).toHaveBeenCalledTimes(1));
    expect(comparePull.mock.calls[0][2]).toEqual([{ 'Doc. No.': 'D1', cancel: 'F' }]);
  });

  it('products rows are posted unchanged', async () => {
    renderTab('products');
    const rows = [{ 'Item Code': 'A', Extra: 'keep me' }];
    parseExcelFile.mockResolvedValueOnce(rows);
    fireEvent.change(screen.getByLabelText('Excel file to compare'), {
      target: { files: [new File(['x'], 'p.xlsx')] },
    });
    await waitFor(() => expect(comparePull).toHaveBeenCalledTimes(1));
    expect(comparePull.mock.calls[0][2]).toEqual(rows);
  });
});

describe('fix 2.2: a failed compare shows the error', () => {
  it('toasts the extracted message when the compare request fails', async () => {
    comparePull.mockReset().mockRejectedValue(new Error('Compare failed: body too large'));
    renderTab();
    await screen.findByText(/Columns read: Doc No, Item Code/); // mappings loaded
    await dropListing([{ 'Doc No': 'D1' }]);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Compare failed: body too large'));
  });
});

describe('fix 2.3: a stale result never outlives its file', () => {
  it('a failed parse clears that source\'s previous result', async () => {
    renderTab();
    await screen.findByText(/Columns read: Doc No, Item Code/); // mappings loaded
    await dropListing([{ 'Doc No': 'D1' }]);
    expect(await screen.findByText(/1,234 lines in the window/)).toBeInTheDocument();
    await dropListing(new Error("Sheet 'Master' not found (found sheets: A, B)."), 'Other.xlsm');
    await waitFor(() => expect(screen.queryByText(/1,234 lines in the window/)).not.toBeInTheDocument());
  });

  it('removing the file chip clears that source\'s previous result', async () => {
    renderTab();
    await screen.findByText(/Columns read: Doc No, Item Code/); // mappings loaded
    await dropListing([{ 'Doc No': 'D1' }]);
    expect(await screen.findByText(/1,234 lines in the window/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove Order Listing.xlsm' }));
    await waitFor(() => expect(screen.queryByText(/1,234 lines in the window/)).not.toBeInTheDocument());
  });
});

describe('fix 2.4: no fixed width at 375px', () => {
  it('the dropzone wrapper shrinks and its title wraps (never truncates) with the full text in title', async () => {
    renderTab();
    const titleText = 'Order Listing (macro), sheet Master';
    const title = await screen.findByText(titleText);
    expect(title.className).toMatch(/\bbreak-words\b/);
    expect(title.className).not.toMatch(/\btruncate\b/);
    expect(title.getAttribute('title')).toBe(titleText);
    const wrapper = (screen.getByLabelText('Order Listing sheet to compare').parentElement as HTMLElement)
      .parentElement as HTMLElement;
    expect(wrapper.className).toMatch(/\bmin-w-0\b/);
    expect(wrapper.className).toMatch(/\bw-full\b/);
    expect(wrapper.className).not.toMatch(/\bmin-w-\[|\bmin-w-(?!0\b)\d/);
    expect((wrapper.parentElement as HTMLElement).className).toMatch(/\bmin-w-0\b/);
  });
});
