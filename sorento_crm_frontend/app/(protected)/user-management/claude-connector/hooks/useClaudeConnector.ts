import { useQuery } from '@tanstack/react-query';
import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { getGrant, listGrants, type GrantsExtraParams } from '../services/claudeConnectorService';

import { GRANT_KEY, GRANTS_KEY, grantsListQueryKey } from '../lib/grantsPager';

export function useGrantsQuery(params: DataGridApiFetchParams, extra?: GrantsExtraParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: grantsListQueryKey(params, extra),
    queryFn: () => listGrants(params, extra),
    retry: 1,
  });
}

export function useGrantQuery(id: string | null) {
  return useQuery({
    queryKey: [GRANT_KEY, id],
    queryFn: () => getGrant(id as string),
    enabled: !!id,
    retry: 1,
  });
}

/** Revoke asks nothing (D7): a 5 s reversible window, the countdown lands where the button was. */
export function useGrantRevocation() {
  return useDeferredRowAction({
    actionKey: 'analytics_connector_grant.revoke',
    entityType: 'analytics_connector_grant',
    verb: 'Revoking',
    successMessage: 'Claude access revoked',
    invalidateKeys: [[GRANTS_KEY], [GRANT_KEY]],
    surface: 'inline',
  });
}
