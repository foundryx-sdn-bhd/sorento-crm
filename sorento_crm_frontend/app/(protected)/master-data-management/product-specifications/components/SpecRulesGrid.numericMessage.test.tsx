/**
 * (N3, number-fields round 1b) a refused numeric value cell keeps its sentence until valid.
 * Review round 2 (PR #1302) on the rules grid:
 * - B-5: one Remove at a time. A second Remove while the first is counting down
 *   used to re-point the countdown at the second rule, so the server removed the
 *   first while the draft dropped the second.
 * - A rule not yet saved to the server is dropped locally: no countdown, no call.
 * - S-11: in-place edit refuses what the server would refuse, in its words.
 * - Ruling 13: a Number rule's What to find cell opens the rule modal.
 * - B-7: Try it's per-row read is a readable value, never a slug or `true`.
 * - S-13: under `sm` only Order, What to find and Value it sets stay; at 1280 the
 *   edit-mode columns fit, so the row menu is not scrolled off the right.
 */
import React from 'react';
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn(), message: vi.fn(), dismiss: vi.fn() },
}));

const createPendingAction = vi.fn();
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn().mockResolvedValue({}),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

type MenuSlotProps = { children?: React.ReactNode };
type MenuItemProps = MenuSlotProps & { onClick?: () => void; disabled?: boolean; variant?: string };
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: MenuSlotProps) => <>{children}</>,
  DropdownMenuContent: ({ children }: MenuSlotProps) => <div>{children}</div>,
  DropdownMenuItem: ({ children, onClick, disabled }: MenuItemProps) => (
    <button type="button" onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
}));

import { SpecRulesGrid } from './SpecRulesGrid';
import type { SpecDerivationRule, SpecRegistryKey } from '../types/productSpec.types';

function withClient(children: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const ROUND: SpecDerivationRule = {
  builder: { kind: 'words', look_in: 'name', words: ['ROUND'], value: 'round' },
  _uid: 'r0',
};
const SQUARE: SpecDerivationRule = {
  builder: { kind: 'words', look_in: 'name', words: ['SQUARE'], value: 'square' },
  _uid: 'r1',
};

function shape(saved: SpecDerivationRule[], overrides: Partial<SpecRegistryKey> = {}): SpecRegistryKey {
  const stored = saved.map(({ builder }) => ({ builder }));
  return {
    spec_key: 'shape',
    label: 'Shape',
    data_type: 'enum',
    unit: null,
    allowed_values: ['round', 'square', 'rose_gold'],
    excluded_values: [],
    user_values: [],
    suppressed_values: [],
    value_weights: {},
    derivation_rules: stored,
    effective_rules: stored,
    synonyms: {},
    value_labels: { rose_gold: 'Rose gold' },
    applies_when: {},
    read_from: 'rules',
    rank_weight: null,
    measured_coverage: 0,
    source: 'seed',
    user_synonyms: {},
    suppressed_synonyms: {},
    match_tolerance: 0,
    match_decay: 0,
    is_active: true,
    ...overrides,
  };
}

function renderGrid(props: Partial<React.ComponentProps<typeof SpecRulesGrid>> = {}) {
  const onChange = vi.fn();
  const onEdit = vi.fn();
  const rules = props.rules ?? [ROUND, SQUARE];
  const utils = render(
    withClient(
      <SpecRulesGrid
        rules={rules}
        spec={shape([ROUND, SQUARE])}
        registry={[]}
        mode="edit"
        onChange={onChange}
        onEdit={onEdit}
        onAdd={vi.fn()}
        {...props}
      />,
    ),
  );
  return { onChange, onEdit, ...utils };
}

const rowOf = (n: number) => screen.getByLabelText(`Rule ${n} actions`).closest('tr')!;

beforeEach(() => {
  createPendingAction.mockReset();
  createPendingAction.mockImplementation(async ({ entityId }: { entityId: string }) => ({
    id: 'pa-1',
    action_key: 'spec_rule.remove',
    entity_type: 'spec_rule',
    entity_id: entityId,
    commit_at: new Date(Date.now() + 5000).toISOString(),
    window_seconds: 5,
  }));
});

const NUM_RULE: SpecDerivationRule = {
  builder: { kind: 'words', look_in: 'name', words: ['ROUND'], value: 5 },
  _uid: 'n0',
} as SpecDerivationRule;

describe('N3 - numeric value cell message stays until the value is valid', () => {
  it('keeps the sentence through a still-invalid keystroke and clears it once valid', () => {
    const spec = shape([NUM_RULE], { data_type: 'numeric', allowed_values: [] });
    renderGrid({ rules: [NUM_RULE], spec });
    fireEvent.click(within(rowOf(1)).getByText(/^5/));
    const box = within(rowOf(1)).getByRole('spinbutton') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '' } });
    fireEvent.blur(box);
    expect(within(rowOf(1)).getByText(/^Enter a number/)).toBeInTheDocument();
    // Once shown the sentence follows the typing: a valid figure clears it, going blank
    // again (the browser reports "" for a bad entry too) brings it back.
    fireEvent.change(box, { target: { value: '3' } });
    expect(within(rowOf(1)).queryByText(/^Enter a number/)).toBeNull();
    fireEvent.change(box, { target: { value: '' } });
    expect(within(rowOf(1)).getByText(/^Enter a number/)).toBeInTheDocument();
    fireEvent.change(box, { target: { value: '7' } });
    expect(within(rowOf(1)).queryByText(/^Enter a number/)).toBeNull();
  });
});
