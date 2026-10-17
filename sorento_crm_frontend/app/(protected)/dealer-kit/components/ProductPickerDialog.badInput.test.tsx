/** NUMBER-FIELDS round 4 (S2): a dealer-kit collection rule holding bad number input is refused on "Use these products", with the sentence on that box. */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { typeBadInput } from '@/test-utils/badInput';

vi.mock('../services/productPickerService', () => ({
  PICKER_PAGE_SIZE: 50,
  listPickerProducts: vi.fn(async () => []),
  listPickerCategories: vi.fn(async () => []),
  listProductThumbnails: vi.fn(async () => ({})),
}));

vi.mock('@/components/rule-builder/useRuleFacts', () => ({
  useRuleFacts: () => ({
    data: [
      {
        key: 'product.listPrice',
        label: 'List price',
        type: 'number',
        operators: ['eq', 'gt', 'between'],
        source: 'product',
        sourceLabel: 'Product',
      },
    ],
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder}
      data-testid={`select-${placeholder}`}
      value={value}
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
vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { EMPTY_SELECTION, ProductPickerDialog } from './ProductPickerDialog';

function setup(operator: 'eq' | 'between') {
  const onSave = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ProductPickerDialog open onOpenChange={vi.fn()} value={EMPTY_SELECTION} onSave={onSave} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: /add condition/i }));
  fireEvent.change(screen.getByTestId('select-Field'), { target: { value: 'product.listPrice' } });
  fireEvent.change(screen.getByTestId('select-Operator'), { target: { value: operator } });
  return { onSave };
}

describe('ProductPickerDialog rule with bad number input (S2)', () => {
  it('between bound: Use these products sends nothing and the sentence stays on the box', () => {
    const { onSave } = setup('between');
    typeBadInput(document.querySelectorAll('input[type="number"]')[0] as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: /use these products/i }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
  });

  it('single value: bad input shows the sentence on the box and Use these products sends nothing', () => {
    const { onSave } = setup('eq');
    typeBadInput(document.querySelector('input[type="number"]') as HTMLElement);
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /use these products/i }));
    expect(onSave).not.toHaveBeenCalled();
  });
});

describe('ProductPickerDialog bad rule number while on the By hand tab (N3)', () => {
  it('Use these products is never a silent no-op: a message is visible and nothing is saved', () => {
    const { onSave } = setup('eq');
    typeBadInput(document.querySelector('input[type="number"]') as HTMLElement);
    fireEvent.mouseDown(screen.getByRole('tab', { name: /by hand/i }));
    fireEvent.click(screen.getByRole('button', { name: /use these products/i }));
    expect(onSave).not.toHaveBeenCalled();
    // Either the sentence on the rule box (tab switched back) or a message that names the rule's bad number.
    const msgs = screen
      .queryAllByText(/enter a number|(rule|number).*(not a number|invalid|fix)|(not a number|invalid|fix).*(rule|number)/i)
      .filter((el) => !el.closest("button, [role=tab]"));
    expect(msgs.length).toBeGreaterThan(0);
  });
});

describe('ProductPickerDialog reopen resets tab and refusal (N6)', () => {
  it('after By hand plus a refusal, close and reopen lands on By rule with no refusal message', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const ui = (open: boolean) => (
      <QueryClientProvider client={client}>
        <ProductPickerDialog open={open} onOpenChange={vi.fn()} value={EMPTY_SELECTION} onSave={vi.fn()} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui(true));
    fireEvent.click(screen.getByRole('button', { name: /add condition/i }));
    fireEvent.change(screen.getByTestId('select-Field'), { target: { value: 'product.listPrice' } });
    fireEvent.change(screen.getByTestId('select-Operator'), { target: { value: 'eq' } });
    typeBadInput(document.querySelector('input[type="number"]') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: /use these products/i }));
    fireEvent.mouseDown(screen.getByRole('tab', { name: /by hand/i }));
    expect(screen.getByRole('tab', { name: /by hand/i })).toHaveAttribute('data-state', 'active');
    rerender(ui(false));
    rerender(ui(true));
    expect(screen.getByRole('tab', { name: /by rule/i })).toHaveAttribute('data-state', 'active');
    expect(screen.getByRole('tab', { name: /by hand/i })).toHaveAttribute('data-state', 'inactive');
    expect(screen.queryAllByText(/enter a number/i)).toHaveLength(0);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('ProductPickerDialog reopen clears the refusal flag by itself (round 9 N3)', () => {
  it('a saved selection that still holds a bad number shows no alert after reopen until Use these products is pressed', () => {
    const bad = {
      ...EMPTY_SELECTION,
      conditions: {
        kind: 'group',
        id: 'g-1',
        combinator: 'and',
        rules: [{ kind: 'condition', id: 'c-1', fact: 'product.listPrice', operator: 'eq', value: 'NaN' }],
      },
    } as unknown as typeof EMPTY_SELECTION;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const ui = (open: boolean) => (
      <QueryClientProvider client={client}>
        <ProductPickerDialog open={open} onOpenChange={vi.fn()} value={bad} onSave={vi.fn()} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui(true));
    fireEvent.click(screen.getByRole('button', { name: /use these products/i }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    rerender(ui(false));
    rerender(ui(true));
    // The draft is reset to the same bad value, so only the refusal flag can keep the alert up.
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /use these products/i }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});
