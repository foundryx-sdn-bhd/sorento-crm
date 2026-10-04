/**
 * ReportPage - the one screen every report is rendered by (AC-C6).
 *
 * The four states a run can be in are what this covers: waiting, nothing to show, refused
 * because the sync cap was hit, and data. The capped one matters most: it is a 422 that is
 * an ANSWER (narrow it, or export instead), so it must read as a warning with an Export
 * button, never as "something went wrong".
 *
 * The page is driven entirely by `GET /reports/{key}` + `POST /run`, so nothing here names
 * a sponsorship column: report #2 renders through the same file.
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
    value,
    onChange,
    options = [],
    placeholder,
  }: {
    value?: string;
    onChange?: (v: string) => void;
    options?: Array<{ value: string; label: string }>;
    placeholder?: string;
  }) => (
    <select aria-label={placeholder} value={value} onChange={(e) => onChange?.(e.target.value)}>
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

import { ReportCappedError, type ReportMeta, type ReportResult } from '@/services/reportService';
import { ONE_PAGE, ReportPage, numericSortingFn, periodIsRunnable } from './ReportPage';

const DEFAULT_VIEW = {
  params: {
    date_basis: 'approved_at',
    period: { kind: 'year' as const, year: 2026 },
    status: ['approved'],
  },
  detail: { columns: ['request_number', 'sales_agent', 'project_value'], order: [] },
  pivot: { rows: 'sales_agent', cols: 'month', measures: ['project_value'] },
};

const META: ReportMeta = {
  key: 'sponsorship',
  title: 'Sponsorship report',
  permission: 'procurement.sponsorship_forms.report',
  params: [
    {
      kind: 'date_basis',
      key: 'date_basis',
      label: 'Date basis',
      default: 'approved_at',
      options: [
        { value: 'approved_at', label: 'Approved' },
        { value: 'request_date', label: 'Form date' },
      ],
    },
    { kind: 'period', key: 'period', label: 'Period', default: { kind: 'year', year: 2026 }, years: [2026, 2025] },
    {
      kind: 'select',
      key: 'status',
      label: 'Status',
      multi: true,
      clearable: true,
      default: ['approved'],
      options: [
        { value: 'approved', label: 'Approved' },
        { value: 'submitted', label: 'Submitted' },
      ],
    },
  ],
  catalog: [
    { key: 'request_number', label: 'PS No', type: 'text', tag: 'dimension' },
    { key: 'sales_agent', label: 'Sales agent', type: 'text', tag: 'dimension' },
    { key: 'month', label: 'Month', type: 'text', tag: 'dimension' },
    { key: 'project_value', label: 'Project value', type: 'money', tag: 'measure' },
  ],
  default_view: DEFAULT_VIEW,
  can_publish: true,
};

const RESULT: ReportResult = {
  key: 'sponsorship',
  period_label: "Jan'26 to Dec'26",
  row_count: 2,
  layouts: {
    detail: {
      key: 'detail',
      title: 'Sponsorships',
      columns: [
        { key: 'request_number', label: 'PS No', type: 'text' },
        { key: 'sales_agent', label: 'Sales agent', type: 'text' },
        { key: 'project_value', label: 'Project value', type: 'money' },
      ],
      column_groups: [],
      rows: [
        { request_number: 'PSSF26-0310', sales_agent: 'CONTACT Y', project_value: '1166830.70' },
        // No value at all: the cell must show "-", never 0.00.
        { request_number: 'PSSF26-0313', sales_agent: 'CONTACT U', project_value: null },
      ],
      totals: { project_value: '1166830.70' },
    },
    summary: {
      key: 'summary',
      title: 'Summary by salesman',
      row_dim: { key: 'sales_agent', label: 'Sales agent' },
      col_dim: { key: 'month', label: 'Month', values: ['2026-01'], value_labels: { '2026-01': "Jan'26" } },
      measures: [{ key: 'project_value', label: 'Project value', type: 'money' }],
      row_values: ['CONTACT Y'],
      cells: { 'CONTACT Y': { '2026-01': { project_value: '1166830.70' } } },
      row_totals: { 'CONTACT Y': { project_value: '1166830.70' } },
      col_totals: { '2026-01': { project_value: '1166830.70' } },
      grand_total: { project_value: '1166830.70' },
    },
  },
};

/** The same result with the Expected-year tick group the sponsorship report renders. */
const WITH_TICK_GROUP: ReportResult = {
  ...RESULT,
  layouts: {
    ...RESULT.layouts,
    detail: {
      ...RESULT.layouts.detail,
      columns: [
        ...RESULT.layouts.detail.columns,
        { key: 'expected_delivery_year__2026', label: '2026', type: 'bool' },
        { key: 'expected_delivery_year__2027', label: '2027', type: 'bool' },
      ],
      column_groups: [
        {
          label: 'Expected year of delivery',
          source: 'expected_delivery_year',
          keys: ['expected_delivery_year__2026', 'expected_delivery_year__2027'],
        },
      ],
      rows: RESULT.layouts.detail.rows.map((row) => ({
        ...row,
        expected_delivery_year__2026: true,
        expected_delivery_year__2027: false,
      })),
    },
  },
};

