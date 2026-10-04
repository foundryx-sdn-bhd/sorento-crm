'use client';

import { useRouter } from 'next/navigation';
import { Banknote, Edit, Info, Truck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import DetailActions from '@/components/common/DetailActions';
import { useOrder, ordersPagerQuery } from '../hooks/useOrders';
import { useOrderActions } from '../actions';
import { useDeletedRecordGuard } from '@/hooks/useDeletedRecordGuard';
import { formatDate } from '@/lib/helpers';
import OrderLinesCard from './OrderLinesCard';
import OrderFulfilledComplaintsCard from './OrderFulfilledComplaintsCard';

interface OrderDetailProps {
  orderId: string;
  /** Raw list query string (search/sort/filters) carried from the list page, so
   *  Back/Edit links preserve it. The prev/next pager reads it from the URL. */
  listSearch: string;
}

export default function OrderDetail({ orderId, listSearch }: OrderDetailProps) {
  const router = useRouter();
  const { data: order, isLoading } = useOrder(orderId);
  const listQs = listSearch ? `?${listSearch}` : '';
  const { actions, dialogs, pending } = useOrderActions(order, {
    onDeleted: () => router.push(`/order-management/orders${listQs}`),
  });

  // A delivery order this tab deleted a moment ago is gone on purpose, so a stale
  // link to it returns to the list instead of reading as a fault (S6 feedback C).
  const alreadyDeleted = useDeletedRecordGuard({
    entityId: orderId,
    notFound: !isLoading && !order,
    listPath: `/order-management/orders${listQs}`,
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!order) {
    if (alreadyDeleted) return null;
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Delivery order not found</p>
        <Button
          variant="outline"
          onClick={() => router.push(`/order-management/orders${listQs}`)}
          className="mt-4"
        >
          Back to delivery orders
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header: identity left; pager, gear and the one primary button right (D6). */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1 min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-2xl font-bold break-words min-w-0">{order.order_number}</h2>
            {order.order_status && (
              <Badge status={order.order_status.status_name}>
                {order.order_status.status_name}
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground break-words">
            {order.debtor_name || order.debtor_code || '-'} • Delivery order date: {order.order_date ? formatDate(new Date(order.order_date)) : '-'}
          </p>
        </div>
        <DetailActions
          pager={{
            detailPath: '/order-management/orders',
            currentId: orderId,
            ...ordersPagerQuery,
            ariaLabel: 'delivery order',
          }}
          actions={actions}
          dialogs={dialogs}
          pendingAction={pending}
          gearLabel="Delivery order options"
          primary={
            <Button
              onClick={() =>
                router.push(`/order-management/orders/${orderId}/edit${listQs}`)
              }
            >
              <Edit className="size-4" />
              Edit
            </Button>
          }
        />
      </div>

      <Tabs defaultValue="information" className="w-full">
        {/* `overflow-x-auto` is the list's own since S1; the icons are what make
            a scrolled strip readable when a label is half off the edge. */}
        <TabsList className="mb-4">
          <TabsTrigger value="information">
            <Info />
            <span>Delivery order information</span>
          </TabsTrigger>
          <TabsTrigger value="financial">
            <Banknote />
            <span>Financial summary</span>
          </TabsTrigger>
          <TabsTrigger value="delivery">
            <Truck />
            <span>Delivery &amp; tracking</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="information" className="mt-0 space-y-6 focus-visible:outline-none">
          <Card>
            <CardHeader>
              <CardTitle>Delivery order information</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Delivery Order Number</p>
                  <p className="font-medium">{order.order_number}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Delivery Order Date</p>
                  <p className="font-medium">{order.order_date ? formatDate(new Date(order.order_date)) : '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Debtor Code</p>
                  <p className="font-medium">{order.debtor_code || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Debtor Name</p>
                  <p className="font-medium">{order.debtor_name || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Agent</p>
                  <p className="font-medium">{order.agent || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Delivery Order Type</p>
                  <p className="font-medium">{order.order_type || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Cancelled</p>
                  <p className="font-medium">{order.is_cancelled ? 'Yes' : 'No'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Remarks CS</p>
                  <p className="font-medium">{order.remarks_cs || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Delivery Order Status</p>
                  <p className="font-medium">
                    {order.order_status ? (
                      <Badge status={order.order_status.status_name}>{order.order_status.status_name}</Badge>
                    ) : (
                      '-'
                    )}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Estimated Delivery Date</p>
                  <p className="font-medium">{order.estimated_delivery_date ? formatDate(new Date(order.estimated_delivery_date)) : '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Actual Delivery Date</p>
                  <p className="font-medium">{order.actual_delivery_date ? formatDate(new Date(order.actual_delivery_date)) : '-'}</p>
                </div>
              </div>
              {order.remarks && (
                <div>
                  <p className="text-sm text-muted-foreground">Remarks</p>
                  <p className="font-medium whitespace-pre-wrap">{order.remarks}</p>
                </div>
              )}
            </CardContent>
          </Card>
          <OrderLinesCard
            orderId={orderId}
            lines={order.lines ?? []}
            readOnly={(order.autocount_owned_fields ?? []).length > 0}
          />
        </TabsContent>

        <TabsContent value="financial" className="mt-0 focus-visible:outline-none">
          <Card>
            <CardHeader>
              <CardTitle>Financial summary</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
                <div>
                  <p className="text-sm text-muted-foreground">Subtotal Amount</p>
                  <p className="font-medium text-lg">
                    {new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(order.subtotal_amount)}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Discount Amount</p>
                  <p className="font-medium text-lg text-destructive">
                    -{new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(order.discount_amount)}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Tax Amount</p>
                  <p className="font-medium text-lg">
                    {new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(order.tax_amount)}
                  </p>
                </div>
              </div>
              <div className="pt-4 border-t">
                <p className="text-sm text-muted-foreground">Total Amount</p>
                <p className="font-bold text-xl">
                  {new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(order.total_amount)}
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-4">
                <div>
                  <p className="text-sm text-muted-foreground">Created</p>
                  <p className="font-medium text-sm">
                    {formatDate(new Date(order.created_at))}
                  </p>
                </div>
                {order.updated_at && (
                  <div>
                    <p className="text-sm text-muted-foreground">Last Updated</p>
                    <p className="font-medium text-sm">
                      {formatDate(new Date(order.updated_at))}
                    </p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="delivery" className="mt-0 focus-visible:outline-none">
          <Card>
            <CardHeader>
              <CardTitle>Delivery &amp; tracking</CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Debtor Code</p>
                  <p className="font-medium">{order.debtor_code || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Debtor Name</p>
                  <p className="font-medium">{order.debtor_name || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Agent</p>
                  <p className="font-medium">{order.agent || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Delivery Order Type</p>
                  <p className="font-medium">{order.order_type || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Remarks CS</p>
                  <p className="font-medium">{order.remarks_cs || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Customer (Ref)</p>
                  <p className="font-medium">{order.customer_ref || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Cancelled</p>
                  <p className="font-medium">{order.is_cancelled ? 'Yes' : 'No'}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Delivery Time</p>
                  <p className="font-medium">{order.pickup_time || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Checker</p>
                  <p className="font-medium">{order.checker || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Transporter</p>
                  <p className="font-medium">{order.transporter || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Driver Name</p>
                  <p className="font-medium">{order.driver_name || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Lorry Plate</p>
                  <p className="font-medium">{order.lorry_plate || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Warehouse</p>
                  <p className="font-medium">{order.warehouse || '-'}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Salesman</p>
                  <p className="font-medium">{order.salesman || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Trips</p>
                  <p className="font-medium">{order.trips ?? '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Delivery Days (2)</p>
                  <p className={`font-medium ${order.kpi_warning ? 'text-amber-600' : ''}`}>
                    {order.delivery_days ?? '-'} day(s)
                  </p>
                </div>
              </div>

              {(order.delivery_remarks || order.delivery_remarks_cs) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {order.delivery_remarks_cs && (
                    <div>
                      <p className="text-sm text-muted-foreground">Remarks CS</p>
                      <p className="font-medium whitespace-pre-wrap">{order.delivery_remarks_cs}</p>
                    </div>
                  )}
                  {order.delivery_remarks && (
                    <div>
                      <p className="text-sm text-muted-foreground">Remarks</p>
                      <p className="font-medium whitespace-pre-wrap">{order.delivery_remarks}</p>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Reverse of the complaint "Fulfilment Delivery Orders" section: the
          complaint(s) this DO fulfils (auto-linked from Remarks CS). Always
          rendered with its own empty state. */}
      <OrderFulfilledComplaintsCard orderId={orderId} />
    </div>
  );
}
