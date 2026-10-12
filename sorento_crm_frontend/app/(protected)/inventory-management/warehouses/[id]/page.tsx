'use client';

import { use } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { CalendarRange, Edit, Info, MoveLeft, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useBackToListHref } from '@/components/common/BackToList';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import DetailActions from '@/components/common/DetailActions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { warehousesPagerQuery } from '../hooks/useWarehouses';
import { useWarehouse } from '../hooks/useWarehouses';
import { formatDate } from '@/lib/helpers';

/**
 * Read-only field row. Renders even when the record has no value for it.
 *
 * `className` carries the grid span, which must match the same field's span on the edit
 * form: matching DOM order is not enough, since a field spanning both columns in one view
 * and one column in the other lands in a different row and column between the two.
 */
function Field({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={['min-w-0', className].filter(Boolean).join(' ')}>
      <p className="text-sm text-muted-foreground">{label}</p>
      <div className="font-medium break-words">{children}</div>
    </div>
  );
}

function WarehouseToolbar() {
  return (
    <Container>
      <PageHeader
        title="Warehouse"
        actions={
          <Button asChild variant="outline">
            <Link href="/inventory-management/warehouses">
              <MoveLeft /> Back to warehouses
            </Link>
          </Button>
        }
      />
    </Container>
  );
}

export default function WarehouseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const backHref = useBackToListHref('/inventory-management/warehouses');
  const { data: warehouse, isLoading } = useWarehouse(id);

  // Delete asks nothing (D7): the countdown takes the primary button's place
  // and Cancel is the way back.
  const deletion = useDeferredAction({
    actionKey: 'warehouse.delete',
    entityType: 'warehouse',
    entityId: id,
    verb: 'Deleting',
    subject: warehouse
      ? `${warehouse.warehouse_name ?? ''} (${warehouse.warehouse_code})`.trim()
      : '',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Warehouse deleted',
    invalidateKeys: [['warehouses']],
    onCommitted: () => router.push(backHref),
  });

  // Same list, same order, as the listing shows, so prev/next steps through it in order.

  if (isLoading) {
    return (
      <>
        <WarehouseToolbar />
        <Container>
          <div className="space-y-6">
            <Skeleton className="h-10 w-64" />
            <Skeleton className="h-96 w-full" />
          </div>
        </Container>
      </>
    );
  }

  if (!warehouse) {
    return (
      <>
        <WarehouseToolbar />
        <Container>
          <div className="text-center py-12">
            <p className="text-muted-foreground">Warehouse not found</p>
            <Button
              variant="outline"
              onClick={() => router.push('/inventory-management/warehouses')}
              className="mt-4"
            >
              <MoveLeft className="size-4" />
              Back to Warehouses
            </Button>
          </div>
        </Container>
      </>
    );
  }

  // Same label the edit form gives the self option, so View and Edit read alike.
  const poolCode =
    warehouse.pool_warehouse_id === warehouse.id
      ? 'This warehouse (own pool)'
      : warehouse.pool_warehouse_code || null;

  return (
    <>
      <WarehouseToolbar />

      <Container>
        <div className="space-y-6">
          {/* Header. Read-only metadata lives here, never inside a tab body. */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-2xl font-bold break-words">
                  {warehouse.warehouse_name || warehouse.warehouse_code}
                </h2>
                <Badge variant={warehouse.is_active ? 'success' : 'secondary'}>
                  {warehouse.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                System Location: {warehouse.warehouse_code}
              </p>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>Created: {formatDate(new Date(warehouse.created_at))}</span>
                <span>
                  Last Updated:{' '}
                  {warehouse.updated_at ? formatDate(new Date(warehouse.updated_at)) : '-'}
                </span>
              </div>
            </div>
            <DetailActions
              pager={{
                ...warehousesPagerQuery,
                detailPath: '/inventory-management/warehouses',
                currentId: id,
                ariaLabel: 'warehouse',
              }}
              actions={[
                {
                  key: 'warehouse.delete',
                  label: 'Delete warehouse',
                  icon: Trash2,
                  kind: 'destructive' as const,
                  disabled: deletion.isPending,
                  run: deletion.start,
                },
              ]}
              gearLabel="Warehouse options"
              pendingAction={deletion.countdown}
              primary={
                <Button
                  onClick={() => router.push(`/inventory-management/warehouses/${id}/edit`)}
                >
                  <Edit className="size-4" />
                  Edit
                </Button>
              }
            />
          </div>

          {/* Same tab set, same field order, and the same grid spans as the edit view: a
              field that spans both columns there must span both here, or it lands in a
              different row and column between the two views. */}
          <Tabs defaultValue="basic">
            <TabsList>
              <TabsTrigger value="basic">
                <Info />
                <span>Basic Information</span>
              </TabsTrigger>
              <TabsTrigger value="planning">
                <CalendarRange />
                <span>Planning</span>
              </TabsTrigger>
            </TabsList>

            <TabsContent value="basic" className="mt-6">
              <Card>
                <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-6">
                  <Field label="System Location">{warehouse.warehouse_code}</Field>
                  <Field label="System Location Description">
                    {warehouse.warehouse_name || '-'}
                  </Field>
                  <Field label="Warehouse" className="md:col-span-2">
                    {warehouse.location || '-'}
                  </Field>
                  <Field label="Active Status" className="md:col-span-2">
                    <Badge variant={warehouse.is_active ? 'success' : 'secondary'}>
                      {warehouse.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  </Field>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="planning" className="mt-6">
              <Card>
                <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-6">
                  <Field label="Available for planning" className="md:col-span-2">
                    <Badge
                      variant={warehouse.counts_as_available === false ? 'secondary' : 'success'}
                    >
                      {warehouse.counts_as_available === false ? 'Excluded' : 'Counted'}
                    </Badge>
                  </Field>
                  <Field label="Fulfilment planning" className="md:col-span-2">
                    <Badge variant={warehouse.fulfilment_planning ? 'success' : 'secondary'}>
                      {warehouse.fulfilment_planning ? 'On' : 'Off'}
                    </Badge>
                  </Field>
                  <Field label="Draws stock from">{poolCode || 'Stands alone'}</Field>
                  {/* Same field, same position as the edit view: the read view is what
                      teaches the user where things are. */}
                  <Field label="Sells to">
                    <span className="capitalize">{warehouse.segment || 'Not set'}</span>
                  </Field>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      </Container>

    </>
  );
}
