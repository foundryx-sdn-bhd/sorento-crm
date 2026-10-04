/**
 * Review round 2 (r8), AC-L8: a search term that finds nothing must read
 * "No submissions match your filters." with a Clear filters action - not the
 * generic "No X submissions yet." empty state, which today is what shows
 * because `SubmissionList`'s empty branch only compares `items.length` to
 * the CLIENT-side `filters`, and a search term is answered server-side
 * (`fetchSubmissions(kind, q)`) so a search-only zero result already arrives
 * with `items.length === 0` and no `filters` set at all.
 *
 * Mocking pattern mirrors `PortalLanding.viewToggle.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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

import { fetchMeWithGrace, fetchSubmissions } from '../lib/portal-client';
import { PortalLanding } from './PortalLanding';

const ME = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-09-01T00:00:00Z',
  portal_slug: 'contact-x',
  // Every kind is gated now (PLAN-portal-forms-market-segment D2) - this
  // suite is about search, not visibility, so it grants the base four the
  // same way `fetchSubmissions` already answered them before that change.
  visible_form_types: ['complaint', 'stock_inquiry', 'purchase_request', 'sponsorship_form'],
};

const ROW: PortalSubmissionSummary = {
  id: 'si-1',
  kind: 'stock_inquiry',
  title: 'Stock inquiry',
  document_number: 'SI-26-0184',
  reference: 'SI-26-0184',
  status: 'pending_purchasing',
  is_editable: false,
  is_draft: false,
  created_at: '2026-07-01T00:00:00Z',
  product_code: 'ZZT-PROD',
};

beforeEach(() => {
  vi.clearAllMocks();
  searchParams = new URLSearchParams('');
  window.localStorage.clear();
  (fetchMeWithGrace as ReturnType<typeof vi.fn>).mockResolvedValue(ME);
});

describe('PortalLanding - search with zero rows (AC-L8, review round 2)', () => {
  it('shows "No submissions match your filters." with a Clear filters action, not the generic empty state', async () => {
    (fetchSubmissions as ReturnType<typeof vi.fn>).mockImplementation(
      async (kind: string, q?: string) => {
        if (kind !== 'stock_inquiry') return [];
        return q && q.trim() ? [] : [ROW];
      },
    );

    render(<PortalLanding slug="contact-x" />);
    await screen.findByText('SI-26-0184');

    fireEvent.change(screen.getByLabelText('Search submissions'), {
      target: { value: 'ZZT-NOTHING-MATCHES' },
    });

    await waitFor(() =>
      expect(screen.queryByText('SI-26-0184')).toBeNull(),
    );

    expect(
      await screen.findByText('No submissions match your filters.'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Clear filters' }),
    ).toBeInTheDocument();
  });
});
