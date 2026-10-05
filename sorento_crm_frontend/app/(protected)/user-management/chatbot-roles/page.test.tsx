/**
 * Access model S6, mock v8 (AC-AM-1, AC-AM-23). Red test: chatbot-roles/page.tsx and
 * hooks/useChatbotAccess.ts do not exist. useChatbotRoles() -> { data: Role[], isLoading, isError }.
 * Columns: Role, Tier, Customers, Contacts.
 * S6 gaps round: DataGrid carries a listingKey (column personalization) and the page sits in the shared Container.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ChatbotRolesPage from './page';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useParams: () => ({}),
  usePathname: () => '/user-management/chatbot-roles',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const prefs = vi.hoisted(() => ({ useListingColumnPreferences: vi.fn() }));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: (...a: unknown[]) => {
    prefs.useListingColumnPreferences(...a);
    return { resetToDefaults: async () => {}, isLoading: false };
  },
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async () => ({ listing_key: 'k', config: null })),
  upsertUserListColumnConfig: vi.fn(async (listingKey: string, payload: unknown) => ({ listing_key: listingKey, config: payload })),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));
// Container pulls SettingsProvider context this unit test does not need.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div data-testid="container">{children}</div>,
}));
vi.mock('@/hooks/useChatbotAccess', () => ({
  useChatbotRoles: () => ({
    data: [
      { id: 'r1', code: 'purchasing', name: 'Purchasing', description: '', audience_tier: 'office', sees_all_customers: true, domains: [], fields: [], contact_count: 5 },
      { id: 'r2', code: 'dealer', name: 'Dealer', description: '', audience_tier: 'dealer', sees_all_customers: false, domains: [], fields: [], contact_count: 0 },
    ],
    isLoading: false,
    isError: false,
  }),
  useCreateChatbotRole: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteChatbotRole: () => ({ mutate: vi.fn(), isPending: false }),
}));

afterEach(cleanup);

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatbotRolesPage />
    </QueryClientProvider>,
  );
}

describe('Chatbot Roles list page', () => {
  it('AC-AM-1 has the Role, Tier, Customers and Contacts columns', () => {
    renderPage();
    for (const h of ['Role', 'Tier', 'Customers', 'Contacts']) {
      expect(screen.getAllByText(h).length).toBeGreaterThan(0);
    }
  });

  it('AC-AM-23 renders a row per role with tier, customers scope and contact count', () => {
    renderPage();
    expect(screen.getByText('Purchasing')).toBeTruthy();
    expect(screen.getAllByText('Dealer').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Office')).toBeTruthy();
    expect(screen.getByText('All')).toBeTruthy();
    expect(screen.getByText('Linked only')).toBeTruthy();
    expect(screen.getByText('5')).toBeTruthy();
  });

  it('AC-AM-1 has an Add role button', () => {
    renderPage();
    expect(screen.getByRole('button', { name: /add role/i })).toBeTruthy();
  });

  it('S6 the grid has a listingKey so column order and visibility can be personalised', () => {
    renderPage();
    const keys = prefs.useListingColumnPreferences.mock.calls.map((c) => JSON.stringify(c));
    expect(keys.some((k) => k.includes('chatbot'))).toBe(true);
  });

  it('S6 the page is wrapped in the shared Container', () => {
    renderPage();
    expect(screen.getAllByTestId('container').length).toBeGreaterThan(0);
  });

  it('S6 a search box filters the rows by name', () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'Deal' } });
    expect(screen.queryByText('Purchasing')).toBeNull();
    expect(screen.getAllByText('Dealer').length).toBeGreaterThan(0);
  });
});
