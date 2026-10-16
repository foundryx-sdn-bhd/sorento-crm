import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import RequireAccess from '@/app/components/common/RequireAccess';
import OrdersList from './components/OrdersList';

export const metadata: Metadata = {
  title: 'Delivery Orders',
  description: 'Manage delivery orders.',
};

export default async function OrdersPage() {
  return (
    <RequireAccess permission="order_management.orders.view">
      <Container>
        <PageHeader title="Delivery Orders" />
      </Container>

      <Container>
        <OrdersList />
      </Container>
    </RequireAccess>
  );
}
