/**
 * PLAN-portal-forms-market-segment D7 r3: a `/new` page makes no request of
 * its own to check visibility - it reuses the same `/me` fetch every
 * submission page already makes for form defaults, and renders the same
 * inline blocked message the edit page's real 403 uses (AC-L4). The server
 * remains the enforcement; this only avoids an empty form.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { PortalContact } from '../lib/portal-client';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...original,
    fetchMe: vi.fn(),
    fetchSubmission: vi.fn(),
    lookupSet: vi.fn().mockResolvedValue({ options: [], defaultValue: null }),
  };
});

import { fetchMe } from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-08-01T00:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SubmissionForm - a /new page reads visible_form_types off the already-fetched /me (D7 r3)', () => {
  it('blocks a kind the contact cannot see, with a link back to the landing', async () => {
    (fetchMe as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...CONTACT,
      visible_form_types: ['stock_inquiry'],
    });
    render(<SubmissionForm kind="complaint" />);

    expect(
      await screen.findByText('Complaint is not available for your account.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to your forms' })).toBeInTheDocument();
    // No empty form underneath the message.
    expect(screen.queryByText('Project title')).toBeNull();
  });

  it('renders the form as usual for a kind the contact can see', async () => {
    (fetchMe as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...CONTACT,
      visible_form_types: ['complaint'],
    });
    render(<SubmissionForm kind="complaint" />);

    await screen.findByText(CONTACT.name as string);
    expect(screen.queryByText('is not available for your account.', { exact: false })).toBeNull();
  });

  it('makes no extra request beyond the existing /me fetch (no new guard fetch, D7)', async () => {
    (fetchMe as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...CONTACT,
      visible_form_types: ['stock_inquiry'],
    });
    render(<SubmissionForm kind="complaint" />);

    await screen.findByText('Complaint is not available for your account.');
    expect(fetchMe).toHaveBeenCalledTimes(1);
  });
});
