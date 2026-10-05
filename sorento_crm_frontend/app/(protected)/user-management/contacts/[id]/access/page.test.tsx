/**
 * Access model S6, mock v8 (AC-AM-7, AC-AM-22). Red test: the Access tab still renders the
 * Access Agents grid and Field reveals card. Hooks (hooks/useChatbotAccess.ts): useChatbotRegistry,
 * useChatbotRoles (items carry audience_tier + sees_all_customers), useContactAccess(contactId)
 * (GET shape), useSetContactAccess(contactId) -> { mutate({role_ids, overrides}) }.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactAccessPage from './page';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}

const mutate = vi.fn();
const view = vi.hoisted(() => ({ withAgent: true, extraOverrides: 0 }));

vi.mock('@/components/ui/collapsible', () => ({
  Collapsible: ({ children }: { children: React.ReactNode }) => <div data-testid="collapsible">{children}</div>,
  CollapsibleTrigger: ({ children }: { children: React.ReactNode }) => <button type="button">{children}</button>,
  CollapsibleContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
const perms = vi.hoisted(() => ({ manage: true }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => (slug === 'user_management.reference_data.manage' ? perms.manage : true),
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));
vi.mock('@/lib/api', () => ({ apiFetch: () => new Promise(() => {}) }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useParams: () => ({ id: 'c1' }),
  usePathname: () => '/user-management/contacts/c1/access',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('../components/contact-context', () => ({
  useContact: () => ({ isLoading: false, contactId: 'c1' }),
}));
vi.mock('../components/ContactMediaAccessSection', () => ({ default: () => <div>media-access-stub</div> }));
vi.mock('@/hooks/useChatbotAccess', () => ({
  useChatbotRegistry: () => ({
    data: {
      domains: [
        { name: 'incoming', label: 'Incoming stock', group: 'Stock and incoming', supported: true, access_section: null, escalation_agent_code: view.withAgent ? 'inc' : null, escalation_agent_name: view.withAgent ? 'Incoming Stock Agent' : null, escalation_team_code: 'purchasing',
          fields: [{ key: 'incoming.eta', label: 'ETA', kind: 'field' }, { key: 'incoming.consignee', label: 'Consignee', kind: 'field' }] },
        { name: 'ideate', label: 'Share ideas', group: 'Other', supported: true, access_section: null, escalation_agent_code: null, escalation_team_code: null, fields: [] },
      ],
    },
    isLoading: false,
    isError: false,
  }),
  useChatbotRoles: () => ({
    data: [{ id: 'r1', code: 'purchasing', name: 'Purchasing', audience_tier: 'office', sees_all_customers: true, domains: ['incoming'], fields: ['incoming.eta'], contact_count: 5 }],
    isLoading: false,
    isError: false,
  }),
  useContactAccess: () => ({
    data: {
      roles: [{ id: 'r1', code: 'purchasing', name: 'Purchasing' }],
      overrides: [
        { domain_name: 'incoming', field_key: 'incoming.consignee', granted: true },
        ...Array.from({ length: view.extraOverrides }, (_, i) => ({ domain_name: 'incoming', field_key: `incoming.extra${i}`, granted: true })),
      ],
      effective: { domains: ['incoming'], attributes: ['incoming.eta', 'incoming.consignee'], sees_all_customers: true },
    },
    isLoading: false,
    isError: false,
  }),
  useSetContactAccess: () => ({ mutate, isPending: false }),
}));

afterEach(() => {
  cleanup();
  perms.manage = true;
  mutate.mockReset();
  view.withAgent = true;
  view.extraOverrides = 0;
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactAccessPage />
    </QueryClientProvider>,
  );
}

describe('Contact Access tab (v8)', () => {
  it('AC-AM-7 renders the summary, the role chip with tier and the customers line', () => {
    renderPage();
    expect(screen.getByText(/This contact can/)).toBeTruthy();
    expect(screen.getByText('Purchasing · Office')).toBeTruthy();
    expect(screen.getAllByText(/All customers/i).length).toBeGreaterThan(0);
  });

  it('AC-AM-7 renders switches, a collapsed Advanced section, and the changed row with Reset to role', () => {
    renderPage();
    expect(screen.getByRole('switch', { name: 'Incoming stock' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Share ideas' }).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText('Advanced')).toBeTruthy();
    expect(screen.getByText(/Changed for this contact/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reset to role' })).toBeTruthy();
  });

  it('AC-AM-7 the old Access Agents grid and Field reveals are gone', () => {
    renderPage();
    expect(screen.queryByText('Access Agents')).toBeNull();
    expect(screen.queryByText('Field reveals')).toBeNull();
  });

  it('AC-AM-7 Save shows (n) per change and sends one PUT with role_ids and overrides', () => {
    renderPage();
    fireEvent.click(screen.getByRole('switch', { name: 'Share ideas' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save (1)' }));
    expect(mutate).toHaveBeenCalledTimes(1);
    const p = mutate.mock.calls[0][0] as { role_ids: string[]; overrides: unknown[] };
    expect(p.role_ids).toEqual(['r1']);
    expect(p.overrides).toEqual(
      expect.arrayContaining([
        { domain_name: 'incoming', field_key: 'incoming.consignee', granted: true },
        { domain_name: 'ideate', field_key: null, granted: true },
      ]),
    );
  });

  it('AC-AM-7 the Advanced table shows the agent display name, not a title-cased code', () => {
    renderPage();
    expect(screen.getByText('Incoming Stock Agent')).toBeTruthy();
    expect(screen.queryByText('Inc')).toBeNull();
  });

  it('AC-AM-7 Reset to role counts as a change and drops that row overrides from the PUT', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Reset to role' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save (1)' }));
    const p = mutate.mock.calls[0][0] as { overrides: { domain_name: string }[] };
    expect(p.overrides.filter((o) => o.domain_name === 'incoming')).toEqual([]);
  });

  it('S6 Advanced is a Collapsible holding a DataGrid, not a raw table', () => {
    renderPage();
    expect(screen.getAllByTestId('collapsible').length).toBeGreaterThan(0);
    // Every table on the page is the DataGrid's own, never a hand-written one.
    const tables = Array.from(document.querySelectorAll('table'));
    expect(tables.length).toBeGreaterThan(0);
    for (const t of tables) expect(t.getAttribute('data-slot')).toBe('data-grid-table');
  });

  it('S6 Advanced shows an empty state when no granted domain has an agent', () => {
    view.withAgent = false;
    renderPage();
    expect(screen.getByText(/no hand-off/i)).toBeTruthy();
  });

  it('S6 the summary shows an extra details line when the contact has field add-overrides', () => {
    view.extraOverrides = 7;
    renderPage();
    // 7 extras plus the one consignee add-override on the incoming domain.
    expect(screen.getByText(/8 extra incoming details/)).toBeTruthy();
  });

  it('Q3 without reference_data.manage the grant actions are unavailable but removals stay', () => {
    perms.manage = false;
    renderPage();
    expect(screen.queryByText('Add role...')).toBeNull();
    const ideas = screen.getByRole('switch', { name: 'Share ideas' }) as HTMLButtonElement;
    expect(ideas.disabled).toBe(true);
    const incoming = screen.getByRole('switch', { name: 'Incoming stock' }) as HTMLButtonElement;
    expect(incoming.disabled).toBe(false);
    expect(screen.getByRole('button', { name: 'Remove role Purchasing' })).toBeTruthy();
  });

  it('Q3 with reference_data.manage everything is available', () => {
    renderPage();
    expect(screen.getByText('Add role...')).toBeTruthy();
    expect((screen.getByRole('switch', { name: 'Share ideas' }) as HTMLButtonElement).disabled).toBe(false);
  });
});
