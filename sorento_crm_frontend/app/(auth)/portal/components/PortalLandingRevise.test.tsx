/**
 * The long-press preview card is the SECOND revise surface
 * (UAC-portal-submission-revisions B6): same policy block, same sentence, same
 * budget line as the detail page, and it acts through the same form rather than
 * carrying a second copy of it.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type { PortalSubmissionSummary } from '../lib/portal-client';

const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(''),
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

import { fetchMeWithGrace, fetchSubmission, fetchSubmissions } from '../lib/portal-client';
import { PortalLanding } from './PortalLanding';

const ROW: PortalSubmissionSummary = {
  id: 'si-1',
  kind: 'stock_inquiry',
  title: 'Stock inquiry',
  document_number: 'SI-26-0184',
  reference: 'SI-26-0184',
  status: 'pending_purchasing',
  is_editable: false,
  is_draft: false,
  created_at: '2026-07-20T00:00:00Z',
  product_code: 'ABC-123',
};

function submissionWith(revision: Record<string, unknown> | null) {
  return { ...ROW, revision };
}

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
    // suite is about the revise composer, not visibility.
    visible_form_types: ['complaint', 'stock_inquiry', 'purchase_request', 'sponsorship_form'],
  });
  (fetchSubmissions as ReturnType<typeof vi.fn>).mockImplementation(async (kind: string) =>
    kind === 'stock_inquiry' ? [ROW] : [],
  );
});

async function openPreview() {
  render(<PortalLanding slug="contact-x" />);
  const card = await screen.findByText('SI-26-0184');
  fireEvent.contextMenu(card);
  return card;
}

describe('PortalLanding - revise from the long-press preview card', () => {
  it('offers Revise plus the remaining budget when the policy allows it', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(
      submissionWith({
        enabled: true,
        allowed: true,
        used: 1,
        max: 3,
        remaining: 2,
        blocked_reason: null,
      }),
    );
    await openPreview();

    expect(await screen.findByRole('button', { name: 'Revise' })).toBeInTheDocument();
    expect(screen.getByText('2 of 3 revisions left')).toBeInTheDocument();
  });

  it('opens the revise composer on the submission itself, never a second form', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(
      submissionWith({
        enabled: true,
        allowed: true,
        used: 1,
        max: 3,
        remaining: 2,
        blocked_reason: null,
      }),
    );
    await openPreview();
    fireEvent.click(await screen.findByRole('button', { name: 'Revise' }));

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith('/portal/c/contact-x/stock_inquiry/si-1?revise=1'),
    );
  });

  it('shows the same one sentence and no action when the policy blocks it', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(
      submissionWith({
        enabled: true,
        allowed: false,
        used: 3,
        max: 3,
        remaining: 0,
        blocked_reason: 'You have used all 3 revisions.',
      }),
    );
    await openPreview();

    expect(await screen.findByText('You have used all 3 revisions.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revise' })).toBeNull();
  });

  it('renders no revise surface at all when the submission carries no policy block', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(submissionWith(null));
    await openPreview();

    // The card still opens; it just carries no action and no sentence.
    expect(await screen.findByText('Document number')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revise' })).toBeNull();
    expect(screen.queryByTestId('revise-blocked')).toBeNull();
  });
});
