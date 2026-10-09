/**
 * CA-2 round 2 (tester-first RED): ReportPage on a report whose meta carries `pivot_rows_by`
 * (Top asks: {topics: 'topic', products: 'product'}). Changing the dataset param (Count by)
 * makes the pivot rows `pivot_rows_by[newValue]`: the summary re-runs with those rows and its
 * title follows. When `pivot_rows_by` is null (Chat overview) a switch leaves the rows alone.
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

function metaFor(pivotRowsBy: Record<string, string> | null, first: string, second: string) {
  return {
    key: 'zzt_report',
    title: 'ZZT report',
    permission: 'chatbot.analytics.view',
    dataset_param: 'count_by',
    pivot_rows_by: pivotRowsBy,
    params: [
      { kind: 'period', key: 'period', label: 'Period', default: PERIOD, years: [2026] },
      {
        kind: 'select',
        key: 'count_by',
        label: 'Count by',
        multi: false,
        clearable: false,
        default: [first],
        options: [
          { value: first, label: first },
          { value: second, label: second },
        ],
      },
    ],
    catalog: [
      { key: 'topic', label: 'Topic', type: 'text', tag: 'dimension', unavailable_for: [second] },
      { key: 'outcome', label: 'Outcome', type: 'text', tag: 'dimension', unavailable_for: [] },
      { key: 'product', label: 'Product', type: 'text', tag: 'dimension', unavailable_for: [first] },
      { key: 'month', label: 'Month', type: 'text', tag: 'dimension', unavailable_for: [] },
      { key: 'asks', label: 'Asks', type: 'count', tag: 'measure', unavailable_for: [] },
    ],
    default_view: {
      params: { period: PERIOD, count_by: [first] },
      detail: { columns: [], order: [] },
      pivot: { rows: pivotRowsBy ? pivotRowsBy[first] : 'outcome', cols: 'month', measures: ['asks'] },
    },
    can_publish: false,
  } as unknown as ReportMeta;
}

const TOPICS_META = metaFor({ topics: 'topic', products: 'product' }, 'topics', 'products');
const OVERVIEW_META = metaFor(null, 'asks', 'products');

type RunArgs = [string, Record<string, unknown>, { pivot: { rows: string } }];

function resultFor(rows: string) {
  const label = rows.charAt(0).toUpperCase() + rows.slice(1);
  return {
    key: 'zzt_report',
    period_label: '2026',
    // Non-zero: the summary tab strip, where the title prints, renders only for a run with rows.
    row_count: 1,
    layouts: {
      detail: { key: 'detail', title: 'Asks', columns: [], column_groups: [], rows: [], totals: {} },
      summary: {
        key: 'summary',
        title: `${label} by month`,
        row_dim: { key: rows, label },
        col_dim: { key: 'month', label: 'Month', values: [] },
        measures: [],
        row_values: [],
        cells: {},
        row_totals: {},
        col_totals: {},
        grand_total: {},
      },
    },
  } as unknown as ReportResult;
}

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <ReportPage reportKey="zzt_report" breadcrumb={[{ label: 'Chatbot' }, { label: 'ZZT report' }]} />
    </QueryClientProvider>,
  );
}

const rowsSent = () => runReport.mock.calls.map((c) => (c as RunArgs)[2].pivot.rows);

beforeEach(() => {
  vi.clearAllMocks();
  prefsGate.isLoading = false;
  runReport.mockImplementation(async (...args: unknown[]) => resultFor((args as RunArgs)[2].pivot.rows));
  fetchReportViews.mockResolvedValue({ mine: [], shared: [] });
  exportReport.mockResolvedValue({ download_id: 'd1', filename: 'x.xlsx' });
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('ReportPage pivot rows follow the dataset param when meta.pivot_rows_by is set', () => {
  it('opens on the default rows, then runs with product rows after Count by is Products', async () => {
    fetchReportMeta.mockResolvedValue(TOPICS_META);
    render();
    await waitFor(() => expect(rowsSent().length).toBeGreaterThan(0));
    expect(rowsSent().every((r) => r === 'topic')).toBe(true);

    const before = runReport.mock.calls.length;
    fireEvent.change(screen.getByLabelText('report-param-count_by'), { target: { value: 'products' } });

    await waitFor(() => expect(runReport.mock.calls.length).toBeGreaterThan(before));
    const after = rowsSent().slice(before);
    expect(after[after.length - 1]).toBe('product');
    expect(await screen.findByText(/Product by month/)).toBeTruthy();
  });

  it('goes back to topic rows when Count by is Topics again', async () => {
    fetchReportMeta.mockResolvedValue(TOPICS_META);
    render();
    await waitFor(() => expect(rowsSent().length).toBeGreaterThan(0));
    fireEvent.change(screen.getByLabelText('report-param-count_by'), { target: { value: 'products' } });
    await waitFor(() => expect(rowsSent().includes('product')).toBe(true));

    const before = runReport.mock.calls.length;
    fireEvent.change(screen.getByLabelText('report-param-count_by'), { target: { value: 'topics' } });

    await waitFor(() => expect(runReport.mock.calls.length).toBeGreaterThan(before));
    const after = rowsSent().slice(before);
    expect(after[after.length - 1]).toBe('topic');
    expect(await screen.findByText(/Topic by month/)).toBeTruthy();
  });

  it('exports the summary with the product rows', async () => {
    fetchReportMeta.mockResolvedValue(TOPICS_META);
    render();
    await waitFor(() => expect(rowsSent().length).toBeGreaterThan(0));
    fireEvent.change(screen.getByLabelText('report-param-count_by'), { target: { value: 'products' } });
    await waitFor(() => expect(rowsSent().includes('product')).toBe(true));

    fireEvent.click(screen.getAllByRole('button', { name: /Export to Excel/i })[0]);

    await waitFor(() => expect(exportReport).toHaveBeenCalled());
    expect(JSON.stringify(exportReport.mock.calls[0])).toContain('"rows":"product"');
  });
});

describe('ReportPage leaves the rows alone when meta.pivot_rows_by is null (Chat overview)', () => {
  it('a Count by switch keeps the rows the view already had', async () => {
    fetchReportMeta.mockResolvedValue(OVERVIEW_META);
    render();
    await waitFor(() => expect(rowsSent().length).toBeGreaterThan(0));

    const before = runReport.mock.calls.length;
    fireEvent.change(screen.getByLabelText('report-param-count_by'), { target: { value: 'products' } });

    await waitFor(() => expect(runReport.mock.calls.length).toBeGreaterThan(before));
    const after = rowsSent().slice(before);
    expect(after.every((r) => r === 'outcome')).toBe(true);
  });
});
