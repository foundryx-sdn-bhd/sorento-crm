/**
 * QUOTATION-UX fix round 2, defect 6: "<thead> cannot contain a nested <div>".
 *
 * The column DndContext sits inside <thead>, and dnd-kit renders its accessibility nodes inline
 * unless given `accessibility.container`. React logs each nesting warning once per process, so
 * the console spy is installed BEFORE the first render in this file (a fresh module graph per
 * file), and this is the only thing rendered.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { useReactTable, getCoreRowModel, type ColumnDef } from '@tanstack/react-table';

import { DataGrid } from './data-grid';
import { DataGridScroller } from './data-grid-table';
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

type Row = { id: string; name: string };

const COLUMNS: ColumnDef<Row>[] = [
  { accessorKey: 'name', header: 'Name', size: 200, footer: () => 'Total' },
];

function Harness() {
  const table = useReactTable({
    data: [{ id: 'a', name: 'Alpha' }],
    columns: COLUMNS,
    getRowId: (r) => r.id,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <DataGrid
      table={table}
      recordCount={1}
      isLoading={false}
      listingKey={null}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
    >
      <DataGridScroller>
        <DataGridTableDndRows<Row> handleDragEnd={() => undefined} dataIds={['a']} columnsDraggable />
      </DataGridScroller>
    </DataGrid>
  );
}

describe('DataGridTableDndRows DOM nesting (QUOTATION-UX defect 6)', () => {
  it('logs no nesting error with draggable columns and a footer', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<Harness />);
    const nesting = errors.mock.calls
      .map((call: unknown[]) => call.map(String).join(' '))
      .filter((text: string) => /cannot contain a nested|cannot be a child|validateDOMNesting/.test(text));
    errors.mockRestore();
    expect(nesting).toEqual([]);
  });
});
