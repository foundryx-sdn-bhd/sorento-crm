/**
 * Portal detail (UAC S2-11; F6/F7). The gear (`DetailActionsMenu`) holds every stage move -
 * Lost opens a small dialog for the required reason, every other move runs on click. Edit is
 * in place: the SAME cards, title, customer or prospect, amount, close date and product lines
 * each swap for an input where they stand, exactly as the CRM's own `SalesOpportunityDetail`.
 *
 * `SearchableSelect` is mocked as a native `<select>` (static and `fetchOptions` modes both),
 * the same shape the CRM's own opportunity tests use, since the ADR requires every dropdown to
 * be one.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    'aria-label'?: string;
    value: string;
    onChange: (v: string) => void;
    onOptionChange?: (o: { value: string; label: string; disabled?: boolean } | null) => void;
    options?: { value: string; label: string; disabled?: boolean }[];
    fetchOptions?: (
      q: string,
      page: number,
    ) => Promise<{ value: string; label: string; disabled?: boolean }[]>;
    selectedOption?: { value: string; label: string; disabled?: boolean };
    placeholder?: string;
  }) => {
    const [query, setQuery] = React.useState('');
    const [options, setOptions] = React.useState(props.options ?? []);
    React.useEffect(() => {
      if (props.fetchOptions) props.fetchOptions(query, 0).then(setOptions);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [query]);
    const label = props['aria-label'] ?? props.placeholder ?? props.id ?? 'select';
    const merged =
      props.selectedOption && !options.some((o) => o.value === props.selectedOption!.value)
        ? [props.selectedOption, ...options]
        : options;
    return (
      <div>
        {props.fetchOptions ? (
          <input aria-label={`${label} search`} value={query} onChange={(e) => setQuery(e.target.value)} />
        ) : null}
        <select
          aria-label={label}
          value={props.value}
          onChange={(e) => {
            props.onChange(e.target.value);
            const opt = merged.find((o) => o.value === e.target.value) ?? null;
            props.onOptionChange?.(opt);
          }}
        >
          <option value="" />
          {merged.map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
    );
  },
}));

const service = vi.hoisted(() => ({
  getPortalSalesOpportunity: vi.fn(),
  updatePortalSalesOpportunity: vi.fn(),
  getPortalOpportunityMeta: vi.fn(),
  getPortalCustomerOptions: vi.fn(),
  getPortalProductOptions: vi.fn(),
}));
vi.mock('../../lib/sales-opportunity-service', () => service);

import SalesOpportunityPortalDetail from './SalesOpportunityPortalDetail';

function detail(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'opp-1',
    opportunity_no: 'OPP-000001',
    title: 'ZZT Deal',
    customer_id: null,
    customer_name: null,
    prospect_name: 'ZZT Prospect',
    status_id: 'st-new',
    stage_key: 'new',
    stage_label: 'New',
    outcome: 'open',
    // B1: the portal routes send Decimals as JSON numbers, not strings - the fixture
    // reproduces that real shape rather than the "already formatted" strings a naive
    // fixture would use.
    expected_amount: 1000,
    expected_close_date: '2026-11-01',
    lost_reason: null,
    lost_reason_label: null,
    sales_order_id: null,
    sales_order_no: null,
    source: 'portal',
    lines: [
      {
        id: 'l1',
        product_id: 'p1',
        product_code: 'ZZT-001',
        product_name: 'ZZT Basin',
        qty: 2,
        unit_price: 500,
        line_amount: 1000,
      },
    ],
    available_transitions: [
      { to_status_id: 'st-qualified', key: 'qualified', label: 'Qualify' },
      { to_status_id: 'st-won', key: 'won', label: 'Mark won' },
      { to_status_id: 'st-lost', key: 'lost', label: 'Mark lost' },
    ],
    ...over,
  };
}

/** Radix opens its menu on POINTER-down, not click. */
function openGear() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Actions' }), {
    button: 0,
    pointerType: 'mouse',
  });
}

beforeEach(() => {
  Object.values(service).forEach((fn) => fn.mockReset());
  service.getPortalSalesOpportunity.mockResolvedValue(detail());
  service.updatePortalSalesOpportunity.mockResolvedValue(detail());
  service.getPortalOpportunityMeta.mockResolvedValue({
    stages: [],
    lost_reasons: [
      { value: 'price', label: 'Price' },
      { value: 'competitor', label: 'Competitor' },
    ],
  });
  service.getPortalCustomerOptions.mockResolvedValue({ items: [], prospect: null, blocked: null });
  service.getPortalProductOptions.mockResolvedValue([
    { id: 'p1', code: 'ZZT-001', name: 'ZZT Basin', listPrice: '500.00' },
  ]);
});

