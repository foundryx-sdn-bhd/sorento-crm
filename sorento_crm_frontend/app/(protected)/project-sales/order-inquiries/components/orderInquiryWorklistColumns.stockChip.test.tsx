/**
 * STOCK-STATUS (`documentation/plans/inventory/PLAN-stock-status-embed-17oct.md`, AC-20): the
 * worklist's Item code cell carries the stock status chip for the row's product, scoped to
 * the row's SO line. The chip itself is stubbed (its own suite covers it); this pins the wiring.
 * Harness as in `orderInquiryWorklistColumns.cancelledLine.test.tsx`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import type { ColumnDef } from '@tanstack/react-table';
import { describe, expect, it, vi } from 'vitest';
import type { OrderInquiryWorklistRow } from '../../_shared/types/orderInquiry.types';

const chipSpy = vi.fn();
vi.mock('@/components/stock-status/StockStatusChip', () => ({
  StockStatusChip: (props: Record<string, unknown>) => {
    chipSpy(props);
    return <span data-testid="stock-chip" />;
  },
}));

import { useOrderInquiryWorklistColumns } from './orderInquiryWorklistColumns';

function ItemCodeOnly({ rows }: { rows: OrderInquiryWorklistRow[] }) {
  const column = useOrderInquiryWorklistColumns().find(
    (c) =>
      (c as ColumnDef<OrderInquiryWorklistRow> & { accessorKey?: string })
        .accessorKey === 'item_code',
  );
  if (!column) throw new Error('no item_code column');
  const table = useReactTable({
    data: rows,
    columns: [column],
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <table>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id}>
            {row.getVisibleCells().map((cell) => (
              <td key={cell.id}>
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function renderRows(rows: Partial<OrderInquiryWorklistRow>[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const full = rows.map(
    (over, i) =>
      ({
        id: `row-${i}`,
        qty: '10',
        state: 'raised',
        verb: 'ORDER',
        links: [],
        linked_qty: '0',
        ...over,
      }) as OrderInquiryWorklistRow,
  );
  return render(
    <QueryClientProvider client={client}>
      <ItemCodeOnly rows={full} />
    </QueryClientProvider>,
  );
}

describe('worklist Item code: the stock status chip', () => {
  it('carries the chip for the row product, scoped to its SO line', () => {
    renderRows([
      { item_code: 'SRTWB1515', product_id: 'prod-1', core_line_id: 'line-80' },
    ]);
    expect(screen.getByTestId('stock-chip')).toBeInTheDocument();
    expect(chipSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({
        productId: 'prod-1',
        itemCode: 'SRTWB1515',
        lineIds: ['line-80'],
      }),
    );
  });

  it('draws no chip for a row with no product', () => {
    renderRows([{ item_code: 'FREE TEXT', product_id: null }]);
    expect(screen.queryByTestId('stock-chip')).not.toBeInTheDocument();
  });
});
