/**
 * SPEC-REGISTRY-UI F3 + F4 on the Details tab (red tests, written before the code).
 *
 * F3: Unit is always text (fixed once saved), and a new field "What this means to
 * the chatbot" holds the parser hint (view: the hint or "-"; edit: a Textarea with
 * maxLength 200 and an "n / 200" counter).
 * F4: a word that is also on another specification shows "Also on: <labels>".
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

vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

const HINT_LABEL = 'What this means to the chatbot';

function renderWithQuery(children: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>);
}

function baseRow(overrides: Partial<SpecRegistryKey> = {}): SpecRegistryKey {
  return {
    spec_key: 'trap_length',
    label: 'Trap outlet length',
    data_type: 'numeric',
    unit: 'mm',
    allowed_values: [],
    synonyms: { _self: ['length', 'mm'] },
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

let latest: SpecKeyDraft | null = null;
function EditHarness({ row }: { row: SpecRegistryKey }) {
  const [draft, setDraftState] = useState<SpecKeyDraft>(() => projectSpecKeyDraft(row));
  latest = draft;
  return (
    <HeaderTab row={row} mode="edit" draft={draft} setDraft={(u) => setDraftState((d) => u(d))} />
  );
}

/** The value node sitting under a Field's label span. */
function fieldBody(label: string): HTMLElement {
  const labelNode = screen.getByText(label);
  return labelNode.parentElement as HTMLElement;
}

describe('F3 - Unit is fixed once saved', () => {
  it('edit mode shows the unit as text with "Fixed once saved" and no Unit input', () => {
    renderWithQuery(<EditHarness row={baseRow({ unit: 'mm' })} />);

    expect(screen.queryByLabelText('Unit')).toBeNull();
    expect(screen.queryByPlaceholderText('e.g. mm')).toBeNull();
    const unit = fieldBody('Unit');
    expect(unit).toHaveTextContent('mm');
    expect(unit).toHaveTextContent('Fixed once saved');
  });

  it('view mode also carries "Fixed once saved" next to the unit', () => {
    renderWithQuery(<HeaderTab row={baseRow({ unit: 'mm' })} mode="view" draft={null} setDraft={() => {}} />);

    expect(fieldBody('Unit')).toHaveTextContent('mm');
    expect(screen.getByText('Fixed once saved')).toBeInTheDocument();
  });
});

