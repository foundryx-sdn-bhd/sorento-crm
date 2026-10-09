'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { approveConsent, denyConsent, inspectConsent } from '../services/consentService';

/** The consent request as sorento reads it. Inspecting never uses the request up. */
export function useConsentQuery(req: string) {
  return useQuery({
    queryKey: ['claude-connector-consent', req],
    queryFn: () => inspectConsent(req),
    retry: false,
  });
}

/** Allow / Deny. Each answers the request once; the caller hands the result back to Claude. */
export function useConsentMutations(req: string) {
  const approve = useMutation({ mutationFn: () => approveConsent(req) });
  const deny = useMutation({ mutationFn: () => denyConsent(req) });
  return { approve, deny };
}
