'use client';

import { use, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { MoveLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import CustomerForm from '../../components/CustomerForm';
import { useCustomerTabs, type CustomerTab } from '../../components/CustomerDetail';
import { CustomerAsksTab } from '../../components/CustomerAsksTab';
import { CustomerBranchesTab } from '../../components/CustomerBranchesTab';

export default function EditCustomerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const qs = searchParams.toString();
  const querySuffix = qs ? `?${qs}` : '';
  const [tab, setTab] = useState<CustomerTab>('details');
  const tabs = useCustomerTabs();

  return (
    <>
      <Container>
        <PageHeader
          title="Edit Customer"
          actions={
            <Button asChild variant="outline">
              <Link href={`/order-management/customers/${id}${querySuffix}`}>
                <MoveLeft /> Back to customer
              </Link>
            </Button>
          }
        />
      </Container>
      <Container>
        {/* Same tabs, same order as the customer's view page (view = edit). */}
        <Tabs value={tab} onValueChange={(v) => setTab(v as CustomerTab)}>
          <TabsList variant="line" className="mb-5">
            {tabs.map((t) => (
              <TabsTrigger key={t.value} value={t.value}>
                <t.icon className="size-4" />
                <span>{t.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="details">
            <CustomerForm
              customerId={id}
              onSuccess={() => {
                router.push(`/order-management/customers/${id}${querySuffix}`);
              }}
            />
          </TabsContent>
          <TabsContent value="branches">
            {tab === 'branches' && <CustomerBranchesTab customerId={id} />}
          </TabsContent>
          <TabsContent value="asks">
            {tab === 'asks' && <CustomerAsksTab customerId={id} />}
          </TabsContent>
        </Tabs>
      </Container>
    </>
  );
}
