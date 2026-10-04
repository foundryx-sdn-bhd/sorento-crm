/**
 * N9 (reviewer finding, PR #1304): quick successive profile saves can revert each
 * other - `useSaveContactChatbotProfile`'s `onSuccess` only invalidated the query, so
 * the cache stayed stale (or a slower, earlier refetch could land after a later one
 * and overwrite it) until a background refetch resolved. The mutation already HAS the
 * server's fresh response - `queryClient.setQueryData` with it, before invalidating,
 * makes the save's own result visible immediately, with no window for a race.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const saveContactChatbotProfile = vi.fn();
const getContactChatbotProfile = vi.fn();

vi.mock('../services/contactChatbotService', () => ({
  saveContactChatbotProfile: (...a: unknown[]) => saveContactChatbotProfile(...a),
  getContactChatbotProfile: (...a: unknown[]) => getContactChatbotProfile(...a),
  getContactChatbotMemory: vi.fn(),
  saveContactFact: vi.fn(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  contactChatbotQueryKey,
  useSaveContactChatbotProfile,
} from './useContactChatbot';

let client: QueryClient;
function wrapper({ children }: { children: React.ReactNode }) {
  return React.createElement(QueryClientProvider, { client }, children);
}

const SAVED_PROFILE = {
  chatbot_memory_level: 'full' as const,
  tier: 'dealer',
  default_ledgers: [],
  stock_allowed: true,
  notify_salesman: true,
  packing_list_allowed: false,
  eta_offset_applied: true,
  escalation_allowed: true,
};

beforeEach(() => {
  saveContactChatbotProfile.mockReset();
  getContactChatbotProfile.mockReset();
  // Never resolves - isolates the assertion from the background refetch
  // `invalidateQueries` also kicks off, so only `setQueryData` can make the fresh
  // value show up before this test's own `waitFor` gives up.
  getContactChatbotProfile.mockReturnValue(new Promise(() => {}));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

describe('useSaveContactChatbotProfile - the save result is visible immediately (N9)', () => {
  it('sets the query cache to the mutation response as soon as it succeeds, before any refetch resolves', async () => {
    saveContactChatbotProfile.mockResolvedValue(SAVED_PROFILE);
    const { result } = renderHook(() => useSaveContactChatbotProfile('c1'), { wrapper });

    act(() => {
      result.current.mutate(SAVED_PROFILE);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(client.getQueryData(contactChatbotQueryKey('c1'))).toEqual(SAVED_PROFILE);
  });
});
