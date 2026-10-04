/**
 * TranslationsList - loading / empty / error / data + inline edit + deferred delete
 * (AC-G4, purchasing consolidation batch, lane C). No create route: a row is written
 * by the upload preview or the AI fill, never by hand here - this list only reads,
 * corrects (inline) or removes one, so there is no Add button to prove.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

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
  });
}

vi.mock('next/navigation', () => ({
  usePathname: () => '/system-management/translations',
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

// DataGrid persists column prefs through this hook (it fires network) - stub it,
// or no rows render in jsdom.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

/* The grace window is the server's; what this file proves is that the row parks one. */
const createPendingAction = vi.fn().mockResolvedValue({
  id: 'pa-1',
  action_key: 'translation_memory.delete',
  entity_type: 'translation_memory',
  entity_id: 't1',
  commit_at: '2026-09-06T10:00:10',
  window_seconds: 10,
});
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

const hooks = vi.hoisted(() => ({
  useTranslations: vi.fn(),
  useUpdateTranslation: vi.fn(),
}));
vi.mock('../hooks/useTranslations', () => hooks);

import TranslationsList from './TranslationsList';
import type { Translation } from '../types/translation.types';

function translation(over: Partial<Translation> = {}): Translation {
  return {
    id: 't1',
    source_text: '座厕 S-250出水 对冲',
    source_lang: 'zh',
    target_lang: 'en',
    target_text: 'Toilet bowl S-250',
    source: 'ai',
    created_by_name: null,
    updated_at: '2026-09-06T02:00:00',
    hit_count: 3,
    ...over,
  };
}

function mockList(
  rows: Translation[],
  over: Partial<{ isLoading: boolean; isError: boolean; error: Error }> = {},
) {
  hooks.useTranslations.mockReturnValue({
    data: over.isLoading || over.isError
      ? undefined
      : { data: rows, pagination: { total: rows.length, page: 1, limit: 25 }, empty: !rows.length },
    isLoading: !!over.isLoading,
    isError: !!over.isError,
    error: over.error ?? null,
  });
}

let updateMutate: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  updateMutate = vi.fn();
  hooks.useUpdateTranslation.mockReturnValue({ mutate: updateMutate, isPending: false });
  mockList([translation()]);
});

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TranslationsList />
    </QueryClientProvider>,
  );
}

describe('TranslationsList', () => {
  it('renders the source, the translation and the source kind badge', async () => {
    renderList();

    expect(await screen.findByText('座厕 S-250出水 对冲')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Toilet bowl S-250')).toBeInTheDocument();
    expect(screen.getByText('ai')).toBeInTheDocument();
  });

  it('names each row\'s language pair, so a Malay and a Chinese row for one source differ', async () => {
    mockList([
      translation({ id: 't1' }),
      translation({ id: 't2', source_text: 'Product Code', source_lang: 'en', target_lang: 'ms', target_text: 'Kod Produk' }),
      translation({ id: 't3', source_text: 'Product Code', source_lang: 'en', target_lang: 'zh', target_text: '产品代码' }),
    ]);
    renderList();

    expect(await screen.findByText('Chinese to English')).toBeInTheDocument();
    expect(screen.getByText('English to Malay')).toBeInTheDocument();
    expect(screen.getByText('English to Chinese')).toBeInTheDocument();
    expect(screen.getByLabelText('Translation for 座厕 S-250出水 对冲')).toBeInTheDocument();
  });

  it('starts unfiltered and passes no language to the list query', async () => {
    renderList();
    await screen.findByText('座厕 S-250出水 对冲');
    expect(screen.getByTestId('translations-language-filter')).toBeInTheDocument();
    expect(hooks.useTranslations).toHaveBeenLastCalledWith(expect.objectContaining({ targetLang: '' }));
  });

  it('renders who wrote a manual correction, and a dash for an AI-only row', async () => {
    mockList([
      translation({ id: 't1', source: 'ai', created_by_name: null }),
      translation({ id: 't2', source: 'manual', created_by_name: 'Ada Actor', source_text: '纸箱' }),
    ]);
    renderList();

    await screen.findByText('纸箱');
    expect(screen.getByText('Ada Actor')).toBeInTheDocument();
    expect(screen.getByText('-')).toBeInTheDocument();
  });

  it('renders a loading state', () => {
    mockList([], { isLoading: true });
    renderList();

    expect(screen.queryByText('座厕 S-250出水 对冲')).not.toBeInTheDocument();
  });

  it('renders an empty state with a next step, not an explanation', async () => {
    mockList([]);
    renderList();

    expect(
      await screen.findByText('No translations yet. Upload a supplier document to add one.'),
    ).toBeInTheDocument();
  });

  it('renders the error instead of an empty grid', async () => {
    mockList([], { isError: true, error: new Error('Failed to load translations') });
    renderList();

    expect(await screen.findByTestId('translations-error')).toHaveTextContent(
      'Failed to load translations',
    );
  });

  it('saves an edited translation cell on blur', async () => {
    renderList();

    const input = await screen.findByLabelText('Translation for 座厕 S-250出水 对冲');
    fireEvent.change(input, { target: { value: 'Toilet bowl S-250, back outlet' } });
    fireEvent.blur(input);

    await waitFor(() =>
      expect(updateMutate).toHaveBeenCalledWith({
        id: 't1',
        body: { target_text: 'Toilet bowl S-250, back outlet' },
      }),
    );
  });

  it('does not save when the cell is blurred unchanged', async () => {
    renderList();

    const input = await screen.findByLabelText('Translation for 座厕 S-250出水 对冲');
    fireEvent.blur(input);

    expect(updateMutate).not.toHaveBeenCalled();
  });

  it('parks the delete on the row that was pressed, with no dialog in the way (S6-10)', async () => {
    renderList();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete translation of 座厕 S-250出水 对冲' }),
    );

    // D7: the press IS the action, and Cancel in the countdown is the way back.
    // The browser never calls the DELETE - the server does, when the window lapses.
    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'translation_memory.delete',
          entityType: 'translation_memory',
          entityId: 't1',
        }),
      ),
    );
    expect(screen.queryByText('Confirm delete')).not.toBeInTheDocument();
  });
});
