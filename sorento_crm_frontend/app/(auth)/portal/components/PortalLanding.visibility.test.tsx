/**
 * PLAN-portal-forms-market-segment AC-L1/AC-L2/AC-L3: every landing kind is
 * gated the same way now, not just price_tag_request (D2). No kind is
 * offered or fetched unless the resolved `visible_form_types` grants it, and
 * an empty visible set gets a WhatsApp empty state rather than a blank page.
 *
 * Mocking pattern mirrors `PortalLanding.priceTag.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
let searchParams = new URLSearchParams('');
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => searchParams,
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('../lib/portal-client')>();
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

import { fetchMeWithGrace, fetchSubmissions } from '../lib/portal-client';
import { listRequestsAsSummaries } from '../lib/price-tag-request-service';
import { PortalLanding } from './PortalLanding';

const ME = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-09-01T00:00:00Z',
  portal_slug: 'contact-x',
  whatsapp_number: '60177777777',
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
});

// The landing loads on mount and again when the search debounce settles
// (`q` goes from `undefined` to `''`), so asking "was this kind ever
// fetched" has to ignore the second argument entirely.
function calledWithKind(kind: string): boolean {
  return (fetchSubmissions as ReturnType<typeof vi.fn>).mock.calls.some(
    (call) => call[0] === kind,
  );
}

describe('PortalLanding - every kind derives from visible_form_types (AC-L1)', () => {
  it('offers, and fetches, only the granted kind - nothing unconditional', async () => {
    mockContact(['complaint']);
    render(<PortalLanding slug="contact-x" />);

    const trigger = await screen.findByRole('combobox');
    await waitFor(() => expect(fetchSubmissions).toHaveBeenCalled());

    expect(calledWithKind('complaint')).toBe(true);
    expect(calledWithKind('stock_inquiry')).toBe(false);
    expect(calledWithKind('purchase_request')).toBe(false);
    expect(calledWithKind('sponsorship_form')).toBe(false);
    expect(listRequestsAsSummaries).not.toHaveBeenCalled();

    trigger.click();
    expect(await screen.findByText('Complaint')).toBeInTheDocument();
    expect(screen.queryByText('Stock Inquiry')).toBeNull();
    expect(screen.queryByText('Purchase Request')).toBeNull();
    expect(screen.queryByText('Sponsorship Form')).toBeNull();
    expect(screen.queryByText('Price Tag Request')).toBeNull();
  });

  it('fetches every granted kind, legacy and price tag alike, and no others', async () => {
    mockContact(['purchase_request', 'price_tag_request']);
    render(<PortalLanding slug="contact-x" />);

    await waitFor(() => expect(listRequestsAsSummaries).toHaveBeenCalled());
    expect(calledWithKind('purchase_request')).toBe(true);
    expect(calledWithKind('complaint')).toBe(false);
    expect(calledWithKind('stock_inquiry')).toBe(false);
    expect(calledWithKind('sponsorship_form')).toBe(false);
  });
});

describe('PortalLanding - the active tab falls back to the first visible kind (AC-L2)', () => {
  it('defaults to Stock Inquiry when visible - the dealers main form stays first', async () => {
    mockContact(['complaint', 'stock_inquiry', 'purchase_request']);
    render(<PortalLanding slug="contact-x" />);

    expect(
      await screen.findByRole('link', { name: /New Stock Inquiry/ }),
    ).toBeInTheDocument();
  });

  it('with no ?type= in the URL', async () => {
    mockContact(['purchase_request', 'sponsorship_form']);
    render(<PortalLanding slug="contact-x" />);

    expect(
      await screen.findByRole('link', { name: /New Purchase Request/ }),
    ).toBeInTheDocument();
  });

  it('with a ?type= naming a kind outside the visible set', async () => {
    searchParams = new URLSearchParams('type=stock_inquiry');
    mockContact(['purchase_request', 'sponsorship_form']);
    render(<PortalLanding slug="contact-x" />);

    expect(
      await screen.findByRole('link', { name: /New Purchase Request/ }),
    ).toBeInTheDocument();
  });
});

describe('PortalLanding - empty visible set (AC-L3)', () => {
  it('shows the WhatsApp empty state instead of the picker, toolbar or list', async () => {
    mockContact([]);
    render(<PortalLanding slug="contact-x" />);

    expect(
      await screen.findByText('No forms are available for your account.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByLabelText('Search submissions')).toBeNull();

    const waLink = screen.getByRole('link', { name: /Chat with us on WhatsApp/ });
    expect(waLink).toHaveAttribute('href', expect.stringContaining('https://wa.me/60177777777'));

    expect(fetchSubmissions).not.toHaveBeenCalled();
    expect(listRequestsAsSummaries).not.toHaveBeenCalled();
  });

  it('offers Log out as the next step when the contact has no WhatsApp number on file (S3)', async () => {
    (fetchMeWithGrace as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...ME,
      whatsapp_number: null,
      visible_form_types: [],
    });
    render(<PortalLanding slug="contact-x" />);

    await screen.findByText('No forms are available for your account.');
    expect(screen.queryByRole('link', { name: /Chat with us on WhatsApp/ })).toBeNull();
    // Two Log out buttons now exist (header's own, plus the empty state's) -
    // the empty state's own next step is the one under test.
    expect(screen.getAllByRole('button', { name: 'Log out' }).length).toBeGreaterThan(0);
  });
});
