import { useQuery, type QueryClient } from '@tanstack/react-query';
import { getContactTagSettings } from '../services/contactTagSettingsService';

/** The contact's tag settings: expected (merged across its tags) against current. */
export function useContactTagSettings(contactId: string) {
  return useQuery({
    queryKey: ['contact-tag-settings', contactId],
    queryFn: () => getContactTagSettings(contactId),
  });
}

/** Any save that can change a contact's access leaves its Tags section stale: refetch it. */
export function invalidateContactTagSettings(queryClient: QueryClient, contactId: string) {
  void queryClient.invalidateQueries({ queryKey: ['contact-tag-settings', contactId] });
}
