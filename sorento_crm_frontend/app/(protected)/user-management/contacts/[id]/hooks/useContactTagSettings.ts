import { useQuery } from '@tanstack/react-query';
import { getContactTagSettings } from '../services/contactTagSettingsService';

/** The contact's tag settings: expected (merged across its tags) against current. */
export function useContactTagSettings(contactId: string) {
  return useQuery({
    queryKey: ['contact-tag-settings', contactId],
    queryFn: () => getContactTagSettings(contactId),
  });
}
