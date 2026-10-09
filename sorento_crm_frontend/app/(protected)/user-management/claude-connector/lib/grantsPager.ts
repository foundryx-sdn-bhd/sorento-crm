import type { QueryKey } from '@tanstack/react-query';
import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import type { ListPagerPage, ListPagerParams } from '@/hooks/useListPager';
import { listGrants, type GrantsExtraParams } from '../services/claudeConnectorService';

export const GRANTS_KEY = 'claude-connector-grants';
export const GRANT_KEY = 'claude-connector-grant';

/** The list's React Query key; the detail pager rebuilds the SAME key from the URL. */
export function grantsListQueryKey(
  params: DataGridApiFetchParams,
  extra?: GrantsExtraParams,
): QueryKey {
  return [GRANTS_KEY, params.pageIndex, params.pageSize, params.sorting, params.searchQuery, extra?.status ?? ''];
}

function fromUrl(params: ListPagerParams): [DataGridApiFetchParams, GrantsExtraParams] {
  return [
    {
      pageIndex: params.pageIndex,
      pageSize: params.pageSize,
      sorting: params.sorting,
      searchQuery: params.searchQuery,
    },
    { status: params.filters.status },
  ];
}

/** The detail pager's two hooks into the grants list. */
export const grantsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey => grantsListQueryKey(...fromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> => listGrants(...fromUrl(params)),
};

