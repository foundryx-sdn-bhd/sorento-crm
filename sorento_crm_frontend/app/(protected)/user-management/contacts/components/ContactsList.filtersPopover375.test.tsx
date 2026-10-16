/**
 * CONTACT-TAGS browser-pass D3: the Filters popover content is bounded by the available viewport height
 * and scrolls (overflow-y auto), so every filter, "No customers linked", "No tags" and "Clear filters"
 * stay reachable at 375x812. Asserted on the content element's class / inline style, not pixels.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}
if (!('ResizeObserver' in window)) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? vi.fn();

const apiFetchMock = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetchMock(...a) }));
vi.mock('@/hooks/useRespondContactOutbound', () => ({
  RESPOND_CONTACTS_OUTBOUND_KEY: 'respond-contacts-outbound',
  useRespondContactOutboundMutations: () => ({
    setOne: { mutate: vi.fn(), isPending: false }, setBulk: { mutate: vi.fn(), isPending: false },
  }),
}));
vi.mock('@/components/contacts/PortalLinkButton', () => ({ default: () => null }));
vi.mock('@/services/contactImpersonationService', () => ({ startContactImpersonation: vi.fn() }));
vi.mock('next-auth/react', () => ({ useSession: () => ({ data: { user: { email: 'admin@zzt.test' } } }) }));
vi.mock('@/lib/is-superadmin', () => ({ isSuperadminUser: () => false }));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/user-management/contacts',
  useSearchParams: () => ({ get: () => null }),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const ROW = vi.hoisted(() => ({
  id: 'contact-1', name: 'ZZT One', phone_number: '+60100000011', first_name: 'ZZT', last_name: 'One',
  respond_io_id: '1', outbound_enabled: true, access_type_codes: [], access_types: [], regions: ['west'],
  created_at: '2026-01-05T02:00:00', updated_at: '2026-01-05T02:00:00', linked_user_id: null, linked_user_name: null,
}));
vi.mock('../[id]/services/contactService', () => ({
  getContacts: vi.fn().mockResolvedValue({ data: [ROW], pagination: { total: 1 } }),
  getContact: vi.fn(),
  getContactCompanies: vi.fn(),
}));

import ContactsList from './ContactsList';

beforeEach(() => {
  apiFetchMock.mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
});
afterEach(() => cleanup());

describe('Filters popover height bound', () => {
  it('is capped at the available viewport height and scrolls', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContactsList />
      </QueryClientProvider>,
    );
    await screen.findByText('+60100000011');
    fireEvent.pointerDown(screen.getByRole('button', { name: /^filters/i }), { button: 0, ctrlKey: false });
    const menu = (await screen.findByRole('menu')) as HTMLElement;
    const bound = `${menu.className} ${menu.getAttribute('style') ?? ''}`;
    expect(bound).toMatch(/max-h-\S*available-height|max-height:[^;]*available-height/);
    expect(bound).toMatch(/overflow-y-auto/);
  });
});
