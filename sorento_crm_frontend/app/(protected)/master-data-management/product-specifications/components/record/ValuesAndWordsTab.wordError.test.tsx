/**
 * AC-2.4 on the Choices and words tab: a refused word shows under the row it was added to.
 */
import React, { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn(), message: vi.fn(), dismiss: vi.fn() },
}));
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));
vi.mock('../../services/productSpecService', () => ({
  getSpecKeyProducts: vi.fn().mockResolvedValue({
    spec_key: 'finish',
    label: 'Finish or colour',
    total: 0,
    by_value: [],
    by_class: [],
    by_source: [],
    products: [],
  }),
}));

import { ValuesAndWordsTab } from './ValuesAndWordsTab';
import { projectSpecKeyDraft, type SpecKeyDraft, type WordError } from '../../hooks/useSpecKeyRecord';
import type { SpecRegistryKey } from '../../types/productSpec.types';

const MESSAGE = '"silver" is already a word for Metal.';

function finish(): SpecRegistryKey {
  return {
    spec_key: 'finish',
    label: 'Finish or colour',
    data_type: 'enum',
    unit: null,
    allowed_values: ['chrome', 'rose_gold'],
    synonyms: { chrome: ['chrome'], rose_gold: ['rose gold'] },
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
  } as unknown as SpecRegistryKey;
}

function Edit({ wordError, newValue }: { wordError: WordError | null; newValue?: string }) {
  const row = finish();
  const [draft, setDraft] = useState<SpecKeyDraft>(() => {
    const d = projectSpecKeyDraft(row);
    return newValue
      ? { ...d, liveValues: [...d.liveValues, newValue], words: { ...d.words, [newValue]: [newValue] } }
      : d;
  });
  return (
    <ValuesAndWordsTab
      row={row}
      mode="edit"
      draft={draft}
      setDraft={(u) => setDraft((d) => u(d))}
      onEnterEdit={() => {}}
      wordError={wordError}
    />
  );
}

function withClient(children: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('ValuesAndWordsTab - inline word refusal (AC-2.4)', () => {
  it('shows the alert on the choice whose words were refused', () => {
    render(withClient(<Edit wordError={{ word: 'silver', value: 'chrome', message: MESSAGE }} />));

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(MESSAGE);
    const row = alert.closest('tr') as HTMLElement;
    expect(within(row).getByText('Chrome')).toBeInTheDocument();
    expect(screen.getAllByRole('alert')).toHaveLength(1);
  });

  it('shows the alert on a newly added value that was refused (value null)', () => {
    render(
      withClient(
        <Edit newValue="silver" wordError={{ word: 'silver', value: null, message: MESSAGE }} />,
      ),
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(MESSAGE);
    expect(within(alert.closest('tr') as HTMLElement).getByText('Silver')).toBeInTheDocument();
  });

  it('shows nothing in view mode', () => {
    render(
      withClient(
        <ValuesAndWordsTab
          row={finish()}
          mode="view"
          draft={null}
          setDraft={() => {}}
          onEnterEdit={() => {}}
          wordError={{ word: 'silver', value: 'chrome', message: MESSAGE }}
        />,
      ),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
