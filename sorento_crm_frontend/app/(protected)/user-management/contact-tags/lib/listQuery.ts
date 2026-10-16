/** The contact tags list query, shared by the list and the detail page's prev/next. */
import type { QueryKey } from '@tanstack/react-query';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import { getContactTags } from '../services/contactTagService';

export function contactTagsListQueryKey(params: ListPagerParams): QueryKey {
  return [
    'contact-tags',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    params.filters,
  ];
}

export function fetchContactTagsPage(
  params: ListPagerParams,
): Promise<ListPagerPage> {
  return getContactTags(
    {
      pageIndex: params.pageIndex,
      pageSize: params.pageSize,
      sorting: params.sorting,
      searchQuery: params.searchQuery,
    },
    params.filters,
  ) as Promise<ListPagerPage>;
}

export const contactTagsPagerQuery = {
  listQueryKey: contactTagsListQueryKey,
  fetchPage: fetchContactTagsPage,
};
