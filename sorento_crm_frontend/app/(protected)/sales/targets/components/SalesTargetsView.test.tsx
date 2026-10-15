/**
 * Sales > Targets (S1-15, S1-18, S1-22; the S1 hand test of 27 Sep, F3: "why got Active on,
 * supposed to have search bar, then columns, export. it should show a list of team target").
 *
 * The standard list page: line tabs Teams and Agents, then one DataGrid card per tab with the
 * shared toolbar (search, Columns, Export; on Agents a Filters popover holding the Team filter),
 * the standard header row and the standard pager. Each row is one target, whatever its dates
 * (`all`), with its whole range's figures. No date filter, no "No target" rows, no footer lines.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/sales/targets',
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common/PageHeader', () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <header>
      <h1>{title}</h1>
      <div data-testid="header-actions">{actions}</div>
    </header>
  ),
}));
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    clearable?: boolean;
    'aria-label'?: string;
  }) => (
    <select
      id={props.id}
      aria-label={props['aria-label']}
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
    >
      <option value="">{props.placeholder ?? ''}</option>
      {(props.options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const perms = vi.hoisted(() => ({ granted: new Set<string>() }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.granted.has(slug),
}));

const hooks = vi.hoisted(() => ({
  useSalesTargets: vi.fn(),
  usePointRatios: () => ({ data: { rows: [] }, isLoading: false }),
  useSavePointRatio: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useImportDealerTargets: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('../hooks/useSalesTargets', () => hooks);

import SalesTargetsView from './SalesTargetsView';

function row(over: Record<string, unknown> = {}) {
  return {
    target_id: 't1', target_no: 'TGT-000001', name: 'North FY26 H2', subject_kind: 'team',
    sales_agent_id: null, sales_team_id: 'north', subject_label: 'North', team_id: 'north',
    team_name: 'North', left_on: null, members: null,
    metric: 'amount', basis: 'ordered', product_scope: 'brands', scope_labels: ['MOC - Mocha', 'TP - TP Enterprise'],
    period_id: null, period_start: null, period_end: null, start_date: '2026-10-01',
    end_date: '2026-12-31', target_value: 3000, achieved_value: 1500, achieved_pct: 50,
    parent_target_id: null,
    ...over,
  };
}

function withRows(rows: Record<string, unknown>[]) {
  hooks.useSalesTargets.mockReturnValue({
    data: { on: '2026-10-20', rows, unassigned_amount: 12345, no_team_count: 3 },
    isLoading: false, isFetching: false, isError: false, isPlaceholderData: false,
  });
}

function openTab(name: string) {
  const tab = screen.getByRole('tab', { name });
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
}

beforeEach(() => {
  router.push.mockReset();
  perms.granted = new Set(['sales.targets.view', 'sales.targets.add']);
  hooks.useSalesTargets.mockReset();
  withRows([row()]);
});

describe('SalesTargetsView', () => {
  it('opens on the Teams tab, before Agents (S1-15)', () => {
    render(<SalesTargetsView />);
    const tabs = screen.getAllByRole('tab').map((t) => t.textContent?.trim());
    expect(tabs).toEqual(['Teams', 'Agents', 'Dealers']);
    expect(screen.getByRole('tab', { name: 'Teams' }).getAttribute('aria-selected')).toBe('true');
  });

  it('lists every team target, no date: no Active on control, the hook asks for all (F3)', () => {
    render(<SalesTargetsView />);
    expect(screen.queryByLabelText('Active on')).toBeNull();
    expect(screen.queryByText('Active on')).toBeNull();
    expect(hooks.useSalesTargets).toHaveBeenCalledWith({ all: true, subject: 'team' });
  });

  it('has the standard toolbar: search, then Columns, then Export (F3)', () => {
    render(<SalesTargetsView />);
    const search = screen.getByPlaceholderText('Search targets...');
    const columns = screen.getByRole('button', { name: /columns/i });
    const exportButton = screen.getByRole('button', { name: /export/i });
    expect(search.compareDocumentPosition(columns) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(columns.compareDocumentPosition(exportButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Teams has no filter of its own.
    expect(screen.queryByRole('button', { name: /filters/i })).toBeNull();
  });

  it('shows one row per target with its number, name, team, measure chips, dates and figures (F3)', () => {
    render(<SalesTargetsView />);
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim()).filter(Boolean);
    expect(headers).toEqual(['Number', 'Name', 'Team', 'Measures', 'Dates', 'Target', 'Achieved', '%']);
    expect(screen.getByText('TGT-000001')).toBeTruthy();
    expect(screen.getByText('North FY26 H2')).toBeTruthy();
    expect(screen.getByText('1 Oct to 31 Dec 2026')).toBeTruthy();
    expect(screen.getByText('3,000')).toBeTruthy();
    expect(screen.getByText('1,500')).toBeTruthy();
    expect(screen.getByText('50%')).toBeTruthy();
    const measures = screen.getByLabelText('What North FY26 H2 counts');
    expect(within(measures).getByText('Amount')).toBeTruthy();
  });

  it('names a brand target\'s scope as brands in the measure chips (F1)', () => {
    render(<SalesTargetsView />);
    // jsdom lays nothing out, so the chips past the first sit behind "+N"; the measuring row
    // still carries every chip's text.
    expect(document.body.textContent).toContain('2 brands');
  });

  it('has the standard pager, and no No team or Unassigned lines under the list (F3)', () => {
    render(<SalesTargetsView />);
    expect(screen.getByText('Rows per page')).toBeTruthy();
    expect(screen.queryByText('Unassigned')).toBeNull();
    expect(screen.queryByText('No team')).toBeNull();
  });

  it('Set target opens the new target record for the open tab (F4)', () => {
    render(<SalesTargetsView />);
    fireEvent.click(screen.getByRole('button', { name: /set target/i }));
    expect(router.push).toHaveBeenLastCalledWith('/sales/targets/new?kind=team');
    openTab('Agents');
    fireEvent.click(screen.getByRole('button', { name: /set target/i }));
    expect(router.push).toHaveBeenLastCalledWith('/sales/targets/new?kind=agent');
  });

  it('hides Set target without sales.targets.add (S1-18)', () => {
    perms.granted = new Set(['sales.targets.view']);
    render(<SalesTargetsView />);
    expect(screen.queryByRole('button', { name: /set target/i })).toBeNull();
  });

  it('Agents lists agent targets with an Agent and a Team column, and a Team filter in Filters', () => {
    withRows([
      row({
        target_id: 'ta1', target_no: 'TGT-000002', name: 'Ali Oct', subject_kind: 'agent',
        sales_agent_id: 'ali', sales_team_id: null, subject_label: 'ALI - Ali Hassan',
      }),
    ]);
    render(<SalesTargetsView />);
    openTab('Agents');
    expect(hooks.useSalesTargets).toHaveBeenCalledWith({ all: true, subject: 'agent' });
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim()).filter(Boolean);
    expect(headers).toEqual(['Number', 'Name', 'Agent', 'Team', 'Measures', 'Dates', 'Target', 'Achieved', '%']);
    expect(screen.getByText('ALI - Ali Hassan')).toBeTruthy();

    fireEvent.keyDown(screen.getByRole('button', { name: /filters/i }), { key: 'Enter' });
    const team = screen.getByLabelText('Team') as HTMLSelectElement;
    expect(Array.from(team.options).map((o) => o.textContent)).toContain('No team');
    fireEvent.change(team, { target: { value: 'none' } });
    expect(hooks.useSalesTargets).toHaveBeenCalledWith({ all: true, subject: 'agent', salesTeamId: 'none' });
  });

  it('sends the search to the server', async () => {
    render(<SalesTargetsView />);
    fireEvent.change(screen.getByPlaceholderText('Search targets...'), { target: { value: 'TGT-000001' } });
    await waitFor(() =>
      expect(hooks.useSalesTargets).toHaveBeenCalledWith({ all: true, subject: 'team', query: 'TGT-000001' }),
    );
  });

  it('says when there is no target yet', () => {
    withRows([]);
    render(<SalesTargetsView />);
    expect(screen.getByText('No team targets yet')).toBeTruthy();
  });

  it('Dealers lists dealer targets with Counts, Pays to and the current commission (lane SALES-TARGET-DEALER)', () => {
    perms.granted = new Set(['sales.targets.view', 'sales.targets.add', 'sales.targets.edit']);
    withRows([
      row({
        target_id: 'td1', target_no: 'TGT-000214', name: 'Dealer A 2026 quarterly', subject_kind: 'dealer',
        sales_team_id: null, subject_label: 'Dealer A Sdn Bhd', team_id: null, team_name: null,
        product_scope: 'all', scope_labels: [], account_labels: ['A/C I', 'A/C III'],
        account_codes: ['300-A0123', '300-A0124'], payout_label: 'A/C I', commission_earned: 5400,
      }),
    ]);
    render(<SalesTargetsView />);
    openTab('Dealers');
    expect(hooks.useSalesTargets).toHaveBeenCalledWith({ all: true, subject: 'dealer' });
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim()).filter(Boolean);
    expect(headers).toEqual([
      'Number', 'Name', 'Dealer', 'Counts', 'Pays to', 'Measures', 'Dates', 'Target',
      'Achieved (provisional, incl. tax)', '%', 'Commission',
    ]);
    expect(screen.getByText('Dealer A Sdn Bhd')).toBeTruthy();
    expect(screen.getByText('5,400')).toBeTruthy();
    // Import and Point ratio are list actions on the Dealers tab only.
    expect(screen.getAllByText(/Import from Excel|Point ratio|Actions/).length).toBeGreaterThan(0);
  });
});

