import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import { getContactPortalForms, updateContactPortalForm } from '../services/contactPortalFormsService';

export const contactPortalFormsKey = (contactId: string) => ['contact-portal-forms', contactId];

export function useContactPortalForms(contactId: string) {
  return useQuery({
    queryKey: contactPortalFormsKey(contactId),
    queryFn: () => getContactPortalForms(contactId),
    enabled: !!contactId,
    retry: 1,
  });
}

export function useUpdateContactPortalForm(contactId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ formType, isEnabled }: { formType: string; isEnabled: boolean | null }) =>
      updateContactPortalForm(contactId, formType, isEnabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: contactPortalFormsKey(contactId) });
      queryClient.invalidateQueries({ queryKey: ['contact-tag-settings', contactId] });
      toast.success('Portal forms updated');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save portal forms'),
  });
}
