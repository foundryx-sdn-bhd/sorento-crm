'use client';

import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { getCustomer } from '@/app/(protected)/order-management/customers/services/customerService';
import { useContactAccessTypes } from '../../contact-access-types/hooks/useContactAccessTypes';
import { getContact } from '../[id]/services/contactService';
import {
  popoverFilterChips,
  popoverFilterCount,
} from '../lib/contactFilterSummary';
import {
  contactAccessFiltersFromUrl,
  contactsListFilters,
  type ContactAccessFilters as AccessFilters,
} from '../lib/listQuery';
import ContactAccessFilters from './ContactAccessFilters';

interface State {
  accessFilters: AccessFilters;
  customersNone: boolean;
  noTags: boolean;
  setAccessFilters: (next: AccessFilters) => void;
  setCustomersNone: (on: boolean) => void;
  setNoTags: (on: boolean) => void;
}

/**
 * The toolbar `filters` prop for the contacts list: every filter except Tag and Tag settings (which stay
 * primary in the toolbar). The list keeps owning the state and the URL sync; this only presents it.
 */
export function useContactFiltersToolbar(s: State) {
  const map = contactsListFilters({
    chatbotMemoryLevel: null,
    customersNone: s.customersNone,
    access: s.accessFilters,
    noTags: s.noTags,
  });
  const { data: accessTypes = [] } = useContactAccessTypes();
  // Same keys as ContactAccessFilters' own label read-back, so the cache is shared; chips never show an id.
  const customerLabel = useQuery({
    queryKey: ['contact-filter-customer-label', s.accessFilters.customerId],
    queryFn: async () => {
      const c = await getCustomer(s.accessFilters.customerId as string);
      return `${c.customer_code} - ${c.customer_name}`;
    },
    enabled: !!s.accessFilters.customerId,
    staleTime: Infinity,
    retry: 0,
  });
  const differsLabel = useQuery({
    queryKey: [
      'contact-filter-differs-label',
      s.accessFilters.accessDiffersFrom,
    ],
    queryFn: async () => {
      const c = await getContact(s.accessFilters.accessDiffersFrom as string);
      return c.name ? `${c.name} (${c.phone_number})` : c.phone_number;
    },
    enabled: !!s.accessFilters.accessDiffersFrom,
    staleTime: Infinity,
    retry: 0,
  });

  const apply = (next: Record<string, string>) => {
    s.setAccessFilters(contactAccessFiltersFromUrl(next));
    s.setCustomersNone(next.customers === 'none');
    s.setNoTags(next.tags === 'none');
  };
  const clearAll = () => apply({});
  const count = popoverFilterCount(map);

  return {
    kind: 'custom' as const,
    active: count > 0,
    activeCount: count,
    modal: false,
    activeSummary: popoverFilterChips(
      map,
      {
        access_type: accessTypes.find(
          (t) => t.code === s.accessFilters.accessType,
        )?.name,
        customer_id: customerLabel.data,
        access_differs_from: differsLabel.data,
      },
      apply,
    ),
    content: (
      <div className="space-y-3" data-testid="contacts-filters-popover">
        <ContactAccessFilters
          value={s.accessFilters}
          onChange={s.setAccessFilters}
          hideClear
        />
        <div className="flex items-center gap-2">
          <Checkbox
            id="contacts-no-customers"
            checked={s.customersNone}
            onCheckedChange={(v) => s.setCustomersNone(v === true)}
          />
          <Label
            htmlFor="contacts-no-customers"
            className="text-sm font-normal"
          >
            No customers linked
          </Label>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id="contacts-no-tags"
            checked={s.noTags}
            onCheckedChange={(v) => s.setNoTags(v === true)}
          />
          <Label htmlFor="contacts-no-tags" className="text-sm font-normal">
            No tags
          </Label>
        </div>
        {count > 0 ? (
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={clearAll}>
              Clear filters
            </Button>
          </div>
        ) : null}
      </div>
    ),
  };
}
