/**
 * Access model S6, mock v8 (AC-AM-1, AC-AM-2, AC-AM-3, AC-AM-7, AC-AM-23). Red test: page.tsx absent.
 * Hooks (hooks/useChatbotAccess.ts): useChatbotRegistry, useChatbotRole(id), useSetRoleGrants(id)
 * -> { mutate({domains, fields}) }, useUpdateChatbotRole(id), useDeleteChatbotRole,
 * useChatbotRoleContacts(id) -> { data: [{ id, name }] }.
 * S6 gaps round: Delete and per-row Unlink are deferred actions (`chatbot_role.delete`,
 * `chatbot_role_contact.unlink` with entity id "<role_id>:<contact_id>"), useAddRoleContacts(id)
 * -> { mutate({ contact_ids }) }, prev/next via RecordNavigation/ListPager, customers choice is
 * the RadioGroup component (role="radio" + aria-checked).
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ChatbotRolePage from './page';

const mutate = vi.fn();
const updateMutate = vi.fn();
const contactsData = vi.hoisted(() => ({ rows: [{ id: 'c1', name: 'CONTACT O' }] as { id: string; name: string | null }[] }));
const deleteMutate = vi.fn();
const addMutate = vi.fn();
const deferredStart = vi.fn();
const deferredInput = vi.fn();
const rowRun = vi.fn();
const rowInput = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useParams: () => ({ id: 'r1' }),
  usePathname: () => '/user-management/chatbot-roles/r1',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// Container pulls SettingsProvider context this unit test does not need.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div data-testid="container">{children}</div>,
}));
vi.mock('@/components/ui/collapsible', () => ({
  Collapsible: ({ children }: { children: React.ReactNode }) => <div data-testid="collapsible">{children}</div>,
  CollapsibleTrigger: ({ children }: { children: React.ReactNode }) => <button type="button">{children}</button>,
  CollapsibleContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common/RecordNavigation', () => ({
  default: () => <div data-testid="record-nav" />,
}));
vi.mock('@/components/common/ListPager', () => ({
  default: () => <div data-testid="record-nav" />,
}));
vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: (input: unknown) => {
    deferredInput(input);
    return { pending: null, isPending: false, isBlocked: false, start: deferredStart, cancel: vi.fn(), countdown: null };
  },
}));
vi.mock('@/hooks/useDeferredRowAction', () => ({
  useDeferredRowAction: (input: unknown) => {
    rowInput(input);
    return { run: rowRun, targetId: null, countdown: null, isPending: false };
  },
}));
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: { onChange: (v: string[]) => void }) => (
    <button type="button" data-testid="contacts-picker" onClick={() => props.onChange(['c9'])}>
      pick c9
    </button>
  ),
}));
const dom = (name: string, label: string, group: string) => ({
  name, label, group, supported: true, access_section: null, escalation_agent_code: null, escalation_team_code: null, fields: [],
});
vi.mock('@/hooks/useChatbotAccess', () => ({
  useChatbotRegistry: () => ({
    data: { domains: [dom('master_products', 'Product details', 'Products and marketing'), dom('portal_link', 'Their request portal link', 'Products and marketing'), dom('forms', 'Forms', 'Products and marketing')] },
    isLoading: false,
    isError: false,
  }),
  useChatbotRole: () => ({
    data: { id: 'r1', code: 'purchasing', name: 'Purchasing', description: '', audience_tier: 'office', sees_all_customers: true, domains: ['master_products'], fields: [], contact_count: 1 },
    isLoading: false,
    isError: false,
  }),
  useChatbotRoles: () => ({
    data: [{ id: 'r0', name: 'A' }, { id: 'r1', name: 'Purchasing' }, { id: 'r2', name: 'Z' }],
    isLoading: false,
    isError: false,
  }),
  useAddRoleContacts: () => ({ mutate: addMutate, isPending: false }),
  useChatbotRoleContacts: () => ({ data: contactsData.rows, isLoading: false, isError: false }),
  useSetRoleGrants: () => ({ mutate, isPending: false }),
  useUpdateChatbotRole: () => ({ mutate: updateMutate, isPending: false }),
  useDeleteChatbotRole: () => ({ mutate: deleteMutate, isPending: false }),
}));

afterEach(() => {
  cleanup();
  mutate.mockReset();
  updateMutate.mockReset();
  contactsData.rows = [{ id: 'c1', name: 'CONTACT O' }];
  deleteMutate.mockReset();
  addMutate.mockReset();
  deferredStart.mockReset();
  deferredInput.mockReset();
  rowRun.mockReset();
  rowInput.mockReset();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatbotRolePage />
    </QueryClientProvider>,
  );
}

describe('Chatbot role page (v8)', () => {
  it('AC-AM-1 shows Name, Tier and the customers radio from the role', () => {
    renderPage();
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Purchasing');
    expect(screen.getByText('Tier')).toBeTruthy();
    expect(screen.getByText('Office')).toBeTruthy();
    expect(screen.getByRole('radio', { name: /All customers/ }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radio', { name: /Only the customers linked/ }).getAttribute('aria-checked')).toBe('false');
  });

  it('AC-AM-7 shows switches in role mode with the saved state', () => {
    renderPage();
    expect(screen.getByRole('switch', { name: 'Product details' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Forms' }).getAttribute('aria-checked')).toBe('false');
    expect(screen.queryByText(/Reset to role/)).toBeNull();
  });

  it('AC-AM-4 lists the contacts with this role and has a Delete button', () => {
    renderPage();
    expect(screen.getByText('CONTACT O')).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Delete/ })).toBeTruthy();
  });

  it('AC-AM-2 Save (2) after two switches and one setRoleGrants with the full set', () => {
    renderPage();
    fireEvent.click(screen.getByRole('switch', { name: 'Their request portal link' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Forms' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save (2)' }));
    expect(mutate).toHaveBeenCalledTimes(1);
    const p = mutate.mock.calls[0][0] as { domains: string[]; fields: string[] };
    expect([...p.domains].sort()).toEqual(['forms', 'master_products', 'portal_link']);
    expect(p.fields).toEqual([]);
  });

  it('S6 Delete is the deferred chatbot_role.delete action and never calls deleteRole on click', () => {
    renderPage();
    const input = deferredInput.mock.calls.at(-1)?.[0] as Record<string, unknown>;
    expect(input).toMatchObject({ actionKey: 'chatbot_role.delete', entityType: 'chatbot_role', entityId: 'r1' });
    fireEvent.click(screen.getByRole('button', { name: /^Delete/ }));
    expect(deferredStart).toHaveBeenCalledTimes(1);
    expect(deleteMutate).not.toHaveBeenCalled();
  });

  it('S6 shows prev/next record navigation', () => {
    renderPage();
    expect(screen.getAllByTestId('record-nav').length).toBeGreaterThan(0);
  });

  it('S6 the page is wrapped in the shared Container', () => {
    renderPage();
    expect(screen.getAllByTestId('container').length).toBeGreaterThan(0);
  });

  it('S6 Add contacts opens a picker whose choice calls the add endpoint', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Add contacts/ }));
    fireEvent.click(screen.getByTestId('contacts-picker'));
    const confirm = screen.queryByRole('button', { name: /^Add$/ });
    if (confirm) fireEvent.click(confirm);
    expect(addMutate).toHaveBeenCalledTimes(1);
    expect(addMutate.mock.calls[0][0]).toEqual({ contact_ids: ['c9'] });
  });

  it('S6 each holder row has an Unlink that is the deferred chatbot_role_contact.unlink action', () => {
    renderPage();
    const input = rowInput.mock.calls.at(-1)?.[0] as Record<string, unknown>;
    expect(input).toMatchObject({ actionKey: 'chatbot_role_contact.unlink', entityType: 'chatbot_role_contact' });
    fireEvent.click(screen.getByRole('button', { name: /Unlink CONTACT O/ }));
    expect(rowRun).toHaveBeenCalledTimes(1);
    expect(rowRun.mock.calls[0][0]).toMatchObject({ id: 'r1:c1', subject: 'CONTACT O' });
  });

  it('S6 has the same collapsible Advanced hand-off section as the contact page', () => {
    renderPage();
    expect(screen.getAllByTestId('collapsible').length).toBeGreaterThan(0);
    expect(screen.getByText('Advanced')).toBeTruthy();
  });

  it('S6 the role description is editable and saved through the update hook', () => {
    renderPage();
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Buys stock' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save (1)' }));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0]).toMatchObject({ description: 'Buys stock' });
  });

  it('S6 a contact with no name renders a non-empty fallback label', () => {
    contactsData.rows = [{ id: 'c2', name: null }];
    renderPage();
    const link = document.querySelector('a[href="/user-management/contacts/c2/access"]');
    expect(link).not.toBeNull();
    expect((link?.textContent ?? '').trim().length).toBeGreaterThan(0);
  });
});
