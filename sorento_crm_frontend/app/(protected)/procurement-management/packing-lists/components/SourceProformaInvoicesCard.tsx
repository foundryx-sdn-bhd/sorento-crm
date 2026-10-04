'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import type { ColumnDef } from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { formatDate } from '@/lib/helpers';
import { usePackingListSourceInvoices } from '../hooks/usePackingLists';
import type { PackingListSourceInvoice } from '../services/packingListService';

/**
 * Which proforma invoices this container was drafted from, and how much of each came here
 * (AC-F9).
 *
 * Always rendered with its own empty state, per the CRUD standard: a container loaded from
 * a real packing-list upload has no proforma invoice behind it, and "none" is the honest
 * answer rather than a section that quietly disappears.
 *
 * "qty from this PI of its total" is the load-bearing figure: one invoice may be split
 * across two containers (Q9), so 200 of 500 here means 300 is somewhere else, and a card
 * showing only 200 would read as the whole invoice.
 */
export function SourceProformaInvoicesCard({
  packingListId,
  convertedOn,
}: {
  packingListId: string;
  /** When this container was drafted. Read off the container, so the card is not a second
   *  source for a date the header already knows. */
  convertedOn?: string | Date | null;
}) {
  const { data, isLoading, isError, error } = usePackingListSourceInvoices(packingListId);
  const invoices = data?.invoices ?? [];

  const columns = useMemo<ColumnDef<PackingListSourceInvoice>[]>(
    () => [
      {
        accessorKey: 'pi_number',
        header: ({ column }) => <DataGridColumnHeader title="Proforma invoice" column={column} />,
        cell: ({ row }) => <span className="font-medium">{row.original.pi_number}</span>,
        size: 160,
        meta: { headerTitle: 'Proforma invoice' },
      },
      {
        id: 'supplier_name',
        accessorFn: (row) => row.supplier_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        cell: ({ row }) => (
          <span
            className="block max-w-[200px] truncate"
            title={row.original.supplier_name ?? undefined}
          >
            {row.original.supplier_name ?? '-'}
          </span>
        ),
        size: 200,
        meta: { headerTitle: 'Supplier' },
      },
      {
        id: 'invoice_date',
        accessorFn: (row) => row.invoice_date ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Invoice date" column={column} />,
        cell: ({ row }) =>
          row.original.invoice_date ? formatDate(new Date(row.original.invoice_date)) : '-',
        size: 130,
        meta: { headerTitle: 'Invoice date' },
      },
      {
        // WHICH version the container was loaded from (AC-F9). "PI-x" alone does
        // not say whether its goods were priced on the one still in force.
        id: 'revision',
        accessorFn: (row) => row.revision_no,
        header: ({ column }) => <DataGridColumnHeader title="Revision" column={column} />,
        cell: ({ row }) =>
          (row.original.revision_count ?? 1) > 1 ? (
            <span className="flex flex-wrap items-center gap-1.5">
              Revision {row.original.revision_no} of {row.original.revision_count}
              {row.original.status === 'superseded' ? (
                <Badge variant="secondary" appearance="light">
                  Superseded
                </Badge>
              ) : null}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 200,
        meta: { headerTitle: 'Revision' },
      },
      {
        id: 'lines',
        accessorFn: (row) => row.lines,
        header: ({ column }) => (
          <DataGridColumnHeader title="Lines" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-end tabular-nums">
            {row.original.lines} of {row.original.total_lines}
          </span>
        ),
        size: 110,
        meta: { headerTitle: 'Lines' },
      },
      {
        id: 'qty',
        accessorFn: (row) => row.qty,
        header: ({ column }) => (
          <DataGridColumnHeader title="Quantity here" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-end tabular-nums">
            {row.original.qty} of {row.original.total_qty}
          </span>
        ),
        size: 140,
        meta: { headerTitle: 'Quantity here' },
      },
      {
        id: 'amount',
        accessorFn: (row) => row.amount ?? 0,
        header: ({ column }) => (
          <DataGridColumnHeader title="Amount" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-end tabular-nums">
            {row.original.amount == null
              ? '-'
              : `${row.original.currency ?? ''} ${row.original.amount.toLocaleString('en-GB', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`.trim()}
          </span>
        ),
        size: 150,
        meta: { headerTitle: 'Amount' },
      },
      {
        id: 'open',
        header: () => <span className="sr-only">Open</span>,
        cell: ({ row }) => (
          <span className="block text-end">
            <Link
              href={`/scm/proforma-invoices/${row.original.id}`}
              className="text-primary hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              Open
            </Link>
          </span>
        ),
        size: 90,
        enableResizing: false,
        meta: { headerTitle: 'Open' },
      },
    ],
    [],
  );

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Source proforma invoices</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-20 w-full rounded-lg" />
        </CardContent>
      </Card>
    );
  }

  // The read is silent (no toast), so its failure is reported here. Falling through to the
  // empty state would tell the reader the container has no proforma invoice behind it.
  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Source proforma invoices</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            {error instanceof Error ? error.message : 'Failed to load the source proforma invoices'}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      {/* SF-7 (M5 run 3 review): `PanelDataGrid` emits its own Card, so an outer
          one here was a Card inside a Card - dropped, matching
          `ProductPurchaseHistoryTab.tsx`'s title-on-the-grid convention. */}
      <PanelDataGrid<PackingListSourceInvoice>
        title="Source proforma invoices"
        columns={columns}
        rows={invoices}
        getRowId={(row) => row.id}
        listingKey="procurement.packing_lists.view::source-invoices"
        emptyTitle="Read from a packing list, not drafted from a proforma invoice."
        // SF-8 (M5 run 3 review): a document's own line table renders every
        // row - a page-2 would hide lines the reader expects in one scroll.
        paginate={false}
      />
      {invoices.length > 0 && (
        // Who drafted the container and when. Under the table rather than down it: it
        // is one fact about the container, and a column repeating it on every row
        // would read as a fact about each invoice. `created_by` is the NAME the server
        // resolved - `created_by` on the container is a user id, and printing that put
        // a UUID on the page.
        <p className="mt-3 text-sm text-muted-foreground">
          Uploaded by {data?.created_by || 'System'}
          {convertedOn ? `, converted on ${formatDate(new Date(convertedOn))}` : ''}.
        </p>
      )}
    </>
  );
}

export default SourceProformaInvoicesCard;
