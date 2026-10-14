/**
 * SET-QUOTE-LINE A7 / F2 and the owner's naming ruling (14 Oct, "just call Non-standard"): a row
 * with neither a product nor a set reads "Non-standard" (tooltip "Not a catalogue product"); a
 * linked product or set outside the series reads "Non-standard" with the series tooltip; the word
 * "custom" appears nowhere, and a line never shows two Non-standard pills.
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
  const draftOf = (extra: Record<string, string> = {}) => ({
    ...emptyDraft(),
    unit_price: '900.00',
    ...extra,
  });

  it('shows no pill at all for a clean line that names a set', () => {
    render(
      <LineFlags
        quotationId="q1"
        line={line({ product_set_id: 'set-1' })}
        draft={draftOf({ product_set_id: 'set-1' })}
      />,
    );
    expect(screen.queryByText('Non-standard')).not.toBeInTheDocument();
    expect(screen.queryByText(/custom/i)).toBeNull();
  });

  it('reads Non-standard with the catalogue tooltip for a line with neither product nor set', () => {
    render(<LineFlags quotationId="q1" line={line()} draft={draftOf()} />);
    expect(screen.getByText('Non-standard')).toHaveAttribute('title', 'Not a catalogue product');
    expect(screen.queryByText(/custom/i)).toBeNull();
  });

  it('shows exactly one Non-standard pill when the server also marks a no-product line non-standard', () => {
    render(
      <LineFlags
        quotationId="q1"
        line={line({ is_non_standard: true })}
        draft={draftOf()}
      />,
    );
    expect(screen.getAllByText('Non-standard')).toHaveLength(1);
    expect(screen.getByText('Non-standard')).toHaveAttribute('title', 'Not a catalogue product');
    expect(screen.queryByText(/custom/i)).toBeNull();
  });

  it('keeps the series tooltip on a set line outside the series, as one pill', () => {
    render(
      <LineFlags
        quotationId="q1"
        line={line({ product_set_id: 'set-1', is_non_standard: true })}
        draft={draftOf({ product_set_id: 'set-1' })}
      />,
    );
    expect(screen.getAllByText('Non-standard')).toHaveLength(1);
    expect(screen.getByText('Non-standard')).toHaveAttribute(
      'title',
      'Outside the series this scope is quoted from',
    );
    expect(screen.queryByText(/custom/i)).toBeNull();
  });

  it('keeps the series tooltip on a linked product outside the series', () => {
    render(
      <LineFlags
        quotationId="q1"
        line={line({ product_id: 'p1', is_non_standard: true })}
        draft={draftOf({ product_id: 'p1' })}
      />,
    );
    expect(screen.getByText('Non-standard')).toHaveAttribute(
      'title',
      'Outside the series this scope is quoted from',
    );
  });

  it('shows a set line below its floor as below floor and not as non-standard', () => {
    render(
      <LineFlags
        quotationId="q1"
        line={line({
          product_set_id: 'set-1',
          is_below_floor: true,
          floor_value_applied: '900.00',
          floor_level_applied: 'series',
        })}
        draft={draftOf({ product_set_id: 'set-1' })}
      />,
    );
    expect(screen.getByText('Below floor')).toBeInTheDocument();
    expect(screen.queryByText('Non-standard')).not.toBeInTheDocument();
    expect(screen.queryByText(/custom/i)).toBeNull();
  });
});
