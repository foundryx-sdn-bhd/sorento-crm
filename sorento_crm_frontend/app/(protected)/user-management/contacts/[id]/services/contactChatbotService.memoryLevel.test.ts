/**
 * AC-MEM055/AC-MEM057 (round 3 UAC, merged 5b110df8) - the contact chatbot PUT body
 * renames its memory-level field from `chatbot_memory_level` to `memory_level`, and
 * the level's `'past'` value is renamed `'episodes'` everywhere (AC-MEM051). This
 * file pins the OUTGOING HTTP body, which `ContactChatbotSection.test.tsx` cannot
 * see - it mocks the `useSaveContactChatbotProfile` hook, never this service.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { saveContactChatbotProfile, type ChatbotMemoryLevel } from './contactChatbotService';

function ok(body: unknown) {
  return {
    ok: true,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

const BASE_INPUT = {
  chatbot_memory_level: 'full' as ChatbotMemoryLevel,
  tier: null,
  default_ledgers: [],
  stock_allowed: true,
  notify_salesman: false,
  packing_list_allowed: false,
  eta_offset_applied: true,
  escalation_allowed: true,
};

beforeEach(() => {
  apiFetch.mockReset();
});

describe('saveContactChatbotProfile - PUT body (round 3 rename)', () => {
  it('sends `memory_level`, not `chatbot_memory_level`, in the PUT body', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'c1', chatbot_memory_level: 'full' }));

    await saveContactChatbotProfile('c1', BASE_INPUT);

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [, init] = apiFetch.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toHaveProperty('memory_level', 'full');
    expect(body).not.toHaveProperty('chatbot_memory_level');
  });

  it('accepts the level value `episodes` (the round 3 name for the old `past`)', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'c1', chatbot_memory_level: 'episodes' }));

    await saveContactChatbotProfile('c1', { ...BASE_INPUT, chatbot_memory_level: 'episodes' });

    const [, init] = apiFetch.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.memory_level).toBe('episodes');
  });
});
