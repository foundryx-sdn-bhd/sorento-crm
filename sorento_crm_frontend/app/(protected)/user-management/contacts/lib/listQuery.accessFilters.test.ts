/**
 * Lane CONTACT-BULK-ACCESS, UAC A2.3: the contacts list's access filters as query params,
 * and back from the URL. A filter on its default is absent, never an empty string.
 */
import { describe, it, expect } from 'vitest';
import { buildDetailSearch, parseDetailSearch } from '@/lib/listNavQuery';
import { contactAccessFiltersFromUrl, contactsListFilters } from './listQuery';

describe('contactsListFilters access filters', () => {
  it('maps every access filter to its backend param and drops unset ones', () => {
    expect(
      contactsListFilters({
        chatbotMemoryLevel: null,
        access: {
          accessType: 'dealer',
          tier: 'none',
          cost: 'yes',
          escalation: 'no',
          packingList: 'no',
          stock: 'yes',
          customerId: 'cust-1',
          accessDiffersFrom: 'contact-x',
        },
      }),
    ).toEqual({
      access_type: 'dealer',
      tier: 'none',
      cost: 'yes',
      escalation: 'no',
      packing_list: 'no',
      stock: 'yes',
      customer_id: 'cust-1',
      access_differs_from: 'contact-x',
    });
    expect(contactsListFilters({ chatbotMemoryLevel: null, access: { tier: null, cost: '' } })).toEqual({});
  });

  it('round-trips through the URL the list writes', () => {
    const filters = contactsListFilters({
      chatbotMemoryLevel: null,
      customersNone: true,
      access: { escalation: 'yes', accessDiffersFrom: 'contact-x' },
    });
    const search = buildDetailSearch(
      { pageIndex: 0, pageSize: 50, sorting: [], searchQuery: '' },
      filters,
    );
    const state = parseDetailSearch(new URLSearchParams(search));
    expect(state.filters.customers).toBe('none');
    expect(contactAccessFiltersFromUrl(state.filters)).toEqual({
      escalation: 'yes',
      accessDiffersFrom: 'contact-x',
    });
  });
});
