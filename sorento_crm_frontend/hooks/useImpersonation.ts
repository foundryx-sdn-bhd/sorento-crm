"use client";

import { useMutation } from '@tanstack/react-query';
import { useCallback } from 'react';

import {
  impersonationStore,
  useImpersonationSession,
  type ImpersonationSession,
} from '@/lib/impersonation-store';
import {
  fetchCurrentImpersonation,
  startImpersonation,
  stopImpersonation,
} from '@/services/impersonationService';

export function useImpersonation() {
  const session = useImpersonationSession();

  const hydrate = useCallback(async (): Promise<ImpersonationSession | null> => {
    const current = await fetchCurrentImpersonation();
    impersonationStore.setSession(current);
    return current;
  }, []);

  const startMutation = useMutation({
    mutationFn: (targetUserId: string) => startImpersonation(targetUserId),
    onSuccess: (s) => {
      impersonationStore.setSession(s);
    },
  });

  const stopMutation = useMutation({
    mutationFn: stopImpersonation,
    // Clear before the call, not after: a query answered between the server ending
    // the row and onSuccess would carry X-Impersonation-Ended while the target still
    // matched, and show "View-as ended" for a stop the user just asked for.
    onMutate: () => {
      const previous = impersonationStore.getState();
      impersonationStore.setSession(null);
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) impersonationStore.setSession(context.previous);
    },
  });

  return {
    session,
    isImpersonating: !!session,
    hydrate,
    start: startMutation.mutateAsync,
    stop: stopMutation.mutateAsync,
    starting: startMutation.isPending,
    stopping: stopMutation.isPending,
  };
}
