'use client';

import type { UseFormReturn } from 'react-hook-form';
import { FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { AsyncCombobox } from '@/app/(auth)/portal/components/AsyncCombobox';
import { lookupCustomersForRequest } from '../services/customerLookupService';
import type { CustomerLookupItem } from '../types/purchaseRequest.types';
import type { PurchaseRequestSchemaType } from '../forms/purchase-request-schema';

/**
 * The Customer Name field of the PR / SF form, shared by the create form and the edit card
 * so the two screens cannot drift. A pick links the customer and fills Sales type from the
 * server's mapping (PR and SF alike); typed text unlinks it.
 */
export function RequestCustomerField({
  form,
  label,
}: {
  form: UseFormReturn<PurchaseRequestSchemaType>;
  /** Rendered as the field's label; the edit card draws its own caption instead. */
  label?: string;
}) {
  return (
    <FormField
      control={form.control}
      name="customer_name"
      render={({ field }) => (
        <FormItem>
          {label ? <FormLabel>{label}</FormLabel> : null}
          <FormControl>
            <AsyncCombobox<CustomerLookupItem>
              value={field.value ?? ''}
              onChange={(v, item) => {
                field.onChange(v);
                form.setValue('customer_id', item?.customer_id ?? null, { shouldDirty: true });
                if (item?.sales_type) {
                  form.setValue('sales_type', item.sales_type, { shouldDirty: true });
                }
              }}
              fetchOptions={lookupCustomersForRequest}
              optionValue={(o) => o.debtor_name}
              optionLabel={(o) => o.debtor_name}
              optionMeta={(o) => o.customer_code}
              placeholder="Customer name"
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
