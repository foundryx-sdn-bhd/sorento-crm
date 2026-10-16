/**
 * R9/R10/R11/S2, review round 1 - the ONE renderer three callers draw from.
 *
 * What is worth pinning here: our highlight is the only fill a NEW document ever carries,
 * a legacy `'yellow'` document keeps rendering its own colour (rather than being read as
 * "our highlight"), only a row with a `row_key` becomes editable, and `editable=false` never
 * renders an input at all - the public page's read-only guarantee (AC-E7) depends on the
 * SAME component simply not being given `editable`, not on a second code path.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { SupplierSheet, type SupplierSheetModel } from './SupplierSheet';

function sheetWith(overrides: Partial<SupplierSheetModel> = {}): SupplierSheetModel {
  return {
    title: null,
    columns: [
      { label: '型号', label_en: 'Model', field: 'item_code' },
      { label: '需装数量', label_en: 'Qty to load', field: 'qty_to_load' },
      { label: '备注', label_en: 'Remarks', field: 'line_remark' },
    ],
    rows: [
      {
        cells: [
          { value: 'A100', rowspan: 1, colspan: 1, covered: false, fill: 'highlight', red: false },
          { value: 40, rowspan: 1, colspan: 1, covered: false, fill: 'highlight', red: false },
          { value: null, rowspan: 1, colspan: 1, covered: false, fill: 'highlight', red: false },
        ],
        family_span: 1,
        appended: false,
        row_key: 'row-a',
      },
      {
        // Unmatched line on the supplier's own sheet: no `row_key`, so no cell here can
        // become an input even with `editable`.
        cells: [
          { value: 'B200', rowspan: 1, colspan: 1, covered: false, fill: null, red: false },
          { value: null, rowspan: 1, colspan: 1, covered: false, fill: null, red: false },
          { value: null, rowspan: 1, colspan: 1, covered: false, fill: null, red: false },
        ],
        family_span: 1,
        appended: false,
        row_key: null,
      },
      {
        // A notice sent before R10: the supplier's own yellow field survives on the wire.
        cells: [
          { value: 'C300', rowspan: 1, colspan: 1, covered: false, fill: 'yellow', red: false },
          { value: 0, rowspan: 1, colspan: 1, covered: false, fill: 'yellow', red: true },
          { value: null, rowspan: 1, colspan: 1, covered: false, fill: 'yellow', red: false },
        ],
        family_span: 1,
        appended: false,
        row_key: 'row-c',
      },
    ],
    totals: null,
    ...overrides,
  };
}

describe('SupplierSheet', () => {
  it('paints the highlight fill only on the row whose qty to load is > 0 (AC-E3)', () => {
    const { container } = render(<SupplierSheet sheet={sheetWith()} />);

    const highlighted = container.querySelectorAll('td.bg-\\[\\#fff2cc\\]');
    // Row A (qty 40, fill 'highlight') has three cells; nothing else on the sheet does.
    expect(highlighted).toHaveLength(3);
  });

  it('never paints the highlight on a rowSpan cell, even when fill is highlight (R10 feedback)', () => {
    // A family's shared cell (序号/体积/总体积) sits at `rowspan > 1` on the row that anchors
    // it. The backend already withholds `fill` from one of these, but a `sheet_json` frozen
    // before that fix can still send `fill: 'highlight'` on it - this is the second guard.
    const sheet = sheetWith({
      rows: [
        {
          cells: [
            { value: 'A100', rowspan: 3, colspan: 1, covered: false, fill: 'highlight', red: false },
            { value: 40, rowspan: 1, colspan: 1, covered: false, fill: 'highlight', red: false },
            { value: null, rowspan: 1, colspan: 1, covered: false, fill: 'highlight', red: false },
          ],
          family_span: 3,
          appended: false,
          row_key: 'row-a',
        },
      ],
    });

    const { container } = render(<SupplierSheet sheet={sheet} />);

    const highlighted = container.querySelectorAll('td.bg-\\[\\#fff2cc\\]');
    // Only the qty and remark cells (rowspan 1) - not the rowspan-3 family cell.
    expect(highlighted).toHaveLength(2);
  });

  it('renders a legacy yellow fill as its own colour, never as our highlight', () => {
    const { container } = render(<SupplierSheet sheet={sheetWith()} />);

    const yellow = container.querySelectorAll('td.bg-\\[\\#ffff00\\]');
    expect(yellow.length).toBeGreaterThan(0);
    for (const cell of Array.from(yellow)) {
      expect(cell.classList.contains('bg-[#fff2cc]')).toBe(false);
    }
  });

  it('renders inputs only on the row whose row_key is set, when editable', () => {
    render(<SupplierSheet sheet={sheetWith()} editable onQtyChange={vi.fn()} onRemarkChange={vi.fn()} />);

    // Row A and row C both carry a row_key: one Qty input and one Remarks input each.
    expect(screen.getAllByLabelText('Qty to load')).toHaveLength(2);
    expect(screen.getAllByLabelText('Remarks')).toHaveLength(2);
  });

  it('renders no input at all when editable is false', () => {
    render(<SupplierSheet sheet={sheetWith()} />);

    expect(screen.queryByLabelText('Qty to load')).toBeNull();
    expect(screen.queryByLabelText('Remarks')).toBeNull();
  });

  it('calls onRemarkChange with the row_key and the typed value', () => {
    const onRemarkChange = vi.fn();
    render(
      <SupplierSheet sheet={sheetWith()} editable onQtyChange={vi.fn()} onRemarkChange={onRemarkChange} />,
    );

    const [firstRemark] = screen.getAllByLabelText('Remarks');
    fireEvent.change(firstRemark, { target: { value: 'pack in 2 cartons' } });

    expect(onRemarkChange).toHaveBeenCalledWith('row-a', 'pack in 2 cartons');
  });

  it('reads a controlled value off qtyFor/remarkFor rather than the sheet cell (S2)', () => {
    // The whole point of the accessor props: a remark typed on the PLAN TABLE (edits state,
    // not this sheet's own `value`) shows here immediately, without waiting on a refetch.
    render(
      <SupplierSheet
        sheet={sheetWith()}
        editable
        qtyFor={(rowKey) => (rowKey === 'row-a' ? 99 : 0)}
        onQtyChange={vi.fn()}
        remarkFor={(rowKey) => (rowKey === 'row-a' ? 'from the plan table' : '')}
        onRemarkChange={vi.fn()}
      />,
    );

    const [qtyInput] = screen.getAllByLabelText('Qty to load') as HTMLInputElement[];
    const [remarkInput] = screen.getAllByLabelText('Remarks') as HTMLInputElement[];
    expect(qtyInput.value).toBe('99');
    expect(remarkInput.value).toBe('from the plan table');
  });

  it('NUMBER-FIELDS: a cleared quantity stays blank with a sentence and is not passed on as 0', () => {
    const onQtyChange = vi.fn();
    render(<SupplierSheet sheet={sheetWith()} editable onQtyChange={onQtyChange} onRemarkChange={vi.fn()} />);

    const [qtyInput] = screen.getAllByLabelText('Qty to load') as HTMLInputElement[];
    fireEvent.change(qtyInput, { target: { value: '' } });

    expect(qtyInput.value).toBe('');
    expect(screen.queryByText(/^Enter a number/)).not.toBeInTheDocument();
    fireEvent.blur(qtyInput);
    expect(screen.getByText(/^Enter a number/)).toBeInTheDocument();
    expect(onQtyChange).not.toHaveBeenCalled();
    fireEvent.change(qtyInput, { target: { value: '12' } });
    expect(onQtyChange).toHaveBeenCalledWith('row-a', 12);
  });
});
