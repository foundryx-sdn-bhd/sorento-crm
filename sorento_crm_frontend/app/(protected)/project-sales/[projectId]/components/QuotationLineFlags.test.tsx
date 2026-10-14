/**
 * SET-QUOTE-LINE A7 / F2: a line that names a SET is not a custom item. "Custom item" is a fact
 * about a row with neither a product nor a set.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { QuotationLine } from '../../_shared/types/project.types';

vi.mock('../../_shared/hooks/useProjects', () => ({
  useLineVerdict: () => ({ data: undefined }),
}));
vi.mock('./QuotationsPanel', () => ({
  formatMyr: (value: string) => `RM ${value}`,
}));

import { LineFlags } from './QuotationLineFlags';
import { emptyDraft } from './quotationLineDraft';

function line(overrides: Partial<QuotationLine> = {}): QuotationLine {
  return {
    id: 'l1',
    version_id: 'v1',
    product_id: null,
    product_set_id: null,
    set_parts: [],
    unit_price: '900.00',
    is_below_floor: false,
    is_non_standard: false,
    ...overrides,
  } as QuotationLine;
}

describe('LineFlags', () => {
  it('shows no Custom item badge for a line that names a set', () => {
    const stored = line({ product_set_id: 'set-1' });
    render(
      <LineFlags
        quotationId="q1"
        line={stored}
        draft={{ ...emptyDraft(), product_set_id: 'set-1', unit_price: '900.00' }}
      />,
    );
    expect(screen.queryByText('Custom item')).not.toBeInTheDocument();
  });

  it('still shows Custom item for a line with neither a product nor a set', () => {
    render(
      <LineFlags
        quotationId="q1"
        line={line()}
        draft={{ ...emptyDraft(), unit_price: '900.00' }}
      />,
    );
    expect(screen.getByText('Custom item')).toBeInTheDocument();
  });

  it('shows a set line that is below its floor as below floor, not as a custom item', () => {
    const stored = line({
      product_set_id: 'set-1',
      is_below_floor: true,
      floor_value_applied: '900.00',
      floor_level_applied: 'series',
    });
    render(
      <LineFlags
        quotationId="q1"
        line={stored}
        draft={{ ...emptyDraft(), product_set_id: 'set-1', unit_price: '900.00' }}
      />,
    );
    expect(screen.getByText('Below floor')).toBeInTheDocument();
    expect(screen.queryByText('Custom item')).not.toBeInTheDocument();
  });
});
