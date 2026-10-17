/**
 * AC-S1.12 - Details: Name, Unit (not on List specs), In use, Highest believable
 * value, and Other names for this specification as a data grid. Same field labels
 * in both modes; edit swaps each for its input in place. No code name, no "Built
 * in / Added here", no rule count, no Advanced anywhere on this tab.
 */
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HeaderTab } from './HeaderTab';
import { projectSpecKeyDraft, type SpecKeyDraft } from '../../hooks/useSpecKeyRecord';
import type { SpecRegistryKey } from '../../types/productSpec.types';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn(), message: vi.fn(), dismiss: vi.fn() },
}));

// `WordsDataGrid` parks the real `spec_word.remove` deferred action (fix round
// 1) - nothing here presses Remove, so the service only needs to resolve to
// "nothing pending".
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

/** `WordsDataGrid`'s `useDeferredAction` is real, so it needs a live `QueryClient`. */
function renderWithQuery(children: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>);
}

function baseRow(overrides: Partial<SpecRegistryKey> = {}): SpecRegistryKey {
  return {
    spec_key: 'finish',
    label: 'Finish or colour',
    data_type: 'enum',
    unit: null,
    allowed_values: ['chrome'],
    synonyms: { chrome: ['chrome'] },
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
    ...overrides,
  } as SpecRegistryKey;
}

function EditHarness({
  row,
  onDraftChange,
}: {
  row: SpecRegistryKey;
  onDraftChange?: (draft: SpecKeyDraft) => void;
}) {
  const [draft, setDraftState] = useState<SpecKeyDraft>(() => projectSpecKeyDraft(row));
  return (
    <HeaderTab
      row={row}
      mode="edit"
      draft={draft}
      setDraft={(updater) =>
        setDraftState((current) => {
          const next = updater(current);
          onDraftChange?.(next);
          return next;
        })
      }
    />
  );
}

describe('HeaderTab - same field labels in both modes (AC-S1.12)', () => {
  it('renders Name, Unit and In use in view mode', () => {
    const row = baseRow({ is_active: false, unit: 'mm', data_type: 'numeric' });
    renderWithQuery(<HeaderTab row={row} mode="view" draft={null} setDraft={() => {}} />);

    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Unit')).toBeInTheDocument();
    expect(screen.getByText('In use')).toBeInTheDocument();
    expect(screen.getByText('Finish or colour')).toBeInTheDocument();
    expect(screen.getByText('mm')).toBeInTheDocument();
  });

  it('a List specification carries no Unit field (it has no unit of its own)', () => {
    const row = baseRow({ data_type: 'enum' });
    renderWithQuery(<HeaderTab row={row} mode="view" draft={null} setDraft={() => {}} />);
    expect(screen.queryByText('Unit')).not.toBeInTheDocument();
  });

  it('renders the same labels in edit mode, as inputs (Unit stays text)', () => {
    renderWithQuery(<EditHarness row={baseRow({ unit: 'mm', data_type: 'numeric' })} />);

    expect(screen.getByLabelText('Name')).toBeInTheDocument();
    expect(screen.getByText('Unit')).toBeInTheDocument();
    expect(screen.queryByLabelText('Unit')).toBeNull();
    expect(screen.getByRole('switch', { name: 'In use' })).toBeInTheDocument();
  });
});

describe('HeaderTab - Highest believable value (numeric specs only, AC-S1.12)', () => {
  it('shows the stored limit on a numeric spec, in view mode', () => {
    const row = baseRow({ spec_key: 'dim_height', data_type: 'numeric', unit: 'mm', max_value: 5000 });
    renderWithQuery(<HeaderTab row={row} mode="view" draft={null} setDraft={() => {}} />);

    expect(screen.getByText('Highest believable value')).toBeInTheDocument();
    expect(screen.getByText('5000 mm')).toBeInTheDocument();
  });

  it('shows No limit when unset', () => {
    const row = baseRow({ spec_key: 'dim_height', data_type: 'numeric', unit: 'mm', max_value: null });
    renderWithQuery(<HeaderTab row={row} mode="view" draft={null} setDraft={() => {}} />);

    expect(screen.getByText('No limit')).toBeInTheDocument();
  });

  it('is absent on a List specification in either mode', () => {
    const row = baseRow();
    renderWithQuery(<HeaderTab row={row} mode="view" draft={null} setDraft={() => {}} />);
    expect(screen.queryByText('Highest believable value')).not.toBeInTheDocument();
  });
});

describe('HeaderTab - Other names for this specification (D7, D13)', () => {
  it('lists the _self synonyms as a data grid, never a value row', () => {
    const row = baseRow({
      spec_key: 'capacity_oz',
      label: 'Capacity (oz)',
      data_type: 'numeric',
      unit: 'oz',
      synonyms: { _self: ['oz', 'ounce', 'ounces'] },
    });
    renderWithQuery(<HeaderTab row={row} mode="view" draft={null} setDraft={() => {}} />);

    expect(screen.getByText('Other names for this specification')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'oz' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ounce' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ounces' })).toBeInTheDocument();
    // `_self` is a bookkeeping key, never rendered as its own word or value.
    expect(screen.queryByText('_self')).not.toBeInTheDocument();
  });

  it('typing a new word adds it to the draft under _self', () => {
    let latest: SpecKeyDraft | undefined;
    const row = baseRow({
      spec_key: 'capacity_oz',
      data_type: 'numeric',
      synonyms: { _self: ['oz'] },
    });
    renderWithQuery(<EditHarness row={row} onDraftChange={(draft) => (latest = draft)} />);

    fireEvent.click(screen.getByText('+ Add a word'));
    fireEvent.change(screen.getByPlaceholderText('e.g. oz'), { target: { value: 'ounce' } });
    fireEvent.keyDown(screen.getByPlaceholderText('e.g. oz'), { key: 'Enter' });

    expect(latest?.words._self).toEqual(['oz', 'ounce']);
  });
});

describe('HeaderTab - no Advanced, nothing technical (D8, AC-S1.12)', () => {
  it('renders no code name, no Built in, no rule count', () => {
    const row = baseRow({ spec_key: 'capacity_oz' });
    renderWithQuery(<HeaderTab row={row} mode="view" draft={null} setDraft={() => {}} />);

    expect(screen.queryByText('capacity_oz')).not.toBeInTheDocument();
    expect(screen.queryByText(/built in/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/advanced/i)).not.toBeInTheDocument();
  });

  it('typing a label feeds the draft', () => {
    let latest: SpecKeyDraft | undefined;
    renderWithQuery(<EditHarness row={baseRow()} onDraftChange={(draft) => (latest = draft)} />);

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Finish colour' } });

    expect(latest?.label).toBe('Finish colour');
  });
});
