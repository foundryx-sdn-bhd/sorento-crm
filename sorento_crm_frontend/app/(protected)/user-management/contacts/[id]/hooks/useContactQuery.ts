import { useQuery } from '@tanstack/react-query';

import { retryUnlessRefused } from '@/lib/api-client';
import { getContact } from '../services/contactService';

export const contactKey = (contactId: string) => ['respond-contact', contactId];

export function useContactQuery(contactId: string) {
  return useQuery({
    queryKey: contactKey(contactId),
    queryFn: () => getContact(contactId),
    enabled: !!contactId,
    // A refusal or a missing contact will not change on retry; a fault gets one.
    retry: retryUnlessRefused,
    // The contact layout renders the failure in place; no toast on top of it.
    meta: { silent: true },
  });
}
