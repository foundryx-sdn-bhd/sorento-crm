import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import * as service from './chatbotStatusWordService';
import {
  createChatbotStatusWord,
  listChatbotStatusWords,
  updateChatbotStatusWord,
} from './chatbotStatusWordService';

function ok(body: unknown, status = 200) {
  return { ok: true, status, headers: { get: () => 'application/json' }, json: async () => body } as unknown as Response;
}
function fail(detail: string, status = 409) {
  return {
    ok: false,
    status,
    headers: { get: () => 'application/json' },
    json: async () => ({ detail }),
    text: async () => JSON.stringify({ detail }),
  } as unknown as Response;
}

const INPUT = { domain: 'sales', value: 'sales_report', label: 'sales figures', trigger_words: ['sales'], sort_order: 5, prompt_lists: ['status_field_values'] };
const ROW = { ...INPUT, id: 's-1', updated_at: '2026-09-30T09:00:00' };

beforeEach(() => apiFetch.mockReset());

describe('chatbotStatusWordService', () => {
  it('lists the whole table in one page, by sort order', async () => {
    apiFetch.mockResolvedValue(ok({ data: [ROW], pagination: { total: 1, page: 1, limit: 200 } }));
    await expect(listChatbotStatusWords()).resolves.toEqual([ROW]);
    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/system/chatbot/status-words?limit=200&sort=sort_order&dir=asc');
  });

  it('creates with POST and surfaces the server error', async () => {
    apiFetch.mockResolvedValueOnce(ok(ROW, 201));
    await expect(createChatbotStatusWord(INPUT)).resolves.toEqual(ROW);
    expect(apiFetch.mock.calls[0][1].method).toBe('POST');
    apiFetch.mockResolvedValueOnce(fail("A status word with value 'sales_report' already exists."));
    await expect(createChatbotStatusWord(INPUT)).rejects.toThrow('already exists');
  });

  it('updates with PUT on the row id', async () => {
    apiFetch.mockResolvedValue(ok(ROW));
    await updateChatbotStatusWord('s-1', INPUT);
    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/system/chatbot/status-words/s-1');
    expect(apiFetch.mock.calls[0][1].method).toBe('PUT');
  });

  it('has no client-side delete: the delete is a server-deferred pending action', () => {
    expect(Object.keys(service).some((k) => /delete/i.test(k))).toBe(false);
  });
});
