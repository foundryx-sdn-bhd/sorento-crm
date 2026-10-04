import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import {
  contactBrandScopeKey,
  getContactBrandScope,
  saveContactBrandScope,
  type ContactBrandScope,
} from '@/services/contactBrandScopeService';

/** The contact's accessible brands (CONTACT-BRAND-SCOPE). */
export function useContactBrandScopeQuery(contactId: string) {
  return useQuery<ContactBrandScope, Error>({
    queryKey: contactBrandScopeKey(contactId),
    queryFn: () => getContactBrandScope(contactId),
    retry: 1,
  });
}

export function useContactBrandScopeMutations(contactId: string) {
  const queryClient = useQueryClient();

  const save = useMutation<ContactBrandScope, Error, string[]>({
    mutationFn: (brandIds) => saveContactBrandScope(contactId, brandIds),
    onSuccess: (data) => {
      queryClient.setQueryData(contactBrandScopeKey(contactId), data);
      // The contacts list carries the same brands in its Brands column.
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      toast.success('Brands saved');
    },
    onError: (error) => toast.error(error.message || 'Failed to save the contact brands'),
  });

  return { save };
}
