/**
 * New Product Application is one kind in the portal's kind selector, listed from its own
 * service, with its label and count.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('../lib/portal-client', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...original,
    fetchMeWithGrace: vi.fn(),
    fetchSubmissions: vi.fn(async () => []),
    portalLogout: vi.fn(),
    readPortalToken: vi.fn(() => 'tok-123'),
  };
});
vi.mock('../lib/new-product-application-service', () => ({
  listNewProductApplicationsAsSummaries: vi.fn(),
}));

import { fetchMeWithGrace } from '../lib/portal-client';
import { listNewProductApplicationsAsSummaries } from '../lib/new-product-application-service';
import { PortalLanding } from './PortalLanding';

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  (fetchMeWithGrace as ReturnType<typeof vi.fn>).mockResolvedValue({
    contact_id: 'contact-1',
    space_id: 'space-1',
    name: 'Jayson',
    phone_number: '60123456789',
    expires_at: '2026-12-01T00:00:00Z',
    portal_slug: 'contact-x',
    visible_form_types: ['new_product_application'],
  });
  (
    listNewProductApplicationsAsSummaries as ReturnType<typeof vi.fn>
  ).mockResolvedValue([
    {
      id: 'a-1',
      kind: 'new_product_application',
      title: 'XY-1234-MB',
      document_number: 'NPA-000001',
      reference: null,
      status: 'draft',
      status_label: 'Draft',
      is_editable: true,
      is_draft: true,
      created_at: '2026-10-17T01:00:00',
      customer_name: 'EXAMPLE DEVELOPER SDN BHD',
      project_title: 'EXAMPLE RESIDENCE, TOWER B',
    },
  ]);
});

describe('PortalLanding - New Product Application kind', () => {
  it('lists the kind with its label and its applications', async () => {
    render(<PortalLanding slug="contact-x" />);
    await waitFor(() =>
      expect(listNewProductApplicationsAsSummaries).toHaveBeenCalled(),
    );
    expect(
      (await screen.findAllByText('New Product Application')).length,
    ).toBeGreaterThan(0);
    expect(await screen.findByText('NPA-000001')).toBeInTheDocument();
  });
});
