/**
 * D45 / AC-M.14: Price Tag Request is one of the landing dropdown's options,
 * gated on the contact's `visible_form_types`, and never a separate link button.
 *
 * The gating is unit-tested here rather than proved with a second contact in the
 * browser: manufacturing one would mean writing to a shared database that is a
 * copy of production.
 *
 * Mocking pattern mirrors `PortalLanding.revBadge.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import type { PortalSubmissionSummary } from '../lib/portal-client';

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

const PRICE_TAG_ROW: PortalSubmissionSummary = {
  id: 'ptr-1',
  kind: 'price_tag_request',
  title: 'ZZT DEALER SDN BHD',
  document_number: 'PT-202608-0001',
  reference: null,
  status: 'new',
  is_editable: false,
  is_draft: false,
  created_at: '2026-08-20T00:00:00Z',
  customer_name: 'ZZT DEALER SDN BHD',
  needed_by_date: '2026-09-04',
};

const DRAFT_ROW: PortalSubmissionSummary = {
  ...PRICE_TAG_ROW,
  id: 'ptr-2',
  document_number: 'PT-202608-0002',
  is_draft: true,
  is_editable: true,
};

const ME = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-09-01T00:00:00Z',
  portal_slug: 'contact-x',
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
  (listRequestsAsSummaries as ReturnType<typeof vi.fn>).mockResolvedValue([
    PRICE_TAG_ROW,
    DRAFT_ROW,
  ]);
});

describe('PortalLanding - Price Tag Request in the type dropdown', () => {
  it('offers the option, with a count, to a contact whose grant includes it', async () => {
    mockContact(['stock_inquiry', 'price_tag_request']);
    render(<PortalLanding slug="contact-x" />);

    const trigger = await screen.findByRole('combobox');
    // Not a call COUNT: the landing loads on mount and again when the search
    // debounce fires, so two is as correct as one.
    await waitFor(() => expect(listRequestsAsSummaries).toHaveBeenCalled());

    trigger.click();
    const option = await screen.findByText('Price Tag Request');
    expect(option).toBeInTheDocument();
    // The count badge sits beside the label inside the same option row.
    expect(option.parentElement?.textContent).toContain('2');
  });

  it('does not offer it to a contact whose grant omits it, and asks for no list', async () => {
    mockContact(['stock_inquiry']);
    render(<PortalLanding slug="contact-x" />);

    const trigger = await screen.findByRole('combobox');
    await waitFor(() => expect(fetchSubmissions).toHaveBeenCalled());

    trigger.click();
    expect(await screen.findByText('Stock Inquiry')).toBeInTheDocument();
    expect(screen.queryByText('Price Tag Request')).toBeNull();
    expect(listRequestsAsSummaries).not.toHaveBeenCalled();
  });

  it('lists the requests in the same card as the other kinds when selected', async () => {
    searchParams = new URLSearchParams('type=price_tag_request');
    mockContact(['price_tag_request']);
    render(<PortalLanding slug="contact-x" />);

    expect(await screen.findByText('PT-202608-0001')).toBeInTheDocument();
    // One dealer line per card: both rows name the same debtor.
    expect(screen.getAllByText('ZZT DEALER SDN BHD')).toHaveLength(2);
    // A request still holding portal_draft_at reads as Draft, not New.
    expect(screen.getByText('Draft')).toBeInTheDocument();
    expect(screen.getByText('New')).toBeInTheDocument();
  });

  it('falls back to stock inquiry on a ?type= deep link the contact cannot see', async () => {
    searchParams = new URLSearchParams('type=price_tag_request');
    mockContact(['stock_inquiry']);
    render(<PortalLanding slug="contact-x" />);

    // The New button names the active kind, so it is what proves the
    // fallback. The label is split across spans (short "New" below `sm`,
    // full label at `sm+`), so it is queried by accessible name (the whole
    // label, which the button's `aria-label` always carries) rather than
    // exact text.
    expect(
      await screen.findByRole('link', { name: /New Stock Inquiry/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText('PT-202608-0001')).toBeNull();
  });

  it('one failing kind is empty, and the rest of the landing still loads', async () => {
    // Every leg used to be awaited in one Promise.all, so a 403 or a 500 on any
    // one of them rejected the whole load and the landing showed its error
    // screen: the four kinds that answered perfectly well were unreachable.
    mockContact(['stock_inquiry', 'price_tag_request']);
    (listRequestsAsSummaries as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('Request failed with 500'),
    );

    render(<PortalLanding slug="contact-x" />);

    const trigger = await screen.findByRole('combobox');
    trigger.click();
    expect(await screen.findByText('Stock Inquiry')).toBeInTheDocument();
    expect(screen.queryByText('Failed to load portal.')).toBeNull();
  });

  it('a failing legacy kind does not take the gated one down with it', async () => {
    mockContact(['stock_inquiry', 'price_tag_request']);
    (fetchSubmissions as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('Request failed with 500'),
    );
    searchParams = new URLSearchParams('type=price_tag_request');

    render(<PortalLanding slug="contact-x" />);

    expect(await screen.findByText('PT-202608-0001')).toBeInTheDocument();
  });

  it('no longer renders the separate Price Tag Requests link button', async () => {
    mockContact(['price_tag_request']);
    render(<PortalLanding slug="contact-x" />);

    await screen.findByRole('combobox');
    expect(screen.queryByText('Price Tag Requests')).toBeNull();
  });
});

describe('PortalLanding - price tag card shows the revision badge/chip too (AC-R7)', () => {
  // Same mechanism `PortalLanding.revBadge.test.tsx` already pins for the
  // legacy kinds - the card reads `row.revision_no`/`row.has_revision_draft`
  // with no kind-specific branch, so a price_tag_request row must behave
  // identically once the settings row for it is enabled.
  it('shows the "Rev N" badge for a revised price tag request', async () => {
    searchParams = new URLSearchParams('type=price_tag_request');
    mockContact(['price_tag_request']);
    (listRequestsAsSummaries as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...PRICE_TAG_ROW, revision_no: 1, last_revised_at: '2026-09-01T00:00:00Z' },
    ]);

    render(<PortalLanding slug="contact-x" />);

    await screen.findByText('PT-202608-0001');
    expect(screen.getByText('Rev 1')).toBeInTheDocument();
  });

  it('shows the Revising chip when has_revision_draft is true', async () => {
    searchParams = new URLSearchParams('type=price_tag_request');
    mockContact(['price_tag_request']);
    (listRequestsAsSummaries as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        ...PRICE_TAG_ROW,
        revision_no: 1,
        last_revised_at: '2026-09-01T00:00:00Z',
        has_revision_draft: true,
      },
    ]);

    render(<PortalLanding slug="contact-x" />);

    await screen.findByText('PT-202608-0001');
    const chip = screen.getByTestId('revising-chip');
    expect(chip).toHaveTextContent('Revising');
  });
});

describe('PortalLanding - card needed_by_date is formatted, not raw ISO (review round 3)', () => {
  it('renders needed_by_date with toLocaleDateString like created_at', async () => {
    searchParams = new URLSearchParams('type=price_tag_request');
    mockContact(['price_tag_request']);
    (listRequestsAsSummaries as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...PRICE_TAG_ROW, needed_by_date: '2026-09-04' },
    ]);

    render(<PortalLanding slug="contact-x" />);
    await screen.findByText('PT-202608-0001');

    const expected = new Date('2026-09-04').toLocaleDateString(undefined, {
      dateStyle: 'medium',
    });
    expect(screen.getByText(expected)).toBeInTheDocument();
    expect(screen.queryByText('2026-09-04')).toBeNull();
  });
});
