/**
 * PV2-FIX r7h: the per-agent card gains a clearable "Reasoning effort" select next to provider/model,
 * shown only for a key that honours it (`supports_reasoning_effort`), prefilled from the label, saved
 * through the set-model mutation. Red before the field exists.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const mutateAsync = vi.fn();
let keyRows: Array<Record<string, unknown>> = [];

vi.mock('../../hooks/useAIAssistantPrompts', () => ({
  usePromptKeys: () => ({ data: keyRows, isLoading: false }),
  useSetAgentModel: () => ({ mutateAsync, isPending: false }),
}));
vi.mock('@/hooks/useProviderModels', () => ({
  useProviderModels: () => ({ data: undefined, isLoading: false }),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// A native select stands in for the popover select; it exposes its option values and clearable flag.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (p: {
    value: string;
    onChange: (v: string) => void;
    options: Array<{ value: string; label: string }>;
    clearable?: boolean;
    disabled?: boolean;
  }) => (
    <select
      data-testid="select"
      data-options={p.options.map((o) => o.value).join(',')}
      data-clearable={String(!!p.clearable)}
      value={p.value ?? ''}
      disabled={p.disabled}
      onChange={(e) => p.onChange(e.target.value)}
    >
      <option value="" />
      {p.options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

import { AgentModelCard } from './AgentModelCard';

const ROW = {
  name: 'chatbot_semantic_parser_v2',
  provider: null,
  model: null,
  reasoning_effort: 'low',
  supports_reasoning_effort: true,
};

function effortSelect(): HTMLSelectElement | undefined {
  return (screen.queryAllByTestId('select') as HTMLSelectElement[]).find((s) =>
    (s.getAttribute('data-options') || '').split(',').includes('medium'),
  );
}

beforeEach(() => {
  mutateAsync.mockReset();
  mutateAsync.mockResolvedValue({});
  keyRows = [ROW];
});
afterEach(() => cleanup());

describe('AgentModelCard reasoning effort', () => {
  it('shows a clearable Reasoning effort select with None/Low/Medium/High for a key that supports it', () => {
    render(<AgentModelCard name="chatbot_semantic_parser_v2" canEdit />);
    expect(screen.getByText(/reasoning effort/i)).toBeInTheDocument();
    const select = effortSelect()!;
    expect(select).toBeDefined();
    expect(select.getAttribute('data-options')).toBe('none,low,medium,high');
    expect(select.getAttribute('data-clearable')).toBe('true');
    expect(Array.from(select.options).map((o) => o.textContent).filter(Boolean)).toEqual([
      'None',
      'Low',
      'Medium',
      'High',
    ]);
  });

  it('is prefilled from the label', () => {
    render(<AgentModelCard name="chatbot_semantic_parser_v2" canEdit />);
    expect(effortSelect()!.value).toBe('low');
  });

  it('choosing Medium and saving sends reasoning_effort medium through the set-model mutation', () => {
    render(<AgentModelCard name="chatbot_semantic_parser_v2" canEdit />);
    fireEvent.change(effortSelect()!, { target: { value: 'medium' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ reasoning_effort: 'medium' }));
  });

  it('is absent for a key that does not support it', () => {
    keyRows = [{ ...ROW, name: 'spec_understanding', reasoning_effort: null, supports_reasoning_effort: false }];
    render(<AgentModelCard name="spec_understanding" canEdit />);
    expect(screen.queryByText(/reasoning effort/i)).toBeNull();
    expect(effortSelect()).toBeUndefined();
  });

  it('reads and saves the label the runtime reads (runtime_label), not a hard-coded production', () => {
    keyRows = [{ ...ROW, runtime_label: 'staging', reasoning_effort: 'high' }];
    render(<AgentModelCard name="chatbot_semantic_parser_v2" canEdit />);
    expect(effortSelect()!.value).toBe('high');
    fireEvent.change(effortSelect()!, { target: { value: 'medium' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'staging', reasoning_effort: 'medium' }),
    );
  });

  it('a key whose runtime label is production saves on production', () => {
    keyRows = [{ ...ROW, runtime_label: 'production' }];
    render(<AgentModelCard name="chatbot_semantic_parser_v2" canEdit />);
    fireEvent.change(effortSelect()!, { target: { value: 'high' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ label: 'production' }));
  });
});
