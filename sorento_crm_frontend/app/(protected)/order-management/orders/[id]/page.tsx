'use client';

import { use } from 'react';
import { useSearchParams } from 'next/navigation';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import RequireAccess from '@/app/components/common/RequireAccess';
import OrderDetail from '../components/OrderDetail';

export default function OrderDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const searchParams = useSearchParams();
  const listQueryString = searchParams.toString();

  return (
    <RequireAccess permission="order_management.orders.view">
      <Container>
        <PageHeader
          title="Delivery Order"
          actions={
            <BackToList
              listPath="/order-management/orders"
              label="Back to delivery orders"
            />
          }
        />
      </Container>
      <Container>
        <OrderDetail orderId={id} listSearch={listQueryString} />
      </Container>
    </RequireAccess>
  );
}
