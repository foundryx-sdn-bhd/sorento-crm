'use client';

/**
 * DEV-LOGIN-BYPASS: the "Sign in as" picker's users. Layering: UI -> this hook ->
 * `services/devLoginService.ts` -> Next route -> backend. `null` = dev sign-in is off.
 */

import { useQuery } from '@tanstack/react-query';
import { getDevLoginUsers, type DevLoginUser } from '@/services/devLoginService';

export function useDevLoginUsers() {
  return useQuery<DevLoginUser[] | null>({
    queryKey: ['dev-login-users'],
    queryFn: getDevLoginUsers,
    staleTime: Infinity,
    retry: false,
  });
}
