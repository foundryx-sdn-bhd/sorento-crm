/**
 * A column order the user dragged SURVIVES the next visit (AC-C1).
 *
 * The bug it pins: on reload the grid read the saved order back, applied it, and then
 * immediately PUT the report's DEFAULT order over it, so the reorder lasted exactly one
 * page load. Show/hide survived, because the visibility apply is what clobbered the order.
 *
 * Its own file because it runs the REAL `useListingColumnPreferences` against a mocked
 * column-config service (every other ReportPage spec stubs the hook out), and because
 * dnd-kit cannot drag in jsdom, so the DndContext is replaced by a passthrough that hands
 * the test the grid's own `onDragEnd`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { DragEndEvent } from '@dnd-kit/core';

vi.mock('next/navigation', () => ({
  usePathname: () => '/procurement-management/sponsorship-forms/report',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null, toString: () => '' }),
}));

const mockDrag: { end: ((event: DragEndEvent) => void) | null } = { end: null };

vi.mock('@dnd-kit/core', async () => {
  const actual = await vi.importActual<typeof import('@dnd-kit/core')>('@dnd-kit/core');
  return {
    ...actual,
    DndContext: ({
      children,
      onDragEnd,
    }: React.PropsWithChildren<{ onDragEnd: (event: DragEndEvent) => void }>) => {
      mockDrag.end = onDragEnd;
      return <>{children}</>;
    },
  };
});

vi.mock('@dnd-kit/sortable', async () => {
  const actual = await vi.importActual<typeof import('@dnd-kit/sortable')>('@dnd-kit/sortable');
  return {
    ...actual,
    SortableContext: ({ children }: React.PropsWithChildren) => <>{children}</>,
    useSortable: () => ({
      attributes: {},
      listeners: {},
      setNodeRef: vi.fn(),
      transform: null,
      transition: undefined,
      isDragging: false,
    }),
  };
});

vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(),
  upsertUserListColumnConfig: vi.fn(),
  resetUserListColumnConfig: vi.fn(),
}));

vi.mock('@/components/common/SearchableSelect', () => ({ SearchableSelect: () => <div /> }));
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: () => <div />,
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const fetchReportMeta = vi.fn();
const runReport = vi.fn();
const fetchReportViews = vi.fn();

vi.mock('@/services/reportService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/reportService')>();
  return {
    ...actual,
    fetchReportMeta: (...args: unknown[]) => fetchReportMeta(...args),
    runReport: (...args: unknown[]) => runReport(...args),
    fetchReportViews: (...args: unknown[]) => fetchReportViews(...args),
    exportReport: vi.fn(),
    createReportView: vi.fn(),
    deleteReportView: vi.fn(),
    publishReportView: vi.fn(),
    setDefaultReportView: vi.fn(),
  };
});

import * as prefsService from '@/lib/listing-column-preferences/listColumnPreferencesService';
import type { UserListColumnConfigPayload } from '@/lib/listing-column-preferences/listColumnPreferencesService';
import type { ReportMeta, ReportResult } from '@/services/reportService';
import { ReportPage } from './ReportPage';

/** The report's own default order. The saved one below deliberately differs. */
const COLUMNS = ['request_number', 'sales_agent', 'project_value'];
const SAVED_ORDER = ['sales_agent', 'request_number', 'project_value'];

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

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <ReportPage reportKey="sponsorship" breadcrumb={[{ label: 'Report' }]} />
    </QueryClientProvider>,
  );
}

/** The header row the user reads, top row only (this layout has no column groups). */
function headerOrder(): string[] {
  const rows = screen.getAllByRole('row');
  return Array.from(rows[0].querySelectorAll('th'))
    .map((th) => (th.textContent ?? '').trim())
    .filter(Boolean);
}

/** Longer than the hook's 800ms save debounce, so a pending write has landed. */
const settle = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 1000));
  });
};

const savedPayloads = () =>
  vi.mocked(prefsService.upsertUserListColumnConfig).mock.calls.map(
    (call) => call[1] as UserListColumnConfigPayload,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockDrag.end = null;
  fetchReportMeta.mockResolvedValue(META);
  runReport.mockResolvedValue(RESULT);
  fetchReportViews.mockResolvedValue({ mine: [], shared: [] });
  vi.mocked(prefsService.getUserListColumnConfig).mockResolvedValue({
    listing_key: 'procurement.sponsorship_forms.report::detail',
    config: {
      version: 1,
      columnOrder: SAVED_ORDER,
      columnVisibility: { request_number: true, sales_agent: true, project_value: true },
      // A width the user dragged. It differs from the column's own size on purpose: the
      // second PUT the bug report captured only fires once SOMETHING about the applied
      // config differs from the code defaults, which is true of any real saved row.
      columnSizing: { project_value: 220 },
    },
  });
  vi.mocked(prefsService.upsertUserListColumnConfig).mockImplementation(
    async (listingKey: string, payload: UserListColumnConfigPayload) => ({
      listing_key: listingKey,
      config: payload,
    }),
  );
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('ReportPage detail column order', () => {
  it('opens on the saved order and writes nothing back', async () => {
    render();
    await screen.findByText('PSSF26-0310');
    await settle();

    expect(savedPayloads()).toEqual([]);
    expect(headerOrder()).toEqual(['Sales agent', 'PS No', 'Project value']);
  });

  it('saves a dragged order exactly once, and keeps it on screen', async () => {
    render();
    await screen.findByText('PSSF26-0310');
    await settle();

    // Drag Project value onto Sales agent: ['project_value','sales_agent','request_number'].
    act(() => {
      mockDrag.end?.({
        active: { id: 'project_value' },
        over: { id: 'sales_agent' },
      } as DragEndEvent);
    });
    await settle();

    expect(headerOrder()).toEqual(['Project value', 'Sales agent', 'PS No']);
    const payloads = savedPayloads();
    expect(payloads).toHaveLength(1);
    expect(payloads[0].columnOrder).toEqual(['project_value', 'sales_agent', 'request_number']);
  });
});

describe('ReportPage detail columns while a saved view is applied', () => {
  /** The report opens on this: a published default view, which the menu names. */
  const SHARED_DEFAULT = {
    id: 'view-1',
    name: 'Management',
    is_shared: true,
    is_default: true,
    owner_name: 'Other Person',
    view: DEFAULT_VIEW,
  };

  beforeEach(() => {
    fetchReportViews.mockResolvedValue({ mine: [], shared: [SHARED_DEFAULT] });
  });

  it('follows the view, not the column preferences saved under the listing key', async () => {
    // The preferences row is one per LAYOUT, not one per view, so applying view B wrote B's
    // columns under the same key the report default reads. The next visit then opened on
    // B's columns while the menu named the default view, and Save and Export recorded B's
    // columns under that name.
    render();
    await screen.findByText('PSSF26-0310');
    await settle();

    expect(headerOrder()).toEqual(['PS No', 'Sales agent', 'Project value']);
    expect(vi.mocked(prefsService.getUserListColumnConfig)).not.toHaveBeenCalled();
  });

  it('writes nothing back when the columns are changed under a view', async () => {
    render();
    await screen.findByText('PSSF26-0310');
    await settle();

    act(() => {
      mockDrag.end?.({
        active: { id: 'project_value' },
        over: { id: 'sales_agent' },
      } as DragEndEvent);
    });
    await settle();

    // Moved on screen (the view is what the grid follows), saved nowhere.
    expect(headerOrder()).toEqual(['PS No', 'Project value', 'Sales agent']);
    expect(savedPayloads()).toEqual([]);
  });
});