describe('F3 - What this means to the chatbot', () => {
  it('view mode shows the hint text', () => {
    renderWithQuery(
      <HeaderTab
        row={baseRow({ parser_hint: 'Length of the trap outlet pipe' })}
        mode="view"
        draft={null}
        setDraft={() => {}}
      />,
    );

    expect(fieldBody(HINT_LABEL)).toHaveTextContent('Length of the trap outlet pipe');
  });

  it('view mode shows "-" when there is no hint', () => {
    renderWithQuery(
      <HeaderTab row={baseRow({ parser_hint: null })} mode="view" draft={null} setDraft={() => {}} />,
    );

    expect(fieldBody(HINT_LABEL).textContent).toBe(`${HINT_LABEL}-`);
  });

  it('sits after Highest believable value and before Other names (numeric)', () => {
    renderWithQuery(
      <HeaderTab row={baseRow()} mode="view" draft={null} setDraft={() => {}} />,
    );

    const text = document.body.textContent ?? '';
    const highest = text.indexOf('Highest believable value');
    const hint = text.indexOf(HINT_LABEL);
    const other = text.indexOf('Other names for this specification');
    expect(highest).toBeGreaterThanOrEqual(0);
    expect(hint).toBeGreaterThan(highest);
    expect(other).toBeGreaterThan(hint);
  });

  it('sits after In use and before Other names on a List specification', () => {
    renderWithQuery(
      <HeaderTab
        row={baseRow({ data_type: 'enum', unit: null })}
        mode="view"
        draft={null}
        setDraft={() => {}}
      />,
    );

    const text = document.body.textContent ?? '';
    const inUse = text.indexOf('In use');
    const hint = text.indexOf(HINT_LABEL);
    const other = text.indexOf('Other names for this specification');
    expect(hint).toBeGreaterThan(inUse);
    expect(other).toBeGreaterThan(hint);
  });

  it('edit mode renders a Textarea with maxLength 200 and the seeded hint', () => {
    renderWithQuery(<EditHarness row={baseRow({ parser_hint: 'Length of the pipe' })} />);

    const box = screen.getByLabelText(HINT_LABEL) as HTMLTextAreaElement;
    expect(box.tagName).toBe('TEXTAREA');
    expect(box.maxLength).toBe(200);
    expect(box.value).toBe('Length of the pipe');
  });

  it('edit mode shows the counter as "n / 200"', () => {
    renderWithQuery(<EditHarness row={baseRow({ parser_hint: 'Length of the pipe' })} />);
    expect(screen.getByText('18 / 200')).toBeInTheDocument();
  });

  it('an empty hint counts "0 / 200"', () => {
    renderWithQuery(<EditHarness row={baseRow({ parser_hint: null })} />);
    expect(screen.getByText('0 / 200')).toBeInTheDocument();
  });

  it('typing updates draft.parserHint and the counter', () => {
    renderWithQuery(<EditHarness row={baseRow({ parser_hint: null })} />);

    fireEvent.change(screen.getByLabelText(HINT_LABEL), { target: { value: 'Pipe length' } });

    expect(latest!.parserHint).toBe('Pipe length');
    expect(screen.getByText('11 / 200')).toBeInTheDocument();
  });
});

describe('F4 - Also on', () => {
  it('shows "Also on: <labels joined by comma>" for a word on other specifications', () => {
    renderWithQuery(
      <HeaderTab
        row={baseRow({ also_on: { length: ['Product class', 'Type'] } })}
        mode="view"
        draft={null}
        setDraft={() => {}}
      />,
    );

    expect(screen.getByText('Also on: Product class, Type')).toBeInTheDocument();
  });

  it('only the word with an also_on entry gets a line', () => {
    renderWithQuery(
      <HeaderTab
        row={baseRow({ also_on: { length: ['Type'], mm: [] } })}
        mode="view"
        draft={null}
        setDraft={() => {}}
      />,
    );

    expect(screen.getAllByText(/^Also on:/)).toHaveLength(1);
    expect(screen.getByText('Also on: Type')).toBeInTheDocument();
  });

  it('shows no Also on line when also_on is absent', () => {
    renderWithQuery(<HeaderTab row={baseRow()} mode="view" draft={null} setDraft={() => {}} />);
    expect(screen.queryByText(/^Also on:/)).toBeNull();
  });
});

describe('AC-2.4 - a refused word shows under its row', () => {
  const error = { word: 'length', value: '_self', message: '"length" is already a word for Length.' };

  it('edit mode renders the server message as an alert under the matching word', () => {
    function Harness() {
      const row = baseRow();
      const [draft, setDraft] = useState<SpecKeyDraft>(() => projectSpecKeyDraft(row));
      return (
        <HeaderTab
          row={row}
          mode="edit"
          draft={draft}
          setDraft={(u) => setDraft((d) => u(d))}
          wordError={error}
        />
      );
    }
    renderWithQuery(<Harness />);

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('"length" is already a word for Length.');
    expect(alert.className).toContain('text-destructive');
  });

  it('view mode and a different value show no alert', () => {
    renderWithQuery(
      <HeaderTab
        row={baseRow()}
        mode="view"
        draft={null}
        setDraft={() => {}}
        wordError={error}
      />,
    );
    expect(screen.queryByRole('alert')).toBeNull();

    renderWithQuery(
      <HeaderTab
        row={baseRow()}
        mode="view"
        draft={null}
        setDraft={() => {}}
        wordError={{ ...error, value: 'chrome' }}
      />,
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
