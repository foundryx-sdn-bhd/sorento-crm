/**
 * AC-CONSIGN-CRM UAC-06: the Order Inquiry lines view (worklist) shows "Consignment" in
 * the S/O line cell when the row's `so_doc_type` is consignment. Harness copied from
 * `orderInquiryWorklistColumns.cancelledLine.test.tsx`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import type { ColumnDef } from '@tanstack/react-table';
import { describe, expect, it, vi } from 'vitest';
import { useOrderInquiryWorklistColumns } from './orderInquiryWorklistColumns';

// AC-D6 precedent: Radix Tooltip only mounts TooltipContent's portal on hover, which a
// plain render+query cannot see.
vi.mock('@/components/ui/tooltip', async () => {
  const actual = await vi.importActual<typeof import('@/components/ui/tooltip')>(
    '@/components/ui/tooltip',
  );
  return {
    ...actual,
    TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});
import type { OrderInquiryWorklistRow } from '../../_shared/types/orderInquiry.types';

function worklistRow(over: Partial<OrderInquiryWorklistRow> = {}): OrderInquiryWorklistRow {
  return {
    id: 'row-1',
    qty: '10',
    state: 'raised',
    verb: 'ORDER',
    links: [],
    linked_qty: '0',
    ...over,
  } as OrderInquiryWorklistRow;
}

function OneColumnOnly({
  rows,
  columnId,
}: {
  rows: OrderInquiryWorklistRow[];
  columnId: string;
}) {
  const allColumns = useOrderInquiryWorklistColumns();
  const named = allColumns.find((column) => {
    const withKeys = column as ColumnDef<OrderInquiryWorklistRow> & {
      id?: string;
      accessorKey?: string;
    };
    return withKeys.id === columnId || withKeys.accessorKey === columnId;
  });
  if (!named) throw new Error(`no column with id or accessorKey "${columnId}"`);
  const table = useReactTable({
    data: rows,
    columns: [named],
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <table>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id} data-testid={`row-${row.original.id}`}>
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

function renderSoCell(rows: OrderInquiryWorklistRow[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OneColumnOnly rows={rows} columnId="so_number" />
    </QueryClientProvider>,
  );
}

describe('UAC-06: S/O line cell carries the Consignment label', () => {
  it('a consignment row shows Consignment', () => {
    renderSoCell([
      worklistRow({ id: 'r-c', so_number: 'CG000123', so_doc_type: 'consignment' } as Partial<OrderInquiryWorklistRow>),
    ]);
    expect(within(screen.getByTestId('row-r-c')).getByText('Consignment')).toBeInTheDocument();
  });

  it('a sales_order row shows no Consignment label', () => {
    renderSoCell([
      worklistRow({ id: 'r-s', so_number: 'SO000123', so_doc_type: 'sales_order' } as Partial<OrderInquiryWorklistRow>),
    ]);
    expect(within(screen.getByTestId('row-r-s')).queryByText('Consignment')).toBeNull();
  });

  it('a row with a null type shows no Consignment label', () => {
    renderSoCell([
      worklistRow({ id: 'r-n', so_number: 'SO000124', so_doc_type: null } as Partial<OrderInquiryWorklistRow>),
    ]);
    expect(within(screen.getByTestId('row-r-n')).queryByText('Consignment')).toBeNull();
  });
});
