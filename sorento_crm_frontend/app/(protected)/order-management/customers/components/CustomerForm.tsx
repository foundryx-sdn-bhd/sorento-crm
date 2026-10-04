'use client';

import { useEffect, useMemo, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { LoaderCircleIcon, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { searchCustomerGroupsSelect } from '../../customer-groups/services/customerGroupService';
import { useCreateCustomer, useUpdateCustomer, useCustomer } from '../hooks/useCustomers';
import { useCustomerSalesAgentOptions } from '../hooks/useCustomerSalesAgentOptions';
import { CustomerSchema, type CustomerSchemaType } from '../forms/customer-schema';
import type { CustomerFormData } from '../types/customer.types';
import ListPager from '@/components/common/ListPager';
import { customersPagerQuery } from '../hooks/useCustomers';

const ACCOUNT_LEVEL_OPTIONS = Array.from({ length: 9 }, (_, i) => ({
  value: String(i + 1),
  label: `Account ${i + 1}`,
}));

interface CustomerFormProps {
  customerId?: string;
  onSuccess?: () => void;
}

export default function CustomerForm({ customerId, onSuccess }: CustomerFormProps) {
  const router = useRouter();
  const isEditMode = !!customerId;
  const { data: customer, isLoading: isLoadingCustomer } = useCustomer(customerId || null);
  const createMutation = useCreateCustomer();
  const updateMutation = useUpdateCustomer();
  const agentOptions = useCustomerSalesAgentOptions();
  // The group just picked, so the trigger names it before anything is saved.
  const [pickedGroup, setPickedGroup] = useState<SearchableSelectOption | null>(null);

  // The select only offers ACTIVE agents (the backend rejects a fresh pick of an inactive
  // one), but a customer already carrying one - assigned before it was deactivated - must
  // still show it, or the trigger falls back to the placeholder and the next save silently
  // clears the assignment (review PR #1177, should-fix item 2). Shown disabled: visible,
  // not re-selectable once cleared.
  //
  // Gated on `agentOptions.isSuccess`: while the options query is still loading, or after
  // it errors (or 403s - PUT is ungated today, review round 2 security item 2), `options`
  // reads as an empty array too, which is indistinguishable from "this id really is
  // inactive" - and would label an ACTIVE agent "(inactive)" for the whole time the list
  // has not loaded (review round 2, new finding 1).
  const agentSelectOptions = useMemo(() => {
    const base = agentOptions.options;
    const currentId = customer?.sales_agent_id;
    if (!agentOptions.isSuccess || !currentId || base.some((o) => o.value === currentId)) {
      return base;
    }
    const label = customer?.sales_agent_name
      ? `${customer.sales_agent_code} - ${customer.sales_agent_name} (inactive)`
      : `${customer?.sales_agent_code ?? ''} (inactive)`;
    return [...base, { value: currentId, label, disabled: true }];
  }, [agentOptions.options, agentOptions.isSuccess, customer]);

  const form = useForm<CustomerSchemaType>({
    resolver: zodResolver(CustomerSchema),
    defaultValues: {
      customer_code: '',
      customer_name: '',
      email: '',
      phone_number: '',
      is_active: true,
      sales_agent_id: null,
      account_level: null,
      customer_group_id: null,
    },
    mode: 'onTouched',
  });

  // Track if form has been initialized to prevent multiple resets
  const [formInitialized, setFormInitialized] = useState(false);

  // Load customer data when editing
  useEffect(() => {
    if (customer && isEditMode && !formInitialized) {
      form.reset({
        customer_code: customer.customer_code,
        customer_name: customer.customer_name,
        email: customer.email || '',
        phone_number: customer.phone_number || '',
        is_active: customer.is_active,
        sales_agent_id: customer.sales_agent_id || null,
        account_level: customer.account_level ?? null,
        customer_group_id: customer.customer_group_id ?? null,
      });
      setFormInitialized(true);
    }
  }, [customer, isEditMode, form, formInitialized]);

  // Reset formInitialized when customerId changes
  useEffect(() => {
    setFormInitialized(false);
  }, [customerId]);

  const onSubmit = async (data: CustomerSchemaType) => {
    try {
      // Transform data to ensure proper format
      const formData: CustomerFormData = {
        customer_code: data.customer_code,
        customer_name: data.customer_name,
        email: data.email || undefined,
        phone_number: data.phone_number || undefined,
        is_active: data.is_active,
        // Already null or a real id - `field.onChange` normalizes '' to null on every
        // change, so there is nothing left here for `|| null` to catch.
        sales_agent_id: data.sales_agent_id ?? null,
        // Explicit null clears the level: an omitted key would keep the old one.
        account_level: data.account_level ?? null,
        // Explicit null clears the group, same as the level.
        customer_group_id: data.customer_group_id ?? null,
      };

      if (isEditMode && customerId) {
        await updateMutation.mutateAsync({ id: customerId, data: formData });
      } else {
        await createMutation.mutateAsync(formData);
      }
      if (onSuccess) {
        onSuccess();
      } else {
        router.push('/order-management/customers');
      }
    } catch (error) {
      // Error is handled by the mutation hook
      console.error('Customer form submission error:', error);
    }
  };

  if (isEditMode && isLoadingCustomer) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoaderCircleIcon className="size-6 animate-spin" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        {isEditMode && customerId && (
          <div className="flex justify-end">
            <ListPager
              {...customersPagerQuery}
              detailPath="/order-management/customers"
              currentId={customerId}
              ariaLabel="customer"
              hrefFor={(id, search) =>
                `/order-management/customers/${id}/edit${search ? `?${search}` : ''}`
              }
            />
          </div>
        )}
        <Card>
          <CardHeader>
            <CardTitle>{isEditMode ? 'Edit Customer' : 'Create Customer'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Basic Information */}
            <div className="space-y-4">
              <h3 className="text-lg font-semibold">Basic Information</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="customer_code"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Customer Code *</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="CUST-001"
                          {...field}
                          disabled={isEditMode}
                        />
                      </FormControl>
                      <FormDescription>
                        Unique customer identifier (alphanumeric, dashes, underscores only)
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="customer_name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Customer Name *</FormLabel>
                      <FormControl>
                        <Input placeholder="Enter customer name" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Email</FormLabel>
                      <FormControl>
                        <Input type="email" placeholder="customer@example.com" {...field} value={field.value || ''} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="phone_number"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Phone Number</FormLabel>
                      <FormControl>
                        <Input placeholder="+1 (555) 123-4567" {...field} value={field.value || ''} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="sales_agent_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Sales Agent</FormLabel>
                      <FormControl>
                        <SearchableSelect
                          value={field.value || ''}
                          onChange={(v) => field.onChange(v || null)}
                          options={agentSelectOptions}
                          loadError={agentOptions.error}
                          onRetry={() => void agentOptions.refetch()}
                          placeholder="No sales agent"
                          clearable
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="account_level"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Account level</FormLabel>
                      <FormControl>
                        <SearchableSelect
                          aria-label="Account level"
                          value={field.value ? String(field.value) : ''}
                          onChange={(v) => field.onChange(v ? Number(v) : null)}
                          options={ACCOUNT_LEVEL_OPTIONS}
                          placeholder="No account level"
                          clearable
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="customer_group_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Group</FormLabel>
                      <FormControl>
                        <SearchableSelect
                          aria-label="Group"
                          value={field.value || ''}
                          onChange={(v) => field.onChange(v || null)}
                          onOptionChange={setPickedGroup}
                          fetchOptions={async (query) =>
                            (await searchCustomerGroupsSelect(query)).map((g) => ({
                              value: g.id,
                              label: g.name,
                              description: `${g.ledger_count} ${g.ledger_count === 1 ? 'ledger' : 'ledgers'}`,
                            }))
                          }
                          selectedOption={
                            pickedGroup && pickedGroup.value === field.value
                              ? pickedGroup
                              : customer?.customer_group_id &&
                                  customer.customer_group_id === field.value
                                ? {
                                    value: customer.customer_group_id,
                                    label: customer.customer_group_name ?? 'Group',
                                  }
                                : undefined
                          }
                          placeholder="No group"
                          emptyMessage="No groups match."
                          clearable
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </div>

            {/* Status */}
            <div className="space-y-4">
              <FormField
                control={form.control}
                name="is_active"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                    <div className="space-y-0.5">
                      <FormLabel className="text-base">Active Status</FormLabel>
                      <FormDescription>
                        Enable or disable this customer
                      </FormDescription>
                    </div>
                    <FormControl>
                      <Switch
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
            </div>

            <div className="flex justify-end gap-4 pt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (onSuccess) {
                    onSuccess();
                  } else {
                    router.push('/order-management/customers');
                  }
                }}
                disabled={isLoading}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isLoading}>
                {isLoading ? (
                  <>
                    <LoaderCircleIcon className="mr-2 size-4 animate-spin" />
                    {isEditMode ? 'Updating...' : 'Creating...'}
                  </>
                ) : (
                  <>
                    <Save className="mr-2 size-4" />
                    {isEditMode ? 'Update Customer' : 'Create Customer'}
                  </>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </Form>
  );
}
