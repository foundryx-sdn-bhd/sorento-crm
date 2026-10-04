/**
 * PullCompareTab - DO-COMPARE-SIM fix round 3: a file dropped while the mappings are loading
 * clears that source's previous result instead of leaving it on screen. Hooks mocked so the
 * loading flag can be flipped after a first successful compare.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const state = vi.hoisted(() => ({ loading: false }));
const MAPPINGS = {
  items: [
    { kind: 'order_listing', sheet_name: 'Master', updated_at: null,
      columns: [{ excel_header: 'Doc No', transform: 'text', field: 'doc_no' }] },
    { kind: 'order_tracking', sheet_name: 'Master', updated_at: null,
      columns: [{ excel_header: 'Doc. No.', transform: 'text', field: 'doc_no' }] },
  ],
};
const RESULT = {
  summary: { filename: 'f.xlsm', compared_at: '2026-09-30T00:00:00Z', total: 5, matched: 5, different: 0,
    only_in_excel: 0, only_in_pull: 0 },
  differences: [], only_in_excel: [], only_in_pull: [], rows_in_window: 1234, ignored_outside_window: 0,
  source: 'lines',
};
vi.mock('../hooks/useAutocountPull', () => ({
  useComparePull: () => ({
    mutate: (_v: unknown, opts?: { onSuccess?: (d: unknown) => void }) => opts?.onSuccess?.(RESULT),
    isPending: false,
  }),
  useCompareMappings: () =>
    state.loading ? { data: undefined, isLoading: true } : { data: MAPPINGS, isLoading: false },
  useSaveCompareMapping: () => ({ mutate: vi.fn(), isPending: false }),
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

beforeEach(() => {
  state.loading = false;
  parseExcelFile.mockReset();
});

describe('PullCompareTab file dropped while the mappings load', () => {
  it('clears that source\'s previous result', async () => {
    const client = new QueryClient();
    const tree = () =>
      React.createElement(QueryClientProvider, { client },
        React.createElement(PullCompareTab, { jobId: 'job-1', entity: 'delivery_orders' }));
    const { rerender } = render(tree());
    parseExcelFile.mockResolvedValueOnce([{ 'Doc No': 'D1' }]);
    fireEvent.change(screen.getByLabelText('Order Listing sheet to compare'), {
      target: { files: [new File(['x'], 'one.xlsm')] },
    });
    expect(await screen.findByText(/1,234 lines in the window/)).toBeInTheDocument();

    state.loading = true;
    rerender(tree());
    fireEvent.change(screen.getByLabelText('Order Listing sheet to compare'), {
      target: { files: [new File(['x'], 'two.xlsm')] },
    });
    await waitFor(() => expect(screen.queryByText(/1,234 lines in the window/)).not.toBeInTheDocument());
    expect(parseExcelFile).toHaveBeenCalledTimes(1);
  });
});
