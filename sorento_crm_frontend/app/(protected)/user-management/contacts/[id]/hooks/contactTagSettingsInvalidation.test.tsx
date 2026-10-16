/**
 * CONTACT-TAGS browser-pass D2: saving a contact access section that can change the tag status
 * (portal forms, chatbot switches, escalation, AI allowed) invalidates the contact's tag-settings
 * query (['contact-tag-settings', contactId]) so the Tags section refetches.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../services/contactPortalFormsService', () => ({
  getContactPortalForms: vi.fn().mockResolvedValue([]),
  updateContactPortalForm: vi.fn().mockResolvedValue({}),
}));
vi.mock('../services/contactChatbotService', () => ({
  saveContactChatbotProfile: vi.fn().mockResolvedValue({}),
  saveContactEscalation: vi.fn().mockResolvedValue({}),
  saveContactChatbotAllowed: vi.fn().mockResolvedValue({}),
  getContactChatbotProfile: vi.fn().mockResolvedValue({}),
  getContactChatbotMemory: vi.fn().mockResolvedValue({}),
  saveContactFact: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { useUpdateContactPortalForm } from './useContactPortalForms';
import {
  useSaveContactChatbotProfile,
  useSaveContactEscalation,
  useSaveContactChatbotAllowed,
} from './useContactChatbot';

let client: QueryClient;
const wrapper = ({ children }: { children: React.ReactNode }) =>
  React.createElement(QueryClientProvider, { client }, children);

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});

async function expectInvalidated(run: () => { mutate: (v: never) => void; isSuccess: boolean }, input: unknown) {
  const spy = vi.spyOn(client, 'invalidateQueries');
  const { result } = renderHook(run, { wrapper });
  act(() => result.current.mutate(input as never));
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey?: unknown })?.queryKey));
  expect(keys).toContain(JSON.stringify(['contact-tag-settings', 'c1']));
}

describe('tag-settings invalidation after a contact access save', () => {
  it('portal forms', () =>
    expectInvalidated(() => useUpdateContactPortalForm('c1') as never, { formType: 'purchase_request', isEnabled: true }));
  it('chatbot profile switches', () =>
    expectInvalidated(() => useSaveContactChatbotProfile('c1') as never, { stock_allowed: false }));
  it('escalation', () => expectInvalidated(() => useSaveContactEscalation('c1') as never, false));
  it('AI replies allowed', () => expectInvalidated(() => useSaveContactChatbotAllowed('c1') as never, false));
});
