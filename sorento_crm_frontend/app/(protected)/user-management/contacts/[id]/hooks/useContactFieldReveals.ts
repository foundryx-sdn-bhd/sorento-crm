import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import {
  getContactFieldReveals,
  getFieldRevealKeys,
  setContactFieldReveals,
} from '../services/contactFieldRevealService';

// One roster of keys for every contact - not contact-scoped, so every open
// Access tab shares the same cache entry.
export const fieldRevealKeysQueryKey = ['chatbot-field-reveal-keys'] as const;
export const contactFieldRevealsQueryKey = (contactId: string) => [
  'contact-field-reveals',
  contactId,
];

export function useFieldRevealKeys() {
  return useQuery({
    queryKey: fieldRevealKeysQueryKey,
    queryFn: getFieldRevealKeys,
    staleTime: 5 * 60_000,
  });
}

export function useContactFieldReveals(contactId: string) {
  return useQuery({
    queryKey: contactFieldRevealsQueryKey(contactId),
    queryFn: () => getContactFieldReveals(contactId),
    enabled: !!contactId,
    retry: 1,
  });
}

export function useSetContactFieldReveals(contactId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (granted: string[]) => setContactFieldReveals(contactId, granted),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: contactFieldRevealsQueryKey(contactId) });
      queryClient.invalidateQueries({ queryKey: ['contact-tag-settings', contactId] });
      toast.success('Field reveals updated');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save field reveals'),
  });
}
