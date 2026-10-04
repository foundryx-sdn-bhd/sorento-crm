/**
 * Customer detail -> "WhatsApp contacts" section (UAC AC-13, round 2 Q8 b; PLAN D5).
 * `getCustomerLinkedContacts` (services/customerService.ts) is mocked, never fetch; the real
 * `useCustomerLinkedContacts` hook runs. The service unwraps the envelope, so the mock answers an array.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const services = vi.hoisted(() => ({ getCustomerLinkedContacts: vi.fn() }));
vi.mock('../services/customerService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...services,
}));

import CustomerLinkedContactsSection from './CustomerLinkedContactsSection';

const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

const NAMED = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  contact_id: 'contact-named',
  name: 'Ah Beng',
  phone_number: '60123456789',
  is_primary: false,
  created_at: '2026-01-01T08:00:00',
};
const UNNAMED = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  contact_id: 'contact-unnamed',
  name: null,
  phone_number: '60190000432',
  is_primary: false,
  created_at: '2026-01-02T08:00:00',
};

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CustomerLinkedContactsSection customerId="cust-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => services.getCustomerLinkedContacts.mockReset());
afterEach(() => cleanup());

describe('CustomerLinkedContactsSection', () => {
  it('AC-13: one row per link, the name links to the contact record and the phone shows', async () => {
    services.getCustomerLinkedContacts.mockResolvedValue([NAMED, UNNAMED]);
    renderSection();

    const link = await screen.findByRole('link', { name: 'Ah Beng' });
    expect(link).toHaveAttribute('href', '/user-management/contacts/contact-named');
    expect(screen.getByText('60123456789')).toBeInTheDocument();
    expect(services.getCustomerLinkedContacts).toHaveBeenCalledWith('cust-1');
    expect(document.body.textContent ?? '').not.toMatch(UUID_RE);
  });

  it('AC-13: an unnamed contact shows its phone number as the link', async () => {
    services.getCustomerLinkedContacts.mockResolvedValue([UNNAMED]);
    renderSection();

    const link = await screen.findByRole('link', { name: '60190000432' });
    expect(link).toHaveAttribute('href', '/user-management/contacts/contact-unnamed');
  });

  it('AC-13: no links shows the empty state and no button', async () => {
    services.getCustomerLinkedContacts.mockResolvedValue([]);
    renderSection();

    expect(await screen.findByText('No WhatsApp contacts linked')).toBeInTheDocument();
    expect(
      screen.getByText("Link this customer from a contact's Customers card"),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
