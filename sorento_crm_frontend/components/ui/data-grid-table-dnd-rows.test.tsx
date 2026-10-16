/**
 * DataGridTableDndRows, the shared row-drag render path (QUOTATION-UX): its footer, its
 * full-width group row, its expanded row, and the column drop it shares with the column-drag grid.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Table,
} from '@tanstack/react-table';
import type { DragEndEvent } from '@dnd-kit/core';

import { DataGrid } from './data-grid';
import { reorderColumnsOnDragEnd } from './data-grid-table';
import { DataGridTableDndRows } from './data-grid-table-dnd-rows';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

vi.mock('next/navigation', () => ({
  usePathname: () => '/some-listing',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

type Item = { id: string; name: string; qty: number };
const DATA: Item[] = [
  { id: 'r1', name: 'Alpha', qty: 1 },
  { id: 'r2', name: 'Beta', qty: 2 },
];

const COLUMNS: ColumnDef<Item>[] = [
  { id: 'name', accessorKey: 'name', header: 'Name', size: 200, footer: () => 'FOOT-TOTAL' },
  {
    id: 'qty',
    accessorKey: 'qty',
    header: 'Qty',
    size: 100,
    meta: { expandedContent: (row: Item) => <p>{`detail for ${row.name}`}</p> },
  },
];

let lastTable: Table<Item> | null = null;

function Harness({
  columns = COLUMNS,
  expanded,
  renderGroupHeader,
}: {
  columns?: ColumnDef<Item>[];
  expanded?: Record<string, boolean>;
  renderGroupHeader?: (row: Item, previous: Item | null) => React.ReactNode | null;
}) {
  const table = useReactTable({
    data: DATA,
    columns,
    getRowId: (r) => r.id,
    getCoreRowModel: getCoreRowModel(),
    getRowCanExpand: () => true,
    state: { expanded: expanded ?? {} },
  });
  lastTable = table;
  return (
    <DataGrid
      table={table}
      recordCount={DATA.length}
      isLoading={false}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
      renderGroupHeader={renderGroupHeader}
    >
      <DataGridTableDndRows handleDragEnd={() => {}} dataIds={DATA.map((r) => r.id)} />
    </DataGrid>
  );
}

describe('DataGridTableDndRows', () => {
  it('renders a tfoot from a column footer, and none when no column declares one', () => {
    const { container, rerender } = render(<Harness />);
    expect(container.querySelector('tfoot')?.textContent).toContain('FOOT-TOTAL');

    rerender(<Harness columns={COLUMNS.map(({ footer: _footer, ...rest }) => rest)} />);
    expect(container.querySelector('tfoot')).toBeNull();
  });

  it('renders a full-width row for a row the caller marks, spanning every column', () => {
    render(
      <Harness renderGroupHeader={(row) => (row.id === 'r2' ? 'SECTION B' : null)} />,
    );
    const bar = screen.getByTestId('data-grid-group-header');
    expect(bar).toHaveTextContent('SECTION B');
    expect(bar.querySelector('td')).toHaveAttribute('colspan', '2');
    expect(screen.getAllByTestId('data-grid-group-header')).toHaveLength(1);
  });

  it('renders the expanded content under an expanded row only', () => {
    render(<Harness expanded={{ r1: true }} />);
    expect(screen.getByText('detail for Alpha')).toBeInTheDocument();
    expect(screen.queryByText('detail for Beta')).toBeNull();
  });
});

describe('reorderColumnsOnDragEnd', () => {
  it('moves the dragged column id to the drop position in the column order', () => {
    render(<Harness />);
    const setColumnOrder = vi.spyOn(lastTable!, 'setColumnOrder');
    reorderColumnsOnDragEnd(lastTable!, {
      active: { id: 'qty' },
      over: { id: 'name' },
    } as unknown as DragEndEvent);
    expect(setColumnOrder).toHaveBeenCalledWith(['qty', 'name']);
  });

  it('does nothing when dropped outside a column', () => {
    render(<Harness />);
    const setColumnOrder = vi.spyOn(lastTable!, 'setColumnOrder');
    reorderColumnsOnDragEnd(lastTable!, { active: { id: 'qty' }, over: null } as unknown as DragEndEvent);
    expect(setColumnOrder).not.toHaveBeenCalled();
  });
});
