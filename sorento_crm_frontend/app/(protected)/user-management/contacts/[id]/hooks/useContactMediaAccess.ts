import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import {
  getContactMediaAccess,
  updateContactMediaAccess,
  type ContactMediaAccessInput,
  type MediaModality,
} from '../services/contactMediaAccessService';

export const contactMediaAccessKey = (contactId: string) => ['contact-media-access', contactId];

export function useContactMediaAccess(contactId: string) {
  return useQuery({
    queryKey: contactMediaAccessKey(contactId),
    queryFn: () => getContactMediaAccess(contactId),
    enabled: !!contactId,
    retry: 1,
  });
}

export function useUpdateContactMediaAccess(contactId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      modality,
      input,
    }: {
      modality: MediaModality;
      input: ContactMediaAccessInput;
    }) => updateContactMediaAccess(contactId, modality, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: contactMediaAccessKey(contactId) });
      queryClient.invalidateQueries({ queryKey: ['contact-tag-settings', contactId] });
      toast.success('Media access updated');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save media access'),
  });
}
