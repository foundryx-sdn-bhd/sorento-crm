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
