/**
 * Sales Opportunity is one kind in the portal's kind selector, exactly like Price Tag Request
 * (fix lane round 2, F1; owner ruling 27 Sep: "sales opportuniteis need to be 1 of the tab
 * just liek price tag request"), with the agent's My target panel at the top of it (F2).
 *
 * Replaces the S2 test of the separate "Sales Opportunities / Open" card, which the ruling
 * removes. Mocking pattern mirrors `PortalLanding.priceTag.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
let searchParams = new URLSearchParams('');
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => searchParams,
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...original,
    fetchMeWithGrace: vi.fn(),
    fetchSubmissions: vi.fn(),
    fetchSubmission: vi.fn(),
    portalLogout: vi.fn(),
    readPortalToken: vi.fn(() => 'tok-123'),
  };
});

vi.mock('../lib/price-tag-request-service', () => ({
  listRequestsAsSummaries: vi.fn(),
}));

vi.mock('../lib/sales-opportunity-service', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/sales-opportunity-service')>();
  return { ...original, listPortalSalesOpportunities: vi.fn() };
});

vi.mock('../lib/my-target-service', () => ({ getMyTargets: vi.fn() }));

import { fetchMeWithGrace, fetchSubmissions } from '../lib/portal-client';
import { listRequestsAsSummaries } from '../lib/price-tag-request-service';
import { listPortalSalesOpportunities } from '../lib/sales-opportunity-service';
import { getMyTargets } from '../lib/my-target-service';
import { PortalLanding } from './PortalLanding';

const ME = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'Jayson',
  phone_number: '60123456789',
  expires_at: '2026-09-01T00:00:00Z',
  portal_slug: 'contact-x',
};

const OPP = {
  id: 'opp-1',
  opportunity_no: 'OPP-000001',
  title: 'Showroom refit',
  customer_id: null,
  customer_name: null,
  prospect_name: 'Chin Chun',
  stage_key: 'qualified',
  stage_label: 'Qualified',
  outcome: 'open',
  expected_amount: '15000.00',
  expected_close_date: '2026-10-18',
  lost_reason: null,
  lost_reason_label: null,
  source: 'portal',
  lines: [],
  available_transitions: [],
  created_at: '2026-09-20T10:00:00',
};

const OPP_2 = {
  ...OPP,
  id: 'opp-2',
  opportunity_no: 'OPP-000002',
  title: 'Kitchen fit-out',
  prospect_name: null,
  customer_id: 'cust-1',
  customer_name: 'Lim Tiles',
  stage_key: 'new',
  stage_label: 'New',
};

const TARGETS = {
  today: '2026-09-27',
  targets: [
    {
      target_id: 't-1',
      target_no: 'TGT-000001',
      name: 'Q4 sales',
      metric: 'amount',
      basis: 'ordered',
      counts_label: 'Ordered',
      product_scope: 'all',
      scope_labels: [],
      start_date: '2026-09-01',
      end_date: '2026-10-31',
      target_value: '100000.00',
      achieved_value: '40000.00',
      gap_value: '60000.00',
      pipeline_value: '45000.00',
      projected_value: '85000.00',
      short_value: '15000.00',
      opportunities: [
        { id: 'opp-3', opportunity_no: 'OPP-000003', title: 'Fit-out', customer_or_prospect: 'Chin Chun', stage_label: 'New', expected_close_date: '2026-10-10', value: '20000.00' },
        { id: 'opp-1', opportunity_no: 'OPP-000001', title: 'Showroom refit', customer_or_prospect: 'Lim Tiles', stage_label: 'Qualified', expected_close_date: '2026-10-18', value: '15000.00' },
        { id: 'opp-4', opportunity_no: 'OPP-000004', title: 'Bath', customer_or_prospect: 'Tan Home', stage_label: 'New', expected_close_date: '2026-10-28', value: '10000.00' },
      ],
    },
  ],
};

function mockContact(visible: string[]) {
  (fetchMeWithGrace as ReturnType<typeof vi.fn>).mockResolvedValue({
    ...ME,
    visible_form_types: visible,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  searchParams = new URLSearchParams('');
  window.localStorage.clear();
  (fetchSubmissions as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (listRequestsAsSummaries as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (listPortalSalesOpportunities as ReturnType<typeof vi.fn>).mockResolvedValue([OPP, OPP_2]);
  (getMyTargets as ReturnType<typeof vi.fn>).mockResolvedValue(TARGETS);
});

describe('PortalLanding - Sales Opportunity is one kind in the selector (F1)', () => {
  it('offers Sales Opportunity with its count beside Price Tag Request', async () => {
    mockContact(['price_tag_request', 'sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);

    const trigger = await screen.findByRole('combobox');
    await waitFor(() => expect(listPortalSalesOpportunities).toHaveBeenCalled());
    trigger.click();
    const option = await screen.findByText('Sales Opportunity');
    expect(option.parentElement?.textContent).toContain('2');
    // Also once in the trigger: Price Tag Request is the first kind, so it is selected.
    expect((await screen.findAllByText('Price Tag Request')).length).toBeGreaterThan(0);
  });

  it('has no separate Sales Opportunities card or Open link any more', async () => {
    mockContact(['stock_inquiry', 'sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);
    await screen.findByRole('combobox');
    expect(screen.queryByRole('link', { name: /sales opportunities/i })).toBeNull();
    expect(screen.queryByText('Open')).toBeNull();
  });

  it('lists opportunities as cards with number, customer or prospect, stage label, amount and close date', async () => {
    searchParams = new URLSearchParams('type=sales_opportunity');
    mockContact(['sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);

    // The My target panel lists OPP-000001 too; the card is the one inside a role="link".
    expect((await screen.findAllByText('OPP-000001')).length).toBeGreaterThan(0);
    expect(screen.getByText('OPP-000002')).toBeInTheDocument();
    expect(screen.getAllByText('Chin Chun').length).toBeGreaterThan(0);
    expect(screen.getByText('Lim Tiles', { selector: 'li *' })).toBeInTheDocument();
    // Stage labels, never keys.
    expect(screen.getAllByText('Qualified').length).toBeGreaterThan(0);
    expect(screen.queryByText('qualified')).toBeNull();
    const card = screen
      .getAllByText('OPP-000001')
      .map((el) => el.closest('[role="link"]'))
      .find(Boolean) as HTMLElement;
    expect(within(card).getByText(/15,000/)).toBeInTheDocument();
    expect(within(card).getByText(/18 Oct 2026/)).toBeInTheDocument();
  });

  it('has the New button for the kind, pointing at the slug tree', async () => {
    searchParams = new URLSearchParams('type=sales_opportunity');
    mockContact(['sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);
    const link = await screen.findByRole('link', { name: /New Sales Opportunity/ });
    expect(link.getAttribute('href')).toBe('/portal/c/contact-x/sales_opportunity/new');
  });

  it('search narrows the opportunities by title, number or customer', async () => {
    // No target, so the panel does not repeat the numbers the list shows.
    (getMyTargets as ReturnType<typeof vi.fn>).mockResolvedValue({ today: '2026-09-27', targets: [] });
    searchParams = new URLSearchParams('type=sales_opportunity');
    mockContact(['sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);
    await screen.findByText('OPP-000001');
    fireEvent.change(screen.getByRole('textbox', { name: /search/i }), {
      target: { value: 'lim tiles' },
    });
    await waitFor(() => expect(screen.queryByText('OPP-000001')).toBeNull());
    expect(screen.getByText('OPP-000002')).toBeInTheDocument();
  });

  it('is not offered, and nothing is fetched, when the kind is not granted', async () => {
    mockContact(['stock_inquiry']);
    render(<PortalLanding slug="contact-x" />);
    const trigger = await screen.findByRole('combobox');
    await waitFor(() => expect(fetchSubmissions).toHaveBeenCalled());
    trigger.click();
    expect(await screen.findByText('Stock Inquiry')).toBeInTheDocument();
    expect(screen.queryByText('Sales Opportunity')).toBeNull();
    expect(listPortalSalesOpportunities).not.toHaveBeenCalled();
    expect(getMyTargets).not.toHaveBeenCalled();
  });
});

describe('PortalLanding - My target panel at the top of the Sales Opportunity kind (F2)', () => {
  it('shows target, achieved, open before the end date and short as labelled rows, no sentence (Lavish notes)', async () => {
    searchParams = new URLSearchParams('type=sales_opportunity');
    mockContact(['sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);

    const panel = await screen.findByRole('region', { name: 'My target' });
    expect(within(panel).getByText('Q4 sales')).toBeInTheDocument();
    expect(within(panel).getByText('By 31 Oct 2026')).toBeInTheDocument();
    const stat = (label: string) => within(panel).getByTestId(`target-stat-${label}`).textContent;
    expect(stat('target')).toBe('TargetRM 100,000');
    expect(stat('achieved')).toBe('AchievedRM 40,000');
    expect(stat('open')).toBe('Open (3)RM 45,000');
    expect(stat('short')).toBe('ShortRM 15,000');
    // "the wording too long already": no prose sentence left.
    expect(panel.textContent).not.toContain('achieved of');
    expect(panel.textContent).not.toContain('before then');
    expect(panel.textContent).toContain('Amount, ordered, all products');
    // One pin per open opportunity on the timeline, and today marked.
    expect(within(panel).getAllByTestId('target-pin')).toHaveLength(3);
    expect(within(panel).getByTestId('target-today')).toBeInTheDocument();
    expect(within(panel).getByText('OPP-000003')).toBeInTheDocument();
  });

  it('shows Short as zero when the open opportunities would cover the target', async () => {
    (getMyTargets as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...TARGETS,
      targets: [{ ...TARGETS.targets[0], short_value: '0.00', projected_value: '105000.00', pipeline_value: '65000.00' }],
    });
    searchParams = new URLSearchParams('type=sales_opportunity');
    mockContact(['sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);
    const panel = await screen.findByRole('region', { name: 'My target' });
    expect(within(panel).getByTestId('target-stat-short').textContent).toBe('ShortRM 0');
  });

  it('renders no panel when the agent has no active target', async () => {
    (getMyTargets as ReturnType<typeof vi.fn>).mockResolvedValue({ today: '2026-09-27', targets: [] });
    searchParams = new URLSearchParams('type=sales_opportunity');
    mockContact(['sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);
    await screen.findByText('OPP-000001');
    expect(screen.queryByRole('region', { name: 'My target' })).toBeNull();
  });

  it('does not show the panel on another kind', async () => {
    searchParams = new URLSearchParams('type=price_tag_request');
    mockContact(['price_tag_request', 'sales_opportunity']);
    render(<PortalLanding slug="contact-x" />);
    await screen.findByRole('combobox');
    expect(screen.queryByRole('region', { name: 'My target' })).toBeNull();
  });
});
