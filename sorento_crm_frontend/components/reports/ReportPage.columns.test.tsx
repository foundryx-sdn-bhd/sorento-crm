/**
 * Applying a saved view REPLACES the columns on screen (AC-C3, "selecting one applies it
 * atomically).
 *
 * Its own file because the Columns panel and the Views menu are both Radix dropdowns, and
 * reaching their items means rendering the menus flat - which would change what every other
 * ReportPage spec sees on screen.
 *
 * The bug it pins: the first toggle AFTER a view was applied started from the override the
 * user had made BEFORE it, so the pre-view hidden columns came back.
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

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

// Flat menus: the Columns checkboxes and the Views entries are then real controls rather
// than popover content jsdom never opens.
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuLabel: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuItem: ({
    children,
    onClick,
    disabled,
  }: React.PropsWithChildren<{ onClick?: () => void; disabled?: boolean }>) => (
    <button type="button" onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
  DropdownMenuCheckboxItem: ({
    children,
    checked,
    onCheckedChange,
  }: React.PropsWithChildren<{ checked?: boolean; onCheckedChange?: (v: boolean) => void }>) => (
    <label>
      <input
        type="checkbox"
        checked={Boolean(checked)}
        onChange={(e) => onCheckedChange?.(e.target.checked)}
      />
      {children}
    </label>
  ),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: () => <div />,
}));
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: () => <div />,
}));
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

import type { ReportMeta, ReportResult, ReportView } from '@/services/reportService';
import { ReportPage } from './ReportPage';

const COLUMNS = ['request_number', 'sales_agent', 'project_value'];

const DEFAULT_VIEW = {
  params: { date_basis: 'approved_at', period: { kind: 'year' as const, year: 2026 } },
  detail: { columns: COLUMNS, order: COLUMNS },
  pivot: { rows: 'sales_agent', cols: 'month', measures: ['project_value'] },
};

const META: ReportMeta = {
  key: 'sponsorship',
  title: 'Sponsorship report',
  permission: 'procurement.sponsorship_forms.report',
  params: [],
  catalog: [
    { key: 'request_number', label: 'PS No', type: 'text', tag: 'dimension' },
    { key: 'sales_agent', label: 'Sales agent', type: 'text', tag: 'dimension' },
    { key: 'project_value', label: 'Project value', type: 'money', tag: 'measure' },
  ],
  default_view: DEFAULT_VIEW,
  can_publish: false,
};

const RESULT: ReportResult = {
  key: 'sponsorship',
  period_label: "Jan'26 to Dec'26",
  row_count: 1,
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
      rows: [{ request_number: 'PSSF26-0310', sales_agent: 'CONTACT Y', project_value: '900.00' }],
      totals: { project_value: '900.00' },
    },
    summary: {
      key: 'summary',
      title: 'Summary by salesman',
      row_dim: { key: 'sales_agent', label: 'Sales agent' },
      col_dim: { key: 'month', label: 'Month', values: ['2026-01'] },
      measures: [{ key: 'project_value', label: 'Project value', type: 'money' }],
      row_values: ['CONTACT Y'],
      cells: { 'CONTACT Y': { '2026-01': { project_value: '900.00' } } },
      row_totals: { 'CONTACT Y': { project_value: '900.00' } },
      col_totals: { '2026-01': { project_value: '900.00' } },
      grand_total: { project_value: '900.00' },
    },
  },
};

/** A saved view that shows all three columns. */
const ALL_THREE: ReportView = {
  id: 'v-all',
  name: 'Everything',
  is_shared: false,
  is_default: false,
  owner_name: 'You',
  view: DEFAULT_VIEW,
};

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <ReportPage reportKey="sponsorship" breadcrumb={[{ label: 'Report' }]} />
    </QueryClientProvider>,
  );
}

const columnToggle = (label: string) => screen.getByLabelText(label);

beforeEach(() => {
  vi.clearAllMocks();
  fetchReportMeta.mockResolvedValue(META);
  runReport.mockResolvedValue(RESULT);
  fetchReportViews.mockResolvedValue({ mine: [ALL_THREE], shared: [] });
  exportReport.mockResolvedValue({ download_id: 'd1', filename: 'x.xlsx' });
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('ReportPage columns after a view is applied', () => {
  it('a hidden column does not come back on the next toggle', async () => {
    render();
    await screen.findByText('PSSF26-0310');

    // Hide one column, then apply a saved view that shows all three, then hide a different
    // one. What the export carries must be the VIEW's columns minus the last toggle.
    fireEvent.click(columnToggle('Sales agent'));
    fireEvent.click(screen.getByText('Everything'));
    await waitFor(() => expect(columnToggle('Sales agent')).toBeChecked());

    fireEvent.click(columnToggle('Project value'));
    fireEvent.click(screen.getByRole('button', { name: /Export to Excel/ }));

    await waitFor(() => expect(exportReport).toHaveBeenCalled());
    const [, , view] = exportReport.mock.calls[0] as [
      string,
      unknown,
      { detail: { columns: string[] } },
    ];
    expect(view.detail.columns).toEqual(['request_number', 'sales_agent']);
  });
});