const EMPTY: ReportResult = {
  ...RESULT,
  row_count: 0,
  layouts: {
    detail: { ...RESULT.layouts.detail, rows: [], totals: {} },
    summary: { ...RESULT.layouts.summary, row_values: [], cells: {}, row_totals: {}, col_totals: {}, grand_total: {} },
  },
};

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <ReportPage reportKey="sponsorship" breadcrumb={[{ label: 'Sponsorship Forms' }, { label: 'Report' }]} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  prefsGate.isLoading = false;
  fetchReportMeta.mockResolvedValue(META);
  runReport.mockResolvedValue(RESULT);
  fetchReportViews.mockResolvedValue({ mine: [], shared: [] });
  exportReport.mockResolvedValue({ download_id: 'd1', filename: 'Sponsorship report-2026.xlsx' });
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('ReportPage', () => {
  it('waits on skeletons rather than an empty screen while the meta loads', () => {
    fetchReportMeta.mockReturnValue(new Promise(() => {}));
    const { container } = render();

    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('renders the detail rows, the money and the blank cell', async () => {
    render();

    expect(await screen.findByText('PSSF26-0310')).toBeInTheDocument();
    expect(screen.getByText('CONTACT Y')).toBeInTheDocument();
    // Once in the row, once in the totals footer.
    expect(screen.getAllByText('1,166,830.70').length).toBe(2);
    // The second row has no project value: a dash, never a zero.
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
  });

  it('reads a zero money cell as "-", the way the client writes it (AC-G5)', async () => {
    runReport.mockResolvedValue({
      ...RESULT,
      layouts: {
        ...RESULT.layouts,
        detail: {
          ...RESULT.layouts.detail,
          rows: [
            { request_number: 'PSSF26-0310', sales_agent: 'CONTACT Y', project_value: '0.00' },
            { request_number: 'PSSF26-0313', sales_agent: 'CONTACT U', project_value: null },
          ],
          totals: {},
        },
      },
    });
    render();

    await screen.findByText('PSSF26-0310');
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
    // Two rows with no money in them, and a dash in each.
    expect(screen.getAllByText('-').length).toBeGreaterThanOrEqual(2);
  });

  it('shows the whole period at once, with no pagination control (AC-G2)', async () => {
    render();

    await screen.findByText('PSSF26-0310');
    expect(screen.queryByRole('button', { name: /Next page|Go to next page/i })).toBeNull();
    expect(screen.queryByText(/Rows per page/i)).toBeNull();
    // Every row of the run is on screen, whatever a default page size would have been.
    expect(screen.getAllByRole('row').length).toBeGreaterThanOrEqual(RESULT.row_count);
  });

  it('holds the rows it already has while it reloads, rather than 60 grey bars (S6 review, M4-02)', async () => {
    // The page size IS the row count (AC-G2), and the shared DataGrid used to draw one
    // skeleton row per page size - so a year of forms turned every reload into 214 grey
    // rows scrolling past. `ONE_PAGE` capped that at 15; M4-02 then removed the swap
    // altogether once a first answer has landed. The rows stay, dimmed, and the wait is
    // the same wait whatever the answer's size. The cap still matters on the one path
    // that reaches the skeleton with rows in hand - see the case below.
    const rows = Array.from({ length: 60 }, (_, index) => ({
      request_number: `PSSF26-${String(index).padStart(4, '0')}`,
      sales_agent: 'CONTACT Y',
      project_value: '1.00',
    }));
    runReport.mockResolvedValueOnce({
      ...RESULT,
      row_count: rows.length,
      layouts: { ...RESULT.layouts, detail: { ...RESULT.layouts.detail, rows } },
    });
    // The reload never answers, so the grid stays on whatever it decided to show.
    runReport.mockReturnValue(new Promise(() => {}));
    const { container } = render();
    await screen.findByText('PSSF26-0000');
    expect(container.querySelectorAll('tbody tr').length).toBe(rows.length);

    fireEvent.change(screen.getByLabelText('Date basis'), { target: { value: 'request_date' } });

    await waitFor(() =>
      expect(container.querySelector('tbody')!.className).toContain('opacity-60'),
    );
    expect(container.querySelectorAll('tbody [data-slot="skeleton"]').length).toBe(0);
    expect(container.querySelectorAll('tbody tr').length).toBe(rows.length);
    expect(screen.getByText('PSSF26-0000')).toBeInTheDocument();
  });

  it('bounds the skeleton at ONE_PAGE while column preferences resolve (S6 review)', async () => {
    // The path that still reaches the skeleton with an answer in hand: the grid holds
    // the rows back until the reader's saved columns arrive, because painting them
    // under the DEFAULT layout and re-laying them out a tick later is a flash. Without
    // the `ONE_PAGE` cap the grid asks for one skeleton row per PAGE SIZE, and on this
    // screen the page size is the row count - 60 grey bars for a 60-row answer.
    prefsGate.isLoading = true;
    const rows = Array.from({ length: 60 }, (_, index) => ({
      request_number: `PSSF26-${String(index).padStart(4, '0')}`,
      sales_agent: 'CONTACT Y',
      project_value: '1.00',
    }));
    runReport.mockResolvedValue({
      ...RESULT,
      row_count: rows.length,
      layouts: { ...RESULT.layouts, detail: { ...RESULT.layouts.detail, rows } },
    });
    const { container } = render();

    await waitFor(() =>
      expect(container.querySelectorAll('tbody [data-slot="skeleton"]').length).toBeGreaterThan(0),
    );
    expect(container.querySelectorAll('tbody tr').length).toBe(ONE_PAGE.pageSize);
    expect(screen.queryByText('PSSF26-0000')).toBeNull();
  });

  it('labels the totals row beside the money, as the workbook does (N3/AC-G9)', async () => {
    const { container } = render();
    await screen.findByText('PSSF26-0310');

    const footer = container.querySelector('tfoot tr') as HTMLTableRowElement;
    const cells = Array.from(footer.querySelectorAll('td'));
    const label = cells.findIndex((cell) => cell.textContent?.trim() === 'Total');
    const money = cells.findIndex((cell) => cell.textContent?.includes('1,166,830.70'));

    expect(label).toBeGreaterThanOrEqual(0);
    expect(label).toBe(money - 1);
  });

  it('names both tabs, with the row count on the detail one', async () => {
    render();

    expect(await screen.findByRole('tab', { name: /Sponsorships \(2\)/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Summary by salesman' })).toBeInTheDocument();
  });

  it('shows the summary pivot with its month header and grand total', async () => {
    render();

    // Radix Tabs selects on mousedown, not on click.
    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Summary by salesman' }), { button: 0 });

    expect(await screen.findByText("Jan'26")).toBeInTheDocument();
    expect(screen.getAllByText('1,166,830.70').length).toBeGreaterThan(0);
  });

  it('says the period is empty rather than showing an empty grid', async () => {
    runReport.mockResolvedValue(EMPTY);
    render();

    expect(await screen.findByText(/No sponsorships in Jan'26 to Dec'26/)).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Sponsorships/ })).not.toBeInTheDocument();
  });

  it('names the empty state after the report it is rendering, not after sponsorships', async () => {
    // One page renders every report, so a hardcoded noun is wrong for report #2. The empty
    // state is named after the detail layout the backend sent.
    runReport.mockResolvedValue({
      ...EMPTY,
      layouts: {
        ...EMPTY.layouts,
        detail: { ...EMPTY.layouts.detail, title: 'Orders' },
      },
    });
    render();

    expect(await screen.findByText(/No orders in Jan'26 to Dec'26/)).toBeInTheDocument();
  });

  it('treats a capped run as an answer: the cap message plus an Export button', async () => {
    runReport.mockRejectedValue(
      new ReportCappedError('This run returns more than 5,000 rows. Narrow the period or export to Excel instead.'),
    );
    render();

    expect(await screen.findByText(/more than 5,000 rows/)).toBeInTheDocument();
    const buttons = screen.getAllByRole('button', { name: /Export to Excel/ });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(exportReport).toHaveBeenCalled());
  });

  it('offers a retry when the run fails outright', async () => {
    runReport.mockRejectedValue(new Error('Server error. Try again or contact support.'));
    render();

    expect(await screen.findByText('Server error. Try again or contact support.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('re-runs against the backend when the date basis changes', async () => {
    render();
    await screen.findByText('PSSF26-0310');

    fireEvent.change(screen.getByLabelText('Date basis'), { target: { value: 'request_date' } });

    await waitFor(() =>
      expect(
        runReport.mock.calls.some((call) => (call[1] as { date_basis: string }).date_basis === 'request_date'),
      ).toBe(true),
    );
  });

  it('exports the view as it stands on screen', async () => {
    render();
    await screen.findByText('PSSF26-0310');

    fireEvent.click(screen.getByRole('button', { name: /Export to Excel/ }));

    await waitFor(() => expect(exportReport).toHaveBeenCalled());
    const [, , view] = exportReport.mock.calls[0] as [string, unknown, { detail: { columns: string[] } }];
    // The visible columns, in the visible order (AC-C5) - not the whole catalog.
    expect(view.detail.columns).toEqual(['request_number', 'sales_agent', 'project_value']);
  });

  it('exports the tick group by its source, never by the year columns it became', async () => {
    // The grid's leaves are `expected_delivery_year__2026`; the backend catalog has no such
    // column, so sending one 422s the export. A view must name the SOURCE, because next
    // year the members are different columns.
    runReport.mockResolvedValue(WITH_TICK_GROUP);
    fetchReportMeta.mockResolvedValue({
      ...META,
      default_view: {
        ...DEFAULT_VIEW,
        detail: {
          columns: [...DEFAULT_VIEW.detail.columns, 'expected_delivery_year'],
          order: [],
        },
      },
    });
    render();
    await screen.findByText('PSSF26-0310');

    fireEvent.click(screen.getByRole('button', { name: /Export to Excel/ }));

    await waitFor(() => expect(exportReport).toHaveBeenCalled());
    const [, , view] = exportReport.mock.calls[0] as [string, unknown, { detail: { columns: string[] } }];
    expect(view.detail.columns).toEqual([
      'request_number',
      'sales_agent',
      'project_value',
      'expected_delivery_year',
    ]);
  });

  it('still renders the report when the saved views cannot be loaded', async () => {
    // Views are an ADDITION to the report, not a precondition for it. Waiting on a query
    // whose error nothing renders leaves the page on skeletons for good.
    fetchReportViews.mockRejectedValue(new Error('Server error. Try again or contact support.'));
    render();

    expect(await screen.findByText('PSSF26-0310')).toBeInTheDocument();
  });

  it('carries no explanatory prose about how many rows have a value', async () => {
    // ADR 1d: the labels carry the meaning, and the blank cells already say which rows
    // have no value.
    render();
    await screen.findByText('PSSF26-0310');

    expect(screen.queryByText(/rows have a/i)).toBeNull();
  });

  it('asks the backend for the whole catalog and hides client-side', async () => {
    render();
    await screen.findByText('PSSF26-0310');

    const [, , view] = runReport.mock.calls[0] as [string, unknown, { detail: { columns: string[] } }];
    expect(view.detail.columns).toEqual([]);
  });
});

describe('periodIsRunnable', () => {
  /**
   * A custom range is picked in two clicks, and Clear empties both ends. Sending a half a
   * range asks the backend a question it can only answer with a 422, which reads on screen
   * as an error the user caused by clicking a calendar.
   */
  it('accepts a whole range and every non-custom period', () => {
    expect(periodIsRunnable({ period: { kind: 'custom', from: '2026-01-01', to: '2026-03-31' } })).toBe(true);
    expect(periodIsRunnable({ period: { kind: 'year', year: 2026 } })).toBe(true);
    expect(periodIsRunnable({ date_basis: 'approved_at', status: ['approved'] })).toBe(true);
  });

  it('refuses a half-picked, cleared or inverted range', () => {
    expect(periodIsRunnable({ period: { kind: 'custom', from: '2026-01-01', to: '' } })).toBe(false);
    expect(periodIsRunnable({ period: { kind: 'custom', from: '', to: '' } })).toBe(false);
    expect(periodIsRunnable({ period: { kind: 'custom', from: '2026-05-01', to: '2026-03-31' } })).toBe(false);
  });
});

describe('numericSortingFn', () => {
  /** Only what a sorting fn is handed: the value under the column id. */
  const row = (value: unknown) => ({ getValue: () => value }) as never;

  it('ranks money by its number, not by its digits as text', () => {
    // Lexically "1166830.70" sorts before "900.00", which reads as a broken column.
    expect(numericSortingFn(row('900.00'), row('1166830.70'), 'project_value')).toBeLessThan(0);
    expect(numericSortingFn(row('1166830.70'), row('900.00'), 'project_value')).toBeGreaterThan(0);
    expect(numericSortingFn(row('900.00'), row('900.00'), 'project_value')).toBe(0);
  });

  it('keeps a blank below every number rather than ranking it as zero', () => {
    expect(numericSortingFn(row(null), row('0.00'), 'project_value')).toBeLessThan(0);
    expect(numericSortingFn(row(null), row(null), 'project_value')).toBe(0);
  });
});
