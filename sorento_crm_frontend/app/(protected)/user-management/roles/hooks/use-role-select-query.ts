import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

// Custom hook to use roles for selection
export const useRoleSelectQuery = () => {
  // Fetch roles for selection
  const fetchRoleList = async () => {
    const response = await apiFetch('/api/user-management/roles/select');

    // Throw, never return [] or the error body (NEVER-STUCK-UI S3, lever L5): the
    // picker then says "no access" or "could not load, retry" instead of looking
    // empty, and the shared query toast speaks once.
    if (!response.ok) throw new Error(await extractApiError(response, 'Could not load roles.'));

    return response.json();
  };

  return useQuery({
    queryKey: ['user-role-select'],
    queryFn: fetchRoleList,
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });
};
