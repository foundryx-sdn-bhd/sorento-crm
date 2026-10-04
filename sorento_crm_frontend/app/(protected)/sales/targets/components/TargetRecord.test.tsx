/**
 * The target record, `/sales/targets/{id}` and `/sales/targets/new` (UAC S1-18, S1-23 to
 * S1-28; the S1 hand test of 27 Sep, F1, F4, F5, F6).
 *
 * ONE record view for create, view and edit, no modal: a header card, then line tabs in the
 * order Details, Periods, Agents (a team target only), Commission. Create opens the same view
 * empty with the defaults Amount, Ordered, All products, today to month end, split off. Edit is
 * an item in the gear dropdown. Every pick is the system SearchableSelect or
 * SearchableMultiSelect, and a picked category, product or brand always shows its name.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/sales/targets/t1',
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/helpers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/helpers')>()),
  todayMalaysiaYyyyMmDd: () => '2026-10-20',
}));

vi.mock('@/components/common/DetailActions', () => ({
  default: ({
    pagerNode,
    actions,
    primary,
    pendingAction,
  }: {
    pagerNode?: React.ReactNode;
    actions?: { key: string; label: string; run: () => void }[];
    primary?: React.ReactNode;
    pendingAction?: React.ReactNode;
  }) => (
    <div data-testid="detail-actions">
      {pagerNode}
      <div data-testid="gear">
        {(actions ?? []).map((a) => (
          <button key={a.key} type="button" onClick={() => a.run()}>
            {a.label}
          </button>
        ))}
      </div>
      {pendingAction ?? primary}
    </div>
  ),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    disabled?: boolean;
  }) => (
    <select
      id={props.id}
      value={props.value}
      disabled={props.disabled}
      onChange={(e) => props.onChange(e.target.value)}
    >
      <option value="">None</option>
      {(props.options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

// The real component's fallback, kept: a picked value with no known option renders its raw
// value as the chip, which is exactly how a UUID reached the screen (F5).
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    id?: string;
    value: string[];
    onChange: (v: string[]) => void;
    options?: { value: string; label: string }[];
    selectedOptions?: { value: string; label: string }[];
  }) => {
    const known = new Map(
      [...(props.options ?? []), ...(props.selectedOptions ?? [])].map((o) => [o.value, o.label]),
    );
    return (
      <div role="group" aria-labelledby={`${props.id}-label`} id={props.id}>
        {props.value.map((v) => (
          <span key={v} data-testid="chip">
            {known.get(v) ?? v}
          </span>
        ))}
        {(props.options ?? []).map((o) => (
          <label key={o.value}>
            <input
              type="checkbox"
              checked={props.value.includes(o.value)}
              onChange={(e) =>
                props.onChange(
                  e.target.checked ? [...props.value, o.value] : props.value.filter((x) => x !== o.value),
                )
              }
            />
            {o.label}
          </label>
        ))}
      </div>
    );
  },
}));

vi.mock('@/components/ui/date-range-picker', () => ({
  DateRangePicker: (props: {
    from?: string | null;
    to?: string | null;
    onChange: (next: { from: string | null; to: string | null }) => void;
  }) => (
    <span>
      <input
        aria-label="Start date"
        value={props.from ?? ''}
        onChange={(e) => props.onChange({ from: e.target.value || null, to: props.to ?? null })}
      />
      <input
        aria-label="End date"
        value={props.to ?? ''}
        onChange={(e) => props.onChange({ from: props.from ?? null, to: e.target.value || null })}
      />
    </span>
  ),
}));

const perms = vi.hoisted(() => ({ granted: new Set<string>() }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.granted.has(slug),
}));

const create = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const patchHeader = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const patchPeriod = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const addChild = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const productSearch = vi.hoisted(() => vi.fn());
const hooks = vi.hoisted(() => ({
  useSalesTarget: vi.fn(),
  useSalesTargets: vi.fn(),
  useSalesTargetOptions: vi.fn(),
  useTargetProductSearch: () => productSearch,
  useCreateSalesTarget: () => create,
  usePatchSalesTarget: () => patchHeader,
  usePatchSalesTargetPeriod: () => patchPeriod,
  useCreateTargetChild: () => addChild,
}));
vi.mock('../hooks/useSalesTargets', () => hooks);

const duplicate = vi.hoisted(() => vi.fn());
vi.mock('../actions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../actions')>()),
  useSalesTargetActions: () => ({
    actions: [
      { key: 'sales_target.duplicate', label: 'Duplicate', run: duplicate },
      { key: 'sales_target.delete', label: 'Delete target', kind: 'destructive', run: vi.fn() },
    ],
    dialogs: null,
    pending: null,
  }),
}));

import TargetRecord from './TargetRecord';

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;
const PRODUCT_ID = '6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f';

const OPTIONS = {
  agents: [
    { id: 'ali', code: 'ALI', label: 'ALI - Ali Hassan', team_id: 'north', team_name: 'North' },
  ],
  teams: [
    {
      id: 'north', name: 'North', is_active: true,
      members: [
        { sales_agent_id: 'ali', label: 'ALI - Ali Hassan', valid_from: null, valid_to: null },
        { sales_agent_id: 'mei', label: 'MEI - Tan Mei Ling', valid_from: null, valid_to: null },
      ],
    },
  ],
  categories: [{ id: 'cat1', label: 'BAS - Basins', parent_category_id: null }],
  brands: [
    { id: 'b1', label: 'MOC - Mocha' },
    { id: 'b2', label: 'TP - TP Enterprise' },
  ],
};

function detail(over: Record<string, unknown> = {}) {
  return {
    id: 't1', target_no: 'TGT-000001', name: 'Ali Q4 basins', subject_kind: 'agent',
    sales_agent_id: 'ali', sales_team_id: null, subject_label: 'ALI - Ali Hassan',
    subject_team_name: 'North', parent: null, metric: 'amount', basis: 'ordered',
    product_scope: 'products', start_date: '2026-10-01', end_date: '2026-12-31',
    split_every: null, split_unit: null, counts_label: 'Ordered',
    scope: [{ id: PRODUCT_ID, kind: 'product', label: 'BSN-001 - Countertop basin' }],
    periods: [
      { id: 'p1', period_start: '2026-10-01', period_end: '2026-12-31', target_value: 1000, achieved_value: 500, achieved_pct: 50, is_current: true },
    ],
    children: [], members_without_figure: [], child_count: 0,
    commission_method: 'none', tiers: [],
    created_at: '2026-09-26T01:00:00', updated_at: '2026-09-26T01:00:00',
    ...over,
  };
}

function openTab(name: string) {
  const tab = screen.getByRole('tab', { name });
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
}

function tabNames() {
  return screen.getAllByRole('tab').map((t) => t.textContent?.trim());
}

beforeEach(() => {
  router.push.mockReset();
  perms.granted = new Set(['sales.targets.view', 'sales.targets.add', 'sales.targets.edit', 'sales.targets.delete']);
  for (const m of [create, patchHeader, patchPeriod, addChild]) {
    m.mutateAsync.mockReset();
    m.mutateAsync.mockResolvedValue({ id: 'new1' });
  }
  hooks.useSalesTarget.mockReturnValue({ data: detail(), isLoading: false, isError: false });
  hooks.useSalesTargets.mockReturnValue({ data: { on: '2026-10-20', rows: [], unassigned_amount: 0, no_team_count: 0 } });
  hooks.useSalesTargetOptions.mockReturnValue({ data: OPTIONS });
  productSearch.mockResolvedValue([]);
});

describe('TargetRecord: create (F4)', () => {
  it('opens as the record, empty, with the defaults, and no modal', () => {
    render(<TargetRecord preset={{ kind: 'agent' }} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('heading', { name: 'New target' })).toBeTruthy();
    expect(tabNames()).toEqual(['Details', 'Periods', 'Commission']);
    expect((screen.getByLabelText('Measure') as HTMLSelectElement).value).toBe('amount');
    expect((screen.getByLabelText('Counts') as HTMLSelectElement).value).toBe('ordered');
    expect((screen.getByLabelText('Applies to') as HTMLSelectElement).value).toBe('all');
    expect((screen.getByLabelText('Start date') as HTMLInputElement).value).toBe('2026-10-20');
    expect((screen.getByLabelText('End date') as HTMLInputElement).value).toBe('2026-10-31');
    expect((screen.getByRole('switch', { name: 'Split' }) as HTMLButtonElement).getAttribute('aria-checked')).toBe('false');
    // Cancel and Save sit in the header card, not a dialog footer.
    const header = screen.getByTestId('record-header');
    expect(within(header).getByRole('button', { name: 'Save' })).toBeTruthy();
    expect(within(header).getByRole('button', { name: 'Cancel' })).toBeTruthy();
  });

  it('saves an agent target and opens its record', async () => {
    render(<TargetRecord preset={{ kind: 'agent' }} />);
    fireEvent.change(screen.getByLabelText('Who'), { target: { value: 'ali' } });
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Ali Oct' } });
    fireEvent.change(screen.getByLabelText(/Target figure/), { target: { value: '5000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(create.mutateAsync).toHaveBeenCalledTimes(1));
    expect(create.mutateAsync).toHaveBeenCalledWith({
      subject_kind: 'agent', sales_agent_id: 'ali', target_value: 5000, name: 'Ali Oct',
      metric: 'amount', basis: 'ordered', product_scope: 'all',
      start_date: '2026-10-20', end_date: '2026-10-31',
    });
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/sales/targets/new1'));
  });

  it('a team target takes each agent figure on the Agents tab and shows their sum', async () => {
    render(<TargetRecord preset={{ kind: 'team', subjectId: 'north' }} />);
    expect(tabNames()).toEqual(['Details', 'Periods', 'Agents', 'Commission']);
    expect((screen.getByLabelText('Who') as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'North Oct' } });
    openTab('Agents');
    const agents = screen.getByRole('region', { name: 'Agents' });
    fireEvent.change(within(agents).getByLabelText('ALI - Ali Hassan figure'), { target: { value: '600' } });
    fireEvent.change(within(agents).getByLabelText('MEI - Tan Mei Ling figure'), { target: { value: '400' } });
    expect(within(agents).getByText('1,000')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(create.mutateAsync).toHaveBeenCalledTimes(1));
    expect(create.mutateAsync.mock.calls[0][0]).toMatchObject({
      subject_kind: 'team', sales_team_id: 'north',
      agent_figures: [
        { sales_agent_id: 'ali', target_value: 600 },
        { sales_agent_id: 'mei', target_value: 400 },
      ],
    });
  });

  it('Applies to offers Brand; picked brands show their names and are sent (F1)', async () => {
    render(<TargetRecord preset={{ kind: 'agent' }} />);
    const applies = screen.getByLabelText('Applies to') as HTMLSelectElement;
    expect(Array.from(applies.options).map((o) => o.textContent)).toEqual([
      'None', 'All products', 'Categories', 'Products', 'Brands',
    ]);
    fireEvent.change(applies, { target: { value: 'brands' } });
    fireEvent.click(screen.getByLabelText('MOC - Mocha'));
    expect(screen.getByTestId('chip').textContent).toBe('MOC - Mocha');
    fireEvent.change(screen.getByLabelText('Who'), { target: { value: 'ali' } });
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Ali Mocha' } });
    fireEvent.change(screen.getByLabelText(/Target figure/), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(create.mutateAsync).toHaveBeenCalledTimes(1));
    expect(create.mutateAsync.mock.calls[0][0]).toMatchObject({
      product_scope: 'brands', brand_ids: ['b1'],
    });
  });

  it('says what Split does in one line under the dates (F6)', () => {
    render(<TargetRecord preset={{ kind: 'agent' }} />);
    const dates = screen.getByRole('region', { name: 'Dates' });
    expect(
      within(dates).getByText(
        'Split breaks the dates into periods of N days, weeks or months, each with its own figure. Off: one figure for the whole range.',
      ),
    ).toBeTruthy();
  });

  it('cannot save a half-empty range, and says so in plain words (F6)', () => {
    render(<TargetRecord preset={{ kind: 'agent' }} />);
    fireEvent.change(screen.getByLabelText('Who'), { target: { value: 'ali' } });
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Ali Oct' } });
    fireEvent.change(screen.getByLabelText(/Target figure/), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '' } });
    expect(screen.getByText('Pick both a start date and an end date.')).toBeTruthy();
    const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    expect(create.mutateAsync).not.toHaveBeenCalled();
  });
});

describe('TargetRecord: view and edit (F4, F5)', () => {
  it('heads the record, then tabs Details, Periods, Commission; Edit and Duplicate in the gear', () => {
    render(<TargetRecord id="t1" />);
    const header = screen.getByTestId('record-header');
    expect(within(header).getByText('TGT-000001')).toBeTruthy();
    expect(within(header).getByRole('heading', { name: 'Ali Q4 basins' })).toBeTruthy();
    expect(tabNames()).toEqual(['Details', 'Periods', 'Commission']);
    const gear = screen.getByTestId('gear');
    expect(within(gear).getByRole('button', { name: 'Edit' })).toBeTruthy();
    expect(within(gear).getByRole('button', { name: 'Duplicate' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('shows the picked products by code and name in read mode, never an id (F5)', () => {
    render(<TargetRecord id="t1" />);
    const counts = screen.getByRole('region', { name: 'What counts' });
    expect(within(counts).getByText('BSN-001 - Countertop basin')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(UUID);
  });

  it('keeps the picked products named in edit mode, never an id (F5)', () => {
    render(<TargetRecord id="t1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Edit' }));
    expect(screen.getByTestId('chip').textContent).toBe('BSN-001 - Countertop basin');
    expect(document.body.textContent).not.toMatch(UUID);
  });

  it('edits in place in the same tabs and saves only what changed', async () => {
    render(<TargetRecord id="t1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Edit' }));
    expect(tabNames()).toEqual(['Details', 'Periods', 'Commission']);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Ali Q4 all basins' } });
    fireEvent.change(screen.getByLabelText('Applies to'), { target: { value: 'brands' } });
    fireEvent.click(screen.getByLabelText('TP - TP Enterprise'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchHeader.mutateAsync).toHaveBeenCalledTimes(1));
    expect(patchHeader.mutateAsync).toHaveBeenCalledWith({
      targetId: 't1',
      name: 'Ali Q4 all basins',
      product_scope: 'brands',
      category_ids: [],
      product_ids: [],
      brand_ids: ['b2'],
    });
  });

  it('refuses to save an edit whose range lost an end, in plain words (F6)', () => {
    render(<TargetRecord id="t1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '' } });
    expect(screen.getByText('Pick both a start date and an end date.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers no Edit without sales.targets.edit', () => {
    perms.granted = new Set(['sales.targets.view']);
    render(<TargetRecord id="t1" />);
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  });
});

// ------------------------------------------------------------------------------------------
// Fix lane round 3 (the owner's retest of 27 Sep 14:25): figures are edited in Edit mode, no
// row pencils (F1); commission tiers in Edit mode (F2); the agent target record (F3).
// ------------------------------------------------------------------------------------------

const Q4_PERIODS = [
  { id: 'p1', period_start: '2026-10-01', period_end: '2026-10-31', target_value: 1000, achieved_value: 500, achieved_pct: 50, is_current: true, commission_earned: null, bonus_earned: null },
  { id: 'p2', period_start: '2026-11-01', period_end: '2026-11-30', target_value: 1000, achieved_value: 0, achieved_pct: 0, is_current: false, commission_earned: null, bonus_earned: null },
  { id: 'p3', period_start: '2026-12-01', period_end: '2026-12-31', target_value: 1000, achieved_value: 0, achieved_pct: 0, is_current: false, commission_earned: null, bonus_earned: null },
];

function splitAgent(over: Record<string, unknown> = {}) {
  return detail({ split_every: 1, split_unit: 'month', periods: Q4_PERIODS, ...over });
}

function teamTarget(over: Record<string, unknown> = {}) {
  return detail({
    id: 'team1', target_no: 'TGT-000003', name: '2026 Q4 Target', subject_kind: 'team',
    sales_agent_id: null, sales_team_id: 'north', subject_label: 'North', product_scope: 'all', scope: [],
    split_every: 1, split_unit: 'month',
    periods: Q4_PERIODS.map((p) => ({ ...p, id: `t${p.id}`, target_value: 1000 })),
    children: [
      {
        target_id: 'c1', target_no: 'TGT-000004', sales_agent_id: 'ali', label: 'ALI - Ali Hassan',
        periods: [
          { id: 'a1', period_start: '2026-10-01', target_value: 600 },
          { id: 'a2', period_start: '2026-11-01', target_value: 600 },
          { id: 'a3', period_start: '2026-12-01', target_value: 600 },
        ],
      },
      {
        target_id: 'c2', target_no: 'TGT-000005', sales_agent_id: 'cin', label: 'CIN - CONTACT W',
        periods: [
          { id: 'b1', period_start: '2026-10-01', target_value: 400 },
          { id: 'b2', period_start: '2026-11-01', target_value: 400 },
          { id: 'b3', period_start: '2026-12-01', target_value: 400 },
        ],
      },
    ],
    members_without_figure: [{ sales_agent_id: 'mei', label: 'MEI - Tan Mei Ling' }],
    child_count: 2,
    ...over,
  });
}

function openEdit() {
  fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Edit' }));
}

describe('TargetRecord round 3, F1: figures are edited in Edit mode', () => {
  it('Periods in read mode shows values only: no pencil, no input', () => {
    hooks.useSalesTarget.mockReturnValue({ data: splitAgent(), isLoading: false, isError: false });
    render(<TargetRecord id="t1" />);
    openTab('Periods');
    const periods = screen.getByRole('region', { name: 'Periods' });
    expect(within(periods).getByText('Current')).toBeTruthy();
    expect(within(periods).queryAllByRole('button')).toHaveLength(0);
    expect(within(periods).queryAllByRole('spinbutton')).toHaveLength(0);
    expect(within(periods).getAllByText('1,000')).toHaveLength(3);
  });

  it('Periods in Edit mode takes a figure per period and Save sends every changed one in one request', async () => {
    hooks.useSalesTarget.mockReturnValue({ data: splitAgent(), isLoading: false, isError: false });
    render(<TargetRecord id="t1" />);
    openEdit();
    openTab('Periods');
    const periods = screen.getByRole('region', { name: 'Periods' });
    const inputs = within(periods).getAllByRole('spinbutton');
    expect(inputs).toHaveLength(3);
    fireEvent.change(inputs[0], { target: { value: '1500' } });
    fireEvent.change(inputs[2], { target: { value: '2500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchHeader.mutateAsync).toHaveBeenCalledTimes(1));
    expect(patchHeader.mutateAsync).toHaveBeenCalledWith({
      targetId: 't1',
      figures: [
        { period_id: 'p1', target_value: 1500 },
        { period_id: 'p3', target_value: 2500 },
      ],
    });
    expect(patchPeriod.mutateAsync).not.toHaveBeenCalled();
  });

  it('Cancel discards the typed figures', () => {
    hooks.useSalesTarget.mockReturnValue({ data: splitAgent(), isLoading: false, isError: false });
    render(<TargetRecord id="t1" />);
    openEdit();
    openTab('Periods');
    fireEvent.change(within(screen.getByRole('region', { name: 'Periods' })).getAllByRole('spinbutton')[1], {
      target: { value: '9' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    const periods = screen.getByRole('region', { name: 'Periods' });
    expect(within(periods).queryAllByRole('spinbutton')).toHaveLength(0);
    expect(within(periods).getAllByText('1,000')).toHaveLength(3);
    expect(patchHeader.mutateAsync).not.toHaveBeenCalled();
  });

  it('no row pencil anywhere, in read or Edit mode', () => {
    hooks.useSalesTarget.mockReturnValue({ data: teamTarget(), isLoading: false, isError: false });
    render(<TargetRecord id="team1" />);
    const pencil = /edit (period )?figure|edit figure for/i;
    for (const tab of ['Periods', 'Agents']) {
      openTab(tab);
      expect(screen.queryAllByRole('button', { name: pencil })).toHaveLength(0);
    }
    openEdit();
    for (const tab of ['Periods', 'Agents']) {
      openTab(tab);
      expect(screen.queryAllByRole('button', { name: pencil })).toHaveLength(0);
    }
  });

  it('Agents in read mode: each agent per period, the team total, no buttons', () => {
    hooks.useSalesTarget.mockReturnValue({ data: teamTarget(), isLoading: false, isError: false });
    render(<TargetRecord id="team1" />);
    openTab('Agents');
    const agents = screen.getByRole('region', { name: 'Agents' });
    expect(within(agents).getByRole('link', { name: 'ALI - Ali Hassan' })).toBeTruthy();
    expect(within(agents).getAllByText('600')).toHaveLength(3);
    expect(within(agents).getByText('No figure yet')).toBeTruthy();
    expect(within(agents).getByText('Team target')).toBeTruthy();
    expect(within(agents).queryAllByRole('button')).toHaveLength(0);
    expect(within(agents).queryAllByRole('spinbutton')).toHaveLength(0);
  });

  it('Agents in Edit mode: an input per agent per period, the team total per period live, one Save', async () => {
    hooks.useSalesTarget.mockReturnValue({ data: teamTarget(), isLoading: false, isError: false });
    render(<TargetRecord id="team1" />);
    openEdit();
    openTab('Agents');
    const agents = screen.getByRole('region', { name: 'Agents' });
    // Three agents (one with no figure yet) by three months.
    expect(within(agents).getAllByRole('spinbutton')).toHaveLength(9);
    fireEvent.change(within(agents).getByLabelText('ALI - Ali Hassan, 1 Nov 2026'), { target: { value: '900' } });
    fireEvent.change(within(agents).getByLabelText('MEI - Tan Mei Ling, 1 Dec 2026'), { target: { value: '50' } });
    const totals = within(agents).getByTestId('team-totals');
    // Oct 1,000; Nov 900 + 400; Dec 1,000 + 50.
    expect(within(totals).getByText('1,000')).toBeTruthy();
    expect(within(totals).getByText('1,300')).toBeTruthy();
    expect(within(totals).getByText('1,050')).toBeTruthy();

    openTab('Periods');
    const periods = screen.getByRole('region', { name: 'Periods' });
    expect(within(periods).queryAllByRole('spinbutton')).toHaveLength(0);
    expect(within(periods).getByText('1,300')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchHeader.mutateAsync).toHaveBeenCalledTimes(1));
    expect(patchHeader.mutateAsync).toHaveBeenCalledWith({
      targetId: 'team1',
      figures: [{ period_id: 'a2', target_value: 900 }],
      new_agents: [
        {
          sales_agent_id: 'mei',
          figures: [{ period_start: '2026-12-01', target_value: 50 }],
        },
      ],
    });
    expect(addChild.mutateAsync).not.toHaveBeenCalled();
  });
});

describe('TargetRecord round 3, F2: commission tiers in Edit mode only', () => {
  it('read mode with no tiers: "No commission" and Add tier opens Edit mode with a tier row', () => {
    render(<TargetRecord id="t1" />);
    openTab('Commission');
    let commission = screen.getByRole('region', { name: 'Commission' });
    expect(within(commission).getByText('No commission')).toBeTruthy();
    expect(within(commission).queryAllByRole('spinbutton')).toHaveLength(0);
    fireEvent.click(within(commission).getByRole('button', { name: 'Add tier' }));
    commission = screen.getByRole('region', { name: 'Commission' });
    expect(within(commission).getByLabelText('Tier 1 from %')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy();
  });

  it('adds tiers in Edit mode and saves them with how they pay', async () => {
    render(<TargetRecord id="t1" />);
    openEdit();
    openTab('Commission');
    const commission = screen.getByRole('region', { name: 'Commission' });
    fireEvent.click(within(commission).getByRole('button', { name: 'Add tier' }));
    fireEvent.change(within(commission).getByLabelText('Tier 1 from %'), { target: { value: '0' } });
    fireEvent.change(within(commission).getByLabelText('Tier 1 rate (% of RM)'), { target: { value: '2' } });
    fireEvent.click(within(commission).getByRole('button', { name: 'Add tier' }));
    fireEvent.change(within(commission).getByLabelText('Tier 2 from %'), { target: { value: '100' } });
    fireEvent.change(within(commission).getByLabelText('Tier 2 rate (% of RM)'), { target: { value: '4' } });
    fireEvent.change(within(commission).getByLabelText('Tier 2 bonus (RM)'), { target: { value: '500' } });
    expect((within(commission).getByLabelText('How tiers pay') as HTMLSelectElement).value).toBe('marginal');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchHeader.mutateAsync).toHaveBeenCalledTimes(1));
    expect(patchHeader.mutateAsync).toHaveBeenCalledWith({
      targetId: 't1',
      commission_method: 'marginal',
      tiers: [
        { from_pct: 0, rate: 2, bonus_amount: null },
        { from_pct: 100, rate: 4, bonus_amount: 500 },
      ],
    });
  });

  it('read mode shows saved tiers as rows, no inputs; Edit mode removes one', async () => {
    hooks.useSalesTarget.mockReturnValue({
      data: detail({
        commission_method: 'marginal',
        tiers: [
          { from_pct: 0, rate: 2, bonus_amount: null },
          { from_pct: 100, rate: 4, bonus_amount: 500 },
        ],
      }),
      isLoading: false, isError: false,
    });
    render(<TargetRecord id="t1" />);
    openTab('Commission');
    let commission = screen.getByRole('region', { name: 'Commission' });
    expect(within(commission).getByText('Higher rate above each threshold only')).toBeTruthy();
    expect(within(commission).getByText('100%')).toBeTruthy();
    expect(within(commission).queryAllByRole('spinbutton')).toHaveLength(0);
    expect(within(commission).queryByRole('button', { name: /remove tier/i })).toBeNull();
    openEdit();
    commission = screen.getByRole('region', { name: 'Commission' });
    fireEvent.click(within(commission).getByRole('button', { name: 'Remove tier 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchHeader.mutateAsync).toHaveBeenCalledTimes(1));
    expect(patchHeader.mutateAsync).toHaveBeenCalledWith({
      targetId: 't1',
      commission_method: 'marginal',
      tiers: [{ from_pct: 0, rate: 2, bonus_amount: null }],
    });
  });

  it('removing every tier turns the commission off', async () => {
    hooks.useSalesTarget.mockReturnValue({
      data: detail({ commission_method: 'retroactive', tiers: [{ from_pct: 0, rate: 2, bonus_amount: null }] }),
      isLoading: false, isError: false,
    });
    render(<TargetRecord id="t1" />);
    openEdit();
    openTab('Commission');
    fireEvent.click(screen.getByRole('button', { name: 'Remove tier 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchHeader.mutateAsync).toHaveBeenCalledTimes(1));
    expect(patchHeader.mutateAsync).toHaveBeenCalledWith({ targetId: 't1', commission_method: 'none', tiers: [] });
  });

  it('without sales.targets.edit there is no Add tier', () => {
    perms.granted = new Set(['sales.targets.view']);
    render(<TargetRecord id="t1" />);
    openTab('Commission');
    const commission = screen.getByRole('region', { name: 'Commission' });
    expect(within(commission).getByText('No commission')).toBeTruthy();
    expect(within(commission).queryByRole('button', { name: /add tier/i })).toBeNull();
  });
});

describe('TargetRecord round 3, F3: the agent target record', () => {
  const PARENT = teamTarget();
  function childRecord() {
    return splitAgent({
      id: 'c2', target_no: 'TGT-000005', name: '2026 Q4 Target', sales_agent_id: 'cin',
      subject_label: 'CIN - CONTACT W', product_scope: 'all', scope: [],
      parent: { id: 'team1', name: '2026 Q4 Target', target_no: 'TGT-000003' },
      periods: Q4_PERIODS.map((p) => ({ ...p, id: `b${p.id.slice(1)}`, target_value: 400 })),
    });
  }
  beforeEach(() => {
    hooks.useSalesTarget.mockImplementation((id: string | null) => ({
      data: id === 'team1' ? PARENT : id === 'c2' ? childRecord() : undefined,
      isLoading: false,
      isError: false,
    }));
  });

  it('heads the record "Agent target, part of" its team target, with a link', () => {
    render(<TargetRecord id="c2" />);
    const header = screen.getByTestId('record-header');
    expect(within(header).getByText(/Agent target, part of/)).toBeTruthy();
    expect(within(header).getByRole('link', { name: '2026 Q4 Target' }).getAttribute('href')).toBe('/sales/targets/team1');
    expect(tabNames()).toEqual(['Details', 'Periods', 'Team target', 'Commission']);
  });

  it('the Team target tab shows the parent: name, what counts, dates, split, periods, and opens it', () => {
    render(<TargetRecord id="c2" />);
    openTab('Team target');
    const team = screen.getByRole('region', { name: 'Team target' });
    expect(within(team).getByText('2026 Q4 Target')).toBeTruthy();
    expect(within(team).getByText('TGT-000003')).toBeTruthy();
    expect(within(team).getByText('Amount (RM)')).toBeTruthy();
    expect(within(team).getByText('Ordered')).toBeTruthy();
    expect(within(team).getByText('All products')).toBeTruthy();
    expect(within(team).getByText('1 Oct 2026 to 31 Dec 2026')).toBeTruthy();
    expect(within(team).getByText('Every month')).toBeTruthy();
    expect(within(team).getAllByText('1,000')).toHaveLength(3);
    expect(within(team).getByRole('link', { name: 'Open team target' }).getAttribute('href')).toBe('/sales/targets/team1');
  });

  it('inherited fields stay read-only in Edit mode with one "Set on the team target" line', () => {
    render(<TargetRecord id="c2" />);
    expect(screen.queryByText(/Set on 2026 Q4 Target/)).toBeNull();
    openEdit();
    expect(screen.queryByLabelText('Measure')).toBeNull();
    expect(screen.queryByLabelText('Applies to')).toBeNull();
    expect(screen.queryByLabelText('Start date')).toBeNull();
    expect(screen.queryByRole('switch', { name: 'Split' })).toBeNull();
    expect(screen.getAllByText('Set on the team target')).toHaveLength(1);
    expect(screen.queryByText(/Set on 2026 Q4 Target/)).toBeNull();
  });

  it("edits the agent's own figures per period in Edit mode and saves them in one request", async () => {
    render(<TargetRecord id="c2" />);
    openEdit();
    openTab('Periods');
    const inputs = within(screen.getByRole('region', { name: 'Periods' })).getAllByRole('spinbutton');
    expect(inputs).toHaveLength(3);
    fireEvent.change(inputs[1], { target: { value: '800' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchHeader.mutateAsync).toHaveBeenCalledTimes(1));
    expect(patchHeader.mutateAsync).toHaveBeenCalledWith({
      targetId: 'c2',
      figures: [{ period_id: 'b2', target_value: 800 }],
    });
  });
});
