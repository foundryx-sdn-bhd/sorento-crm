'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { type ColumnDef } from '@tanstack/react-table';
import { useRouter } from 'next/navigation';
import { Info, Link2, ListOrdered } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import BackToList from '@/components/common/BackToList';
import { Container } from '@/components/common/container';
import { Field } from '@/components/common/Field';
import ListPager from '@/components/common/ListPager';
import { QueryErrorState } from '@/components/common/LoadErrorState';
import { PageHeader } from '@/components/common/PageHeader';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { isNotFound } from '@/lib/api-client';
import { formatDateTimeInMalaysia, formatMoney2dp } from '@/lib/helpers';
import { BillingStatusBadge, BillingTypeBadge } from '../../components/BillingBadges';
import { OutstandingCell } from '../../components/OutstandingCell';
import { useBillingDocumentQuery } from '../../hooks/useBillingDocuments';
import { formatBillingAmount, formatDay } from '../../lib/format';
import { billingPagerQuery } from '../../lib/listQuery';
import { useCanViewOutstanding } from '../../lib/permissions';
import {
  BILLING_TYPE_LABEL,
  type BillingDocumentDetail as Detail,
  type BillingDocumentLine,
  type BillingRelatedNote,
} from '../../types/billingDocument.types';

/**
 * `/finance/billing-documents/{id}` (UAC S2-5..S2-7). Read only: no edit, no delete. The meta
 * strip sits directly under the header; the line tabs are Details, Lines, Related and every
 * one renders an empty state. Outstanding follows OUTSTANDING_VIEW_SLUG.
 */
type Tab = 'details' | 'lines' | 'related';

function EmptyPanel({ title, hint }: { title: string; hint?: string }) {
  return (
    <Card>
      <div className="flex flex-col items-center gap-1 px-4 py-10 text-center">
        <span className="text-sm font-medium">{title}</span>
        {hint ? <span className="text-sm text-muted-foreground">{hint}</span> : null}
      </div>
    </Card>
  );
}

function LineFrom({ line }: { line: BillingDocumentLine }) {
  if (!line.from_doc_type || !line.from_doc_no) return null;
  const so = line.sales_order;
  if (line.from_doc_type === 'SO' && so) {
    return (
      <Link href={`/scm/sales-orders/${so.id}`} className="block truncate text-primary hover:underline" title={so.doc_no}>
        SO {so.doc_no}
      </Link>
    );
  }
  const label = `${line.from_doc_type} ${line.from_doc_no}`;
  return (
    <span className="block truncate" title={so ? `${label} (${so.doc_no})` : label}>
      {label}
      {so ? (
        <>
          {' '}
          <Link href={`/scm/sales-orders/${so.id}`} className="text-primary hover:underline">
            {so.doc_no}
          </Link>
        </>
      ) : null}
    </span>
  );
}

