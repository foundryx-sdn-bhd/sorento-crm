/**
 * ReportPage on a report with `datasets_by` (CA-1): changing the dataset param (Count by)
 * must never send a pivot choice the new dataset lacks. The run uses the default view's
 * value for that slot and the page shows `data-testid="summary-choice-note"` naming it.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/procurement-management/sponsorship-forms/report',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null, toString: () => '' }),
}));

// DataGrid persists column prefs through this hook (which fires network); without the stub
// the grid renders skeletons forever and no row can be asserted.
const prefsGate = vi.hoisted(() => ({ isLoading: false }));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: prefsGate.isLoading }),
}));

// The filter bar's controls are the shared searchable selects. Native equivalents here:
// the assertions are about which STATE the page is in, not about popover mechanics.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    id,
    value,
    onChange,
    options = [],
  }: {
    id?: string;
    value?: string;
    onChange?: (v: string) => void;
    options?: Array<{ value: string; label: string }>;
  }) => (
    <select aria-label={id} value={value} onChange={(e) => onChange?.(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: ({
    value,
    onChange,
    options = [],
    placeholder,
  }: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: Array<{ value: string; label: string }>;
    placeholder?: string;
  }) => (
    <div aria-label={placeholder ?? 'multi-select'}>
      {options.map((o) => (
        <label key={o.value}>
          <input
            type="checkbox"
            aria-label={o.label}
            checked={value.includes(o.value)}
            onChange={(e) =>
              onChange(e.target.checked ? [...value, o.value] : value.filter((v) => v !== o.value))
            }
          />
          {o.label}
        </label>
      ))}
    </div>
  ),
}));

// Container pulls SettingsProvider context this unit test does not need.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const fetchReportMeta = vi.fn();
const runReport = vi.fn();
const fetchReportViews = vi.fn();
const exportReport = vi.fn();

vi.mock('@/services/reportService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/reportService')>();
  return {
    ...actual,
    fetchReportMeta: (...args: unknown[]) => fetchReportMeta(...args),
    runReport: (...args: unknown[]) => runReport(...args),
    fetchReportViews: (...args: unknown[]) => fetchReportViews(...args),
    exportReport: (...args: unknown[]) => exportReport(...args),
    createReportView: vi.fn(),
    deleteReportView: vi.fn(),
    publishReportView: vi.fn(),
    setDefaultReportView: vi.fn(),
  };
});

import type { ReportMeta, ReportResult } from '@/services/reportService';
import { ReportPage } from './ReportPage';

const PERIOD = { kind: 'year' as const, year: 2026 };
const DEFAULT_VIEW = {
  params: { period: PERIOD, count_by: ['asks'] },
  detail: { columns: [], order: [] },
  pivot: { rows: 'outcome', cols: 'week', measures: ['asks', 'failed'] },
};
const SAVED_VIEW = {
  id: 'v1',
  name: 'By product',
  is_shared: false,
  is_default: true,
  owner_name: 'Me',
  view: {
    params: { period: PERIOD, count_by: ['products'] },
    detail: { columns: [], order: [] },
    pivot: { rows: 'product', cols: 'week', measures: ['asks', 'named'] },
  },
};

const META = {
  key: 'chat_overview',
  title: 'Chat overview',
  permission: 'chatbot.analytics.view',
  dataset_param: 'count_by',
  params: [
    { kind: 'period', key: 'period', label: 'Period', default: PERIOD, years: [2026] },
    {
      kind: 'select',
      key: 'count_by',
      label: 'Count by',
      multi: false,
      clearable: false,
      default: ['asks'],
      options: [
        { value: 'asks', label: 'Asks' },
        { value: 'products', label: 'Products' },
      ],
    },
  ],
  catalog: [
    { key: 'outcome', label: 'Outcome', type: 'text', tag: 'dimension', unavailable_for: [] },
    { key: 'week', label: 'Week', type: 'text', tag: 'dimension', unavailable_for: [] },
    { key: 'product', label: 'Product', type: 'text', tag: 'dimension', unavailable_for: ['asks'] },
    { key: 'asks', label: 'Asks', type: 'count', tag: 'measure', unavailable_for: [] },
    { key: 'failed', label: 'Failed', type: 'count', tag: 'measure', unavailable_for: [] },
    { key: 'named', label: 'Named', type: 'count', tag: 'measure', unavailable_for: ['asks'] },
  ],
  default_view: DEFAULT_VIEW,
  can_publish: false,
} as unknown as ReportMeta;

const RESULT = {
  key: 'chat_overview',
  period_label: '2026',
  row_count: 0,
  layouts: {
    detail: { key: 'detail', title: 'Asks', columns: [], column_groups: [], rows: [], totals: {} },
    summary: {
      key: 'summary',
      title: 'Outcome by week',
      row_dim: { key: 'outcome', label: 'Outcome' },
      col_dim: { key: 'week', label: 'Week', values: [] },
      measures: [],
      row_values: [],
      cells: {},
      row_totals: {},
      col_totals: {},
      grand_total: {},
    },
  },
} as unknown as ReportResult;

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <ReportPage reportKey="chat_overview" breadcrumb={[{ label: 'Chatbot' }, { label: 'Chat overview' }]} />
    </QueryClientProvider>,
  );
}

const pivotsSent = () => runReport.mock.calls.map((c) => (c[2] as { pivot: { rows: string; measures: string[] } }).pivot);

beforeEach(() => {
  vi.clearAllMocks();
  prefsGate.isLoading = false;
  fetchReportMeta.mockResolvedValue(META);
  runReport.mockResolvedValue(RESULT);
  fetchReportViews.mockResolvedValue({ mine: [SAVED_VIEW], shared: [] });
  exportReport.mockResolvedValue({ download_id: 'd1', filename: 'x.xlsx' });
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('ReportPage dataset switch', () => {
  it('falls back to the default view value for a slot the new dataset lacks, with a note', async () => {
    render();
    await waitFor(() => expect(pivotsSent().some((p) => p.rows === 'product')).toBe(true));
    expect(screen.queryByTestId('summary-choice-note')).not.toBeInTheDocument();

    const before = runReport.mock.calls.length;
    fireEvent.change(screen.getByLabelText('report-param-count_by'), { target: { value: 'asks' } });

    await waitFor(() => expect(runReport.mock.calls.length).toBeGreaterThan(before));
    const after = pivotsSent().slice(before);
    for (const pivot of after) {
      expect(pivot.rows).not.toBe('product');
      expect(pivot.measures).not.toContain('named');
    }
    const last = after[after.length - 1];
    expect(last.rows).toBe('outcome');
    expect(last.measures).toEqual(['asks', 'failed']);
    expect((await screen.findByTestId('summary-choice-note')).textContent).toContain('Product');
  });
});
