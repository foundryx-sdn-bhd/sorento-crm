/**
 * CONTACT-TAGS: every contact access save refreshes the Tags section. A save that can change
 * the tag status (edit dialog, market segments, agents table, stock / spec visibility, outbound
 * toggle) must leave ['contact-tag-settings', contactId] invalidated, so the Tags section refetches.
 * Companion to contactTagSettingsInvalidation.test.tsx (portal forms, chatbot, escalation, AI).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, render, screen, act, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const apiFetch = vi.fn();
const getAgentFieldAccess = vi.fn();
const setAgentFieldAccess = vi.fn();
vi.mock('../../../access-agents/services/accessAgentService', async (orig) => ({
  ...(await orig<object>()),
  getAgentFieldAccess: (...a: unknown[]) => getAgentFieldAccess(...a),
  setAgentFieldAccess: (...a: unknown[]) => setAgentFieldAccess(...a),
}));
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), custom: vi.fn() },
}));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { roles: ['user'] } }, status: 'authenticated' }),
}));

vi.mock('@/app/(protected)/user-management/market-segments/services/marketSegmentService', async (orig) => ({
  ...(await orig<object>()),
  setContactMarketSegments: vi.fn().mockResolvedValue([]),
}));
vi.mock('@/services/contactBrandScopeService', async (orig) => ({
  ...(await orig<object>()),
  saveContactBrandScope: vi.fn().mockResolvedValue({ brand_ids: [] }),
}));
vi.mock('@/services/stockVisibilityService', async (orig) => ({
  ...(await orig<object>()),
  saveStockVisibility: vi.fn().mockResolvedValue({}),
  deleteStockVisibility: vi.fn().mockResolvedValue({}),
}));
vi.mock('@/services/specVisibilityService', async (orig) => ({
  ...(await orig<object>()),
  saveSpecVisibility: vi.fn().mockResolvedValue({}),
  deleteSpecVisibility: vi.fn().mockResolvedValue({}),
}));
vi.mock('@/app/(protected)/system-management/respond-contacts/services/respondContactOutboundService', () => ({
  setContactOutbound: vi.fn().mockResolvedValue({ id: 'c1', outbound_enabled: false }),
  setBulkOutbound: vi.fn().mockResolvedValue({ changed: 1 }),
}));

// Table children: each stands in for the dialog that saves, and reports a finished save.
vi.mock('../../../access-agents/components/ContactAgentAccessDialog', () => ({
  default: ({ onOpenChange }: { onOpenChange: (o: boolean) => void }) =>
    React.createElement('button', { onClick: () => onOpenChange(false) }, 'agent-dialog-saved'),
}));
vi.mock('../../../access-agents/components/contact-agent-access-delete-dialog', () => ({
  default: ({ closeDialog }: { closeDialog: () => void }) =>
    React.createElement('button', { onClick: closeDialog }, 'agent-delete-done'),
}));
vi.mock('../components/CopyAccessAgentsFromContactDialog', () => ({
  default: ({ onSuccess }: { onSuccess: () => void }) =>
    React.createElement('button', { onClick: onSuccess }, 'agent-copy-done'),
}));
vi.mock('../../../access-agents/components/ContactFieldAccessDialog', () => ({ default: () => null }));

import { useSetContactMarketSegments } from '@/app/(protected)/user-management/market-segments/hooks/useMarketSegments';
import { useContactBrandScopeMutations } from '@/hooks/useContactBrandScope';
import { useStockVisibilityMutations } from '@/hooks/useStockVisibility';
import { useSpecVisibilityMutations } from '@/hooks/useSpecVisibility';
import { useRespondContactOutboundMutations } from '@/hooks/useRespondContactOutbound';
import ContactAccessAgentsTable from '../components/ContactAccessAgentsTable';
import ContactEditDialog from '../components/ContactEditDialog';

const TAG_KEY = ['contact-tag-settings', 'c1'];
let client: QueryClient;
const wrapper = ({ children }: { children: React.ReactNode }) =>
  React.createElement(QueryClientProvider, { client }, children);

function seedTagSettings() {
  client.setQueryData(TAG_KEY, { seeded: true });
  expect(client.getQueryState(TAG_KEY)?.isInvalidated).toBe(false);
}
const tagSettingsInvalidated = () => client.getQueryState(TAG_KEY)?.isInvalidated === true;

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  apiFetch.mockReset();
});

async function expectMutationInvalidates(
  run: () => { mutate: (v: never) => void; isSuccess: boolean },
  input: unknown,
) {
  seedTagSettings();
  const { result } = renderHook(run, { wrapper });
  act(() => result.current.mutate(input as never));
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  await waitFor(() => expect(tagSettingsInvalidated()).toBe(true));
}

describe('hook writers refresh the Tags section', () => {
  it('market segments save', () =>
    expectMutationInvalidates(() => useSetContactMarketSegments('c1') as never, ['retail']));
  it('stock visibility save', () =>
    expectMutationInvalidates(() => useStockVisibilityMutations({ kind: 'contact', contactId: 'c1' }).save as never, {}));
  it('stock visibility remove', () =>
    expectMutationInvalidates(() => useStockVisibilityMutations({ kind: 'contact', contactId: 'c1' }).remove as never, undefined));
  it('spec visibility save', () =>
    expectMutationInvalidates(() => useSpecVisibilityMutations({ kind: 'contact', contactId: 'c1' }).save as never, {}));
  it('spec visibility remove', () =>
    expectMutationInvalidates(() => useSpecVisibilityMutations({ kind: 'contact', contactId: 'c1' }).remove as never, undefined));
  it('brands save', () =>
    expectMutationInvalidates(() => useContactBrandScopeMutations('c1').save as never, ['b1']));
  it('outbound (messaging) toggle', () =>
    expectMutationInvalidates(() => useRespondContactOutboundMutations().setOne as never, { contactId: 'c1', enabled: false }));
});

describe('agents table writes refresh the Tags section', () => {
  const grant = {
    id: 'g1', agent_id: 'a1', agent_code: 'A1', agent_name: 'Agent', respond_contact_phone: '+60123456789',
    respond_contact_name: 'Al', is_allowed: true, created_at: new Date().toISOString(),
  };
  beforeEach(() => {
    apiFetch.mockImplementation(async (url: string) => {
      const body = url.includes('/access-agents?') && url.includes('/contacts/')
        ? { data: [grant], pagination: { total: 1 } }
        : url.includes('/contacts/c1')
          ? { id: 'c1', phone_number: '+60123456789', name: 'Al' }
          : { data: [] };
      return { ok: true, json: async () => body };
    });
  });

  async function mountTable() {
    seedTagSettings();
    render(React.createElement(QueryClientProvider, { client }, React.createElement(ContactAccessAgentsTable, { contactId: 'c1' })));
    await screen.findByText('A1');
  }

  it('agent grant add / edit save', async () => {
    await mountTable();
    fireEvent.click(await screen.findByText('Add Access Agent'));
    fireEvent.click(await screen.findByText('agent-dialog-saved'));
    await waitFor(() => expect(tagSettingsInvalidated()).toBe(true));
  });

  it('agent grant delete', async () => {
    await mountTable();
    const buttons = screen.getAllByRole('button');
    const trash = buttons.find((b) => b.querySelector('svg.lucide-trash-2, svg.lucide-trash2'));
    fireEvent.click(trash!);
    fireEvent.click(await screen.findByText('agent-delete-done'));
    await waitFor(() => expect(tagSettingsInvalidated()).toBe(true));
  });

  it('agent copy from another contact', async () => {
    await mountTable();
    fireEvent.click(screen.getByText('Copy from another user'));
    fireEvent.click(await screen.findByText('agent-copy-done'));
    await waitFor(() => expect(tagSettingsInvalidated()).toBe(true));
  });
});

describe('contact edit dialog save refreshes the Tags section', () => {
  it('tags, access types, companies and requires-registered-project save', async () => {
    apiFetch.mockImplementation(async (url: string, init?: { method?: string }) => ({
      ok: true,
      json: async () => (init?.method === 'PUT' ? { id: 'c1' } : []),
    }));
    seedTagSettings();
    const contact = { id: 'c1', phone_number: '+60123456789', name: 'Al', access_type_codes: [], tags: [] };
    render(
      React.createElement(
        QueryClientProvider,
        { client },
        React.createElement(ContactEditDialog, { open: true, closeDialog: vi.fn(), contact: contact as never }),
      ),
    );
    fireEvent.click(await screen.findByRole('button', { name: /update contact/i }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(expect.stringContaining('/contacts/c1'), expect.objectContaining({ method: 'PUT' })));
    await waitFor(() => expect(tagSettingsInvalidated()).toBe(true));
  });
});

describe('field access dialog save refreshes the Tags section', () => {
  it('agent field access saved for a contact', async () => {
    const FIELDS = [
      { resource: 'incoming_stock', field_key: 'eta_date', label: 'ETA', is_allowed: true, override: null, effective: true },
    ];
    getAgentFieldAccess.mockResolvedValue({ agent_code: 'x', fields: FIELDS, overrides: [] });
    setAgentFieldAccess.mockResolvedValue({ agent_code: 'x', fields: FIELDS, overrides: [] });
    const { default: RealDialog } = await vi.importActual<{ default: React.ComponentType<Record<string, unknown>> }>(
      '../../../access-agents/components/ContactFieldAccessDialog',
    );
    seedTagSettings();
    render(
      React.createElement(
        QueryClientProvider,
        { client },
        React.createElement(RealDialog, {
          open: true, onOpenChange: vi.fn(), agentId: 'a1', agentName: 'Agent', contactId: 'c1', contactName: 'Al',
        }),
      ),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'ETA: Follows agent (allowed)' }));
    fireEvent.click(await screen.findByRole('button', { name: /^Save \(1\)/ }));
    await waitFor(() => expect(setAgentFieldAccess).toHaveBeenCalled());
    await waitFor(() => expect(tagSettingsInvalidated()).toBe(true));
  });
});