describe('SalesOpportunityPortalDetail', () => {
  it('F6: offers only the stages named in available_transitions, in a gear menu', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    expect(screen.getByRole('menuitem', { name: 'Qualify' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Mark won' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Mark lost' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Send proposal' })).toBeNull();
  });

  it('F6: the header has one CTA (Edit) plus the gear - no "Move stage" button row', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    expect(screen.queryByText(/move stage/i)).toBeNull();
    expect(screen.getByRole('button', { name: /^edit$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Actions' })).toBeTruthy();
  });

  it('clicking Mark lost opens a dialog titled with the transition label, with a required reason picker sourced from meta lost_reasons', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark lost' }));
    expect(screen.getByRole('heading', { name: 'Mark lost' })).toBeTruthy();
    const reasonSelect = await screen.findByLabelText('Lost reason');
    expect(screen.getByRole('option', { name: 'Price' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Competitor' })).toBeTruthy();
    expect(reasonSelect).toBeTruthy();
  });

  it('confirming Mark lost without a reason does not call updatePortalSalesOpportunity', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark lost' }));
    await screen.findByLabelText('Lost reason');
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    expect(service.updatePortalSalesOpportunity).not.toHaveBeenCalled();
  });

  it('confirming Mark lost with a reason PATCHes status_id and lost_reason', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark lost' }));
    const reasonSelect = await screen.findByLabelText('Lost reason');
    fireEvent.change(reasonSelect, { target: { value: 'price' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    await waitFor(() =>
      expect(service.updatePortalSalesOpportunity).toHaveBeenCalledWith('opp-1', {
        status_id: 'st-lost',
        lost_reason: 'price',
      }),
    );
  });

  it('clicking Qualify in the gear PATCHes status_id right away - not a terminal move, no confirm step', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Qualify' }));
    await waitFor(() =>
      expect(service.updatePortalSalesOpportunity).toHaveBeenCalledWith('opp-1', {
        status_id: 'st-qualified',
      }),
    );
  });

  // Won is terminal (a closed opportunity cannot be edited again) - it gets the same
  // confirm step Lost does, just without a reason field (reviewer ruling stands).
  it('F6: clicking Mark won opens a confirm dialog titled with the transition label; Confirm PATCHes status_id only', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark won' }));
    expect(service.updatePortalSalesOpportunity).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: 'Mark won' })).toBeTruthy();
    expect(screen.queryByLabelText('Lost reason')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    await waitFor(() =>
      expect(service.updatePortalSalesOpportunity).toHaveBeenCalledWith('opp-1', {
        status_id: 'st-won',
      }),
    );
  });

  it('F6: Cancel on a pending Mark won sends nothing', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark won' }));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(service.updatePortalSalesOpportunity).not.toHaveBeenCalled();
  });

  // Nit: no feature explanations in the UI - the title "Mark won" plus Confirm/Cancel is
  // the whole dialog.
  it('nit: the Mark won dialog has no explanatory sentence', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark won' }));
    expect(screen.queryByText('This closes the opportunity.')).toBeNull();
  });

  // Nit: Lost is a stage move, not a delete - the CRM's own gear does not style it
  // destructive either.
  it('nit: Mark lost is a secondary action in the gear, not styled destructive', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    const item = screen.getByRole('menuitem', { name: 'Mark lost' });
    expect(item.className).not.toContain('text-destructive');
  });

  it('nit: a failed Mark won save keeps the dialog open, and only closes once the retry succeeds', async () => {
    service.updatePortalSalesOpportunity.mockRejectedValueOnce(new Error('network down'));
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark won' }));
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    await waitFor(() => expect(service.updatePortalSalesOpportunity).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('heading', { name: 'Mark won' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    await waitFor(() => expect(service.updatePortalSalesOpportunity).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Mark won' })).toBeNull());
  });

  it('nit: a failed Mark lost save keeps the dialog open with the typed reason', async () => {
    service.updatePortalSalesOpportunity.mockRejectedValueOnce(new Error('network down'));
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    openGear();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark lost' }));
    const reasonSelect = await screen.findByLabelText('Lost reason');
    fireEvent.change(reasonSelect, { target: { value: 'price' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    await waitFor(() => expect(service.updatePortalSalesOpportunity).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('heading', { name: 'Mark lost' })).toBeTruthy();
    expect((screen.getByLabelText('Lost reason') as HTMLSelectElement).value).toBe('price');
  });

  it('has no sales order field anywhere in the portal', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    expect(screen.queryByLabelText(/sales order/i)).toBeNull();
  });

  it('F6: no gear and no Edit when the opportunity is terminal (won)', async () => {
    service.getPortalSalesOpportunity.mockResolvedValue(
      detail({ outcome: 'won', stage_key: 'won', stage_label: 'Won', available_transitions: [] }),
    );
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    expect(screen.queryByRole('button', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^edit$/i })).toBeNull();
  });

  it('F6: no gear when the opportunity is terminal (lost)', async () => {
    service.getPortalSalesOpportunity.mockResolvedValue(
      detail({ outcome: 'lost', stage_key: 'lost', stage_label: 'Lost', available_transitions: [] }),
    );
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    expect(screen.queryByRole('button', { name: 'Actions' })).toBeNull();
  });

  it('F7: lists each line as product, qty, unit price and amount, plus a total', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('ZZT-001');
    expect(screen.getByText('ZZT Basin')).toBeTruthy();
    expect(screen.getByText('2')).toBeTruthy();
    expect(screen.getByText('RM 500.00')).toBeTruthy();
    expect(screen.getAllByText('RM 1,000.00').length).toBeGreaterThan(0);
    expect(screen.getByText('Total')).toBeTruthy();
  });

  it('fix browser-defect-1: shows the lost reason once the opportunity is Lost', async () => {
    service.getPortalSalesOpportunity.mockResolvedValue(
      detail({
        outcome: 'lost',
        stage_key: 'lost',
        stage_label: 'Lost',
        lost_reason: 'price',
        lost_reason_label: 'Price',
        available_transitions: [],
      }),
    );
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    expect(screen.getByText('Price')).toBeTruthy();
  });

  it('fix nit: shows an error state with Retry when the load fails', async () => {
    service.getPortalSalesOpportunity.mockRejectedValueOnce(new Error('network down'));
    service.getPortalSalesOpportunity.mockResolvedValueOnce(detail());
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText(/failed to load/i);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    await screen.findByText('OPP-000001');
  });

  it('fix B3: Edit reveals the same form pre-filled with the opportunity', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('ZZT Deal');
    expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('1000.00');
  });

  it('fix B3: saving an edit PATCHes the opportunity and returns to the view', async () => {
    service.updatePortalSalesOpportunity.mockResolvedValue(detail({ title: 'ZZT Deal Renamed' }));
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ZZT Deal Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(service.updatePortalSalesOpportunity).toHaveBeenCalledTimes(1));
    expect(service.updatePortalSalesOpportunity.mock.calls[0][0]).toBe('opp-1');
    expect(service.updatePortalSalesOpportunity.mock.calls[0][1].title).toBe('ZZT Deal Renamed');
    await waitFor(() => expect(screen.queryByLabelText('Title')).toBeNull());
  });

  it('fix B3: Cancel returns to the view without saving', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByLabelText('Title')).toBeNull();
    expect(service.updatePortalSalesOpportunity).not.toHaveBeenCalled();
    expect(screen.getByText('ZZT Deal')).toBeTruthy();
  });

  it('fix B3: no Edit once the opportunity is closed (N7)', async () => {
    service.getPortalSalesOpportunity.mockResolvedValue(
      detail({ outcome: 'won', stage_key: 'won', available_transitions: [] }),
    );
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    expect(screen.queryByRole('button', { name: /^edit$/i })).toBeNull();
  });

  it('fix2 should-fix 5: Edit keeps the SAME view - the Products card stays, its rows become editable in place', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    expect(screen.getByText('Products')).toBeTruthy();
    expect(screen.getByText('ZZT-001')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    expect(screen.getByText('Products')).toBeTruthy();
    expect(screen.getByRole('button', { name: /add product/i })).toBeTruthy();
  });

  it('fix2 should-fix 5: the opportunity number and stage badge stay visible while editing (read-only meta)', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect(screen.getByText('OPP-000001')).toBeTruthy();
    expect(screen.getByText('New')).toBeTruthy();
  });

  it('fix2 should-fix 3: the stage pill colours from stage_key', async () => {
    service.getPortalSalesOpportunity.mockResolvedValue(
      detail({ outcome: 'lost', stage_key: 'lost', stage_label: 'Lost', available_transitions: [] }),
    );
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    const badge = await screen.findByText('Lost');
    expect(badge.className).toMatch(/--color-destructive-soft/);
  });

  it('fix2 B-new: switching a prospect-backed opportunity to a customer sends customer_id and an explicit prospect_name: null', async () => {
    service.getPortalCustomerOptions.mockResolvedValue({
      items: [{ customer_id: 'cust-2', customer_code: 'C2', customer_name: 'ZZT New Customer' }],
      prospect: null,
      blocked: null,
    });
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    const customerSelect = await screen.findByLabelText('Customer or prospect');
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'C2 - ZZT New Customer' })).toBeTruthy(),
    );
    fireEvent.change(customerSelect, { target: { value: 'cust-2' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(service.updatePortalSalesOpportunity).toHaveBeenCalledTimes(1));
    const payload = service.updatePortalSalesOpportunity.mock.calls[0][1];
    expect(payload.customer_id).toBe('cust-2');
    expect(payload.prospect_name).toBeNull();
  });

  it('fix2 B-new: switching a customer-backed opportunity to a prospect sends prospect_name and an explicit customer_id: null', async () => {
    service.getPortalSalesOpportunity.mockResolvedValue(
      detail({ customer_id: 'cust-1', customer_name: 'ZZT Dealer', prospect_name: null }),
    );
    service.getPortalCustomerOptions.mockResolvedValue({
      items: [],
      prospect: { name: 'ZZT New Prospect' },
      blocked: null,
    });
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    const customerSelect = await screen.findByLabelText('Customer or prospect');
    await waitFor(() =>
      expect(
        screen.getByRole('option', { name: 'Add "ZZT New Prospect" as a new prospect' }),
      ).toBeTruthy(),
    );
    fireEvent.change(customerSelect, { target: { value: 'prospect:ZZT New Prospect' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(service.updatePortalSalesOpportunity).toHaveBeenCalledTimes(1));
    const payload = service.updatePortalSalesOpportunity.mock.calls[0][1];
    expect(payload.prospect_name).toBe('ZZT New Prospect');
    expect(payload.customer_id).toBeNull();
  });

  it('S4: keeps a freshly picked customer visible in Edit after the search list changes', async () => {
    service.getPortalCustomerOptions.mockImplementation(async (q: string) => {
      if (q === 'ZZT New') {
        return {
          items: [{ customer_id: 'cust-2', customer_code: 'C2', customer_name: 'ZZT New Customer' }],
          prospect: null,
          blocked: null,
        };
      }
      return { items: [], prospect: null, blocked: null };
    });
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    const customerSelect = await screen.findByLabelText('Customer or prospect');
    fireEvent.change(screen.getByLabelText('Customer or prospect search'), {
      target: { value: 'ZZT New' },
    });
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'C2 - ZZT New Customer' })).toBeTruthy(),
    );
    fireEvent.change(customerSelect, { target: { value: 'cust-2' } });

    // Reopen with a different (empty) search - the fresh result set doesn't include it.
    fireEvent.change(screen.getByLabelText('Customer or prospect search'), { target: { value: '' } });
    await waitFor(() => expect(service.getPortalCustomerOptions).toHaveBeenCalledWith(''));

    expect(screen.getByRole('option', { name: 'C2 - ZZT New Customer' })).toBeTruthy();
  });

  it('F7: edit prefills each line unit price and the total updates as qty changes, until an amount is typed by hand', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    expect((screen.getByLabelText('Unit price') as HTMLInputElement).value).toBe('500.00');
    // The stored amount (1000.00) already equals the stored line's amount, so it is
    // untouched - changing qty recomputes it.
    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '3' } });
    await waitFor(() =>
      expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('1500.00'),
    );
  });

  it('F7: a hand-typed stored amount that does not match the lines total is left alone on Edit', async () => {
    service.getPortalSalesOpportunity.mockResolvedValue(detail({ expected_amount: 5000 }));
    render(<SalesOpportunityPortalDetail id="opp-1" />);
    await screen.findByText('OPP-000001');
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('5000.00');
    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '3' } });
    expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('5000.00');
  });

  it('Back returns to the portal home on the Sales Opportunity kind, in the slug tree when given one', async () => {
    render(<SalesOpportunityPortalDetail id="opp-1" slug="contact-x" />);
    await screen.findByText('OPP-000001');
    expect(screen.getByRole('link', { name: /Back/ }).getAttribute('href')).toBe(
      '/portal/c/contact-x?type=sales_opportunity',
    );
  });
});
