/** NUMBER-FIELDS round 3 (low-harm): bad input in a numeric predicate value shows the sentence, never saved as blank. */
import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PredicateBuilder } from './PredicateBuilder';
import type { Predicate, RoutableField } from './predicateTypes';
import { typeBadInput } from '@/test-utils/badInput';

const FIELDS: RoutableField[] = [
  { field: 'total_project_value', label: 'Total Project Value', type: 'numeric' },
];

function Harness() {
  const [predicates, setPredicates] = useState<Predicate[]>([
    { field: 'total_project_value', operator: 'equals', value: '5' },
  ]);
  return <PredicateBuilder predicates={predicates} fields={FIELDS} onChange={setPredicates} />;
}

describe('PredicateBuilder bad number input', () => {
  it('shows the sentence under the numeric value box', () => {
    Element.prototype.scrollIntoView = vi.fn();
    render(<Harness />);
    typeBadInput(screen.getByLabelText('Predicate value'));
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
  });
});
