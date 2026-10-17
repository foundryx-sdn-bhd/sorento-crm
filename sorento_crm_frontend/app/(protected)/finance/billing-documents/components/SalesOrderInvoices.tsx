'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { type ColumnDef } from '@tanstack/react-table';
import { useRouter } from 'next/navigation';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { useBillingDocumentsQuery } from '../hooks/useBillingDocuments';
import { formatBillingAmount, formatDay } from '../lib/format';
import { useCanViewOutstanding } from '../lib/permissions';
import type { BillingDocumentListRow } from '../types/billingDocument.types';
import { BillingStatusBadge, BillingTypeBadge } from './BillingBadges';
import { OutstandingCell } from './OutstandingCell';

/**
 * The sales order record's Invoices tab (UAC S3-1): the billing documents whose lines point at
 * this order, unpaged because one order has a handful. The caller hides the tab without the
 * view slug; Outstanding follows OUTSTANDING_VIEW_SLUG.
 */
export function SalesOrderInvoices({ salesOrderId }: { salesOrderId: string }) {
  const router = useRouter();
  const canSeeOutstanding = useCanViewOutstanding();
  const { data, isLoading, error, refetch } = useBillingDocumentsQuery({
    pageIndex: 0,
    pageSize: 100,
    sorting: [{ id: 'doc_date', desc: true }],
    searchQuery: '',
    salesOrderId,
  });

  const columns = useMemo<ColumnDef<BillingDocumentListRow>[]>(
    () => [
      {
        id: 'document_type',
        header: 'Type',
        size: 130,
        cell: ({ row }) => <BillingTypeBadge type={row.original.document_type} />,
        meta: { headerTitle: 'Type' },
      },
      {
        id: 'doc_no',
        header: 'Number',
        size: 140,
        cell: ({ row }) => (
          <Link
            href={`/finance/billing-documents/${row.original.id}`}
            onClick={(e) => e.stopPropagation()}
            className="block truncate font-medium text-primary hover:underline"
            title={row.original.doc_no}
          >
            {row.original.doc_no}
          </Link>
        ),
        meta: { headerTitle: 'Number' },
      },
      { id: 'doc_date', header: 'Date', size: 110, cell: ({ row }) => formatDay(row.original.doc_date), meta: { headerTitle: 'Date' } },
      {
        id: 'total',
        header: 'Total',
        size: 140,
        cell: ({ row }) => formatBillingAmount(row.original.total, row.original.currency_code),
        meta: { headerTitle: 'Total', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      ...(canSeeOutstanding
        ? [
            {
              id: 'outstanding',
              header: 'Outstanding',
              size: 140,
              cell: ({ row }) => (
                <OutstandingCell
                  outstanding={row.original.outstanding}
                  currencyCode={row.original.currency_code}
                />
              ),
              meta: { headerTitle: 'Outstanding', headerClassName: 'text-right', cellClassName: 'text-right' },
            } as ColumnDef<BillingDocumentListRow>,
          ]
        : []),
      {
        id: 'status',
        header: 'Status',
        size: 120,
        cell: ({ row }) => <BillingStatusBadge status={row.original.status} />,
        meta: { headerTitle: 'Status' },
      },
    ],
    [canSeeOutstanding],
  );

  return (
    <PanelDataGrid
      title="Invoices"
      columns={columns}
      rows={data?.data ?? []}
      getRowId={(r) => r.id}
      listingKey="finance.billing_documents.view::sales-order"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      paginate={false}
      emptyTitle="No invoices"
      emptyBody="No billing documents point at this order yet."
      onRowClick={(r) => router.push(`/finance/billing-documents/${r.id}`)}
    />
  );
}
