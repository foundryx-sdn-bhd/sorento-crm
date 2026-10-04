/**
 * Portal list cards surface the revision state (portal revision UX): a row
 * that has been revised carries a "Rev N" badge next to its document number
 * and reads "Revised <date>" instead of the created date. A never-revised row
 * shows neither.
 *
 * Mocking pattern mirrors `PortalLandingRevise.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import type { PortalSubmissionSummary } from '../lib/portal-client';

const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(''),
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

import { fetchMeWithGrace, fetchSubmissions } from '../lib/portal-client';
import { PortalLanding } from './PortalLanding';

const REVISED_ROW: PortalSubmissionSummary = {
  id: 'si-revised',
  kind: 'stock_inquiry',
  title: 'Stock inquiry',
  document_number: 'SI-26-0184',
  reference: 'SI-26-0184',
  status: 'pending_purchasing',
  is_editable: false,
  is_draft: false,
  created_at: '2026-07-01T00:00:00Z',
  revision_no: 2,
  last_revised_at: '2026-07-20T00:00:00Z',
  product_code: 'ABC-123',
};

const UNREVISED_ROW: PortalSubmissionSummary = {
  id: 'si-fresh',
  kind: 'stock_inquiry',
  title: 'Stock inquiry',
  document_number: 'SI-26-0200',
  reference: 'SI-26-0200',
  status: 'pending_purchasing',
  is_editable: false,
  is_draft: false,
  created_at: '2026-07-05T00:00:00Z',
  revision_no: 0,
  last_revised_at: null,
  product_code: 'XYZ-999',
};

const REVISING_ROW: PortalSubmissionSummary = {
  id: 'si-revising',
  kind: 'stock_inquiry',
  title: 'Stock inquiry',
  document_number: 'SI-26-0777',
  reference: 'SI-26-0777',
  status: 'responded',
  is_editable: false,
  is_draft: false,
  created_at: '2026-07-10T00:00:00Z',
  revision_no: 1,
  last_revised_at: '2026-07-11T00:00:00Z',
  has_revision_draft: true,
  product_code: 'DEF-456',
};

const NOT_REVISING_ROW: PortalSubmissionSummary = {
  id: 'si-not-revising',
  kind: 'stock_inquiry',
  title: 'Stock inquiry',
  document_number: 'SI-26-0888',
  reference: 'SI-26-0888',
  status: 'responded',
  is_editable: false,
  is_draft: false,
  created_at: '2026-07-12T00:00:00Z',
  revision_no: 1,
  last_revised_at: '2026-07-13T00:00:00Z',
  has_revision_draft: false,
  product_code: 'GHI-789',
};

beforeEach(() => {
  vi.clearAllMocks();
  (fetchMeWithGrace as ReturnType<typeof vi.fn>).mockResolvedValue({
    contact_id: 'contact-1',
    space_id: 'space-1',
    name: 'CONTACT X Lee',
    phone_number: '60123456789',
    expires_at: '2026-09-01T00:00:00Z',
    portal_slug: 'contact-x',
    // Every kind is gated now (PLAN-portal-forms-market-segment D2) - this
    // suite is about revision badges, not visibility.
    visible_form_types: ['complaint', 'stock_inquiry', 'purchase_request', 'sponsorship_form'],
  });
  (fetchSubmissions as ReturnType<typeof vi.fn>).mockImplementation(
    async (kind: string) =>
      kind === 'stock_inquiry' ? [REVISED_ROW, UNREVISED_ROW] : [],
  );
});

describe('PortalLanding - revision badge and date on the submission card', () => {
  it('shows "Rev 2" and a Revised date line for a revised row', async () => {
    render(<PortalLanding slug="contact-x" />);
    await screen.findByText('SI-26-0184');

    expect(screen.getByText('Rev 2')).toBeInTheDocument();
    expect(screen.getByText(/^Revised /)).toBeInTheDocument();
  });

  it('shows neither the badge nor a Revised line for an unrevised row', async () => {
    render(<PortalLanding slug="contact-x" />);
    const freshCard = await screen.findByText('SI-26-0200');

    expect(screen.queryByText('Rev 0')).toBeNull();
    // Only the revised row's "Revised ..." line exists; the fresh row keeps
    // its plain created-date line instead.
    await waitFor(() =>
      expect(screen.getAllByText(/^Revised /)).toHaveLength(1),
    );
    expect(freshCard).toBeInTheDocument();
  });
});

describe('PortalLanding - revising chip for a parked, unsent draft', () => {
  beforeEach(() => {
    (fetchSubmissions as ReturnType<typeof vi.fn>).mockImplementation(
      async (kind: string) =>
        kind === 'stock_inquiry' ? [REVISING_ROW, NOT_REVISING_ROW] : [],
    );
  });

  it('shows the Revising chip for a row with a parked draft', async () => {
    render(<PortalLanding slug="contact-x" />);
    await screen.findByText('SI-26-0777');

    const chips = screen.getAllByTestId('revising-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent('Revising');
  });

  it('shows no Revising chip for a row without a parked draft', async () => {
    render(<PortalLanding slug="contact-x" />);
    const notRevisingCard = await screen.findByText('SI-26-0888');

    expect(notRevisingCard).toBeInTheDocument();
    expect(screen.getAllByTestId('revising-chip')).toHaveLength(1);
  });
});
