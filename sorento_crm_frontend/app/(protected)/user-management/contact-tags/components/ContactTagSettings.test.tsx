/**
 * CONTACT-TAGS S2, UAC T5.2: the tag detail "Access settings" section, view and edit.
 * Assumed: ./ContactTagSettings (default export), props { tag: ContactTag, editing: boolean,
 * onDone: () => void }; saves through updateContactTag(tag.id, { settings }) from
 * ../services/contactTagService. Test ids: each group root `settings-group`, its title
 * `settings-group-title`, a bool/3-state row `setting-row-<key>` (radios Not set / On / Off, agents
 * Allow / Deny), the group switch is role=switch named /set on this tag/i, button "Save".
 * A failed save shows the thrown message and keeps the editor (and the edit) in place.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contact-tags/t1',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

// Catalog fetches the group editors make (access types, brands, agents ...): empty lists.
vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] }),
}));

const updateContactTag = vi.fn();
vi.mock('../services/contactTagService', () => ({
  updateContactTag: (...a: unknown[]) => updateContactTag(...a),
  getContactTag: vi.fn(),
  getContactTags: vi.fn().mockResolvedValue({ data: [], pagination: { total: 0 } }),
}));

import ContactTagSettings from './ContactTagSettings';

const GROUPS = [
  'Access types', 'Brands', 'Companies', 'Market segments', 'Regions', 'Chatbot', 'Portal forms',
  'Field reveals', 'Chatbot agents', 'Agent field access', 'Media access', 'Attachment types',
  'Stock visibility', 'Spec visibility', 'Messaging (outbound)',
];

const tag = (settings: Record<string, unknown>) => ({
  id: 't1', name: 'Dealer', description: null, is_active: true, sort_order: 0, settings,
  contact_count: 0, created_at: '', updated_at: '',
});

function render(settings: Record<string, unknown>, editing: boolean, onDone = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  rtlRender(
    <QueryClientProvider client={client}>
      <ContactTagSettings tag={tag(settings) as never} editing={editing} onDone={onDone} />
    </QueryClientProvider>,
  );
  return { onDone };
}

const group = (title: string) =>
  screen
    .getAllByTestId('settings-group')
    .find((g) => within(g).getByTestId('settings-group-title').textContent === title)!;

beforeEach(() => {
  vi.clearAllMocks();
  updateContactTag.mockResolvedValue(tag({}));
});

describe('ContactTagSettings view', () => {
  it('renders the 15 groups in the mock order', () => {
    render({}, false);
    expect(screen.getAllByTestId('settings-group-title').map((n) => n.textContent)).toEqual(GROUPS);
  });

  it('unset groups read Not set; a set group shows its values', () => {
    render({ portal_forms: { purchase_request: false }, regions: ['west'] }, false);
    expect(within(group('Brands')).getByText('Not set')).toBeInTheDocument();
    expect(within(group('Portal forms')).queryByText('Not set')).toBeNull();
    expect(within(group('Portal forms')).getByText(/purchase request/i)).toBeInTheDocument();
    expect(within(group('Portal forms')).getByText('Off')).toBeInTheDocument();
    expect(within(group('Regions')).getByText(/west/i)).toBeInTheDocument();
  });
});

describe('ContactTagSettings edit', () => {
  it('saving sends PATCH settings holding only the groups switched on', async () => {
    render({}, true);
    fireEvent.click(within(group('Portal forms')).getByRole('switch', { name: /set on this tag/i }));
    const row = screen.getByTestId('setting-row-purchase_request');
    fireEvent.click(within(row).getByRole('radio', { name: 'Off' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateContactTag).toHaveBeenCalled());
    expect(updateContactTag.mock.calls[0][0]).toBe('t1');
    expect(updateContactTag.mock.calls[0][1]).toEqual({
      settings: { portal_forms: { purchase_request: false } },
    });
  });

  it('choosing Not set on a bool removes that key', async () => {
    render({ portal_forms: { purchase_request: false, complaint: true } }, true);
    const row = screen.getByTestId('setting-row-purchase_request');
    fireEvent.click(within(row).getByRole('radio', { name: 'Not set' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateContactTag).toHaveBeenCalled());
    expect(updateContactTag.mock.calls[0][1]).toEqual({ settings: { portal_forms: { complaint: true } } });
  });

  it('a 403 names the section, keeps the editor open and keeps the edit', async () => {
    updateContactTag.mockRejectedValueOnce(new Error('Changing agents needs access agents edit.'));
    const { onDone } = render({}, true);
    fireEvent.click(within(group('Portal forms')).getByRole('switch', { name: /set on this tag/i }));
    fireEvent.click(within(screen.getByTestId('setting-row-purchase_request')).getByRole('radio', { name: 'Off' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/Changing agents needs access agents edit\./)).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Save' })).not.toBeDisabled();
    expect(
      within(screen.getByTestId('setting-row-purchase_request')).getByRole('radio', { name: 'Off' }),
    ).toHaveAttribute('aria-checked', 'true');
  });
});
