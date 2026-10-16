/**
 * RC-1 (FE): an enum routable field (Product lines, Customer debtor type) offers its
 * `options` as the value choices. Likely already green - kept as a guard so the two new
 * enum fields cannot regress into free-text inputs.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PredicateBuilder } from './PredicateBuilder';
import type { Predicate, RoutableField } from './predicateTypes';

const FIELDS: RoutableField[] = [
  {
    field: 'product_lines',
    label: 'Product lines',
    type: 'enum',
    options: [
      { value: 'has', label: 'Has lines' },
      { value: 'none', label: 'No lines' },
    ],
  },
  {
    field: 'customer_debtor_type',
    label: 'Customer debtor type',
    type: 'enum',
    options: [
      { value: 'project', label: 'Project' },
      { value: 'project_cash', label: 'Project cash' },
      { value: 'cash_sales', label: 'Cash sales' },
    ],
  },
];

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

const optionLabels = () =>
  [...document.querySelectorAll('[role="option"]')].map((o) => o.textContent?.trim());

describe('PredicateBuilder enum fields', () => {
  it('offers the field options as value choices, not a free-text input', () => {
    const predicates: Predicate[] = [
      { field: 'customer_debtor_type', operator: 'equals', value: 'project_cash' },
    ];
    render(<PredicateBuilder predicates={predicates} fields={FIELDS} onChange={vi.fn()} />);

    expect(screen.queryByLabelText('Predicate value')).not.toBeInTheDocument();
    const row = screen.getByTestId('predicate-row');
    const valueSelect = [...row.querySelectorAll('[role="combobox"]')].pop() as HTMLElement;
    expect(valueSelect).toHaveTextContent('Project cash');

    fireEvent.click(valueSelect);
    expect(optionLabels()).toEqual(['Project', 'Project cash', 'Cash sales']);
  });

  it('offers Has lines / No lines for Product lines', () => {
    const predicates: Predicate[] = [{ field: 'product_lines', operator: 'equals', value: 'none' }];
    render(<PredicateBuilder predicates={predicates} fields={FIELDS} onChange={vi.fn()} />);

    const row = screen.getByTestId('predicate-row');
    const valueSelect = [...row.querySelectorAll('[role="combobox"]')].pop() as HTMLElement;
    expect(valueSelect).toHaveTextContent('No lines');
    fireEvent.click(valueSelect);
    expect(optionLabels()).toEqual(['Has lines', 'No lines']);
  });
});
