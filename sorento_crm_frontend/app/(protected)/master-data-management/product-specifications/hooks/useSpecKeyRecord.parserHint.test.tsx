/**
 * SPEC-REGISTRY-UI F2 (red tests): the one PATCH carries the parser hint, no
 * longer carries the unit, and a word-on-another-key 409 reaches the error toast
 * with the draft kept open. The real service runs over a mocked apiFetch so the
 * 409 body travels through extractApiError.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

import { apiFetch } from '@/lib/api';
import { toast } from '@/lib/toast';
import { useSpecKeyRecord } from './useSpecKeyRecord';
import type { SpecRegistryKey } from '../types/productSpec.types';

const mockedFetch = vi.mocked(apiFetch);

function row(overrides: Partial<SpecRegistryKey> = {}): SpecRegistryKey {
  return {
    spec_key: 'trap_length',
    label: 'Trap outlet length',
    data_type: 'numeric',
    unit: 'mm',
    allowed_values: [],
    synonyms: { _self: ['length'] },
    excluded_values: [],
    user_values: [],
    suppressed_values: [],
    value_weights: {},
    derivation_rules: [],
    effective_rules: [],
    applies_when: {},
    read_from: 'rules',
    rank_weight: 1,
    measured_coverage: null,
    source: 'seed',
    user_synonyms: {},
    suppressed_synonyms: {},
    match_tolerance: 0,
    match_decay: 0,
    is_active: true,
    parser_hint: null,
    label_owned: true,
    ...overrides,
  } as SpecRegistryKey;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let client: QueryClient;
function wrapper({ children }: { children: React.ReactNode }) {
  return React.createElement(QueryClientProvider, { client }, children);
}

function sentBody(): Record<string, unknown> {
  const call = mockedFetch.mock.calls[0];
  return JSON.parse((call[1] as RequestInit).body as string);
}

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

describe('useSpecKeyRecord - parser hint (F2)', () => {
  it('edit() seeds draft.parserHint from row.parser_hint, "" when null', () => {
    const { result, rerender } = renderHook(({ r }) => useSpecKeyRecord(r), {
      wrapper,
      initialProps: { r: row({ parser_hint: 'Pipe length' }) },
    });
    act(() => result.current.edit());
    expect(result.current.draft?.parserHint).toBe('Pipe length');

    act(() => result.current.cancel());
    rerender({ r: row({ parser_hint: null }) });
    act(() => result.current.edit());
    expect(result.current.draft?.parserHint).toBe('');
  });

  it('the PATCH carries parser_hint trimmed', async () => {
    const r = row();
    mockedFetch.mockResolvedValue(jsonResponse({ ...r, products_updated: 0 }));
    const { result } = renderHook(() => useSpecKeyRecord(r), { wrapper });

    act(() => result.current.edit());
    act(() => {
      result.current.setDraft((d) => ({ ...d, parserHint: '  Pipe length  ' }));
    });
    await act(async () => {
      await result.current.save();
    });

    expect(sentBody().parser_hint).toBe('Pipe length');
  });

  it('a blank hint goes out as null', async () => {
    const r = row({ parser_hint: 'old hint' });
    mockedFetch.mockResolvedValue(jsonResponse({ ...r, parser_hint: null, products_updated: 0 }));
    const { result } = renderHook(() => useSpecKeyRecord(r), { wrapper });

    act(() => result.current.edit());
    act(() => {
      result.current.setDraft((d) => ({ ...d, parserHint: '   ' }));
    });
    await act(async () => {
      await result.current.save();
    });

    const body = sentBody();
    expect('parser_hint' in body).toBe(true);
    expect(body.parser_hint).toBeNull();
  });

  it('the PATCH no longer carries a unit key', async () => {
    const r = row({ unit: 'mm' });
    mockedFetch.mockResolvedValue(jsonResponse({ ...r, products_updated: 0 }));
    const { result } = renderHook(() => useSpecKeyRecord(r), { wrapper });

    act(() => result.current.edit());
    await act(async () => {
      await result.current.save();
    });

    expect('unit' in sentBody()).toBe(false);
  });

  it('a 409 spec_registry_word_on_other_key toasts its message and stays in edit', async () => {
    const MESSAGE = '"length" already belongs to Type. Remove it there first.';
    mockedFetch.mockResolvedValue(
      jsonResponse({ detail: MESSAGE, code: 'spec_registry_word_on_other_key' }, 409),
    );
    const r = row();
    const { result } = renderHook(() => useSpecKeyRecord(r), { wrapper });

    act(() => result.current.edit());
    act(() => {
      result.current.setDraft((d) => ({ ...d, parserHint: 'kept text' }));
    });
    await act(async () => {
      const ok = await result.current.save();
      expect(ok).toBe(false);
    });

    expect(toast.error).toHaveBeenCalledWith(MESSAGE, expect.anything());
    expect(result.current.mode).toBe('edit');
    expect(result.current.draft?.parserHint).toBe('kept text');
  });

  describe('wordError (AC-2.4)', () => {
    const MESSAGE = '"length" is already a word for Length.';
    const refusal = () =>
      jsonResponse(
        {
          message: MESSAGE,
          detail: null,
          code: 'spec_registry_word_on_other_key',
          details: { word: 'length', other_label: 'Length', value: '_self' },
        },
        409,
      );

    async function refused() {
      mockedFetch.mockResolvedValue(refusal());
      const r = row();
      const hook = renderHook(() => useSpecKeyRecord(r), { wrapper });
      act(() => hook.result.current.edit());
      await act(async () => {
        await hook.result.current.save();
      });
      return hook;
    }

    it('is set from the 409 body, and the toast still fires', async () => {
      const { result } = await refused();

      expect(result.current.wordError).toEqual({
        word: 'length',
        value: '_self',
        message: MESSAGE,
      });
      expect(toast.error).toHaveBeenCalledWith(MESSAGE, expect.anything());
    });

    it('is null for a 409 that carries no details', async () => {
      mockedFetch.mockResolvedValue(
        jsonResponse({ detail: 'x', code: 'spec_registry_word_on_other_key' }, 409),
      );
      const r = row();
      const { result } = renderHook(() => useSpecKeyRecord(r), { wrapper });
      act(() => result.current.edit());
      await act(async () => {
        await result.current.save();
      });
      expect(result.current.wordError).toBeNull();
    });

    it('clears when that value\'s words change', async () => {
      const { result } = await refused();

      act(() => {
        result.current.setDraft((d) => ({ ...d, parserHint: 'unrelated' }));
      });
      expect(result.current.wordError).not.toBeNull();

      act(() => {
        result.current.setDraft((d) => ({ ...d, words: { ...d.words, _self: ['other'] } }));
      });
      expect(result.current.wordError).toBeNull();
    });

    it('clears on cancel', async () => {
      const { result } = await refused();
      act(() => result.current.cancel());
      expect(result.current.wordError).toBeNull();
    });

    it('clears on a successful save', async () => {
      const { result } = await refused();
      mockedFetch.mockResolvedValue(jsonResponse({ ...row(), products_updated: 0 }));
      await act(async () => {
        await result.current.save();
      });
      expect(result.current.wordError).toBeNull();
    });
  });
});
