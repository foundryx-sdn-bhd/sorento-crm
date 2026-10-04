'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Edit, Info, MessageSquareText, Store } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useBackToListHref, useHrefWithListState } from '@/components/common/BackToList';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useCustomer, customersPagerQuery } from '../hooks/useCustomers';
import { formatDate } from '@/lib/helpers';
import DetailActions from '@/components/common/DetailActions';
import { useCustomerActions } from '../actions';
import { CustomerAsksTab } from './CustomerAsksTab';
import { CustomerBranchesTab } from './CustomerBranchesTab';
import { useHasPermission } from '@/hooks/usePermissions';
import CustomerLinkedContactsSection from './CustomerLinkedContactsSection';
import CustomerOpportunitiesSection from './CustomerOpportunitiesSection';
import CustomerGroupLedgersCard from './CustomerGroupLedgersCard';

export type CustomerTab = 'details' | 'branches' | 'asks';

/** Shared with the edit page, so view and edit carry the same tabs in the same order. */
export const CUSTOMER_TABS: { value: CustomerTab; label: string; icon: typeof Info }[] = [
  { value: 'details', label: 'Details', icon: Info },
  // #1356: the customer's AutoCount branches, read only; needs order_management.branches.view.
  { value: 'branches', label: 'Branches', icon: Store },
  { value: 'asks', label: 'Asks', icon: MessageSquareText },
];

/** The tabs this user may open: Branches is hidden without order_management.branches.view. */
export function useCustomerTabs() {
  const canSeeBranches = useHasPermission('order_management.branches.view');
  return CUSTOMER_TABS.filter((t) => t.value !== 'branches' || canSeeBranches);
}

interface CustomerDetailProps {
  customerId: string;
}

export default function CustomerDetail({ customerId }: CustomerDetailProps) {
  const router = useRouter();
  const backHref = useBackToListHref('/order-management/customers');
  // Edit carries the list state too: the edit screen has a pager of its own.
  const editHref = useHrefWithListState(
    `/order-management/customers/${customerId}/edit`,
  );
  const { data: customer, isLoading } = useCustomer(customerId);
  const [tab, setTab] = useState<CustomerTab>('details');
  const tabs = useCustomerTabs();
  const { actions, dialogs } = useCustomerActions(customer, {
    onDeleted: () => router.push(backHref),
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!customer) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Customer not found</p>
        <Button variant="outline" onClick={() => router.push('/order-management/customers')} className="mt-4">
          Back to Customers
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1 min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-2xl font-bold break-words min-w-0">{customer.customer_name}</h2>
            <Badge variant={customer.is_active ? 'success' : 'secondary'}>
              <BadgeDot />
              {customer.is_active ? 'Active' : 'Inactive'}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Customer Code: {customer.customer_code}
          </p>
        </div>
        <DetailActions
          pager={{
            ...customersPagerQuery,
            detailPath: '/order-management/customers',
            currentId: customerId,
            ariaLabel: 'customer',
          }}
          actions={actions}
          dialogs={dialogs}
          gearLabel="Customer options"
          primary={
            <Button
              onClick={() =>
                router.push(editHref)
              }
            >
              <Edit className="size-4" />
              Edit
            </Button>
          }
        />
      </div>

      {/* Chatbot stock ask v2 S5 (AC-SA508): line tabs, Details first (the two cards,
          unchanged), then the customer's stock asks. */}
      <Tabs value={tab} onValueChange={(v) => setTab(v as CustomerTab)}>
        <TabsList variant="line" className="mb-5">
          {tabs.map((t) => (
            <TabsTrigger key={t.value} value={t.value} onClick={() => setTab(t.value)}>
              <t.icon className="size-4" />
              <span>{t.label}</span>
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="details">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Contact Information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <p className="text-sm text-muted-foreground">Email</p>
                  <p className="font-medium">{customer.email || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Phone</p>
                  <p className="font-medium">{customer.phone_number || '-'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Sales Agent</p>
                  <p className="font-medium">
                    {customer.sales_agent_code
                      ? customer.sales_agent_name
                        ? `${customer.sales_agent_code} - ${customer.sales_agent_name}`
                        : customer.sales_agent_code
                      : 'No sales agent assigned'}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Account level</p>
                  <p className="font-medium">
                    {customer.account_level ? `Account ${customer.account_level}` : 'No account level set'}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Group</p>
                  <p className="font-medium">
                    {customer.customer_group_id && customer.customer_group_name ? (
                      <Link
                        href={`/order-management/customer-groups/${customer.customer_group_id}`}
                        className="text-primary hover:underline"
                      >
                        {customer.customer_group_name}
                      </Link>
                    ) : (
                      'No group'
                    )}
                  </p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Additional Information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <p className="text-sm text-muted-foreground">Status</p>
                  <Badge variant={customer.is_active ? 'success' : 'secondary'}>
                    <BadgeDot />
                    {customer.is_active ? 'Active' : 'Inactive'}
                  </Badge>
                </div>
                {customer.orders_count !== undefined && (
                  <div>
                    <p className="text-sm text-muted-foreground">Total Delivery Orders</p>
                    <p className="font-medium">{customer.orders_count}</p>
                  </div>
                )}
                <div>
                  <p className="text-sm text-muted-foreground">Created</p>
                  <p className="font-medium text-sm">
                    {formatDate(new Date(customer.created_at))}
                  </p>
                </div>
                {customer.updated_at && (
                  <div>
                    <p className="text-sm text-muted-foreground">Last Updated</p>
                    <p className="font-medium text-sm">
                      {formatDate(new Date(customer.updated_at))}
                    </p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="mt-6">
            <CustomerGroupLedgersCard
              customerId={customerId}
              groupId={customer.customer_group_id ?? null}
            />
          </div>

          <div className="mt-6">
            <CustomerLinkedContactsSection customerId={customerId} />
          </div>

          <CustomerOpportunitiesSection
            customerId={customerId}
            customerName={customer.customer_name}
          />
        </TabsContent>

        <TabsContent value="branches">
          {tab === 'branches' && <CustomerBranchesTab customerId={customerId} />}
        </TabsContent>

        <TabsContent value="asks">
          {tab === 'asks' && <CustomerAsksTab customerId={customerId} />}
        </TabsContent>
      </Tabs>
    </div>
  );
}
