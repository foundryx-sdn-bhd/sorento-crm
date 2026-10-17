/** NUMBER-FIELDS round 3 (R10): bad input in a 'between' box shows the sentence on that box. */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { RuleFactItem } from './types';

// ---- mock the facts hook ----
const useRuleFactsMock = vi.fn();
vi.mock('./useRuleFacts', () => ({
  useRuleFacts: (...a: unknown[]) => useRuleFactsMock(...a),
}));

// ---- stub the searchable dropdowns as native <select> / checkbox groups ----
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
    disabled,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
    disabled?: boolean;
  }) => (
    <select
      aria-label={placeholder}
      data-testid={`select-${placeholder}`}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {(options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string[];
    onChange: (v: string[]) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <div data-testid={`multi-${placeholder}`}>
      {(options ?? []).map((o) => (
        <button
          key={o.value}
          type="button"
          data-testid={`multi-opt-${o.value}`}
          onClick={() =>
            onChange(
              value.includes(o.value)
                ? value.filter((x) => x !== o.value)
                : [...value, o.value],
            )
          }
        >
          {o.label}
        </button>
      ))}
    </div>
  ),
}));
vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { RuleBuilder } from './RuleBuilder';

const FACTS: RuleFactItem[] = [
  {
    key: 'promotion.name',
    label: 'Promotion name',
    type: 'string',
    operators: ['eq', 'neq', 'contains', 'in', 'not_in'],
    source: 'promotion',
    sourceLabel: 'Promotion',
  },
  {
    key: 'promotion.accessLevels',
    label: 'Access levels',
    type: 'list',
    operators: ['contains_any', 'contains_all', 'not_contains'],
    source: 'promotion',
    sourceLabel: 'Promotion',
    options: [
      { value: 'sorento_dealer', label: 'Sorento Dealer' },
      { value: 'cabana_office', label: 'Cabana Office' },
    ],
  },
  {
    key: 'promotion.isActive',
    label: 'Active',
    type: 'boolean',
    operators: ['is_true', 'is_false'],
    source: 'promotion',
    sourceLabel: 'Promotion',
  },
  {
    key: 'promotion.startDate',
    label: 'Start date',
    type: 'date',
    operators: ['before', 'after', 'between'],
    source: 'promotion',
    sourceLabel: 'Promotion',
  },
  {
    key: 'promotion.endDate.daysUntil',
    label: 'Days until end',
    type: 'number',
    operators: ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'between'],
    source: 'promotion',
    sourceLabel: 'Promotion',
  },
];

import { typeBadInput } from '@/test-utils/badInput';

function dataState() {
  useRuleFactsMock.mockReturnValue({ data: FACTS, isLoading: false, isError: false, error: null });
}

describe('RuleBuilder bad number input (R10)', () => {
  it.each([0, 1])('between box %i: bad input shows the sentence under the box', (index) => {
    dataState();
    render(<RuleBuilder sources={['promotion']} value={null} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /add condition/i }));
    fireEvent.change(screen.getByTestId('select-Field'), {
      target: { value: 'promotion.endDate.daysUntil' },
    });
    fireEvent.change(screen.getByTestId('select-Operator'), { target: { value: 'between' } });
    const boxes = document.querySelectorAll('input[type="number"]');
    typeBadInput(boxes[index] as HTMLElement);
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
  });
});
