/**
 * `/sales/opportunities/{id}` detail page (UAC S2-11, S2-12, S2-13; plan section 16; fix
 * round 2 F5-F7).
 *
 * Stage moves (`available_transitions`) live in the gear, before Delete; Lost opens a dialog
 * with a required reason select and blocks Confirm without it; Won opens a dialog with an
 * optional sales order select; every section (the Details tab's Opportunity card, the
 * Products tab) renders with an empty state; a RecordNavigation is present.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/components/common/RecordNavigation', () => ({
  RecordNavigation: () => <nav aria-label="record navigation" />,
}));

// The gear renders its items as buttons inside `data-testid="gear"` (same pattern
// `SalesTeamDetail.test.tsx` uses), so a test can tell an item apart from the primary CTA.
vi.mock('@/components/common/DetailActions', () => ({
  default: ({
    pagerNode,
    actions,
    primary,
    pendingAction,
  }: {
    pagerNode?: React.ReactNode;
    actions?: { key: string; label: string; run: () => void; disabled?: boolean }[];
    primary?: React.ReactNode;
    pendingAction?: React.ReactNode;
  }) => (
    <div data-testid="detail-actions">
      {pagerNode}
      <div data-testid="gear">
        {(actions ?? []).map((a) => (
          <button key={a.key} type="button" disabled={a.disabled} onClick={() => a.run()}>
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
    'aria-label'?: string;
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    fetchOptions?: (q: string) => Promise<{ value: string; label: string }[]>;
    selectedOption?: { value: string; label: string };
  }) => {
    const [options, setOptions] = React.useState(props.options ?? []);
    React.useEffect(() => {
      if (props.fetchOptions) props.fetchOptions('').then(setOptions);
    }, []);
    // A `selectedOption` (async mode's "preset value shows its own label" fallback,
    // Phase 3 fix2 nit) merges in even before a fetch resolves - the same reason the
    // real component carries it.
    const merged =
      props.selectedOption && !options.some((o) => o.value === props.selectedOption!.value)
        ? [props.selectedOption, ...options]
        : options;
    return (
      <select
        aria-label={props['aria-label'] ?? props.id ?? 'select'}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
      >
        <option value="" />
        {merged.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  },
}));

const perms = vi.hoisted(() => ({ granted: new Set<string>(['sales.opportunities.edit']) }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.granted.has(slug),
}));

const hooks = vi.hoisted(() => ({
  useSalesOpportunity: vi.fn(),
  useSalesOpportunities: vi.fn(),
  useSalesOpportunityMeta: vi.fn(),
  useSalesOpportunityAgentOptions: vi.fn(),
  useSaveSalesOpportunity: vi.fn(),
}));
vi.mock('../../hooks/useSalesOpportunities', () => hooks);

// A thin stand-in for the real hook (F6): it turns `transitions` into gear items the same
// way `actions.tsx` does, so this file can exercise the wiring (canEdit-gated, disabled
// while editing, `onTransition` called on click) without also mocking `useDeferredAction`
// for a Delete item none of these tests exercise.
const actionsMock = vi.hoisted(() => ({ useSalesOpportunityActions: vi.fn() }));
vi.mock('../../actions', () => actionsMock);

const service = vi.hoisted(() => ({
  getSalesOpportunitySalesOrderOptions: vi.fn(),
  getSalesOpportunityCustomerOptions: vi.fn(),
  getSalesOpportunityProductOptions: vi.fn(),
}));
vi.mock('../../services/salesOpportunityService', () => service);

import SalesOpportunityDetail from './SalesOpportunityDetail';

const save = { mutateAsync: vi.fn(), isPending: false };

function detail(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'opp-1',
    opportunity_no: 'OPP-000001',
    title: 'ZZT Deal',
    customer_id: 'cust-1',
    customer_name: 'ZZT Dealer',
    prospect_name: null,
    sales_agent_id: null,
    sales_agent_label: null,
    status_id: 'st-new',
    stage_key: 'new',
    stage_label: 'New',
    win_probability: 10,
    outcome: 'open',
    expected_amount: '1000.00',
    expected_close_date: '2026-11-01',
    lost_reason: null,
    lost_reason_label: null,
    sales_order_id: null,
    sales_order_no: null,
    source: 'crm',
    created_by_label: 'Jane Doe',
    created_at: '2026-09-26T00:00:00',
    updated_at: '2026-09-26T00:00:00',
    stage_changed_at: '2026-09-26T00:00:00',
    lines: [],
    available_transitions: [
      { to_status_id: 'st-qualified', key: 'qualified', label: 'Qualified' },
      { to_status_id: 'st-won', key: 'won', label: 'Won' },
      { to_status_id: 'st-lost', key: 'lost', label: 'Lost' },
    ],
    ...over,
  };
}

beforeEach(() => {
  save.mutateAsync.mockReset();
  save.mutateAsync.mockResolvedValue({});
  hooks.useSaveSalesOpportunity.mockReturnValue(save);
  hooks.useSalesOpportunity.mockReturnValue({
    data: detail(),
    isLoading: false,
    isError: false,
  });
  hooks.useSalesOpportunityMeta.mockReturnValue({
    data: {
      stages: [],
      lost_reasons: [
        { value: 'price', label: 'Price' },
        { value: 'competitor', label: 'Competitor' },
      ],
    },
  });
  hooks.useSalesOpportunities.mockReturnValue({
    data: { data: [detail()], pagination: { total: 1, page: 1, limit: 200 }, empty: false },
  });
  hooks.useSalesOpportunityAgentOptions.mockReturnValue({ data: [] });
  perms.granted = new Set(['sales.opportunities.edit']);
  actionsMock.useSalesOpportunityActions.mockReset();
  actionsMock.useSalesOpportunityActions.mockImplementation(
    (
      _opportunity: unknown,
      options: {
        transitions?: { to_status_id: string; key: string; label: string }[];
        onTransition?: (t: { to_status_id: string; key: string; label: string }) => void;
      } = {},
    ) => {
      const { transitions = [], onTransition } = options;
      const actions = transitions.map((t) => ({
        key: `sales_opportunity.stage.${t.to_status_id}`,
        label: t.label,
        run: () => onTransition?.(t),
      }));
      return { actions, dialogs: null, pending: null };
    },
  );
  service.getSalesOpportunitySalesOrderOptions.mockReset();
  service.getSalesOpportunitySalesOrderOptions.mockResolvedValue([
    { id: 'so-1', so_number: 'SO-000001', customer_id: 'cust-1', customer_name: 'ZZT Dealer', order_date: '2026-10-01' },
  ]);
  service.getSalesOpportunityCustomerOptions.mockReset();
  service.getSalesOpportunityCustomerOptions.mockResolvedValue({ items: [], prospect: null, blocked: null });
  service.getSalesOpportunityProductOptions.mockReset();
  service.getSalesOpportunityProductOptions.mockResolvedValue([
    { value: 'p1', label: 'ZZT-001 - ZZT Basin' },
  ]);
});

describe('SalesOpportunityDetail', () => {
  it('renders a RecordNavigation', () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.getByLabelText('record navigation')).toBeTruthy();
  });

  it('offers only the stages named in available_transitions, as gear items', () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    const gear = within(screen.getByTestId('gear'));
    expect(gear.getByRole('button', { name: 'Qualified' })).toBeTruthy();
    expect(gear.getByRole('button', { name: 'Won' })).toBeTruthy();
    expect(gear.getByRole('button', { name: 'Lost' })).toBeTruthy();
    expect(gear.queryByRole('button', { name: 'Proposal' })).toBeNull();
    expect(gear.queryByRole('button', { name: 'Negotiation' })).toBeNull();
  });

  it('fix round2 F6: no stage items in the gear without sales.opportunities.edit', () => {
    perms.granted = new Set();
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(within(screen.getByTestId('gear')).queryByRole('button')).toBeNull();
  });

  it('fix round2 F6: a non-lost/won transition (Qualify) runs immediately, no dialog', async () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Qualified' }));
    await waitFor(() =>
      expect(save.mutateAsync).toHaveBeenCalledWith({ id: 'opp-1', status_id: 'st-qualified' }),
    );
    expect(screen.queryByLabelText(/lost reason/i)).toBeNull();
  });

  it('fix round2 F6: no stage items are reachable while an edit session is open (the header states one intent: Save or Cancel)', () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect(screen.queryByTestId('gear')).toBeNull();
  });

  it('choosing Lost opens a dialog with a required reason select and blocks Confirm without it', async () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Lost' }));
    expect(screen.getByRole('heading', { name: 'Lost' })).toBeTruthy();
    const reasonSelect = await screen.findByLabelText(/lost reason/i);
    expect(reasonSelect).toBeTruthy();

    const confirm = screen.getByRole('button', { name: /^confirm$/i });
    fireEvent.click(confirm);
    expect(save.mutateAsync).not.toHaveBeenCalled();

    fireEvent.change(reasonSelect, { target: { value: 'price' } });
    fireEvent.click(confirm);
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalled());
  });

  it('choosing Won opens a dialog with an optional sales order select', async () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Won' }));
    expect(await screen.findByLabelText(/sales order/i)).toBeTruthy();
  });

  it('renders every section with an empty state when there is nothing to show', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ lines: [] }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.getByRole('tab', { name: 'Details' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Products' })).toBeTruthy();
    expect(screen.getByText('Opportunity')).toBeTruthy();

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Products' }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.getByText(/no products yet/i)).toBeTruthy();
  });

  it('shows a loading state', () => {
    hooks.useSalesOpportunity.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.queryByText('Opportunity')).toBeNull();
  });

  it('fix reviewer-2: Lost reason options come from meta.lost_reasons, not a hardcoded list', async () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Lost' }));
    await screen.findByLabelText(/lost reason/i);
    expect(screen.getByRole('option', { name: 'Price' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Competitor' })).toBeTruthy();
    // "Went to competitor" is the OLD hardcoded fallback's label for the same value -
    // its absence is what proves the options came from meta, not the fallback.
    expect(screen.queryByRole('option', { name: 'Went to competitor' })).toBeNull();
  });

  it('fix reviewer-3: the sales order picker searches by q and labels "SO - customer"', async () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Won' }));
    await screen.findByLabelText(/sales order/i);
    await waitFor(() =>
      expect(service.getSalesOpportunitySalesOrderOptions).toHaveBeenCalledWith('opp-1', ''),
    );
    expect(screen.getByRole('option', { name: 'SO-000001 - ZZT Dealer' })).toBeTruthy();
  });

  it('fix reviewer-3: shows the linked sales order after a Won move', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ outcome: 'won', stage_key: 'won', sales_order_id: 'so-1', sales_order_no: 'SO-000001', available_transitions: [] }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.getByText('SO-000001')).toBeTruthy();
  });

  it('fix reviewer-3: shows the lost reason label after a Lost move', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ outcome: 'lost', stage_key: 'lost', lost_reason: 'price', lost_reason_label: 'Price', available_transitions: [] }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.getByText('Price')).toBeTruthy();
  });

  it('fix round2 F7: the Products tab (view mode) shows unit price, amount and a total row', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({
        lines: [
          {
            id: 'l1',
            product_id: 'p1',
            product_code: 'ZZT-001',
            product_name: 'ZZT Basin',
            qty: 2,
            unit_price: '10.00',
            line_amount: '20.00',
          },
          {
            id: 'l2',
            product_id: 'p2',
            product_code: 'ZZT-002',
            product_name: 'ZZT Faucet',
            qty: 1,
            unit_price: null,
            line_amount: null,
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Products' }), {
      button: 0,
      ctrlKey: false,
    });
    // The priced line's amount and the total (one priced line, so they match).
    expect(screen.getAllByText('RM 20.00').length).toBe(2);
    expect(screen.getByText('RM 10.00')).toBeTruthy();
    expect(screen.getByText('Total')).toBeTruthy();
  });

  it('fix round2 F7: editing seeds the stored Unit price, and Expected amount stays live once it matched the stored sum', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({
        expected_amount: '20.00',
        lines: [
          {
            id: 'l1',
            product_id: 'p1',
            product_code: 'ZZT-001',
            product_name: 'ZZT Basin',
            qty: 2,
            unit_price: '10.00',
            line_amount: '20.00',
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Products' }), {
      button: 0,
      ctrlKey: false,
    });
    expect((screen.getByLabelText(/unit price/i) as HTMLInputElement).value).toBe('10.00');
    fireEvent.change(screen.getByLabelText(/qty/i), { target: { value: '4' } });

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Details' }), {
      button: 0,
      ctrlKey: false,
    });
    expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('40.00');
  });

  it('fix round2 S1: removing the priced line brings Expected amount to 0.00, not the stale stored total', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({
        expected_amount: '20.00',
        lines: [
          {
            id: 'l1',
            product_id: 'p1',
            product_code: 'ZZT-001',
            product_name: 'ZZT Basin',
            qty: 2,
            unit_price: '10.00',
            line_amount: '20.00',
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Products' }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(screen.getByRole('button', { name: /remove/i }));

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Details' }), {
      button: 0,
      ctrlKey: false,
    });
    expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('0.00');
  });

  it('fix round2 F7: a customised Expected amount stays put even as a line changes', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({
        expected_amount: '999.00',
        lines: [
          {
            id: 'l1',
            product_id: 'p1',
            product_code: 'ZZT-001',
            product_name: 'ZZT Basin',
            qty: 2,
            unit_price: '10.00',
            line_amount: '20.00',
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Products' }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.change(screen.getByLabelText(/qty/i), { target: { value: '4' } });

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Details' }), {
      button: 0,
      ctrlKey: false,
    });
    expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('999.00');
  });

  it('fix B2: Edit swaps the title, amount and close date for inputs in place', () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.queryByLabelText('Title')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('ZZT Deal');
    expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('1000.00');
    expect((screen.getByLabelText('Expected close date') as HTMLInputElement).value).toBe('2026-11-01');
  });

  it('fix B2: saving an edit sends the updated fields and lines through the id payload', async () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({
        lines: [{ id: 'l1', product_id: 'p1', product_code: 'ZZT-001', product_name: 'ZZT Basin', qty: 2 }],
      }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ZZT Deal Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = save.mutateAsync.mock.calls[0][0];
    expect(payload.id).toBe('opp-1');
    expect(payload.title).toBe('ZZT Deal Renamed');
    expect(payload.lines).toEqual([{ product_id: 'p1', qty: 2, unit_price: null }]);
  });

  it('NUMBER-FIELDS: a cleared Expected amount keeps Save enabled, says why on press and sends nothing', async () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText('Expected amount'), { target: { value: '' } });

    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);
    expect(await screen.findByText(/^Enter a number/)).toBeInTheDocument();
    expect(save.mutateAsync).not.toHaveBeenCalled();
    expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('ZZT Deal');

    fireEvent.change(screen.getByLabelText('Expected amount'), { target: { value: '12' } });
    expect(screen.queryByText(/^Enter a number/)).not.toBeInTheDocument();
  });

  it('fix B2: Cancel leaves the record unchanged and exits edit mode', () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Should not stick' } });
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByLabelText('Title')).toBeNull();
    expect(screen.getByText('ZZT Deal')).toBeTruthy();
  });

  it('fix B2: renders a real RecordNavigation position within the default list', () => {
    hooks.useSalesOpportunities.mockReturnValue({
      data: {
        data: [detail({ id: 'opp-0' }), detail({ id: 'opp-1' }), detail({ id: 'opp-2' })],
        pagination: { total: 3, page: 1, limit: 200 },
        empty: false,
      },
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.getByLabelText('record navigation')).toBeTruthy();
  });

  it('fix B2: no Edit button without sales.opportunities.edit', () => {
    perms.granted = new Set();
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.queryByRole('button', { name: /^edit$/i })).toBeNull();
  });

  it('fix2 should-fix 1: no Edit once the opportunity is closed (won), same as the portal', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ outcome: 'won', stage_key: 'won', available_transitions: [] }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.queryByRole('button', { name: /^edit$/i })).toBeNull();
  });

  it('fix2 should-fix 1: no Edit once the opportunity is closed (lost)', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ outcome: 'lost', stage_key: 'lost', available_transitions: [] }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.queryByRole('button', { name: /^edit$/i })).toBeNull();
  });

  it('fix2 should-fix 2: saving without touching Agent does not send sales_agent_id at all', async () => {
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0]).not.toHaveProperty('sales_agent_id');
  });

  it('fix2 should-fix 2: changing Agent sends the new sales_agent_id', async () => {
    hooks.useSalesOpportunityAgentOptions.mockReturnValue({
      data: [{ id: 'agent-1', code: 'ALI', label: 'ALI - Ali Hassan' }],
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText('Agent'), { target: { value: 'agent-1' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0].sales_agent_id).toBe('agent-1');
  });

  it('fix2 should-fix 3: the header Stage pill colours from stage_key', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ outcome: 'lost', stage_key: 'lost', stage_label: 'Lost', available_transitions: [] }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    expect(screen.getByText('Lost').className).toMatch(/--color-destructive-soft/);
  });

  it('fix2 B-new: switching a customer-backed opportunity to a prospect sends customer_id: null', async () => {
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [],
      prospect: { name: 'ZZT New Prospect' },
      blocked: null,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    await screen.findByRole('option', { name: /add "ZZT New Prospect" as a new prospect/i });
    fireEvent.change(screen.getByLabelText('Customer or prospect'), {
      target: { value: 'prospect:ZZT New Prospect' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = save.mutateAsync.mock.calls[0][0];
    expect(payload.prospect_name).toBe('ZZT New Prospect');
    expect(payload.customer_id).toBeNull();
  });

  it('fix2 B-new: switching a prospect-backed opportunity to a customer sends prospect_name: null', async () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ customer_id: null, customer_name: null, prospect_name: 'ZZT Old Prospect' }),
      isLoading: false,
      isError: false,
    });
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [{ customer_id: 'cust-2', customer_code: 'C2', customer_name: 'ZZT New Customer' }],
      prospect: null,
      blocked: null,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    await screen.findByRole('option', { name: 'C2 - ZZT New Customer' });
    fireEvent.change(screen.getByLabelText('Customer or prospect'), { target: { value: 'cust-2' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = save.mutateAsync.mock.calls[0][0];
    expect(payload.customer_id).toBe('cust-2');
    expect(payload.prospect_name).toBeNull();
  });

  it('fix2 nit: a prospect-backed opportunity shows the prospect name in the select once editing', () => {
    hooks.useSalesOpportunity.mockReturnValue({
      data: detail({ customer_id: null, customer_name: null, prospect_name: 'ZZT Prospect Co' }),
      isLoading: false,
      isError: false,
    });
    render(<SalesOpportunityDetail id="opp-1" />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect(screen.getByRole('option', { name: 'ZZT Prospect Co' })).toBeTruthy();
  });
});
