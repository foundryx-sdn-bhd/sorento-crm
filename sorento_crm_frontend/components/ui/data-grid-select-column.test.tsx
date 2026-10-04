/**
 * FULFIL-CONFIRM-SCOPE (AC-L1): `buildSelectColumn({ selectAllRows: true })` ticks every row
 * across every page and says so ("Select all rows"); the default header tick box stays the
 * page-only "Select all rows on this page", so no other listing changes behaviour.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table';

import { buildSelectColumn } from './data-grid-select-column';

type Row = { id: string; name: string };

const DATA: Row[] = Array.from({ length: 5 }, (_, index) => ({
  id: `r${index + 1}`,
  name: `Row ${index + 1}`,
}));

function Harness({ selectAllRows }: { selectAllRows?: boolean }) {
  const columns = React.useMemo<ColumnDef<Row>[]>(
    () => [buildSelectColumn<Row>(selectAllRows ? { selectAllRows: true } : undefined)],
    [selectAllRows],
  );
  const table = useReactTable({
    data: DATA,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getRowId: (row) => row.id,
    enableRowSelection: true,
    initialState: { pagination: { pageIndex: 0, pageSize: 2 } },
  });
  return (
    <div>
      {table.getHeaderGroups().map((group) =>
        group.headers.map((header) => (
          <div key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</div>
        )),
      )}
      <output data-testid="selected">
        {table
          .getSelectedRowModel()
          .rows.map((row) => row.id)
          .join(',')}
      </output>
    </div>
  );
}

describe('buildSelectColumn: selectAllRows', () => {
  it('labels the header box "Select all rows" and ticks every row across every page', () => {
    render(<Harness selectAllRows />);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows' }));

    expect(screen.getByTestId('selected')).toHaveTextContent('r1,r2,r3,r4,r5');
  });

  it('un-ticks every row again on a second click', () => {
    render(<Harness selectAllRows />);
    const box = screen.getByRole('checkbox', { name: 'Select all rows' });

    fireEvent.click(box);
    fireEvent.click(box);

    expect(screen.getByTestId('selected')).toHaveTextContent('');
  });

  it('keeps the default header box page-only and labelled for the page', () => {
    render(<Harness />);

    expect(screen.queryByRole('checkbox', { name: 'Select all rows' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows on this page' }));

    expect(screen.getByTestId('selected')).toHaveTextContent('r1,r2');
    expect(screen.getByTestId('selected')).not.toHaveTextContent('r3');
  });
});