export default function BillingDocumentDetail({ id }: { id: string }) {
  const router = useRouter();
  const canSeeOutstanding = useCanViewOutstanding();
  const { data: doc, isLoading, error, refetch, isFetching } = useBillingDocumentQuery(id);
  const [tab, setTab] = useState<Tab>('details');

  const notFound = isNotFound(error);
  const failed = !!error && (!doc || notFound);

  const lineColumns = useMemo<ColumnDef<BillingDocumentLine>[]>(
    () => [
      { id: 'line_no', header: 'No.', size: 60, cell: ({ row }) => row.original.line_no, meta: { headerTitle: 'No.' } },
      {
        id: 'item_code',
        header: 'Item',
        size: 140,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.item_code ?? ''}>
            {row.original.item_code}
          </span>
        ),
        meta: { headerTitle: 'Item' },
      },
      {
        id: 'description',
        header: 'Description',
        size: 240,
        cell: ({ row }) => {
          const text = row.original.product_name ?? row.original.description ?? '';
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
        meta: { headerTitle: 'Description' },
      },
      {
        id: 'quantity',
        header: 'Qty',
        size: 80,
        cell: ({ row }) => formatMoney2dp(row.original.quantity),
        meta: { headerTitle: 'Qty', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      { id: 'uom', header: 'UoM', size: 70, cell: ({ row }) => row.original.uom, meta: { headerTitle: 'UoM' } },
      {
        id: 'unit_price',
        header: 'Price',
        size: 100,
        cell: ({ row }) => formatMoney2dp(row.original.unit_price),
        meta: { headerTitle: 'Price', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'discount_amount',
        header: 'Discount',
        size: 100,
        cell: ({ row }) => formatMoney2dp(row.original.discount_amount),
        meta: { headerTitle: 'Discount', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'net_amount',
        header: 'Net',
        size: 110,
        cell: ({ row }) => formatMoney2dp(row.original.net_amount),
        meta: { headerTitle: 'Net', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      { id: 'tax_code', header: 'Tax code', size: 90, cell: ({ row }) => row.original.tax_code, meta: { headerTitle: 'Tax code' } },
      {
        id: 'tax_amount',
        header: 'Tax',
        size: 100,
        cell: ({ row }) => formatMoney2dp(row.original.tax_amount),
        meta: { headerTitle: 'Tax', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'line_total',
        header: 'Total',
        size: 110,
        cell: ({ row }) => formatMoney2dp(row.original.line_total),
        meta: { headerTitle: 'Total', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'from',
        header: 'From',
        size: 170,
        cell: ({ row }) => <LineFrom line={row.original} />,
        meta: { headerTitle: 'From' },
      },
    ],
    [],
  );

  const noteColumns = useMemo<ColumnDef<BillingRelatedNote>[]>(
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
        size: 120,
        cell: ({ row }) => formatBillingAmount(row.original.total, row.original.currency_code),
        meta: { headerTitle: 'Total', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      ...(canSeeOutstanding
        ? [
            {
              id: 'outstanding',
              header: 'Outstanding',
              size: 130,
              cell: ({ row }: { row: { original: BillingRelatedNote } }) => (
                <OutstandingCell
                  outstanding={row.original.outstanding}
                  currencyCode={row.original.currency_code}
                />
              ),
              meta: { headerTitle: 'Outstanding', headerClassName: 'text-right', cellClassName: 'text-right' },
            } as ColumnDef<BillingRelatedNote>,
          ]
        : []),
    ],
    [canSeeOutstanding],
  );

  if (failed && notFound) {
    return (
      <Container>
        <PageHeader title="Billing Document" actions={<BackToList listPath="/finance/billing-documents" label="Back to billing documents" />} />
        <Card className="flex flex-col items-center gap-1 p-10 text-center">
          <div className="text-sm font-semibold">Billing document not found</div>
        </Card>
      </Container>
    );
  }
  if (failed) {
    return (
      <Container>
        <PageHeader title="Billing Document" actions={<BackToList listPath="/finance/billing-documents" label="Back to billing documents" />} />
        <QueryErrorState
          error={error}
          title="Could not load this billing document"
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      </Container>
    );
  }

  const header = (d: Detail | undefined) => (
    <PageHeader
      title={
        d ? (
          <span className="flex flex-wrap items-center gap-2">
            <span className="break-all">{d.doc_no}</span>
            <BillingTypeBadge type={d.document_type} />
            <BillingStatusBadge status={d.status} />
          </span>
        ) : (
          <Skeleton className="h-7 w-48" />
        )
      }
      crumbTitle={d?.doc_no ?? 'Billing Document'}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <ListPager
            {...billingPagerQuery}
            detailPath="/finance/billing-documents"
            currentId={id}
            ariaLabel="billing document"
          />
          <BackToList listPath="/finance/billing-documents" label="Back to billing documents" />
        </div>
      }
    />
  );

  if (isLoading || !doc) {
    return (
      <>
        <Container>{header(undefined)}</Container>
        <Container>
          <div className="space-y-4">
            <Skeleton className="h-5 w-full max-w-xl" />
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        </Container>
      </>
    );
  }

  const outstandingLabel =
    doc.outstanding === null
      ? null
      : doc.outstanding === 0
        ? 'Paid'
        : `Outstanding ${formatBillingAmount(doc.outstanding, doc.currency_code)}`;
  const hasRelated =
    !!doc.against ||
    doc.related.notes.length > 0 ||
    doc.related.sales_orders.length > 0 ||
    doc.related.delivery_order_nos.length > 0;

  return (
    <>
      <Container>{header(doc)}</Container>
      <Container>
        <div className="space-y-5">
          {/* Meta strip: read-only facts, never inside a tab body. */}
          <div
            aria-label="Document facts"
            className="flex flex-col gap-1 text-sm text-muted-foreground sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-3"
          >
            <span>AutoCount</span>
            <span>Last synced {doc.last_synced_at ? formatDateTimeInMalaysia(doc.last_synced_at) : '-'}</span>
            <span>Document date {formatDay(doc.doc_date)}</span>
            {canSeeOutstanding && outstandingLabel ? (
              <span className="font-medium text-foreground">
                {outstandingLabel}
                {doc.outstanding !== 0 && doc.due_date ? ` · due ${formatDay(doc.due_date)}` : ''}
                {doc.outstanding_as_of ? ` · as of ${formatDay(doc.outstanding_as_of)}` : ''}
              </span>
            ) : null}
          </div>

          <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
            <TabsList variant="line" className="mb-5">
              <TabsTrigger value="details">
                <Info className="size-4" />
                <span>Details</span>
              </TabsTrigger>
              <TabsTrigger value="lines">
                <ListOrdered className="size-4" />
                <span>Lines</span>
              </TabsTrigger>
              <TabsTrigger value="related">
                <Link2 className="size-4" />
                <span>Related</span>
              </TabsTrigger>
            </TabsList>

            <TabsContent value="details">
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
                <Card className="lg:col-span-2">
                  <CardHeader>
                    <CardTitle>Document</CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field label="Customer">
                      {doc.customer_id ? (
                        <Link
                          href={`/order-management/customers/${doc.customer_id}`}
                          className="text-primary hover:underline"
                        >
                          {doc.customer_name}
                        </Link>
                      ) : (
                        doc.customer_name
                      )}
                      <span className="ms-2 font-normal text-muted-foreground">{doc.debtor_code}</span>
                    </Field>
                    <Field label="Agent">
                      {doc.agent_code ? (
                        doc.agent_name ? `${doc.agent_code} - ${doc.agent_name}` : doc.agent_code
                      ) : (
                        '-'
                      )}
                    </Field>
                    <Field label="Currency">
                      {doc.currency_code}
                      {doc.currency_code !== 'MYR' ? (
                        <span className="ms-2 font-normal text-muted-foreground">
                          rate {doc.currency_rate}
                        </span>
                      ) : null}
                    </Field>
                    <Field label="Reference">{doc.ref || '-'}</Field>
                    <div className="sm:col-span-2">
                      <Field label="Description">{doc.description || '-'}</Field>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle>Totals</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Field label="Net">{formatBillingAmount(doc.net_total, doc.currency_code)}</Field>
                    <Field label="Tax">{formatBillingAmount(doc.tax_total, doc.currency_code)}</Field>
                    <Field label="Total">{formatBillingAmount(doc.total, doc.currency_code)}</Field>
                    <Field label="Local net (MYR)">{formatMoney2dp(doc.local_net_total)}</Field>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            <TabsContent value="lines">
              <PanelDataGrid
                title="Lines"
                columns={lineColumns}
                rows={doc.lines}
                getRowId={(l) => String(l.line_no)}
                listingKey="finance.billing_documents.view::lines"
                paginate={false}
                emptyTitle="No lines"
                emptyBody="This document has no lines."
              />
            </TabsContent>

            <TabsContent value="related">
              {hasRelated ? (
                <div className="space-y-6">
                  {doc.against ? (
                    <Card>
                      <CardHeader>
                        <CardTitle>
                          {doc.document_type === 'credit_note' ? 'Credit note against' : 'Debit note against'}
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        {doc.against.id ? (
                          <Link
                            href={`/finance/billing-documents/${doc.against.id}`}
                            className="text-sm font-medium text-primary hover:underline"
                          >
                            {doc.against.doc_no}
                          </Link>
                        ) : (
                          <span className="text-sm font-medium">{doc.against.doc_no}</span>
                        )}
                      </CardContent>
                    </Card>
                  ) : null}
                  {doc.related.notes.length > 0 ? (
                    <PanelDataGrid
                      title="Credit and debit notes"
                      columns={noteColumns}
                      rows={doc.related.notes}
                      getRowId={(n) => n.id}
                      listingKey="finance.billing_documents.view::notes"
                      paginate={false}
                      emptyTitle="No credit or debit notes"
                      onRowClick={(n) => router.push(`/finance/billing-documents/${n.id}`)}
                    />
                  ) : null}
                  {doc.related.sales_orders.length > 0 ? (
                    <Card>
                      <CardHeader>
                        <CardTitle>Sales orders</CardTitle>
                      </CardHeader>
                      <CardContent className="flex flex-wrap gap-x-4 gap-y-1">
                        {doc.related.sales_orders.map((so) => (
                          <Link
                            key={so.id}
                            href={`/scm/sales-orders/${so.id}`}
                            className="text-sm font-medium text-primary hover:underline"
                          >
                            {so.doc_no}
                          </Link>
                        ))}
                      </CardContent>
                    </Card>
                  ) : null}
                  {doc.related.delivery_order_nos.length > 0 ? (
                    <Card>
                      <CardHeader>
                        <CardTitle>Delivery orders</CardTitle>
                      </CardHeader>
                      <CardContent className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                        {doc.related.delivery_order_nos.map((no) => (
                          <span key={no}>{no}</span>
                        ))}
                      </CardContent>
                    </Card>
                  ) : null}
                </div>
              ) : (
                <EmptyPanel
                  title="No related documents"
                  hint={`No notes, sales orders or delivery orders are linked to this ${BILLING_TYPE_LABEL[doc.document_type].toLowerCase()}.`}
                />
              )}
            </TabsContent>
          </Tabs>
        </div>
      </Container>
    </>
  );
}
